#!/usr/bin/env bash
set -Eeuo pipefail

KERNEL="${1:-workspace/touchgrass-a52xq}"
OUT="${2:-artifacts/f2fs-atgc-p3b}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
cd "$KERNEL"

COMMITS=(
  093749e296e29a4b0162eb925a6701a01e8c9a98
  61461fc921b756ae16e64243f72af2bfc2e620db
  89e53ff1651a61cf2abef9356e2f60d0086215be
  8939a8489ca64b56f49428b0d882709080a928d4
  7d19e3dab0002e527052b0aaf986e8c32e5537bf
  80dc113aaa47c0d1dfd01f708d4d0c083022121b
)

names=(
  "ATGC original"
  "ATGC checkpointed-data correctness"
  "ATGC default age threshold"
  "ATGC sysfs tunables"
  "ATGC gc_idle correctness"
  "ATGC reject LFS mode"
)

find fs/f2fs include/trace/events -name '*.rej' -type f -delete 2>/dev/null || true
: > "$OUT/report.txt"
failed=0

for i in "${!COMMITS[@]}"; do
  sha="${COMMITS[$i]}"
  label="${names[$i]}"
  patchfile="$OUT/$sha.patch"
  log="$OUT/$sha.apply.log"
  rejdir="$OUT/rejects-$sha"

  echo "=== $sha :: $label ===" | tee -a "$OUT/report.txt"
  curl -LfsS "https://github.com/torvalds/linux/commit/${sha}.patch" -o "$patchfile"

  set +e
  patch -p1 --forward --batch --fuzz=0 < "$patchfile" > "$log" 2>&1
  rc=$?
  set -e
  cat "$log"

  mkdir -p "$rejdir"
  while IFS= read -r rej; do
    rel="${rej#./}"
    mkdir -p "$rejdir/$(dirname "$rel")"
    cp "$rej" "$rejdir/$rel"
  done < <(find fs/f2fs include/trace/events -name '*.rej' -type f -print 2>/dev/null || true)

  if [ "$rc" -ne 0 ]; then
    failed=1
    echo "PARTIAL rc=$rc" | tee -a "$OUT/report.txt"
  else
    echo "APPLIED" | tee -a "$OUT/report.txt"
  fi

  find fs/f2fs include/trace/events -name '*.rej' -type f -delete 2>/dev/null || true
done

find "$OUT" -path '*/rejects-*/*.rej' -type f -print | sort > "$OUT/rejects.txt" || true
if [ -s "$OUT/rejects.txt" ]; then
  while IFS= read -r rej; do
    echo "===== $rej =====" >> "$OUT/rejects-full.txt"
    cat "$rej" >> "$OUT/rejects-full.txt"
  done < "$OUT/rejects.txt"
fi

git diff --stat -- fs/f2fs include/trace/events/f2fs.h | tee "$OUT/diffstat.txt"
git diff --check > "$OUT/diff-check.txt" 2>&1 || true

if [ "$failed" -ne 0 ]; then
  echo "P3B strict ATGC staging has Samsung-specific rejects." | tee -a "$OUT/report.txt"
  exit 2
fi

grep -Fq 'F2FS_MOUNT_ATGC' fs/f2fs/f2fs.h
grep -Fq 'CURSEG_ALL_DATA_ATGC' fs/f2fs/f2fs.h
grep -Fq 'GC_IDLE_AT' fs/f2fs/f2fs.h
grep -Fq 'GC_AT' fs/f2fs/segment.h
grep -Fq 'AT_SSR' fs/f2fs/segment.h
grep -Fq 'atgc_age_threshold' fs/f2fs/sysfs.c
grep -Fq '"atgc"' fs/f2fs/super.c
git diff --check

echo "P3B strict ATGC source application complete" | tee -a "$OUT/report.txt"
