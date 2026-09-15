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

    def function_end(source: str, start: int) -> int:
        brace = source.find("{", start)
        if brace < 0:
            raise SystemExit("schedutil helper opening brace missing")
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
        raise SystemExit("schedutil helper closing brace missing")

    # Linux 4.19.250 made the tunables kobject own the final free. The Samsung
    # tree still carries its older explicit struct-pointer free helper for its
    # tunables cache. Keeping both creates conflicting C definitions and, if
    # merely renamed, would double-free after gov_attr_set_put().
    kobj_free_sig = "static void sugov_tunables_free(struct kobject *kobj)"
    vendor_free_sig = "static void sugov_tunables_free(struct sugov_tunables *tunables)"
    if text.count(kobj_free_sig) != 1:
        raise SystemExit(
            f"schedutil kobject tunables free count is {text.count(kobj_free_sig)}, expected 1"
        )
    if text.count(vendor_free_sig) == 1:
        vendor_start = text.index(vendor_free_sig)
        text = text[:vendor_start] + text[function_end(text, vendor_start):]
    elif text.count(vendor_free_sig) != 0:
        raise SystemExit("schedutil vendor tunables free helper count is not 0 or 1")

    release_line = "\t.release = &sugov_tunables_free,\n"
    if text.count(release_line) != 1:
        raise SystemExit("schedutil kobject release callback is missing or duplicated")

    clear_def = "static void sugov_clear_global_tunables(void)"
    clear_body = """static void sugov_clear_global_tunables(void)
{
\tif (!have_governor_per_policy())
\t\tglobal_tunables = NULL;
}

"""
    # Remove any previously inserted copies, then place exactly one before
    # sugov_init so both its fail path and sugov_exit see a declaration.
    while clear_def in text:
        pos = text.index(clear_def)
        existing = text[pos:function_end(text, pos)]
        if existing.strip() != clear_body.strip():
            raise SystemExit("schedutil clear-global helper has an unknown body")
        text = text[:pos] + text[function_end(text, pos):]
    init_anchor = "static int sugov_init(struct cpufreq_policy *policy)"
    if text.count(init_anchor) != 1:
        raise SystemExit("schedutil init anchor is not unique")
    text = text.replace(init_anchor, clear_body + init_anchor, 1)

    vendor_fail = """fail:
\tkobject_put(&tunables->attr_set.kobj);
\tpolicy->governor_data = NULL;
\tsugov_tunables_free(tunables);
"""
    target_fail = """fail:
\tkobject_put(&tunables->attr_set.kobj);
\tpolicy->governor_data = NULL;
\tsugov_clear_global_tunables();
"""
    if vendor_fail in text:
        if text.count(vendor_fail) != 1:
            raise SystemExit("schedutil vendor fail cleanup is not unique")
        text = text.replace(vendor_fail, target_fail, 1)
    elif text.count(target_fail) != 1:
        raise SystemExit("schedutil fail cleanup is neither vendor nor target-safe")

    # Normalize only the lifetime-critical section of sugov_exit().  Stable
    # 4.19.250 can merge comments/braces from its kobject-lifetime conversion
    # into Samsung's cached-tunables exit path, so matching the whole block is
    # too brittle.  Validate the function shape, then emit one reviewed core.
    exit_sig = "static void sugov_exit(struct cpufreq_policy *policy)"
    if text.count(exit_sig) != 1:
        raise SystemExit("schedutil exit function is not unique")
    exit_start = text.index(exit_sig)
    exit_end = function_end(text, exit_start)
    exit_body = text[exit_start:exit_end]

    required_exit_tokens = (
        "mutex_lock(&global_tunables_lock);",
        "gov_attr_set_put(&tunables->attr_set, &sg_policy->tunables_hook);",
        "policy->governor_data = NULL;",
        "mutex_unlock(&global_tunables_lock);",
    )
    for token in required_exit_tokens:
        if exit_body.count(token) != 1:
            raise SystemExit(
                f"schedutil exit token count for {token!r} is "
                f"{exit_body.count(token)}, expected 1"
            )
    if "sugov_tunables_save(policy, tunables);" not in exit_body:
        raise SystemExit("schedutil Samsung tunables cache save is missing")
    if not (
        "sugov_tunables_free(tunables);" in exit_body
        or "sugov_clear_global_tunables();" in exit_body
    ):
        raise SystemExit("schedutil exit has no recognized final cleanup")

    lock_line = "\tmutex_lock(&global_tunables_lock);\n"
    unlock_line = "\tmutex_unlock(&global_tunables_lock);\n"
    lock_pos = exit_body.index(lock_line)
    unlock_pos = exit_body.index(unlock_line, lock_pos)
    canonical_exit_core = """\tmutex_lock(&global_tunables_lock);

\t/*
\t * The 4.19.250 kobject release callback can free tunables from
\t * gov_attr_set_put(). Preserve Samsung's per-policy cached values only
\t * when this is the final user, and do so before dropping that reference.
\t */
\tif (tunables->attr_set.usage_count == 1)
\t\tsugov_tunables_save(policy, tunables);

\tcount = gov_attr_set_put(&tunables->attr_set, &sg_policy->tunables_hook);
\tpolicy->governor_data = NULL;
\tif (!count)
\t\tsugov_clear_global_tunables();

"""
    exit_body = (
        exit_body[:lock_pos]
        + canonical_exit_core
        + exit_body[unlock_pos:]
    )
    text = text[:exit_start] + exit_body + text[exit_end:]

    # Postconditions: no pointer-based free may remain in the exit path, the
    # cache save must precede the potentially freeing put, and cleanup is once.
    exit_end = function_end(text, exit_start)
    repaired_exit = text[exit_start:exit_end]
    if "sugov_tunables_free(tunables);" in repaired_exit:
        raise SystemExit("schedutil exit still uses pointer-based tunables free")
    if repaired_exit.count("sugov_tunables_save(policy, tunables);") != 1:
        raise SystemExit("schedutil exit cache-save count is not one")
    if repaired_exit.count("sugov_clear_global_tunables();") != 1:
        raise SystemExit("schedutil exit global-clear count is not one")
    if repaired_exit.count("tunables->attr_set.usage_count == 1") != 1:
        raise SystemExit("schedutil exit final-user guard count is not one")
    if repaired_exit.index("sugov_tunables_save(policy, tunables);") > repaired_exit.index(
        "gov_attr_set_put(&tunables->attr_set, &sg_policy->tunables_hook);"
    ):
        raise SystemExit("schedutil cache save still occurs after kobject put")

    path.write_text(text)
    final = path.read_text()
    if final.count(kobj_free_sig) != 1 or vendor_free_sig in final:
        raise SystemExit("schedutil tunables lifetime repair failed")
    if final.count(clear_def) != 1:
        raise SystemExit("schedutil clear-global helper count is not one")
    if final.index(clear_def) > final.index(init_anchor):
        raise SystemExit("schedutil clear-global helper is declared too late")
    final_exit_start = final.index(exit_sig)
    final_exit_end = function_end(final, final_exit_start)
    final_exit = final[final_exit_start:final_exit_end]
    if final_exit.count("tunables->attr_set.usage_count == 1") != 1:
        raise SystemExit("schedutil final exit guard count is not one")
    if final_exit.count("sugov_tunables_save(policy, tunables);") != 1:
        raise SystemExit("schedutil final exit cache-save count is not one")
    if final_exit.count("sugov_clear_global_tunables();") != 1:
        raise SystemExit("schedutil final exit cleanup count is not one")

    # The late compatibility helper repairs the procfs header/root path to the
    # stable three-argument proc_fill_super ABI. The generic merge can still
    # retain Samsung's one-argument inode.c implementation, producing a type
    # conflict. Upgrade only the function ABI and stable option validation while
    # retaining the Samsung inode body.
    proc_inode = KERNEL / "fs/proc/inode.c"
    proc_text = proc_inode.read_text()
    old_proc_prefix = """int proc_fill_super(struct super_block *s)
{
\tstruct inode *root_inode;
\tint ret;
"""
    new_proc_prefix = """int proc_fill_super(struct super_block *s, void *data, int silent)
{
\tstruct pid_namespace *ns = get_pid_ns(s->s_fs_info);
\tstruct inode *root_inode;
\tint ret;

\tif (!proc_parse_options(data, ns))
\t\treturn -EINVAL;
"""
    if old_proc_prefix in proc_text:
        if proc_text.count(old_proc_prefix) != 1:
            raise SystemExit("proc_fill_super old implementation is not unique")
        proc_text = proc_text.replace(old_proc_prefix, new_proc_prefix, 1)
    elif proc_text.count(new_proc_prefix) != 1:
        raise SystemExit("proc_fill_super implementation ABI is unrecognized")

    old_iflags = "\ts->s_iflags |= SB_I_USERNS_VISIBLE | SB_I_NODEV;\n"
    new_iflags = "\ts->s_iflags |= SB_I_USERNS_VISIBLE | SB_I_NOEXEC | SB_I_NODEV;\n"
    if old_iflags in proc_text:
        if proc_text.count(old_iflags) != 1:
            raise SystemExit("proc_fill_super iflags anchor is not unique")
        proc_text = proc_text.replace(old_iflags, new_iflags, 1)
    elif proc_text.count(new_iflags) != 1:
        raise SystemExit("proc_fill_super iflags shape is unrecognized")

    proc_inode.write_text(proc_text)
    proc_final = proc_inode.read_text()
    if proc_final.count("int proc_fill_super(struct super_block *s, void *data, int silent)") != 1:
        raise SystemExit("proc_fill_super stable ABI postcondition failed")
    if proc_final.count("if (!proc_parse_options(data, ns))") != 1:
        raise SystemExit("proc_fill_super option validation postcondition failed")

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
