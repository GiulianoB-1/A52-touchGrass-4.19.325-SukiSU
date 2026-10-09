#!/system/bin/sh
# P170: read-only CoreSight ETM diagnostic for use as root in Termux.
# Do not write to registers using devmem, ioremap, or kernel arguments.
echo "===== P170 A52 ETM4X READ-ONLY DIAGNOSTIC ====="
echo "kernel=$(uname -r)"
echo "date=$(date)"
echo "===== KERNEL CONFIG ====="
if [ -r /proc/config.gz ]; then
  gzip -dc /proc/config.gz | grep -E 'CONFIG_(CORESIGHT_SOURCE_ETM4X|CORESIGHT_LINK_AND_SINK_TMC|AUTOFDO_CLANG|THINLTO|ARM_PMU|PERF_EVENTS)'
else
  echo "/proc/config.gz unavailable"
fi

echo "===== CORESIGHT DEVICES ====="
ls -ld /sys/bus/coresight/devices/* 2>&1

echo "===== ETM REGISTER SNAPSHOTS ====="
found=0
for d in /sys/bus/coresight/devices/*; do
  [ -r "$d/mgmt/trcauthstatus" ] || continue
  found=1
  echo "----- $d -----"
  for reg in trcauthstatus trcoslsr trclsr trcpdsr trcpdcr trcdevid; do
    if [ -r "$d/mgmt/$reg" ]; then
      printf '%s=' "$reg"
      cat "$d/mgmt/$reg"
    else
      echo "$reg=unavailable"
    fi
  done
done
if [ "$found" -eq 0 ]; then
  echo "NO_ETM_MGMT_INTERFACES: check dmesg/probe logs, do not assume authorization denial"
fi

echo "===== CORESIGHT PERF SOURCES ====="
ls -ld /sys/bus/event_source/devices/cs_etm /sys/bus/event_source/devices/cs-etm 2>&1
echo "===== CORESIGHT KERNEL LOGS ====="
dmesg | grep -iE 'coresight|etm4|etm0|cs-etm|tmc|trace.*auth' | tail -70
echo "===== END ====="
