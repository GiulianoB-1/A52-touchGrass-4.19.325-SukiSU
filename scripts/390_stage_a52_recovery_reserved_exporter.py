#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * Recovery-only A52 Samsung reserved-RAM snapshot exporter.
 *
 * Freeze the complete physical range 0xB1000000..0xB1AFFFFF (11 MiB)
 * during pure_initcall, before normal Samsung sec_log/sec_debug core init
 * can reuse any portion. Userspace receives only the frozen copy via a
 * root-only read-only misc device.
 *
 * This deliberately avoids CONFIG_DEVMEM and /dev/mem.
 */
#include <linux/capability.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/sizes.h>
#include <linux/string.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>

#define A52_SAMSUNG_RESERVED_PHYS 0xB1000000ULL
#define A52_SAMSUNG_RESERVED_SIZE (11U * SZ_1M)

static u8 *a52_samsung_reserved_snapshot;
static int a52_samsung_reserved_error = -ENODATA;

static int __init a52_samsung_reserved_snapshot_init(void)
{
	void __iomem *mapping;

	a52_samsung_reserved_snapshot = vmalloc(A52_SAMSUNG_RESERVED_SIZE);
	if (!a52_samsung_reserved_snapshot) {
		a52_samsung_reserved_error = -ENOMEM;
		pr_err("a52_samsung_reserved_raw: snapshot allocation failed\n");
		return 0;
	}

	/*
	 * TouchGrass/Samsung sec_log maps this debug-DRAM neighborhood through
	 * ioremap_cache(). Mirror that contract here. The region is outside the
	 * normal page allocator on the A52 recovery memory map.
	 */
	mapping = ioremap_cache(A52_SAMSUNG_RESERVED_PHYS,
			       A52_SAMSUNG_RESERVED_SIZE);
	if (!mapping) {
		vfree(a52_samsung_reserved_snapshot);
		a52_samsung_reserved_snapshot = NULL;
		a52_samsung_reserved_error = -EIO;
		pr_err("a52_samsung_reserved_raw: ioremap_cache failed for 0x%llx-0x%llx\n",
			(unsigned long long)A52_SAMSUNG_RESERVED_PHYS,
			(unsigned long long)(A52_SAMSUNG_RESERVED_PHYS +
					     A52_SAMSUNG_RESERVED_SIZE - 1));
		return 0;
	}

	memcpy_fromio(a52_samsung_reserved_snapshot, mapping,
		      A52_SAMSUNG_RESERVED_SIZE);
	iounmap(mapping);
	a52_samsung_reserved_error = 0;

	pr_info("a52_samsung_reserved_raw: captured 0x%llx-0x%llx before Samsung debug init\n",
		(unsigned long long)A52_SAMSUNG_RESERVED_PHYS,
		(unsigned long long)(A52_SAMSUNG_RESERVED_PHYS +
				     A52_SAMSUNG_RESERVED_SIZE - 1));
	return 0;
}
pure_initcall(a52_samsung_reserved_snapshot_init);

static int a52_samsung_reserved_open(struct inode *inode, struct file *file)
{
	if (!capable(CAP_SYS_RAWIO) && !capable(CAP_SYS_ADMIN))
		return -EPERM;
	return a52_samsung_reserved_error;
}

static ssize_t a52_samsung_reserved_read(struct file *file, char __user *buf,
					 size_t count, loff_t *ppos)
{
	size_t remaining;

	if (!a52_samsung_reserved_snapshot)
		return a52_samsung_reserved_error ?
			a52_samsung_reserved_error : -ENODATA;
	if (*ppos < 0)
		return -EINVAL;
	if (*ppos >= A52_SAMSUNG_RESERVED_SIZE)
		return 0;

	remaining = A52_SAMSUNG_RESERVED_SIZE - (size_t)*ppos;
	if (count > remaining)
		count = remaining;

	if (copy_to_user(buf,
			 a52_samsung_reserved_snapshot + *ppos, count))
		return -EFAULT;

	*ppos += count;
	return count;
}

static const struct file_operations a52_samsung_reserved_fops = {
	.owner = THIS_MODULE,
	.open = a52_samsung_reserved_open,
	.read = a52_samsung_reserved_read,
	.llseek = default_llseek,
};

static struct miscdevice a52_samsung_reserved_miscdev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "a52_samsung_reserved_raw",
	.fops = &a52_samsung_reserved_fops,
	.mode = 0400,
};

static int __init a52_samsung_reserved_device_init(void)
{
	int ret = misc_register(&a52_samsung_reserved_miscdev);

	if (ret)
		pr_err("a52_samsung_reserved_raw: misc device registration failed: %d\n",
		       ret);
	else
		pr_info("a52_samsung_reserved_raw: /dev/a52_samsung_reserved_raw ready, size=%u\n",
			(unsigned int)A52_SAMSUNG_RESERVED_SIZE);
	return ret;
}
device_initcall(a52_samsung_reserved_device_init);

MODULE_DESCRIPTION("A52 recovery-only frozen Samsung reserved-RAM exporter");
MODULE_LICENSE("GPL v2");
'''

MAKE_MARKER = "# A52 recovery-only Samsung reserved-RAM exporter"
MAKE_ENTRY = f"\n{MAKE_MARKER}\nobj-y += a52_samsung_reserved_raw.o\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    kernel = args.kernel.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    source_path = kernel / "drivers/misc/a52_samsung_reserved_raw.c"
    makefile_path = kernel / "drivers/misc/Makefile"
    if not makefile_path.is_file():
        raise SystemExit("drivers/misc/Makefile is missing")

    source_path.write_text(SOURCE, encoding="utf-8")
    makefile = makefile_path.read_text(encoding="utf-8")
    if MAKE_MARKER in makefile:
        raise SystemExit("reserved exporter Makefile entry already exists")
    makefile_path.write_text(makefile.rstrip() + MAKE_ENTRY, encoding="utf-8")

    checks = {
        "source_created": source_path.is_file(),
        "pure_initcall": "pure_initcall(a52_samsung_reserved_snapshot_init);" in SOURCE,
        "device_initcall": "device_initcall(a52_samsung_reserved_device_init);" in SOURCE,
        "fixed_physical_base": "A52_SAMSUNG_RESERVED_PHYS 0xB1000000ULL" in SOURCE,
        "fixed_eleven_mib_size": "A52_SAMSUNG_RESERVED_SIZE (11U * SZ_1M)" in SOURCE,
        "ioremap_cache": "ioremap_cache(A52_SAMSUNG_RESERVED_PHYS" in SOURCE,
        "snapshot_copy": "memcpy_fromio(a52_samsung_reserved_snapshot" in SOURCE,
        "read_only_file_operations": ".read = a52_samsung_reserved_read" in SOURCE and ".write" not in SOURCE,
        "capability_gate": "CAP_SYS_RAWIO" in SOURCE and "CAP_SYS_ADMIN" in SOURCE,
        "root_read_only_mode": ".mode = 0400" in SOURCE,
        "makefile_linked_builtin": "obj-y += a52_samsung_reserved_raw.o" in makefile_path.read_text(),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("reserved exporter staging audit failed: " + ", ".join(failed))

    (output / "a52_samsung_reserved_raw.c").write_text(SOURCE, encoding="utf-8")
    (output / "reserved-exporter-stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "physical_address": "0xB1000000",
                "size": "0x00B00000",
                "device": "/dev/a52_samsung_reserved_raw",
                "mapping": "ioremap_cache + frozen vmalloc copy",
                "read_only": True,
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
