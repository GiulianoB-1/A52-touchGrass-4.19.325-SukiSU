#!/usr/bin/env bash
set -Eeuo pipefail
python3 - <<'PY'
from pathlib import Path
p=Path('scripts/203_ci.sh')
s=p.read_text()
start=s.index("for marker in (\n    '{ .compatible = \"qcom,qsmmu-v500\"")
end=s.index("for marker in (\n    'SMMU parent-qcom-create scm=%d'", start)
core='''for marker in (\n    '{ .compatible = "qcom,qsmmu-v500", .data = &arm_mmu500 },',\n    'smmu->skip_init = of_property_read_bool',\n    'if (!smmu->skip_init)',\n    'if (smmu->use_3lvl_tables)',\n    'ias = min(ias, 39UL);',\n    'SMMU parent-probe enter dev=%s driver=%s',\n    'SMMU parent-dt rc=%d model=%d skip=%d lvl3=%d',\n    'SMMU parent-impl rc=%d',\n    'SMMU parent-cfg rc=%d groups=%u cbs=%u',\n    'SMMU parent-register rc=%d',\n    'SMMU parent-probe exit rc=%d',\n):\n    assert marker in core, marker\n'''
s=s[:start]+core+s[end:]
s=s.replace("for marker in (\n    'SMMU parent-qcom-create scm=%d',\n    'SMMU parent-qcom-cfg enter groups=%u',\n    'SMMU parent-qcom-cfg exit rc=0',\n    'SMMU parent-qcom-create rc=0',\n):", "for marker in (\n    'SMMU parent-qcom scm=%d',\n):")
s=s.replace("'SMMU parent-dt version=%d model=%d girq=%u skip=%d lvl3=%d'", "'SMMU parent-dt rc=%d model=%d skip=%d lvl3=%d'")
s=s.replace("'SMMU parent-qcom-create scm=%d'", "'SMMU parent-qcom scm=%d'")
s=s.replace("  'SMMU parent-qcom-cfg enter groups=%u' \\\n", "")
s=s.replace("  'SMMU parent-reset enter skip=%d groups=%u cbs=%u' \\\n", "")
s=s.replace("  'SMMU parent-domain 3lvl ias=%lu' \\\n", "")
s=s.replace("  'SMMU parent-probe exit rc=%d legacy=0' \\\n", "  'SMMU parent-probe exit rc=%d' \\\n")
s=s.replace('phase203-apps-smmu-qsmmuv500-compat.patch','phase203-apps-smmu-parent-trace.patch')
s=s.replace("'status': 'a52-apps-smmu-qsmmuv500-compat-audited'", "'status': 'a52-apps-smmu-parent-trace-audited'")
s=s.replace("'functional_change_from_phase202': 'lagoon-apps-smmu-qsmmuv500-minimal-compatibility'", "'functional_change_from_phase202': 'none-diagnostic-parent-trace-only'")
s=s.replace("'qcom_qsmmuv500_match_added': True,", "'qcom_qsmmuv500_match_preexisting': True,\n    'phase203_parent_trace_added': True,")
s=s.replace("    'qcom_qsmmuv500_match_added',\n", "    'qcom_qsmmuv500_match_preexisting',\n    'phase203_parent_trace_added',\n")
Path('/tmp/203_ci_runtime.sh').write_text(s)
PY
bash -n /tmp/203_ci_runtime.sh
bash /tmp/203_ci_runtime.sh

OUT="$PWD/artifacts/a52xq-apps-smmu-qsmmuv500-compat"
cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 Phase 203 Lagoon Apps SMMU parent-probe trace

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 202 showed that the display context-bank child is deferred because its
supplier 15000000.apps-smmu has no bound driver.

Post-Phase-202 source verification found that qcom,qsmmu-v500 matching,
qcom,skip-init preservation, and the 39-bit three-level table behavior already
exist in the cumulative kernel. Phase 203 therefore changes no SMMU policy.

Phase 203 records the parent probe entry, DT result, Qualcomm SCM readiness,
implementation selection, hardware configuration, IOMMU registration, and
probe completion. Phase 202 device-link tracing, the Phase 201 component gate,
and the R99 three-copy RS32/CRC32C recorder remain unchanged.

No IOMMU bypass or dependency relaxation is added. DTB, DTBO, ramdisk, panel
commands, display timing, and power policy remain unchanged.

Compile-audited, not hardware validated.
EOF
(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
