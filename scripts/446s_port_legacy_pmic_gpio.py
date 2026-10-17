#!/usr/bin/env python3
"""446S: install pinned TouchGrass legacy SPMI GPIO driver on GKI 5.10.

Driver is deliberately separate from native ACK pinctrl-spmi-gpio.c.
It only binds qcom,spmi-gpio nodes with <base size> reg and per-pin IRQs.
"""
from pathlib import Path
import argparse, shutil
TAG="A52_PHASE446S_LEGACY_PMIC_GPIO_V1"
SRC=Path("vendor/phase446s/a52-pinctrl-spmi-gpio.c")
DST=Path("drivers/pinctrl/qcom/a52-pinctrl-spmi-gpio.c")
MAKE=Path("drivers/pinctrl/qcom/Makefile")
KBUILD="obj-y += a52-pinctrl-spmi-gpio.o"
def audit(root):
    c=(root/DST).read_text()
    m=(root/MAKE).read_text()
    for t in (TAG,"P446T PMIC_GPIO probe",
              '.compatible = "qcom,spmi-gpio"',
              '.name = "a52-qcom-spmi-gpio"',
              'platform_get_irq(pdev, pin_idx)',
              "qcom,gpios-disallowed","gpiochip_add_data",
              "gpiochip_add_pin_range"):
        if t not in c:raise RuntimeError("446S PMIC GPIO missing "+t)
    if "CONFIG_PINCTRL_SPMI_GPIO" in KBUILD:
        raise RuntimeError("446S vendor GPIO must be built regardless of native CONFIG")
    if KBUILD not in m:raise RuntimeError("446S PMIC GPIO Kbuild missing")
    if ".compatible = \"qcom,pm8916-gpio\"" in c:
        raise RuntimeError("446S vendor must not steal native-compatible nodes")
    print("P446S PMIC GPIO source PASS: TG legacy only, per-pin IRQ, reg-size geometry")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    opt=p.parse_args()
    root=opt.root
    if not opt.check_only:
        src=Path(__file__).resolve().parent.parent/SRC
        if not src.is_file():raise RuntimeError("missing pinned vendor source "+str(src))
        dst=root/DST
        if dst.exists() and TAG not in dst.read_text():
            raise RuntimeError("refuse to overwrite unrecognized PMIC GPIO driver")
        shutil.copyfile(src,dst)
        make=root/MAKE
        text=make.read_text()
        if KBUILD not in text:
            make.write_text(text+"\n# "+TAG+"\n"+KBUILD+"\n")
    audit(root)
if __name__=="__main__":
    main()
