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
STABLE = WORKSPACE / "linux-stable-4.19.207-second-half-only"
BASE_TREE = WORKSPACE / "linux-base-4.19.207-first-half-end"
THEIRS_TREE = WORKSPACE / "linux-theirs-4.19.207-full"
GOOD_TAG = "v4.19.206"
BAD_TAG = "v4.19.207"
POLICY = ROOT / "scripts" / "05_merge_linux_4.19.325.py"
FIX_SCRIPT = ROOT / "scripts" / "checkpoint_fix_linux_4.19.210_compile.sh"
BUILD_TEMPLATE = ROOT / "scripts" / "40_build_a52_p1_full_hardware_diag.sh"


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


def remote_tag_commit(tag: str) -> str:
    repo = os.environ.get(
        "LINUX_STABLE_REPO",
        "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
    )
    rows = run("git", "ls-remote", repo, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}", capture=True)
    direct = None
    peeled = None
    for row in rows.splitlines():
        sha, ref = row.split("\t", 1)
        if ref.endswith("^{}"):
            peeled = sha
        else:
            direct = sha
    result = peeled or direct
    if not result:
        raise SystemExit(f"Unable to resolve official stable tag {tag}")
    return result


def archive(repo: Path, ref: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    p1 = subprocess.Popen(["git", "-C", str(repo), "archive", ref], stdout=subprocess.PIPE)
    p2 = subprocess.run(["tar", "-x", "-C", str(destination)], stdin=p1.stdout, check=True)
    assert p1.stdout is not None
    p1.stdout.close()
    if p1.wait() != 0 or p2.returncode != 0:
        raise SystemExit(f"Failed to archive {ref}")


def repair_merge_shapes() -> None:
    root = KERNEL
    header = (root / "include/linux/timerqueue.h").read_text()
    path = root / "drivers/soc/qcom/event_timer.c"
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

    path = root / "kernel/sched/cpufreq_schedutil.c"
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
        raise SystemExit("Prepared touchGrass kernel tree is missing")
    if kernel_version() != "4.19.206":
        raise SystemExit(f"Expected prepared Linux 4.19.206 tree, found {kernel_version()}")
    for required in (POLICY, FIX_SCRIPT, BUILD_TEMPLATE):
        if not required.is_file():
            raise SystemExit(f"Required helper is missing: {required}")

    for path in (STABLE, BASE_TREE, THEIRS_TREE):
        shutil.rmtree(path, ignore_errors=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    STABLE.mkdir(parents=True)

    repo = os.environ.get(
        "LINUX_STABLE_REPO",
        "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
    )
    run("git", "init", "-q", cwd=STABLE)
    run("git", "remote", "add", "origin", repo, cwd=STABLE)
    run(
        "git",
        "fetch",
        "--quiet",
        "--depth=1024",
        "origin",
        f"refs/tags/{BAD_TAG}:refs/tags/{BAD_TAG}",
        cwd=STABLE,
    )

    good_sha = remote_tag_commit(GOOD_TAG)
    bad_sha = run("git", "rev-parse", f"{BAD_TAG}^{{commit}}", cwd=STABLE, capture=True)
    try:
        run("git", "cat-file", "-e", f"{good_sha}^{{commit}}", cwd=STABLE)
    except subprocess.CalledProcessError:
        run("git", "fetch", "--quiet", "--deepen=4096", "origin", BAD_TAG, cwd=STABLE)
    run("git", "update-ref", f"refs/tags/{GOOD_TAG}", good_sha, cwd=STABLE)
    run("git", "merge-base", "--is-ancestor", GOOD_TAG, BAD_TAG, cwd=STABLE)

    commits = run(
        "git",
        "rev-list",
        "--reverse",
        "--first-parent",
        f"{GOOD_TAG}..{BAD_TAG}",
        cwd=STABLE,
        capture=True,
    ).splitlines()
    count = len(commits)
    if count < 4:
        raise SystemExit(f"Unexpectedly small 4.19.207 commit range: {count}")

    split_position = max(1, count // 2)
    split_sha = commits[split_position - 1]
    second_half_start_position = split_position + 1
    second_half_start_sha = commits[second_half_start_position - 1]
    split_subject = run("git", "show", "-s", "--format=%s", split_sha, cwd=STABLE, capture=True)
    second_half_start_subject = run(
        "git", "show", "-s", "--format=%s", second_half_start_sha, cwd=STABLE, capture=True
    )
    bad_subject = run("git", "show", "-s", "--format=%s", bad_sha, cwd=STABLE, capture=True)

    commit_list = run(
        "git",
        "log",
        "--reverse",
        "--first-parent",
        "--format=%H%x09%s",
        f"{GOOD_TAG}..{BAD_TAG}",
        cwd=STABLE,
        capture=True,
    )
    (ARTIFACTS / "linux-4.19.207-commit-list.tsv").write_text(commit_list + "\n")

    # Apply only the delta from the end of the first half through v4.19.207
    # onto the hardware-tested v4.19.206 vendor tree. This is intentionally
    # a non-cumulative side experiment, not a valid stable-kernel bisect point.
    archive(STABLE, split_sha, BASE_TREE)
    archive(STABLE, BAD_TAG, THEIRS_TREE)

    status = ARTIFACTS / "linux-4.19.207-second-half-only-name-status.zlist"
    with status.open("wb") as output:
        subprocess.run(
            [
                "git",
                "-C",
                str(STABLE),
                "diff",
                "--name-status",
                "-z",
                "--no-renames",
                split_sha,
                BAD_TAG,
            ],
            stdout=output,
            check=True,
        )

    conflict_list = ARTIFACTS / "linux-4.19.207-second-half-only-conflicts.txt"
    policy_report = ARTIFACTS / "linux-4.19.207-second-half-only-policy.txt"
    policy_log = ARTIFACTS / "linux-4.19.207-second-half-only-policy.tsv"
    run(
        "python3",
        str(POLICY),
        str(KERNEL),
        str(BASE_TREE),
        str(THEIRS_TREE),
        str(status),
        str(conflict_list),
        str(policy_report),
        str(policy_log),
        split_sha,
        bad_sha,
    )

    candidate_version = kernel_version()
    generated_fix = ROOT / "scripts/checkpoint_fix_linux_4.19.207-second-half-only.generated.sh"
    fix_text = FIX_SCRIPT.read_text().replace(
        "TARGET_VERSION=4.19.210", f"TARGET_VERSION={candidate_version}", 1
    )
    generated_fix.write_text(fix_text)
    generated_fix.chmod(0o755)
    run(str(generated_fix))
    repair_merge_shapes()
    run("git", "-C", str(KERNEL), "diff", "--check")

    generated_build = ROOT / "scripts/build_a52_linux_4.19.207_second_half_only.generated.sh"
    build_text = BUILD_TEMPLATE.read_text()
    build_text = build_text.replace(
        "TARGET_VERSION=4.19.325", f"TARGET_VERSION={candidate_version}", 1
    )
    build_text = build_text.replace(
        "a52xq-p1-full-hardware-diag-",
        "a52xq-linux-4.19.207-second-half-only-no-root-",
        1,
    )
    build_text = build_text.replace(
        "-d TOUCHSCREEN_STM_FTS5CU56A",
        "-d KSU -d KSU_SUSFS -d TOUCHSCREEN_STM_FTS5CU56A",
        1,
    )

    lockup_block = r'''# Request lockup-to-panic diagnostics when these 4.19 symbols exist.
for symbol in LOCKUP_DETECTOR SOFTLOCKUP_DETECTOR BOOTPARAM_SOFTLOCKUP_PANIC DETECT_HUNG_TASK BOOTPARAM_HUNG_TASK_PANIC; do
  if grep -RqsE "^[[:space:]]*(menu)?config[[:space:]]+${symbol}([[:space:]]|$)" \
      "$KERNEL_DIR/kernel" "$KERNEL_DIR/lib" "$KERNEL_DIR/arch/arm64"; then
    "$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" -e "$symbol"
  fi
done
if grep -RqsE '^[[:space:]]*(menu)?config[[:space:]]+DEFAULT_HUNG_TASK_TIMEOUT([[:space:]]|$)' \
    "$KERNEL_DIR/kernel" "$KERNEL_DIR/lib" "$KERNEL_DIR/arch/arm64"; then
  "$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --set-val DEFAULT_HUNG_TASK_TIMEOUT 60
fi

'''
    marker = 'build_kernel "$LABEL"'
    if marker not in build_text:
        raise SystemExit("Build template marker is missing")
    build_text = build_text.replace(marker, lockup_block + marker, 1)

    generated_build.write_text(build_text)
    generated_build.chmod(0o755)
    run(str(generated_build))

    out = ARTIFACTS / "p1-full-hardware-diag"
    audit_symbols = [
        "LOCKUP_DETECTOR",
        "SOFTLOCKUP_DETECTOR",
        "BOOTPARAM_SOFTLOCKUP_PANIC",
        "DETECT_HUNG_TASK",
        "BOOTPARAM_HUNG_TASK_PANIC",
        "DEFAULT_HUNG_TASK_TIMEOUT",
        "PANIC_TIMEOUT",
    ]
    final_config = (out / "final.config").read_text().splitlines()
    audit = []
    for symbol in audit_symbols:
        prefix = f"CONFIG_{symbol}="
        disabled = f"# CONFIG_{symbol} is not set"
        match = next((line for line in final_config if line.startswith(prefix) or line == disabled), None)
        audit.append(match or f"CONFIG_{symbol}=<absent>")
    (out / "lockup-diagnostics-audit.txt").write_text("\n".join(audit) + "\n")

    state = (
        "experiment=second-half-only-non-cumulative\n"
        f"known_good_tag={GOOD_TAG}\n"
        f"known_good_commit={good_sha}\n"
        f"full_release_tag={BAD_TAG}\n"
        f"full_release_commit={bad_sha}\n"
        f"full_release_subject={bad_subject}\n"
        f"full_release_applied_commits={count}\n"
        f"omitted_first_half_end_position={split_position}\n"
        f"omitted_first_half_end_commit={split_sha}\n"
        f"omitted_first_half_end_subject={split_subject}\n"
        f"applied_second_half_start_position={second_half_start_position}\n"
        f"applied_second_half_start_commit={second_half_start_sha}\n"
        f"applied_second_half_start_subject={second_half_start_subject}\n"
        f"applied_second_half_commit_count={count - split_position}\n"
        f"reported_kernel_version={candidate_version}\n"
        "bisect_value=side-experiment-only\n"
        "root_integration=none\n"
        "touchscreen_driver=disabled-for-controlled-comparison\n"
        "lockup_diagnostics=requested-when-supported\n"
    )
    (out / "second-half-only-state.txt").write_text(state)
    (ARTIFACTS / "linux-4.19.207-second-half-only-state.txt").write_text(state)
    print(state, end="")


if __name__ == "__main__":
    main()
