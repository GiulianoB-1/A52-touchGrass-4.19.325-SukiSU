#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.206
FIX_COMMIT=75c9b1955b7e1f0a959b70f9d631a93634d742e5
ANDROID_COMMON_REPO=https://android.googlesource.com/kernel/common
WORK="$WORKSPACE/bpf-ringbuf-overrun-fix"
PATCH="$ARTIFACTS_DIR/bpf-ringbuf-patches/ringbuf-overrunning-reservations.patch"
RINGBUF="$KERNEL_DIR/kernel/bpf/ringbuf.c"

[[ "$(kernel_version)" == "$TARGET_VERSION" ]] || fail "Expected Linux $TARGET_VERSION"
[[ -s "$RINGBUF" ]] || fail "BPF ring-buffer implementation is missing"
mkdir -p "$WORK" "$(dirname "$PATCH")"
rm -rf "$WORK"/*

git init -q "$WORK/donor"
git -C "$WORK/donor" remote add origin "$ANDROID_COMMON_REPO"
git -C "$WORK/donor" -c protocol.version=2 fetch --quiet --filter=blob:none --depth=2 origin "$FIX_COMMIT"
resolved=$(git -C "$WORK/donor" rev-parse FETCH_HEAD)
parent=$(git -C "$WORK/donor" rev-parse "${resolved}^")
git -C "$WORK/donor" diff --binary --full-index "$parent" "$resolved" -- kernel/bpf/ringbuf.c > "$PATCH"
test -s "$PATCH" || fail "Android ring-buffer overrun fix patch is empty"

if grep -Fq 'unsigned long pending_pos;' "$RINGBUF" && \
   grep -Fq 'new_prod_pos - pend_pos > rb->mask' "$RINGBUF"; then
  result=already-present-vendor
else
  python3 - "$RINGBUF" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    "\tunsigned long producer_pos __aligned(PAGE_SIZE);\n"
    "\tchar data[] __aligned(PAGE_SIZE);\n",
    "\tunsigned long producer_pos __aligned(PAGE_SIZE);\n"
    "\tunsigned long pending_pos;\n"
    "\tchar data[] __aligned(PAGE_SIZE);\n",
    "pending position field",
)
replace_once(
    "\trb->producer_pos = 0;\n\n\treturn rb;\n",
    "\trb->producer_pos = 0;\n"
    "\trb->pending_pos = 0;\n\n"
    "\treturn rb;\n",
    "pending position initialization",
)
replace_once(
    "\tunsigned long cons_pos, prod_pos, new_prod_pos, flags;\n"
    "\tu32 len, pg_off;\n"
    "\tstruct bpf_ringbuf_hdr *hdr;\n",
    "\tunsigned long cons_pos, prod_pos, new_prod_pos, pend_pos, flags;\n"
    "\tstruct bpf_ringbuf_hdr *hdr;\n"
    "\tu32 len, pg_off, tmp_size, hdr_len;\n",
    "reservation locals",
)
replace_once(
    "\tprod_pos = rb->producer_pos;\n"
    "\tnew_prod_pos = prod_pos + len;\n\n"
    "\t/* check for out of ringbuf space by ensuring producer position\n"
    "\t * doesn't advance more than (ringbuf_size - 1) ahead\n"
    "\t */\n"
    "\tif (new_prod_pos - cons_pos > rb->mask) {\n",
    "\tpend_pos = rb->pending_pos;\n"
    "\tprod_pos = rb->producer_pos;\n"
    "\tnew_prod_pos = prod_pos + len;\n\n"
    "\twhile (pend_pos < prod_pos) {\n"
    "\t\thdr = (void *)rb->data + (pend_pos & rb->mask);\n"
    "\t\thdr_len = READ_ONCE(hdr->len);\n"
    "\t\tif (hdr_len & BPF_RINGBUF_BUSY_BIT)\n"
    "\t\t\tbreak;\n"
    "\t\ttmp_size = hdr_len & ~BPF_RINGBUF_DISCARD_BIT;\n"
    "\t\ttmp_size = round_up(tmp_size + BPF_RINGBUF_HDR_SZ, 8);\n"
    "\t\tpend_pos += tmp_size;\n"
    "\t}\n"
    "\trb->pending_pos = pend_pos;\n\n"
    "\t/* check for out of ringbuf space:\n"
    "\t * - by ensuring producer position doesn't advance more than\n"
    "\t *   (ringbuf_size - 1) ahead\n"
    "\t * - by ensuring oldest not yet committed record until newest\n"
    "\t *   record does not span more than (ringbuf_size - 1)\n"
    "\t */\n"
    "\tif (new_prod_pos - cons_pos > rb->mask ||\n"
    "\t    new_prod_pos - pend_pos > rb->mask) {\n",
    "oldest pending reservation bound",
)
path.write_text(text)
PY
  result=applied-vendor
fi

grep -Fq 'unsigned long pending_pos;' "$RINGBUF" || fail "pending_pos tracking is missing"
grep -Fq 'new_prod_pos - pend_pos > rb->mask' "$RINGBUF" || fail "oldest-pending-record bound is missing"
grep -Fq 'BPF_RINGBUF_BUSY_BIT' "$RINGBUF" || fail "ring-buffer busy-record tracking is missing"

git -C "$KERNEL_DIR" add -A
git -C "$KERNEL_DIR" commit -m 'Backport ringbuf overrunning-reservations fix' >/dev/null || true
{
  echo "ringbuf_overrun_fix_commit=$FIX_COMMIT"
  echo "ringbuf_overrun_fix_result=$result"
  echo 'source_patch_recorded=yes'
  echo 'pending_record_span_check=present'
} | tee "$ARTIFACTS_DIR/bpf-ringbuf-overrun-fix.txt"
