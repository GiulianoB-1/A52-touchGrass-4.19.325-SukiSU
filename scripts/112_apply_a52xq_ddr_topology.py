#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(errors='replace')


def append_before_endif(path: Path, marker: str, block: str) -> int:
    text = read(path)
    if marker in text:
        return 0
    index = text.rfind('#endif')
    if index < 0:
        raise SystemExit(f'{path} has no closing #endif')
    path.write_text(text[:index] + '\n' + block.rstrip() + '\n' + text[index:])
    return 1


def patch_ddr_topology(gki: Path) -> dict[str, int]:
    path = gki / 'a52-port-compat.h'
    text = read(path)
    include_marker = '/* A52_PHASE10_DDR_TOPOLOGY_INCLUDES */'
    include_count = 0
    if include_marker not in text:
        block = (
            '\n' + include_marker + '\n'
            '#include <linux/of.h>\n'
        )
        index = text.rfind('#endif')
        if index < 0:
            raise SystemExit('a52-port-compat.h has no closing #endif')
        path.write_text(text[:index] + block + text[index:])
        include_count = 1

    helper_marker = '/* A52_PHASE10_DDR_TOPOLOGY_HELPERS */'
    helper_block = r'''
/* A52_PHASE10_DDR_TOPOLOGY_HELPERS */
/*
 * TouchGrass read these values from the flat /memory node. All current A52
 * consumers execute after the live device tree exists, so use the equivalent
 * OF property API retained by Android 5.10.
 */
static inline int of_fdt_get_ddrtype(void)
{
	struct device_node *memory;
	u32 value;
	int ret = -ENOENT;

	memory = of_find_node_by_path("/memory");
	if (memory && !of_property_read_u32(memory, "ddr_device_type", &value))
		ret = (int)value;
	of_node_put(memory);
	return ret;
}

static inline int of_fdt_get_ddrrank(int channel)
{
	struct device_node *memory;
	char property[32];
	u32 value;
	int ret = -ENOENT;

	snprintf(property, sizeof(property), "ddr_device_rank_ch%d", channel);
	memory = of_find_node_by_path("/memory");
	if (memory && !of_property_read_u32(memory, property, &value))
		ret = (int)value;
	of_node_put(memory);
	return ret;
}

static inline int of_fdt_get_ddrhbb(int channel, int rank)
{
	struct device_node *memory;
	char property[40];
	u32 value;
	int ret = -ENOENT;

	snprintf(property, sizeof(property), "ddr_device_hbb_ch%d_rank%d",
		 channel, rank);
	memory = of_find_node_by_path("/memory");
	if (memory && !of_property_read_u32(memory, property, &value))
		ret = (int)value;
	of_node_put(memory);
	return ret;
}
'''
    helper_count = append_before_endif(path, helper_marker, helper_block)
    return {'includes': include_count, 'helpers': helper_count}


def validate(gki: Path) -> dict[str, bool]:
    text = read(gki / 'a52-port-compat.h')
    return {
        'of_include': '#include <linux/of.h>' in text,
        'ddrtype_helper': 'static inline int of_fdt_get_ddrtype(void)' in text,
        'ddrrank_helper': 'static inline int of_fdt_get_ddrrank(int channel)' in text,
        'ddrhbb_helper': 'static inline int of_fdt_get_ddrhbb(int channel, int rank)' in text,
        'ddrtype_property': '"ddr_device_type"' in text,
        'ddrrank_property': '"ddr_device_rank_ch%d"' in text,
        'ddrhbb_property': '"ddr_device_hbb_ch%d_rank%d"' in text,
        'node_reference_released': text.count('of_node_put(memory);') >= 3,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    report = {
        'status': 'phase10-ddr-topology-compat-staged',
        'flashable': False,
        'hardware_validated': False,
        'ddr_topology': patch_ddr_topology(gki),
        'semantic_source': 'live-of-memory-node-equivalent-to-touchgrass-flat-properties',
    }
    report['validation'] = validate(gki)
    failures = [name for name, passed in report['validation'].items() if not passed]
    expected = {
        'ddr_topology.includes': report['ddr_topology']['includes'] == 1,
        'ddr_topology.helpers': report['ddr_topology']['helpers'] == 1,
    }
    failures.extend(name for name, passed in expected.items() if not passed)

    (output / 'phase10-ddr-topology-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    if failures:
        raise SystemExit('Workflow 112 staging validation failed: ' + ', '.join(failures))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
