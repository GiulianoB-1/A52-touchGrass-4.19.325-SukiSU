#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source match, found {count}")
    return text.replace(old, new, 1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage the A52 downstream qcom,msm-watchdog contract on GKI 5.10"
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    source = gki / "drivers/watchdog/qcom-wdt.c"
    if not source.is_file():
        raise SystemExit(f"missing QCOM watchdog source: {source}")

    original = source.read_text()
    text = original

    text = replace_once(
        text,
        """struct qcom_wdt_match_data {
\tconst u32 *offset;
\tbool pretimeout;
};
""",
        """struct qcom_wdt_match_data {
\tconst u32 *offset;
\tbool pretimeout;
\tunsigned long fixed_rate;
};
""",
        "extend watchdog match data",
    )

    text = replace_once(
        text,
        """static const struct qcom_wdt_match_data match_data_kpss = {
\t.offset = reg_offset_data_kpss,
\t.pretimeout = true,
};
""",
        """static const struct qcom_wdt_match_data match_data_kpss = {
\t.offset = reg_offset_data_kpss,
\t.pretimeout = true,
};

static const struct qcom_wdt_match_data match_data_msm = {
\t.offset = reg_offset_data_kpss,
\t.pretimeout = true,
\t.fixed_rate = 32765,
};
""",
        "add downstream MSM watchdog match data",
    )

    text = replace_once(
        text,
        """\tu32 percpu_offset;
\tint irq, ret;
\tstruct clk *clk;
""",
        """\tu32 percpu_offset;
\tint irq, ret;
\tstruct clk *clk;
\tbool running;
""",
        "add firmware-running state",
    )

    text = replace_once(
        text,
        """\tclk = devm_clk_get(dev, NULL);
\tif (IS_ERR(clk)) {
\t\tdev_err(dev, \"failed to get input clock\\n\");
\t\treturn PTR_ERR(clk);
\t}

\tret = clk_prepare_enable(clk);
\tif (ret) {
\t\tdev_err(dev, \"failed to setup clock\\n\");
\t\treturn ret;
\t}
\tret = devm_add_action_or_reset(dev, qcom_clk_disable_unprepare, clk);
\tif (ret)
\t\treturn ret;

\t/*
\t * We use the clock rate to calculate the max timeout, so ensure it's
\t * not zero to avoid a divide-by-zero exception.
\t *
\t * WATCHDOG_CORE assumes units of seconds, if the WDT is clocked such
\t * that it would bite before a second elapses it's usefulness is
\t * limited.  Bail if this is the case.
\t */
\twdt->rate = clk_get_rate(clk);
""",
        """\tif (data->fixed_rate) {
\t\t/*
\t\t * The downstream qcom,msm-watchdog binding has no clock
\t\t * phandle. Qualcomm's vendor driver uses a 32765 Hz counter.
\t\t */
\t\twdt->rate = data->fixed_rate;
\t} else {
\t\tclk = devm_clk_get(dev, NULL);
\t\tif (IS_ERR(clk)) {
\t\t\tdev_err(dev, \"failed to get input clock\\n\");
\t\t\treturn PTR_ERR(clk);
\t\t}

\t\tret = clk_prepare_enable(clk);
\t\tif (ret) {
\t\t\tdev_err(dev, \"failed to setup clock\\n\");
\t\t\treturn ret;
\t\t}
\t\tret = devm_add_action_or_reset(dev,
\t\t\t\t\t      qcom_clk_disable_unprepare, clk);
\t\tif (ret)
\t\t\treturn ret;

\t\twdt->rate = clk_get_rate(clk);
\t}

\t/*
\t * We use the clock rate to calculate the max timeout, so ensure it's
\t * not zero to avoid a divide-by-zero exception.
\t *
\t * WATCHDOG_CORE assumes units of seconds, if the WDT is clocked such
\t * that it would bite before a second elapses it's usefulness is
\t * limited.  Bail if this is the case.
\t */
""",
        "bridge fixed watchdog rate",
    )

    text = replace_once(
        text,
        """\tif (readl(wdt_addr(wdt, WDT_STS)) & 1)
\t\twdt->wdd.bootstatus = WDIOF_CARDRESET;
""",
        """\trunning = readl(wdt_addr(wdt, WDT_STS)) & 1;
\tif (running)
\t\twdt->wdd.bootstatus = WDIOF_CARDRESET;
""",
        "detect firmware-enabled watchdog",
    )

    text = replace_once(
        text,
        """\twdt->wdd.timeout = min(wdt->wdd.max_timeout, 30U);
\twatchdog_init_timeout(&wdt->wdd, 0, dev);

\tret = devm_watchdog_register_device(dev, &wdt->wdd);
""",
        """\twdt->wdd.timeout = min(wdt->wdd.max_timeout, 30U);
\twatchdog_init_timeout(&wdt->wdd, 0, dev);

\t/*
\t * Firmware may leave the watchdog running. Reprogram it before the
\t * boot timeout expires and tell the watchdog core to keep pinging it
\t * until userspace takes ownership.
\t */
\tif (running) {
\t\tqcom_wdt_start(&wdt->wdd);
\t\tset_bit(WDOG_HW_RUNNING, &wdt->wdd.status);
\t}

\tret = devm_watchdog_register_device(dev, &wdt->wdd);
""",
        "handoff firmware-enabled watchdog",
    )

    text = replace_once(
        text,
        """static const struct of_device_id qcom_wdt_of_table[] = {
\t{ .compatible = \"qcom,kpss-timer\", .data = &match_data_apcs_tmr },
""",
        """static const struct of_device_id qcom_wdt_of_table[] = {
\t{ .compatible = \"qcom,msm-watchdog\", .data = &match_data_msm },
\t{ .compatible = \"qcom,kpss-timer\", .data = &match_data_apcs_tmr },
""",
        "add downstream compatible",
    )

    source.write_text(text)
    output.mkdir(parents=True, exist_ok=True)

    patch = subprocess.run(
        ["git", "-C", str(gki), "diff", "--", "drivers/watchdog/qcom-wdt.c"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    if not patch.strip():
        raise SystemExit("watchdog bridge produced no source diff")

    patch_path = output / "a52xq-msm-watchdog.patch"
    patch_path.write_text(patch)
    (output / "patched-qcom-wdt.c").write_text(text)

    required = (
        'qcom,msm-watchdog',
        'fixed_rate = 32765',
        'set_bit(WDOG_HW_RUNNING',
        'data->fixed_rate',
    )
    missing = [needle for needle in required if needle not in text]
    if missing:
        raise SystemExit("missing watchdog bridge contracts: " + ", ".join(missing))

    report = [
        "# A52xq MSM watchdog compatibility bridge",
        "",
        "- Downstream compatible: `qcom,msm-watchdog`",
        "- Register layout: upstream KPSS offsets",
        "- Downstream fixed counter rate: `32765 Hz`",
        "- Firmware-running handoff: `WDOG_HW_RUNNING`",
        "- Kernel pre-userspace pinger: requires `CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED=y`",
        "",
        f"- Original source SHA-256: `{hashlib.sha256(original.encode()).hexdigest()}`",
        f"- Patched source SHA-256: `{sha256(output / 'patched-qcom-wdt.c')}`",
    ]
    (output / "WATCHDOG-BRIDGE-REPORT.md").write_text("\n".join(report) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
