#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 132_apply_rndis_ipa_pm_p1.py <kernel-tree> <artifact-dir>")

kernel = Path(sys.argv[1])
art = Path(sys.argv[2])
art.mkdir(parents=True, exist_ok=True)
p = kernel / "drivers/platform/msm/ipa/ipa_clients/rndis_ipa.c"
s = p.read_text()

before_count = s.count("ipa_pm_is_used()")
if before_count != 5:
    raise SystemExit(f"expected exactly 5 ipa_pm_is_used() calls in rndis_ipa.c, found {before_count}")

repls = [
(
'''\tif (ipa_pm_is_used())
\t\tresult = rndis_ipa_register_pm_client(rndis_ipa_ctx);
\telse
\t\tresult = rndis_ipa_create_rm_resource(rndis_ipa_ctx);
\tif (result) {
\t\tRNDIS_IPA_ERROR("fail on RM create\\n");
\t\tgoto fail_create_rm;
\t}
\tRNDIS_IPA_DEBUG("RM resource was created\\n");
''',
'''\t/* A52 RNDIS PM P1: IPA v18 has no ipa_pm_is_used() dispatcher.
\t * Newer Qualcomm RNDIS drivers use the PM framework directly.
\t */
\tresult = rndis_ipa_register_pm_client(rndis_ipa_ctx);
\tif (result) {
\t\tRNDIS_IPA_ERROR("fail on PM register\\n");
\t\tgoto fail_create_rm;
\t}
\tRNDIS_IPA_DEBUG("PM client was registered\\n");
'''
),
(
'''fail:
\tif (ipa_pm_is_used())
\t\trndis_ipa_deregister_pm_client(rndis_ipa_ctx);
\telse
\t\trndis_ipa_destroy_rm_resource(rndis_ipa_ctx);
fail_create_rm:
''',
'''fail:
\t/* A52 RNDIS PM P1: pair direct PM registration with PM cleanup. */
\trndis_ipa_deregister_pm_client(rndis_ipa_ctx);
fail_create_rm:
'''
),
(
'''\tif (ipa_pm_is_used())
\t\tretval = rndis_ipa_deregister_pm_client(rndis_ipa_ctx);
\telse
\t\tretval = rndis_ipa_destroy_rm_resource(rndis_ipa_ctx);
\tif (retval) {
\t\tRNDIS_IPA_ERROR("Fail to clean RM\\n");
\t\treturn retval;
\t}
\tRNDIS_IPA_DEBUG("RM was successfully destroyed\\n");
''',
'''\t/* A52 RNDIS PM P1: use the PM framework directly on IPA v18. */
\tretval = rndis_ipa_deregister_pm_client(rndis_ipa_ctx);
\tif (retval) {
\t\tRNDIS_IPA_ERROR("Fail to deregister PM\\n");
\t\treturn retval;
\t}
\tRNDIS_IPA_DEBUG("PM was successfully deregistered\\n");
'''
),
(
'''\tif (ipa_pm_is_used())
\t\treturn ipa_pm_activate(rndis_ipa_ctx->pm_hdl);

\treturn ipa_rm_inactivity_timer_request_resource(
\t\t\tDRV_RESOURCE_ID);
''',
'''\t/* A52 RNDIS PM P1: IPA v18 RNDIS uses PM, not legacy RM. */
\treturn ipa_pm_activate(rndis_ipa_ctx->pm_hdl);
'''
),
(
'''\tif (ipa_pm_is_used())
\t\tipa_pm_deferred_deactivate(rndis_ipa_ctx->pm_hdl);
\telse
\t\tipa_rm_inactivity_timer_release_resource(DRV_RESOURCE_ID);
''',
'''\t/* A52 RNDIS PM P1: IPA v18 RNDIS uses PM, not legacy RM. */
\tipa_pm_deferred_deactivate(rndis_ipa_ctx->pm_hdl);
'''
),
]

for idx, (old, new) in enumerate(repls, 1):
    count = s.count(old)
    if count != 1:
        raise SystemExit(f"patch block {idx}: expected exactly 1 match, found {count}")
    s = s.replace(old, new, 1)

after_count = s.count("ipa_pm_is_used()")
if after_count != 1:
    # One occurrence is intentionally present only in the explanatory comment above.
    raise SystemExit(f"unexpected ipa_pm_is_used() text count after patch: {after_count}")

# Ensure there are no executable calls left; comment-only occurrence is allowed.
code_lines = [
    line for line in s.splitlines()
    if "ipa_pm_is_used()" in line and not line.lstrip().startswith("*")
]
if code_lines:
    raise SystemExit("executable ipa_pm_is_used() references remain: " + repr(code_lines))

required = [
    "A52 RNDIS PM P1: IPA v18 has no ipa_pm_is_used() dispatcher.",
    "result = rndis_ipa_register_pm_client(rndis_ipa_ctx);",
    "rndis_ipa_deregister_pm_client(rndis_ipa_ctx);",
    "return ipa_pm_activate(rndis_ipa_ctx->pm_hdl);",
    "ipa_pm_deferred_deactivate(rndis_ipa_ctx->pm_hdl);",
]
for needle in required:
    if needle not in s:
        raise SystemExit(f"missing post-patch marker: {needle}")

p.write_text(s)

report = art / "report.txt"
report.write_text(
    "A52 RNDIS IPA PM P1\n"
    "====================\n"
    f"target={p}\n"
    f"ipa_pm_is_used_calls_before={before_count}\n"
    "ipa_pm_is_used_executable_calls_after=0\n"
    "connect=direct_pm_register\n"
    "connect_error=direct_pm_deregister\n"
    "disconnect=direct_pm_deregister\n"
    "resource_request=direct_pm_activate\n"
    "resource_release=direct_pm_deferred_deactivate\n"
    "scope=rndis_ipa.c_only\n"
)

print(report.read_text(), end="")
