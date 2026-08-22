#!/system/bin/sh
MODDIR=${MODDIR:-${0%/*}/..}
export MODDIR
. "$MODDIR/bin/common.sh"

target=${1:-all}
mkdir -p "$OUTROOT"

package_one() {
  session=$1
  [ -d "$session" ] || return 0
  [ -f "$session/.packaged" ] && return 0

  base=$(basename "$session")
  archive="TouchGrass-Parity-Probe-v1.1-$base.tar.gz"
  dest=/data/local/tmp
  [ -d /sdcard/Download ] && [ -w /sdcard/Download ] && dest=/sdcard/Download

  {
    echo "profile=touchgrass-parity-probe-v1.1"
    echo "session=$base"
    echo "raw_bytes=$(du -sk "$session" 2>/dev/null | awk '{print $1 * 1024}')"
  } >"$session/PACKAGE-PRE.txt"

  (
    cd "$OUTROOT" || exit 1
    tar -czf "$dest/$archive" "$base"
  ) || return 1

  sha256sum "$dest/$archive" >"$dest/$archive.sha256" 2>/dev/null || true
  {
    echo "archive=$dest/$archive"
    echo "bytes=$(wc -c <"$dest/$archive" 2>/dev/null)"
    echo "sha256=$(sha256sum "$dest/$archive" 2>/dev/null | awk '{print $1}')"
  } >"$session/PACKAGE.txt"
  touch "$session/.packaged"
  echo "$dest/$archive"
}

if [ "$target" = "all" ]; then
  for d in "$OUTROOT"/20??????-??????-*; do
    [ -d "$d" ] && package_one "$d"
  done
else
  package_one "$target"
fi
exit 0
