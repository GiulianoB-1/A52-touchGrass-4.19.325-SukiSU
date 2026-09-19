#!/usr/bin/env bash
set -Eeuo pipefail

KERNEL="${1:-workspace/touchgrass-a52xq}"
OUT="${2:-artifacts/f2fs-atgc-p3}"
ROOT="$PWD"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

# Dependency order matches upstream history. Samsung already carries the
# aligned-pin-file work, but not the later in-memory curseg abstraction that
# ATGC was built on.
COMMITS=(
  d0b9e42ab6155dc05fc83f00af9f45d4dd02264d
  093749e296e29a4b0162eb925a6701a01e8c9a98
  61461fc921b756ae16e64243f72af2bfc2e620db
  632faca72938f9f63049e48a8c438913828ac7a9
  89e53ff1651a61cf2abef9356e2f60d0086215be
  8939a8489ca64b56f49428b0d882709080a928d4
  7d19e3dab0002e527052b0aaf986e8c32e5537bf
  80dc113aaa47c0d1dfd01f708d4d0c083022121b
)

names=(
  "prerequisite: introduce in-memory curseg"
  "ATGC original"
  "ATGC checkpointed-data fix"
  "ATGC unallocated section/zone fix"
  "ATGC default age threshold"
  "ATGC sysfs tunables"
  "ATGC gc_idle enable fix"
  "ATGC reject LFS mode"
)

cd "$KERNEL"
find fs/f2fs include/trace/events -name '*.rej' -type f -delete 2>/dev/null || true

printf 'A52 F2FS P3 ATGC strict dependency staging\n' | tee "$OUT/report.txt"
printf 'tree=%s\n' "$(git rev-parse HEAD)" | tee -a "$OUT/report.txt"

failed=0
for i in "${!COMMITS[@]}"; do
  sha="${COMMITS[$i]}"
  label="${names[$i]}"
  patchfile="$OUT/$sha.patch"
  log="$OUT/$sha.apply.log"
  rejdir="$OUT/rejects-$sha"

  echo "=== $sha :: $label ===" | tee -a "$OUT/report.txt"
  curl -LfsS "https://github.com/torvalds/linux/commit/${sha}.patch" -o "$patchfile"

  # This pass is intentionally strict. No fuzz is allowed because an earlier
  # diagnostic run proved that fuzzy patch placement can corrupt Samsung's
  # segment macros. Rejected hunks are adaptation inputs, not errors to hide.
  set +e
  patch -p1 --forward --batch --fuzz=0 < "$patchfile" >"$log" 2>&1
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
    echo "PARTIAL rc=$rc" | tee -a "$OUT/report.txt"
    failed=1
  else
    echo "APPLIED" | tee -a "$OUT/report.txt"
  fi

  find fs/f2fs include/trace/events -name '*.rej' -type f -delete 2>/dev/null || true
done

echo "=== resulting ATGC-related diffstat ===" | tee -a "$OUT/report.txt"
git diff --stat -- fs/f2fs include/trace/events/f2fs.h | tee -a "$OUT/report.txt"

find "$OUT" -path '*/rejects-*/*.rej' -type f -print | sort | tee "$OUT/rejects.txt" || true
if [ -s "$OUT/rejects.txt" ]; then
  while IFS= read -r rej; do
    echo "===== $rej =====" >> "$OUT/rejects-full.txt"
    cat "$rej" >> "$OUT/rejects-full.txt"
  done < "$OUT/rejects.txt"
fi

git diff --check || true

if [ "$failed" -ne 0 ]; then
  echo "Strict ATGC dependency staging found Samsung-specific hunks to adapt." | tee -a "$OUT/report.txt"
  exit 2
fi

grep -Fq 'F2FS_MOUNT_ATGC' fs/f2fs/f2fs.h
grep -Fq 'CURSEG_ALL_DATA_ATGC' fs/f2fs/f2fs.h
grep -Fq 'GC_IDLE_AT' fs/f2fs/f2fs.h
grep -Fq 'GC_AT' fs/f2fs/segment.h
grep -Fq 'AT_SSR' fs/f2fs/segment.h
grep -Fq 'atgc_age_threshold' fs/f2fs/sysfs.c
grep -Fq '"atgc"' fs/f2fs/super.c

echo "ATGC P3 strict source application complete" | tee -a "$OUT/report.txt"
