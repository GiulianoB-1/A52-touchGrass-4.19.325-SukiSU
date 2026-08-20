#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "A52_PHASE285_LATCHED_CLOCK_CHAIN_VALUES_V1"

BLOCK = r'''
/* A52_PHASE285_LATCHED_CLOCK_CHAIN_VALUES_V1
 * Phase284 proved the upstream DRM mode clock is non-zero, but its early
 * P276 284O/M/C/P records were overwritten by the late P269 frontier stream.
 * Preserve the *exact varargs* already passed to those Phase284 records before
 * text packing/truncation.  Keep first + last observation for every chain
 * stage, attach a monotonically increasing causal sequence, and replay the
 * typed raw values during the 180..300 second late-frontier window.
 *
 * This is observation/retention only: no clock/parent/register operation is
 * added, removed, retried, reordered or re-read by Phase285.
 *
 * P285 replay grammar (all numeric values are raw hexadecimal):
 *   P285 S<tag>a n=<count> q=<seq> 0=<v0> 1=<v1>  (single observation)
 *   P285 F<tag>... / L<tag>...                    (first / last)
 * Values 2..4 are on the 'b' record and 5..7 on the 'c' record.  The n field
 * says how many positional values are meaningful.  Sort samples by q to
 * reconstruct the original causal order.
 */
#define A52_P285_SLOT_COUNT 19U
#define A52_P285_VALUE_COUNT 8U

struct a52_p285_sample {
	u64 seq;
	u64 v[A52_P285_VALUE_COUNT];
	u8 n;
	bool valid;
};

struct a52_p285_slot {
	struct a52_p285_sample first;
	struct a52_p285_sample last;
};

enum a52_p285_slot_id {
	A52_P285_O0,
	A52_P285_O1,
	A52_P285_M0,
	A52_P285_M1,
	A52_P285_M2,
	A52_P285_M3,
	A52_P285_M4,
	A52_P285_M5,
	A52_P285_M6,
	A52_P285_M7,
	A52_P285_M8,
	A52_P285_C0,
	A52_P285_C1,
	A52_P285_C2,
	A52_P285_C3,
	A52_P285_P0,
	A52_P285_P1,
	A52_P285_P2,
	A52_P285_P3,
};

static const char * const a52_p285_tag[A52_P285_SLOT_COUNT] = {
	"O0", "O1", "M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7",
	"M8", "C0", "C1", "C2", "C3", "P0", "P1", "P2", "P3",
};
static struct a52_p285_slot a52_p285_slots[A52_P285_SLOT_COUNT];
static DEFINE_SPINLOCK(a52_p285_lock);
static atomic64_t a52_p285_causal_seq = ATOMIC64_INIT(0);

static void a52_p285_latch(unsigned int slot, unsigned int n, const u64 *v)
{
	struct a52_p285_sample sample;
	unsigned long flags;

	if (slot >= A52_P285_SLOT_COUNT || !v || !n || n > A52_P285_VALUE_COUNT)
		return;
	memset(&sample, 0, sizeof(sample));
	sample.seq = (u64)atomic64_inc_return(&a52_p285_causal_seq);
	sample.n = (u8)n;
	sample.valid = true;
	memcpy(sample.v, v, n * sizeof(v[0]));

	spin_lock_irqsave(&a52_p285_lock, flags);
	if (!a52_p285_slots[slot].first.valid)
		a52_p285_slots[slot].first = sample;
	a52_p285_slots[slot].last = sample;
	spin_unlock_irqrestore(&a52_p285_lock, flags);
}

static void a52_p285_capture_fmt(const char *fmt, va_list src)
{
	va_list ap;
	u64 v[A52_P285_VALUE_COUNT] = { 0 };
	unsigned int slot = A52_P285_SLOT_COUNT;
	unsigned int n = 0;

	if (!fmt || strncmp(fmt, "P276 284", 8))
		return;
	va_copy(ap, src);
	if (!strcmp(fmt, "P276 284O0 c=%d y=%u in=%u l=%u b=%u d=%u")) {
		slot = A52_P285_O0; n = 6;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned int); v[2] = va_arg(ap, unsigned int);
		v[3] = va_arg(ap, unsigned int); v[4] = va_arg(ap, unsigned int);
		v[5] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P276 284O1 bit=%llx lane=%llx b=%llx i=%llx p=%llx")) {
		slot = A52_P285_O1; n = 5;
		v[0] = va_arg(ap, unsigned long long); v[1] = va_arg(ap, unsigned long long);
		v[2] = va_arg(ap, unsigned long long); v[3] = va_arg(ap, unsigned long long);
		v[4] = va_arg(ap, unsigned long long);
	} else if (!strcmp(fmt, "P276 284M0 c=%u m=%d b=%llx p=%llx i=%llx e=%llx")) {
		slot = A52_P285_M0; n = 6;
		v[0] = va_arg(ap, unsigned int); v[1] = (u64)(s64)va_arg(ap, int);
		v[2] = va_arg(ap, unsigned long long); v[3] = va_arg(ap, unsigned long long);
		v[4] = va_arg(ap, unsigned long long); v[5] = va_arg(ap, unsigned long long);
	} else if (!strcmp(fmt, "P276 284M1 c=%u req=%llx rc=%d a=%lx p=%lx")) {
		slot = A52_P285_M1; n = 5;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned long long);
		v[2] = (u64)(s64)va_arg(ap, int); v[3] = va_arg(ap, unsigned long);
		v[4] = va_arg(ap, unsigned long);
	} else if (!strcmp(fmt, "P276 284M2 c=%u rb=%llx ri=%llx rc=%d ab=%lx pb=%lx ai=%lx")) {
		slot = A52_P285_M2; n = 7;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned long long);
		v[2] = va_arg(ap, unsigned long long); v[3] = (u64)(s64)va_arg(ap, int);
		v[4] = va_arg(ap, unsigned long); v[5] = va_arg(ap, unsigned long);
		v[6] = va_arg(ap, unsigned long);
	} else if (!strcmp(fmt, "P276 284M3 cb=%lx bp=%lx tb=%lx cp=%lx pp=%lx tp=%lx")) {
		slot = A52_P285_M3; n = 6;
		v[0] = va_arg(ap, unsigned long); v[1] = va_arg(ap, unsigned long);
		v[2] = va_arg(ap, unsigned long); v[3] = va_arg(ap, unsigned long);
		v[4] = va_arg(ap, unsigned long); v[5] = va_arg(ap, unsigned long);
	} else if (!strcmp(fmt, "P276 284M4 rc=%d cb=%lx bp=%lx cp=%lx pp=%lx")) {
		slot = A52_P285_M4; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned long);
		v[2] = va_arg(ap, unsigned long); v[3] = va_arg(ap, unsigned long);
		v[4] = va_arg(ap, unsigned long);
	} else if (!strcmp(fmt, "P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx")) {
		slot = A52_P285_M5; n = 4;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned long long);
		v[2] = va_arg(ap, unsigned long long); v[3] = va_arg(ap, unsigned long long);
	} else if (!strcmp(fmt, "P276 284M6 c=%d req=%llx rc=%d a=%lx p=%lx")) {
		slot = A52_P285_M6; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned long long);
		v[2] = (u64)(s64)va_arg(ap, int); v[3] = va_arg(ap, unsigned long);
		v[4] = va_arg(ap, unsigned long);
	} else if (!strcmp(fmt, "P276 284M7 c=%d req=%llx rc=%d a=%lx p=%lx")) {
		slot = A52_P285_M7; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned long long);
		v[2] = (u64)(s64)va_arg(ap, int); v[3] = va_arg(ap, unsigned long);
		v[4] = va_arg(ap, unsigned long);
	} else if (!strcmp(fmt, "P276 284M8 c=%d req=%llx rc=%d a=%lx p=%lx")) {
		slot = A52_P285_M8; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned long long);
		v[2] = (u64)(s64)va_arg(ap, int); v[3] = va_arg(ap, unsigned long);
		v[4] = va_arg(ap, unsigned long);
	} else if (!strcmp(fmt, "P276 284C0 q=%u %x %x %x %x")) {
		slot = A52_P285_C0; n = 5;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
		v[4] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P276 284C1 q=%u %x %x %x %x")) {
		slot = A52_P285_C1; n = 5;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
		v[4] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P276 284C2 q=%u %x %x %x %x %x %x")) {
		slot = A52_P285_C2; n = 7;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
		v[4] = va_arg(ap, unsigned int); v[5] = va_arg(ap, unsigned int);
		v[6] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P276 284C3 q=%u e=%x")) {
		slot = A52_P285_C3; n = 2;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P276 284P0 q=%u %u %x %x %x %x")) {
		slot = A52_P285_P0; n = 6;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
		v[4] = va_arg(ap, unsigned int); v[5] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P276 284P1 q=%u %x %x %x %x %x %x")) {
		slot = A52_P285_P1; n = 7;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
		v[4] = va_arg(ap, unsigned int); v[5] = va_arg(ap, unsigned int);
		v[6] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P276 284P2 q=%u %x %x %x %x %x %x")) {
		slot = A52_P285_P2; n = 7;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
		v[4] = va_arg(ap, unsigned int); v[5] = va_arg(ap, unsigned int);
		v[6] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P276 284P3 q=%u %x %x %x")) {
		slot = A52_P285_P3; n = 4;
		v[0] = va_arg(ap, unsigned int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
	}
	va_end(ap);
	if (slot < A52_P285_SLOT_COUNT)
		a52_p285_latch(slot, n, v);
}

static void a52_p285_emit_sample(const char *tag, char which,
		const struct a52_p285_sample *sample)
{
	if (!tag || !sample || !sample->valid)
		return;
	a52_ackfr_record("P285 %c%sa n=%u q=%llx 0=%llx 1=%llx", which, tag,
		sample->n, (unsigned long long)sample->seq,
		(unsigned long long)sample->v[0], (unsigned long long)sample->v[1]);
	if (sample->n > 2)
		a52_ackfr_record("P285 %c%sb 2=%llx 3=%llx 4=%llx", which, tag,
			(unsigned long long)sample->v[2], (unsigned long long)sample->v[3],
			(unsigned long long)sample->v[4]);
	if (sample->n > 5)
		a52_ackfr_record("P285 %c%sc 5=%llx 6=%llx 7=%llx", which, tag,
			(unsigned long long)sample->v[5], (unsigned long long)sample->v[6],
			(unsigned long long)sample->v[7]);
}

static void a52_p285_replay(unsigned long boot_s)
{
	struct a52_p285_slot snap;
	unsigned long flags;
	unsigned int i;

	a52_ackfr_record("P285 H t=%lu n=%llu", boot_s,
		(unsigned long long)atomic64_read(&a52_p285_causal_seq));
	for (i = 0; i < A52_P285_SLOT_COUNT; i++) {
		spin_lock_irqsave(&a52_p285_lock, flags);
		snap = a52_p285_slots[i];
		spin_unlock_irqrestore(&a52_p285_lock, flags);
		if (!snap.first.valid)
			continue;
		if (snap.first.seq == snap.last.seq) {
			a52_p285_emit_sample(a52_p285_tag[i], 'S', &snap.first);
			continue;
		}
		a52_p285_emit_sample(a52_p285_tag[i], 'F', &snap.first);
		a52_p285_emit_sample(a52_p285_tag[i], 'L', &snap.last);
	}
}
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase285: expected exactly one {label}, found {count}")
    return text.replace(old, new, 1)


def apply(root: Path, check_only: bool) -> None:
    rec = root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
    if not rec.is_file():
        raise SystemExit(f"Phase285: recorder source missing: {rec}")
    s = rec.read_text()
    if MARKER in s:
        required = [
            'strncmp(message, "P285 ", 5)',
            'strncmp(fmt, "P285", 4)',
            'a52_p285_capture_fmt(fmt, args);',
            'a52_p285_replay(boot_s);',
            'P285 %c%sa n=%u q=%llx 0=%llx 1=%llx',
            'P285 H t=%lu n=%llu',
        ]
        missing = [token for token in required if token not in s]
        if missing:
            raise SystemExit("Phase285: partial marker installation: " + ", ".join(missing))
        print("Phase285: latched clock-chain evidence already installed and complete")
        return
    if check_only:
        raise SystemExit("Phase285: marker not installed")

    s = replace_once(
        s,
        "static struct rs_control *a52_r179_rs;\n",
        "static struct rs_control *a52_r179_rs;\n\n" + BLOCK + "\n",
        "recorder-state insertion anchor",
    )
    s = replace_once(
        s,
        'return !strncmp(message, "P276 ", 5) ||',
        'return !strncmp(message, "P285 ", 5) ||\n       !strncmp(message, "P276 ", 5) ||',
        "post-capacity critical admission anchor",
    )
    s = replace_once(
        s,
        'if (strncmp(fmt, "P276", 4) &&',
        'if (strncmp(fmt, "P285", 4) &&\n    strncmp(fmt, "P276", 4) &&',
        "focused admission anchor",
    )
    s = replace_once(
        s,
        "\tva_start(args, fmt);\n\tvscnprintf(event.message, sizeof(event.message), fmt, args);\n\tva_end(args);",
        "\tva_start(args, fmt);\n\ta52_p285_capture_fmt(fmt, args);\n\tvscnprintf(event.message, sizeof(event.message), fmt, args);\n\tva_end(args);",
        "varargs capture anchor",
    )
    replay_anchor = '''\ta52_ackfr_record("P273 R t=%lu kd=%d nm=%d e=%d s=%d", boot_s,
\t\tatomic_read(&a52_r273_kd_status), atomic_read(&a52_r273_mode_count),
\t\tatomic_read(&a52_r273_encoder_id), atomic_read(&a52_r273_selected_id));
'''
    replay_new = replay_anchor + '''\tif (boot_s >= 180U && boot_s <= 300U)
\t\ta52_p285_replay(boot_s);
'''
    s = replace_once(s, replay_anchor, replay_new, "late-frontier replay anchor")
    rec.write_text(s)
    print("Phase285: installed exact-varargs clock-chain latch + late replay")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    apply(args.root, args.check_only)


if __name__ == "__main__":
    main()
