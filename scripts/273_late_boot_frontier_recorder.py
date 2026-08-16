#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root>")

ROOT = Path(sys.argv[1])
REC = ROOT / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
SDE = ROOT / "drivers/a52_display/msm/sde/sde_connector.c"
MARKER = "A52_PHASE273_LATE_BOOT_FRONTIER_RECORDER_V2"
SB_MARKER = "A52_PHASE273_P271_SB_DEDUP_V2"


def replace_once(text: str, old: str, new: str, what: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{what}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_recorder(text: str) -> str:
    if MARKER in text:
        return text

    text = replace_once(
        text,
        'return !strncmp(message, "P271 ", 5) ||\n',
        'return !strncmp(message, "P273 ", 5) ||\n'
        '       !strncmp(message, "P271 ", 5) ||\n',
        "critical P273 admission",
    )

    text = replace_once(
        text,
        'if (strncmp(fmt, "P271", 4) &&\n',
        'if (strncmp(fmt, "P273", 4) &&\n'
        '    strncmp(fmt, "P271", 4) &&\n',
        "format P273 admission",
    )

    # Sticky display/DRM frontier. This is fed before capacity suppression, so
    # later P273 summaries retain the last observed display stage even when the
    # raw DISP event itself has rolled out of ramoops.
    anchor = 'static int a52_r228_hex(const char *message, const char *key, int fallback)\n{\n'
    idx = text.find(anchor)
    if idx < 0:
        raise RuntimeError("missing a52_r228_hex anchor")
    block = r'''
/* A52_PHASE273_LATE_BOOT_FRONTIER_RECORDER_V2
 * Phase272 hardware proved the DRM mode-loss blocker is gone. The retained
 * window then became dominated by a repeated successful P271 best-encoder
 * callback. Keep a compact late-boot frontier that survives that traffic.
 * Observation only: no DRM, DSI, panel, Binder, scheduler or userspace return
 * value is modified.
 */
#define A52_R273_FRONTIER_END_S 900U
#define A52_R273_SCAN_FAST_MS 2000U
#define A52_R273_SCAN_SLOW_MS 5000U
#define A52_R273_SUMMARY_S 15U
#define A52_R273_TASK_COUNT 14U

static atomic_t a52_r273_disp_count = ATOMIC_INIT(0);
static atomic_t a52_r273_kd_status = ATOMIC_INIT(-61);
static atomic_t a52_r273_mode_count = ATOMIC_INIT(-1);
static atomic_t a52_r273_encoder_id = ATOMIC_INIT(-1);
static atomic_t a52_r273_selected_id = ATOMIC_INIT(-1);
static DEFINE_SPINLOCK(a52_r273_disp_lock);
static char a52_r273_last_disp_fn[40];
static char a52_r273_last_disp_kind = '?';

static void a52_r273_track_message(const char *message)
{
	unsigned long flags;
	const char *fn;
	char kind;
	size_t len;

	if (!message)
		return;

	if (!strncmp(message, "DISP ", 5)) {
		fn = strnstr(message, "fn=", A52_R179_MESSAGE_LEN);
		if (fn) {
			fn += 3;
			kind = !strncmp(message, "DISP enter ", 11) ? 'E' :
			       (!strncmp(message, "DISP exit ", 10) ? 'X' : '?');
			len = strcspn(fn, " ");
			spin_lock_irqsave(&a52_r273_disp_lock, flags);
			strscpy(a52_r273_last_disp_fn, fn,
				min_t(size_t, sizeof(a52_r273_last_disp_fn), len + 1));
			a52_r273_last_disp_kind = kind;
			spin_unlock_irqrestore(&a52_r273_disp_lock, flags);
		}
		atomic_inc(&a52_r273_disp_count);
		return;
	}

	if (!strncmp(message, "P271 V ", 7) &&
	    strnstr(message, "k=D", A52_R179_MESSAGE_LEN)) {
		atomic_set(&a52_r273_kd_status,
			a52_r228_dec(message, "st=", atomic_read(&a52_r273_kd_status)));
	} else if (!strncmp(message, "P269 CONN ", 10)) {
		atomic_set(&a52_r273_mode_count,
			a52_r228_dec(message, "nm=", atomic_read(&a52_r273_mode_count)));
		atomic_set(&a52_r273_encoder_id,
			a52_r228_dec(message, "enc=", atomic_read(&a52_r273_encoder_id)));
	} else if (!strncmp(message, "P271 G ", 7)) {
		atomic_set(&a52_r273_selected_id,
			a52_r228_dec(message, "sel=", atomic_read(&a52_r273_selected_id)));
	}
}

'''
    text = text[:idx] + block + text[idx:]

    text = replace_once(
        text,
        '\ta52_r230_journal_message(event.message);\n'
        '\ta52_r228_track_message(event.message);\n'
        '\tcritical = a52_r179_is_critical_message(event.message);\n',
        '\ta52_r230_journal_message(event.message);\n'
        '\ta52_r228_track_message(event.message);\n'
        '\ta52_r273_track_message(event.message);\n'
        '\tcritical = a52_r179_is_critical_message(event.message);\n',
        "frontier tracker call",
    )

    # Sparse userspace process frontier. system_server and SystemUI are zygote
    # children, so scanning task identity is intentional; exec-only tracing
    # would miss their lifecycle.
    anchor = 'static atomic_t a52_r179_heartbeat_count = ATOMIC_INIT(0);\n'
    block = r'''
enum a52_r273_task_id {
	A52_R273_INIT = 0,
	A52_R273_SM,
	A52_R273_HSM,
	A52_R273_VSM,
	A52_R273_VOLD,
	A52_R273_KEYSTORE,
	A52_R273_SF,
	A52_R273_Z64,
	A52_R273_Z32,
	A52_R273_SYSTEM_SERVER,
	A52_R273_BOOTANIM,
	A52_R273_SYSTEMUI,
	A52_R273_NETD,
	A52_R273_AUDIO,
};

struct a52_r273_task_sample {
	pid_t pid;
	pid_t tgid;
	pid_t ppid;
	char comm[TASK_COMM_LEN];
};

static const char * const a52_r273_task_key[A52_R273_TASK_COUNT] = {
	"I", "SM", "HSM", "VSM", "V", "K", "SF",
	"Z64", "Z32", "SS", "BA", "UI", "N", "A",
};
static pid_t a52_r273_last_pid[A52_R273_TASK_COUNT];
static u32 a52_r273_ever_mask;
static u32 a52_r273_gone_mask;
static unsigned int a52_r273_last_summary_bucket;

static int a52_r273_task_id(const char *comm)
{
	if (!comm)
		return -1;
	if (!strcmp(comm, "init"))
		return A52_R273_INIT;
	if (!strcmp(comm, "servicemanager"))
		return A52_R273_SM;
	if (!strncmp(comm, "hwservicemanage", 15))
		return A52_R273_HSM;
	if (!strncmp(comm, "vndservicemanag", 15))
		return A52_R273_VSM;
	if (!strcmp(comm, "vold"))
		return A52_R273_VOLD;
	if (!strcmp(comm, "keystore2"))
		return A52_R273_KEYSTORE;
	if (!strcmp(comm, "surfaceflinger"))
		return A52_R273_SF;
	if (!strcmp(comm, "zygote64"))
		return A52_R273_Z64;
	if (!strcmp(comm, "zygote"))
		return A52_R273_Z32;
	if (!strcmp(comm, "system_server"))
		return A52_R273_SYSTEM_SERVER;
	if (!strcmp(comm, "bootanimation"))
		return A52_R273_BOOTANIM;
	if (!strncmp(comm, "com.android.sys", 15))
		return A52_R273_SYSTEMUI;
	if (!strcmp(comm, "netd"))
		return A52_R273_NETD;
	if (!strcmp(comm, "audioserver"))
		return A52_R273_AUDIO;
	return -1;
}

static void a52_r273_scan_tasks(unsigned long boot_s)
{
	struct a52_r273_task_sample now[A52_R273_TASK_COUNT];
	struct task_struct *task;
	unsigned int now_mask = 0;
	unsigned int bucket;
	unsigned long flags;
	char comm[TASK_COMM_LEN];
	char disp_fn[40];
	char disp_kind;
	pid_t zpid;
	unsigned int i;
	int id;

	memset(now, 0, sizeof(now));
	rcu_read_lock();
	for_each_process(task) {
		get_task_comm(comm, task);
		id = a52_r273_task_id(comm);
		if (id < 0 || now[id].pid)
			continue;
		now[id].pid = task_pid_nr(task);
		now[id].tgid = task_tgid_nr(task);
		now[id].ppid = task_ppid_nr(task);
		strscpy(now[id].comm, comm, sizeof(now[id].comm));
		now_mask |= BIT(id);
	}
	rcu_read_unlock();

	for (i = 0; i < A52_R273_TASK_COUNT; i++) {
		if (now[i].pid && !a52_r273_last_pid[i]) {
			a52_ackfr_record("P273 U + k=%s p=%d t=%d pp=%d c=%.15s",
				a52_r273_task_key[i], now[i].pid, now[i].tgid,
				now[i].ppid, now[i].comm);
			a52_r273_ever_mask |= BIT(i);
		} else if (!now[i].pid && a52_r273_last_pid[i]) {
			a52_ackfr_record("P273 U - k=%s p=%d",
				a52_r273_task_key[i], a52_r273_last_pid[i]);
			a52_r273_gone_mask |= BIT(i);
		} else if (now[i].pid && a52_r273_last_pid[i] != now[i].pid) {
			a52_ackfr_record("P273 U R k=%s o=%d n=%d pp=%d",
				a52_r273_task_key[i], a52_r273_last_pid[i],
				now[i].pid, now[i].ppid);
			a52_r273_ever_mask |= BIT(i);
			a52_r273_gone_mask |= BIT(i);
		}
		a52_r273_last_pid[i] = now[i].pid;
	}

	bucket = (unsigned int)(boot_s / A52_R273_SUMMARY_S);
	if (!bucket || bucket == a52_r273_last_summary_bucket)
		return;
	a52_r273_last_summary_bucket = bucket;
	zpid = now[A52_R273_Z64].pid ? now[A52_R273_Z64].pid :
		now[A52_R273_Z32].pid;

	a52_ackfr_record("P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d",
		boot_s, now_mask, a52_r273_ever_mask, a52_r273_gone_mask,
		now[A52_R273_SF].pid, zpid, now[A52_R273_SYSTEM_SERVER].pid,
		now[A52_R273_SYSTEMUI].pid, now[A52_R273_BOOTANIM].pid);

	spin_lock_irqsave(&a52_r273_disp_lock, flags);
	strscpy(disp_fn, a52_r273_last_disp_fn, sizeof(disp_fn));
	disp_kind = a52_r273_last_disp_kind;
	spin_unlock_irqrestore(&a52_r273_disp_lock, flags);
	a52_ackfr_record("P273 D t=%lu n=%d k=%c fn=%.36s", boot_s,
		atomic_read(&a52_r273_disp_count), disp_kind,
		disp_fn[0] ? disp_fn : "none");
	a52_ackfr_record("P273 R t=%lu kd=%d nm=%d e=%d s=%d", boot_s,
		atomic_read(&a52_r273_kd_status), atomic_read(&a52_r273_mode_count),
		atomic_read(&a52_r273_encoder_id), atomic_read(&a52_r273_selected_id));
}

static void a52_r273_frontier_fn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_r273_frontier_work, a52_r273_frontier_fn);

static void a52_r273_frontier_fn(struct work_struct *work)
{
	unsigned long boot_s = jiffies_to_msecs(jiffies) / 1000U;
	unsigned int delay_ms;

	a52_r273_scan_tasks(boot_s);
	if (boot_s >= A52_R273_FRONTIER_END_S)
		return;
	delay_ms = boot_s < 300U ? A52_R273_SCAN_FAST_MS : A52_R273_SCAN_SLOW_MS;
	schedule_delayed_work(&a52_r273_frontier_work,
		msecs_to_jiffies(delay_ms));
}

'''
    text = replace_once(text, anchor, block + anchor, "late task frontier block")

    text = replace_once(
        text,
        '\tschedule_delayed_work(&a52_r179_heartbeat_work,\n'
        '\t\tmsecs_to_jiffies(A52_R179_HEARTBEAT_INTERVAL_MS));\n'
        '\tpr_info("phase199 triple-copy RS+CRC32C recorder enabled stored=%llu dropped=%llu\\n",\n',
        '\tschedule_delayed_work(&a52_r179_heartbeat_work,\n'
        '\t\tmsecs_to_jiffies(A52_R179_HEARTBEAT_INTERVAL_MS));\n'
        '\tschedule_delayed_work(&a52_r273_frontier_work,\n'
        '\t\tmsecs_to_jiffies(A52_R273_SCAN_FAST_MS));\n'
        '\ta52_ackfr_record("P273 START h=%u q=%u/%u s=%u",\n'
        '\t\tA52_R273_FRONTIER_END_S, A52_R273_SCAN_FAST_MS,\n'
        '\t\tA52_R273_SCAN_SLOW_MS, A52_R273_SUMMARY_S);\n'
        '\tpr_info("phase199 triple-copy RS+CRC32C recorder enabled stored=%llu dropped=%llu\\n",\n',
        "late-init frontier schedule",
    )
    return text


def patch_sde(text: str) -> str:
    if SB_MARKER in text:
        return text

    anchor = 'static atomic_t a52_r179_conn_commit_count = ATOMIC_INIT(0);\n'
    block = r'''
/* A52_PHASE273_P271_SB_DEDUP_V2
 * Phase272 hardware produced about 1000 identical successful best-encoder
 * records. Keep the first 8, every state transition, then one of each 128
 * steady-state calls. The returned encoder is untouched.
 */
static atomic_t a52_r273_sb_calls = ATOMIC_INIT(0);
static atomic_t a52_r273_sb_last_sig = ATOMIC_INIT(-1);

'''
    text = replace_once(text, anchor, anchor + block, "P271 SB sampler globals")

    old = '''\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid))\n\t\ta52_ackfr_record("P271 SB id=%u eid=%u stp=%u best=%u",\n\t\t\tconnector->base.id, c_conn->encoder ? c_conn->encoder->base.id : 0,\n\t\t\tconnector->state ? 1 : 0,\n\t\t\t(connector->state && connector->state->best_encoder) ?\n\t\t\t\tconnector->state->best_encoder->base.id : 0);\n'''
    new = '''\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid)) {\n\t\tu32 eid = c_conn->encoder ? c_conn->encoder->base.id : 0;\n\t\tu32 stp = connector->state ? 1 : 0;\n\t\tu32 best = (connector->state && connector->state->best_encoder) ?\n\t\t\tconnector->state->best_encoder->base.id : 0;\n\t\tint call = atomic_inc_return(&a52_r273_sb_calls);\n\t\tint sig = ((eid & 0x7fff) << 16) | ((best & 0x7fff) << 1) | stp;\n\t\tint prev = atomic_xchg(&a52_r273_sb_last_sig, sig);\n\n\t\tif (call <= 8 || sig != prev || !(call & 0x7f))\n\t\t\ta52_ackfr_record("P271 SB id=%u eid=%u stp=%u best=%u",\n\t\t\t\tconnector->base.id, eid, stp, best);\n\t}\n'''
    text = replace_once(text, old, new, "P271 SB hot callback")
    return text


rec_text = REC.read_text()
sde_text = SDE.read_text()
new_rec = patch_recorder(rec_text)
new_sde = patch_sde(sde_text)
REC.write_text(new_rec)
SDE.write_text(new_sde)

# Fail closed if the patch did not produce the exact intended observable-only
# markers or accidentally duplicated either patch.
checks = [
    (new_rec.count(MARKER) == 1, "recorder marker count"),
    (new_sde.count(SB_MARKER) == 1, "SDE marker count"),
    ('return !strncmp(message, "P273 ", 5)' in new_rec, "P273 critical admission"),
    ('P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d' in new_rec,
     "process summary"),
    ('P273 D t=%lu n=%d k=%c fn=%.36s' in new_rec, "display frontier"),
    ('P273 R t=%lu kd=%d nm=%d e=%d s=%d' in new_rec, "DRM frontier"),
    ('call <= 8 || sig != prev || !(call & 0x7f)' in new_sde, "SB sampler"),
    ('return c_conn->encoder;' in new_sde, "encoder return preserved"),
]
for ok, what in checks:
    if not ok:
        raise RuntimeError(f"Phase273 verification failed: {what}")

print("Phase273 late-boot frontier recorder patch: PASS")
