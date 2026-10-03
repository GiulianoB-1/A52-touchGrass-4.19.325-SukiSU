#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 /path/to/cvd-host_package.tar.gz /path/to/aosp_cf_x86_64_phone-img-BUILD.zip"
  exit 2
fi

HOST_PACKAGE="$(readlink -f "$1")"
IMAGE_ZIP="$(readlink -f "$2")"
LAB_ROOT="${CF_LAB_ROOT:-$HOME/cuttlefish-sf-q1}"
INSTANCE="$LAB_ROOT/instance"

test -s "$HOST_PACKAGE"
test -s "$IMAGE_ZIP"

rm -rf "$INSTANCE"
mkdir -p "$INSTANCE"

tar -xvf "$HOST_PACKAGE" -C "$INSTANCE"
unzip -o "$IMAGE_ZIP" -d "$INSTANCE"

test -x "$INSTANCE/bin/launch_cvd"
test -x "$INSTANCE/bin/adb"

cat > "$LAB_ROOT/BUILD-INFO.txt" <<EOF
Prepared: $(date -Iseconds)
Host package: $HOST_PACKAGE
Image zip: $IMAGE_ZIP
Target: aosp_cf_x86_64_phone-userdebug
Android branch intended: android16-release
EOF

echo
echo "Prepared Cuttlefish instance at: $INSTANCE"
echo "Next: $(cd "$(dirname "$0")" && pwd)/launch.sh"
