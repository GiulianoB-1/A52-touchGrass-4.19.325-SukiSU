#!/usr/bin/env python3
"""Materialize the registered Phase257 fast workflow as Phase258 A/B on branch."""
from __future__ import annotations

from pathlib import Path
import py_compile

BR257 = "agent/a52-phase257-kgsl-publication-pipeline-v1"
BR258 = "agent/a52-phase258-no-namei-ab-v1"


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    p255 = Path("scripts/255_phase254_postboot_visibility_overlay.py")
    s = p255.read_text(encoding="utf-8")
    old_route = 'PHASE257 = Path(__file__).resolve().parent / "257_phase256_kgsl_publication_pipeline_overlay.py"'
    new_route = 'PHASE257 = Path(__file__).resolve().parent / "258_phase257_no_namei_ab.py"'
    if new_route not in s:
        s = once(s, old_route, new_route, "Phase255 Phase258 route")
        p255.write_text(s, encoding="utf-8")

    wf = Path(".github/workflows/257-fast-a52-kgsl-publication-rebuild.yml")
    t = wf.read_text(encoding="utf-8")
    if "name: 258 A/B - A52 KGSL publication without namei syscall hooks" in t:
        print("Phase258 A/B workflow already materialized")
        return 0

    t = once(t,
        "name: 257 fast - A52 KGSL publication one-compile rebuild",
        "name: 258 A/B - A52 KGSL publication without namei syscall hooks",
        "workflow name")
    t = once(t,
        "run-name: Fast rebuild Phase 257 from proven Phase 227 source boundary",
        "run-name: Phase 258 no-namei A/B from proven Phase 227 source boundary",
        "run name")
    t = once(t, f"      - {BR257}\n", f"      - {BR258}\n", "push branch")
    t = once(t,
        "      - scripts/257_phase256_kgsl_publication_pipeline_overlay.py\n",
        "      - scripts/257_phase256_kgsl_publication_pipeline_overlay.py\n"
        "      - scripts/258_phase257_no_namei_ab.py\n"
        "      - scripts/258_design.md\n"
        "      - scripts/258_materialize_ab_build.py\n",
        "workflow paths")
    t = once(t,
        "      - name: Check out corrected Phase 257 branch",
        "      - name: Check out Phase 258 no-namei A/B branch",
        "checkout label")
    t = once(t,
        "            scripts/257_phase256_kgsl_publication_pipeline_overlay.py \\\n"
        "            scripts/38_repack_a52_p1_boot.py",
        "            scripts/257_phase256_kgsl_publication_pipeline_overlay.py \\\n"
        "            scripts/258_phase257_no_namei_ab.py \\\n"
        "            scripts/258_materialize_ab_build.py \\\n"
        "            scripts/38_repack_a52_p1_boot.py",
        "py_compile list")
    t = once(t,
        "          python3 scripts/257_phase256_kgsl_publication_pipeline_overlay.py --self-test\n",
        "          python3 scripts/257_phase256_kgsl_publication_pipeline_overlay.py --self-test\n"
        "          python3 scripts/258_phase257_no_namei_ab.py --self-test\n",
        "Phase258 self-test")
    t = once(t,
        "      - name: Apply cumulative Phase 217 through corrected Phase 257",
        "      - name: Apply cumulative Phase 217 through Phase 258 no-namei A/B",
        "apply step label")

    old_checks = '''          grep -Fq 'A52_PHASE257_KGSL_PUBLICATION_CORE_V1' "$ROOT/drivers/base/core.c"
          grep -Fq 'A52_PHASE257_KGSL_LATE_REEMIT_V1' "$ROOT/fs/open.c"
          grep -Fq 'A52_PHASE257_KGSL_NODE_SYSCALL_V1' "$ROOT/fs/namei.c"
          grep -Fq 'A52_PHASE257_NAMEI_ANDROID510_SYSCALL_REPAIR_V1' "$ROOT/fs/namei.c"
          grep -Fq 'F257 s5' "$ROOT/fs/namei.c"
'''
    new_checks = '''          grep -Fq 'A52_PHASE257_KGSL_PUBLICATION_CORE_V1' "$ROOT/drivers/base/core.c"
          grep -Fq 'A52_PHASE257_KGSL_LATE_REEMIT_V1' "$ROOT/fs/open.c"
          grep -Fq 'A52_PHASE258_NO_NAMEI_AB_V1' "$ROOT/fs/open.c"
          ! grep -Fq 'A52_PHASE257_KGSL_NODE_SYSCALL_V1' "$ROOT/fs/namei.c"
          ! grep -Fq 'A52_PHASE257_NAMEI_ANDROID510_SYSCALL_REPAIR_V1' "$ROOT/fs/namei.c"
          ! grep -Fq 'a52_r257_kgsl_node_event' "$ROOT/fs/namei.c"
          ! grep -Fq 'a52_r257_kgsl_node_snapshot' "$ROOT/fs/namei.c"
          ! grep -Fq 'F257 s5' "$ROOT/fs/namei.c"
'''
    t = once(t, old_checks, new_checks, "generated no-namei checks")
    t = once(t,
        "      - name: Verify Phase 257 compiled markers",
        "      - name: Verify Phase 258 A/B compiled markers",
        "verify label")

    for line in (
        "            'F257 mk n=%u rc=%d p=%d g=%d mo=%o M=%u m=%u c=%.15s' \\\n",
        "            'F257 ul n=%u rc=%d p=%d g=%d c=%.15s' \\\n",
        "            'F257 s4 kc=%d kr=%d p=%d g=%d mo=%o M=%u m=%u kt=%llu' \\\n",
    ):
        if t.count(line) != 1:
            raise SystemExit(f"compiled marker line drifted: {line!r}")
        t = t.replace(line, "", 1)
    last = "            'F257 s5 uc=%d ur=%d p=%d g=%d kc=%.15s uc=%.15s ut=%llu'; do\n"
    if t.count(last) != 1:
        raise SystemExit("compiled s5 marker line drifted")
    t = t.replace(last, "", 1)
    s3 = "            'F257 s3 mc=%d mr=%d dn=%d c=%.15s d=%.31s lt=%llu' \\\n"
    if t.count(s3) != 1:
        raise SystemExit("compiled s3 marker line drifted")
    t = t.replace(s3,
        "            'F257 s3 mc=%d mr=%d dn=%d c=%.15s d=%.31s lt=%llu'; do\n",
        1)

    done_anchor = '''          done

          grep -Fxq 'CONFIG_TMPFS_POSIX_ACL=y' "$CFG"
'''
    done_replacement = '''          done

          for forbidden in \\
            'F257 mk n=%u rc=%d p=%d g=%d mo=%o M=%u m=%u c=%.15s' \\
            'F257 ul n=%u rc=%d p=%d g=%d c=%.15s' \\
            'F257 s4 kc=%d kr=%d p=%d g=%d mo=%o M=%u m=%u kt=%llu' \\
            'F257 s5 uc=%d ur=%d p=%d g=%d kc=%.15s uc=%.15s ut=%llu'; do
            if grep -aFq "$forbidden" "$IMAGE"; then
              echo "Phase258 A/B unexpectedly contains removed namei marker: $forbidden" >&2
              exit 1
            fi
          done

          grep -Fxq 'CONFIG_TMPFS_POSIX_ACL=y' "$CFG"
'''
    t = once(t, done_anchor, done_replacement, "negative compiled marker audit")

    t = t.replace("phase257-fast-out", "phase258-ab-out")
    t = once(t,
        "      - name: Repack proven boot image with Phase 257 kernel only",
        "      - name: Repack proven boot image with Phase 258 A/B kernel only",
        "repack label")
    t = once(t,
        "          cp scripts/257_phase256_kgsl_publication_pipeline_overlay.py phase258-ab-out/audit/\n",
        "          cp scripts/257_phase256_kgsl_publication_pipeline_overlay.py phase258-ab-out/audit/\n"
        "          cp scripts/258_phase257_no_namei_ab.py phase258-ab-out/audit/\n"
        "          cp scripts/258_materialize_ab_build.py phase258-ab-out/audit/\n",
        "audit overlay copy")
    t = once(t,
        "          cp scripts/257_design.md phase258-ab-out/PHASE257-DESIGN.md\n",
        "          cp scripts/258_design.md phase258-ab-out/PHASE258-DESIGN.md\n",
        "design copy")
    t = once(t, "              'phase': 257,", "              'phase': 258,", "identity phase")
    t = once(t,
        "              'build_path': 'one-compile-fast-rebuild',",
        "              'build_path': 'one-compile-fast-no-namei-ab',",
        "identity build path")
    t = once(t,
        "              'phase257_namei_anchor': 'android-5.10-syscall-boundary',",
        "              'phase258_ab_removed_phase257_namei': True,\n"
        "              'phase258_retained_phase257_core_open': True,",
        "identity A/B fields")
    t = once(t,
        "              'A52 GKI 5.10 Phase 257 fast one-compile KGSL publication recorder\\n\\n'",
        "              'A52 GKI 5.10 Phase 258 no-namei A/B KGSL publication recorder\\n\\n'",
        "README title")
    t = t.replace("applying cumulative Phase257", "applying cumulative Phase258 no-namei A/B")
    t = once(t,
        "      - name: Upload fast Phase 257 candidate",
        "      - name: Upload Phase 258 no-namei A/B candidate",
        "upload label")
    t = once(t,
        "          name: A52XQ-Phase257-FAST-ONE-COMPILE-${{ github.run_number }}-NOT-HARDWARE-VALIDATED",
        "          name: A52XQ-Phase258-NO-NAMEI-AB-${{ github.run_number }}-NOT-HARDWARE-VALIDATED",
        "artifact name")
    t = once(t,
        "          name: A52XQ-Phase257-FAST-FAILURE-${{ github.run_number }}",
        "          name: A52XQ-Phase258-NO-NAMEI-AB-FAILURE-${{ github.run_number }}",
        "failure artifact name")

    wf.write_text(t, encoding="utf-8")
    py_compile.compile("scripts/258_phase257_no_namei_ab.py", doraise=True)
    py_compile.compile("scripts/258_materialize_ab_build.py", doraise=True)
    if new_route not in p255.read_text(encoding="utf-8"):
        raise SystemExit("Phase255 did not route to Phase258 overlay")
    if BR258 not in wf.read_text(encoding="utf-8"):
        raise SystemExit("Phase258 workflow branch was not materialized")
    print("Phase258 A/B workflow materialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
