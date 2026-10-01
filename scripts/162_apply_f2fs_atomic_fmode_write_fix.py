#!/usr/bin/env python3
from pathlib import Path
import sys

UPSTREAM_STABLE_COMMIT = "700f3a7c7fa5764c9f24bbf7c78e0b6e479fa653"
TARGET = Path("fs/f2fs/file.c")

FUNCTIONS = (
    "f2fs_ioc_start_atomic_write",
    "f2fs_ioc_commit_atomic_write",
    "f2fs_ioc_start_volatile_write",
    "f2fs_ioc_release_volatile_write",
    "f2fs_ioc_abort_volatile_write",
)

GUARD = """\tif (!(filp->f_mode & FMODE_WRITE))
\t\treturn -EBADF;

"""
OWNER_CHECK = """\tif (!inode_owner_or_capable(inode))
\t\treturn -EACCES;
"""


def function_block(text: str, name: str) -> tuple[int, int, str]:
    sig = f"static int {name}(struct file *filp)"
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"{name}: function signature not found")

    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"{name}: opening brace not found")

    depth = 0
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                return start, end, text[start:end]

    raise SystemExit(f"{name}: unterminated function")


def patch(root: Path) -> None:
    path = root / TARGET
    if not path.is_file():
        raise SystemExit(f"missing target: {path}")

    text = path.read_text()
    changed = []

    for name in FUNCTIONS:
        start, end, block = function_block(text, name)

        if OWNER_CHECK not in block:
            raise SystemExit(f"{name}: inode_owner_or_capable anchor missing")

        guard_count = block.count("if (!(filp->f_mode & FMODE_WRITE))")
        if guard_count > 1:
            raise SystemExit(f"{name}: duplicate FMODE_WRITE guard ({guard_count})")

        if guard_count == 0:
            if block.count(OWNER_CHECK) != 1:
                raise SystemExit(
                    f"{name}: expected one ownership-check anchor, "
                    f"found {block.count(OWNER_CHECK)}"
                )
            block = block.replace(OWNER_CHECK, GUARD + OWNER_CHECK, 1)
            text = text[:start] + block + text[end:]
            changed.append(name)

        _, _, verify = function_block(text, name)
        guard_pos = verify.find("if (!(filp->f_mode & FMODE_WRITE))")
        owner_pos = verify.find("if (!inode_owner_or_capable(inode))")
        if guard_pos < 0 or owner_pos < 0 or guard_pos > owner_pos:
            raise SystemExit(f"{name}: FMODE_WRITE guard is not before ownership check")
        if "if (!(filp->f_mode & FMODE_WRITE))\n\t\treturn -EBADF;" not in verify:
            raise SystemExit(f"{name}: FMODE_WRITE guard does not return -EBADF")

    path.write_text(text)

    final = path.read_text()
    for name in FUNCTIONS:
        _, _, block = function_block(final, name)
        if block.count("if (!(filp->f_mode & FMODE_WRITE))") != 1:
            raise SystemExit(f"{name}: final guard count is not exactly one")

    print("P162 F2FS atomic/volatile write FMODE_WRITE hardening: PASS")
    print(f"  stable_commit={UPSTREAM_STABLE_COMMIT}")
    print(f"  affected_ioctls={len(FUNCTIONS)}")
    if changed:
        print("  patched=" + ",".join(changed))
    else:
        print("  patched=already-present")
    print("  behavior=read-only file descriptors return -EBADF before owner/capability checks")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    patch(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
