#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import sys
import traceback
import types
import urllib.request
from pathlib import Path

BASE_NAME = "96_stage_touchgrass_ufs_reference_recorder_base.py"
BASE_BLOB_SHA = "558ce3449a6f25266a7745e0b78a947a1764ce15"
BASE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "0ceebb36610bed004f5f08c2e63465777dee3809/"
    "scripts/96_stage_touchgrass_ufs_reference_recorder_base.py"
)


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def load_base() -> types.ModuleType:
    local = Path(__file__).with_name(BASE_NAME)
    if local.is_file():
        data = local.read_bytes()
        origin = str(local)
    else:
        with urllib.request.urlopen(BASE_URL, timeout=30) as response:
            data = response.read()
        origin = BASE_URL

    actual = git_blob_sha(data)
    if actual != BASE_BLOB_SHA:
        raise SystemExit(
            f"recorder base integrity failure: expected {BASE_BLOB_SHA}, got {actual}"
        )

    module = types.ModuleType("touchgrass_recorder_stage_base")
    module.__file__ = origin
    exec(compile(data, origin, "exec"), module.__dict__)
    return module


_impl = load_base()


def insert_after_regex(
    text: str,
    function_anchor: str,
    pattern: str,
    insertion: str,
    label: str,
) -> str:
    start, _, end = _impl.function_bounds(text, function_anchor, label)
    matches = list(re.finditer(pattern, text[start:end], flags=re.MULTILINE))
    if len(matches) != 1:
        _impl.fail(label, f"expected one regex target in function, found {len(matches)}")
    point = start + matches[0].end()
    return text[:point] + insertion + text[point:]


def instrument_ufshcd(text: str) -> str:
    text = _impl.add_include(
        text,
        '#include "ufshcd.h"\n',
        "ufshcd include",
    )

    init_anchor = "int ufshcd_init(struct ufs_hba *hba,"
    text = _impl.insert_after_fragment(
        text,
        init_anchor,
        "\tstruct device *dev = hba->dev;\n",
        "\n\ttgref_record(\"CORE init_begin dev=%s irq=%u mmio=%p host_no=%d\",\n"
        "\t\t     dev_name(dev), irq, mmio_base, hba->host->host_no);\n",
        "core init begin",
    )

    text = _impl.insert_after_fragment(
        text,
        init_anchor,
        "\terr = ufshcd_hba_init(hba);\n",
        "\ttgref_record(\"CORE hba_init ret=%d\", err);\n",
        "core hba init",
    )
    text = _impl.insert_after_fragment(
        text,
        init_anchor,
        "\tufshcd_hba_capabilities(hba);\n",
        "\ttgref_record(\"CORE capabilities cap=0x%x nutrs=%d nutmrs=%d\",\n"
        "\t\t     hba->capabilities, hba->nutrs, hba->nutmrs);\n",
        "core capabilities",
    )
    text = _impl.insert_after_fragment(
        text,
        init_anchor,
        "\thba->ufs_version = ufshcd_get_ufs_version(hba);\n",
        "\ttgref_record(\"CORE version value=0x%x\", hba->ufs_version);\n",
        "core version",
    )

    for statement, fmt, args, label in (
        (
            "\terr = ufshcd_set_dma_mask(hba);\n",
            "dma_mask ret=%d",
            "err",
            "core dma",
        ),
        (
            "\terr = ufshcd_memory_alloc(hba);\n",
            "memory_alloc ret=%d",
            "err",
            "core memory",
        ),
        (
            "\terr = scsi_add_host(host, hba->dev);\n",
            "scsi_add_host ret=%d host_no=%d",
            "err, host->host_no",
            "core scsi host",
        ),
        (
            "\terr = ufshcd_hba_enable(hba);\n",
            "hba_enable ret=%d state=%d hcs=0x%x",
            "err, hba->ufshcd_state, ufshcd_readl(hba, REG_CONTROLLER_STATUS)",
            "core hba enable",
        ),
        (
            "\tasync_schedule(ufshcd_async_scan, hba);\n",
            "async_scan_scheduled state=%d",
            "hba->ufshcd_state",
            "core async schedule",
        ),
    ):
        text = _impl.insert_after_fragment(
            text,
            init_anchor,
            statement,
            f'\ttgref_record("CORE {fmt}", {args});\n',
            label,
        )

    text = insert_after_regex(
        text,
        init_anchor,
        r"\terr\s*=\s*devm_request_irq\(dev,\s*irq,\s*ufshcd_intr,\s*IRQF_SHARED,\s*\n"
        r"\s*dev_name\(dev\),\s*hba\);\n",
        "\ttgref_record(\"CORE request_irq ret=%d irq=%u\", err, irq);\n",
        "core irq",
    )

    probe_anchor = "static int ufshcd_probe_hba("
    probe_points = (
        (
            "\tret = ufshcd_link_startup(hba);\n",
            "\t",
            "link_startup ret=%d state=%d link=%d",
            "ret, hba->ufshcd_state, hba->uic_link_state",
            "core link",
        ),
        (
            "\tret = ufshcd_verify_dev_init(hba);\n",
            "\t",
            "verify_dev_init ret=%d",
            "ret",
            "core verify",
        ),
        (
            "\tret = ufshcd_complete_dev_init(hba);\n",
            "\t",
            "complete_dev_init ret=%d",
            "ret",
            "core complete",
        ),
        (
            "\tret = ufs_read_device_desc_data(hba);\n",
            "\t",
            "device_desc ret=%d manufacturer=0x%x spec=0x%x",
            "ret, hba->dev_info.w_manufacturer_id, hba->dev_info.w_spec_version",
            "core descriptor",
        ),
        (
            "\t\tret = ufshcd_config_pwr_mode(hba, &hba->max_pwr_info.info);\n",
            "\t\t",
            "power_mode ret=%d rxgear=%d txgear=%d rxlane=%d txlane=%d",
            "ret, hba->max_pwr_info.info.gear_rx, hba->max_pwr_info.info.gear_tx, "
            "hba->max_pwr_info.info.lane_rx, hba->max_pwr_info.info.lane_tx",
            "core power",
        ),
        (
            "\t\tscsi_scan_host(hba->host);\n",
            "\t\t",
            "scsi_scan host_no=%d",
            "hba->host->host_no",
            "core scan",
        ),
    )
    for statement, indent, fmt, args, label in probe_points:
        text = _impl.insert_after_fragment(
            text,
            probe_anchor,
            statement,
            f'{indent}tgref_record("CORE {fmt}", {args});\n',
            label,
        )

    text = _impl.insert_before_last_return(
        text,
        probe_anchor,
        "\treturn ret;\n",
        "\ttgref_record(\"CORE probe_hba_end ret=%d state=%d link=%d "
        "manufacturer=0x%x spec=0x%x\",\n"
        "\t\t     ret, hba->ufshcd_state, hba->uic_link_state,\n"
        "\t\t     hba->dev_info.w_manufacturer_id,\n"
        "\t\t     hba->dev_info.w_spec_version);\n",
        "core probe end",
    )
    return text


_impl.instrument_ufshcd = instrument_ufshcd


def requested_output() -> Path | None:
    try:
        index = sys.argv.index("--output")
        return Path(sys.argv[index + 1]).resolve()
    except (ValueError, IndexError):
        return None


def main() -> int:
    try:
        return _impl.main()
    except BaseException:
        output = requested_output()
        if output is not None:
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
