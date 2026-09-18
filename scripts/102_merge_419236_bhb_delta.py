#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path.cwd()
WORKSPACE = ROOT / "workspace"
KERNEL = WORKSPACE / "touchgrass-a52xq"
ARTIFACTS = ROOT / "artifacts"
STABLE = WORKSPACE / "linux-stable-4.19.236-bhb-delta"
BASE_TREE = WORKSPACE / "linux-base-4.19.235-bhb-delta"
THEIRS_TREE = WORKSPACE / "linux-theirs-4.19.236-bhb-delta"

FROM_TAG = "v4.19.235"
TO_TAG = "v4.19.236"
EXPECTED_FROM_SHA = "6b481672f19259632a852d013cacd5655e8d7da8"
EXPECTED_TO_SHA = "67aefbfee14b1f29cb4529911aef7322899ecc8b"
EXPECTED_DELTA_COMMITS = 58

POLICY = ROOT / "scripts" / "05_merge_linux_4.19.325.py"


def run(*args: str, cwd: Path | None = None, capture: bool = False) -> str:
    proc = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return proc.stdout.strip() if capture else ""


def kernel_version(tree: Path = KERNEL) -> str:
    values: dict[str, str] = {}
    for line in (tree / "Makefile").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in {"VERSION", "PATCHLEVEL", "SUBLEVEL"}:
            values[parts[0]] = parts[2]
    return f"{values['VERSION']}.{values['PATCHLEVEL']}.{values['SUBLEVEL']}"


def archive(repo: Path, ref: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    p1 = subprocess.Popen(["git", "-C", str(repo), "archive", ref], stdout=subprocess.PIPE)
    assert p1.stdout is not None
    p2 = subprocess.run(["tar", "-x", "-C", str(destination)], stdin=p1.stdout, check=True)
    p1.stdout.close()
    if p1.wait() != 0 or p2.returncode != 0:
        raise SystemExit(f"failed to archive {ref}")


def verify_bhb(tree: Path) -> None:
    required = {
        "arch/arm64/include/asm/vectors.h": (
            "EL1_VECTOR_BHB_LOOP",
            "CONFIG_MITIGATE_SPECTRE_BRANCH_HISTORY",
        ),
        "arch/arm64/kernel/cpu_errata.c": (
            "ARM64_SPECTRE_BHB",
            "Spectre-BHB",
            "spectre_bhb_enable_mitigation",
        ),
        "arch/arm64/kernel/entry.S": (
            "__bp_harden_el1_vectors",
        ),
        "arch/arm64/Kconfig": (
            "MITIGATE_SPECTRE_BRANCH_HISTORY",
        ),
    }
    for rel, markers in required.items():
        path = tree / rel
        if not path.is_file():
            raise SystemExit(f"BHB boundary file missing after .236 delta: {rel}")
        text = path.read_text()
        for marker in markers:
            if marker not in text:
                raise SystemExit(f"BHB marker missing after .236 delta: {rel}: {marker}")


def main() -> None:
    if not (KERNEL / ".git").is_dir():
        raise SystemExit("prepared touchGrass tree is missing")
    if kernel_version() != "4.19.235":
        raise SystemExit(
            f"Phase102 requires the proven 4.19.235 source baseline, found {kernel_version()}"
        )
    if not POLICY.is_file():
        raise SystemExit(f"required merge policy missing: {POLICY}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for path in (STABLE, BASE_TREE, THEIRS_TREE):
        shutil.rmtree(path, ignore_errors=True)

    repo = os.environ.get("LINUX_STABLE_REPO", "https://github.com/gregkh/linux.git")
    run("git", "init", "-q", str(STABLE))
    run("git", "-C", str(STABLE), "remote", "add", "origin", repo)
    run(
        "git", "-C", str(STABLE), "fetch", "--quiet", "--depth=128", "origin",
        f"refs/tags/{TO_TAG}:refs/tags/{TO_TAG}",
    )

    to_sha = run("git", "-C", str(STABLE), "rev-parse", f"{TO_TAG}^{{commit}}", capture=True)
    if to_sha != EXPECTED_TO_SHA:
        raise SystemExit(f"{TO_TAG} SHA mismatch: {to_sha}")
    run("git", "-C", str(STABLE), "cat-file", "-e", f"{EXPECTED_FROM_SHA}^{{commit}}")
    run("git", "-C", str(STABLE), "update-ref", f"refs/tags/{FROM_TAG}", EXPECTED_FROM_SHA)
    run("git", "-C", str(STABLE), "merge-base", "--is-ancestor", FROM_TAG, TO_TAG)

    commits = run(
        "git", "-C", str(STABLE), "rev-list", "--reverse", "--first-parent",
        f"{FROM_TAG}..{TO_TAG}", capture=True,
    ).splitlines()
    if len(commits) != EXPECTED_DELTA_COMMITS:
        raise SystemExit(
            f"unexpected .235->.236 first-parent delta length: {len(commits)}"
        )
    if commits[-1] != EXPECTED_TO_SHA:
        raise SystemExit(f".236 delta tip mismatch: {commits[-1]}")

    commit_list = run(
        "git", "-C", str(STABLE), "log", "--reverse", "--first-parent",
        "--format=%H%x09%s", f"{FROM_TAG}..{TO_TAG}", capture=True,
    )
    (ARTIFACTS / "phase102-linux-4.19.235-to-4.19.236-commit-list.tsv").write_text(
        commit_list + "\n"
    )

    archive(STABLE, FROM_TAG, BASE_TREE)
    archive(STABLE, TO_TAG, THEIRS_TREE)

    status = ARTIFACTS / "phase102-419236-bhb-delta-name-status.zlist"
    with status.open("wb") as output:
        subprocess.run(
            [
                "git", "-C", str(STABLE), "diff", "--name-status", "-z",
                "--no-renames", FROM_TAG, TO_TAG,
            ],
            stdout=output,
            check=True,
        )

    conflicts = ARTIFACTS / "phase102-419236-bhb-delta-conflicts.txt"
    report = ARTIFACTS / "phase102-419236-bhb-delta-policy.txt"
    policy_log = ARTIFACTS / "phase102-419236-bhb-delta-policy.tsv"
    run(
        "python3", str(POLICY), str(KERNEL), str(BASE_TREE), str(THEIRS_TREE),
        str(status), str(conflicts), str(report), str(policy_log),
        EXPECTED_FROM_SHA, EXPECTED_TO_SHA,
    )

    candidate_version = kernel_version()
    if candidate_version != "4.19.236":
        raise SystemExit(f"merged tree reports {candidate_version}, expected 4.19.236")

    verify_bhb(KERNEL)
    run("git", "-C", str(KERNEL), "diff", "--check")

    state = (
        "experiment=phase102-current-stack-419236-bhb-boundary\n"
        f"from_tag={FROM_TAG}\n"
        f"from_commit={EXPECTED_FROM_SHA}\n"
        f"to_tag={TO_TAG}\n"
        f"to_commit={EXPECTED_TO_SHA}\n"
        f"delta_commits={len(commits)}\n"
        f"reported_kernel_version={candidate_version}\n"
        "spectre_bhb_backport=present\n"
        "baseline=physically-tested-phase101-419235-no-instant-reset\n"
        "current_stack=phase89-plus-existing-scheduler-mm-fuse-zram-resukisu-baseline\n"
    )
    (ARTIFACTS / "phase102-419236-bhb-state.txt").write_text(state)
    print(state, end="")

    shutil.rmtree(STABLE, ignore_errors=True)
    shutil.rmtree(BASE_TREE, ignore_errors=True)
    shutil.rmtree(THEIRS_TREE, ignore_errors=True)


if __name__ == "__main__":
    main()
