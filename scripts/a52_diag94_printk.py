#!/usr/bin/env python3
from __future__ import annotations

from a52_diag94_common import replace_first_supported, replace_once


def instrument_printk(printk: str) -> str:
    declaration = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    if declaration not in printk:
        printk = replace_once(
            printk,
            "#include <linux/kernel.h>\n",
            "#include <linux/kernel.h>\n" + declaration,
            "declare persistent diagnostic helper in printk.c",
        )

    counter_anchor = "atomic_t ignore_console_lock_warning __read_mostly = ATOMIC_INIT(0);\n"
    helper_code = (
        "static unsigned int a52_storage_kmsg_count;\n\n"
        "static bool a52_storage_kmsg_match(const char *line)\n"
        "{\n"
        "\treturn strstr(line, \"ufs\") || strstr(line, \"UFS\") ||\n"
        "\t       strstr(line, \"ufsh\") || strstr(line, \"1d84000\") ||\n"
        "\t       strstr(line, \"1d87000\") || strstr(line, \"scsi\") ||\n"
        "\t       strstr(line, \"SCSI\") || strstr(line, \"sd \") ||\n"
        "\t       strstr(line, \"block\") || strstr(line, \"partition\") ||\n"
        "\t       strstr(line, \"GPT\") || strstr(line, \"gpt\") ||\n"
        "\t       strstr(line, \"qmp\") || strstr(line, \"phy\") ||\n"
        "\t       strstr(line, \"PHY\") || strstr(line, \"regulator\") ||\n"
        "\t       strstr(line, \"rpmh\") || strstr(line, \"smmu\") ||\n"
        "\t       strstr(line, \"iommu\");\n"
        "}\n\n"
    )
    if helper_code not in printk:
        printk = replace_once(
            printk,
            counter_anchor,
            helper_code + counter_anchor,
            "add storage printk mirror helper",
        )

    declaration_candidates = (
        "\tint printed_len;\n\tbool in_sched = false;\n\tunsigned long flags;\n",
        "\tint printed_len;\n\tbool in_sched = false;\n",
    )

    def add_locals(anchor: str) -> str:
        return (
            anchor
            + "\tva_list a52_args;\n"
            + "\tchar a52_line[192];\n"
            + "\tint a52_len;\n"
            + "\tint a52_i;\n"
        )

    printk = replace_first_supported(
        printk,
        declaration_candidates,
        add_locals,
        "add vprintk storage mirror locals",
    )

    suppress_anchor = "\t/* Suppress unimportant messages after panic happens */\n"
    capture = (
        "\tif (unlikely(a52_storage_kmsg_count < 128)) {\n"
        "\t\tva_copy(a52_args, args);\n"
        "\t\ta52_len = vscnprintf(a52_line, sizeof(a52_line), fmt, a52_args);\n"
        "\t\tva_end(a52_args);\n"
        "\t\tif (a52_len > 0 && a52_storage_kmsg_match(a52_line)) {\n"
        "\t\t\tfor (a52_i = 0; a52_i < a52_len; a52_i++)\n"
        "\t\t\t\tif (a52_line[a52_i] == '\\n' || a52_line[a52_i] == '\\r')\n"
        "\t\t\t\t\ta52_line[a52_i] = '|';\n"
        "\t\t\ta52_storage_kmsg_count++;\n"
        "\t\t\ta52_persistent_diag_mark(\"A52LOG seq=%u pid=%d comm=%s level=%d facility=%d msg=%s\\n\",\n"
        "\t\t\t\t\t a52_storage_kmsg_count, current->pid, current->comm,\n"
        "\t\t\t\t\t level, facility, a52_line);\n"
        "\t\t}\n"
        "\t}\n\n"
    )
    printk = replace_once(
        printk,
        suppress_anchor,
        capture + suppress_anchor,
        "mirror storage-related kernel printk messages",
    )
    return printk
