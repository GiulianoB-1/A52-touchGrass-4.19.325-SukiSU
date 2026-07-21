#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/ufs-tmrd-compat-$TARGET_VERSION.txt"

 test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
 test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before UFS TMRD repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
ufshcd = root / "drivers/scsi/ufs/ufshcd.c"
ufshci = root / "drivers/scsi/ufs/ufshci.h"

source = ufshcd.read_text()
header = ufshci.read_text()

struct_start = header.index("struct utp_task_req_desc {")
struct_end = header.index("\n};", struct_start)
tmrd = header[struct_start:struct_end]

for required in ("struct utp_upiu_header\treq_header;", "struct utp_upiu_header\trsp_header;"):
    if required not in tmrd:
        raise SystemExit(f"Linux 4.19.325 UFS TMRD member is missing: {required}")

for obsolete in ("task_req_upiu", "task_rsp_upiu"):
    if obsolete in tmrd:
        raise SystemExit(f"obsolete Samsung UFS TMRD member remains in header: {obsolete}")

replacements = {
    "tmrdp->task_req_upiu": "&tmrdp->req_header",
    "tmrdp->task_rsp_upiu": "&tmrdp->rsp_header",
}

for old, new in replacements.items():
    count = source.count(old)
    if count == 1:
        source = source.replace(old, new, 1)
    elif count == 0 and source.count(new) == 1:
        pass
    else:
        raise SystemExit(
            f"UFS TMRD debug pointer anchor mismatch for {old}: "
            f"old={count}, new={source.count(new)}"
        )

ufshcd.write_text(source)

final = ufshcd.read_text()
for old, new in replacements.items():
    if old in final:
        raise SystemExit(f"obsolete UFS TMRD pointer remains: {old}")
    if final.count(new) != 1:
        raise SystemExit(f"UFS TMRD replacement count is not one: {new}")
PY

git -C "$KERNEL_DIR" diff --check -- drivers/scsi/ufs/ufshcd.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'request_upiu=task_req_upiu-to-req_header\n'
  printf 'response_upiu=task_rsp_upiu-to-rsp_header\n'
  printf 'layout=preserved-dw4-and-dw12-addresses\n'
  printf 'result=linux-4.19.325-ufs-tmrd-member-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION UFS TMRD compatibility repaired"
