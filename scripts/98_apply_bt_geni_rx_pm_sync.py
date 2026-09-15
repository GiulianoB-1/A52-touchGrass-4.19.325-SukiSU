#!/usr/bin/env python3
from pathlib import Path
import sys

P2B_MARKER = "A52 BT-P2B: Qualcomm asynchronous real wake-byte handling"
P2C_MARKER = "A52 BT-P2C: Qualcomm RX-stop and runtime-PM synchronization"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    kernel = Path(sys.argv[1])
    path = kernel / "drivers/tty/serial/msm_geni_serial.c"
    text = path.read_text()

    if P2C_MARKER in text:
        print("BT-P2C already applied")
        return
    if P2B_MARKER not in text:
        raise SystemExit("BT-P2C requires BT-P2B to be applied first")

    # Newer Qualcomm GENI uses a baud-derived stale wait instead of the old
    # fixed 10 ms spin on every RX stop. Keep the same 10 ms safety cap.
    text = replace_once(
        text,
        """#define SEC_TO_USEC		(1000000)
#define STALE_DELAY		(1000) //10msec
#define DEFAULT_BITS_PER_CHAR	(10)
""",
        """#define SEC_TO_USEC		(1000000)
#define SYSTEM_DELAY		(500) /* 500 usec scheduling/IRQ margin */
#define STALE_DELAY_MAX		(10000) /* 10 msec safety cap */
#define DEFAULT_BITS_PER_CHAR	(10)
""",
        "dynamic stale constants",
    )

    text = replace_once(
        text,
        """	atomic_t check_wakeup_byte;
	struct workqueue_struct *wakeup_irq_wq;
	struct delayed_work wakeup_irq_dwork;
	struct completion wakeup_comp;

	struct msm_geni_serial_ver_info ver_info;
""",
        """	atomic_t check_wakeup_byte;
	struct workqueue_struct *wakeup_irq_wq;
	struct delayed_work wakeup_irq_dwork;
	struct completion wakeup_comp;

	/*
	 * A52 BT-P2C: serialize stop_rx against runtime suspend so resources
	 * cannot be switched off while RX cancel/abort is still touching GENI.
	 */
	atomic_t stop_rx_inprogress;

	struct msm_geni_serial_ver_info ver_info;
""",
        "stop-rx state",
    )

    # Bring over the newer Qualcomm return contract: when real RX DMA data is
    # still pending after the short in-flight wait, runtime PM must retry
    # rather than continuing toward clocks/resources off.
    old_wait = """static void wait_for_transfers_inflight(struct uart_port *uport)
{
	int iter = 0;
	struct msm_geni_serial_port *port = GET_DEV_PORT(uport);
	unsigned int geni_status;

	geni_status = geni_read_reg_nolog(uport->membase, SE_GENI_STATUS);
	/* Possible stop rx is called before this. */
	if (!(geni_status & S_GENI_CMD_ACTIVE))
		return;

	while (iter < WAIT_XFER_MAX_ITER) {
		if (check_transfers_inflight(uport)) {
			usleep_range(WAIT_XFER_MIN_TIMEOUT_US,
					WAIT_XFER_MAX_TIMEOUT_US);
			iter++;
		} else {
			break;
		}
	}
	if (check_transfers_inflight(uport)) {
		geni_se_dump_dbg_regs(&port->serial_rsc,
				uport->membase, port->ipc_log_misc);
	}
}
"""
    new_wait = """static int wait_for_transfers_inflight(struct uart_port *uport)
{
	int iter = 0;
	struct msm_geni_serial_port *port = GET_DEV_PORT(uport);
	unsigned int geni_status;
	u32 rx_len_in = 0;

	geni_status = geni_read_reg_nolog(uport->membase, SE_GENI_STATUS);
	/* Possible stop rx is called before this. */
	if (!(geni_status & S_GENI_CMD_ACTIVE))
		return 0;

	while (iter < WAIT_XFER_MAX_ITER) {
		if (check_transfers_inflight(uport)) {
			usleep_range(WAIT_XFER_MIN_TIMEOUT_US,
					WAIT_XFER_MAX_TIMEOUT_US);
			iter++;
		} else {
			break;
		}
	}

	if (check_transfers_inflight(uport)) {
		rx_len_in = geni_read_reg_nolog(uport->membase,
						 SE_DMA_RX_LEN_IN);
		if (rx_len_in) {
			IPC_LOG_MSG(port->ipc_log_misc,
				"%s: RX data still pending (%u), retry PM later\\n",
				__func__, rx_len_in);
			return -EBUSY;
		}

		/*
		 * A TX can still be completing here; retain the vendor diagnostic
		 * dump and let the normal stop-TX path reconcile it.
		 */
		geni_se_dump_dbg_regs(&port->serial_rsc,
				uport->membase, port->ipc_log_misc);
	}

	return 0;
}
"""
    text = replace_once(text, old_wait, new_wait, "inflight return status")

    text = replace_once(
        text,
        """static int vote_clock_off(struct uart_port *uport)
{
	struct msm_geni_serial_port *port = GET_DEV_PORT(uport);
	int usage_count;
""",
        """static int vote_clock_off(struct uart_port *uport)
{
	struct msm_geni_serial_port *port = GET_DEV_PORT(uport);
	int usage_count;
	int ret;
""",
        "vote clock ret declaration",
    )

    text = replace_once(
        text,
        """	wait_for_transfers_inflight(uport);
	port->ioctl_count--;
""",
        """	ret = wait_for_transfers_inflight(uport);
	if (ret) {
		IPC_LOG_MSG(port->ipc_log_pwr,
			"%s: RX still active, defer userspace clock-off\\n",
			__func__);
		return -EAGAIN;
	}
	port->ioctl_count--;
""",
        "userspace clock-off busy handling",
    )

    # stop_rx itself becomes single-owner. atomic_cmpxchg closes the tiny
    # read-then-set race present in some newer downstream implementations.
    text = replace_once(
        text,
        """	u32 dma_rx_status, s_irq_status;
	int usage_count;
	int iter = 0;

	IPC_LOG_MSG(port->ipc_log_misc, "%s\\n", __func__);

	geni_status = geni_read_reg_nolog(uport->membase, SE_GENI_STATUS);
""",
        """	u32 dma_rx_status, s_irq_status, stale_delay, baud;
	int usage_count;

	IPC_LOG_MSG(port->ipc_log_misc, "%s\\n", __func__);

	if (atomic_cmpxchg(&port->stop_rx_inprogress, 0, 1)) {
		IPC_LOG_MSG(port->ipc_log_misc,
			"%s: stop RX already in progress\\n", __func__);
		return -EBUSY;
	}

	geni_status = geni_read_reg_nolog(uport->membase, SE_GENI_STATUS);
""",
        "stop-rx ownership",
    )

    text = replace_once(
        text,
        """	if (!(geni_status & S_GENI_CMD_ACTIVE)) {
		IPC_LOG_MSG(port->ipc_log_misc,
			"%s: RX is Inactive, geni_sts: 0x%x\\n",
						__func__, geni_status);
		return 0;
	}
""",
        """	if (!(geni_status & S_GENI_CMD_ACTIVE)) {
		IPC_LOG_MSG(port->ipc_log_misc,
			"%s: RX is Inactive, geni_sts: 0x%x\\n",
						__func__, geni_status);
		atomic_set(&port->stop_rx_inprogress, 0);
		return 0;
	}
""",
        "inactive stop-rx release",
    )

    old_stale = """		/*
		 * Wait for the stale timeout around 10msec to happen
		 * if there is any data pending in the rx fifo.
		 * This will help to handle incoming rx data in
		 * stop_rx_sequencer for interrupt latency or
		 * system delay cases.
		 */
		while (iter < STALE_DELAY) {
			iter++;
			udelay(10);
		}

"""
    new_stale = """		/*
		 * Qualcomm newer GENI: derive the RX stale guard from the current
		 * baud instead of always spinning for 10 ms.  Keep a 10 ms cap and
		 * a 500 us IRQ/scheduling margin.  Bluetooth high-speed UART thus
		 * spends substantially less awake time merely preparing to suspend.
		 */
		baud = port->cur_baud ? port->cur_baud : 115200;
		stale_delay = (STALE_COUNT * SEC_TO_USEC) / baud;
		stale_delay = (2 * stale_delay) + SYSTEM_DELAY;
		if (stale_delay > STALE_DELAY_MAX)
			stale_delay = STALE_DELAY_MAX;
		udelay(stale_delay);

"""
    text = replace_once(text, old_stale, new_stale, "baud-derived stale wait")

    text = replace_once(
        text,
        """				pm_runtime_mark_last_busy(uport->dev);
				return -EBUSY;
""",
        """				pm_runtime_mark_last_busy(uport->dev);
				atomic_set(&port->stop_rx_inprogress, 0);
				return -EBUSY;
""",
        "delayed DMA stop-rx release",
    )

    text = replace_once(
        text,
        """	is_rx_active = geni_status & S_GENI_CMD_ACTIVE;
	if (is_rx_active)
		return -EBUSY;
	else
		return 0;
}
""",
        """	atomic_set(&port->stop_rx_inprogress, 0);
	is_rx_active = geni_status & S_GENI_CMD_ACTIVE;
	if (is_rx_active)
		return -EBUSY;
	else
		return 0;
}
""",
        "final stop-rx release",
    )

    # Initialize the atomic state at probe and again at a clean open.
    text = replace_once(
        text,
        """	init_completion(&dev_port->m_cmd_timeout);
	init_completion(&dev_port->s_cmd_timeout);
	init_completion(&dev_port->wakeup_comp);
""",
        """	init_completion(&dev_port->m_cmd_timeout);
	init_completion(&dev_port->s_cmd_timeout);
	init_completion(&dev_port->wakeup_comp);
	atomic_set(&dev_port->stop_rx_inprogress, 0);
""",
        "probe stop-rx init",
    )

    text = replace_once(
        text,
        """	msm_port->wakeup_irq_wake_set = false;
	atomic_set(&msm_port->check_wakeup_byte, 0);

	if (likely(!uart_console(uport))) {
""",
        """	msm_port->wakeup_irq_wake_set = false;
	atomic_set(&msm_port->check_wakeup_byte, 0);
	atomic_set(&msm_port->stop_rx_inprogress, 0);

	if (likely(!uart_console(uport))) {
""",
        "startup stop-rx reset",
    )

    # Runtime suspend now obeys both the in-flight RX result and any
    # independently-running serial-framework stop_rx before clocks go away.
    text = replace_once(
        text,
        """	int ret = 0;
	u32 geni_status = geni_read_reg_nolog(port->uport.membase,
""",
        """	int ret = 0;
	int stop_wait_count = 0;
	u32 geni_status = geni_read_reg_nolog(port->uport.membase,
""",
        "runtime suspend wait counter",
    )

    text = replace_once(
        text,
        """	wait_for_transfers_inflight(&port->uport);
	/*
	 * Manual RFR On.
""",
        """	if (!port->shutdown_in_progress) {
		ret = wait_for_transfers_inflight(&port->uport);
		if (ret) {
			IPC_LOG_MSG(port->ipc_log_pwr,
				"%s: pending RX data, retry runtime suspend\\n",
				__func__);
			return -EBUSY;
		}
	}
	/*
	 * Manual RFR On.
""",
        "runtime suspend inflight guard",
    )

    sync_block = """	/*
	 * BT-P2C: msm_geni_serial_stop_rx() can run independently through the
	 * serial core.  Do not switch off GENI resources until its register
	 * cancel/abort sequence has completed.
	 */
	while (atomic_read(&port->stop_rx_inprogress)) {
		usleep_range(9000, 11000);
		if (++stop_wait_count > 10) {
			IPC_LOG_MSG(port->ipc_log_pwr,
				"%s: stop_rx still busy, abort runtime suspend\\n",
				__func__);
			enable_irq(port->uport.irq);
			return -EBUSY;
		}
	}
	if (stop_wait_count)
		IPC_LOG_MSG(port->ipc_log_pwr,
			"%s: waited %d cycles for stop_rx\\n",
			__func__, stop_wait_count);

"""
    text = replace_once(
        text,
        """	msm_geni_serial_allow_rx(port);

	geni_ios = geni_read_reg_nolog(port->uport.membase, SE_GENI_IOS);
""",
        """	msm_geni_serial_allow_rx(port);

""" + sync_block + """	geni_ios = geni_read_reg_nolog(port->uport.membase, SE_GENI_IOS);
""",
        "runtime suspend stop-rx synchronization",
    )

    text += "\n/* " + P2C_MARKER + " */\n"
    path.write_text(text)

    final = path.read_text()
    invariants = [
        "atomic_t stop_rx_inprogress;",
        "atomic_cmpxchg(&port->stop_rx_inprogress, 0, 1)",
        "STALE_DELAY_MAX",
        "pending RX data, retry runtime suspend",
        "stop_rx still busy, abort runtime suspend",
        "RX still active, defer userspace clock-off",
        P2C_MARKER,
    ]
    for needle in invariants:
        if needle not in final:
            raise SystemExit(f"missing BT-P2C invariant: {needle}")
    if "while (iter < STALE_DELAY)" in final:
        raise SystemExit("legacy fixed 10 ms stale loop still present")

    print(f"patched {path}")
    print(P2C_MARKER)


if __name__ == "__main__":
    main()
