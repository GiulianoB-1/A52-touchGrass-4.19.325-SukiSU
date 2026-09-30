#!/usr/bin/env python3
from __future__ import annotations
import argparse, re
from pathlib import Path

MARK = "A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
ATOMIC = Path("drivers/gpu/drm/drm_atomic_helper.c")
DRM = Path("drivers/a52_display/msm/dsi/dsi_drm.c")
MSM = Path("drivers/a52_display/msm/msm_drv.c")
ENCODER = Path("drivers/a52_display/msm/sde/sde_encoder.c")


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

    # Make the old Phase280 retention latch observable instead of silently
    # swallowing the encoder/modeset diagnostics we care about.
    retained_decl = "static atomic_t a52_r280_retained = ATOMIC_INIT(0);\n"
    retained_extra = r'''static atomic_t a52_r280_retained = ATOMIC_INIT(0);
static atomic_t a52_p418_gate_drop = ATOMIC_INIT(0);

unsigned int a52_ackfr_phase418_retained(void)
{
	return (unsigned int)atomic_read(&a52_r280_retained);
}
EXPORT_SYMBOL_GPL(a52_ackfr_phase418_retained);

static void a52_p418_retention_drop_note(const char *fmt)
{
	unsigned int n;

	if (!fmt)
		return;
	n = (unsigned int)atomic_inc_return(&a52_p418_gate_drop);
	if (n <= 16U || !(n & 127U))
		a52_ackfr_record("P418 GATE n=%u ret=1 f=%.9s", n, fmt);
}
'''
    text = one(text, retained_decl, retained_extra, "retention diagnostics")

    retain_old = '''if (unlikely(atomic_read(&a52_r280_retained)) &&
	    !a52_r382_diag_format(fmt) &&
'''
    retain_new = '''if (unlikely(atomic_read(&a52_r280_retained)) &&
	    strncmp(fmt, "P418", 4) &&
	    !a52_r382_diag_format(fmt) &&
'''
    text = one(text, retain_old, retain_new, "allow P418 after retention")

    retain_return = '''	      ((fmt[5] == '3' && fmt[6] == '3' &&
	        (fmt[7] == '1' || fmt[7] == '2' || fmt[7] == '9')) ||
	       (fmt[5] == '3' && fmt[6] == '4' && fmt[7] == '0'))))
		return;
'''
    retain_return_new = '''	      ((fmt[5] == '3' && fmt[6] == '3' &&
	        (fmt[7] == '1' || fmt[7] == '2' || fmt[7] == '9')) ||
	       (fmt[5] == '3' && fmt[6] == '4' && fmt[7] == '0')))) {
		if (!strncmp(fmt, "P276 387", 9) ||
		    !strncmp(fmt, "P276 394", 9))
			a52_p418_retention_drop_note(fmt);
		return;
	}
'''
    text = one(text, retain_return, retain_return_new, "retention drop note")

    boot_old = '''	a52_ackfr_record("P414 BOOT id=%llu disk=%u ram=%u",
		(unsigned long long)a52_p414_boot_id,
		A52_P414_DISK_CAPACITY, A52_P414_RAM_CAPACITY);
'''
    boot_new = boot_old + '''	a52_ackfr_record("P418 CTRL boot ret=%u",
		a52_ackfr_phase418_retained());
	a52_ackfr_record("P276 394T boot=1 ret=%u",
		a52_ackfr_phase418_retained());
'''
    text = one(text, boot_old, boot_new, "boot positive control")

    # Phase417 proved connector/mode enumeration is healthy. Retire the very
    # high-volume Phase269 property/UAPI dump from both admission gates.
    lines = text.splitlines(keepends=True)
    kept = []
    removed = 0
    for line in lines:
        if (line.lstrip().startswith('strncmp(fmt, "P269", 4) &&')):
            removed += 1
            continue
        kept.append(line)
    text = ''.join(kept)
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
extern unsigned int a52_ackfr_phase418_retained(void);

static bool a52_p418_modeset_log(unsigned int n, int ret)
{
	(void)ret;
	return n <= 16U || !(n & 127U);
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
	unsigned int retained = a52_ackfr_phase418_retained();

	if (n == 1U) {
		a52_ackfr_record("P418 CTRL atom ret=%u", retained);
		a52_ackfr_record("P276 394T atom=1 ret=%u", retained);
	}

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
		"P418 MS n=%u r=%d rt=%u p=%d t=%d c=%.12s am=%u au=%u cr=%d en=%u ac=%u mc=%u cc=%u cm=%x",
		n, ret, retained, current->pid, current->tgid, current->comm,
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

    entry_anchor = '''\ta52_p387_n = (unsigned int)atomic_inc_return(&a52_p387_modeset_seq);
'''
    entry_new = entry_anchor + '''\tif (a52_p387_n == 1U) {
\t\tunsigned int a52_p418_ret = a52_ackfr_phase418_retained();
\t\ta52_ackfr_record("P418 CTRL pre ret=%u", a52_p418_ret);
\t\ta52_ackfr_record("P276 394T pre=1 ret=%u", a52_p418_ret);
\t}
'''
    text = one(text, entry_anchor, entry_new, "atomic entry positive control")

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
	bool test = !!(flags & DRM_MODE_ATOMIC_TEST_ONLY);

	if (rc < 0)
		atomic_inc(&a52_p418_atomic_err);
	if (log || !test)
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



ENCODER_HELPER = r'''
/* A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1
 * Encoder atomic-check discriminator. First 16 calls record every sub-step;
 * later calls preserve failures and 1/128 summaries.
 */
extern unsigned int a52_ackfr_phase418_retained(void);
static atomic_t a52_p418_enc_seq = ATOMIC_INIT(0);
static atomic_t a52_p418_enc_fail[11];

static void a52_p418_enc_note(unsigned int n, unsigned int step, int rc,
		struct drm_encoder *drm_enc, struct drm_crtc_state *crtc_state,
		struct sde_connector_state *sde_conn_state)
{
	struct msm_display_topology *t = NULL;

	if (step < ARRAY_SIZE(a52_p418_enc_fail) && rc)
		atomic_inc(&a52_p418_enc_fail[step]);
	if (sde_conn_state)
		t = &sde_conn_state->mode_info.topology;

	if (n <= 16U || !(n & 127U))
		a52_ackfr_record(
			"P418 E n=%u s=%u r=%d id=%u rt=%u t=%d m=%u a=%u c=%u lm=%u ce=%u in=%u",
			n, step, rc, drm_enc ? drm_enc->base.id : 0U,
			a52_ackfr_phase418_retained(), current->tgid,
			crtc_state ? crtc_state->mode_changed : 9U,
			crtc_state ? crtc_state->active_changed : 9U,
			crtc_state ? crtc_state->connectors_changed : 9U,
			t ? t->num_lm : 99U, t ? t->num_enc : 99U,
			t ? t->num_intf : 99U);

	if (!(n & 127U) && step == 10U) {
		a52_ackfr_record("P418 ES0 n=%u f0=%d f1=%d f2=%d f3=%d f4=%d f5=%d",
			n, atomic_read(&a52_p418_enc_fail[0]),
			atomic_read(&a52_p418_enc_fail[1]),
			atomic_read(&a52_p418_enc_fail[2]),
			atomic_read(&a52_p418_enc_fail[3]),
			atomic_read(&a52_p418_enc_fail[4]),
			atomic_read(&a52_p418_enc_fail[5]));
		a52_ackfr_record("P418 ES1 n=%u f6=%d f7=%d f8=%d f9=%d f10=%d",
			n, atomic_read(&a52_p418_enc_fail[6]),
			atomic_read(&a52_p418_enc_fail[7]),
			atomic_read(&a52_p418_enc_fail[8]),
			atomic_read(&a52_p418_enc_fail[9]),
			atomic_read(&a52_p418_enc_fail[10]));
	}
}
'''


def patch_encoder(text: str) -> str:
    if MARK in text:
        return text

    reserve_sig = '''static int _sde_encoder_atomic_check_reserve(struct drm_encoder *drm_enc,
\tstruct drm_crtc_state *crtc_state,
\tstruct drm_connector_state *conn_state,
\tstruct sde_encoder_virt *sde_enc, struct sde_kms *sde_kms,
\tstruct sde_connector *sde_conn,
\tstruct sde_connector_state *sde_conn_state)
'''
    reserve_sig_new = '''static int _sde_encoder_atomic_check_reserve(struct drm_encoder *drm_enc,
\tstruct drm_crtc_state *crtc_state,
\tstruct drm_connector_state *conn_state,
\tstruct sde_encoder_virt *sde_enc, struct sde_kms *sde_kms,
\tstruct sde_connector *sde_conn,
\tstruct sde_connector_state *sde_conn_state,
\tunsigned int a52_p418_n)
'''
    text = one(text, reserve_sig, ENCODER_HELPER + "\n" + reserve_sig_new,
               "encoder helper and reserve signature")

    # Reserve sub-steps: mode-info, compression sanity, RM reserve,
    # topology update, connector info blob.
    text = one(text,
'''		ret = sde_connector_get_mode_info(&sde_conn->base,
				adj_mode, &sde_conn_state->mode_info);
		if (ret) {
''',
'''		ret = sde_connector_get_mode_info(&sde_conn->base,
				adj_mode, &sde_conn_state->mode_info);
		a52_p418_enc_note(a52_p418_n, 3U, ret, drm_enc,
			crtc_state, sde_conn_state);
		if (ret) {
''', "encoder mode-info step")

    text = one(text,
'''			ret = -EINVAL;
			return ret;
		}

		/* Reserve dynamic resources, indicating atomic_check phase */
''',
'''			ret = -EINVAL;
			a52_p418_enc_note(a52_p418_n, 4U, ret, drm_enc,
				crtc_state, sde_conn_state);
			return ret;
		}
		a52_p418_enc_note(a52_p418_n, 4U, 0, drm_enc,
			crtc_state, sde_conn_state);

		/* Reserve dynamic resources, indicating atomic_check phase */
''', "encoder compression step")

    text = one(text,
'''		ret = sde_rm_reserve(&sde_kms->rm, drm_enc, crtc_state,
			conn_state, true);
		if (ret) {
''',
'''		ret = sde_rm_reserve(&sde_kms->rm, drm_enc, crtc_state,
			conn_state, true);
		a52_p418_enc_note(a52_p418_n, 5U, ret, drm_enc,
			crtc_state, sde_conn_state);
		if (ret) {
''', "encoder RM reserve step")

    text = one(text,
'''		ret = sde_rm_update_topology(conn_state, topology);
		if (ret) {
''',
'''		ret = sde_rm_update_topology(conn_state, topology);
		a52_p418_enc_note(a52_p418_n, 6U, ret, drm_enc,
			crtc_state, sde_conn_state);
		if (ret) {
''', "encoder topology update step")

    text = one(text,
'''		ret = sde_connector_set_blob_data(conn_state->connector,
				conn_state,
				CONNECTOR_PROP_SDE_INFO);
		if (ret) {
''',
'''		ret = sde_connector_set_blob_data(conn_state->connector,
				conn_state,
				CONNECTOR_PROP_SDE_INFO);
		a52_p418_enc_note(a52_p418_n, 7U, ret, drm_enc,
			crtc_state, sde_conn_state);
		if (ret) {
''', "encoder blob step")

    # Main encoder check sequence.
    start, end = function_bounds(text, "sde_encoder_virt_atomic_check")
    fn = text[start:end]
    fn = one(fn,
'''	int ret = 0;
	bool qsync_dirty = false, has_modeset = false;
''',
'''	int ret = 0;
	unsigned int a52_p418_n;
	bool qsync_dirty = false, has_modeset = false;
''', "encoder local sequence")
    fn = one(fn,
'''	sde_enc = to_sde_encoder_virt(drm_enc);
''',
'''	a52_p418_n = (unsigned int)atomic_inc_return(&a52_p418_enc_seq);
	sde_enc = to_sde_encoder_virt(drm_enc);
''', "encoder sequence assign")

    fn = one(fn,
'''	ret = _sde_encoder_atomic_check_phys_enc(sde_enc, crtc_state,
			conn_state);
	if (ret)
		return ret;
''',
'''	ret = _sde_encoder_atomic_check_phys_enc(sde_enc, crtc_state,
			conn_state);
	a52_p418_enc_note(a52_p418_n, 0U, ret, drm_enc,
		crtc_state, sde_conn_state);
	if (ret)
		return ret;
''', "encoder phys step")

    fn = one(fn,
'''	ret = _sde_encoder_atomic_check_pu_roi(sde_enc, crtc_state,
			conn_state, sde_conn_state, sde_crtc_state);
	if (ret)
		return ret;
''',
'''	ret = _sde_encoder_atomic_check_pu_roi(sde_enc, crtc_state,
			conn_state, sde_conn_state, sde_crtc_state);
	a52_p418_enc_note(a52_p418_n, 1U, ret, drm_enc,
		crtc_state, sde_conn_state);
	if (ret)
		return ret;
''', "encoder PU ROI step")

    fn = one(fn,
'''	ret = sde_connector_set_old_topology_name(conn_state, old_top);
	if (ret)
		return ret;
''',
'''	ret = sde_connector_set_old_topology_name(conn_state, old_top);
	a52_p418_enc_note(a52_p418_n, 2U, ret, drm_enc,
		crtc_state, sde_conn_state);
	if (ret)
		return ret;
''', "encoder old topology step")

    fn = one(fn,
'''	ret = _sde_encoder_atomic_check_reserve(drm_enc, crtc_state,
			conn_state, sde_enc, sde_kms, sde_conn, sde_conn_state);
	if (ret)
		return ret;
''',
'''	ret = _sde_encoder_atomic_check_reserve(drm_enc, crtc_state,
			conn_state, sde_enc, sde_kms, sde_conn, sde_conn_state,
			a52_p418_n);
	a52_p418_enc_note(a52_p418_n, 8U, ret, drm_enc,
		crtc_state, sde_conn_state);
	if (ret)
		return ret;
''', "encoder reserve aggregate step")

    fn = one(fn,
'''	ret = sde_connector_roi_v1_check_roi(conn_state);
	if (ret) {
''',
'''	ret = sde_connector_roi_v1_check_roi(conn_state);
	a52_p418_enc_note(a52_p418_n, 9U, ret, drm_enc,
		crtc_state, sde_conn_state);
	if (ret) {
''', "encoder ROI v1 step")

    fn = one(fn,
'''	if (has_modeset && qsync_dirty &&
		!msm_is_mode_seamless_vrr(adj_mode)) {
		SDE_ERROR("invalid qsync during modeset\\n");
		return -EINVAL;
	}

	SDE_EVT32(DRMID(drm_enc), adj_mode->flags, adj_mode->private_flags);

	return ret;
''',
'''	if (has_modeset && qsync_dirty &&
		!msm_is_mode_seamless_vrr(adj_mode)) {
		SDE_ERROR("invalid qsync during modeset\\n");
		ret = -EINVAL;
		a52_p418_enc_note(a52_p418_n, 10U, ret, drm_enc,
			crtc_state, sde_conn_state);
		return ret;
	}
	a52_p418_enc_note(a52_p418_n, 10U, 0, drm_enc,
		crtc_state, sde_conn_state);

	SDE_EVT32(DRMID(drm_enc), adj_mode->flags, adj_mode->private_flags);

	return ret;
''', "encoder qsync step")

    text = text[:start] + fn + text[end:]
    text += f'\n/* {MARK}: encoder atomic sub-step discriminator. */\n'
    return text


def validate(root: Path) -> None:
    rec=(root/REC).read_text(errors="replace")
    atomic=(root/ATOMIC).read_text(errors="replace")
    drm=(root/DRM).read_text(errors="replace")
    msm=(root/MSM).read_text(errors="replace")
    enc=(root/ENCODER).read_text(errors="replace")

    for tok in (
        MARK, 'strncmp(fmt, "P418", 4)', "P418 APX t=%lu",
        "a52_p418_apex_sample(boot_s);", "P418 GATE n=%u ret=1",
        "P418 CTRL boot ret=%u", "P276 394T boot=1 ret=%u",
        "a52_ackfr_phase418_retained",
    ):
        if tok not in rec:
            raise SystemExit("Phase418 recorder token missing: " + tok)
    if 'strncmp(fmt, "P269", 4)' in rec:
        raise SystemExit("Phase418 high-volume P269 admission still active")

    for tok in (
        MARK, "P418 MS n=%u r=%d rt=%u", "a52_p418_modeset_result",
        "P418 CTRL pre ret=%u", "P276 394T pre=1 ret=%u",
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

    for tok in (
        MARK, "P418 E n=%u s=%u r=%d id=%u rt=%u t=%d", "P418 ES0 n=%u",
        "_sde_encoder_atomic_check_phys_enc", "_sde_encoder_atomic_check_pu_roi",
        "sde_connector_set_old_topology_name", "sde_connector_get_mode_info",
        "sde_rm_reserve", "sde_rm_update_topology",
        "sde_connector_roi_v1_check_roi", "CONNECTOR_PROP_QSYNC_MODE",
    ):
        if tok not in enc:
            raise SystemExit("Phase418 encoder token missing: " + tok)


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    ns=ap.parse_args()
    for rel in (REC,ATOMIC,DRM,MSM,ENCODER):
        if not (ns.root/rel).is_file():
            raise SystemExit("Phase418 source missing: "+str(rel))
    if not ns.check_only:
        for rel,fn in ((REC,patch_rec),(ATOMIC,patch_atomic),(DRM,patch_drm),(MSM,patch_msm),(ENCODER,patch_encoder)):
            p=ns.root/rel
            p.write_text(fn(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase418 atomic present discriminator: PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
