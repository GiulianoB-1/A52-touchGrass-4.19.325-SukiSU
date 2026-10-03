#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${CF_HOST_INSTALL_MODE:-packages}"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "FAIL: this installer currently expects an Ubuntu/Debian host."
  exit 1
fi

if [[ "$MODE" == "packages" ]]; then
  echo "Installing official prebuilt Cuttlefish host packages..."

  sudo apt-get update
  sudo apt-get install -y curl ca-certificates gnupg libpulse0

  sudo curl -fsSL \
    https://us-apt.pkg.dev/doc/repo-signing-key.gpg \
    -o /etc/apt/trusted.gpg.d/artifact-registry.asc
  sudo chmod a+r /etc/apt/trusted.gpg.d/artifact-registry.asc

  echo "deb https://us-apt.pkg.dev/projects/android-cuttlefish-artifacts android-cuttlefish main" | \
    sudo tee /etc/apt/sources.list.d/artifact-registry.list >/dev/null

  sudo apt-get update
  sudo apt-get install -y cuttlefish-base cuttlefish-user
else
  echo "Building Cuttlefish host packages from source (explicit fallback mode)."
  ROOT="${CF_HOST_ROOT:-$HOME/cuttlefish-host}"
  SRC="$ROOT/android-cuttlefish"

  sudo apt-get update
  sudo apt-get install -y \
    git devscripts equivs config-package-dev debhelper-compat golang curl \
    unzip zip ca-certificates

  mkdir -p "$ROOT"
  if [[ ! -d "$SRC/.git" ]]; then
    git clone https://github.com/google/android-cuttlefish "$SRC"
  else
    git -C "$SRC" fetch --all --prune
    git -C "$SRC" pull --ff-only
  fi

  cd "$SRC"
  tools/buildutils/build_packages.sh

  sudo apt-get install -y ./cuttlefish-base_*_*64.deb ./cuttlefish-user_*_*64.deb
fi

for g in kvm cvdnetwork render; do
  if getent group "$g" >/dev/null 2>&1; then
    sudo usermod -aG "$g" "$USER"
  else
    echo "NOTE: group '$g' does not exist on this host; skipping."
  fi
done

echo
echo "Cuttlefish host installation finished."
echo "Installed packages:"
dpkg-query -W -f='  ${binary:Package} ${Version}\n' cuttlefish-base cuttlefish-user 2>/dev/null || true
echo
echo "IMPORTANT: run 'wsl --shutdown' from Windows, reopen Ubuntu, then run ./check_host.sh"
