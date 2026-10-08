#!/usr/bin/env python3
"""P169: add optional AutoFDO sample-profile support to P168's Clang 22 ThinLTO kernel.

TRAINING mode: CONFIG_AUTOFDO_CLANG=y, no CLANG_AUTOFDO_PROFILE.
PROFILED mode: same tree/config + real CLANG_AUTOFDO_PROFILE exported for make.
Intentionally no -ffunction-sections or -fsplit-machine-functions: legacy 4.19
initcall/linker layout must be validated before adding code-layout transforms.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 169_apply_autofdo_clang22.py <kernel-root>")
root = Path(sys.argv[1]).resolve()

def edit(rel, old, new, label):
    path = root / rel
    source = path.read_text()
    if new in source:
        print(f"{label}=present")
        return
    if source.count(old) != 1:
        raise SystemExit(f"{label}: anchor count={source.count(old)}, expected 1")
    path.write_text(source.replace(old, new, 1))
    print(f"{label}=patched")

edit(
    "arch/Kconfig",
    "config CFI\n\tbool\n",
    'config AUTOFDO_CLANG\n'
    '\tbool "Clang AutoFDO build (experimental)"\n'
    '\tdepends on CC_IS_CLANG\n'
    '\thelp\n'
    '\t  Generate profiling-friendly locations in an unprofiled build.\n'
    '\t  Supply CLANG_AUTOFDO_PROFILE with a genuine LLVM sample profile\n'
    '\t  in subsequent builds to guide Clang and ThinLTO optimization.\n'
    '\t  Without a profile, this does not enable profile-guided optimization.\n'
    '\n'
    "config CFI\n\tbool\n",
    "P169_ARCH_KCONFIG",
)
edit(
    "Makefile",
    "ifdef CONFIG_DEBUG_INFO\n",
    "ifdef CONFIG_AUTOFDO_CLANG\ninclude scripts/Makefile.autofdo\nendif\n\n"
    "ifdef CONFIG_DEBUG_INFO\n",
    "P169_TOP_MAKEFILE",
)
edit(
    "scripts/Makefile.lib",
    "# If building the kernel in a separate objtree expand all occurrences\n",
    "# P169: apply AutoFDO flags to kernel C objects unless explicitly opted out.\n"
    "ifeq ($(CONFIG_AUTOFDO_CLANG),y)\n"
    "_c_flags += $(if $(patsubst n%,, \\\n"
    "\t$(AUTOFDO_PROFILE_$(basetarget).o)$(AUTOFDO_PROFILE)y), \\\n"
    "\t$(CFLAGS_AUTOFDO_CLANG))\n"
    "endif\n\n"
    "# If building the kernel in a separate objtree expand all occurrences\n",
    "P169_MAKEFILE_LIB",
)
afdo = root / "scripts/Makefile.autofdo"
payload = """# SPDX-License-Identifier: GPL-2.0
# P169: Clang 22 AutoFDO for A52 4.19.206, compatible with ThinLTO.
# No profile: emit sufficient DWARF/discriminators to collect samples.
# With a profile: Clang and the ThinLTO linker consume the SAME profile.
CFLAGS_AUTOFDO_CLANG := -fdebug-info-for-profiling
CFLAGS_AUTOFDO_CLANG += -mllvm -enable-fs-discriminator=true
CFLAGS_AUTOFDO_CLANG += -mllvm -improved-fs-discriminator=true

ifndef CONFIG_DEBUG_INFO
CFLAGS_AUTOFDO_CLANG += -gline-tables-only
endif

ifneq ($(strip $(CLANG_AUTOFDO_PROFILE)),)
CFLAGS_AUTOFDO_CLANG += -fprofile-sample-use=$(CLANG_AUTOFDO_PROFILE)

# Clang 22 LLD must receive the same sample input at ThinLTO backend time.
ifdef CONFIG_THINLTO
KBUILD_LDFLAGS += --lto-sample-profile=$(CLANG_AUTOFDO_PROFILE)
endif
endif

export CFLAGS_AUTOFDO_CLANG
"""
if afdo.exists():
    if afdo.read_text() != payload:
        raise SystemExit("P169_MAKEFILE_AUTOFDO: existing content differs, refusing overwrite")
    print("P169_MAKEFILE_AUTOFDO=present")
else:
    afdo.write_text(payload)
    print("P169_MAKEFILE_AUTOFDO=created")

cfg = root / "arch/arm64/configs/a52xq_defconfig"
text = cfg.read_text()
if "CONFIG_AUTOFDO_CLANG=y\n" not in text:
    if "# CONFIG_AUTOFDO_CLANG is not set\n" in text:
        text = text.replace("# CONFIG_AUTOFDO_CLANG is not set\n",
                            "CONFIG_AUTOFDO_CLANG=y\n", 1)
    else:
        text += "\n# A52 P169: AutoFDO training / optional sample-profile consumption\nCONFIG_AUTOFDO_CLANG=y\n"
    cfg.write_text(text)
print("P169_AUTOFDO_CONFIG=enabled")

# P168 builds O2 + ThinLTO25; never change those parameters here.
assert "CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE=y" in cfg.read_text()
assert "CONFIG_THINLTO=y" in cfg.read_text()
assert "CONFIG_LTO_CLANG=y" in cfg.read_text()
print("P169_BASELINE_O2_THINLTO=preserved")
