#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

source "$SCRIPT_DIR/common.sh"

TARGET_VERSION=4.19.206
SUSFS_BRANCH=kernel-4.19
SUSFS_EXPECTED_VERSION=v1.5.9
LABEL="touchgrass-4.19.206-resukisu-v4.1.0-susfs-v1.5.9-safe"
IMAGE="$ARTIFACTS_DIR/Image-$LABEL"
CONFIG="$ARTIFACTS_DIR/config-$LABEL"
STRINGS="$ARTIFACTS_DIR/Image-$LABEL.strings.txt"
BOOT_OUT="$ARTIFACTS_DIR/a52xq-linux-4.19.206-resukisu-susfs-v1.5.9-inline-boot"

mkdir -p "$ARTIFACTS_DIR" "$BOOT_OUT" intake
chmod +x scripts/*.sh scripts/*.py

info "Preparing exact touchGrass source through Linux 4.19.200"
./scripts/01_prepare_source.sh
./scripts/03_apply_linux_4.19.153.sh
./scripts/04_apply_linux_4.19.154.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.154 4.19.159
./scripts/checkpoint_resolve_linux_4.19.159.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.159 4.19.164
./scripts/checkpoint_resolve_linux_4.19.164.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.164 4.19.180
./scripts/checkpoint_resolve_linux_4.19.180.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.180 4.19.200
./scripts/checkpoint_resolve_linux_4.19.200.sh
test "$(kernel_version)" = 4.19.200 || fail "Expected Linux 4.19.200 before the final merge"

info "Applying official Linux 4.19.206 merge"
cp scripts/checkpoint_merge_linux_4.19.210.sh scripts/checkpoint_merge_linux_4.19.206.generated.sh
sed -i \
  -e 's/TO_TAG=v4.19.210/TO_TAG=v4.19.206/' \
  -e '/^TARGET_COMMIT=/d' \
  -e '/test "$to_sha" = "$TARGET_COMMIT"/d' \
  scripts/checkpoint_merge_linux_4.19.206.generated.sh
chmod +x scripts/checkpoint_merge_linux_4.19.206.generated.sh
./scripts/checkpoint_merge_linux_4.19.206.generated.sh
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Linux 4.19.206 merge did not complete"

info "Applying reviewed Linux 4.19.206 merge-shape repairs"
cp scripts/checkpoint_fix_linux_4.19.210_compile.sh scripts/checkpoint_fix_linux_4.19.206.generated.sh
sed -i 's/TARGET_VERSION=4.19.210/TARGET_VERSION=4.19.206/' \
  scripts/checkpoint_fix_linux_4.19.206.generated.sh
chmod +x scripts/checkpoint_fix_linux_4.19.206.generated.sh
./scripts/checkpoint_fix_linux_4.19.206.generated.sh

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
header = (root / 'include/linux/timerqueue.h').read_text()
path = root / 'drivers/soc/qcom/event_timer.c'
text = path.read_text()
newer = '''static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {
\t.rb_root = RB_ROOT_CACHED,
};
'''
older = '''static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {
\t.head = RB_ROOT,
\t.next = NULL,
};
'''
wants_newer = 'struct rb_root_cached rb_root;' in header
desired = newer if wants_newer else older
obsolete = older if wants_newer else newer
if desired not in text:
    if obsolete not in text:
        raise SystemExit('event_timer initializer shape is unrecognized')
    path.write_text(text.replace(obsolete, desired, 1))

path = root / 'kernel/sched/cpufreq_schedutil.c'
text = path.read_text()
call = 'sugov_clear_global_tunables();'
definition = 'static void sugov_clear_global_tunables(void)'
anchor = 'static void sugov_exit('
if call in text and definition not in text:
    if anchor not in text:
        raise SystemExit('schedutil exit anchor missing')
    helper = '''static void sugov_clear_global_tunables(void)
{
\tif (!have_governor_per_policy())
\t\tglobal_tunables = NULL;
}

'''
    path.write_text(text.replace(anchor, helper + anchor, 1))
elif call not in text:
    raise SystemExit('schedutil cleanup call missing after generic repair')
PY

info "Restoring the working A52 hardware and STM FTS touchscreen configuration"
DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" \
  -e TOUCHSCREEN_STM_FTS5CU56A \
  -e PRINTK -e PRINTK_TIME -e IKCONFIG -e IKCONFIG_PROC \
  -e DEBUG_KERNEL -e FRAME_POINTER -e PANIC_ON_OOPS \
  -e PSTORE -e PSTORE_RAM -e PSTORE_CONSOLE -e PSTORE_PMSG \
  -e SERIAL_MSM_GENI -e SERIAL_MSM_GENI_CONSOLE \
  -e ARCH_QCOM -e ARCH_LAGOON \
  -e QCOM_SCM -e QCOM_RPMH -e QCOM_SMEM -e QCOM_SMP2P \
  -e QCOM_COMMAND_DB -e COMMON_CLK_QCOM -e SDM_GCC_LAGOON \
  -e PINCTRL -e PINCTRL_MSM -e PINCTRL_LAGOON \
  -e REGULATOR -e REGULATOR_QCOM_RPMH \
  -e IOMMU_SUPPORT -e ARM_SMMU -e QTI_IOMMU_SUPPORT \
  -e SCSI -e SCSI_UFSHCD -e SCSI_UFSHCD_PLATFORM -e SCSI_UFS_QCOM \
  -e PHY_QCOM_UFS \
  -e BLK_DEV_INITRD -e DEVTMPFS -e DEVTMPFS_MOUNT \
  -e EXT4_FS -e F2FS_FS -e FS_ENCRYPTION \
  -e SECURITY -e SECURITY_SELINUX -e ANDROID_BINDER_IPC \
  -e INPUT -e INPUT_TOUCHSCREEN -e INPUT_MISC -e INPUT_QPNP_POWER_ON \
  -e DRM -e QCOM_KGSL -e QCOM_KGSL_IOMMU \
  -e MEDIA_SUPPORT -e SOUND -e SND \
  -e WLAN -e CFG80211 -e MAC80211 -e BT
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --set-val PANIC_TIMEOUT 10

info "Replacing obsolete touchGrass exec dispatch"
./scripts/07_patch_resukisu_exec_hook.sh

info "Adapting the ReSukiSU SUSFS wrapper to the maintained Linux 4.19 branch"
python3 - scripts/08_build_resukisu_safe_checkpoint.sh <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text()

old_clone = r'''info "Integrating pinned SUSFS kernel 4.19 patch"
SUSFS_DIR="$KERNEL_DIR/../susfs4ksu-$SUSFS_VERSION"
rm -rf "$SUSFS_DIR"
git init -q "$SUSFS_DIR"
git -C "$SUSFS_DIR" remote add origin "$SUSFS_REPO"
git -C "$SUSFS_DIR" fetch --quiet --depth=1 origin "refs/tags/$SUSFS_TAG"
git -C "$SUSFS_DIR" checkout --quiet --detach FETCH_HEAD
cp "$SUSFS_DIR/kernel_patches/fs/susfs.c" "$KERNEL_DIR/fs/susfs.c"
cp "$SUSFS_DIR/kernel_patches/include/linux/susfs.h" "$KERNEL_DIR/include/linux/susfs.h"
patch -d "$KERNEL_DIR" -p1 --forward --batch --fuzz=3 < "$SUSFS_DIR/kernel_patches/50_add_susfs_in_kernel-4.19.patch"
sed -i 's/[[:space:]]\+$//' "$KERNEL_DIR/fs/namespace.c" "$KERNEL_DIR/fs/overlayfs/readdir.c"
'''
new_clone = r'''info "Integrating maintained SUSFS v1.5.9 Linux 4.19 patch"
SUSFS_DIR="$KERNEL_DIR/../susfs4ksu-kernel-4.19"
rm -rf "$SUSFS_DIR"
git init -q "$SUSFS_DIR"
git -C "$SUSFS_DIR" remote add origin "$SUSFS_REPO"
git -C "$SUSFS_DIR" fetch --quiet --depth=1 origin "refs/heads/kernel-4.19"
git -C "$SUSFS_DIR" checkout --quiet --detach FETCH_HEAD
SUSFS_COMMIT="$(git -C "$SUSFS_DIR" rev-parse HEAD)"
cp "$SUSFS_DIR/kernel_patches/fs/susfs.c" "$KERNEL_DIR/fs/susfs.c"
cp "$SUSFS_DIR/kernel_patches/include/linux/susfs.h" "$KERNEL_DIR/include/linux/susfs.h"
cp "$SUSFS_DIR/kernel_patches/include/linux/susfs_def.h" "$KERNEL_DIR/include/linux/susfs_def.h"
grep -Fq '#define SUSFS_VERSION "v1.5.9"' "$KERNEL_DIR/include/linux/susfs.h" || fail "Unexpected SUSFS branch version"
patch -d "$KERNEL_DIR" -p1 --forward --batch --fuzz=3 < "$SUSFS_DIR/kernel_patches/50_add_susfs_in_kernel-4.19.patch"
sed -i 's/[[:space:]]\+$//' "$KERNEL_DIR/fs/namespace.c" "$KERNEL_DIR/fs/overlayfs/readdir.c"
python3 - "$KERNEL_DIR/include/linux/susfs_def.h" "$RESUKISU_DIR/kernel/feature/kernel_umount.c" <<'SUSFSCOMPATPY'
from pathlib import Path
import sys

header = Path(sys.argv[1])
text = header.read_text()
anchor = '#endif // #ifndef KSU_SUSFS_DEF_H\n'
compat = r'''

/* ReSukiSU compatibility names for the maintained 4.19 SUSFS state bit. */
static inline bool susfs_is_current_proc_umounted(void)
{
    return susfs_is_current_non_root_user_app_proc();
}

static inline void susfs_set_current_proc_umounted(void)
{
    susfs_set_current_non_root_user_app_proc();
}

/* SUS_PATH is disabled in this conservative build, so no monitor is needed. */
static inline void susfs_start_sdcard_monitor_fn(void)
{
}

'''
if 'susfs_is_current_proc_umounted' not in text:
    if text.count(anchor) != 1:
        raise SystemExit('susfs_def.h final guard anchor mismatch')
    header.write_text(text.replace(anchor, compat + anchor, 1))

umount = Path(sys.argv[2])
text = umount.read_text()
line = '    schedule_work(&susfs_extra_works);\n'
if text.count(line) != 1:
    raise SystemExit('ReSukiSU susfs_extra_works anchor mismatch')
umount.write_text(text.replace(line, '', 1))
SUSFSCOMPATPY
'''
if text.count(old_clone) != 1:
    raise SystemExit('old SUSFS clone block mismatch')
text = text.replace(old_clone, new_clone, 1)

text, count = re.subn(
    r'''python3 - "\$KERNEL_DIR/include/linux/sched/user\.h" <<'USERPY'\n.*?\nUSERPY\n''',
    '',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit('legacy SUSFS user_struct compatibility block mismatch')
text = text.replace(
    '''grep -Fq 'unsigned long android_kabi_reserved1;' "$KERNEL_DIR/include/linux/sched/user.h" || \\
  fail "SUSFS user state field is missing"\n''',
    '',
    1,
)

old_config = '''    new_config = ('  -e KSU_MULTI_MANAGER_SUPPORT -d KSU_TRACEPOINT_HOOK -d KSU_MANUAL_HOOK \\\n'
                  '  -e KSU_SUSFS -d KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \\\n'
                  '  -d KSU_MANUAL_HOOK_AUTO_INITRC_HOOK -d KSU_MANUAL_HOOK_AUTO_INPUT_HOOK')'''
new_config = '''    new_config = ('  -e KSU_MULTI_MANAGER_SUPPORT -d KSU_TRACEPOINT_HOOK -d KSU_MANUAL_HOOK \\\n'
                  '  -e KSU_SUSFS -d KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \\\n'
                  '  -d KSU_MANUAL_HOOK_AUTO_INITRC_HOOK -d KSU_MANUAL_HOOK_AUTO_INPUT_HOOK \\\n'
                  '  -d KSU_SUSFS_SUS_PATH -e KSU_SUSFS_SUS_MOUNT -e KSU_SUSFS_SUS_KSTAT \\\n'
                  '  -e KSU_SUSFS_SPOOF_UNAME -d KSU_SUSFS_ENABLE_LOG \\\n'
                  '  -d KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS -d KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG \\\n'
                  '  -d KSU_SUSFS_OPEN_REDIRECT -d KSU_SUSFS_SUS_MAP')'''
if text.count(old_config) != 1:
    raise SystemExit('safe SUSFS config block mismatch')
text = text.replace(old_config, new_config, 1)

text = text.replace('v1.4.2', 'v1.5.9')
text = text.replace(
    '''  printf 'susfs_version=%s\\n' "$SUSFS_VERSION"''',
    '''  printf 'susfs_version=%s\\n' "v1.5.9-kernel-4.19"\n  printf 'susfs_commit=%s\\n' "$SUSFS_COMMIT"''',
    1,
)
path.write_text(text)
PY

info "Building Linux 4.19.206 with ReSukiSU and SUSFS inline hooks"
set -o pipefail
./scripts/08_build_resukisu_safe_checkpoint.sh "$TARGET_VERSION" susfs \
  2>&1 | tee "$ARTIFACTS_DIR/linux-4.19.206-resukisu-susfs-inline.log"

test -s "$IMAGE" || fail "Rooted kernel image is missing"
test -s "$CONFIG" || fail "Rooted kernel configuration is missing"
test -s "$STRINGS" || fail "Kernel strings audit is missing"

info "Enforcing root, inline-hook, hardware, and safety invariants"
grep -Fxq 'CONFIG_KSU=y' "$CONFIG"
grep -Fxq 'CONFIG_KSU_SUSFS=y' "$CONFIG"
grep -Fxq 'CONFIG_KSU_MULTI_MANAGER_SUPPORT=y' "$CONFIG"
grep -Fxq '# CONFIG_KSU_MANUAL_HOOK is not set' "$CONFIG"
grep -Fxq '# CONFIG_KSU_TRACEPOINT_HOOK is not set' "$CONFIG"
grep -Fxq '# CONFIG_KPROBES is not set' "$CONFIG"
grep -Fxq '# CONFIG_KSU_SUSFS_SUS_PATH is not set' "$CONFIG"
grep -Fxq 'CONFIG_KSU_SUSFS_SUS_MOUNT=y' "$CONFIG"
grep -Fxq 'CONFIG_KSU_SUSFS_SUS_KSTAT=y' "$CONFIG"
grep -Fxq 'CONFIG_KSU_SUSFS_SPOOF_UNAME=y' "$CONFIG"
grep -Fxq '# CONFIG_KSU_SUSFS_ENABLE_LOG is not set' "$CONFIG"
grep -Fxq '# CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS is not set' "$CONFIG"
grep -Fxq '# CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG is not set' "$CONFIG"
grep -Fxq '# CONFIG_KSU_SUSFS_OPEN_REDIRECT is not set' "$CONFIG"
grep -Fxq '# CONFIG_KSU_SUSFS_SUS_MAP is not set' "$CONFIG"
grep -Fxq 'CONFIG_TOUCHSCREEN_STM_FTS5CU56A=y' "$CONFIG"
grep -Fxq 'CONFIG_ARCH_LAGOON=y' "$CONFIG"
grep -Fxq 'CONFIG_SCSI_UFS_QCOM=y' "$CONFIG"
grep -Fxq 'CONFIG_QCOM_KGSL=y' "$CONFIG"
grep -Fq 'ksu_handle_sys_read(fd, &buf, &count);' "$KERNEL_DIR/fs/read_write.c"
grep -Fq 'ksu_handle_input_handle_event(&type, &code, &value);' "$KERNEL_DIR/drivers/input/input.c"
grep -Fq 'ksu_handle_setresuid(ruid, euid, suid);' "$KERNEL_DIR/kernel/sys.c"
grep -Fq 'static bool ksu_kernel_umount_enabled = false;' "$KERNEL_DIR/KernelSU/kernel/feature/kernel_umount.c"
grep -Fq 'default_non_root_profile.umount_modules = false;' "$KERNEL_DIR/KernelSU/kernel/policy/allowlist.c"
grep -Fxq 'v4.1.0-2206a7dd-dirty@ReSukiSU' "$STRINGS"

SUSFS_COMMIT="$(git -C "$KERNEL_DIR/../susfs4ksu-kernel-4.19" rev-parse HEAD)"
{
  echo 'status=build-passed'
  echo 'hardware_validated=no'
  echo 'kernel_version=4.19.206-touchGrassKernel+'
  echo 'resukisu=v4.1.0-2206a7dd'
  echo 'susfs=v1.5.9-kernel-4.19'
  echo "susfs_commit=$SUSFS_COMMIT"
  echo 'hook_mode=susfs-inline'
  echo 'manual_hook=off'
  echo 'tracepoint_hook=off'
  echo 'kernel_unmount_default=off'
  echo 'module_unmount_default=off'
  echo 'sus_path=off'
  echo 'sus_mount=on'
  echo 'sus_kstat=on'
  echo 'spoof_uname=on'
  echo 'automatic_unmount=off'
  echo 'open_redirect=off'
  echo 'cmdline_spoof=off'
  echo 'symbol_hiding=off'
  echo 'touchscreen=stm-fts-enabled'
  echo "image_sha256=$(sha256sum "$IMAGE" | awk '{print $1}')"
} | tee "$ARTIFACTS_DIR/linux-4.19.206-resukisu-susfs-inline-metadata.txt"

info "Downloading and validating the checksum-locked original boot image"
: "${GH_TOKEN:?GH_TOKEN is required for boot image download}"
ASSET_LINE="$(gh api --paginate "repos/${GITHUB_REPOSITORY}/releases?per_page=30" \
  --jq '.[] | .assets[] | select(.name | endswith(".img")) | [.id, .name, .created_at] | @tsv' | head -n 1)"
test -n "$ASSET_LINE" || fail "No boot image release asset was found"
IFS=$'\t' read -r ASSET_ID ASSET_NAME ASSET_CREATED <<< "$ASSET_LINE"
curl --fail --location --retry 3 --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/octet-stream' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/releases/assets/${ASSET_ID}" \
  --output intake/boot.img
python3 scripts/37_validate_a52_p1_boot_source.py intake/boot.img \
  --lock projects/a52-p1-boot-image/boot-source.lock \
  --output "$BOOT_OUT/source-validation.json"
printf 'source_asset=%s\nsource_created=%s\n' "$ASSET_NAME" "$ASSET_CREATED" \
  > "$BOOT_OUT/source-asset.txt"

info "Repacking and auditing the experimental rooted boot image"
gzip -n -9 -c "$IMAGE" > "$BOOT_OUT/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source intake/boot.img \
  --kernel "$BOOT_OUT/Image.gz" \
  --output "$BOOT_OUT/boot.img" \
  --report "$BOOT_OUT/repack-report.json"
cp "$CONFIG" "$BOOT_OUT/final.config"
cp "$ARTIFACTS_DIR/linux-4.19.206-resukisu-susfs-inline-metadata.txt" "$BOOT_OUT/metadata.txt"
cat > "$BOOT_OUT/NOTICE.txt" <<'NOTICE'
EXPERIMENTAL A52XQ LINUX 4.19.206 ROOTED BOOT IMAGE

ReSukiSU v4.1.0 and maintained SUSFS v1.5.9 for Linux 4.19 are enabled.
Hook mode: SUSFS inline hook. ReSukiSU manual and tracepoint hooks are disabled.
The STM FTS touchscreen and the working A52 hardware configuration are retained.
Kernel and module unmount defaults are disabled.

For the first hardware test, SUS path hiding, automatic unmounting, open redirect,
command-line spoofing, symbol hiding, SUS map, and SUS-SU are disabled. SUS mount
hiding, kstat spoofing, and uname spoofing remain enabled.

This build passed compilation and repack audits only. It has not been validated on
physical hardware. Back up boot and data before testing, and keep the known-working
4.19.206 boot image available for immediate rollback.
NOTICE
(
  cd "$BOOT_OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

info "Linux 4.19.206 ReSukiSU plus SUSFS inline build and repack passed"
