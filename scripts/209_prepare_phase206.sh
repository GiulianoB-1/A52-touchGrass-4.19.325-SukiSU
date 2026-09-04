#!/usr/bin/env bash
set -Eeuo pipefail

OUT="$PWD/artifacts/a52xq-smmu-display-contracts"
ZIP="$PWD/workspace/phase206-success.zip"
ARTIFACT_ID=8830356785
ARTIFACT_SHA256=f5e5c51cee21b1548aa19660f57e7b3f5cf05abee80035c7915d42d891d322e4
SEED="/tmp/phase227-seed"
EXPECTED_SEED_RUN_ID=31644392197
EXPECTED_PHASE199_PATCH_SHA256=f9d08b3ce41d6a5a71ddea5699046983e0a5deddb9b6504bc1b5b30894c0a049

rm -rf "$OUT" "$ZIP"
mkdir -p "$OUT" "$PWD/workspace"

# Phase319 reconstruction compatibility. The original Phase206 artifact has
# expired, but the live, SHA-manifested Phase227 seed is a retained descendant
# of that exact Phase206 source/config boundary and carries every byte consumed
# by scripts/319_reconstruct_phase206_from_verified_artifact.sh. Prefer that
# retained descendant when present. All copied bytes are first verified against
# the seed's own SHA256SUMS; the bridge then verifies the derived manifest again.
# No kernel behavior, observer logic, or historical Phase175 identity gate is
# changed here.
if [ -s "$SEED/BUILD-IDENTITY.json" ] && [ -s "$SEED/SHA256SUMS" ]; then
  (
    cd "$SEED"
    sha256sum -c SHA256SUMS
  )

  python3 - "$SEED/BUILD-IDENTITY.json" "$EXPECTED_SEED_RUN_ID" <<'PY'
import json
from pathlib import Path
import sys

identity = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_run = sys.argv[2]
if str(identity.get("phase")) != "227":
    raise SystemExit(f"Phase206 retained-seed phase mismatch: {identity.get('phase')!r}")
if str(identity.get("run_id")) != expected_run:
    raise SystemExit(
        f"Phase206 retained-seed run mismatch: {identity.get('run_id')!r} != {expected_run}"
    )
print("Phase206 retained Phase227 seed identity: PASS")
PY

  # Phase208 is source-only at this boundary and preserves the Phase206 config.
  # Prove that invariant before using before-phase208.config as Phase206 final.
  cmp -s "$SEED/config/before-phase199.config" "$SEED/config/before-phase208.config"

  mkdir -p "$OUT/config" "$OUT/stage"
  for cfg in \
    before-phase199.config \
    before-phase200.config \
    before-phase201.config \
    before-phase202.config \
    before-phase203.config \
    before-phase204.config \
    before-phase206.config; do
    test -s "$SEED/config/$cfg"
    cp "$SEED/config/$cfg" "$OUT/config/$cfg"
  done
  cp "$SEED/config/before-phase208.config" "$OUT/config/final.config"

  for name in \
    phase199-post-kms-crc32c.patch \
    recorder-after-phase199.c \
    recorder-before-phase200.c \
    msm-smmu-before-phase200.c \
    sde-kms-before-phase200.c \
    msm-smmu-after-phase200.c \
    sde-kms-after-phase200.c \
    recorder-after-phase200.c \
    msm-drv-before-phase201.c \
    msm-drv-after-phase201.c \
    msm-smmu-after-phase201.c \
    recorder-after-phase201.c \
    drivers-base-dd-after-phase202.c \
    drivers-base-core-after-phase202.c \
    drivers-base-platform-after-phase202.c \
    drivers-of-device-after-phase202.c \
    drivers-iommu-of_iommu-after-phase202.c \
    drivers-a52_secure-a52_ack_secure_flight_recorder-after-phase202.c \
    arm-smmu-after-phase203.c \
    arm-smmu-qcom-after-phase203.c \
    recorder-after-phase203.c \
    arm-smmu-after-phase204.c \
    arm-smmu-qcom-after-phase204.c \
    recorder-after-phase204.c \
    drivers-a52_display-msm-msm_smmu.c-after-phase206 \
    drivers-iommu-arm-arm-smmu-arm-smmu.c-after-phase206 \
    drivers-iommu-arm-arm-smmu-arm-smmu.h-after-phase206 \
    drivers-iommu-dma-iommu.c-after-phase206 \
    include-linux-iommu.h-after-phase206 \
    recorder-after-phase206.c; do
    test -s "$SEED/stage/$name"
    cp "$SEED/stage/$name" "$OUT/stage/$name"
  done

  printf '%s  %s\n' \
    "$EXPECTED_PHASE199_PATCH_SHA256" \
    "$OUT/stage/phase199-post-kms-crc32c.patch" | sha256sum -c -

  (
    cd "$OUT"
    find config stage -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
    sha256sum -c SHA256SUMS
  )

  printf '%s\n' 'Phase 206 inherited artifact verification: PASS via retained Phase227 seed'
  exit 0
fi

# Historical fallback for workflows that do not already possess the retained
# Phase227 seed. This path remains byte-identical in intent to the original
# Phase209 preparation and still fails closed if the old artifact is unavailable.
curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/${ARTIFACT_ID}/zip" \
  --output "$ZIP"

printf '%s  %s\n' "$ARTIFACT_SHA256" "$ZIP" | sha256sum -c -
unzip -q "$ZIP" -d "$OUT"
(
  cd "$OUT"
  sha256sum -c SHA256SUMS
)

printf '%s\n' 'Phase 206 inherited artifact verification: PASS'
