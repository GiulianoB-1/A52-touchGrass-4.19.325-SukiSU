#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

GKI_SHA = "f960ed27302b1ff8e61e152fc202554d778deccd"

PROBES = {
    "erofs": {
        "target": ",".join((
            "fs/erofs/super.o", "fs/erofs/inode.o", "fs/erofs/data.o",
            "fs/erofs/namei.o", "fs/erofs/dir.o", "fs/erofs/utils.o",
            "fs/erofs/pcpubuf.o", "fs/erofs/xattr.o",
            "fs/erofs/decompressor.o", "fs/erofs/zmap.o", "fs/erofs/zdata.o",
        )),
        "config": "CONFIG_EROFS_FS",
        "description": "EROFS system and vendor filesystem stack",
        "enable": (
            "CONFIG_EROFS_FS", "CONFIG_EROFS_FS_XATTR", "CONFIG_EROFS_FS_ZIP",
        ),
        "sources": (
            "fs/erofs/super.c", "fs/erofs/inode.c", "fs/erofs/data.c",
            "fs/erofs/namei.c", "fs/erofs/dir.c", "fs/erofs/utils.c",
            "fs/erofs/pcpubuf.c", "fs/erofs/xattr.c",
            "fs/erofs/decompressor.c", "fs/erofs/zmap.c", "fs/erofs/zdata.c",
        ),
    },
    "f2fs": {
        "target": ",".join((
            "fs/f2fs/dir.o", "fs/f2fs/file.o", "fs/f2fs/inode.o",
            "fs/f2fs/namei.o", "fs/f2fs/hash.o", "fs/f2fs/super.o",
            "fs/f2fs/inline.o", "fs/f2fs/checkpoint.o", "fs/f2fs/gc.o",
            "fs/f2fs/data.o", "fs/f2fs/node.o", "fs/f2fs/segment.o",
            "fs/f2fs/recovery.o", "fs/f2fs/shrinker.o",
            "fs/f2fs/extent_cache.o", "fs/f2fs/sysfs.o", "fs/f2fs/xattr.o",
            "fs/f2fs/acl.o", "fs/f2fs/verity.o", "fs/f2fs/compress.o",
        )),
        "config": "CONFIG_F2FS_FS",
        "description": "F2FS userdata, encryption, verity, and compression stack",
        "enable": (
            "CONFIG_F2FS_FS", "CONFIG_F2FS_FS_XATTR",
            "CONFIG_F2FS_FS_POSIX_ACL", "CONFIG_FS_ENCRYPTION",
            "CONFIG_FS_ENCRYPTION_INLINE_CRYPT", "CONFIG_FS_VERITY",
            "CONFIG_F2FS_FS_COMPRESSION",
        ),
        "sources": (
            "fs/f2fs/dir.c", "fs/f2fs/file.c", "fs/f2fs/inode.c",
            "fs/f2fs/namei.c", "fs/f2fs/hash.c", "fs/f2fs/super.c",
            "fs/f2fs/inline.c", "fs/f2fs/checkpoint.c", "fs/f2fs/gc.c",
            "fs/f2fs/data.c", "fs/f2fs/node.c", "fs/f2fs/segment.c",
            "fs/f2fs/recovery.c", "fs/f2fs/shrinker.c",
            "fs/f2fs/extent_cache.c", "fs/f2fs/sysfs.c", "fs/f2fs/xattr.c",
            "fs/f2fs/acl.c", "fs/f2fs/verity.c", "fs/f2fs/compress.c",
        ),
    },
    "dm-core": {
        "target": ",".join((
            "drivers/md/dm.o", "drivers/md/dm-table.o", "drivers/md/dm-target.o",
            "drivers/md/dm-linear.o", "drivers/md/dm-stripe.o",
            "drivers/md/dm-ioctl.o", "drivers/md/dm-io.o",
            "drivers/md/dm-kcopyd.o", "drivers/md/dm-sysfs.o",
            "drivers/md/dm-stats.o", "drivers/md/dm-rq.o",
            "drivers/md/dm-init.o", "drivers/md/dm-uevent.o",
        )),
        "config": "CONFIG_BLK_DEV_DM",
        "description": "Device-mapper core for Android dynamic partitions",
        "enable": (
            "CONFIG_BLK_DEV_DM", "CONFIG_BLK_DEV_DM_BUILTIN",
            "CONFIG_DM_INIT", "CONFIG_DM_UEVENT",
        ),
        "sources": (
            "drivers/md/dm.c", "drivers/md/dm-table.c", "drivers/md/dm-target.c",
            "drivers/md/dm-linear.c", "drivers/md/dm-stripe.c",
            "drivers/md/dm-ioctl.c", "drivers/md/dm-io.c",
            "drivers/md/dm-kcopyd.c", "drivers/md/dm-sysfs.c",
            "drivers/md/dm-stats.c", "drivers/md/dm-rq.c",
            "drivers/md/dm-init.c", "drivers/md/dm-uevent.c",
        ),
    },
    "dm-verity": {
        "target": "drivers/md/dm-verity-target.o,drivers/md/dm-verity-fec.o",
        "config": "CONFIG_DM_VERITY",
        "description": "dm-verity and AVB forward-error correction",
        "enable": (
            "CONFIG_BLK_DEV_DM", "CONFIG_DM_VERITY", "CONFIG_DM_VERITY_FEC",
        ),
        "sources": (
            "drivers/md/dm-verity-target.c", "drivers/md/dm-verity-fec.c",
        ),
    },
    "dm-crypt": {
        "target": "drivers/md/dm-crypt.o",
        "config": "CONFIG_DM_CRYPT",
        "description": "Device-mapper crypt target",
        "enable": (
            "CONFIG_BLK_DEV_DM", "CONFIG_CRYPTO", "CONFIG_DM_CRYPT",
        ),
        "sources": ("drivers/md/dm-crypt.c",),
    },
    "dm-default-key": {
        "target": "drivers/md/dm-default-key.o",
        "config": "CONFIG_DM_DEFAULT_KEY",
        "description": "Android metadata-encryption default-key target",
        "enable": (
            "CONFIG_BLK_DEV_DM", "CONFIG_BLK_INLINE_ENCRYPTION",
            "CONFIG_DM_DEFAULT_KEY",
        ),
        "sources": ("drivers/md/dm-default-key.c",),
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    artifact = args.output.resolve()
    gki_head = output("git", "-C", str(gki), "rev-parse", "HEAD")
    if gki_head != GKI_SHA:
        raise SystemExit(f"unexpected GKI revision: {gki_head}")

    artifact.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for probe, data in PROBES.items():
        missing = [relative for relative in data["sources"] if not (gki / relative).is_file()]
        source_hashes = [
            f"{relative}={sha256(gki / relative)}"
            for relative in data["sources"]
            if (gki / relative).is_file()
        ]
        rows.append({
            "probe": probe,
            "description": str(data["description"]),
            "target": str(data["target"]),
            "config_symbol": str(data["config"]),
            "enable_symbols": ",".join(data["enable"]),
            "source_files": ",".join(data["sources"]),
            "missing_sources": ",".join(missing),
            "source_sha256": ";".join(source_hashes),
        })

    write_tsv(
        artifact / "probe-plan.tsv",
        [
            "probe", "description", "target", "config_symbol",
            "enable_symbols", "source_files", "missing_sources", "source_sha256",
        ],
        rows,
    )
    metadata = [
        "artifact_type=a52xq-gki-5.10-android-storage-stack-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"planned_probes={len(PROBES)}",
        "probe_scope=erofs,f2fs,dm-core,dm-verity,dm-crypt,dm-default-key",
        "source_policy=pinned-official-gki-only",
        "output_scope=individual-object-compilation-only",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def diagnostics(path: Path, limit: int = 20) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    patterns = (
        "error:", "fatal error:", "undefined reference", "No rule to make target",
        "No such file or directory", "implicit declaration", "warning:",
    )
    selected: list[str] = []
    lines = path.read_text(errors="replace").splitlines()
    for line in lines:
        if any(pattern.lower() in line.lower() for pattern in patterns):
            cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        if len(selected) >= limit:
            break
    return selected or [line.strip() for line in lines[-12:] if line.strip()] or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    artifact = args.output.resolve()
    status = args.status_file.resolve()
    with status.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if {row.get("probe") for row in rows} != set(PROBES):
        raise SystemExit("Android storage-stack compile status probe set mismatch")
    shutil.copy2(status, artifact / "compile-status.tsv")

    compiled = sum(row.get("result") == "compiled" for row in rows)
    failed = sum(row.get("result") == "compile-failed" for row in rows)
    blocked = sum(row.get("result") == "config-blocked" for row in rows)
    missing = sum(row.get("result") == "source-missing" for row in rows)
    report = [
        "# A52xq GKI 5.10 Android storage-stack probe", "", "## Result", "",
        f"- compiled: **{compiled}**", f"- compile failures: **{failed}**",
        f"- Kconfig blocked: **{blocked}**", f"- source missing: **{missing}**", "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend([
            f"### `{probe}`", "", f"- target: `{row['target']}`",
            f"- symbol: `{row['config_symbol']}` resolved to `{row['resolved_value']}`",
            f"- result: **{row['result']}**", f"- exit code: `{row['exit_code']}`",
            f"- object produced: `{row['object_produced']}`", "", "First diagnostics:", "",
        ])
        report.extend(
            f"- `{line.replace('`', chr(39))}`"
            for line in diagnostics(artifact / "logs" / f"{probe}.log")
        )
        report.append("")
    (artifact / "PORTING-PROBE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = (artifact / "analysis-metadata.txt").read_text().rstrip().splitlines()
    metadata.extend([
        f"compiled_success={compiled}", f"compile_failed={failed}",
        f"config_blocked={blocked}", f"source_missing={missing}",
    ])
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    files = sorted(
        path for path in artifact.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    with (artifact / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(artifact).as_posix()}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--gki", type=Path, required=True)
    stage_parser.add_argument("--output", type=Path, required=True)
    stage_parser.set_defaults(func=stage)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.add_argument("--status-file", type=Path, required=True)
    finalize_parser.set_defaults(func=finalize)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
