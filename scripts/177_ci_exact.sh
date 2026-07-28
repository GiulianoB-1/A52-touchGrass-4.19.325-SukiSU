#!/usr/bin/env bash
set -Eeuo pipefail

PAYLOAD="$PWD/scripts/177_patch_payload.b64"
PATCH="$PWD/patches/177-a52-display-init-recorder-plain.patch"
EXPECTED_PATCH_SHA256="04d57ae9bca9bdb8119ab2a1d6632690cb520e79454f339423427af887d292cb"

mkdir -p "$(dirname "$PATCH")"
base64 --decode "$PAYLOAD" | gzip -dc > "$PATCH"
printf '%s  %s\n' "$EXPECTED_PATCH_SHA256" "$PATCH" | sha256sum -c -
test "$(wc -l < "$PATCH")" = 816

python3 - <<'PY'
from pathlib import Path
path = Path('scripts/177_ci.sh')
text = path.read_text()
old = 'git -C gki/common apply --check "$PATCH"\n'
new = (
    'git -C gki/common apply --check --verbose "$PATCH" 2>&1 | '
    'tee "$OUT/logs/trace-patch-check.log"\n'
)
if text.count(old) != 1:
    raise SystemExit(f'expected one trace patch check, found {text.count(old)}')
path.write_text(text.replace(old, new, 1))
PY

exec bash scripts/177_ci.sh
