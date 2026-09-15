#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "A52 BT-P2A: Qualcomm GENI wake/shutdown hardening"


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

    if MARKER in text:
        print("BT-P2A already applied")
        return

    text = replace_once(
        text,
        """	bool manual_flow;
	bool is_clk_aon;
	struct msm_geni_serial_ver_info ver_info;
""",
        """	bool manual_flow;
	bool is_clk_aon;
	/* A52 BT-P2A: explicit wake IRQ lifecycle and shutdown state. */
	bool wakeup_enabled;
	bool wakeup_irq_wake_set;
	bool shutdown_in_progress;
	struct msm_geni_serial_ver_info ver_info;
""",
        "struct wake state",
    )

    old_isr = """static irqreturn_t msm_geni_wakeup_isr(int isr, void *dev)
{
	struct uart_port *uport = dev;
	struct msm_geni_serial_port *port = GET_DEV_PORT(uport);
	struct tty_struct *tty;
	unsigned long flags;

	IPC_LOG_MSG(port->ipc_log_misc, "%s++\\n", __func__);

	spin_lock_irqsave(&uport->lock, flags);
	IPC_LOG_MSG(port->ipc_log_rx, "%s: Edge-Count %d\\n", __func__,
							port->edge_count);
	if (port->wakeup_byte && (port->edge_count == 2)) {
		tty = uport->state->port.tty;
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
    new_isr = """static irqreturn_t msm_geni_wakeup_isr(int isr, void *dev)
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
    text = replace_once(text, old_isr, new_isr, "wake ISR")

    text = replace_once(
        text,
        """	IPC_LOG_MSG(msm_port->ipc_log_misc, "%s:\\n", __func__);
	/* Stop the console before stopping the current tx */
""",
        """	IPC_LOG_MSG(msm_port->ipc_log_misc, "%s:\\n", __func__);
	if (!uart_console(uport))
		msm_port->shutdown_in_progress = true;

	/* Stop the console before stopping the current tx */
""",
        "shutdown state entry",
    )

    text = replace_once(
        text,
        """		if (msm_port->wakeup_irq > 0) {
			irq_set_irq_wake(msm_port->wakeup_irq, 0);
			disable_irq(msm_port->wakeup_irq);
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
		}
""",
        "shutdown wake cleanup",
    )

    text = replace_once(
        text,
        """	msm_port->startup_in_progress = true;

	if (likely(!uart_console(uport))) {
""",
        """	msm_port->startup_in_progress = true;
	msm_port->shutdown_in_progress = false;
	msm_port->wakeup_enabled = false;
	msm_port->wakeup_irq_wake_set = false;

	if (likely(!uart_console(uport))) {
""",
        "startup wake state reset",
    )

    text = replace_once(
        text,
        """	if (msm_port->wakeup_irq > 0) {
		ret = request_irq(msm_port->wakeup_irq, msm_geni_wakeup_isr,
				IRQF_TRIGGER_FALLING | IRQF_ONESHOT,
				"hs_uart_wakeup", uport);
		if (unlikely(ret)) {
			dev_err(uport->dev, "%s:Failed to get WakeIRQ ret%d\\n",
								__func__, ret);
			goto exit_startup;
		}
		disable_irq(msm_port->wakeup_irq);
		ret = irq_set_irq_wake(msm_port->wakeup_irq, 1);
		if (unlikely(ret)) {
			dev_err(uport->dev, "%s:Failed to set IRQ wake:%d\\n",
					__func__, ret);
			goto exit_startup;
		}
	}
""",
        """	if (msm_port->wakeup_irq > 0) {
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
""",
        "startup dynamic wake arming",
    )

    text = replace_once(
        text,
        """	if (port->wakeup_irq > 0) {
		port->edge_count = 0;
		enable_irq(port->wakeup_irq);
	}
	IPC_LOG_MSG(port->ipc_log_pwr, "%s: End\\n", __func__);
""",
        """	/*
	 * A52 BT-P2A: only arm the out-of-band wake GPIO while the UART is
	 * runtime-suspended and its tty is still open.  This mirrors newer
	 * Qualcomm GENI lifecycle handling without changing Samsung's wake
	 * byte protocol.
	 */
	if (!port->shutdown_in_progress && port->wakeup_irq > 0 &&
	    port->uport.state && port->uport.state->port.tty) {
		port->edge_count = 0;
		if (!port->wakeup_irq_wake_set) {
			ret = irq_set_irq_wake(port->wakeup_irq, 1);
			if (unlikely(ret)) {
				dev_err(dev, "%s:Failed to set WakeIRQ:%d\\n",
					__func__, ret);
				/* Runtime wake still works even if system-wake setup fails. */
				ret = 0;
			} else {
				port->wakeup_irq_wake_set = true;
			}
		}
		if (!port->wakeup_enabled) {
			enable_irq(port->wakeup_irq);
			port->wakeup_enabled = true;
		}
	}
	IPC_LOG_MSG(port->ipc_log_pwr, "%s: End\\n", __func__);
""",
        "runtime suspend wake arming",
    )

    text = replace_once(
        text,
        """	__pm_relax(port->geni_wake);
	__pm_stay_awake(port->geni_wake);
	if (port->wakeup_irq > 0)
		disable_irq(port->wakeup_irq);
	/*
	 * Resources On.
""",
        """	__pm_relax(port->geni_wake);
	__pm_stay_awake(port->geni_wake);

	/* Disarm the wake GPIO before bringing the normal GENI IRQ back. */
	if (port->wakeup_irq > 0 && port->wakeup_enabled) {
		disable_irq(port->wakeup_irq);
		port->wakeup_enabled = false;
	}
	if (port->wakeup_irq > 0 && port->wakeup_irq_wake_set) {
		ret = irq_set_irq_wake(port->wakeup_irq, 0);
		if (unlikely(ret))
			dev_err(dev, "%s:Failed to unset WakeIRQ:%d\\n",
				__func__, ret);
		port->wakeup_irq_wake_set = false;
		ret = 0;
	}
	/*
	 * Resources On.
""",
        "runtime resume wake disarming",
    )

    text += "\n/* " + MARKER + " */\n"
    path.write_text(text)
    print(f"patched {path}")
    print(MARKER)


if __name__ == "__main__":
    main()
