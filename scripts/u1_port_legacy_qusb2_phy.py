#!/usr/bin/env python3
"""A52 Phase U1: port TouchGrass's lagoon legacy QUSB2 PHY to GKI 5.10.

USB2 high-speed gadget first: do not compile USB3 PHY, change security
properties, force regulators, or touch the display. Uses TG pinned USB2 PHY
source at build time. Compile-time assertions ensure the port really lands.
"""
import argparse
from pathlib import Path

MARK = "A52_PHASE_U1_LEGACY_QUSB2_V2_PORT_V1"
KERNEL_PATH = Path("drivers/usb/phy/phy-a52-qusb2-v2.c")

def once(src: str, old: str, new: str, desc: str) -> str:
    n = src.count(old)
    if n != 1:
        raise RuntimeError(f"{desc}: expected 1 anchor, found {n}")
    return src.replace(old, new, 1)

def cut_function(src: str, signature: str) -> str:
    first = src.find(signature)
    if first < 0:
        raise RuntimeError(f"function missing {signature}")
    op = src.find("{", first)
    if op < 0:
        raise RuntimeError("opening brace missing")
    depth = 0
    for idx in range(op, len(src)):
        if src[idx] == "{":
            depth += 1
        elif src[idx] == "}":
            depth -= 1
            if depth == 0:
                return src[:first] + src[idx + 1:]
    raise RuntimeError("unbalanced function")

def port_phy(source: str) -> str:
    if 'qcom,qusb2phy-v2' not in source or 'usb_add_phy_dev(&qphy->phy)' not in source:
        raise RuntimeError("unexpected TouchGrass USB2 PHY")
    # The Linux 5.10 upstream USB PHY structure no longer has this downstream
    # charger-diagnostic-only callback. It is not used for gadget enumeration.
    source = once(source, '\tqphy->phy.drive_dp_pulse\t= msm_qusb_phy_drive_dp_pulse;\n',
                  '', "remove downstream-only usb_phy callback")
    source = cut_function(source, 'static int msm_qusb_phy_drive_dp_pulse(')
    # Linux 5.10 has void debugfs_create_x8(); the TG 4.19 helper
    # incorrectly tests the return value as struct dentry *.
    # Keep debugfs optional and don't let a debugfs failure abort PHY probe.
    source = cut_function(source, 'static int qusb_phy_create_debugfs(')
    debugfs_510 = r'''static int qusb_phy_create_debugfs(struct qusb_phy *qphy)
{
    int i;
    char name[6];

    qphy->root = debugfs_create_dir(dev_name(qphy->phy.dev), NULL);
    if (IS_ERR_OR_NULL(qphy->root))
        return 0;

    for (i = 0; i < 5; ++i) {
        snprintf(name, sizeof(name), "tune%d", i + 1);
        debugfs_create_x8(name, 0644, qphy->root, &qphy->tune[i]);
    }
    debugfs_create_x8("bias_ctrl2", 0644, qphy->root,
                      &qphy->bias_ctrl2);
    return 0;
}

'''
    source = once(source, 'static int qusb2_get_regulators(struct qusb_phy *qphy)\n',
                  debugfs_510 + 'static int qusb2_get_regulators(struct qusb_phy *qphy)\n',
                  'Linux 5.10 void debugfs API compatibility')
    source = source.replace('devm_ioremap_nocache(', 'devm_ioremap(')
    # GKI still has usb_phy.flags but does not define Samsung extensions.
    # Keep those code paths dormant; USB2 gadget path never sets these bits.
    compat = '''/* ''' + MARK + ''' 
 * Upstream usb_phy retains .flags but lacks downstream private flag defines.
 * These are reserved local bits; the upstream gadget stack never sets them.
 */
#ifndef PHY_HOST_MODE
#define PHY_HOST_MODE BIT(16)
#endif
#ifndef PHY_HSFS_MODE
#define PHY_HSFS_MODE BIT(17)
#endif
#ifndef PHY_LS_MODE
#define PHY_LS_MODE BIT(18)
#endif
#ifndef PHY_SUS_OVERRIDE
#define PHY_SUS_OVERRIDE BIT(19)
#endif
#ifndef EUD_SPOOF_DISCONNECT
#define EUD_SPOOF_DISCONNECT BIT(20)
#endif

'''
    source = once(source, '#undef dev_dbg\n#define dev_dbg dev_err\n',
                  compat, "TG debug flood override")
    return source

def patch_dt(dts: str) -> str:
    if "A52_PHASE_U1_GADGET_HS_ONLY" in dts:
        return dts
    if 'usb0: ssusb@a600000 {' not in dts or 'qcom,qusb2phy-v2' not in dts:
        raise RuntimeError("wrong lagoon DT for A52 USB")
    # Removing the absent index 1 makes the DWC3 core see -ENODEV, which is
    # supported and leaves USB3 PHY NULL without indefinite -EPROBE_DEFER.
    dts = once(dts, 'usb-phy = <&qusb_phy0>, <&usb_qmp_dp_phy>;',
               'usb-phy = <&qusb_phy0>; /* A52_PHASE_U1_GADGET_HS_ONLY */',
               "remove absent USB3 PHY phandle")
    dts = once(dts, 'maximum-speed = "super-speed";',
               'maximum-speed = "high-speed";', "USB2-only speed cap")
    dts = once(dts, 'dr_mode = "drd";', 'dr_mode = "peripheral";',
               "gadget-only mode")
    dts = once(dts, 'usb0: ssusb@a600000 {\n',
               'usb0: ssusb@a600000 {\n'
               '\t\tqcom,select-utmi-as-pipe-clk;\n',
               "select UTMI as pipe clock")
    return dts

def patch(root: Path, tg: Path):
    source = tg / "drivers/usb/phy/phy-msm-qusb-v2.c"
    dst = root / KERNEL_PATH
    mk = root / "drivers/usb/phy/Makefile"
    dt = root / "arch/arm64/boot/dts/vendor/qcom/lagoon-usb.dtsi"
    if not source.is_file():
        raise RuntimeError("pinned TouchGrass phy-msm-qusb-v2.c missing")
    if not dst.parent.is_dir():
        raise RuntimeError("GKI USB PHY directory missing")
    # GKI source need not contain the downstream lagoon DTS.
    # The boot image carries the real lagoon DTB, patched by
    # u1_patch_boot_dtb.py during repacking.
    if not dst.is_file():
        dst.write_text(port_phy(source.read_text()))
    text = mk.read_text()
    if "phy-a52-qusb2-v2.o" not in text:
        text += "\n# " + MARK + ": built-in legacy USB2 gadget PHY\n"
        text += "obj-$(CONFIG_USB_PHY) += phy-a52-qusb2-v2.o\n"
        mk.write_text(text)
    if dt.is_file():
        dt.write_text(patch_dt(dt.read_text()))
    patch_active_dwc3(root)

def patch_active_dwc3(root: Path):
    """Boot header v2 repacker preserves original DTB; force HS in the live code."""
    core = root / "drivers/usb/dwc3/core.c"
    glue = root / "drivers/usb/dwc3/dwc3-qcom.c"
    if not core.is_file() or not glue.is_file():
        raise RuntimeError("active GKI DWC3 source missing")
    c = core.read_text()
    helper = r'''/* A52_PHASE_U1_RUNTIME_HS_ONLY
 * Boot repacker preserves the stock DTB: compiled C quirk is essential.
 * Apply only to the downstream Qualcomm USB3 glue parent of this phone.
 */
static bool a52_u1_usb2_only(struct device *dev)
{
    struct device_node *parent = dev && dev->parent ?
                                 dev->parent->of_node : NULL;
    return parent && of_device_is_compatible(parent, "qcom,dwc-usb3-msm") &&
           of_node_name_eq(parent, "ssusb");
}

'''
    if "A52_PHASE_U1_RUNTIME_HS_ONLY" not in c:
        c = once(c, "static int dwc3_core_get_phy(struct dwc3 *dwc)\n",
                 helper + "static int dwc3_core_get_phy(struct dwc3 *dwc)\n",
                 "legacy PHY get helper")
        c = once(c,
                 '\t\tdwc->usb3_phy = devm_usb_get_phy_by_phandle(dev, "usb-phy", 1);\n',
                 '\t\tdwc->usb3_phy = devm_usb_get_phy_by_phandle(dev, "usb-phy", 1);\n'
                 '\t\tif (a52_u1_usb2_only(dev) && IS_ERR(dwc->usb3_phy) &&\n'
                 '\t\t    PTR_ERR(dwc->usb3_phy) == -EPROBE_DEFER) {\n'
                 '\t\t\tdev_warn(dev, "U1: optional USB3 PHY absent, use high-speed gadget\\n");\n'
                 '\t\t\tdwc->usb3_phy = NULL;\n'
                 '\t\t}\n',
                 "make only legacy USB3 PHY optional")
        c = once(c, "\tdwc3_get_properties(dwc);\n",
                 "\tdwc3_get_properties(dwc);\n"
                 "\tif (a52_u1_usb2_only(dev)) {\n"
                 "\t\tdwc->maximum_speed = USB_SPEED_HIGH;\n"
                 "\t\tdwc->dr_mode = USB_DR_MODE_PERIPHERAL;\n"
                 '\t\tdev_warn(dev, "U1: forcing authenticated high-speed USB gadget\\n");\n'
                 "\t}\n",
                 "force high-speed gadget regardless of preserved DTB")
        core.write_text(c)

    g = glue.read_text()
    if "A52_PHASE_U1_GKI_UTMI_PIPE" not in g:
        g = once(g,
                 'ignore_pipe_clk = device_property_read_bool(dev,\n'
                 '\t\t\t\t"qcom,select-utmi-as-pipe-clk");\n',
                 'ignore_pipe_clk = device_property_read_bool(dev,\n'
                 '\t\t\t\t"qcom,select-utmi-as-pipe-clk");\n'
                 '\t/* A52_PHASE_U1_GKI_UTMI_PIPE: HS-only while USB3 PHY absent. */\n'
                 '\tif (np && res && res->start == 0x0a600000ULL &&\n'
                 '\t    of_device_is_compatible(np, "qcom,dwc-usb3-msm")) {\n'
                 '\t\tignore_pipe_clk = true;\n'
                 '\t\tdev_warn(dev, "U1: selecting UTMI as PIPE on lagoon\\n");\n'
                 '\t}\n',
                 "select UTMI clock for lagoon core without DTB changes")
        glue.write_text(g)

def validate(root: Path):
    src = (root / KERNEL_PATH).read_text()
    mk = (root / "drivers/usb/phy/Makefile").read_text()
    dt_path = root / "arch/arm64/boot/dts/vendor/qcom/lagoon-usb.dtsi"
    dt = dt_path.read_text() if dt_path.is_file() else None
    for token in (MARK, 'qcom,qusb2phy-v2', 'usb_add_phy_dev(&qphy->phy)',
                  'static int qusb_phy_create_debugfs(',
                  'debugfs_create_x8("bias_ctrl2", 0644, qphy->root,'):
        if token not in src:
            raise RuntimeError("ported USB2 driver missing " + token)
    for token in ('phy-a52-qusb2-v2.o', 'CONFIG_USB_PHY'):
        if token not in mk:
            raise RuntimeError("Makefile missing " + token)
    if dt is not None:
        for token in ('A52_PHASE_U1_GADGET_HS_ONLY', 'maximum-speed = "high-speed";',
                      'dr_mode = "peripheral";', 'qcom,select-utmi-as-pipe-clk;'):
            if token not in dt:
                raise RuntimeError("optional source DTS missing " + token)
        if 'usb-phy = <&qusb_phy0>, <&usb_qmp_dp_phy>;' in dt:
            raise RuntimeError("USB3 PHY still requested by source DTS")
    else:
        print("U1: lagoon-usb.dtsi not in GKI tree; actual boot DTB patched at packaging")
    core = (root / "drivers/usb/dwc3/core.c").read_text()
    glue = (root / "drivers/usb/dwc3/dwc3-qcom.c").read_text()
    for text, marker in ((core, "A52_PHASE_U1_RUNTIME_HS_ONLY"),
                         (core, "optional USB3 PHY absent"),
                         (core, "dwc->maximum_speed = USB_SPEED_HIGH;"),
                         (glue, "A52_PHASE_U1_GKI_UTMI_PIPE")):
        if marker not in text:
            raise RuntimeError("active USB quirk missing " + marker)
    print("Phase U1 PASS: QUSB2 legacy PHY, runtime optional USB3, HS gadget, UTMI PIPE")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--touchgrass", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    if not a.check_only:
        patch(a.root, a.touchgrass)
    validate(a.root)

if __name__ == "__main__":
    main()
