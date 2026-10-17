#!/usr/bin/env python3
"""446W: early g_serial console on ttyGS3, preserve ttyGS0 for Android.

Selectively allocate the FIRST legacy g_serial ACM/gser function on ttyGS3,
without allocating 0/1/2. Subsequent configfs allocations start at ttyGS0.
Guard U446 preflight/owner-count/open-gate/detach against the selected line.
Print U446 defer decisions on printk with rate limiting (in addition to P445).
Default a52.446w_tty=3. Fallback a52.446w_tty=0 requires console=ttyGS0.
"""
from pathlib import Path
import argparse
U=Path("drivers/usb/gadget/function/u_serial.c")
S=Path("drivers/usb/gadget/legacy/serial.c")
C=Path("drivers/usb/gadget/configfs.c")
TAG="A52_PHASE446W_TTYGS3_EARLY_PORT_V1"
def one(s,a,b,label):
    if s.count(a)!=1:raise RuntimeError("446W tty "+label+" matched "+str(s.count(a)))
    return s.replace(a,b,1)

def patch_u(s):
    if TAG in s:return s
    if "A52_PHASE446U_USB_SAFE_COORDINATOR_V2" not in s:
        raise RuntimeError("U446 v2 must precede W")
    s='#include <linux/ratelimit.h>\n#include <linux/atomic.h>\nunsigned char a52w_early_console_line(void);\n'+s
    anchor="int gserial_alloc_line_no_console(unsigned char *line_num)"
    block=r'''/* A52_PHASE446W_TTYGS3_EARLY_PORT_V1 */
static unsigned char a52w_console_port=3;
static atomic_t a52w_first_alloc=ATOMIC_INIT(0);
static int __init a52w_console_arg(char *s)
{
	if (!s)return -EINVAL;
	if (!strcmp(s,"0")) a52w_console_port=0;
	else if (!strcmp(s,"3")) a52w_console_port=3;
	else return -EINVAL;
	return 0;
}
early_param("a52.446w_tty",a52w_console_arg);
unsigned char a52w_early_console_line(void) { return a52w_console_port; }
EXPORT_SYMBOL_GPL(a52w_early_console_line);

'''
    s=one(s,anchor,block+anchor,"early port selection")
    old='''	for (port_num = 0; port_num < MAX_U_SERIAL_PORTS; port_num++) {
		ret = gs_port_alloc(port_num, &coding);
		if (ret == -EBUSY)
			continue;
		if (ret)
			return ret;
		break;
	}
	if (ret)
		return ret;'''
    new=r'''	/*
	 * g_serial is the first USB serial function on A52 boot.
	 * Allocate ONLY line3 for it; don't occupy lines0-2. Once early
	 * g_serial is removed, Samsung configfs ACM/DUN uses line0.
	 */
	if (a52w_console_port==3 &&
	    atomic_cmpxchg(&a52w_first_alloc,0,1)==0) {
		port_num=3;
		ret=gs_port_alloc(port_num,&coding);
		if (ret) {
			pr_err("P446W USB early ttyGS3 allocation FAILED rc=%d\n",ret);
			return ret; /* fail closed, never hijack ttyGS0 */
		}
		pr_info("P446W USB early legacy g_serial allocated ttyGS3; ttyGS0 reserved for future Android configfs\n");
	} else {
		for (port_num=0;port_num<MAX_U_SERIAL_PORTS;port_num++) {
			ret=gs_port_alloc(port_num,&coding);
			if (ret==-EBUSY)continue;
			if (ret)return ret;
			break;
		}
		if (ret)return ret;
	}'''
    s=one(s,old,new,"first allocation only line3")
    s=one(s,'if (!ret && !*line_num)','if (!ret && *line_num == a52w_early_console_line())',
          "console-init on requested port")
    s=one(s,'if (port_num == 0 && unlikely(port->a52u_block_opens))',
          'if (port_num == a52w_early_console_line() && unlikely(port->a52u_block_opens))',
          "U446 open count gate for console line only")
    s=one(s,'dev != MKDEV(gs_tty_driver->major, 0)',
          'dev != MKDEV(gs_tty_driver->major, a52w_early_console_line())',
          "U446 owner scan for console tty")
    anchor='''	a52_ackfr_record("U446 PRE count=%u port_usb=%u console=%u req=%u req_len=%u fifo=%u udc=a600000.dwc3",'''
    s=one(s,anchor,r'''	pr_info_ratelimited("P446W U446 PRE port=%u tty_count=%u console=%d usb_connected=%d\n",
		port_num,count,!!port->console,!!port->port_usb);
'''+anchor,"mirror preflight to printk")
    anchor='''		a52_ackfr_record("U446 DEFER tty_open_count=%u",count);'''
    s=one(s,anchor,anchor+r'''
		pr_info_ratelimited("P446W U446 DEFER port=%u reason=tty_open_count count=%u (no unbind)\n",port_num,count);''',"mirror count defer")
    anchor='''	a52_ackfr_record("U446 CONSOLE detach-complete");'''
    s=one(s,anchor,anchor+r'''
	pr_info_ratelimited("P446W U446 DETACH COMPLETE port=%u\n",port_num);''',"mirror detachment")
    return s

def patch_serial(s):
    if TAG in s:return s
    anchor='''extern int a52_446u_preflight_port(unsigned char port_num);'''
    s=one(s,anchor,'extern unsigned char a52w_early_console_line(void);\n'+anchor,
          "port query for early coordinator")
    for fn in ("a52_446u_preflight_port","a52_446u_detach_console",
               "a52_446u_unblock_opens"):
        s=one(s,fn+"(0);",fn+"(a52w_early_console_line());",
              fn+" worker route")
    anchor='''	a52_ackfr_record("U446 coordinator worker-enter");'''
    s=one(s,anchor,anchor+r'''
	pr_info_ratelimited("P446W U446 COORDINATOR START console=ttyGS%u\n",a52w_early_console_line());''',"coordinator start printk")
    anchor='''	a52_ackfr_record("U446 coordinator done rc=%d",rc);'''
    s=one(s,anchor,anchor+r'''
	pr_info_ratelimited("P446W U446 COORDINATOR DONE console=ttyGS%u rc=%d state=%d\n",a52w_early_console_line(),rc,a52u_state);''',"coordinator result printk")
    return s

def patch_configfs(s):
    if TAG in s:return s
    anchor='''			a52_ackfr_record("U446 UDC defer rc=%d",ret);'''
    s=one(s,anchor,anchor+r'''
			pr_info_ratelimited("P446W U446 CONFIGFS defer rc=%d (legacy console still owns UDC)\n",ret);''',
          "mirror configfs defer")
    anchor='''		a52_ackfr_record("U446 UDC configfs probe-exit rc=%d",ret);'''
    s=one(s,anchor,anchor+r'''
		pr_info_ratelimited("P446W U446 CONFIGFS probe rc=%d\n",ret);''',"mirror adb bind result")
    return s

def check(root):
    want={U:(TAG,'unsigned char a52w_early_console_line(void);','early_param("a52.446w_tty"',
                  'a52w_first_alloc','port_num=3','P446W USB early legacy',
                  'port_num == a52w_early_console_line()',
                  'MKDEV(gs_tty_driver->major, a52w_early_console_line())',
                  'P446W U446 DEFER'),
          S:("a52w_early_console_line()","P446W U446 COORDINATOR",
             "a52_446u_preflight_port(a52w_early_console_line())"),
          C:("P446W U446 CONFIGFS defer","P446W U446 CONFIGFS probe")}
    for p,ts in want.items():
        s=(root/p).read_text()
        for t in ts:
            if t not in s:raise RuntimeError("446W USB missing "+t+" in "+str(p))
    u=(root/U).read_text()
    if 'if (port_num == 0 && unlikely(port->a52u_block_opens))' in u:
        raise RuntimeError("U446 still blocks ttyGS0")
    print("P446W USB CHECK PASS: legacy console 3, generic ttyGS0 free, U446 port-scoped count+holder+detach, rate-limited printk")
def main():
    a=argparse.ArgumentParser();a.add_argument("--root",required=True,type=Path)
    a.add_argument("--check-only",action="store_true");x=a.parse_args()
    if not x.check_only:
        for p,fn in ((U,patch_u),(S,patch_serial),(C,patch_configfs)):
            f=x.root/p;f.write_text(fn(f.read_text()))
    check(x.root)
if __name__=="__main__":main()
