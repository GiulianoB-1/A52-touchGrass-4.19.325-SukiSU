#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_VERSION="${1:-}"
case "$TARGET_VERSION" in
  4.19.*) ;;
  *) printf 'ERROR: expected a Linux 4.19.x target version\n' >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$SCRIPT_DIR/08_build_linux_4.19.153_resukisu_safe.sh"
GENERATED="$SCRIPT_DIR/.generated-resukisu-safe-$TARGET_VERSION.sh"

test -f "$TEMPLATE" || { printf 'ERROR: safe integration template is missing: %s\n' "$TEMPLATE" >&2; exit 1; }

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

python3 - "$TEMPLATE" "$GENERATED" "$TARGET_VERSION" <<'PY'
from pathlib import Path
import sys

template = Path(sys.argv[1])
out = Path(sys.argv[2])
target = sys.argv[3]
text = template.read_text()
needle = "4.19.153"
count = text.count(needle)
if count < 8:
    raise SystemExit(f"template version marker count is unexpectedly low: {count}")
text = text.replace(needle, target)

# Linux 4.19.325 contains two upstream whitespace diagnostics in files that are
# not part of the A52 or ReSukiSU integration. Preserve them in the artifacts,
# but do not block the direct non-flashable compile checkpoint. Earlier targets
# retain the strict diff check.
if target == "4.19.325":
    strict = 'git -C "$KERNEL_DIR" diff --check\n'
    recorded = (
        'git -C "$KERNEL_DIR" diff --check > '
        '"$ARTIFACTS_DIR/linux-$TARGET_VERSION-diff-check.txt" 2>&1 || true\n'
    )
    if text.count(strict) != 1:
        raise SystemExit("kernel diff-check anchor mismatch")
    text = text.replace(strict, recorded, 1)

out.write_text(text)
out.chmod(0o755)
PY

bash "$GENERATED"
