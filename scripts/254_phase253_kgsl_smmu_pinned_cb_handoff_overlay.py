#!/usr/bin/env python3
"""Phase 254: restore TouchGrass KGSL firmware context-bank handoff semantics.

Phase253 hardware reaches kgsl_device_platform_probe(), but the default KGSL
pagetable attach returns -ENOSPC before the K253 domain-init success marker.
TouchGrass claims a translated firmware handoff CB for that master before
falling back to context-bank bitmap allocation. ACK 5.10 retains the same live
state as pinned S2CRs plus a reserved context_map bit, but Phase253 did not
claim that bank.

Claim only the PROCID/SID0 KGSL master's own translated pinned mapping, keep it
pinned until the rest of domain init succeeds so failures remain retry-safe,
then transfer ownership to the real HLOS domain without clearing context_map.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CFILE = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
P253 = "A52_PHASE253_KGSL_SMMU_DOMAIN_CONTRACT_V1"
MARKER = "A52_PHASE254_KGSL_PINNED_CB_HANDOFF_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}: {old[:120]!r}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str, label: str) -> tuple[int, int]:
    pat = re.compile(r"(?m)^[^\n]*\b" + re.escape(name) + r"\s*\(")
    for m in pat.finditer(text):
        start = m.start()
        brace = text.find("{", m.end())
        semi = text.find(";", m.end())
        if brace < 0 or (semi >= 0 and semi < brace):
            continue
        depth = 0
        i = brace
        state = "code"
        while i < len(text):
            c = text[i]
            n = text[i + 1] if i + 1 < len(text) else ""
            if state == "code":
                if c == '"': state = "str"
                elif c == "'": state = "char"
                elif c == "/" and n == "/": state = "line"; i += 1
                elif c == "/" and n == "*": state = "block"; i += 1
                elif c == "{": depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0: return start, i + 1
            elif state == "str":
                if c == "\\": i += 1
                elif c == '"': state = "code"
            elif state == "char":
                if c == "\\": i += 1
                elif c == "'": state = "code"
            elif state == "line":
                if c == "\n": state = "code"
            elif state == "block":
                if c == "*" and n == "/": state = "code"; i += 1
            i += 1
    raise RuntimeError(f"{label}: function {name} not found")


def one_in_function(text: str, name: str, old: str, new: str, label: str) -> str:
    start, end = function_bounds(text, name, label)
    body = text[start:end]
    if new in body:
        return text
    count = body.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one scoped anchor, found {count}: {old[:120]!r}")
    body = body.replace(old, new, 1)
    return text[:start] + body + text[end:]


HELPERS = r'''/* A52_PHASE254_KGSL_PINNED_CB_HANDOFF_V1
 * TouchGrass transfers a live translated firmware CB to the one DT master
 * whose stream IDs overlap it. ACK 5.10 represents that firmware state as
 * pinned S2CRs plus a reserved context_map bit. Claim only this master's own
 * translated pinned mapping; never free or force an arbitrary occupied CB.
 */
static int a52_kgsl_claim_pinned_cb(struct iommu_domain *domain,
				    struct arm_smmu_device *smmu,
				    struct device *dev, bool *handoff)
{
	struct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);
	struct iommu_fwspec *fwspec = dev_iommu_fwspec_get(dev);
	struct arm_smmu_master_cfg *master = dev_iommu_priv_get(dev);
	int i, idx, cb = -1, ret = -ENOENT;
	unsigned int matches = 0;
	bool sid0 = false;

	*handoff = false;
	if (a52_kgsl_dynamic_domain(domain) ||
	    !a52_kgsl_procid_domain(smmu_domain) || !fwspec || !master ||
	    master->smmu != smmu)
		return -ENOENT;

	/* Lagoon gfx3d_user is SID 0. PROCID + SID0 keeps this correction
	 * scoped away from display and Apps-SMMU domains.
	 */
	for (i = 0; i < fwspec->num_ids; i++) {
		u16 sid = FIELD_GET(ARM_SMMU_SMR_ID, fwspec->ids[i]);
		if (!sid) {
			sid0 = true;
			break;
		}
	}
	if (!sid0)
		return -ENOENT;

	mutex_lock(&smmu->stream_map_mutex);
	for_each_cfg_sme(master, fwspec, i, idx) {
		struct arm_smmu_s2cr *s2cr;

		if (idx == INVALID_SMENDX) {
			ret = -EINVAL;
			goto out;
		}
		s2cr = &smmu->s2crs[idx];

		/* TouchGrass creates cb_handoff only for firmware TRANS entries. */
		if (!s2cr->pinned || s2cr->type != S2CR_TYPE_TRANS)
			continue;

		if (cb < 0)
			cb = s2cr->cbndx;
		else if (cb != s2cr->cbndx) {
			ret = -EINVAL;
			goto out;
		}
		matches++;
	}

	if (cb < 0)
		goto out;
	if (cb >= smmu->num_context_banks ||
	    !test_bit(cb, smmu->context_map)) {
		ret = -EINVAL;
		goto out;
	}

	*handoff = true;
	ret = cb;
	a52_ackfr_record("K254 C claim cb=%d n=%u", cb, matches);
out:
	mutex_unlock(&smmu->stream_map_mutex);
	return ret;
}

static void a52_kgsl_finish_pinned_cb(struct arm_smmu_device *smmu, u8 cb)
{
	unsigned int i, released = 0;

	mutex_lock(&smmu->stream_map_mutex);
	for (i = 0; i < smmu->num_mapping_groups; i++) {
		struct arm_smmu_s2cr *s2cr = &smmu->s2crs[i];

		if (!s2cr->pinned || s2cr->type != S2CR_TYPE_TRANS ||
		    s2cr->cbndx != cb)
			continue;

		/* context_map remains set: ownership moves to this real domain. */
		s2cr->pinned = false;
		released++;
	}
	mutex_unlock(&smmu->stream_map_mutex);

	a52_ackfr_record("K254 C own cb=%u n=%u", cb, released);
}

'''


def patch_c(text: str, label: str) -> str:
    if MARKER in text:
        validate_c(text, label)
        return text
    if P253 not in text:
        raise RuntimeError(f"{label}: Phase253 domain contract is not present")

    helper_anchor = "static int arm_smmu_alloc_context_bank(struct arm_smmu_domain *smmu_domain,\n"
    text = one(text, helper_anchor, HELPERS + helper_anchor,
               f"{label}: handoff helpers")

    decl_old = "\tbool dynamic = a52_kgsl_dynamic_domain(domain);\n"
    decl_new = decl_old + "\tbool pinned_handoff = false; /* " + MARKER + " */\n"
    text = one_in_function(text, "arm_smmu_init_domain_context", decl_old, decl_new,
                           f"{label}: handoff state")

    alloc_old = '''\t} else {
\t\tret = arm_smmu_alloc_context_bank(smmu_domain, smmu, dev, start);
\t\tif (ret < 0)
\t\t\tgoto out_unlock;
\t}
'''
    alloc_new = '''\t} else {
\t\tret = a52_kgsl_claim_pinned_cb(domain, smmu, dev,
\t\t\t\t\t       &pinned_handoff);
\t\tif (ret == -ENOENT) {
\t\t\tret = arm_smmu_alloc_context_bank(smmu_domain, smmu, dev, start);
\t\t\tif (a52_kgsl_procid_domain(smmu_domain))
\t\t\t\ta52_ackfr_record("K254 C alloc rc=%d", ret);
\t\t}
\t\tif (ret < 0)
\t\t\tgoto out_unlock;
\t}
'''
    text = one_in_function(text, "arm_smmu_init_domain_context", alloc_old, alloc_new,
                           f"{label}: claim before allocation")

    finish_old = '''\tmutex_unlock(&smmu_domain->init_mutex);
\tsmmu_domain->pgtbl_ops = pgtbl_ops;
'''
    finish_new = '''\tif (pinned_handoff)
\t\ta52_kgsl_finish_pinned_cb(smmu, cfg->cbndx);

\tmutex_unlock(&smmu_domain->init_mutex);
\tsmmu_domain->pgtbl_ops = pgtbl_ops;
'''
    text = one_in_function(text, "arm_smmu_init_domain_context", finish_old, finish_new,
                           f"{label}: finalize handoff")

    clear_old = '''out_clear_smmu:
\tif (!dynamic)
\t\t__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);
'''
    clear_new = '''out_clear_smmu:
\t/* A claimed firmware CB was reserved before this domain. If a later
\t * init step fails, leave its bitmap bit and pinned S2CR intact so the
\t * next KGSL probe can retry the same handoff safely.
\t */
\tif (!dynamic && !pinned_handoff)
\t\t__arm_smmu_free_bitmap(smmu->context_map, cfg->cbndx);
'''
    text = one_in_function(text, "arm_smmu_init_domain_context", clear_old, clear_new,
                           f"{label}: rollback-safe bitmap")

    validate_c(text, label)
    return text


def validate_c(text: str, label: str) -> None:
    required = (
        P253, MARKER, "a52_kgsl_claim_pinned_cb", "a52_kgsl_finish_pinned_cb",
        "s2cr->pinned", "s2cr->type != S2CR_TYPE_TRANS",
        "test_bit(cb, smmu->context_map)", "master->smmu != smmu",
        "FIELD_GET(ARM_SMMU_SMR_ID, fwspec->ids[i])",
        'K254 C claim cb=%d n=%u', 'K254 C alloc rc=%d',
        'K254 C own cb=%u n=%u', "if (!dynamic && !pinned_handoff)",
        "context_map remains set: ownership moves to this real domain",
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"{label}: missing {token!r}")


def patch_recorder(text: str, label: str) -> str:
    if 'strncmp(fmt, "K254", 4)' in text and '!strncmp(message, "K254 ", 5)' in text:
        return text
    text = one(text,
        'if (strncmp(fmt, "K253", 4) &&\n',
        'if (strncmp(fmt, "K254", 4) &&\n    strncmp(fmt, "K253", 4) &&\n',
        f"{label}: K254 format filter")
    return one(text,
        'return !strncmp(message, "K253 ", 5) ||\n',
        'return !strncmp(message, "K254 ", 5) ||\n       !strncmp(message, "K253 ", 5) ||\n',
        f"{label}: K254 critical filter")


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out, seen = [], set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key); out.append(root)
    return out


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    hits, seen = [], set()
    for root in candidate_roots(args, base):
        cp, rp = root / CFILE, root / RECORDER
        if not (cp.is_file() and rp.is_file()):
            continue
        ct = cp.read_text(encoding="utf-8")
        if P253 not in ct or "K253 D init dyn=%d cb=%u asid=%u proc=%u" not in ct:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key); hits.append(root)
    if len(hits) != 1:
        raise RuntimeError("expected one Phase253-generated gki/common root, found " +
                           (", ".join(map(str, hits)) or "none"))
    return hits[0]


def model_claim(entries: list[tuple[bool, str, int]], master_indices: list[int],
                context_bits: set[int]) -> int | None:
    cb = None
    for idx in master_indices:
        pinned, typ, bank = entries[idx]
        if not pinned or typ != "TRANS":
            continue
        if cb is None:
            cb = bank
        elif cb != bank:
            raise ValueError("conflicting pinned context banks")
    if cb is None:
        return None
    if cb not in context_bits:
        raise ValueError("pinned CB missing reserved bitmap bit")
    return cb


def self_test() -> None:
    e = [(True, "TRANS", 0), (True, "BYPASS", 2), (False, "TRANS", 3)]
    assert model_claim(e, [0], {0, 2}) == 0
    assert model_claim(e, [1], {0, 2}) is None
    assert model_claim(e, [2], {0, 2}) is None
    try:
        model_claim([(True, "TRANS", 0), (True, "TRANS", 1)], [0, 1], {0, 1})
    except ValueError:
        pass
    else:
        raise AssertionError("conflicting CBs must not be fabricated into success")
    try:
        model_claim([(True, "TRANS", 0)], [0], set())
    except ValueError:
        pass
    else:
        raise AssertionError("handoff without reserved context_map bit must fail")
    for token in ("s2cr->pinned", "S2CR_TYPE_TRANS", "test_bit(cb, smmu->context_map)",
                  "K254 C claim", "K254 C own"):
        assert token in HELPERS, token
    print("Phase 254 KGSL pinned-CB handoff overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    cp, rp = root / CFILE, root / RECORDER
    cp.write_text(patch_c(cp.read_text(encoding="utf-8"), str(cp)), encoding="utf-8")
    rp.write_text(patch_recorder(rp.read_text(encoding="utf-8"), str(rp)), encoding="utf-8")
    print("Phase 254 KGSL firmware context-bank handoff applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
