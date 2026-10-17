#!/usr/bin/env python3
"""446U Part B: explicit ttyGS0 kthread/console quiesce BEFORE g_serial unbind.

Fail closed: if the Phase380 console thread has not terminated within 200ms,
do not unregister g_serial or bind ConfigFS. a52.usb_handover=0 is an opt-out.
"""
import argparse
from pathlib import Path

TAG="A52_PHASE446U_CONSOLE_FIRST_ADB_HANDOVER_V1"
SERIAL=Path("drivers/usb/gadget/legacy/serial.c")
CONSOLE=Path("drivers/usb/gadget/function/u_serial.c")
CONFIGFS=Path("drivers/usb/gadget/configfs.c")

def once(s,a,b,label):
    if s.count(a)!=1:raise RuntimeError("446U USB "+label+" anchor count="+str(s.count(a)))
    return s.replace(a,b,1)

def patch_console(s):
    if TAG in s:return s
    for check in ("A52_PHASE380_USB_KTHREAD_CONSOLE_V1","struct task_struct\t*task;",
                  "static int a52_gs_console_thread(void *data)"):
        if check not in s:raise RuntimeError("446U USB console prerequisite absent "+check)
    s=once(s,
           "struct task_struct\t*task;\n\twait_queue_head_t\twait;",
           '''struct task_struct	*task;
	struct completion	a52u_exited;
	bool			a52u_stopping;
	bool			a52u_detached;
	wait_queue_head_t	wait;''',"console fields")
    s=once(s,
           "while (!kthread_should_stop()) {\n\t\twait_event_interruptible(cons->wait,\n\t\t\tkthread_should_stop() || atomic_xchg(&cons->kick, 0));\n\t\tif (kthread_should_stop())\n\t\t\tbreak;",
           '''while (!kthread_should_stop() && !READ_ONCE(cons->a52u_stopping)) {
		wait_event_interruptible(cons->wait,
			kthread_should_stop() || READ_ONCE(cons->a52u_stopping) ||
			atomic_xchg(&cons->kick, 0));
		if (kthread_should_stop() || READ_ONCE(cons->a52u_stopping))
			break;''',"thread bounded cooperative stop")
    s=once(s,
           "\treturn 0;\n}\n\nstatic void gs_console_write(",
           "\tcomplete(&cons->a52u_exited);\n\treturn 0;\n}\n\nstatic void gs_console_write(",
           "thread stopped completion")
    # Avoid *any* new FIFO writes after requesting detach. The rest of
    # Phase380 normal console write behavior remains untouched.
    needle="struct gs_console *cons = container_of(co, struct gs_console, console);\n\tunsigned long flags;\n\tsize_t n;"
    s=once(s,needle,needle+'''
	/* Phase380 declaration order preserved. */
	if (unlikely(READ_ONCE(cons->a52u_stopping)))
		return;
''',"console no-write gate")
    s=once(s,
        "\tinit_waitqueue_head(&cons->wait);\n\tatomic_set(&cons->kick, 0);",
        '''	init_waitqueue_head(&cons->wait);
	init_completion(&cons->a52u_exited);
	cons->a52u_stopping = false;
	cons->a52u_detached = false;
	atomic_set(&cons->kick, 0);''',"console state init")
    s=once(s,
        "\tunregister_console(&cons->console);\n\n\tspin_lock_irq(&port->port_lock);",
        "\tif (!cons->a52u_detached)\n\t\tunregister_console(&cons->console);\n\n\tspin_lock_irq(&port->port_lock);",
        "avoid unregister twice during unbind")
    s=once(s,
        "\tif (cons->task)\n\t\tkthread_stop(cons->task);\n\tkfifo_free(&cons->buf);",
        "\tif (cons->task)\n\t\tkthread_stop(cons->task);\n\tkfifo_free(&cons->buf);",
        "Phase380 exit unchanged")

    # Exported via u_serial.h and called by legacy serial.c in sleepable
    # ConfigFS write context while endpoints and port are still allocated.
    anchor="ssize_t gserial_set_console(unsigned char port_num, const char *page, size_t count)"
    helper=r'''
/* A52_PHASE446U_CONSOLE_FIRST_ADB_HANDOVER_V1 */
int a52_446u_detach_console(unsigned char port_num)
{
	struct gs_port *port;
	struct gs_console *cons;
	unsigned long flags;
	unsigned long timeout;
	int ret = 0;
	unsigned int fifo;

	if (port_num >= MAX_U_SERIAL_PORTS)
		return -EINVAL;
	mutex_lock(&ports[port_num].lock);
	port = ports[port_num].port;
	if (!port || !port->console) {
		a52_ackfr_record("P446U USB CONSOLE absent port=%u", port_num);
		goto out;
	}
	cons = port->console;
	if (cons->a52u_detached)
		goto out;
	a52_ackfr_record("P446U USB DETACH_BEGIN port=%u task=%u",
			 port_num, !!cons->task);
	WRITE_ONCE(cons->a52u_stopping, true);
	atomic_set(&cons->kick, 1);
	wake_up_interruptible(&cons->wait);
	if (cons->task) {
		timeout = wait_for_completion_timeout(&cons->a52u_exited,
						      msecs_to_jiffies(200));
		if (!timeout) {
			/* Never enter gs_unbind with a live console worker. */
			ret = -ETIMEDOUT;
			a52_ackfr_record("P446U USB DETACH_TIMEOUT port=%u",
					 port_num);
			WRITE_ONCE(cons->a52u_stopping, false);
			goto out;
		}
		kthread_stop(cons->task);
		cons->task = NULL;
	}
	spin_lock_irqsave(&cons->lock, flags);
	fifo = kfifo_len(&cons->buf);
	kfifo_reset(&cons->buf);
	spin_unlock_irqrestore(&cons->lock, flags);
	a52_ackfr_record("P446U USB KTHREAD_STOPPED port=%u fifo_dropped=%u",
			 port_num, fifo);
	/* No call to console printk may enter ttyGS0 after unregister. */
	ret = unregister_console(&cons->console);
	a52_ackfr_record("P446U USB UNREGISTER_CONSOLE rc=%d", ret);
	if (ret)
		goto out;
	cons->a52u_detached = true;
	a52_ackfr_record("P446U USB DETACH_END port=%u", port_num);
out:
	mutex_unlock(&ports[port_num].lock);
	return ret;
}
EXPORT_SYMBOL_GPL(a52_446u_detach_console);

'''
    s=once(s,anchor,helper+anchor,"exported detach helper")
    if '#include <linux/completion.h>' not in s:s='#include <linux/completion.h>\n#include <linux/jiffies.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n'+s
    return s

def patch_serial(s):
    if TAG in s:return s
    anchor="static int __init init(void)\n{"
    block=r'''
/* A52_PHASE446U_CONSOLE_FIRST_ADB_HANDOVER_V1
 * Default enabled. Add a52.usb_handover=0 to the kernel boot cmdline
 * to preserve the original ttyGS0 COM3 for the entire boot.
 */
static bool a52u_usb_handover = true;
static int __init a52u_usb_handover_arg(char *str)
{
	if (!str) return -EINVAL;
	if (str[0] == '0' && !str[1])
		a52u_usb_handover = false;
	else if (str[0] == '1' && !str[1])
		a52u_usb_handover = true;
	else
		return -EINVAL;
	return 0;
}
early_param("a52.usb_handover", a52u_usb_handover_arg);
extern int a52_446u_detach_console(unsigned char port_num);

int a52_446u_release_early_serial(void)
{
	int ret;
	if (!a52u_usb_handover) {
		a52_ackfr_record("P446U USB HANDOVER_DISABLED COM3_PRESERVED");
		return 0; /* ConfigFS will correctly see the still-busy UDC. */
	}
	if (!enable)
		return 0;
	a52_ackfr_record("P446U USB RELEASE_BEGIN");
	ret = a52_446u_detach_console(0);
	if (ret) {
		a52_ackfr_record("P446U USB RELEASE_ABORT detach_rc=%d", ret);
		return ret;
	}
	/* ttyGS0 is removed from the kernel console list before this call. */
	a52_ackfr_record("P446U USB GS_UNREGISTER_BEGIN");
	ret = switch_gserial_enable(false);
	a52_ackfr_record("P446U USB GS_UNREGISTER_END rc=%d", ret);
	if (ret)
		return ret;
	enable = false;
	return 0;
}
EXPORT_SYMBOL_GPL(a52_446u_release_early_serial);

'''
    s=once(s,anchor,block+anchor,"serial handover exported helper")
    # 5.10 gs_unbind declares 'int i;' first. Do not insert a statement
    # before it: -Werror=declaration-after-statement. Fail closed if that
    # function prologue ever changes in the reconstructed GKI tree.
    gs_prologue="static int gs_unbind(struct usb_composite_dev *cdev)\n{\n\tint i;\n"
    if s.count(gs_prologue)!=1:
        raise RuntimeError("446U gs_unbind exact declaration prologue mismatch")
    s=s.replace(gs_prologue,
                gs_prologue+'\n\ta52_ackfr_record("P446U USB GS_UNBIND_BEGIN");\n',
                1)
    start=s.index("static int gs_unbind(")
    end=s.index("\n}",start)
    part=s[start:end]
    if part.count("return 0;")!=1:raise RuntimeError("446U gs_unbind end ambiguous")
    part=part.replace("return 0;",'''a52_ackfr_record("P446U USB GS_UNBIND_END rc=0");
	return 0;''')
    s=s[:start]+part+s[end:]
    if '#include <linux/a52_ack_secure_flight_recorder.h>' not in s:
        s='#include <linux/a52_ack_secure_flight_recorder.h>\n'+s
    return s

def patch_configfs(s):
    if TAG in s:return s
    sig="static ssize_t gadget_dev_desc_UDC_store(struct config_item *item,"
    declaration='''/* A52_PHASE446U_CONSOLE_FIRST_ADB_HANDOVER_V1 */
#if IS_BUILTIN(CONFIG_USB_G_SERIAL)
extern int a52_446u_release_early_serial(void);
#endif

'''
    s=once(s,sig,declaration+sig,"ConfigFS declaration")
    needle='''		gi->composite.gadget_driver.udc_name = name;
		ret = usb_gadget_probe_driver(&gi->composite.gadget_driver);'''
    block=r'''		/* A52 446U: every step before UDC bind is synchronous. */
#if IS_BUILTIN(CONFIG_USB_G_SERIAL)
		if (!strcmp(name, "a600000.dwc3")) {
			a52_ackfr_record("P446U USB UDC_REQUEST name=a600000.dwc3");
			ret = a52_446u_release_early_serial();
			if (ret)
				goto err;
		}
#endif
		gi->composite.gadget_driver.udc_name = name;
		ret = usb_gadget_probe_driver(&gi->composite.gadget_driver);
		a52_ackfr_record("P446U USB CONFIGFS_BIND rc=%d", ret);'''
    s=once(s,needle,block,"ConfigFS release before bind")
    if '#include <linux/a52_ack_secure_flight_recorder.h>' not in s:
        s='#include <linux/a52_ack_secure_flight_recorder.h>\n'+s
    return s

def check(root):
    rules={
      CONSOLE:(TAG,"P446U USB DETACH_BEGIN","P446U USB DETACH_END","P446U USB DETACH_TIMEOUT",
               "P446U USB KTHREAD_STOPPED","P446U USB UNREGISTER_CONSOLE",
               "wait_for_completion_timeout","kfifo_reset","unregister_console(&cons->console)"),
      SERIAL:(TAG,'early_param("a52.usb_handover"',"P446U USB GS_UNBIND_BEGIN",
              "P446U USB GS_UNBIND_END","P446U USB GS_UNREGISTER_BEGIN",
              "P446U USB GS_UNREGISTER_END","P446U USB HANDOVER_DISABLED"),
      CONFIGFS:(TAG,"P446U USB UDC_REQUEST","P446U USB CONFIGFS_BIND",
                'strcmp(name, "a600000.dwc3")',"a52_446u_release_early_serial()")
    }
    for f,words in rules.items():
        src=(root/f).read_text()
        for word in words:
            if word not in src:raise RuntimeError(f"446U USB missing {word} in {f}")
    print("P446U B AUDIT PASS: 200ms bounded stop -> FIFO drop -> unregister console -> g_serial unregister -> ConfigFS UDC; flag default ON")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    if not a.check_only:
        for f,fn in ((CONSOLE,patch_console),(SERIAL,patch_serial),(CONFIGFS,patch_configfs)):
            file=a.root/f
            file.write_text(fn(file.read_text()))
    check(a.root)
if __name__=="__main__": main()
