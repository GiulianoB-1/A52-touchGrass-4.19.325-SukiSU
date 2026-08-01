#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_recorder(text: str) -> str:
    if 'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c' in text:
        return text

    text = replace_once(
        text,
        " * A52 GKI 5.10 display takeover recorder, phase 179.\n",
        " * A52 GKI 5.10 display takeover recorder, phase 199.\n",
        "recorder phase comment",
    )
    text = replace_once(
        text,
        " * Records are fixed-size, Reed-Solomon protected, and written independently\n"
        " * to the record, console and ftrace RAMOOPS banks. CRC is intentionally not used in\n"
        " * this phase. Payloads remain metadata-only.\n",
        " * Records are fixed-size, CRC32C validated, Reed-Solomon protected, and written\n"
        " * independently to the record, console and ftrace RAMOOPS banks. Payloads\n"
        " * remain metadata-only.\n",
        "recorder protection comment",
    )
    text = replace_once(
        text,
        '#define pr_fmt(fmt) "A52R179: " fmt\n',
        '#define pr_fmt(fmt) "A52R199: " fmt\n',
        "recorder printk prefix",
    )
    text = replace_once(
        text,
        "#define A52_R179_CAPACITY 768U\n"
        "#define A52_R179_MESSAGE_LEN 94U\n",
        "#define A52_R179_CAPACITY 896U\n"
        "#define A52_R179_MESSAGE_LEN 90U\n",
        "capacity and message length",
    )
    text = replace_once(
        text,
        "#define A52_R179_COMMIT 0x5a52c179U\n"
        "#define A52_R179_VERSION 1U\n",
        "#define A52_R179_COMMIT 0x5a52c199U\n"
        "#define A52_R179_VERSION 2U\n",
        "format version and commit",
    )
    text = replace_once(
        text,
        '#define A52_R179_PREFIX "R79"\n',
        '#define A52_R179_PREFIX "R99"\n',
        "transport prefix",
    )
    text = replace_once(
        text,
        "\tchar message[A52_R179_MESSAGE_LEN - 1];\n"
        "\t__le32 commit;\n",
        "\tchar message[A52_R179_MESSAGE_LEN - 1];\n"
        "\t__le32 crc32c;\n"
        "\t__le32 commit;\n",
        "record crc field",
    )

    crc_function = r'''
static u32 a52_r199_crc32c(const void *buffer, size_t len)
{
	const u8 *bytes = buffer;
	u32 crc = ~0U;
	size_t index;
	unsigned int bit;

	for (index = 0; index < len; index++) {
		crc ^= bytes[index];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^
				((crc & 1) ? 0x82f63b78U : 0U);
	}
	return ~crc;
}

'''
    text = replace_once(
        text,
        "extern unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,\n"
        "\t\t\t\t\t     unsigned int targets);\n\n",
        "extern unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,\n"
        "\t\t\t\t\t     unsigned int targets);\n\n"
        + crc_function,
        "crc32c function insertion",
    )
    text = replace_once(
        text,
        'memcpy(data->magic, "A52R0179", sizeof(data->magic));\n',
        'memcpy(data->magic, "A52R0199", sizeof(data->magic));\n',
        "record magic",
    )
    text = replace_once(
        text,
        "\tmemcpy(data->comm, event->comm, sizeof(data->comm));\n"
        "\tmemcpy(data->message, event->message, message_len);\n"
        "\tdata->commit = cpu_to_le32(A52_R179_COMMIT);\n",
        "\tmemcpy(data->comm, event->comm, sizeof(data->comm));\n"
        "\tmemcpy(data->message, event->message, message_len);\n"
        "\tdata->crc32c = cpu_to_le32(a52_r199_crc32c(data,\n"
        "\t\t\toffsetof(struct a52_r179_data, crc32c)));\n"
        "\tdata->commit = cpu_to_le32(A52_R179_COMMIT);\n",
        "record crc calculation",
    )
    text = replace_once(
        text,
        "\treturn !strncmp(message, \"BOOT \", 5) ||\n"
        "\t       !strncmp(message, \"HB \", 3) ||\n"
        "\t       !strncmp(message, \"REFGEN \", 7) ||\n"
        "\t       !strncmp(message, \"DISP \", 5) ||\n"
        "\t       !strncmp(message, \"WDT \", 4);\n",
        "\treturn !strncmp(message, \"BOOT \", 5) ||\n"
        "\t       !strncmp(message, \"HB \", 3) ||\n"
        "\t       !strncmp(message, \"REFGEN \", 7) ||\n"
        "\t       !strncmp(message, \"DISP \", 5) ||\n"
        "\t       !strncmp(message, \"WDT \", 4) ||\n"
        "\t       !strncmp(message, \"DRMPOST \", 8) ||\n"
        "\t       !strncmp(message, \"KMSPOST \", 8) ||\n"
        "\t       !strncmp(message, \"KMSBLK \", 7) ||\n"
        "\t       !strncmp(message, \"CAT \", 4) ||\n"
        "\t       !strncmp(message, \"A52GDSC \", 8);\n",
        "critical retention prefixes",
    )
    text = replace_once(
        text,
        'a52_ackfr_record("BOOT ctl=%s rel=%s cap=%u rs=%u copies=3 crc=0",',
        'a52_ackfr_record("BOOT ctl=%s rel=%s cap=%u rs=%u copies=3 crc=crc32c",',
        "control crc marker",
    )
    text = replace_once(
        text,
        'a52_ackfr_record("BOOT rs=ready phase=197 roots=%u copies=3 crc=0",',
        'a52_ackfr_record("BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c",',
        "ready marker",
    )
    text = replace_once(
        text,
        'pr_info("phase197 triple-copy recorder enabled stored=%llu dropped=%llu\\n",',
        'pr_info("phase199 triple-copy RS+CRC32C recorder enabled stored=%llu dropped=%llu\\n",',
        "recorder printk profile",
    )
    return text


def run(root: Path) -> None:
    path = root / RECORDER
    if not path.is_file():
        raise SystemExit(f"missing recorder source: {path}")
    original = path.read_text(encoding="utf-8")
    patched = patch_recorder(original)
    path.write_text(patched, encoding="utf-8")

    required = (
        "A52 GKI 5.10 display takeover recorder, phase 199",
        '#define pr_fmt(fmt) "A52R199: " fmt',
        "#define A52_R179_CAPACITY 896U",
        "#define A52_R179_MESSAGE_LEN 90U",
        "#define A52_R179_COMMIT 0x5a52c199U",
        "#define A52_R179_VERSION 2U",
        '#define A52_R179_PREFIX "R99"',
        "__le32 crc32c;",
        "a52_r199_crc32c",
        'memcpy(data->magic, "A52R0199"',
        "offsetof(struct a52_r179_data, crc32c)",
        '!strncmp(message, "DRMPOST ", 8)',
        '!strncmp(message, "KMSPOST ", 8)',
        '!strncmp(message, "KMSBLK ", 7)',
        '!strncmp(message, "CAT ", 4)',
        '!strncmp(message, "A52GDSC ", 8)',
        "copies=3 crc=crc32c",
        "BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c",
        "phase199 triple-copy RS+CRC32C recorder enabled",
    )
    for marker in required:
        if marker not in patched:
            raise SystemExit(f"missing Phase 199 marker: {marker}")
    if "copies=3 crc=0" in patched:
        raise SystemExit("legacy crc=0 marker remains")


def self_test() -> None:
    source = Path(__file__).resolve().parents[1] / "stage" / "recorder-after-phase197.c"
    if not source.is_file():
        fixture = '''// SPDX-License-Identifier: GPL-2.0-only
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
	char message[A52_R179_MESSAGE_LEN - 1];
	__le32 commit;
} __packed;
extern unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,
					     unsigned int targets);
static void p(void) {
	memcpy(data->magic, "A52R0179", sizeof(data->magic));
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
void c(void) {
	a52_ackfr_record("BOOT ctl=%s rel=%s cap=%u rs=%u copies=3 crc=0",
	a52_ackfr_record("BOOT rs=ready phase=197 roots=%u copies=3 crc=0",
	pr_info("phase197 triple-copy recorder enabled stored=%llu dropped=%llu\\n",
}
'''
    else:
        fixture = source.read_text(encoding="utf-8")
    patched = patch_recorder(fixture)
    assert 'memcpy(data->magic, "A52R0199"' in patched
    assert "__le32 crc32c;" in patched
    assert "copies=3 crc=crc32c" in patched
    assert '!strncmp(message, "DRMPOST ", 8)' in patched
    assert "copies=3 crc=0" not in patched
    print("phase199 recorder CRC32C patcher self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Phase 199 recorder CRC32C and retention hardening")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.root is None:
        parser.error("--root is required unless --self-test is used")
    run(args.root)
    print("phase199 recorder CRC32C and post-KMS retention staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
