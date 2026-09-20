#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/f2fs-block-age-p5b").resolve()
out.mkdir(parents=True, exist_ok=True)

root = kernel / "fs" / "f2fs"
superp = root / "super.c"
sysfsp = root / "sysfs.c"

# P5B: activate the P5A block-age extent cache during the initial mount.
# This mirrors P3C's boot-time ATGC activation and preserves the upstream rule
# that age_extent_cache cannot be toggled dynamically by remount.

s = superp.read_text()
old = """		set_opt(sbi, ATGC);
	}
"""
new = """		set_opt(sbi, ATGC);
		/*
		 * P5B: activate block-age extent cache at initial mount only.
		 * P5A's remount guard remains authoritative, so no live
		 * enable/disable transition is introduced here.
		 */
		set_opt(sbi, AGE_EXTENT_CACHE);
	}
"""
if old not in s:
    raise RuntimeError("super.c: P3C initial-mount activation anchor changed")
s = s.replace(old, new, 1)
superp.write_text(s)

# Read-only observability for runtime validation. These do not affect policy.
s = sysfsp.read_text()
if "age_extent_cache_enabled_show" not in s:
    anchor = """#define F2FS_GENERAL_RO_ATTR(name) \\
static struct f2fs_attr f2fs_attr_##name = __ATTR(name, 0444, name##_show, NULL)
"""
    if anchor not in s:
        raise RuntimeError("sysfs.c: general RO attribute macro anchor changed")

    funcs = r'''
static ssize_t age_extent_cache_enabled_show(struct f2fs_attr *a,
		struct f2fs_sb_info *sbi, char *buf)
{
	return sprintf(buf, "%u\n", !!test_opt(sbi, AGE_EXTENT_CACHE));
}

static ssize_t age_extent_tree_count_show(struct f2fs_attr *a,
		struct f2fs_sb_info *sbi, char *buf)
{
	return sprintf(buf, "%d\n", atomic_read(&sbi->total_age_ext_tree));
}

static ssize_t age_extent_node_count_show(struct f2fs_attr *a,
		struct f2fs_sb_info *sbi, char *buf)
{
	return sprintf(buf, "%d\n", atomic_read(&sbi->total_age_ext_node));
}

static ssize_t allocated_data_blocks_show(struct f2fs_attr *a,
		struct f2fs_sb_info *sbi, char *buf)
{
	return sprintf(buf, "%lld\n",
		       (long long)atomic64_read(&sbi->allocated_data_blocks));
}

'''
    s = s.replace(anchor, funcs + anchor, 1)

if "F2FS_GENERAL_RO_ATTR(age_extent_cache_enabled);" not in s:
    anchor = "F2FS_GENERAL_RO_ATTR(mounted_time_sec);\n"
    if anchor not in s:
        raise RuntimeError("sysfs.c: mounted_time_sec declaration anchor changed")
    s = s.replace(anchor, anchor +
        "F2FS_GENERAL_RO_ATTR(age_extent_cache_enabled);\n"
        "F2FS_GENERAL_RO_ATTR(age_extent_tree_count);\n"
        "F2FS_GENERAL_RO_ATTR(age_extent_node_count);\n"
        "F2FS_GENERAL_RO_ATTR(allocated_data_blocks);\n", 1)

if "ATTR_LIST(age_extent_cache_enabled)," not in s:
    anchor = "\tATTR_LIST(mounted_time_sec),\n"
    if anchor not in s:
        raise RuntimeError("sysfs.c: mounted_time_sec attr-list anchor changed")
    s = s.replace(anchor, anchor +
        "\tATTR_LIST(age_extent_cache_enabled),\n"
        "\tATTR_LIST(age_extent_tree_count),\n"
        "\tATTR_LIST(age_extent_node_count),\n"
        "\tATTR_LIST(allocated_data_blocks),\n", 1)

sysfsp.write_text(s)

subprocess.run(["git", "diff", "--check"], cwd=kernel, check=True)

superc = superp.read_text()
for token in [
    "P5B: activate block-age extent cache at initial mount only",
    "set_opt(sbi, AGE_EXTENT_CACHE);",
    "switch age_extent_cache option is not allowed",
]:
    if token not in superc:
        raise RuntimeError(f"P5B super.c invariant missing: {token}")

sysfsc = sysfsp.read_text()
for token in [
    "age_extent_cache_enabled_show",
    "age_extent_tree_count_show",
    "age_extent_node_count_show",
    "allocated_data_blocks_show",
    "F2FS_GENERAL_RO_ATTR(age_extent_cache_enabled);",
    "ATTR_LIST(age_extent_cache_enabled),",
]:
    if token not in sysfsc:
        raise RuntimeError(f"P5B sysfs invariant missing: {token}")

# P5A implementation must still be present.
for rel, token in [
    ("extent_cache.c", "f2fs_update_age_extent_cache"),
    ("segment.c", "__get_age_segment_type"),
    ("segment.c", "atomic64_inc_return(&sbi->allocated_data_blocks)"),
    ("f2fs.h", "F2FS_MOUNT_AGE_EXTENT_CACHE"),
]:
    if token not in (root / rel).read_text():
        raise RuntimeError(f"P5A prerequisite missing: {rel}: {token}")

(out / "report.txt").write_text(
    "F2FS P5B block-age activation\n"
    "base=P5A boot-validated dormant\n"
    "age_extent_cache default=on at initial mount\n"
    "dynamic remount switching=unchanged/prohibited\n"
    "observability=enabled,tree_count,node_count,allocated_data_blocks\n"
)
with (out / "block-age-p5b.diff").open("wb") as f:
    subprocess.run(["git", "diff", "--", "fs/f2fs/super.c", "fs/f2fs/sysfs.c"],
                   cwd=kernel, stdout=f, check=True)

print("P5B block-age initial-mount activation applied")
