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
STABLE = WORKSPACE / "linux-stable-4.19.250-full-current"
BASE_TREE = WORKSPACE / "linux-base-4.19.206-full250-current"
THEIRS_TREE = WORKSPACE / "linux-theirs-4.19.250-full-current"

GOOD_TAG = "v4.19.206"
BAD_TAG = "v4.19.250"
TEST_POSITION = 3197
PRIOR_BAD_POSITION = 73

EXPECTED_GOOD_SHA = "b172b44fcb1771e083aad806fa96f3f60e2ddfac"
EXPECTED_TEST_SHA = "7c6679265082335bca08b954208b385143c0d4de"
EXPECTED_PRIOR_BAD_SHA = "c5c62f4c936407fb734cae64700f20b68273b059"
# Phase100 intentionally keeps boot diagnostics out of the bisect delta.

POLICY = ROOT / "scripts" / "05_merge_linux_4.19.325.py"
FIX_TEMPLATES = (
    ROOT / "scripts" / "checkpoint_fix_linux_4.19.250_compile.sh",
    ROOT / "scripts" / "checkpoint_fix_linux_4.19.250_event_usb.sh",
    ROOT / "scripts" / "checkpoint_fix_linux_4.19.250_late.sh",
    ROOT / "scripts" / "checkpoint_fix_linux_4.19.250_later.sh",
    ROOT / "scripts" / "checkpoint_fix_linux_4.19.250_link.sh",
)


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
        text = text.replace(anchor, helper + anchor, 1)
    elif call not in text:
        raise SystemExit("schedutil cleanup call missing after generic repair")

    # The 4.19.250 generic compatibility repair can expose the same Samsung
    # cleanup helper twice: once from the vendor tree and once from the merged
    # upstream shape.  Keep the first implementation only, but refuse to delete
    # anything unless all duplicate function bodies are byte-identical.
    starts = []
    pos = 0
    while True:
        pos = text.find(definition, pos)
        if pos < 0:
            break
        starts.append(pos)
        pos += len(definition)

    def function_end(source: str, start: int) -> int:
        brace = source.find("{", start)
        if brace < 0:
            raise SystemExit("schedutil cleanup helper opening brace missing")
        depth = 0
        for index in range(brace, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    while end < len(source) and source[end] == "\n":
                        end += 1
                    return end
        raise SystemExit("schedutil cleanup helper closing brace missing")

    if len(starts) > 1:
        first_body = text[starts[0]:function_end(text, starts[0])]
        for start in starts[1:]:
            body = text[start:function_end(text, start)]
            if body != first_body:
                raise SystemExit("schedutil duplicate cleanup helpers are not identical")
        for start in reversed(starts[1:]):
            text = text[:start] + text[function_end(text, start):]

    path.write_text(text)
    if text.count(definition) != 1:
        raise SystemExit(f"schedutil cleanup helper count is {text.count(definition)}, expected 1")

    # Samsung's legacy drivers/char/Kconfig opens "Character devices" without
    # closing it locally. With the 4.19.250 drivers/Kconfig layout, that causes
    # the parent file's final endmenu to close the child menu instead, leaving
    # "Device Drivers" unterminated. Restore the upstream-local menu closure
    # while preserving all Samsung char options.
    char_kconfig = KERNEL / "drivers/char/Kconfig"
    char_text = char_kconfig.read_text()
    menu_marker = 'menu "Character devices"'
    menu_count = char_text.count(menu_marker)
    endmenu_count = sum(
        1 for line in char_text.splitlines() if line.strip() == "endmenu"
    )
    if menu_count != 1:
        raise SystemExit(
            f"drivers/char/Kconfig Character devices menu count is {menu_count}, expected 1"
        )
    if endmenu_count == 0:
        if not char_text.endswith("\n"):
            char_text += "\n"
        char_text += "\nendmenu\n"
        char_kconfig.write_text(char_text)
    elif endmenu_count != 1:
        raise SystemExit(
            f"drivers/char/Kconfig endmenu count is {endmenu_count}, expected 0 or 1"
        )

    drivers_kconfig = (KERNEL / "drivers/Kconfig").read_text()
    if drivers_kconfig.count('menu "Device Drivers"') != 1:
        raise SystemExit("drivers/Kconfig Device Drivers menu anchor is not unique")
    if sum(1 for line in drivers_kconfig.splitlines() if line.strip() == "endmenu") != 1:
        raise SystemExit("drivers/Kconfig top-level endmenu count is not one")


def main() -> None:
    if not (KERNEL / ".git").is_dir():
        raise SystemExit("prepared touchGrass tree is missing")
    if kernel_version() != "4.19.206":
        raise SystemExit(f"expected current stack on Linux 4.19.206, found {kernel_version()}")
    for helper in (POLICY, *FIX_TEMPLATES):
        if not helper.is_file():
            raise SystemExit(f"required helper missing: {helper}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for path in (STABLE, BASE_TREE, THEIRS_TREE):
        shutil.rmtree(path, ignore_errors=True)

    repo = os.environ.get("LINUX_STABLE_REPO", "https://github.com/gregkh/linux.git")
    run("git", "init", "-q", str(STABLE))
    run("git", "-C", str(STABLE), "remote", "add", "origin", repo)
    # 4.19.250 is 3197 first-parent commits beyond 4.19.206. Fetch enough
    # history in one transaction; a 1024-depth fetch followed by --deepen can
    # race Git's shallow-file bookkeeping on Actions runners.
    run(
        "git", "-C", str(STABLE), "fetch", "--quiet", "--depth=8192", "origin",
        f"refs/tags/{BAD_TAG}:refs/tags/{BAD_TAG}",
    )

    bad_sha = run("git", "-C", str(STABLE), "rev-parse", f"{BAD_TAG}^{{commit}}", capture=True)
    run("git", "-C", str(STABLE), "cat-file", "-e", f"{EXPECTED_GOOD_SHA}^{{commit}}")

    run("git", "-C", str(STABLE), "update-ref", f"refs/tags/{GOOD_TAG}", EXPECTED_GOOD_SHA)
    run("git", "-C", str(STABLE), "merge-base", "--is-ancestor", GOOD_TAG, BAD_TAG)

    commits = run(
        "git", "-C", str(STABLE), "rev-list", "--reverse", "--first-parent",
        f"{GOOD_TAG}..{BAD_TAG}", capture=True,
    ).splitlines()
    if len(commits) < TEST_POSITION:
        raise SystemExit(f"unexpected 4.19.250 range length: {len(commits)}")

    test_sha = commits[TEST_POSITION - 1]
    prior_bad_sha = commits[PRIOR_BAD_POSITION - 1]
    if test_sha != EXPECTED_TEST_SHA:
        raise SystemExit(f"full250 SHA mismatch: {test_sha}")
    if prior_bad_sha != EXPECTED_PRIOR_BAD_SHA:
        raise SystemExit(f"full250 SHA mismatch: {prior_bad_sha}")

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
    (ARTIFACTS / "phase100-linux-4.19.250-commit-list.tsv").write_text(commit_list + "\n")

    archive(STABLE, GOOD_TAG, BASE_TREE)
    archive(STABLE, test_sha, THEIRS_TREE)

    status = ARTIFACTS / "phase100-full250-name-status.zlist"
    with status.open("wb") as output:
        subprocess.run(
            [
                "git", "-C", str(STABLE), "diff", "--name-status", "-z",
                "--no-renames", GOOD_TAG, test_sha,
            ],
            stdout=output,
            check=True,
        )

    conflicts = ARTIFACTS / "phase100-full250-conflicts.txt"
    report = ARTIFACTS / "phase100-full250-policy.txt"
    policy_log = ARTIFACTS / "phase100-full250-policy.tsv"
    run(
        "python3", str(POLICY), str(KERNEL), str(BASE_TREE), str(THEIRS_TREE),
        str(status), str(conflicts), str(report), str(policy_log),
        EXPECTED_GOOD_SHA, test_sha,
    )

    # The generic merge policy can preserve vendor deletion semantics for a
    # newly added upstream source even when a later Makefile now requires it.
    # Restore this exact v4.19.250 source from the already-verified THEIRS tree;
    # this mirrors the historical 4.19.250 link-closure behavior.
    chacha20_src = THEIRS_TREE / "lib/chacha20.c"
    chacha20_dst = KERNEL / "lib/chacha20.c"
    if not chacha20_src.is_file():
        raise SystemExit("verified v4.19.250 THEIRS tree is missing lib/chacha20.c")
    if not chacha20_dst.is_file():
        shutil.copy2(chacha20_src, chacha20_dst)
        (ARTIFACTS / "phase100-upstream-closure.txt").write_text(
            "lib/chacha20.c\n"
        )
    elif chacha20_dst.read_bytes() != chacha20_src.read_bytes():
        raise SystemExit("existing lib/chacha20.c differs from exact v4.19.250 source")

    # Normalize the two known v4.19.250 synclink_gt whitespace defects.
    # This mirrors checkpoint_merge_linux_4.19.250.sh exactly so that the
    # compatibility repair script can use git diff --check as an invariant.
    synclink = KERNEL / "drivers/tty/synclink_gt.c"
    text = synclink.read_text()
    normalizations = (
        ("\t \tset_gtsignals(info);", "\t\tset_gtsignals(info);", "set_gtsignals"),
        (" \tget_gtsignals(info);", "\tget_gtsignals(info);", "get_gtsignals"),
    )
    rows = []
    for old, new, label in normalizations:
        count = text.count(old)
        if count > 1:
            raise SystemExit(f"synclink {label}: expected at most one whitespace defect, found {count}")
        if count == 1:
            text = text.replace(old, new, 1)
            rows.append(f"{label}=normalized\\n")
    synclink.write_text(text)
    (ARTIFACTS / "phase100-synclink-whitespace.txt").write_text("".join(rows))

    candidate_version = kernel_version()
    if candidate_version != "4.19.250":
        raise SystemExit(f"merged tree reports {candidate_version}, expected 4.19.250")
    for helper in FIX_TEMPLATES:
        run("bash", str(helper))

    repair_merge_shapes()
    run("git", "-C", str(KERNEL), "diff", "--check")

    state = (
        "experiment=phase100-current-stack-419250-full\n"
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
    (ARTIFACTS / "phase100-full250-state.txt").write_text(state)
    print(state, end="")

    shutil.rmtree(STABLE, ignore_errors=True)
    shutil.rmtree(BASE_TREE, ignore_errors=True)
    shutil.rmtree(THEIRS_TREE, ignore_errors=True)


if __name__ == "__main__":
    main()
