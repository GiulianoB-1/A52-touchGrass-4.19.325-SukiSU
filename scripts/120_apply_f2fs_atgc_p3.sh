#!/usr/bin/env bash
set -Eeuo pipefail

KERNEL="${1:-workspace/touchgrass-a52xq}"
OUT="${2:-artifacts/f2fs-atgc-p3}"
mkdir -p "$OUT"

COMMITS=(
  093749e296e29a4b0162eb925a6701a01e8c9a98
  61461fc921b756ae16e64243f72af2bfc2e620db
  89e53ff1651a61cf2abef9356e2f60d0086215be
  7d19e3dab0002e527052b0aaf986e8c32e5537bf
  8939a8489ca64b56f49428b0d882709080a928d4
)

names=(
  "ATGC original"
  "ATGC checkpointed-data fix"
  "ATGC default age threshold"
  "ATGC gc_idle enable fix"
  "ATGC sysfs tunables"
)

cd "$KERNEL"
rm -f fs/f2fs/*.rej include/trace/events/*.rej 2>/dev/null || true

printf 'A52 F2FS P3 ATGC source set\n' | tee "$OLDPWD/$OUT/report.txt"
printf 'base=%s\n' "$(git rev-parse HEAD)" | tee -a "$OLDPWD/$OUT/report.txt"

failed=0
for i in "${!COMMITS[@]}"; do
  sha="${COMMITS[$i]}"
  label="${names[$i]}"
  patchfile="$OLDPWD/$OUT/$sha.patch"

  echo "=== $sha :: $label ===" | tee -a "$OLDPWD/$OUT/report.txt"
  curl -LfsS "https://github.com/torvalds/linux/commit/${sha}.patch" -o "$patchfile"

  # Vendor F2FS is close to the 5.10-era source but contains Samsung deltas.
  # Apply matching hunks with conservative fuzz and retain .rej files for
  # explicit adaptation rather than silently dropping incompatible pieces.
  set +e
  patch -p1 --forward --batch --fuzz=3 < "$patchfile"     >"$OLDPWD/$OUT/$sha.apply.log" 2>&1
  rc=$?
  set -e
  cat "$OLDPWD/$OUT/$sha.apply.log"

  if [ "$rc" -ne 0 ]; then
    echo "PARTIAL rc=$rc" | tee -a "$OLDPWD/$OUT/report.txt"
    failed=1
  else
    echo "APPLIED" | tee -a "$OLDPWD/$OUT/report.txt"
  fi
done

echo "=== resulting ATGC-related diffstat ===" | tee -a "$OLDPWD/$OUT/report.txt"
git diff --stat -- fs/f2fs include/trace/events/f2fs.h | tee -a "$OLDPWD/$OUT/report.txt"

find fs/f2fs include/trace/events -name '*.rej' -type f -print   | tee "$OLDPWD/$OUT/rejects.txt" || true

if [ -s "$OLDPWD/$OUT/rejects.txt" ]; then
  echo "ATGC P3 has rejected hunks requiring Samsung-tree adaptation."     | tee -a "$OLDPWD/$OUT/report.txt"
  while IFS= read -r rej; do
    echo "===== $rej =====" | tee -a "$OLDPWD/$OUT/rejects-full.txt"
    cat "$rej" | tee -a "$OLDPWD/$OUT/rejects-full.txt"
  done < "$OLDPWD/$OUT/rejects.txt"
  exit 2
fi

if [ "$failed" -ne 0 ]; then
  echo "ATGC P3 patch command reported a failure without .rej output."     | tee -a "$OLDPWD/$OUT/report.txt"
  exit 3
fi

git diff --check

grep -Fq 'F2FS_MOUNT_ATGC' fs/f2fs/f2fs.h
grep -Fq 'CURSEG_ALL_DATA_ATGC' fs/f2fs/f2fs.h
grep -Fq 'GC_IDLE_AT' fs/f2fs/f2fs.h
grep -Fq 'GC_AT' fs/f2fs/segment.h
grep -Fq 'AT_SSR' fs/f2fs/segment.h
grep -Fq 'atgc_age_threshold' fs/f2fs/sysfs.c
grep -Fq '"atgc"' fs/f2fs/super.c

echo "ATGC P3 source application complete" | tee -a "$OLDPWD/$OUT/report.txt"
