#!/usr/bin/env python3
from pathlib import Path
import os
import shutil
import subprocess
import sys

(
    root_arg,
    base_arg,
    theirs_arg,
    status_arg,
    conflict_arg,
    report_arg,
    policy_arg,
    from_sha,
    to_sha,
) = sys.argv[1:]

root = Path(root_arg)
base_root = Path(base_arg)
theirs_root = Path(theirs_arg)
status_file = Path(status_arg)
conflict_list = Path(conflict_arg)
report = Path(report_arg)
policy_log = Path(policy_arg)

# These conflicts are unrelated to the A52 vendor platform, or are large
# security-sensitive subsystems where the final 4.19.325 implementation is the
# correct baseline for the later audit. All other overlapping regions preserve
# the Android/Samsung/Qualcomm side for this non-flashable build checkpoint.
THEIRS_EXACT = {
    ".gitignore",
    "drivers/char/random.c",
    "include/crypto/chacha20.h",
    "include/linux/random.h",
    "include/trace/events/random.h",
    "kernel/trace/bpf_trace.c",
    "net/core/filter.c",
    "security/integrity/iint.c",
    "security/selinux/avc.c",
    "drivers/tty/tty_jobctrl.c",
    "drivers/scsi/sd.c",
    "virt/kvm/arm/psci.c",
}
THEIRS_PREFIXES = (
    "Documentation/",
    "arch/arm/",
    "arch/mips/",
    "arch/x86/",
    "drivers/edac/",
    "drivers/gpu/drm/virtio/",
    "drivers/hid/",
    "drivers/media/dvb-core/",
    "drivers/net/ethernet/broadcom/",
    "drivers/nfc/",
    "fs/ubifs/",
    "kernel/bpf/",
    "mm/kasan/",
)


def exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def identity(path: Path):
    if not exists(path):
        return ("missing", b"")
    if path.is_symlink():
        return ("symlink", os.readlink(path).encode())
    return ("file", path.read_bytes())


def remove_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_entry(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if exists(destination):
        remove_entry(destination)
    if source.is_symlink():
        os.symlink(os.readlink(source), destination)
    else:
        shutil.copy2(source, destination)


def regular_text(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    return b"\0" not in path.read_bytes()


def symlink_ancestor(path: Path):
    current = path.parent
    while current != root:
        if current.is_symlink():
            return current
        current = current.parent
    return None


def policy(path: str):
    if path in THEIRS_EXACT:
        return "theirs", "explicit-upstream"
    if path.startswith(THEIRS_PREFIXES):
        return "theirs", "non-a52-or-upstream-audit-baseline"
    return "ours", "preserve-android-samsung-qualcomm"


def resolve_markers(data: bytes, choice: str):
    lines = data.splitlines(keepends=True)
    output = []
    regions = 0
    i = 0
    while i < len(lines):
        if not lines[i].startswith(b"<<<<<<<"):
            output.append(lines[i])
            i += 1
            continue
        regions += 1
        i += 1
        ours = []
        while i < len(lines) and not lines[i].startswith(b"|||||||"):
            ours.append(lines[i])
            i += 1
        if i >= len(lines):
            raise RuntimeError("malformed diff3 conflict: missing base marker")
        i += 1
        base = []
        while i < len(lines) and not lines[i].startswith(b"======="):
            base.append(lines[i])
            i += 1
        if i >= len(lines):
            raise RuntimeError("malformed diff3 conflict: missing separator")
        i += 1
        theirs = []
        while i < len(lines) and not lines[i].startswith(b">>>>>>>"):
            theirs.append(lines[i])
            i += 1
        if i >= len(lines):
            raise RuntimeError("malformed diff3 conflict: missing end marker")
        i += 1
        output.extend(ours if choice == "ours" else theirs)
    result = b"".join(output)
    if b"<<<<<<<" in result or b"|||||||" in result or b">>>>>>>" in result:
        raise RuntimeError("conflict marker remains after policy resolution")
    return result, regions


raw = status_file.read_bytes().split(b"\0")
if raw and raw[-1] == b"":
    raw.pop()
if len(raw) % 2:
    raise SystemExit(f"unexpected name-status field count: {len(raw)}")

stats = {
    "paths": 0,
    "upstream_taken": 0,
    "already_equal": 0,
    "clean_merges": 0,
    "vendor_deletions": 0,
    "vendor_symlink_paths": 0,
    "conflicts_detected": 0,
    "policy_ours_paths": 0,
    "policy_theirs_paths": 0,
    "policy_regions": 0,
    "unresolved_conflicts": 0,
}
conflicts = []
policy_rows = []


def record_policy(rel: str, choice: str, reason: str, regions: int = 1):
    conflicts.append(rel)
    stats["conflicts_detected"] += 1
    stats[f"policy_{choice}_paths"] += 1
    stats["policy_regions"] += regions
    policy_rows.append(f"{rel}\t{choice}\t{reason}\tregions={regions}\n")


for i in range(0, len(raw), 2):
    status = raw[i].decode("ascii", "strict")
    rel = raw[i + 1].decode("utf-8", "surrogateescape")
    stats["paths"] += 1
    ours = root / rel
    base = base_root / rel
    theirs = theirs_root / rel

    if symlink_ancestor(ours) is not None:
        stats["vendor_symlink_paths"] += 1
        continue

    ours_id = identity(ours)
    base_id = identity(base)
    theirs_id = identity(theirs)

    if status == "A":
        if ours_id[0] == "missing":
            copy_entry(theirs, ours)
            stats["upstream_taken"] += 1
        elif ours_id == theirs_id:
            stats["already_equal"] += 1
        else:
            choice, reason = policy(rel)
            if choice == "theirs":
                copy_entry(theirs, ours)
            record_policy(rel, choice, reason)
        continue

    if status == "D":
        if ours_id[0] == "missing":
            stats["already_equal"] += 1
        elif ours_id == base_id:
            remove_entry(ours)
            stats["vendor_deletions"] += 1
        else:
            choice, reason = policy(rel)
            if choice == "theirs":
                remove_entry(ours)
            record_policy(rel, choice, reason)
        continue

    if status not in {"M", "T"}:
        choice, reason = policy(rel)
        if choice == "theirs":
            if theirs_id[0] == "missing":
                if exists(ours):
                    remove_entry(ours)
            else:
                copy_entry(theirs, ours)
        record_policy(rel, choice, reason)
        continue

    if ours_id == base_id:
        copy_entry(theirs, ours)
        stats["upstream_taken"] += 1
        continue
    if ours_id == theirs_id:
        stats["already_equal"] += 1
        continue

    choice, reason = policy(rel)
    if ours_id[0] == "missing" or base_id[0] == "missing" or theirs_id[0] == "missing":
        if choice == "theirs":
            if theirs_id[0] == "missing":
                if exists(ours):
                    remove_entry(ours)
            else:
                copy_entry(theirs, ours)
        record_policy(rel, choice, reason)
        continue

    if not (regular_text(ours) and regular_text(base) and regular_text(theirs)):
        if choice == "theirs":
            copy_entry(theirs, ours)
        record_policy(rel, choice, reason)
        continue

    merged = subprocess.run(
        ["git", "merge-file", "-p", "--diff3", str(ours), str(base), str(theirs)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if merged.returncode == 255:
        raise SystemExit(
            f"git merge-file failed for {rel}: {merged.stderr.decode(errors='replace')}"
        )
    if merged.returncode == 0:
        ours.write_bytes(merged.stdout)
        stats["clean_merges"] += 1
    else:
        resolved, regions = resolve_markers(merged.stdout, choice)
        ours.write_bytes(resolved)
        record_policy(rel, choice, reason, regions)

conflict_list.write_text("".join(f"{path}\n" for path in conflicts))
policy_log.write_text("".join(policy_rows))
report.write_text(
    f"from_tag=v4.19.154\n"
    f"from_commit={from_sha}\n"
    f"to_tag=v4.19.325\n"
    f"to_commit={to_sha}\n"
    + "".join(f"{key}={value}\n" for key, value in stats.items())
    + "result=policy-resolved-direct-merge\n"
    + "flashable=no\n"
)
print(report.read_text(), end="")
