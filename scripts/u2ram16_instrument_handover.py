#!/usr/bin/env python3
"""U2RAM16: persistent, CRC-checked handover checkpoints in EXISTING P445 RAM.

Apply AFTER scripts/u2_apply_gki_usb_composite_handover.py, BEFORE make Image.
Uses the existing a52_p445_store_section() recorder at B1A62000. Each
checkpoint is one CRC32-protected P445 section containing sixteen identical
40-byte records (data redundancy). The separate P445 header carries boot_id,
so a stale record is distinguishable from the present boot. The reader searches
raw Samsung reserved RAM for FCOMP445; conventional ramoops is NOT this area.

No independent ioremap, recorder zeroing, extra RAM ownership or busy loops.
Do not instrument the low-level u_serial spinlock or print via dying console.
"""
import argparse
from pathlib import Path

MARK = "A52_U2_HANDOVER_RAM16_P445_V1"
SERIAL = Path("drivers/usb/gadget/legacy/serial.c")
CONFIGFS = Path("drivers/usb/gadget/configfs.c")
P445 = Path("drivers/a52_display/msm/a52_phase445.c")

def one(src, before, after, label):
    count = src.count(before)
    if count != 1:
        raise RuntimeError(f"U2RAM16 {label}: exact anchor count {count}, expected 1")
    return src.replace(before, after, 1)

def patch_serial(s):
    if MARK in s:
        return s
    src = """/* A52_U2_HANDOVER_RAM16_P445_V1
 * P445 reserved RAM initialized at subsys_initcall (~0.73s).
 * Each event stores sixteen redundant records in ONE P445 section;
 * the P445 section itself has a commit marker and CRC32.
 * Read-only consumers can recover the record by majority vote.
 */
#include <linux/ktime.h>
extern int a52_p445_store_section(u32 stage, u32 type, const char *name,
                                  u32 aux0, u32 aux1, const void *data, u32 len);
struct a52_u2_ram_record {
    u32 magic, step, result, inverse_step, inverse_result, tag;
    u64 time_ns, inverse_time_ns;
};
void a52_u2_ram_checkpoint(u32 step, s32 result)
{
    struct a52_u2_ram_record rec[16];
    const u64 ns = ktime_get_ns();
    int i;
    for (i = 0; i < 16; ++i) {
        rec[i].magic = 0x5532524dU; /* MR2U LE */
        rec[i].step = step;
        rec[i].result = (u32)result;
        rec[i].inverse_step = ~step;
        rec[i].inverse_result = ~(u32)result;
        rec[i].tag = 0xa52c0001U;
        rec[i].time_ns = ns;
        rec[i].inverse_time_ns = ~ns;
    }
    (void)a52_p445_store_section(0x55320000U | step, 0x5532U,
                                 "U2RAM16", step, (u32)result,
                                 rec, sizeof(rec));
}
EXPORT_SYMBOL_GPL(a52_u2_ram_checkpoint);

"""
    s=one(s, "static int switch_gserial_enable(bool do_enable);\n",
          src+"static int switch_gserial_enable(bool do_enable);\n", "insert recorder")
    s=one(s,
          '    pr_warn("A52U2 UDC handover: releasing early ttyGS g_serial\\n");\n    ret = switch_gserial_enable(false);\n',
          '    a52_u2_ram_checkpoint(11, 0); /* release entered */\n'
          '    pr_warn("A52U2 UDC handover: releasing early ttyGS g_serial\\n");\n'
          '    a52_u2_ram_checkpoint(12, 0); /* before release call */\n'
          '    ret = switch_gserial_enable(false);\n'
          '    a52_u2_ram_checkpoint(19, ret); /* switch returned */\n',
          "release checkpoints")
    s=one(s,
          '\tusb_composite_unregister(&gserial_driver);\n\treturn 0;\n',
          '\ta52_u2_ram_checkpoint(13, 0); /* entering composite unregister */\n'
          '\tusb_composite_unregister(&gserial_driver);\n'
          '\ta52_u2_ram_checkpoint(18, 0); /* composite unregister returned */\n'
          '\treturn 0;\n',
          "unregister checkpoints")
    s=one(s,
          'static int gs_unbind(struct usb_composite_dev *cdev)\n{\n\tint i;\n',
          'static int gs_unbind(struct usb_composite_dev *cdev)\n{\n\tint i;\n'
          '\ta52_u2_ram_checkpoint(15, 0); /* legacy unbind begins */\n',
          "unbind entry")
    s=one(s,
          '\tkfree(otg_desc[0]);\n\totg_desc[0] = NULL;\n\n\treturn 0;\n}\n\nstatic struct usb_composite_driver gserial_driver',
          '\tkfree(otg_desc[0]);\n\totg_desc[0] = NULL;\n'
          '\ta52_u2_ram_checkpoint(16, 0); /* legacy unbind finished */\n\n'
          '\treturn 0;\n}\n\nstatic struct usb_composite_driver gserial_driver',
          "unbind exit")
    s=one(s,
          '    enable = false;\n    pr_warn("A52U2 UDC handover: g_serial released, ConfigFS may bind\\n");',
          '    enable = false;\n'
          '    a52_u2_ram_checkpoint(20, 0); /* release completed */\n'
          '    pr_warn("A52U2 UDC handover: g_serial released, ConfigFS may bind\\n");',
          "release success")
    return s

def patch_configfs(s):
    if MARK in s:
        return s
    s=one(s,
          'extern int a52_u2_release_early_serial(void);\n',
          'extern int a52_u2_release_early_serial(void);\n'
          'extern void a52_u2_ram_checkpoint(u32 step, s32 result); /* '+MARK+' */\n',
          "checkpoint declaration")
    s=one(s,
          'pr_warn("A52U2 ConfigFS requests A52 UDC: begin handover\\n");\n'
          '\t\t\tret = a52_u2_release_early_serial();',
          'a52_u2_ram_checkpoint(10, 0); /* Android requested UDC */\n'
          '\t\t\tpr_warn("A52U2 ConfigFS requests A52 UDC: begin handover\\n");\n'
          '\t\t\tret = a52_u2_release_early_serial();\n'
          '\t\t\ta52_u2_ram_checkpoint(21, ret); /* release returned */',
          "ConfigFS release")
    s=one(s,
          '\t\tgi->composite.gadget_driver.udc_name = name;\n'
          '\t\tret = usb_gadget_probe_driver(&gi->composite.gadget_driver);\n'
          '\t\tpr_warn("A52U2 ConfigFS UDC bind result=%d\\n", ret);',
          '\t\ta52_u2_ram_checkpoint(22, 0); /* before Android gadget bind */\n'
          '\t\tgi->composite.gadget_driver.udc_name = name;\n'
          '\t\tret = usb_gadget_probe_driver(&gi->composite.gadget_driver);\n'
          '\t\ta52_u2_ram_checkpoint(23, ret); /* Android gadget bind result */\n'
          '\t\tpr_warn("A52U2 ConfigFS UDC bind result=%d\\n", ret);',
          "Android bind")
    return s

def validate(root):
    s=(root/SERIAL).read_text()
    c=(root/CONFIGFS).read_text()
    p=(root/P445).read_text()
    for needle in [MARK,"void a52_u2_ram_checkpoint(u32 step, s32 result)",
                   "a52_u2_ram_checkpoint(11, 0)","a52_u2_ram_checkpoint(12, 0)",
                   "a52_u2_ram_checkpoint(13, 0)","a52_u2_ram_checkpoint(15, 0)",
                   "a52_u2_ram_checkpoint(16, 0)","a52_u2_ram_checkpoint(18, 0)",
                   "a52_u2_ram_checkpoint(19, ret)","a52_u2_ram_checkpoint(20, 0)",
                   '"U2RAM16"', "rec[16]", "EXPORT_SYMBOL_GPL(a52_u2_ram_checkpoint)"]:
        if needle not in s: raise RuntimeError("U2RAM16 serial missing "+needle)
    for needle in [MARK,"a52_u2_ram_checkpoint(10, 0)","a52_u2_ram_checkpoint(21, ret)",
                   "a52_u2_ram_checkpoint(22, 0)","a52_u2_ram_checkpoint(23, ret)"]:
        if needle not in c: raise RuntimeError("U2RAM16 ConfigFS missing "+needle)
    for needle in ['#define P445_RAM_PHYS 0xB1A62000ULL', 'EXPORT_SYMBOL_GPL(a52_p445_store_section)',
                   'P445_SEC_COMMIT', 'p445_flush_ram(p445_ram, P445_HEADER_BYTES)']:
        if needle not in p: raise RuntimeError("U2RAM16 backing recorder missing "+needle)
    print("U2RAM16 source contract PASS; uses existing Phase445 reserved RAM, 16 copies per event")
    print("U2RAM16 WARNING: P445 buffer could overflow or be corrupted; decode CRC before trusting.")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--check-only",action="store_true")
    a=parser.parse_args()
    root=a.root
    if not a.check_only:
        for rel,patch in [(SERIAL,patch_serial),(CONFIGFS,patch_configfs)]:
            p=root/rel
            s=p.read_text()
            if MARK in s:
                print("U2RAM16 already applied:",rel)
            else:
                p.write_text(patch(s))
    validate(root)

if __name__=="__main__":
    main()
