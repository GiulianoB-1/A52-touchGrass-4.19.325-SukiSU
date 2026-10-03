#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${CF_HOST_ROOT:-$HOME/cuttlefish-host}"
SRC="$ROOT/android-cuttlefish"

sudo apt-get update
sudo apt-get install -y   git devscripts equivs config-package-dev debhelper-compat golang curl   unzip zip ca-certificates

mkdir -p "$ROOT"
if [[ ! -d "$SRC/.git" ]]; then
  git clone https://github.com/google/android-cuttlefish "$SRC"
else
  git -C "$SRC" fetch --all --prune
fi

cd "$SRC"
tools/buildutils/build_packages.sh

sudo dpkg -i ./cuttlefish-base_*_*64.deb || sudo apt-get install -f -y
sudo dpkg -i ./cuttlefish-user_*_*64.deb || sudo apt-get install -f -y

sudo usermod -aG kvm,cvdnetwork,render "$USER"

echo
echo "Cuttlefish host packages installed."
echo "IMPORTANT: reboot this Ubuntu host before launching Cuttlefish so groups/udev/module changes apply."
echo "After reboot run: $(cd "$(dirname "$0")" && pwd)/check_host.sh"
