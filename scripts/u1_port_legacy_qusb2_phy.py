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
    if not dt.is_file():
        raise RuntimeError("GKI lagoon-usb.dtsi missing")
    if not dst.is_file():
        dst.write_text(port_phy(source.read_text()))
    text = mk.read_text()
    if "phy-a52-qusb2-v2.o" not in text:
        text += "\n# " + MARK + ": built-in legacy USB2 gadget PHY\n"
        text += "obj-$(CONFIG_USB_PHY) += phy-a52-qusb2-v2.o\n"
        mk.write_text(text)
    dt.write_text(patch_dt(dt.read_text()))

def validate(root: Path):
    src = (root / KERNEL_PATH).read_text()
    mk = (root / "drivers/usb/phy/Makefile").read_text()
    dt = (root / "arch/arm64/boot/dts/vendor/qcom/lagoon-usb.dtsi").read_text()
    for token in (MARK, 'qcom,qusb2phy-v2', 'usb_add_phy_dev(&qphy->phy)'):
        if token not in src:
            raise RuntimeError("ported USB2 driver missing " + token)
    for token in ('phy-a52-qusb2-v2.o', 'CONFIG_USB_PHY'):
        if token not in mk:
            raise RuntimeError("Makefile missing " + token)
    for token in ('A52_PHASE_U1_GADGET_HS_ONLY', 'maximum-speed = "high-speed";',
                  'dr_mode = "peripheral";', 'qcom,select-utmi-as-pipe-clk;'):
        if token not in dt:
            raise RuntimeError("DT missing " + token)
    if 'usb-phy = <&qusb_phy0>, <&usb_qmp_dp_phy>;' in dt:
        raise RuntimeError("USB3 PHY still requested by DWC3")
    print("Phase U1 PASS: legacy QUSB2 driver compiled in, USB3 absent, USB2 peripheral")

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
