#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
arch_kconfig = root / "arch/Kconfig"
top_make = root / "Makefile"
make_lib = root / "scripts/Makefile.lib"
autofdo = root / "scripts/Makefile.autofdo"
defconfig = root / "arch/arm64/configs/a52xq_defconfig"

k = arch_kconfig.read_text()
block = r'''
config AUTOFDO_CLANG
	bool "Enable Clang AutoFDO build (EXPERIMENTAL)"
	depends on CC_IS_CLANG
	help
	  Enable Clang AutoFDO instrumentation and profile consumption.
	  Pass CLANG_AUTOFDO_PROFILE=<profile> at build time to optimize
	  using an AutoFDO sample profile. Without a profile, the compiler
	  emits profiling-friendly discriminators for profile collection.

	  If unsure, say N.

'''
anchor = 'config CFI\n\tbool\n'
if 'config AUTOFDO_CLANG' not in k:
    if anchor not in k:
        raise SystemExit('arch/Kconfig AutoFDO anchor missing')
    k = k.replace(anchor, block + anchor, 1)
    arch_kconfig.write_text(k)

m = top_make.read_text()
include = 'include scripts/Makefile.autofdo\n'
if include not in m:
    anchor = 'ifdef CONFIG_DEBUG_INFO\n'
    if anchor not in m:
        raise SystemExit('top Makefile AutoFDO include anchor missing')
    m = m.replace(anchor, 'ifdef CONFIG_AUTOFDO_CLANG\n' + include + 'endif\n\n' + anchor, 1)
    top_make.write_text(m)

lib = make_lib.read_text()
lib_block = r'''
#
# Enable AutoFDO build flags except files/directories explicitly disabled.
# AUTOFDO_PROFILE_obj.o=n or AUTOFDO_PROFILE=n disables the flags.
#
ifeq ($(CONFIG_AUTOFDO_CLANG),y)
_c_flags += $(if $(patsubst n%,, \
	$(AUTOFDO_PROFILE_$(basetarget).o)$(AUTOFDO_PROFILE)y), \
	$(CFLAGS_AUTOFDO_CLANG))
endif

'''
anchor = '# If building the kernel in a separate objtree expand all occurrences\n'
if 'Enable AutoFDO build flags' not in lib:
    if anchor not in lib:
        raise SystemExit('scripts/Makefile.lib AutoFDO anchor missing')
    lib = lib.replace(anchor, lib_block + anchor, 1)
    make_lib.write_text(lib)

autofdo.write_text(r'''# SPDX-License-Identifier: GPL-2.0

# Clang AutoFDO support backported for the A52 Linux 4.19.206 tree.
CFLAGS_AUTOFDO_CLANG := -fdebug-info-for-profiling
CFLAGS_AUTOFDO_CLANG += -mllvm -enable-fs-discriminator=true
CFLAGS_AUTOFDO_CLANG += -mllvm -improved-fs-discriminator=true

ifndef CONFIG_DEBUG_INFO
CFLAGS_AUTOFDO_CLANG += -gline-tables-only
endif

ifdef CLANG_AUTOFDO_PROFILE
CFLAGS_AUTOFDO_CLANG += -fprofile-sample-use=$(CLANG_AUTOFDO_PROFILE)
CFLAGS_AUTOFDO_CLANG += -ffunction-sections
CFLAGS_AUTOFDO_CLANG += -fsplit-machine-functions
endif

export CFLAGS_AUTOFDO_CLANG
''')

d = defconfig.read_text()
if 'CONFIG_AUTOFDO_CLANG=y' not in d:
    d += '\n# A52 AutoFDO backport\nCONFIG_AUTOFDO_CLANG=y\n'
    defconfig.write_text(d)

print('autofdo_a1_kbuild=applied')
