#!/usr/bin/env bash
set -Eeuo pipefail

# Keep the reviewed Phase319 reconstruction wrapper immutable and repair only
# the diagnostic verifier that was added in commit 2da61048. That verifier
# incorrectly rejected its own replacement because the replacement intentionally
# retains the original authoritative SHA-check lines.
BASE_REF=2da61048dc896f3ab7fe2427997a10e53b944a4b
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

: "${GITHUB_REPOSITORY:?}"
curl -fL --retry 5 --retry-all-errors --silent --show-error \
  "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/${BASE_REF}/scripts/319_regenerate_phase175_base.sh" \
  -o "$TMP"
test -s "$TMP"

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = '''if old in text or text.count('cp "$OUT" /tmp/p319gki-phase175-regenerated.patch') != 1:
    raise SystemExit("Phase175 mismatch diagnostic insertion failed")
'''
new = '''if text.count('cp "$OUT" /tmp/p319gki-phase175-regenerated.patch') != 1:
    raise SystemExit("Phase175 mismatch diagnostic patch-copy insertion failed")
if text.count("Phase319 regeneration: Phase175 patch identity expected=%s actual=%s") != 1:
    raise SystemExit("Phase175 mismatch diagnostic identity-print insertion failed")
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"Phase175 diagnostic postcheck repair anchor expected 1, found {count}")
text = text.replace(old, new, 1)
if old in text or text.count(new) != 1:
    raise SystemExit("Phase175 diagnostic postcheck repair failed")
path.write_text(text, encoding="utf-8")
print("Phase319 regeneration: Phase175 diagnostic postcheck repair PASS")
PY

bash "$TMP" "$@"
