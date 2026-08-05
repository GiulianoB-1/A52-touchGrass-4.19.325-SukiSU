/* A52_PHASE228_TRI_TRACK_SNAPSHOT */
#define A52_R179_MESSAGE_LEN 90U

static void a52_r228_track_message(const char *message)
{
}

static void a52_r179_write_control(const char *kind)
{
}

void a52_ackfr_record(const char *fmt, ...)
{
	struct a52_r179_event event;
	va_list args;

	va_start(args, fmt);
	vscnprintf(event.message, sizeof(event.message), fmt, args);
	va_end(args);
	a52_r228_track_message(event.message);
}

static atomic_t a52_r179_heartbeat_count = ATOMIC_INIT(0);

static void a52_r179_heartbeat_fn(struct work_struct *work)
{
	unsigned int tick;

	tick = (unsigned int)atomic_inc_return(&a52_r179_heartbeat_count);
	a52_r226_task_snapshot(tick);
}
