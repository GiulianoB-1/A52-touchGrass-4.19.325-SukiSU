#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def add_include_and_helper(text: str) -> str:
    text = one(
        text,
        '#include <linux/gpio/machine.h>\n',
        '#include <linux/gpio/machine.h>\n#include <linux/of.h>\n',
        'add OF include',
    )
    anchor = 'static bool gpiolib_initialized;\n'
    helper = '''static bool gpiolib_initialized;\n\nextern void a52_ackfr_record(const char *fmt, ...);\n\nstatic bool a52_gpio_core_trace(const struct gpio_chip *gc)\n{\n\treturn gc && gc->parent && gc->parent->of_node &&\n\t\tof_device_is_compatible(gc->parent->of_node,\n\t\t\t\t\t"qcom,lagoon-pinctrl");\n}\n\n#define A52_GPIOCORE_TRACE(gc, fmt, ...) do { \\\n\tif (a52_gpio_core_trace(gc)) \\\n\t\ta52_ackfr_record(fmt, ##__VA_ARGS__); \\\n} while (0)\n'''
    return one(text, anchor, helper, 'add Lagoon GPIO-core trace helper')


def patch_gpiochip_add(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    text = add_include_and_helper(text)

    text = one(
        text,
        '''\tbool\t\tblock_gpio_read = false;\n\n\t/*\n\t * First: allocate and populate the internal stat container, and\n''',
        '''\tbool\t\tblock_gpio_read = false;\n\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE add enter ng=%u base=%d",\n\t\t\t   gc->ngpio, base);\n\n\t/*\n\t * First: allocate and populate the internal stat container, and\n''',
        'trace gpiochip add entry',
    )

    text = one(
        text,
        '''\tgdev = kzalloc(sizeof(*gdev), GFP_KERNEL);\n\tif (!gdev)\n\t\treturn -ENOMEM;\n\tgdev->dev.bus = &gpio_bus_type;\n''',
        '''\tgdev = kzalloc(sizeof(*gdev), GFP_KERNEL);\n\tif (!gdev)\n\t\treturn -ENOMEM;\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE gdev-alloc ok=1");\n\tgdev->dev.bus = &gpio_bus_type;\n''',
        'trace gdev allocation',
    )

    text = one(
        text,
        '''\tof_gpio_dev_init(gc, gdev);\n\n\t/*\n''',
        '''\tof_gpio_dev_init(gc, gdev);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE of-dev-init done");\n\n\t/*\n''',
        'trace OF device init',
    )

    text = one(
        text,
        '''\tgdev->descs = kcalloc(gc->ngpio, sizeof(gdev->descs[0]), GFP_KERNEL);\n\tif (!gdev->descs) {\n''',
        '''\tgdev->descs = kcalloc(gc->ngpio, sizeof(gdev->descs[0]), GFP_KERNEL);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE desc-alloc ok=%u", !!gdev->descs);\n\tif (!gdev->descs) {\n''',
        'trace descriptor allocation',
    )

    text = one(
        text,
        '''\tspin_unlock_irqrestore(&gpio_lock, flags);\n\n\tBLOCKING_INIT_NOTIFIER_HEAD(&gdev->notifier);\n''',
        '''\tspin_unlock_irqrestore(&gpio_lock, flags);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE list-add done base=%d", gdev->base);\n\n\tBLOCKING_INIT_NOTIFIER_HEAD(&gdev->notifier);\n''',
        'trace global GPIO list insertion',
    )

    text = one(
        text,
        '''\tif (gc->names)\n\t\tret = gpiochip_set_desc_names(gc);\n\telse\n\t\tret = devprop_gpiochip_set_names(gc);\n\tif (ret)\n\t\tgoto err_remove_from_list;\n\n\tret = gpiochip_alloc_valid_mask(gc);\n''',
        '''\tA52_GPIOCORE_TRACE(gc, "GPIOCORE names enter explicit=%u", !!gc->names);\n\tif (gc->names)\n\t\tret = gpiochip_set_desc_names(gc);\n\telse\n\t\tret = devprop_gpiochip_set_names(gc);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE names exit rc=%d", ret);\n\tif (ret)\n\t\tgoto err_remove_from_list;\n\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE valid-alloc enter");\n\tret = gpiochip_alloc_valid_mask(gc);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE valid-alloc exit rc=%d mask=%u",\n\t\t\t   ret, !!gc->valid_mask);\n''',
        'trace names and valid-mask allocation',
    )

    text = one(
        text,
        '''\tif (ret)\n\t\tgoto err_remove_from_list;\n\n\tret = of_gpiochip_add(gc);\n\tif (ret)\n\t\tgoto err_free_gpiochip_mask;\n\n\tret = gpiochip_init_valid_mask(gc);\n\tif (ret)\n\t\tgoto err_remove_of_chip;\n\n\ttrace_android_vh_gpio_block_read(gdev, &block_gpio_read);\n\tif (!block_gpio_read) {\n\t\tfor (i = 0; i < gc->ngpio; i++) {\n\t\t\tstruct gpio_desc *desc = &gdev->descs[i];\n\n\t\t\tif (gc->get_direction && gpiochip_line_is_valid(gc, i)) {\n\t\t\t\tassign_bit(FLAG_IS_OUT,\n\t\t\t\t\t   &desc->flags, !gc->get_direction(gc, i));\n\t\t\t} else {\n\t\t\t\tassign_bit(FLAG_IS_OUT,\n\t\t\t\t\t   &desc->flags, !gc->direction_input);\n\t\t\t}\n\t\t}\n\t}\n\n\tret = gpiochip_add_pin_ranges(gc);\n''',
        '''\tif (ret)\n\t\tgoto err_remove_from_list;\n\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE of-add enter");\n\tret = of_gpiochip_add(gc);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE of-add exit rc=%d", ret);\n\tif (ret)\n\t\tgoto err_free_gpiochip_mask;\n\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE valid-init enter");\n\tret = gpiochip_init_valid_mask(gc);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE valid-init exit rc=%d", ret);\n\tif (ret)\n\t\tgoto err_remove_of_chip;\n\n\ttrace_android_vh_gpio_block_read(gdev, &block_gpio_read);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE dir-scan enter block=%u",\n\t\t\t   block_gpio_read);\n\tif (!block_gpio_read) {\n\t\tfor (i = 0; i < gc->ngpio; i++) {\n\t\t\tstruct gpio_desc *desc = &gdev->descs[i];\n\n\t\t\tif (gc->get_direction && gpiochip_line_is_valid(gc, i)) {\n\t\t\t\tint direction;\n\n\t\t\t\tA52_GPIOCORE_TRACE(gc,\n\t\t\t\t\t"GPIOCORE dir-read enter pin=%u", i);\n\t\t\t\tdirection = gc->get_direction(gc, i);\n\t\t\t\tA52_GPIOCORE_TRACE(gc,\n\t\t\t\t\t"GPIOCORE dir-read exit pin=%u rc=%d",\n\t\t\t\t\ti, direction);\n\t\t\t\tassign_bit(FLAG_IS_OUT, &desc->flags, !direction);\n\t\t\t} else {\n\t\t\t\tassign_bit(FLAG_IS_OUT,\n\t\t\t\t\t   &desc->flags, !gc->direction_input);\n\t\t\t}\n\t\t}\n\t}\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE dir-scan exit");\n\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE pin-ranges enter");\n\tret = gpiochip_add_pin_ranges(gc);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE pin-ranges exit rc=%d", ret);\n''',
        'trace OF, valid mask, direction scan and pin ranges',
    )

    text = one(
        text,
        '''\tret = gpiochip_irqchip_init_valid_mask(gc);\n\tif (ret)\n\t\tgoto err_free_hogs;\n\n\tret = gpiochip_irqchip_init_hw(gc);\n\tif (ret)\n\t\tgoto err_remove_irqchip_mask;\n\n\tret = gpiochip_add_irqchip(gc, lock_key, request_key);\n\tif (ret)\n\t\tgoto err_remove_irqchip_mask;\n''',
        '''\tA52_GPIOCORE_TRACE(gc, "GPIOCORE irq-valid enter");\n\tret = gpiochip_irqchip_init_valid_mask(gc);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE irq-valid exit rc=%d", ret);\n\tif (ret)\n\t\tgoto err_free_hogs;\n\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE irq-hw enter");\n\tret = gpiochip_irqchip_init_hw(gc);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE irq-hw exit rc=%d", ret);\n\tif (ret)\n\t\tgoto err_remove_irqchip_mask;\n\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE irq-add enter");\n\tret = gpiochip_add_irqchip(gc, lock_key, request_key);\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE irq-add exit rc=%d", ret);\n\tif (ret)\n\t\tgoto err_remove_irqchip_mask;\n''',
        'trace IRQ-chip registration',
    )

    text = one(
        text,
        '''\tif (gpiolib_initialized) {\n\t\tret = gpiochip_setup_dev(gdev);\n\t\tif (ret)\n\t\t\tgoto err_remove_irqchip;\n\t}\n\treturn 0;\n''',
        '''\tA52_GPIOCORE_TRACE(gc, "GPIOCORE setup-dev enter init=%u",\n\t\t\t   gpiolib_initialized);\n\tif (gpiolib_initialized) {\n\t\tret = gpiochip_setup_dev(gdev);\n\t\tA52_GPIOCORE_TRACE(gc, "GPIOCORE setup-dev exit rc=%d", ret);\n\t\tif (ret)\n\t\t\tgoto err_remove_irqchip;\n\t}\n\tA52_GPIOCORE_TRACE(gc, "GPIOCORE add success");\n\treturn 0;\n''',
        'trace GPIO device setup and success',
    )

    path.write_text(text, encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    patch_gpiochip_add(args.root / 'drivers/gpio/gpiolib.c')
    print('phase189 Lagoon GPIO-core stage trace applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
