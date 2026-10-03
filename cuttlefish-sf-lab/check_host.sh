#!/usr/bin/env bash
set -Eeuo pipefail

echo "=== Cuttlefish SF Lab: host preflight ==="
echo "host: $(hostname)"
echo "kernel: $(uname -srmo)"
echo "arch: $(uname -m)"
echo

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "FAIL: SF-Q1 currently expects an x86_64 host."
  exit 1
fi

virt_count="$(grep -Ecw 'vmx|svm' /proc/cpuinfo || true)"
echo "virtualization CPU flags: $virt_count"
if [[ "$virt_count" -eq 0 ]]; then
  echo "FAIL: CPU virtualization flags are not visible."
  exit 1
fi

if [[ -e /dev/kvm ]]; then
  echo "/dev/kvm: present"
  ls -l /dev/kvm
else
  echo "FAIL: /dev/kvm is missing."
  echo "Enable AMD-V/SVM or Intel VT-x in firmware and make sure KVM modules are loaded."
  exit 1
fi

echo
echo "groups: $(id -nG)"
if [[ -r /dev/kvm && -w /dev/kvm ]]; then
  echo "KVM access: PASS"
else
  echo "KVM access: NOT YET AVAILABLE to this user"
  echo "The install script adds the user to kvm/cvdnetwork/render; log out or reboot afterwards."
fi

echo
echo "render nodes:"
ls -l /dev/dri/renderD* 2>/dev/null || echo "  none"

echo
echo "memory:"
free -h

echo
echo "disk:"
df -h "$HOME"

echo
echo "CPU:"
lscpu | grep -E 'Model name|CPU\(s\)|Virtualization:' || true

echo
echo "Preflight complete."
