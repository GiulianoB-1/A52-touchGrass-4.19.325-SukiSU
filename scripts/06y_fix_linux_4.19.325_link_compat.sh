#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/link-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before link repair"

python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
repairs = []


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1:
        path.write_text(text.replace(old, new, 1))
        repairs.append(label)
    elif old_count == 0 and new_count == 1:
        return
    else:
        raise SystemExit(
            f"{label}: anchor mismatch old={old_count}, new={new_count}"
        )


# The merge kept lib/Makefile's unconditional chacha20.o entry and the random
# driver's calls, but treated the source file as a vendor deletion. Restore the
# exact Linux 4.19.325 implementation.
chacha = root / "lib/chacha20.c"
chacha_text = r'''/*
 * ChaCha20 256-bit cipher algorithm, RFC7539
 *
 * Copyright (C) 2015 Martin Willi
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */

#include <linux/kernel.h>
#include <linux/export.h>
#include <linux/bitops.h>
#include <linux/cryptohash.h>
#include <asm/unaligned.h>
#include <crypto/chacha20.h>

void chacha20_block(u32 *state, u8 *stream)
{
	u32 x[16];
	int i;

	for (i = 0; i < ARRAY_SIZE(x); i++)
		x[i] = state[i];

	for (i = 0; i < 20; i += 2) {
		x[0]  += x[4];    x[12] = rol32(x[12] ^ x[0],  16);
		x[1]  += x[5];    x[13] = rol32(x[13] ^ x[1],  16);
		x[2]  += x[6];    x[14] = rol32(x[14] ^ x[2],  16);
		x[3]  += x[7];    x[15] = rol32(x[15] ^ x[3],  16);

		x[8]  += x[12];   x[4]  = rol32(x[4]  ^ x[8],  12);
		x[9]  += x[13];   x[5]  = rol32(x[5]  ^ x[9],  12);
		x[10] += x[14];   x[6]  = rol32(x[6]  ^ x[10], 12);
		x[11] += x[15];   x[7]  = rol32(x[7]  ^ x[11], 12);

		x[0]  += x[4];    x[12] = rol32(x[12] ^ x[0],   8);
		x[1]  += x[5];    x[13] = rol32(x[13] ^ x[1],   8);
		x[2]  += x[6];    x[14] = rol32(x[14] ^ x[2],   8);
		x[3]  += x[7];    x[15] = rol32(x[15] ^ x[3],   8);

		x[8]  += x[12];   x[4]  = rol32(x[4]  ^ x[8],   7);
		x[9]  += x[13];   x[5]  = rol32(x[5]  ^ x[9],   7);
		x[10] += x[14];   x[6]  = rol32(x[6]  ^ x[10],  7);
		x[11] += x[15];   x[7]  = rol32(x[7]  ^ x[11],  7);

		x[0]  += x[5];    x[15] = rol32(x[15] ^ x[0],  16);
		x[1]  += x[6];    x[12] = rol32(x[12] ^ x[1],  16);
		x[2]  += x[7];    x[13] = rol32(x[13] ^ x[2],  16);
		x[3]  += x[4];    x[14] = rol32(x[14] ^ x[3],  16);

		x[10] += x[15];   x[5]  = rol32(x[5]  ^ x[10], 12);
		x[11] += x[12];   x[6]  = rol32(x[6]  ^ x[11], 12);
		x[8]  += x[13];   x[7]  = rol32(x[7]  ^ x[8],  12);
		x[9]  += x[14];   x[4]  = rol32(x[4]  ^ x[9],  12);

		x[0]  += x[5];    x[15] = rol32(x[15] ^ x[0],   8);
		x[1]  += x[6];    x[12] = rol32(x[12] ^ x[1],   8);
		x[2]  += x[7];    x[13] = rol32(x[13] ^ x[2],   8);
		x[3]  += x[4];    x[14] = rol32(x[14] ^ x[3],   8);

		x[10] += x[15];   x[5]  = rol32(x[5]  ^ x[10],  7);
		x[11] += x[12];   x[6]  = rol32(x[6]  ^ x[11],  7);
		x[8]  += x[13];   x[7]  = rol32(x[7]  ^ x[8],   7);
		x[9]  += x[14];   x[4]  = rol32(x[4]  ^ x[9],   7);
	}

	for (i = 0; i < ARRAY_SIZE(x); i++)
		put_unaligned_le32(x[i] + state[i], &stream[i * sizeof(u32)]);

	state[12]++;
}
EXPORT_SYMBOL(chacha20_block);
'''
if not chacha.exists():
    chacha.write_text(chacha_text)
    repairs.append("lib/chacha20.c=restored-linux-stable-source")
elif "void chacha20_block(u32 *state, u8 *stream)" not in chacha.read_text():
    raise SystemExit("existing lib/chacha20.c lacks chacha20_block")
if "sha1.o chacha20.o irq_regs.o" not in (root / "lib/Makefile").read_text():
    raise SystemExit("lib/Makefile does not build chacha20.o")


# Linux stable renamed the implementation to timer_delete_sync() while retaining
# del_timer_sync() as an inline compatibility wrapper.
timer_h = root / "include/linux/timer.h"
old_timer = (
    "#ifdef CONFIG_SMP\n"
    "  extern int del_timer_sync(struct timer_list *timer);\n"
    "#else\n"
    "# define del_timer_sync(t)\t\tdel_timer(t)\n"
    "#endif\n"
)
new_timer = (
    "static inline int del_timer_sync(struct timer_list *timer)\n"
    "{\n"
    "\treturn timer_delete_sync(timer);\n"
    "}\n"
)
replace_once(timer_h, old_timer, new_timer,
             "include/linux/timer.h=restored-del-timer-sync-wrapper")


# The random driver now exposes random_init(command_line). Convert any retained
# Android-era call to rand_initialize().
main = root / "init/main.c"
main_text = main.read_text()
if "rand_initialize();" in main_text:
    if main_text.count("rand_initialize();") != 1:
        raise SystemExit("unexpected rand_initialize call count")
    main.write_text(main_text.replace("rand_initialize();",
                                      "random_init(command_line);", 1))
    repairs.append("init/main.c=updated-random-initializer-call")
elif main_text.count("random_init(command_line);") != 1:
    raise SystemExit("kernel random initialization call is missing")


# Restore the stable initrd helper only when a caller survived without its
# definition.
initramfs = root / "init/initramfs.c"
text = initramfs.read_text()
initrd_sig = "static void __init populate_initrd_image(char *err)\n"
if "populate_initrd_image(err);" in text and initrd_sig not in text:
    anchor = "static int __init populate_rootfs(void)\n"
    block = '''#ifdef CONFIG_BLK_DEV_RAM
static void __init populate_initrd_image(char *err)
{
\tssize_t written;
\tstruct file *file;
\tloff_t pos = 0;

\tunpack_to_rootfs(__initramfs_start, __initramfs_size);

\tprintk(KERN_INFO "rootfs image is not initramfs (%s); looks like an initrd\\n",
\t\t\terr);
\tfile = filp_open("/initrd.image", O_WRONLY|O_CREAT|O_LARGEFILE, 0700);
\tif (IS_ERR(file))
\t\treturn;

\twritten = xwrite(file, (char *)initrd_start, initrd_end - initrd_start,
\t\t\t&pos);
\tif (written != initrd_end - initrd_start)
\t\tpr_err("/initrd.image: incomplete write (%zd != %ld)\\n",
\t\t       written, initrd_end - initrd_start);
\tfput(file);
}
#endif /* CONFIG_BLK_DEV_RAM */

'''
    if text.count(anchor) != 1:
        raise SystemExit("populate_rootfs insertion anchor mismatch")
    initramfs.write_text(text.replace(anchor, block + anchor, 1))
    repairs.append("init/initramfs.c=restored-populate-initrd-image")


# Restore small static helpers when a call survived a conflict but its local
# definition did not.
sched = root / "kernel/sched/cpufreq_schedutil.c"
text = sched.read_text()
if "sugov_clear_global_tunables();" in text and \
        "static void sugov_clear_global_tunables(void)\n" not in text:
    anchor = "static int sugov_init(struct cpufreq_policy *policy)\n"
    block = '''static void sugov_clear_global_tunables(void)
{
\tif (!have_governor_per_policy())
\t\tglobal_tunables = NULL;
}

'''
    if text.count(anchor) != 1:
        raise SystemExit("schedutil helper insertion anchor mismatch")
    sched.write_text(text.replace(anchor, block + anchor, 1))
    repairs.append("schedutil=restored-global-tunables-clear-helper")

proc_internal = root / "fs/proc/internal.h"
text = proc_internal.read_text()
if "pde_force_lookup(" in (root / "fs/proc/generic.c").read_text() and \
        "static inline void pde_force_lookup(" not in text:
    anchor = "extern const struct dentry_operations proc_net_dentry_ops;\n"
    block = '''static inline void pde_force_lookup(struct proc_dir_entry *pde)
{
\t/* /proc/net/ entries can be changed under us by setns(CLONE_NEWNET) */
\tpde->proc_dops = &proc_net_dentry_ops;
}
'''
    if text.count(anchor) != 1:
        raise SystemExit("proc force-lookup insertion anchor mismatch")
    proc_internal.write_text(text.replace(anchor, anchor + block, 1))
    repairs.append("fs/proc/internal.h=restored-pde-force-lookup")

iommu = root / "drivers/iommu/io-pgtable-arm.c"
text = iommu.read_text()
if "paddr_to_iopte(" in text and \
        "static arm_lpae_iopte paddr_to_iopte(" not in text:
    anchor = "typedef u64 arm_lpae_iopte;\n"
    block = '''
static arm_lpae_iopte paddr_to_iopte(phys_addr_t paddr,
\t\t\t\t     struct arm_lpae_io_pgtable *data)
{
\tarm_lpae_iopte pte = paddr;

\t/* Of the bits which overlap, either 51:48 or 15:12 are always RES0 */
\treturn (pte | (pte >> (48 - 12))) & ARM_LPAE_PTE_ADDR_MASK;
}
'''
    if text.count(anchor) != 1:
        raise SystemExit("IOMMU paddr helper insertion anchor mismatch")
    iommu.write_text(text.replace(anchor, anchor + block, 1))
    repairs.append("iommu=restored-paddr-to-iopte")

mmc_host = root / "drivers/mmc/core/host.c"
text = mmc_host.read_text()
if "mmc_validate_host_caps(host)" in text and \
        "static int mmc_validate_host_caps(" not in text:
    anchor = "int mmc_add_host(struct mmc_host *host)\n"
    block = '''static int mmc_validate_host_caps(struct mmc_host *host)
{
\tif (host->caps & MMC_CAP_SDIO_IRQ && !host->ops->enable_sdio_irq) {
\t\tdev_warn(host->parent, "missing ->enable_sdio_irq() ops\\n");
\t\treturn -EINVAL;
\t}

\treturn 0;
}

'''
    if text.count(anchor) != 1:
        raise SystemExit("MMC host validator insertion anchor mismatch")
    mmc_host.write_text(text.replace(anchor, block + anchor, 1))
    repairs.append("mmc-host=restored-capability-validator")

sdhci = root / "drivers/mmc/host/sdhci.c"
text = sdhci.read_text()
if "sdhci_preset_needed(host" in text and \
        "static bool sdhci_preset_needed(" not in text:
    anchor = "void sdhci_set_ios(struct mmc_host *mmc, struct mmc_ios *ios)\n"
    block = '''static bool sdhci_timing_has_preset(unsigned char timing)
{
\tswitch (timing) {
\tcase MMC_TIMING_UHS_SDR12:
\tcase MMC_TIMING_UHS_SDR25:
\tcase MMC_TIMING_UHS_SDR50:
\tcase MMC_TIMING_UHS_SDR104:
\tcase MMC_TIMING_UHS_DDR50:
\tcase MMC_TIMING_MMC_DDR52:
\t\treturn true;
\t};
\treturn false;
}

static bool sdhci_preset_needed(struct sdhci_host *host, unsigned char timing)
{
\treturn !(host->quirks2 & SDHCI_QUIRK2_PRESET_VALUE_BROKEN) &&
\t       sdhci_timing_has_preset(timing);
}

static bool sdhci_presetable_values_change(struct sdhci_host *host,
\t\t\t\t\t    struct mmc_ios *ios)
{
\treturn !host->preset_enabled &&
\t       (sdhci_preset_needed(host, ios->timing) ||
\t\thost->drv_type != ios->drv_type);
}

'''
    if text.count(anchor) != 1:
        raise SystemExit("SDHCI preset helper insertion anchor mismatch")
    sdhci.write_text(text.replace(anchor, block + anchor, 1))
    repairs.append("sdhci=restored-preset-helpers")


# The modern fscrypt/F2FS merge uses the dcache flag directly. Define the helper
# once outside the CONFIG_FS_ENCRYPTION split so both configurations compile.
fscrypt_h = root / "include/linux/fscrypt.h"
text = fscrypt_h.read_text()
fscrypt_helper = '''static inline bool fscrypt_is_nokey_name(const struct dentry *dentry)
{
\treturn dentry->d_flags & DCACHE_ENCRYPTED_NAME;
}

'''
if "fscrypt_is_nokey_name(" not in text:
    anchor = "#ifdef CONFIG_FS_ENCRYPTION\n"
    if text.count(anchor) != 1:
        raise SystemExit("fscrypt config insertion anchor mismatch")
    fscrypt_h.write_text(text.replace(anchor, fscrypt_helper + anchor, 1))
    repairs.append("include/linux/fscrypt.h=restored-no-key-name-helper")


# block_validity.c now exposes the inode-aware helper. Update the two stale
# callers instead of reintroducing the removed superblock-only API.
ext4_inode = root / "fs/ext4/inode.c"
text = ext4_inode.read_text()
old_one = (
    "ext4_data_block_valid(EXT4_SB(inode->i_sb), map->m_pblk,\n"
    "\t\t\t\t   map->m_len)"
)
new_one = "ext4_inode_block_valid(inode, map->m_pblk, map->m_len)"
if old_one in text:
    text = text.replace(old_one, new_one, 1)
    repairs.append("ext4-inode=updated-map-block-validator")
elif new_one not in text:
    raise SystemExit("ext4 map-block validator call is not recognized")
old_two = "ext4_data_block_valid(EXT4_SB(sb), ei->i_file_acl, 1)"
new_two = "ext4_inode_block_valid(inode, ei->i_file_acl, 1)"
if old_two in text:
    text = text.replace(old_two, new_two, 1)
    repairs.append("ext4-inode=updated-xattr-block-validator")
elif new_two not in text:
    raise SystemExit("ext4 xattr-block validator call is not recognized")
ext4_inode.write_text(text)


# Some Samsung clock-scaling code can retain the legacy helper call after the
# state-bit API was removed. Map it to the current BKOPS state representation.
mmc_card_h = root / "include/linux/mmc/card.h"
core_c = root / "drivers/mmc/core/core.c"
core_text = core_c.read_text()
card_text = mmc_card_h.read_text()
if "mmc_card_doing_bkops(" in core_text and "mmc_card_doing_bkops(" not in card_text:
    anchor = "static inline bool mmc_card_support_auto_bkops(const struct mmc_card *c)\n"
    block = '''static inline bool mmc_card_doing_bkops(const struct mmc_card *c)
{
\treturn c->bkops.needs_bkops;
}

'''
    if card_text.count(anchor) != 1:
        raise SystemExit("MMC BKOPS helper insertion anchor mismatch")
    mmc_card_h.write_text(card_text.replace(anchor, block + anchor, 1))
    repairs.append("mmc-card=restored-bkops-state-helper")


# Final source-shape checks.
checks = {
    "chacha20": "void chacha20_block(u32 *state, u8 *stream)" in chacha.read_text(),
    "timer_alias": new_timer in timer_h.read_text(),
    "random_init": main.read_text().count("random_init(command_line);") == 1,
    "fscrypt_nokey": fscrypt_h.read_text().count("fscrypt_is_nokey_name(") == 1,
    "ext4_old_call_removed": "ext4_data_block_valid(" not in ext4_inode.read_text(),
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("link compatibility postconditions failed: " + ", ".join(failed))

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- \
  lib/chacha20.c \
  include/linux/timer.h \
  include/linux/fscrypt.h \
  include/linux/mmc/card.h \
  init/main.c init/initramfs.c \
  kernel/sched/cpufreq_schedutil.c \
  fs/proc/internal.h fs/ext4/inode.c \
  drivers/iommu/io-pgtable-arm.c \
  drivers/mmc/core/host.c drivers/mmc/host/sdhci.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'result=linux-4.19.325-final-link-compatibility-repaired\n'
  printf 'restored_chacha20_source=yes\n'
  printf 'restored_timer_alias=yes\n'
  printf 'updated_stale_callers=yes\n'
} | tee -a "$REPORT"

info "Linux $TARGET_VERSION final link compatibility repaired"
