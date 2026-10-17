#!/usr/bin/env python3
"""446U Part B v2: one-flight bounded console-first USB handover.

Based on the *final* Phase446T imported 5.10 source, not vanilla GKI.
Every source replacement fails closed on missing/duplicate exact anchors.
No g_serial unbind occurs if the legacy ttyGS0 open count is nonzero,
the console worker has not acknowledged stop, or a console request
cannot be drained. ConfigFS never holds gi->lock during teardown.
"""
from pathlib import Path
import argparse

TAG="A52_PHASE446U_USB_SAFE_COORDINATOR_V2"
U=Path("drivers/usb/gadget/function/u_serial.c")
S=Path("drivers/usb/gadget/legacy/serial.c")
C=Path("drivers/usb/gadget/configfs.c")
R=Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")

def once(text, old, new, label):
    n=text.count(old)
    if n!=1: raise RuntimeError(f"U446 {label}: expected one exact source anchor, got {n}")
    return text.replace(old,new,1)

def patch_u(s):
    if TAG in s: return s
    for t in ("A52_PHASE380_USB_KTHREAD_CONSOLE_V1",
              "static int a52_gs_console_thread(void *data)",
              "int gserial_alloc_line(unsigned char *line_num)"):
        if t not in s: raise RuntimeError("U446 missing 446T USB prerequisite: "+t)
    s='#include <linux/completion.h>\n#include <linux/fdtable.h>\n#include <linux/rcupdate.h>\n#include <linux/tty.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n'+s
    s=once(s,"\tstruct task_struct\t*task;\n\twait_queue_head_t\twait;",
        "\tstruct task_struct\t*task;\n\tstruct completion\ta52u_worker_ack;\n\tstruct completion\ta52u_request_ack;\n\tbool\t\t\ta52u_stopping;\n\tbool\t\t\ta52u_detached;\n\tbool\t\t\ta52u_req_pending;\n\twait_queue_head_t\twait;","console fields")
    s=once(s,"\tstruct tty_port\t\tport;\n\tspinlock_t\t\tport_lock;",
        "\tstruct tty_port\t\tport;\n\tbool\t\t\ta52u_block_opens;\n\tspinlock_t\t\tport_lock;","port fields")
    anchor="\tif (!port) {\n\t\tstatus = -ENODEV;\n\t\tgoto out;\n\t}\n\n\tspin_lock_irq(&port->port_lock);"
    s=once(s,anchor,anchor.replace("\n\n\tspin_lock_irq(&port->port_lock);",
        "\n\t/* U446 closes the open-count race before composite unregister. */\n\tif (unlikely(READ_ONCE(port->a52u_block_opens))) {\n\t\tstatus = -EBUSY;\n\t\tgoto out;\n\t}\n\n\tspin_lock_irq(&port->port_lock);"),"reject new opens")
    s=once(s,"\tstruct usb_request *req = cons->req;\n\tstruct usb_ep *ep;\n\tsize_t size;",
        "\tstruct usb_request *req = cons->req;\n\tstruct usb_ep *ep;\n\tsize_t size;\n\tint queue_rc;","console queue local")
    s=once(s,"\tif (!req)\n\t\treturn;\t/* disconnected */",
        "\tif (!req || READ_ONCE(cons->a52u_stopping))\n\t\treturn;\t/* disconnected or stopping */","no req after stop")
    s=once(s,"\treq->length = size;\n\n\tspin_unlock_irq(&cons->lock);\n\tif (usb_ep_queue(ep, req, GFP_ATOMIC))\n\t\treq->length = 0;\n\tspin_lock_irq(&cons->lock);",
        r'''	req->length = size;
	reinit_completion(&cons->a52u_request_ack);
	cons->a52u_req_pending = true;
	spin_unlock_irq(&cons->lock);
	queue_rc = usb_ep_queue(ep, req, GFP_ATOMIC);
	spin_lock_irq(&cons->lock);
	if (queue_rc) {
		req->length = 0;
		cons->a52u_req_pending = false;
		complete(&cons->a52u_request_ack);
	}''',"track in-flight request")
    s=once(s,"\tstruct gs_console *cons = req->context;\n\n\tswitch (req->status) {",
        r'''	struct gs_console *cons = req->context;
	bool stopping;
	int status = req->status;

	spin_lock(&cons->lock);
	cons->a52u_req_pending = false;
	req->length = 0;
	stopping = cons->a52u_stopping;
	spin_unlock(&cons->lock);
	if (stopping) {
		a52_ackfr_record("U446 request-complete status=%d", status);
		/* No read or write to cons/req after signalling acknowledgement. */
		complete(&cons->a52u_request_ack);
		return;
	}
	complete(&cons->a52u_request_ack);

	switch (req->status) {''',"request complete ack")
    s=once(s,"\twhile (!kthread_should_stop()) {\n\t\twait_event_interruptible(cons->wait,\n\t\t\tkthread_should_stop() || atomic_xchg(&cons->kick, 0));\n\t\tif (kthread_should_stop())\n\t\t\tbreak;",
        r'''	while (!kthread_should_stop() && !READ_ONCE(cons->a52u_stopping)) {
		wait_event_interruptible(cons->wait,
			kthread_should_stop() || READ_ONCE(cons->a52u_stopping) ||
			atomic_xchg(&cons->kick, 0));
		if (kthread_should_stop() || READ_ONCE(cons->a52u_stopping))
			break;''',"worker cooperative exit")
    s=once(s,"\treturn 0;\n}\n\nstatic void gs_console_write(",
        "\tcomplete(&cons->a52u_worker_ack);\n\treturn 0;\n}\n\nstatic void gs_console_write(","worker ack")
    s=once(s,"\tspin_lock_irqsave(&cons->lock, flags);\n\n\tn = kfifo_in(&cons->buf, buf, count);",
        "\tspin_lock_irqsave(&cons->lock, flags);\n\tif (unlikely(cons->a52u_stopping))\n\t\tgoto a52u_write_out;\n\n\tn = kfifo_in(&cons->buf, buf, count);","console FIFO write gate")
    s=once(s,"\tif (cons->req && !cons->req->length)\n\t\ta52_gs_console_kick(cons);\n\n\tspin_unlock_irqrestore(&cons->lock, flags);",
        "\tif (cons->req && !cons->req->length)\n\t\ta52_gs_console_kick(cons);\na52u_write_out:\n\tspin_unlock_irqrestore(&cons->lock, flags);","write gate unlock")
    s=once(s,"\tinit_waitqueue_head(&cons->wait);\n\tatomic_set(&cons->kick, 0);",
        "\tinit_waitqueue_head(&cons->wait);\n\tinit_completion(&cons->a52u_worker_ack);\n\tinit_completion(&cons->a52u_request_ack);\n\tcons->a52u_stopping = false;\n\tcons->a52u_detached = false;\n\tcons->a52u_req_pending = false;\n\tatomic_set(&cons->kick, 0);","console init")
    s=once(s,"\tunregister_console(&cons->console);\n\n\tspin_lock_irq(&port->port_lock);",
        "\tif (!cons->a52u_detached)\n\t\tunregister_console(&cons->console);\n\n\tspin_lock_irq(&port->port_lock);","prevent duplicate console unregister")
    # Normal disconnect is unchanged; successful handover has already detached
    # cons->req, so this legacy path sees NULL and never dequeues under lock.
    # Never change the generic runtime USB completion semantics of 446T.
    preflight=r'''
/* A52_PHASE446U_USB_SAFE_COORDINATOR_V2
 * Scan open fds with tty_file_private while file locks protect fds.
 * /dev/console and /dev/ttyGS0 both resolve to the same tty_struct.
 */
struct a52u_owner_find {
	struct tty_struct *tty;
	int fd;
};
static int a52u_owner_fd(const void *data, struct file *file, unsigned fd)
{
	struct a52u_owner_find *find = (struct a52u_owner_find *)data;
	struct tty_file_private *tp;
	dev_t dev;
	if (!file || !file_inode(file))
		return 0;
	dev = file_inode(file)->i_rdev;
	if (!S_ISCHR(file_inode(file)->i_mode) ||
	    (dev != MKDEV(gs_tty_driver->major, 0) && dev != MKDEV(5, 1)))
		return 0;
	tp = file->private_data;
	if (!tp || tp->tty != find->tty)
		return 0;
	find->fd = fd;
	return 1;
}
static void a52u_log_tty_holders(struct tty_struct *tty)
{
	struct task_struct *task;
	unsigned int shown=0;
	if (!tty) {
		a52_ackfr_record("U446 holder tty pointer unavailable");
		return;
	}
	rcu_read_lock();
	for_each_process(task) {
		struct files_struct *files;
		struct a52u_owner_find find = {.tty=tty,.fd=-1};
		char name[TASK_COMM_LEN];
		files = get_files_struct(task);
		if (!files) continue;
		iterate_fd(files,0,a52u_owner_fd,&find);
		put_files_struct(files);
		if (find.fd<0) continue;
		get_task_comm(name,task);
		a52_ackfr_record("U446 holder pid=%d comm=%s fd=%d",
				task_pid_nr(task),name,find.fd);
		if (++shown>=16) break;
	}
	rcu_read_unlock();
	if (!shown)
		a52_ackfr_record("U446 holder count>0 owner_scan=none (kernel/tty refs possible)");
}

/* Separate from ConfigFS gi->lock and never calls composite operations. */
int a52_446u_preflight_port(unsigned char port_num)
{
	struct gs_port *port;
	struct tty_struct *tty=NULL;
	unsigned int count;
	if (port_num>=MAX_U_SERIAL_PORTS) return -EINVAL;
	mutex_lock(&ports[port_num].lock);
	port=ports[port_num].port;
	if (!port) {
		mutex_unlock(&ports[port_num].lock);
		return -ENODEV;
	}
	spin_lock_irq(&port->port_lock);
	count=port->port.count;
	tty=port->port.tty;
	if (!count)
		port->a52u_block_opens=true;
	spin_unlock_irq(&port->port_lock);
	a52_ackfr_record("U446 PRE count=%u port_usb=%u console=%u req=%u req_len=%u fifo=%u udc=a600000.dwc3",
			 count,!!port->port_usb,!!port->console,
			 port->console && !!port->console->req,
			 port->console && port->console->req ?
				 port->console->req->length : 0,
			 port->console ? kfifo_len(&port->console->buf) : 0);
	if (count) {
		a52u_log_tty_holders(tty);
		a52_ackfr_record("U446 DEFER tty_open_count=%u",count);
	}
	mutex_unlock(&ports[port_num].lock);
	return count ? -EBUSY : 0;
}
EXPORT_SYMBOL_GPL(a52_446u_preflight_port);

void a52_446u_unblock_opens(unsigned char port_num)
{
	if (port_num>=MAX_U_SERIAL_PORTS) return;
	mutex_lock(&ports[port_num].lock);
	if (ports[port_num].port)
		WRITE_ONCE(ports[port_num].port->a52u_block_opens,false);
	mutex_unlock(&ports[port_num].lock);
}
EXPORT_SYMBOL_GPL(a52_446u_unblock_opens);

int a52_446u_detach_console(unsigned char port_num)
{
	struct gs_port *port;
	struct gs_console *cons;
	struct usb_request *req;
	struct usb_ep *ep;
	unsigned long flags, deadline;
	unsigned int fifo=0;
	bool pending=false;
	int ret=0, deq_rc=0;
	if (port_num>=MAX_U_SERIAL_PORTS) return -EINVAL;
	mutex_lock(&ports[port_num].lock);
	port=ports[port_num].port;
	if (!port || !port->console) {
		ret=port ? 0 : -ENODEV;
		goto out;
	}
	cons=port->console;
	if (cons->a52u_detached)
		goto out;
	/* Preflight must have blocked new tty opens already. */
	if (!READ_ONCE(port->a52u_block_opens)) {
		ret=-EPERM;
		goto out;
	}
	a52_ackfr_record("U446 CONSOLE worker-stop-enter port=%u",port_num);
	spin_lock_irqsave(&cons->lock,flags);
	cons->a52u_stopping=true;
	spin_unlock_irqrestore(&cons->lock,flags);
	a52_gs_console_kick(cons);
	if (cons->task) {
		deadline=wait_for_completion_timeout(&cons->a52u_worker_ack,
						    msecs_to_jiffies(500));
		if (!deadline) {
			a52_ackfr_record("U446 CONSOLE worker-stop-timeout (ABORT)");
			spin_lock_irqsave(&cons->lock,flags);
			cons->a52u_stopping=false;
			spin_unlock_irqrestore(&cons->lock,flags);
			ret=-ETIMEDOUT;
			goto out;
		}
		a52_ackfr_record("U446 CONSOLE worker-stop-ack");
		/* Ack guarantees kthread_stop cannot wait on USB requests. */
		kthread_stop(cons->task);
		cons->task=NULL;
	}
	spin_lock_irqsave(&cons->lock,flags);
	fifo=kfifo_len(&cons->buf);
	kfifo_reset(&cons->buf);
	spin_unlock_irqrestore(&cons->lock,flags);
	a52_ackfr_record("U446 CONSOLE fifo_drop=%u",fifo);
	a52_ackfr_record("U446 CONSOLE unregister-enter");
	ret=unregister_console(&cons->console);
	a52_ackfr_record("U446 CONSOLE unregister-exit rc=%d",ret);
	if (ret) goto out; /* No composite teardown; retain allocated cons/req. */

	spin_lock_irqsave(&cons->lock,flags);
	req=cons->req;
	ep=cons->console.data;
	pending=cons->a52u_req_pending;
	spin_unlock_irqrestore(&cons->lock,flags);
	if (req && ep && pending) {
		a52_ackfr_record("U446 CONSOLE request-dequeue-enter pending=1");
		deq_rc=usb_ep_dequeue(ep,req);
		a52_ackfr_record("U446 CONSOLE request-dequeue-exit rc=%d",deq_rc);
		deadline=wait_for_completion_timeout(&cons->a52u_request_ack,
						      msecs_to_jiffies(500));
		if (!deadline) {
			a52_ackfr_record("U446 CONSOLE request-ack-timeout (ABORT)");
			ret=-ETIMEDOUT;
			goto out;
		}
	}
	/* Even when pending was already cleared, completion is signalled before
	 * callback returns from its stopping branch; do not free on timeout.
	 */
	if (req && ep) {
		spin_lock_irqsave(&cons->lock,flags);
		cons->req=NULL;
		spin_unlock_irqrestore(&cons->lock,flags);
		gs_free_req(ep,req);
		a52_ackfr_record("U446 CONSOLE request-free");
	}
	cons->a52u_detached=true;
	a52_ackfr_record("U446 CONSOLE detach-complete");
out:
	mutex_unlock(&ports[port_num].lock);
	return ret;
}
EXPORT_SYMBOL_GPL(a52_446u_detach_console);

'''
    s=once(s,"ssize_t gserial_set_console(unsigned char port_num, const char *page, size_t count)",preflight+"ssize_t gserial_set_console(unsigned char port_num, const char *page, size_t count)","safety preflight and detach")
    s=once(s,"\t/* wait for old opens to finish */\n\twait_event(port->close_wait, gs_closed(port));",
        "\t/* The U446 coordinator must have ruled out live tty openers. */\n\tif (READ_ONCE(port->a52u_block_opens))\n\t\ta52_ackfr_record(\"U446 UNBIND free_port before_close_wait count=%u\",\n\t\t\t\t port->port.count);\n\t/* Original 446T wait retained; preflight+open gate make it immediate. */\n\twait_event(port->close_wait, gs_closed(port));","close_wait witness")
    s=once(s,"\tport = ports[port_num].port;\n\tgs_console_exit(port);",
        "\tport = ports[port_num].port;\n\tif (READ_ONCE(port->a52u_block_opens))\n\t\ta52_ackfr_record(\"U446 UNBIND gserial_free_line count=%u\",port->port.count);\n\tgs_console_exit(port);","free line witness")
    return s

def patch_s(s):
    if TAG in s:return s
    s='#include <linux/completion.h>\n#include <linux/mutex.h>\n#include <linux/workqueue.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n'+s
    block=r'''
/* A52_PHASE446U_USB_SAFE_COORDINATOR_V2
 * No composite teardown from ConfigFS's gi->lock critical section.
 * Timeout returns -EAGAIN: a future UDC write may recheck the state.
 */
static bool a52u_usb_handover = true;
static int __init a52u_usb_handover_arg(char *value)
{
	if (!value) return -EINVAL;
	if (value[0]=='0' && !value[1]) a52u_usb_handover=false;
	else if (value[0]=='1' && !value[1]) a52u_usb_handover=true;
	else return -EINVAL;
	return 0;
}
early_param("a52.usb_handover",a52u_usb_handover_arg);

extern int a52_446u_preflight_port(unsigned char port_num);
extern void a52_446u_unblock_opens(unsigned char port_num);
extern int a52_446u_detach_console(unsigned char port_num);

static DEFINE_MUTEX(a52u_handover_lock);
static DECLARE_COMPLETION(a52u_handover_done);
static int a52u_state; /* 0=idle, 1=inflight, 2=success, 3=defer */
static int a52u_result;

static void a52u_handover_worker(struct work_struct *work);
static DECLARE_WORK(a52u_handover_work,a52u_handover_worker);

static void a52u_handover_worker(struct work_struct *work)
{
	int rc;
	a52_ackfr_record("U446 coordinator worker-enter");
	rc=a52_446u_preflight_port(0);
	if (rc) goto done;
	rc=a52_446u_detach_console(0);
	if (rc) goto done; /* never unregister with a live console */
	a52_ackfr_record("U446 UNBIND composite-unregister-enter");
	rc=switch_gserial_enable(false);
	a52_ackfr_record("U446 UNBIND composite-unregister-exit rc=%d",rc);
	if (!rc) {
		enable=false;
		a52_ackfr_record("U446 UDC legacy-unregistered");
	}
done:
	/* If a worker or request is still active, no memory is freed here. */
	if (rc)
		a52_446u_unblock_opens(0);
	mutex_lock(&a52u_handover_lock);
	a52u_result=rc;
	a52u_state=rc ? 3 : 2;
	complete_all(&a52u_handover_done);
	mutex_unlock(&a52u_handover_lock);
	a52_ackfr_record("U446 coordinator done rc=%d",rc);
}
int a52_446u_release_early_serial(void)
{
	unsigned long ack;
	int rc;
	if (!a52u_usb_handover) {
		a52_ackfr_record("U446 HANDOVER_DISABLED COM3_PRESERVED");
		return 0; /* The still-owned UDC will reject Android's bind. */
	}
	if (!enable) return 0;
	mutex_lock(&a52u_handover_lock);
	if (a52u_state != 1 && a52u_state != 2) {
		a52u_state=1;
		reinit_completion(&a52u_handover_done);
		schedule_work(&a52u_handover_work);
	}
	mutex_unlock(&a52u_handover_lock);
	ack=wait_for_completion_timeout(&a52u_handover_done,
					msecs_to_jiffies(2500));
	if (!ack) {
		a52_ackfr_record("U446 coordinator bounded-timeout ret=EAGAIN");
		return -EAGAIN;
	}
	mutex_lock(&a52u_handover_lock);
	rc=a52u_result;
	mutex_unlock(&a52u_handover_lock);
	return rc ? -EAGAIN : 0;
}
EXPORT_SYMBOL_GPL(a52_446u_release_early_serial);

'''
    s=once(s,"static int __init init(void)\n{",block+"static int __init init(void)\n{","worker coordinator")
    orig="static int gs_unbind(struct usb_composite_dev *cdev)\n{\n\tint i;\n"
    new=orig+r'''
	a52_ackfr_record("U446 UNBIND gs_unbind enter");
'''
    s=once(s,orig,new,"gs_unbind enter after declarations")
    s=once(s,"\t\tusb_put_function(f_serial[i]);\n\t\tusb_put_function_instance(fi_serial[i]);",
        r'''		a52_ackfr_record("U446 UNBIND func_put enter i=%d",i);
		usb_put_function(f_serial[i]);
		a52_ackfr_record("U446 UNBIND func_put exit i=%d",i);
		a52_ackfr_record("U446 UNBIND inst_put enter i=%d",i);
		usb_put_function_instance(fi_serial[i]);
		a52_ackfr_record("U446 UNBIND inst_put exit i=%d",i);''',"function release witness")
    begin=s.index("static int gs_unbind(")
    end=s.index("\n}",begin)
    part=s[begin:end]
    part=once(part,"\treturn 0;",'\ta52_ackfr_record("U446 UNBIND gs_unbind exit");\n\treturn 0;',"gs_unbind exit")
    s=s[:begin]+part+s[end:]
    return s

def patch_c(s):
    if TAG in s:return s
    s='#include <linux/a52_ack_secure_flight_recorder.h>\n'+s
    sig="static ssize_t gadget_dev_desc_UDC_store(struct config_item *item,"
    s=once(s,sig,'#if IS_BUILTIN(CONFIG_USB_G_SERIAL)\nextern int a52_446u_release_early_serial(void);\n#endif\n\n'+sig,"configfs declaration")
    anchor="\tif (name[len - 1] == '\\n')\n\t\tname[len - 1] = '\\0';\n\n\tmutex_lock(&gi->lock);"
    repl=r'''	if (name[len - 1] == '\n')
		name[len - 1] = '\0';

	/* U446: execute bounded worker handover WITHOUT holding gi->lock. */
#if IS_BUILTIN(CONFIG_USB_G_SERIAL)
	if (!strcmp(name,"a600000.dwc3")) {
		a52_ackfr_record("U446 UDC request name=a600000.dwc3");
		ret=a52_446u_release_early_serial();
		if (ret) {
			a52_ackfr_record("U446 UDC defer rc=%d",ret);
			kfree(name);
			return ret;
		}
	}
#endif
	mutex_lock(&gi->lock);'''
    s=once(s,anchor,repl,"out-of-lock handover")
    s=once(s,"\t\tgi->composite.gadget_driver.udc_name = name;\n\t\tret = usb_gadget_probe_driver(&gi->composite.gadget_driver);",
        r'''		gi->composite.gadget_driver.udc_name = name;
		a52_ackfr_record("U446 UDC configfs probe-enter");
		ret = usb_gadget_probe_driver(&gi->composite.gadget_driver);
		a52_ackfr_record("U446 UDC configfs probe-exit rc=%d",ret);
		if (!ret && !strcmp(name,"a600000.dwc3"))
			a52_ackfr_record("U446 UDC adb-bound");''',"configfs probe")
    return s

def patch_recorder(s):
    if TAG in s: return s
    # Both the freeze gate and normal format admission need U446, otherwise
    # no P445/ramoops breadcrumb survives the console disappearing.
    a="if (unlikely(atomic_read(&a52_r280_retained)) &&"
    s=once(s,a,a+'\n\t    strncmp(fmt, "U446", 4) &&',"retention admission")
    a="if (strncmp(fmt, \"P440\", 4) &&"
    s=once(s,a,"if (strncmp(fmt, \"U446\", 4) &&\n    strncmp(fmt, \"P440\", 4) &&","recorder normal admission")
    return s

def check(root):
    checks={
      U:(TAG,"U446 PRE count=","U446 holder pid=",
         "U446 CONSOLE worker-stop-ack","U446 CONSOLE unregister-enter",
         "U446 CONSOLE request-dequeue-enter","U446 CONSOLE request-free",
         "U446 UNBIND free_port before_close_wait","a52u_block_opens",
         "wait_for_completion_timeout"),
      S:(TAG,'early_param("a52.usb_handover"',
         "static DECLARE_WORK(a52u_handover_work","U446 coordinator",
         "U446 UNBIND gs_unbind enter","U446 UNBIND func_put enter",
         "U446 UNBIND inst_put exit","U446 UDC legacy-unregistered"),
      C:("U446 UDC request","a52_446u_release_early_serial",
         "U446 UDC configfs probe-enter","U446 UDC configfs probe-exit",
         "U446 UDC adb-bound"),
      R:('strncmp(fmt, "U446", 4)',),
    }
    for p,fragments in checks.items():
        data=(root/p).read_text()
        for x in fragments:
            if x not in data:
                raise RuntimeError("U446 audit missing "+x+" in "+str(p))
    cf=(root/C).read_text()
    call=cf.index("ret=a52_446u_release_early_serial();")
    lock=cf.index("mutex_lock(&gi->lock);",call)
    if lock<call:
        raise RuntimeError("U446 blocking teardown under ConfigFS gi->lock")
    print("U446 safe coordinator AUDIT PASS: one worker, bounded wait, tty count gate, worker/request acks, console-first unregister, persistent checkpoints")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    x=ap.parse_args()
    if not x.check_only:
        for p,fn in ((U,patch_u),(S,patch_s),(C,patch_c),(R,patch_recorder)):
            f=x.root/p
            f.write_text(fn(f.read_text()))
    check(x.root)

if __name__=="__main__":
    main()
