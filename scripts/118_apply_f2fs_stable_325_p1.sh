#!/usr/bin/env bash
set -Eeuo pipefail

KERNEL="${1:-workspace/touchgrass-a52xq}"
REPORT="${2:-artifacts/f2fs-stable325-p1.txt}"
PATCHDIR="${3:-artifacts/f2fs-stable325-patches}"

test -d "$KERNEL/.git" || { echo "missing kernel git tree: $KERNEL" >&2; exit 1; }
mkdir -p "$(dirname "$REPORT")" "$PATCHDIR"

# Official linux-4.19.y F2FS commits after v4.19.206 through v4.19.325,
# oldest first.  We apply only patches that match this Samsung-derived tree
# cleanly.  Patches already represented in the vendor tree are recorded as
# ALREADY; structurally divergent patches are recorded for manual P2 porting.
COMMITS=(
  8377a6af62992a55b19da1c3ac2b0c0ecae7e5ec
  8002259a366e8a7206f7e706a1e4e33bee6efcef
  efd37f01d719a0f51dcc4929ca2edae46e7a600b
  83def4345440d49dfa90ebd51ba3e87f4dd608cd
  f9dfa44be0fb5e8426183a70f69a246cf5827f49
  c782e685193144e774b502ceec957e0d5524a3fa
  89659bfd778fb32e54ca5fe152983afb93912a61
  385edd3ce5b4b1e9d31f474a5e35a39779ec1110
  6cc2086923b873a75223a86ed1bdf38133634d9f
  fbfad62b29e9f8f1c1026a806c9e064ec2a7c342
  d967df65e19de75a1acea67aec5f9a347079699f
  620ab4d50609885900a19af8bb29c89f645d3959
  ff85a1dbd90d29f73033177ff8d8de4a27d9721c
  a6807ef0f3b3d8508d3b07a2e35de8a91820a014
  bccae81d41220a33087e3216d1cba6f0d181d811
  92575f05a32dafb16348bfa5e62478118a9be069
  37f6006362a2329a8e2fb4be0df759b49f1f3a02
  45c9da086dded78a12bc580f5bb012545a910803
  6735aa9e3303ccba153de1417ac983f8b3a733bd
  16ae3132ff7746894894927c1892493693b89135
  02160112e6d45c2610b049df6eb693d7a2e57b46
  bc1fb291f36dd1d9d667241d9fe30b835dbb8ee8
  3506e1b893b5c2afa96922f36a01f018e4c4bbba
  bc14bb3ef307947fc3110bca8a34a85a63300b6c
  2d2916516577f2239b3377d9e8d12da5e6ccdfcf
  3c2c864f19490da6e892290441ba7dcc7bae2576
  4d9c9b7991627db9e3b97a62908dfef8b2b7201b
  54739b2a2e312436ce9c0cf8860f1167979a5f1f
  eb92623290e2b5a942bf480f8abcb8c7c47c4c06
  c1ea7a86d7e18dc629d717f57cd5df127cea0f88
  72c6b13f468ed21148f3b1b9b2b0aeecc1a74e59
  60bffc6e6b32fb88e5c1234448de5ccf88b590f5
  24dfe070d6d05d62a00c41d5d52af5a448ae7af7
  700f3a7c7fa5764c9f24bbf7c78e0b6e479fa653
)

applied=0
already=0
skipped=0
failed_download=0

: > "$REPORT"
{
  echo "A52 F2FS stable-325 compatibility backport P1"
  echo "kernel=$(git -C "$KERNEL" rev-parse HEAD)"
  echo "source_range=linux-4.19.y:v4.19.206..v4.19.325"
  echo "candidate_count=${#COMMITS[@]}"
  echo
} >> "$REPORT"

for sha in "${COMMITS[@]}"; do
  patch="$PATCHDIR/$sha.patch"
  url="https://github.com/gregkh/linux/commit/$sha.patch"

  if ! curl -fL --retry 3 --retry-delay 2 "$url" -o "$patch"; then
    echo "DOWNLOAD_FAIL $sha" | tee -a "$REPORT"
    failed_download=$((failed_download + 1))
    continue
  fi

  subject="$(sed -n 's/^Subject: \[PATCH[^]]*\] //p; s/^Subject: //p' "$patch" | head -1)"
  [ -n "$subject" ] || subject="(subject unavailable)"

  if git -C "$KERNEL" apply --reverse --check "$OLDPWD/$patch" >/dev/null 2>&1; then
    echo "ALREADY       $sha | $subject" | tee -a "$REPORT"
    already=$((already + 1))
    continue
  fi

  if git -C "$KERNEL" apply --check "$OLDPWD/$patch" >/dev/null 2>&1; then
    git -C "$KERNEL" apply "$OLDPWD/$patch"
    echo "APPLIED       $sha | $subject" | tee -a "$REPORT"
    applied=$((applied + 1))
    continue
  fi

  echo "SKIPPED       $sha | $subject" | tee -a "$REPORT"
  skipped=$((skipped + 1))
done

{
  echo
  echo "applied=$applied"
  echo "already=$already"
  echo "skipped=$skipped"
  echo "download_fail=$failed_download"
  echo "processed=$((applied + already + skipped + failed_download))"
} | tee -a "$REPORT"

test "$failed_download" -eq 0
test "$((applied + already))" -gt 0
git -C "$KERNEL" diff --check

echo "F2FS stable-325 P1 compatibility pass complete"
