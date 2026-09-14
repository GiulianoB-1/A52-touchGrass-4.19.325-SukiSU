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
STABLE = WORKSPACE / "linux-stable-4.19.207-full207-current"
BASE_TREE = WORKSPACE / "linux-base-4.19.206-full207-current"
THEIRS_TREE = WORKSPACE / "linux-theirs-4.19.207-full207-current"

GOOD_TAG = "v4.19.206"
BAD_TAG = "v4.19.207"
TEST_POSITION = 295
PRIOR_BAD_POSITION = 73

EXPECTED_GOOD_SHA = "b172b44fcb1771e083aad806fa96f3f60e2ddfac"
EXPECTED_TEST_SHA = "2950c9c5e0df6bd34af45a5168bbee345e95eae2"
EXPECTED_PRIOR_BAD_SHA = "c5c62f4c936407fb734cae64700f20b68273b059"
# Phase97 intentionally keeps boot diagnostics out of the bisect delta.

POLICY = ROOT / "scripts" / "05_merge_linux_4.19.325.py"
FIX_TEMPLATE = ROOT / "scripts" / "checkpoint_fix_linux_4.19.210_compile.sh"


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


def repair_merge_shapes() -> None:
    # The historical 4.19.207 bisect needed these two vendor/upstream shape
    # repairs after the generic three-way policy resolver.
    header = (KERNEL / "include/linux/timerqueue.h").read_text()
    path = KERNEL / "drivers/soc/qcom/event_timer.c"
    text = path.read_text()
    newer = """static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {
\t.rb_root = RB_ROOT_CACHED,
};
"""
    older = """static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {
\t.head = RB_ROOT,
\t.next = NULL,
};
"""
    wants_newer = "struct rb_root_cached rb_root;" in header
    desired = newer if wants_newer else older
    obsolete = older if wants_newer else newer
    if desired not in text:
        if obsolete not in text:
            raise SystemExit("event_timer initializer shape is unrecognized")
        path.write_text(text.replace(obsolete, desired, 1))

    path = KERNEL / "kernel/sched/cpufreq_schedutil.c"
    text = path.read_text()
    call = "sugov_clear_global_tunables();"
    definition = "static void sugov_clear_global_tunables(void)"
    anchor = "static void sugov_exit("
    if call in text and definition not in text:
        if anchor not in text:
            raise SystemExit("schedutil exit anchor missing")
        helper = """static void sugov_clear_global_tunables(void)
{
\tif (!have_governor_per_policy())
\t\tglobal_tunables = NULL;
}

"""
        path.write_text(text.replace(anchor, helper + anchor, 1))
    elif call not in text:
        raise SystemExit("schedutil cleanup call missing after generic repair")


def main() -> None:
    if not (KERNEL / ".git").is_dir():
        raise SystemExit("prepared touchGrass tree is missing")
    if kernel_version() != "4.19.206":
        raise SystemExit(f"expected current stack on Linux 4.19.206, found {kernel_version()}")
    for helper in (POLICY, FIX_TEMPLATE):
        if not helper.is_file():
            raise SystemExit(f"required helper missing: {helper}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for path in (STABLE, BASE_TREE, THEIRS_TREE):
        shutil.rmtree(path, ignore_errors=True)

    repo = os.environ.get("LINUX_STABLE_REPO", "https://github.com/gregkh/linux.git")
    run("git", "init", "-q", str(STABLE))
    run("git", "-C", str(STABLE), "remote", "add", "origin", repo)
    run(
        "git", "-C", str(STABLE), "fetch", "--quiet", "--depth=1024", "origin",
        f"refs/tags/{BAD_TAG}:refs/tags/{BAD_TAG}",
    )

    bad_sha = run("git", "-C", str(STABLE), "rev-parse", f"{BAD_TAG}^{{commit}}", capture=True)
    try:
        run("git", "-C", str(STABLE), "cat-file", "-e", f"{EXPECTED_GOOD_SHA}^{{commit}}")
    except subprocess.CalledProcessError:
        run("git", "-C", str(STABLE), "fetch", "--quiet", "--deepen=4096", "origin", BAD_TAG)

    run("git", "-C", str(STABLE), "update-ref", f"refs/tags/{GOOD_TAG}", EXPECTED_GOOD_SHA)
    run("git", "-C", str(STABLE), "merge-base", "--is-ancestor", GOOD_TAG, BAD_TAG)

    commits = run(
        "git", "-C", str(STABLE), "rev-list", "--reverse", "--first-parent",
        f"{GOOD_TAG}..{BAD_TAG}", capture=True,
    ).splitlines()
    if len(commits) < TEST_POSITION:
        raise SystemExit(f"unexpected 4.19.207 range length: {len(commits)}")

    test_sha = commits[TEST_POSITION - 1]
    prior_bad_sha = commits[PRIOR_BAD_POSITION - 1]
    if test_sha != EXPECTED_TEST_SHA:
        raise SystemExit(f"full207 SHA mismatch: {test_sha}")
    if prior_bad_sha != EXPECTED_PRIOR_BAD_SHA:
        raise SystemExit(f"full207 SHA mismatch: {prior_bad_sha}")

    test_subject = run(
        "git", "-C", str(STABLE), "show", "-s", "--format=%s", test_sha, capture=True
    )
    prior_bad_subject = run(
        "git", "-C", str(STABLE), "show", "-s", "--format=%s", prior_bad_sha, capture=True
    )

    commit_list = run(
        "git", "-C", str(STABLE), "log", "--reverse", "--first-parent",
        "--format=%H%x09%s", f"{GOOD_TAG}..{BAD_TAG}", capture=True,
    )
    (ARTIFACTS / "phase97-linux-4.19.207-commit-list.tsv").write_text(commit_list + "\n")

    archive(STABLE, GOOD_TAG, BASE_TREE)
    archive(STABLE, test_sha, THEIRS_TREE)

    status = ARTIFACTS / "phase97-full207-name-status.zlist"
    with status.open("wb") as output:
        subprocess.run(
            [
                "git", "-C", str(STABLE), "diff", "--name-status", "-z",
                "--no-renames", GOOD_TAG, test_sha,
            ],
            stdout=output,
            check=True,
        )

    conflicts = ARTIFACTS / "phase97-full207-conflicts.txt"
    report = ARTIFACTS / "phase97-full207-policy.txt"
    policy_log = ARTIFACTS / "phase97-full207-policy.tsv"
    run(
        "python3", str(POLICY), str(KERNEL), str(BASE_TREE), str(THEIRS_TREE),
        str(status), str(conflicts), str(report), str(policy_log),
        EXPECTED_GOOD_SHA, test_sha,
    )

    candidate_version = kernel_version()
    generated_fix = ROOT / "scripts" / ".phase97-full207-compile-fix.sh"
    fix_text = FIX_TEMPLATE.read_text()
    if fix_text.count("TARGET_VERSION=4.19.210") != 1:
        raise SystemExit("compile-fix target anchor mismatch")
    generated_fix.write_text(
        fix_text.replace("TARGET_VERSION=4.19.210", f"TARGET_VERSION={candidate_version}", 1)
    )
    generated_fix.chmod(0o755)
    try:
        run(str(generated_fix))
    finally:
        generated_fix.unlink(missing_ok=True)

    repair_merge_shapes()
    run("git", "-C", str(KERNEL), "diff", "--check")

    state = (
        "experiment=phase97-current-stack-419207-full\n"
        f"known_good_tag={GOOD_TAG}\n"
        f"known_good_commit={EXPECTED_GOOD_SHA}\n"
        "known_good_applied_commits=0\n"
        f"known_bad_tag={BAD_TAG}\n"
        f"known_bad_commit={bad_sha}\n"
        f"full_release_applied_commits={len(commits)}\n"
        f"prior_bad_applied_commits={PRIOR_BAD_POSITION}\n"
        f"prior_bad_commit={prior_bad_sha}\n"
        f"prior_bad_subject={prior_bad_subject}\n"
        f"test_applied_commits={TEST_POSITION}\n"
        f"test_commit={test_sha}\n"
        f"test_subject={test_subject}\n"
        f"reported_kernel_version={candidate_version}\n"
        "phase90_inmem_curseg=absent\n"
        "current_stack=phase89-plus-existing-scheduler-mm-fuse-zram-resukisu-baseline\n"
    )
    (ARTIFACTS / "phase97-full207-state.txt").write_text(state)
    print(state, end="")

    shutil.rmtree(STABLE, ignore_errors=True)
    shutil.rmtree(BASE_TREE, ignore_errors=True)
    shutil.rmtree(THEIRS_TREE, ignore_errors=True)


if __name__ == "__main__":
    main()
