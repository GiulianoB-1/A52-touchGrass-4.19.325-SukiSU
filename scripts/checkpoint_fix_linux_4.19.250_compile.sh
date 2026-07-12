#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.250
REPORT="$ARTIFACTS_DIR/compile-api-fix-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before compile repair"

python3 - "$KERNEL_DIR" "$TOUCHGRASS_COMMIT" <<'PY'
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
touchgrass = sys.argv[2]


def git_blob(path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "show", f"{touchgrass}:{path}"],
        text=True,
    )


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


assembler = root / "arch/arm64/include/asm/assembler.h"
text = assembler.read_text()
block = (
    "/*\n"
    " * Clear Branch History instruction\n"
    " */\n"
    "\t.macro clearbhb\n"
    "\thint\t#22\n"
    "\t.endm\n"
)
count = text.count(block)
if count != 2:
    raise SystemExit(f"arm64 clearbhb duplicate: expected two definitions, found {count}")
text = text.replace(block + "\n" + block, block, 1)
assembler.write_text(text)

final = assembler.read_text()
if final.count("\t.macro clearbhb\n") != 1:
    raise SystemExit("arm64 clearbhb postcondition failed")
print("applied=arm64 clearbhb duplicate removal")

verifier = root / "kernel/bpf/verifier.c"
text = verifier.read_text()
start = text.index("static int convert_ctx_accesses(")
end = text.index("\nstatic int jit_subprogs(", start)
segment = text[start:end]

declaration_anchor = "\t\tbool ctx_access;\n\n"
declaration = (
    "\t\tbool ctx_access;\n"
    "\t\tbpf_convert_ctx_access_t convert_ctx_access;\n\n"
)
if segment.count(declaration_anchor) != 1:
    raise SystemExit(
        "BPF context-conversion declaration anchor mismatch: "
        f"found {segment.count(declaration_anchor)}"
    )
segment = segment.replace(declaration_anchor, declaration, 1)

dead_filter = (
    "\t\tif (env->insn_aux_data[i + delta].ptr_type != PTR_TO_CTX)\n"
    "\t\t\tcontinue;\n"
)
if segment.count(dead_filter) != 1:
    raise SystemExit(
        "BPF pointer-type filter mismatch: "
        f"found {segment.count(dead_filter)}"
    )
segment = segment.replace(dead_filter, "", 1)
text = text[:start] + segment + text[end:]
verifier.write_text(text)

final = verifier.read_text()
start = final.index("static int convert_ctx_accesses(")
end = final.index("\nstatic int jit_subprogs(", start)
segment = final[start:end]
if segment.count("bpf_convert_ctx_access_t convert_ctx_access;") != 1:
    raise SystemExit("BPF conversion callback declaration postcondition failed")
if dead_filter in segment:
    raise SystemExit("BPF PTR_TO_CTX-only filter remains after repair")
for required in (
    "case PTR_TO_CTX:",
    "case PTR_TO_SOCKET:",
    "case PTR_TO_SOCK_COMMON:",
    "case PTR_TO_TCP_SOCK:",
    "cnt = convert_ctx_access(type, insn, insn_buf, env->prog,",
):
    if required not in segment:
        raise SystemExit(f"BPF conversion dispatch postcondition missing: {required}")
print("applied=BPF context-conversion callback and pointer dispatch")

# The stable filename implementation expects the old 4.19 fscrypt_info layout,
# while touchGrass carries Samsung's newer fscrypt key and policy structures.
# Restore the matching touchGrass implementation and object list as one unit.
fname = root / "fs/crypto/fname.c"
crypto_makefile = root / "fs/crypto/Makefile"
vendor_fname = git_blob("fs/crypto/fname.c")
vendor_makefile = git_blob("fs/crypto/Makefile")
if fname.read_text() != vendor_fname:
    fname.write_text(vendor_fname)
if crypto_makefile.read_text() != vendor_makefile:
    crypto_makefile.write_text(vendor_makefile)

fname_text = fname.read_text()
for required in (
    "const struct fscrypt_info *ci = inode->i_crypt_info;",
    "struct crypto_skcipher *tfm = ci->ci_key.tfm;",
    "fscrypt_policy_flags(&ci->ci_policy)",
    "struct fscrypt_nokey_name",
):
    if required not in fname_text:
        raise SystemExit(f"touchGrass fscrypt filename postcondition missing: {required}")
make_text = crypto_makefile.read_text()
for obj in ("hkdf.o", "keyring.o", "keysetup.o", "keysetup_v1.o"):
    if obj not in make_text:
        raise SystemExit(f"touchGrass fscrypt object missing from Makefile: {obj}")
if "keyinfo.o" in make_text:
    raise SystemExit("obsolete fscrypt keyinfo.o remains in Makefile")
print("applied=touchGrass fscrypt filename API and object set")

# Keep Samsung's dynamically allocated wakeup_source pointer, but finish the
# stable sysfs_emit_at conversion in pm_show_wakelocks().
wakelock = root / "kernel/power/wakelock.c"
text = wakelock.read_text()
start = text.index("ssize_t pm_show_wakelocks(")
end = text.index("\n#if CONFIG_PM_WAKELOCKS_LIMIT", start)
segment = text[start:end]
old_emit = '\t\t\tstr += scnprintf(str, end - str, "%s ", wl->name);\n'
new_emit = '\t\t\tlen += sysfs_emit_at(buf, len, "%s ", wl->name);\n'
if segment.count(old_emit) != 1:
    raise SystemExit(f"wakelock emit anchor mismatch: found {segment.count(old_emit)}")
segment = segment.replace(old_emit, new_emit, 1)
text = text[:start] + segment + text[end:]
wakelock.write_text(text)

final = wakelock.read_text()
start = final.index("ssize_t pm_show_wakelocks(")
end = final.index("\n#if CONFIG_PM_WAKELOCKS_LIMIT", start)
segment = final[start:end]
if "struct wakeup_source\t*ws;" not in final:
    raise SystemExit("wakelock vendor wakeup_source pointer is missing")
for required in (
    "if (wl->ws->active == show_active)",
    'len += sysfs_emit_at(buf, len, "%s ", wl->name);',
    'len += sysfs_emit_at(buf, len, "\\n");',
    "return len;",
):
    if required not in segment:
        raise SystemExit(f"wakelock postcondition missing: {required}")
if "str +=" in segment or "end - str" in segment:
    raise SystemExit("obsolete wakelock string cursor remains")
print("applied=wakelock vendor pointer with stable sysfs emission")

# Samsung's ext4 checker macro already has the late-stable logical-block
# argument. Thread the htree leaf block into dx_make_map(), and identify the
# root directory block explicitly for the dot and dotdot checks.
namei = root / "fs/ext4/namei.c"
text = namei.read_text()
old_proto = (
    "static int dx_make_map(struct inode *dir, struct buffer_head *bh,\n"
    "\t\t       struct dx_hash_info *hinfo,\n"
    "\t\t       struct dx_map_entry *map_tail);\n"
)
new_proto = (
    "static int dx_make_map(struct inode *dir, struct buffer_head *bh,\n"
    "\t\t       ext4_lblk_t lblk, struct dx_hash_info *hinfo,\n"
    "\t\t       struct dx_map_entry *map_tail);\n"
)
old_def = (
    "static int dx_make_map(struct inode *dir, struct buffer_head *bh,\n"
    "\t\t       struct dx_hash_info *hinfo,\n"
    "\t\t       struct dx_map_entry *map_tail)\n"
)
new_def = (
    "static int dx_make_map(struct inode *dir, struct buffer_head *bh,\n"
    "\t\t       ext4_lblk_t lblk, struct dx_hash_info *hinfo,\n"
    "\t\t       struct dx_map_entry *map_tail)\n"
)
old_map_check = (
    "\t\tif (ext4_check_dir_entry(dir, NULL, de, bh, base, buflen,\n"
    "\t\t\t\t\t ((char *)de) - base))\n"
)
new_map_check = (
    "\t\tif (ext4_check_dir_entry(dir, NULL, de, bh, base, buflen,\n"
    "\t\t\t\t\t lblk, ((char *)de) - base))\n"
)
old_map_call = "\tcount = dx_make_map(dir, *bh, hinfo, map);\n"
new_map_call = (
    "\tcount = dx_make_map(dir, *bh, dx_get_block(frame->at), hinfo, map);\n"
)
text = replace_once(text, old_proto, new_proto, "ext4 dx_make_map prototype")
text = replace_once(text, old_def, new_def, "ext4 dx_make_map definition")
text = replace_once(text, old_map_check, new_map_check,
                    "ext4 dx_make_map directory check")
text = replace_once(text, old_map_call, new_map_call, "ext4 dx_make_map caller")

old_dot = (
    "\t\tif (ext4_check_dir_entry(inode, NULL, de, bh, bh->b_data,\n"
    "\t\t\t\t\t bh->b_size, 0) ||\n"
)
new_dot = (
    "\t\tif (ext4_check_dir_entry(inode, NULL, de, bh, bh->b_data,\n"
    "\t\t\t\t\t bh->b_size, 0, 0) ||\n"
)
old_dotdot = (
    "\t\tif (ext4_check_dir_entry(inode, NULL, de, bh, bh->b_data,\n"
    "\t\t\t\t\t bh->b_size, offset) ||\n"
)
new_dotdot = (
    "\t\tif (ext4_check_dir_entry(inode, NULL, de, bh, bh->b_data,\n"
    "\t\t\t\t\t bh->b_size, 0, offset) ||\n"
)
text = replace_once(text, old_dot, new_dot, "ext4 dot entry check")
text = replace_once(text, old_dotdot, new_dotdot, "ext4 dotdot entry check")
namei.write_text(text)

final = namei.read_text()
for required in (new_proto, new_def, new_map_check, new_map_call, new_dot, new_dotdot):
    if final.count(required) != 1:
        raise SystemExit("ext4 logical-block postcondition failed")
for stale in (old_map_check, old_map_call, old_dot, old_dotdot):
    if stale in final:
        raise SystemExit("stale ext4 directory-check signature remains")
print("applied=ext4 logical-block directory checks")

# The late CPU hotplug table names random-pool callbacks whose declarations are
# provided by linux/random.h. The merge retained the table but dropped the
# public include.
cpu = root / "kernel/cpu.c"
text = cpu.read_text()
random_include = "#include <linux/random.h>\n"
if random_include not in text:
    anchor = "#include <linux/cpuset.h>\n"
    text = replace_once(text, anchor, anchor + random_include,
                        "CPU hotplug random include")
    cpu.write_text(text)

final = cpu.read_text()
if final.count(random_include) != 1:
    raise SystemExit("CPU hotplug random include postcondition failed")
for callback in ("random_prepare_cpu", "random_online_cpu"):
    if callback not in final:
        raise SystemExit(f"CPU hotplug callback missing: {callback}")
print("applied=CPU hotplug random callback declarations")
PY

{
  echo 'target=4.19.250'
  echo 'arm64_clearbhb_definitions=1'
  echo 'bpf_convert_ctx_access_callback=restored'
  echo 'bpf_socket_pointer_dispatch=reachable'
  echo 'fscrypt_filename_api=touchgrass'
  echo 'fscrypt_object_set=touchgrass'
  echo 'wakelock_sysfs_emit=stable-with-vendor-pointer'
  echo 'ext4_directory_lblk=threaded'
  echo 'cpu_random_hotplug_declarations=restored'
  echo 'result=compile-api-compatible'
} | tee "$REPORT"

info "Linux $TARGET_VERSION compile mismatches repaired"
