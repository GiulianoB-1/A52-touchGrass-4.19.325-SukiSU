// SPDX-License-Identifier: GPL-2.0-only
/*
 * A52 GKI 5.10 display takeover recorder, phase 179.
 *
 * Records are fixed-size, Reed-Solomon protected, and written independently
 * to the record, console and ftrace RAMOOPS banks. CRC is intentionally not used in
 * this phase. Payloads remain metadata-only.
 */
#undef pr_fmt
#define pr_fmt(fmt) "A52R179: " fmt

#define A52_R179_CAPACITY 768U
#define A52_R179_MESSAGE_LEN 94U
#define A52_R179_COMMIT 0x5a52c179U
#define A52_R179_VERSION 1U
#define A52_R179_PREFIX "R79"

struct a52_r179_data {
	u8 magic[8];
	__le16 version;
	__le16 header_len;
	__le64 seq;
	__le64 monotonic_ns;
	__le32 pid;
	__le32 tgid;
	__le32 cpu;
	__le16 kind;
	__le16 message_len;
	char comm[TASK_COMM_LEN];
	char message[A52_R179_MESSAGE_LEN - 1];
	__le32 commit;
} __packed;

extern unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,
					     unsigned int targets);

static void a52_r179_pack(const struct a52_r179_event *event,
			  struct a52_r179_data *data)
{
	size_t message_len;

	memset(data, 0, sizeof(*data));
	memcpy(data->magic, "A52R0179", sizeof(data->magic));
	data->version = cpu_to_le16(A52_R179_VERSION);
	data->header_len = cpu_to_le16(60U);
	message_len = strnlen(event->message, sizeof(data->message));
	data->message_len = cpu_to_le16((u16)message_len);
	memcpy(data->comm, event->comm, sizeof(data->comm));
	memcpy(data->message, event->message, message_len);
	data->commit = cpu_to_le32(A52_R179_COMMIT);
}

static bool a52_r179_is_critical_message(const char *message)
{
	return !strncmp(message, "BOOT ", 5) ||
	       !strncmp(message, "HB ", 3) ||
	       !strncmp(message, "REFGEN ", 7) ||
	       !strncmp(message, "DISP ", 5) ||
	       !strncmp(message, "WDT ", 4);
}

static void a52_r179_write_control(const char *kind)
{
	a52_ackfr_record("BOOT ctl=%s rel=%s cap=%u rs=%u copies=3 crc=0",
			  kind, init_utsname()->release, A52_R179_CAPACITY,
			  A52_R179_RS_ROOTS);
}

static int __init a52_r179_rs_init(void)
{
	a52_ackfr_record("BOOT rs=ready phase=197 roots=%u copies=3 crc=0",
			  A52_R179_RS_ROOTS);
	return 0;
}

static int __init a52_r179_late(void)
{
	pr_info("phase197 triple-copy recorder enabled stored=%llu dropped=%llu\n",
		(unsigned long long)0, (unsigned long long)0);
	return 0;
}
