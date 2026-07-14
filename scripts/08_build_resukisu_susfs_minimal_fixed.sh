#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/08_build_resukisu_susfs_compat.sh"
FIXED="$SCRIPT_DIR/.generated-susfs-minimal-fixed-wrapper.sh"

cleanup() {
  rm -f "$FIXED"
}
trap cleanup EXIT

python3 - "$SOURCE" "$FIXED" <<'FIXWRAPPERPY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
out = Path(sys.argv[2])
text = source.read_text()

start = "python3 - \"$SOURCE\" \"$GENERATED\" <<'PY'\n"
end = "\nPY\n\nexec \"$GENERATED\" \"$@\"\n"

if text.count(start) != 1:
    raise SystemExit('outer generator heredoc start mismatch')
if text.count(end) != 1:
    raise SystemExit('outer generator heredoc end mismatch')

text = text.replace(start, "python3 - \"$SOURCE\" \"$GENERATED\" <<'RESUKISUSUSFSGENERATORPY'\n", 1)
text = text.replace(end, "\nRESUKISUSUSFSGENERATORPY\n\nexec \"$GENERATED\" \"$@\"\n", 1)
out.write_text(text)
out.chmod(0o755)
FIXWRAPPERPY

exec "$FIXED" "$@"
