#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/102-a52-fuse-negotiation-probe-btp2c-clang11.yml"
DIAG = ROOT / "scripts/05a_diagnose_linux_checkpoint.sh"


def replace_exact(text, old, new, expected, label):
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} matches, found {count}")
    return text.replace(old, new)


# ---------------------------------------------------------------------------
# 1. Fix the intermittent Git shallow-file race in stable checkpoint fetches.
# Fetch both tags in ONE operation, at ONE depth, with maintenance disabled.
# ---------------------------------------------------------------------------
diag = DIAG.read_text()
old_fetch = '''git init -q "$STABLE_DIR"
git -C "$STABLE_DIR" remote add origin "$LINUX_STABLE_REPO"
git -C "$STABLE_DIR" fetch --quiet --depth=1000 origin "refs/tags/$TO_TAG:refs/tags/$TO_TAG"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$FROM_TAG:refs/tags/$FROM_TAG"
'''
new_fetch = '''git init -q "$STABLE_DIR"
git -C "$STABLE_DIR" remote add origin "$LINUX_STABLE_REPO"
git -C "$STABLE_DIR" config gc.auto 0
git -C "$STABLE_DIR" config maintenance.auto false
# A single shallow fetch avoids concurrent/overlapping .git/shallow rewrites
# seen on newer GitHub runners (fatal: shallow file has changed since we read it).
git -C "$STABLE_DIR" fetch --quiet --depth=1000 origin \\
  "refs/tags/$FROM_TAG:refs/tags/$FROM_TAG" \\
  "refs/tags/$TO_TAG:refs/tags/$TO_TAG"
'''
diag = replace_exact(diag, old_fetch, new_fetch, 1, "stable single-fetch conversion")
DIAG.write_text(diag)

# ---------------------------------------------------------------------------
# 2. Add a prepared Phase103 source cache.  This is deliberately before P104,
# so P104 can change repeatedly without invalidating the expensive baseline.
# ---------------------------------------------------------------------------
wf = WF.read_text()

# Bump compiler cache generation because the old v1 key was saved by a failed
# pre-build run and can no longer be updated by actions/cache.
wf = replace_exact(
    wf,
    "a52xq-fuse-740-passthrough-btp2c-${{ runner.os }}-v1",
    "a52xq-fuse-740-passthrough-btp2c-${{ runner.os }}-v2",
    2,
    "ccache generation bump",
)

# Keep the restore-key count within GitHub's 10-key maximum while allowing v1
# to seed the new v2 cache if it still exists.
old_restore = '''          restore-keys: |
            a52xq-fuse-neg-probe-btp2c-${{ runner.os }}-
'''
new_restore = '''          restore-keys: |
            a52xq-fuse-740-passthrough-btp2c-${{ runner.os }}-
            a52xq-fuse-neg-probe-btp2c-${{ runner.os }}-
'''
wf = replace_exact(wf, old_restore, new_restore, 1, "ccache self-prefix restore")
wf = replace_exact(
    wf,
    "            a52xq-bbr3-ccache-${{ runner.os }}-\n",
    "",
    1,
    "drop oldest ccache restore key",
)

bt_anchor = "      - name: Restore BT-P2C prepared source cache\n"
phase103_restore = '''      - name: Restore FUSE 7.40 Phase103 prepared source cache
        id: fuse103_source
        uses: actions/cache/restore@v4
        with:
          path: workspace/touchgrass-a52xq
          key: a52xq-fuse740-phase103-${{ runner.os }}-runtime-proven-v1

'''
wf = replace_exact(wf, bt_anchor, phase103_restore + bt_anchor, 1, "insert Phase103 restore")

# Everything from the old BT source fallback through Phase103 must be skipped
# when the final Phase103 snapshot was restored.
start = wf.index(bt_anchor)
end_anchor = "      - name: Apply native FUSE 7.40 upstream passthrough\n"
end = wf.index(end_anchor)
segment = wf[start:end]
prefix = "steps.fuse103_source.outputs.cache-hit != 'true' && "
lines = segment.splitlines(True)
for i, line in enumerate(lines):
    if line.startswith("        if: ") and prefix not in line:
        lines[i] = line.replace("        if: ", "        if: " + prefix, 1)
segment = "".join(lines)

# These steps originally had no condition at all.
for name in (
    "Restore BT-P2C prepared source cache",
    "Apply runtime-proven FUSE Modern P1",
    "Apply runtime-proven FUSE Modern P2",
    "Apply FUSE Modern P3 stack-depth semantics",
    "Apply FUSE INIT negotiation probe",
    "Apply FUSE 7.40 INIT compatibility phase",
):
    marker = f"      - name: {name}\n"
    if marker not in segment:
        raise SystemExit(f"missing unconditional step marker: {name}")
    segment = segment.replace(
        marker,
        marker + "        if: steps.fuse103_source.outputs.cache-hit != 'true'\n",
        1,
    )

wf = wf[:start] + segment + wf[end:]

phase103_save = '''      - name: Save FUSE 7.40 Phase103 prepared source cache
        if: steps.fuse103_source.outputs.cache-hit != 'true'
        uses: actions/cache/save@v4
        with:
          path: workspace/touchgrass-a52xq
          key: a52xq-fuse740-phase103-${{ runner.os }}-runtime-proven-v1

'''
wf = replace_exact(wf, end_anchor, phase103_save + end_anchor, 1, "insert Phase103 save")

# Structural checks.
required = [
    "id: fuse103_source",
    "a52xq-fuse740-phase103-${{ runner.os }}-runtime-proven-v1",
    "Save FUSE 7.40 Phase103 prepared source cache",
    "a52xq-fuse-740-passthrough-btp2c-${{ runner.os }}-v2",
    "if: steps.fuse103_source.outputs.cache-hit != 'true'",
]
for needle in required:
    if needle not in wf:
        raise SystemExit(f"workflow postcondition missing: {needle}")

# Primary cache key + restore keys must remain <= 10.
restore_block = wf.split("      - name: Restore compiler cache\n", 1)[1].split("      - name: Configure compiler cache\n", 1)[0]
restore_key_count = 1 + restore_block.count("            a52xq-")
if restore_key_count > 10:
    raise SystemExit(f"compiler cache key count is {restore_key_count}, maximum is 10")

WF.write_text(wf)
print("patched=stable-single-fetch")
print("patched=compiler-cache-v2")
print("patched=phase103-prepared-source-cache")
print(f"compiler_cache_key_count={restore_key_count}")
