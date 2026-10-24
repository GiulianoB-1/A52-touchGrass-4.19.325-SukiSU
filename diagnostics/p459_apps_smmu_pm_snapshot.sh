#!/system/bin/sh
# P459 read-only apps SMMU PM sampling, no sysfs writes.
# Usage: sh p459_apps_smmu_pm_snapshot.sh [snapshot|watch 120]
set -u
pm_attr() {
  if [ -r "$1/power/$2" ]; then cat "$1/power/$2" 2>/dev/null; else echo unavailable; fi
}
dev_sample() {
  [ -d "$1" ] || return
  printf 'P459 PM dev=%s status=%s usage=%s control=%s active_ms=%s suspended_ms=%s\n' \
    "${1##*/}" "$(pm_attr "$1" runtime_status)" "$(pm_attr "$1" runtime_usage)" \
    "$(pm_attr "$1" control)" "$(pm_attr "$1" runtime_active_time)" \
    "$(pm_attr "$1" runtime_suspended_time)"
}
sample() {
  echo "=== P459 SAMPLE uptime=$(cat /proc/uptime 2>/dev/null) ==="
  for d in /sys/bus/platform/devices/*apps-smmu* /sys/bus/platform/devices/*smmu-kgsl* \
           /sys/bus/platform/devices/*dwc3* /sys/bus/platform/devices/*qusb* \
           /sys/bus/platform/devices/*venus* /sys/bus/platform/devices/*npu* \
           /sys/bus/platform/devices/*lpass* /sys/bus/platform/devices/*mdss* \
           /sys/bus/platform/devices/*sde* /sys/bus/platform/devices/*ufs* \
           /sys/bus/platform/devices/*ipa* /sys/bus/platform/devices/*pcie* \
           /sys/bus/platform/devices/*cam*; do
    [ -d "$d" ] && dev_sample "$d"
  done
}
inventory() {
  echo '=== P459 DEVICE/MASTER INVENTORY (sysfs links are not proof of active votes) ==='
  uname -a
  echo "cmdline=$(cat /proc/cmdline 2>/dev/null)"
  for d in /sys/bus/platform/devices/*apps-smmu*; do
    [ -d "$d" ] || continue
    echo "apps-smmu=$d resolved=$(readlink -f "$d" 2>/dev/null)"
    for l in "$d"/supplier:* "$d"/consumer:* "$d"/iommu_group "$d"/driver; do
      [ -L "$l" ] && echo "link=$l points=$(readlink -f "$l" 2>/dev/null)"
    done
    for child in "$d"/of_node/*; do
      [ -r "$child/compatible" ] || continue
      compat=$(tr '\000' '\n' < "$child/compatible" 2>/dev/null)
      case "$compat" in
        *qcom,qsmmuv500-tbu*)
          printf 'TBU child=%s compatible=%s status=' "${child##*/}" "$compat"
          if [ -r "$child/status" ]; then tr '\000' '\n' < "$child/status"; else echo okay-default; fi
          ;;
      esac
    done
  done
  echo '=== IOMMU GROUP -> MASTER DEVICE MAP ==='
  for g in /sys/kernel/iommu_groups/*; do
    [ -d "$g" ] || continue
    printf 'group=%s ' "${g##*/}"
    [ -L "$g/iommu" ] && printf 'iommu=%s ' "$(readlink -f "$g/iommu")"
    printf 'devices='
    for d in "$g"/devices/*; do [ -L "$d" ] && printf '%s ' "${d##*/}"; done
    printf '\n'
  done
  echo '=== PLATFORM IOMMU CONSUMERS + POWER STATUS ==='
  for d in /sys/bus/platform/devices/*; do
    [ -L "$d/iommu_group" ] || continue
    echo "master=${d##*/} iommu_group=$(readlink -f "$d/iommu_group")"
    dev_sample "$d"
  done
  echo '=== KERNEL SUSPEND FAILURE MARKERS ==='
  dmesg 2>/dev/null | grep -E '15000000.apps-smmu|system suspend blocked until qsmmuv500|P446S KGSL_SMMU_RPM|PM: failed to suspend|suspend entry|suspend exit' | tail -150
}
case "${1:-snapshot}" in
  snapshot) inventory; sample;;
  watch)
    seconds="${2:-120}"
    case "$seconds" in ''|*[!0-9]*) echo invalid-seconds >&2; exit 2;; esac
    [ "$seconds" -le 240 ] || { echo watch-max-240 >&2; exit 2; }
    inventory
    i=0
    while [ "$i" -lt "$seconds" ]; do sample; i=$((i+1)); sleep 1; done
    ;;
  *) echo 'Usage: sh p459_apps_smmu_pm_snapshot.sh [snapshot|watch 120]' >&2; exit 2;;
esac
