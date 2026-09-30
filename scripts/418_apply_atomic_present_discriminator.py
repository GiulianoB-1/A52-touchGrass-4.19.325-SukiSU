#!/usr/bin/env python3
from __future__ import annotations
import argparse, re
from pathlib import Path

MARK = "A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
ATOMIC = Path("drivers/gpu/drm/drm_atomic_helper.c")
DRM = Path("drivers/a52_display/msm/dsi/dsi_drm.c")
MSM = Path("drivers/a52_display/msm/msm_drv.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase418 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str) -> tuple[int, int]:
    masked = list(text)
    state = "normal"
    esc = False
    i = 0
    while i < len(text):
        c = text[i]; n = text[i+1] if i+1 < len(text) else ""
        if state == "normal":
            if c == "/" and n == "/":
                masked[i] = masked[i+1] = " "; state = "line"; i += 2; continue
            if c == "/" and n == "*":
                masked[i] = masked[i+1] = " "; state = "block"; i += 2; continue
            if c == '"':
                masked[i] = " "; state = "string"; esc = False
            elif c == "'":
                masked[i] = " "; state = "char"; esc = False
        elif state == "line":
            if c == "\n": state = "normal"
            else: masked[i] = " "
        elif state == "block":
            if c == "*" and n == "/":
                masked[i] = masked[i+1] = " "; state = "normal"; i += 2; continue
            if c != "\n": masked[i] = " "
        else:
            quote = '"' if state == "string" else "'"
            if c != "\n":
                masked[i] = " "
                if esc: esc = False
                elif c == "\\": esc = True
                elif c == quote: state = "normal"
        i += 1
    mtext = "".join(masked)
    hits = []
    for m in re.finditer(r"\b" + re.escape(name) + r"\s*\(", mtext):
        p = m.end()-1; depth = 0; close = -1
        for j in range(p, len(mtext)):
            if mtext[j] == "(": depth += 1
            elif mtext[j] == ")":
                depth -= 1
                if depth == 0: close = j; break
        if close < 0: continue
        tail = mtext[close+1:close+4097]
        br = tail.find("{"); semi = tail.find(";")
        if br < 0 or (semi >= 0 and semi < br): continue
        opening = close + 1 + br
        depth = 0
        for j in range(opening, len(mtext)):
            if mtext[j] == "{": depth += 1
            elif mtext[j] == "}":
                depth -= 1
                if depth == 0:
                    hits.append((m.start(), j+1)); break
    if len(hits) != 1:
        raise SystemExit(f"Phase418 definition count mismatch {name}: {len(hits)}")
    return hits[0]


APEX_HELPER = r'''
/* A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1
 * Low-rate process frontier for the long pre-zygote gap. One sample/10 s.
 */
static unsigned int a52_p418_apex_bucket;

static void a52_p418_apex_sample(unsigned long boot_s)
{
	struct task_struct *task;
	unsigned int bucket = (unsigned int)(boot_s / 10UL);
	unsigned int n = 0;
	pid_t pid = 0;
	long state = 0;
	int cpu = -1;
	char comm[TASK_COMM_LEN] = "-";
	char tmp[TASK_COMM_LEN];

	if (!bucket || bucket == a52_p418_apex_bucket || boot_s > 180UL)
		return;
	a52_p418_apex_bucket = bucket;

	rcu_read_lock();
	for_each_process(task) {
		get_task_comm(tmp, task);
		if (strncmp(tmp, "apexd", 5))
			continue;
		n++;
		if (!pid) {
			pid = task_pid_nr(task);
			state = READ_ONCE(task->state);
			cpu = task_cpu(task);
			strscpy(comm, tmp, sizeof(comm));
		}
	}
	rcu_read_unlock();

	a52_ackfr_record("P418 APX t=%lu n=%u p=%d st=%lx cpu=%d c=%.15s",
		boot_s, n, pid, state, cpu, comm);
}
'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE417_POSTBIND_FRONTIER_V1" not in text:
        raise SystemExit("Phase418 requires Phase417 recorder")

    text = one(text,
        'if (strncmp(fmt, "P417", 4) &&\n',
        'if (strncmp(fmt, "P418", 4) &&\n'
        '    strncmp(fmt, "P417", 4) &&\n',
        "first P418 gate")
    text = one(text,
        '\tif (!fmt || (\n\t    strncmp(fmt, "P417", 4) &&\n',
        '\tif (!fmt || (\n\t    strncmp(fmt, "P418", 4) &&\n'
        '\t    strncmp(fmt, "P417", 4) &&\n',
        "second P418 gate")

    # Phase417 proved connector/mode enumeration is healthy. Retire the very
    # high-volume Phase269 property/UAPI dump from both admission gates.
    p269 = re.compile(r'(?m)^[ \\t]+strncmp\\(fmt, "P269", 4\\) &&\\n')
    text, removed = p269.subn('', text)
    if removed != 2:
        raise SystemExit(
            f"Phase418 P269 retirement: expected 2 admission lines, found {removed}")

    decl = "static void a52_r273_frontier_fn(struct work_struct *work);\n"
    text = one(text, decl, APEX_HELPER + "\n" + decl, "APEX helper insertion")

    call = "\ta52_r273_scan_tasks(boot_s);\n"
    text = one(text, call, call + "\ta52_p418_apex_sample(boot_s);\n",
               "APEX sample call")

    text += f'\n/* {MARK}: sparse atomic-present + APEX frontier. */\n'
    return text


ATOMIC_HELPER = r'''
/* A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1
 * Phase417 observed ~48 successful modeset checks/s but no commit.
 * Sample successful checks sparsely, always report errors.
 */
static bool a52_p418_modeset_log(unsigned int n, int ret)
{
	return ret || n <= 16U || !(n & 127U);
}

static void a52_p418_modeset_result(struct drm_atomic_state *state,
				    unsigned int n, int ret)
{
	struct drm_crtc *crtc;
	struct drm_crtc_state *cs;
	int i;
	int crtc_id = -1;
	unsigned int en = 9U, ac = 9U, mc = 9U, cc = 9U;
	unsigned int cm = 0U;

	if (!a52_p418_modeset_log(n, ret))
		return;

	for_each_new_crtc_in_state(state, crtc, cs, i) {
		crtc_id = crtc ? crtc->base.id : -1;
		if (cs) {
			en = cs->enable;
			ac = cs->active;
			mc = cs->mode_changed;
			cc = cs->connectors_changed;
			cm = cs->connector_mask;
		}
		break;
	}

	a52_ackfr_record(
		"P418 MS n=%u r=%d p=%d t=%d c=%.12s am=%u au=%u cr=%d en=%u ac=%u mc=%u cc=%u cm=%x",
		n, ret, current->pid, current->tgid, current->comm,
		state ? state->allow_modeset : 9U,
		state ? state->async_update : 9U,
		crtc_id, en, ac, mc, cc, cm);
}
'''


def patch_atomic(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1" not in text:
        raise SystemExit("Phase418 requires Phase387 atomic splitter")
    text = one(text,
        '\tA52_ACKFR_SCOPE("DISP", "a52.drm_atomic_helper_check_modeset");\n',
        '',
        "remove high-rate atomic scope")

    anchor = "static inline bool a52_p387_log(unsigned int n)\n"
    pos = text.find(anchor)
    if pos < 0:
        raise SystemExit("Phase418 P387 helper anchor missing")
    start, end = function_bounds(text, "a52_p387_log")
    text = text[:end] + "\n" + ATOMIC_HELPER + text[end:]

    old = '''\tret = mode_fixup(state);
\tif (ret && a52_p387_log(a52_p387_n))
\t\ta52_ackfr_record("P276 387M n=%u st=11 r=%d", a52_p387_n, ret);
\treturn ret;
'''
    new = '''\tret = mode_fixup(state);
\tif (ret && a52_p387_log(a52_p387_n))
\t\ta52_ackfr_record("P276 387M n=%u st=11 r=%d", a52_p387_n, ret);
\ta52_p418_modeset_result(state, a52_p387_n, ret);
\treturn ret;
'''
    text = one(text, old, new, "modeset result hook")
    text += f'\n/* {MARK}: sparse modeset result sampler. */\n'
    return text


def patch_drm(text: str) -> str:
    if MARK in text:
        return text
    text = one(text,
        '\tA52_ACKFR_SCOPE("DISP", "a52.dsi_bridge_mode_fixup");\n',
        '',
        "remove high-rate bridge fixup scope")
    text += f'\n/* {MARK}: Phase417 bridge fixup scope retired; mode_set/pre_enable remain. */\n'
    return text


MSM_HELPER = r'''
/* A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1
 * Observe only Composer DRM_MODE_ATOMIC ioctls. Phase269's broad 1024-ioctl
 * window is exhausted during property enumeration before the first present.
 */
static atomic_t a52_p418_atomic_seq = ATOMIC_INIT(0);
static atomic_t a52_p418_atomic_test = ATOMIC_INIT(0);
static atomic_t a52_p418_atomic_real = ATOMIC_INIT(0);
static atomic_t a52_p418_atomic_err = ATOMIC_INIT(0);

static bool a52_p418_atomic_pre(unsigned int cmd, unsigned long arg,
			       unsigned int *seq, unsigned int *flags,
			       bool *log)
{
	struct drm_mode_atomic a;
	bool test;

	*seq = 0U;
	*flags = 0U;
	*log = false;
	if (_IOC_NR(cmd) != 0xBC || !a52_r269_is_composer_task())
		return false;

	*seq = (unsigned int)atomic_inc_return(&a52_p418_atomic_seq);
	if (copy_from_user(&a, (void __user *)arg, sizeof(a))) {
		a52_ackfr_record("P418 AT in n=%u p=%d t=%d copy=-14",
			*seq, current->pid, current->tgid);
		*log = true;
		return true;
	}

	*flags = a.flags;
	test = !!(a.flags & DRM_MODE_ATOMIC_TEST_ONLY);
	if (test)
		atomic_inc(&a52_p418_atomic_test);
	else
		atomic_inc(&a52_p418_atomic_real);

	*log = !test || *seq <= 16U || !(*seq & 127U);
	if (*log)
		a52_ackfr_record(
			"P418 AT in n=%u p=%d t=%d c=%.12s fl=%x objs=%u test=%u allow=%u",
			*seq, current->pid, current->tgid, current->comm,
			a.flags, a.count_objs, test,
			!!(a.flags & DRM_MODE_ATOMIC_ALLOW_MODESET));
	return true;
}

static void a52_p418_atomic_post(unsigned int seq, unsigned int flags,
				bool log, long rc)
{
	if (rc < 0)
		atomic_inc(&a52_p418_atomic_err);
	if (log || rc < 0)
		a52_ackfr_record(
			"P418 AT out n=%u rc=%ld fl=%x tc=%d real=%d err=%d",
			seq, rc, flags,
			atomic_read(&a52_p418_atomic_test),
			atomic_read(&a52_p418_atomic_real),
			atomic_read(&a52_p418_atomic_err));
}
'''


def patch_msm(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE269_COMPOSER_DRM_UAPI_V1" not in text:
        raise SystemExit("Phase418 requires Phase269 composer wrapper")

    _, helper_end = function_bounds(text, "a52_r269_is_composer_task")
    text = text[:helper_end] + "\n" + MSM_HELPER + text[helper_end:]

    for fn, call in (("a52_r211_drm_ioctl", "drm_ioctl"),
                     ("a52_r211_drm_compat_ioctl", "drm_compat_ioctl")):
        start, end = function_bounds(text, fn)
        body = text[start:end]
        body = one(body,
            "\tunsigned int trace_id, composer_id = 0;\n\tbool trace, composer;\n\tlong rc;\n",
            "\tunsigned int trace_id, composer_id = 0;\n"
            "\tunsigned int a52_p418_n = 0, a52_p418_flags = 0;\n"
            "\tbool trace, composer;\n"
            "\tbool a52_p418_log = false, a52_p418_atomic = false;\n"
            "\tlong rc;\n",
            fn + " locals")
        target = f"\trc = {call}(filp, cmd, arg);\n"
        repl = (
            "\ta52_p418_atomic = a52_p418_atomic_pre(cmd, arg,\n"
            "\t\t&a52_p418_n, &a52_p418_flags, &a52_p418_log);\n" +
            target +
            "\tif (a52_p418_atomic)\n"
            "\t\ta52_p418_atomic_post(a52_p418_n, a52_p418_flags,\n"
            "\t\t\ta52_p418_log, rc);\n"
        )
        body = one(body, target, repl, fn + " call")
        text = text[:start] + body + text[end:]

    text += f'\n/* {MARK}: exact Composer atomic ioctl discriminator. */\n'
    return text


def validate(root: Path) -> None:
    rec=(root/REC).read_text(errors="replace")
    atomic=(root/ATOMIC).read_text(errors="replace")
    drm=(root/DRM).read_text(errors="replace")
    msm=(root/MSM).read_text(errors="replace")

    for tok in (
        MARK, 'strncmp(fmt, "P418", 4)', "P418 APX t=%lu",
        "a52_p418_apex_sample(boot_s);",
    ):
        if tok not in rec:
            raise SystemExit("Phase418 recorder token missing: " + tok)
    if 'strncmp(fmt, "P269", 4)' in rec:
        raise SystemExit("Phase418 high-volume P269 admission still active")

    for tok in (
        MARK, "P418 MS n=%u r=%d", "a52_p418_modeset_result",
    ):
        if tok not in atomic:
            raise SystemExit("Phase418 atomic token missing: " + tok)
    if 'A52_ACKFR_SCOPE("DISP", "a52.drm_atomic_helper_check_modeset");' in atomic:
        raise SystemExit("Phase418 high-rate atomic scope still active")
    if 'A52_ACKFR_SCOPE("DISP", "a52.dsi_bridge_mode_fixup");' in drm:
        raise SystemExit("Phase418 high-rate bridge fixup scope still active")

    for tok in (
        MARK, "P418 AT in n=%u", "P418 AT out n=%u",
        "DRM_MODE_ATOMIC_TEST_ONLY", "DRM_MODE_ATOMIC_ALLOW_MODESET",
        "a52_p418_atomic_pre(cmd, arg",
    ):
        if tok not in msm:
            raise SystemExit("Phase418 MSM token missing: " + tok)


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    ns=ap.parse_args()
    for rel in (REC,ATOMIC,DRM,MSM):
        if not (ns.root/rel).is_file():
            raise SystemExit("Phase418 source missing: "+str(rel))
    if not ns.check_only:
        for rel,fn in ((REC,patch_rec),(ATOMIC,patch_atomic),(DRM,patch_drm),(MSM,patch_msm)):
            p=ns.root/rel
            p.write_text(fn(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase418 atomic present discriminator: PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
