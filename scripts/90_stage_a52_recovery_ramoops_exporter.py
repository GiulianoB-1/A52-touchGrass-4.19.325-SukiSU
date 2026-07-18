#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * Recovery-only A52 ramoops snapshot exporter.
 *
 * Snapshot the exact 1 MiB persistent RAM range before the normal ramoops
 * postcore initcall can validate, clear, or rewrite any zone. Userspace can
 * read only the frozen copy through a root-only misc device.
 */
#include <linux/capability.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/sizes.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>

#define A52_RAMOOPS_PHYS 0xB1B00000ULL
#define A52_RAMOOPS_SIZE SZ_1M

static u8 *a52_ramoops_snapshot;
static int a52_snapshot_error = -ENODATA;

static int __init a52_ramoops_snapshot_init(void)
{
	void __iomem *mapping;

	a52_ramoops_snapshot = vmalloc(A52_RAMOOPS_SIZE);
	if (!a52_ramoops_snapshot) {
		a52_snapshot_error = -ENOMEM;
		pr_err("a52_ramoops_raw: snapshot allocation failed\n");
		return 0;
	}

	mapping = ioremap_wc(A52_RAMOOPS_PHYS, A52_RAMOOPS_SIZE);
	if (!mapping)
		mapping = ioremap(A52_RAMOOPS_PHYS, A52_RAMOOPS_SIZE);
	if (!mapping) {
		vfree(a52_ramoops_snapshot);
		a52_ramoops_snapshot = NULL;
		a52_snapshot_error = -EIO;
		pr_err("a52_ramoops_raw: physical mapping failed\n");
		return 0;
	}

	memcpy_fromio(a52_ramoops_snapshot, mapping, A52_RAMOOPS_SIZE);
	iounmap(mapping);
	a52_snapshot_error = 0;
	pr_info("a52_ramoops_raw: captured 0x%llx-0x%llx before ramoops init\n",
		(unsigned long long)A52_RAMOOPS_PHYS,
		(unsigned long long)(A52_RAMOOPS_PHYS + A52_RAMOOPS_SIZE - 1));
	return 0;
}
pure_initcall(a52_ramoops_snapshot_init);

static int a52_ramoops_open(struct inode *inode, struct file *file)
{
	if (!capable(CAP_SYS_RAWIO) && !capable(CAP_SYS_ADMIN))
		return -EPERM;
	return a52_snapshot_error;
}

static ssize_t a52_ramoops_read(struct file *file, char __user *buf,
				 size_t count, loff_t *ppos)
{
	size_t remaining;

	if (!a52_ramoops_snapshot)
		return a52_snapshot_error ? a52_snapshot_error : -ENODATA;
	if (*ppos < 0)
		return -EINVAL;
	if (*ppos >= A52_RAMOOPS_SIZE)
		return 0;

	remaining = A52_RAMOOPS_SIZE - (size_t)*ppos;
	if (count > remaining)
		count = remaining;
	if (copy_to_user(buf, a52_ramoops_snapshot + *ppos, count))
		return -EFAULT;
	*ppos += count;
	return count;
}

static const struct file_operations a52_ramoops_fops = {
	.owner = THIS_MODULE,
	.open = a52_ramoops_open,
	.read = a52_ramoops_read,
	.llseek = default_llseek,
};

static struct miscdevice a52_ramoops_miscdev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "a52_ramoops_raw",
	.fops = &a52_ramoops_fops,
	.mode = 0400,
};

static int __init a52_ramoops_device_init(void)
{
	int ret = misc_register(&a52_ramoops_miscdev);

	if (ret)
		pr_err("a52_ramoops_raw: misc device registration failed: %d\n", ret);
	else
		pr_info("a52_ramoops_raw: /dev/a52_ramoops_raw ready, size=%u\n",
			(unsigned int)A52_RAMOOPS_SIZE);
	return ret;
}
device_initcall(a52_ramoops_device_init);

MODULE_DESCRIPTION("A52 recovery-only frozen ramoops raw exporter");
MODULE_LICENSE("GPL v2");
'''

MAKE_MARKER = "# A52 recovery-only frozen ramoops exporter"
MAKE_ENTRY = f"\n{MAKE_MARKER}\nobj-y += a52_ramoops_raw.o\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    kernel = args.kernel.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    source_path = kernel / "drivers/misc/a52_ramoops_raw.c"
    makefile_path = kernel / "drivers/misc/Makefile"
    if not makefile_path.is_file():
        raise SystemExit("drivers/misc/Makefile is missing")

    source_path.write_text(SOURCE, encoding="utf-8")
    makefile = makefile_path.read_text(encoding="utf-8")
    if MAKE_MARKER in makefile:
        raise SystemExit("exporter Makefile entry already exists")
    makefile_path.write_text(makefile.rstrip() + MAKE_ENTRY, encoding="utf-8")

    checks = {
        "source_created": source_path.is_file(),
        "pure_initcall": "pure_initcall(a52_ramoops_snapshot_init);" in SOURCE,
        "device_initcall": "device_initcall(a52_ramoops_device_init);" in SOURCE,
        "fixed_physical_range": "A52_RAMOOPS_PHYS 0xB1B00000ULL" in SOURCE,
        "fixed_one_mib_size": "A52_RAMOOPS_SIZE SZ_1M" in SOURCE,
        "read_only_file_operations": ".read = a52_ramoops_read" in SOURCE and ".write" not in SOURCE,
        "capability_gate": "CAP_SYS_RAWIO" in SOURCE and "CAP_SYS_ADMIN" in SOURCE,
        "root_read_only_mode": ".mode = 0400" in SOURCE,
        "makefile_linked_builtin": "obj-y += a52_ramoops_raw.o" in makefile_path.read_text(),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("staging audit failed: " + ", ".join(failed))

    (output / "a52_ramoops_raw.c").write_text(SOURCE, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "physical_address": "0xB1B00000",
                "size": "0x00100000",
                "device": "/dev/a52_ramoops_raw",
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
