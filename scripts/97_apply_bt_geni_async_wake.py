#!/usr/bin/env python3
from pathlib import Path
import sys

P2A_MARKER = "A52 BT-P2A: Qualcomm GENI wake/shutdown hardening"
P2B_MARKER = "A52 BT-P2B: Qualcomm asynchronous real wake-byte handling"


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

    if P2B_MARKER in text:
        print("BT-P2B already applied")
        return
    if P2A_MARKER not in text:
        raise SystemExit("BT-P2B requires BT-P2A to be applied first")

    text = replace_once(
        text,
        "#include <linux/dma-mapping.h>\n",
        "#include <linux/dma-mapping.h>\n#include <linux/workqueue.h>\n",
        "workqueue include",
    )

    text = replace_once(
        text,
        """	bool wakeup_enabled;
	bool wakeup_irq_wake_set;
	bool shutdown_in_progress;
	struct msm_geni_serial_ver_info ver_info;
""",
        """	bool wakeup_enabled;
	bool wakeup_irq_wake_set;
	bool shutdown_in_progress;

	/*
	 * A52 BT-P2B: newer Qualcomm asynchronous wake handling.
	 * The wake GPIO schedules a high-priority worker which runtime-resumes
	 * GENI and lets the real 0xFD wake byte arrive through the UART RX path.
	 */
	atomic_t check_wakeup_byte;
	struct workqueue_struct *wakeup_irq_wq;
	struct delayed_work wakeup_irq_dwork;
	struct completion wakeup_comp;

	struct msm_geni_serial_ver_info ver_info;
""",
        "async wake state",
    )

    old_isr = """static irqreturn_t msm_geni_wakeup_isr(int isr, void *dev)
{
	struct uart_port *uport = dev;
	struct msm_geni_serial_port *port = GET_DEV_PORT(uport);
	struct tty_struct *tty;
	unsigned long flags;

	IPC_LOG_MSG(port->ipc_log_misc, "%s++\\n", __func__);

	spin_lock_irqsave(&uport->lock, flags);

	/*
	 * A52 BT-P2A: Qualcomm GENI wake/shutdown hardening.
	 *
	 * Preserve Samsung's existing edge-count + synthetic 0xFD wake-byte
	 * protocol in this phase.  The hardening here only closes the race
	 * where the wake GPIO can fire while the UART is being closed and the
	 * tty pointer has already disappeared.
	 */
	if (port->shutdown_in_progress || !uport->state ||
	    !uport->state->port.tty) {
		IPC_LOG_MSG(port->ipc_log_rx,
			"%s: Ignore wake during shutdown/closed tty\\n", __func__);
		spin_unlock_irqrestore(&uport->lock, flags);
		return IRQ_HANDLED;
	}

	tty = uport->state->port.tty;
	IPC_LOG_MSG(port->ipc_log_rx, "%s: Edge-Count %d\\n", __func__,
							port->edge_count);
	if (port->wakeup_byte && (port->edge_count == 2)) {
		tty_insert_flip_char(tty->port, port->wakeup_byte, TTY_NORMAL);
		IPC_LOG_MSG(port->ipc_log_rx, "%s: Inject 0x%x\\n",
					__func__, port->wakeup_byte);
		port->edge_count = 0;
		tty_flip_buffer_push(tty->port);
		__pm_wakeup_event(port->geni_wake, WAKEBYTE_TIMEOUT_MSEC);
	} else if (port->edge_count < 2) {
		port->edge_count++;
	}
	spin_unlock_irqrestore(&uport->lock, flags);

	IPC_LOG_MSG(port->ipc_log_misc, "%s--\\n", __func__);
	return IRQ_HANDLED;
}
"""

    new_isr = """/*
 * A52 BT-P2B: port the newer Qualcomm asynchronous wake model while
 * retaining the A52's existing GENI DMA/resource implementation.
 *
 * A falling edge on the RX/wake GPIO no longer manufactures a synthetic
 * 0xFD byte.  Instead it queues work, runtime-resumes the UART and waits
 * for the controller's real wake byte to arrive through DMA RX.
 */
static void msm_geni_wakeup_work(struct work_struct *work)
{
	struct msm_geni_serial_port *port;
	struct uart_port *uport;
	unsigned long timeout;

	port = container_of(work, struct msm_geni_serial_port,
			    wakeup_irq_dwork.work);
	uport = &port->uport;

	if (!atomic_read(&port->check_wakeup_byte) ||
	    READ_ONCE(port->shutdown_in_progress))
		return;

	reinit_completion(&port->wakeup_comp);

	/* Close the complete/reinit race with UART shutdown. */
	if (READ_ONCE(port->shutdown_in_progress)) {
		atomic_set(&port->check_wakeup_byte, 0);
		return;
	}

	if (msm_geni_serial_power_on(uport, false)) {
		atomic_set(&port->check_wakeup_byte, 0);
		IPC_LOG_MSG(port->ipc_log_rx,
			"%s: Failed to power on UART\\n", __func__);
		return;
	}

	/*
	 * The Bluetooth HAL normally sees the real 0xFD byte and issues
	 * TIOCPMGET. vote_clock_on() completes wakeup_comp, transferring the
	 * runtime-PM ownership from this temporary worker vote to userspace.
	 */
	timeout = wait_for_completion_timeout(&port->wakeup_comp,
					 msecs_to_jiffies(WAKEBYTE_TIMEOUT_MSEC));
	if (!timeout) {
		IPC_LOG_MSG(port->ipc_log_rx,
			"%s: wake-byte/HAL acknowledgement timeout\\n", __func__);
		atomic_set(&port->check_wakeup_byte, 0);
	}

	msm_geni_serial_power_off(uport, false);
}

static irqreturn_t msm_geni_wakeup_isr(int isr, void *dev)
{
	struct uart_port *uport = dev;
	struct msm_geni_serial_port *port = GET_DEV_PORT(uport);
	unsigned long flags;

	spin_lock_irqsave(&uport->lock, flags);

	if (port->shutdown_in_progress || !uport->state ||
	    !uport->state->port.tty || !port->wakeup_irq_wq) {
		IPC_LOG_MSG(port->ipc_log_rx,
			"%s: Ignore wake during shutdown/closed tty\\n", __func__);
		spin_unlock_irqrestore(&uport->lock, flags);
		return IRQ_HANDLED;
	}

	/* Suppress repeated RX-pin edges belonging to the same wake sequence. */
	if (atomic_read(&port->check_wakeup_byte)) {
		spin_unlock_irqrestore(&uport->lock, flags);
		return IRQ_HANDLED;
	}

	atomic_set(&port->check_wakeup_byte, 1);
	queue_delayed_work(port->wakeup_irq_wq, &port->wakeup_irq_dwork, 0);
	spin_unlock_irqrestore(&uport->lock, flags);

	return IRQ_HANDLED;
}
"""
    text = replace_once(text, old_isr, new_isr, "replace synthetic wake ISR")

    text = replace_once(
        text,
        """	ret = msm_geni_serial_power_on(uport, false);
	if (ret) {
		dev_err(uport->dev, "Failed to vote clock on\\n");
		return ret;
	}
	port->ioctl_count++;
""",
        """	ret = msm_geni_serial_power_on(uport, false);
	if (ret) {
		dev_err(uport->dev, "Failed to vote clock on\\n");
		return ret;
	}

	/*
	 * BT-P2B: a successful HAL clock vote acknowledges the asynchronous
	 * controller wake.  The worker can now release only its temporary PM
	 * reference while this userspace vote keeps the UART available.
	 */
	atomic_set(&port->check_wakeup_byte, 0);
	complete(&port->wakeup_comp);

	port->ioctl_count++;
""",
        "HAL wake acknowledgement",
    )

    helper = """/*
 * BT-P2B: validate the real controller wake byte received after the wake GPIO
 * fired.  While waiting for 0xFD, incomplete/garbled first RX fragments are
 * dropped rather than exposed to the Bluetooth HAL.
 */
static bool msm_geni_find_wakeup_byte(struct uart_port *uport, int size)
{
	struct msm_geni_serial_port *port = GET_DEV_PORT(uport);
	unsigned char *buf = (unsigned char *)port->rx_buf;

	if (size > 0 && buf[0] == port->wakeup_byte) {
		IPC_LOG_MSG(port->ipc_log_rx,
			"%s: Found real wake byte 0x%x\\n",
			__func__, port->wakeup_byte);
		atomic_set(&port->check_wakeup_byte, 0);
		return true;
	}

	IPC_LOG_MSG(port->ipc_log_rx,
		"%s: Wake byte not found in %d bytes, dropping fragment\\n",
		__func__, size);
	return false;
}

"""
    text = replace_once(
        text,
        "static void check_rx_buf(char *buf, struct uart_port *uport, int size)\n",
        helper + "static void check_rx_buf(char *buf, struct uart_port *uport, int size)\n",
        "wake byte helper",
    )

    text = replace_once(
        text,
        """	if (drop_rx)
		goto exit_handle_dma_rx;

	tport = &uport->state->port;
""",
        """	if (drop_rx)
		goto exit_handle_dma_rx;

	/*
	 * After an out-of-band GPIO wake, wait for the controller's real 0xFD
	 * byte instead of synthesizing one in the wake ISR.
	 */
	if (atomic_read(&msm_port->check_wakeup_byte)) {
		if (!msm_geni_find_wakeup_byte(uport, rx_bytes)) {
			memset(msm_port->rx_buf, 0, rx_bytes);
			goto exit_handle_dma_rx;
		}
	}

	tport = &uport->state->port;
""",
        "DMA wake byte validation",
    )

    text = replace_once(
        text,
        """	msm_port->startup_in_progress = true;
	msm_port->shutdown_in_progress = false;
	msm_port->wakeup_enabled = false;
	msm_port->wakeup_irq_wake_set = false;

	if (likely(!uart_console(uport))) {
""",
        """	msm_port->startup_in_progress = true;
	msm_port->shutdown_in_progress = false;
	msm_port->wakeup_enabled = false;
	msm_port->wakeup_irq_wake_set = false;
	atomic_set(&msm_port->check_wakeup_byte, 0);

	if (likely(!uart_console(uport))) {
""",
        "startup async state reset",
    )

    old_start_wake = """	if (msm_port->wakeup_irq > 0) {
		ret = request_irq(msm_port->wakeup_irq, msm_geni_wakeup_isr,
				IRQF_TRIGGER_FALLING | IRQF_ONESHOT,
				"hs_uart_wakeup", uport);
		if (unlikely(ret)) {
			dev_err(uport->dev, "%s:Failed to get WakeIRQ ret%d\\n",
								__func__, ret);
			goto exit_startup;
		}

		/*
		 * Keep the wake line disabled while GENI itself is active.
		 * runtime_suspend() arms the GPIO wake path immediately before
		 * the UART is allowed to sleep.
		 */
		disable_irq(msm_port->wakeup_irq);
	}
"""
    new_start_wake = """	if (msm_port->wakeup_irq > 0) {
		msm_port->wakeup_irq_wq =
			alloc_workqueue("a52_bt_wake", WQ_HIGHPRI, 1);
		if (!msm_port->wakeup_irq_wq) {
			dev_err(uport->dev, "%s:Wake workqueue allocation failed\\n",
				__func__);
			ret = -ENOMEM;
			goto exit_startup;
		}
		INIT_DELAYED_WORK(&msm_port->wakeup_irq_dwork,
				  msm_geni_wakeup_work);

		ret = request_irq(msm_port->wakeup_irq, msm_geni_wakeup_isr,
				IRQF_TRIGGER_FALLING | IRQF_ONESHOT,
				"hs_uart_wakeup", uport);
		if (unlikely(ret)) {
			dev_err(uport->dev, "%s:Failed to get WakeIRQ ret%d\\n",
								__func__, ret);
			destroy_workqueue(msm_port->wakeup_irq_wq);
			msm_port->wakeup_irq_wq = NULL;
			goto exit_startup;
		}

		/*
		 * Keep the wake line disabled while GENI itself is active.
		 * runtime_suspend() arms it; the first falling edge queues the
		 * asynchronous UART-resume worker.
		 */
		disable_irq(msm_port->wakeup_irq);
	}
"""
    text = replace_once(text, old_start_wake, new_start_wake,
                        "startup wake workqueue")

    text = replace_once(
        text,
        """	/* Complete signals to handle cancel cmd completion */
	init_completion(&dev_port->m_cmd_timeout);
	init_completion(&dev_port->s_cmd_timeout);
""",
        """	/* Complete signals to handle cancel cmd completion */
	init_completion(&dev_port->m_cmd_timeout);
	init_completion(&dev_port->s_cmd_timeout);
	init_completion(&dev_port->wakeup_comp);
""",
        "wakeup completion init",
    )

    text = replace_once(
        text,
        """	IPC_LOG_MSG(msm_port->ipc_log_misc, "%s:\\n", __func__);
	if (!uart_console(uport))
		msm_port->shutdown_in_progress = true;

	/* Stop the console before stopping the current tx */
""",
        """	IPC_LOG_MSG(msm_port->ipc_log_misc, "%s:\\n", __func__);
	if (!uart_console(uport)) {
		msm_port->shutdown_in_progress = true;

		/*
		 * Stop any queued/running asynchronous wake before dismantling the
		 * IRQ and runtime-PM state.  complete_all() lets a worker already
		 * waiting for HAL acknowledgement leave without a two-second stall.
		 */
		atomic_set(&msm_port->check_wakeup_byte, 0);
		complete_all(&msm_port->wakeup_comp);
		if (msm_port->wakeup_irq_wq)
			cancel_delayed_work_sync(&msm_port->wakeup_irq_dwork);
	}

	/* Stop the console before stopping the current tx */
""",
        "shutdown async cancellation",
    )

    text = replace_once(
        text,
        """		if (msm_port->wakeup_irq > 0) {
			if (msm_port->wakeup_enabled) {
				disable_irq(msm_port->wakeup_irq);
				msm_port->wakeup_enabled = false;
			}
			if (msm_port->wakeup_irq_wake_set) {
				ret = irq_set_irq_wake(msm_port->wakeup_irq, 0);
				if (unlikely(ret))
					dev_err(uport->dev,
						"%s:Failed to unset WakeIRQ:%d\\n",
						__func__, ret);
				msm_port->wakeup_irq_wake_set = false;
			}
			free_irq(msm_port->wakeup_irq, uport);
		}
""",
        """		if (msm_port->wakeup_irq > 0) {
			if (msm_port->wakeup_enabled) {
				disable_irq(msm_port->wakeup_irq);
				msm_port->wakeup_enabled = false;
			}
			if (msm_port->wakeup_irq_wake_set) {
				ret = irq_set_irq_wake(msm_port->wakeup_irq, 0);
				if (unlikely(ret))
					dev_err(uport->dev,
						"%s:Failed to unset WakeIRQ:%d\\n",
						__func__, ret);
				msm_port->wakeup_irq_wake_set = false;
			}
			free_irq(msm_port->wakeup_irq, uport);
			if (msm_port->wakeup_irq_wq) {
				destroy_workqueue(msm_port->wakeup_irq_wq);
				msm_port->wakeup_irq_wq = NULL;
			}
		}
""",
        "shutdown workqueue destroy",
    )

    text = replace_once(
        text,
        """	if (!uart_console(&port->uport))
		wakeup_source_unregister(port->geni_wake);

	uart_remove_one_port(drv, &port->uport);
""",
        """	if (!uart_console(&port->uport))
		wakeup_source_unregister(port->geni_wake);

	uart_remove_one_port(drv, &port->uport);
	if (port->wakeup_irq_wq) {
		cancel_delayed_work_sync(&port->wakeup_irq_dwork);
		destroy_workqueue(port->wakeup_irq_wq);
		port->wakeup_irq_wq = NULL;
	}
""",
        "remove workqueue fallback",
    )

    # Phase96 still resets edge_count during runtime suspend.  It is no
    # longer part of wake detection in P2B, so remove that obsolete reset.
    text = replace_once(
        text,
        """		port->edge_count = 0;
		if (!port->wakeup_irq_wake_set) {
""",
        """		if (!port->wakeup_irq_wake_set) {
""",
        "remove edge-count suspend reset",
    )

    text += "\n/* " + P2B_MARKER + " */\n"
    path.write_text(text)

    # Strong invariants: P2B must really remove the synthetic-byte mechanism.
    final = path.read_text()
    if "tty_insert_flip_char(tty->port, port->wakeup_byte" in final:
        raise SystemExit("synthetic wake byte injection still present")
    if "port->edge_count == 2" in final:
        raise SystemExit("edge-count wake detection still present")
    if "msm_geni_wakeup_work" not in final:
        raise SystemExit("async wake worker missing")
    if "msm_geni_find_wakeup_byte" not in final:
        raise SystemExit("real wake-byte RX validation missing")

    print(f"patched {path}")
    print(P2B_MARKER)


if __name__ == "__main__":
    main()
