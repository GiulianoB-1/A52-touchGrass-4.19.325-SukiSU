#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

GKI_SHA = "f960ed27302b1ff8e61e152fc202554d778deccd"
UPSTREAM_SHA = "830b3c68c1fb1e9176028d02ef86f3cf76aa2476"

PROBES = {
    "icc-core": {
        "target": "drivers/interconnect/core.o,drivers/interconnect/bulk.o",
        "config": "CONFIG_INTERCONNECT",
        "description": "Generic Linux interconnect core",
        "enable": ("CONFIG_INTERCONNECT",),
        "sources": (
            "drivers/interconnect/core.c",
            "drivers/interconnect/bulk.c",
        ),
    },
    "qcom-bcm-voter": {
        "target": "drivers/interconnect/qcom/bcm-voter.o",
        "config": "CONFIG_INTERCONNECT_QCOM_BCM_VOTER",
        "description": "Qualcomm Bus Clock Manager voter",
        "enable": (
            "CONFIG_INTERCONNECT",
            "CONFIG_QCOM_RPMH",
            "CONFIG_QCOM_COMMAND_DB",
            "CONFIG_INTERCONNECT_QCOM",
            "CONFIG_INTERCONNECT_QCOM_BCM_VOTER",
        ),
        "sources": ("drivers/interconnect/qcom/bcm-voter.c",),
    },
    "qcom-icc-rpmh": {
        "target": "drivers/interconnect/qcom/icc-rpmh.o",
        "config": "CONFIG_INTERCONNECT_QCOM_RPMH",
        "description": "Qualcomm RPMh interconnect backend",
        "enable": (
            "CONFIG_INTERCONNECT",
            "CONFIG_QCOM_RPMH",
            "CONFIG_QCOM_COMMAND_DB",
            "CONFIG_INTERCONNECT_QCOM",
            "CONFIG_INTERCONNECT_QCOM_RPMH",
        ),
        "sources": ("drivers/interconnect/qcom/icc-rpmh.c",),
    },
    "sm6350-provider": {
        "target": "drivers/interconnect/qcom/sm6350.o",
        "config": "CONFIG_INTERCONNECT_QCOM_SM6350",
        "description": "Backported Qualcomm SM6350/Lagoon NoC provider",
        "enable": (
            "CONFIG_INTERCONNECT",
            "CONFIG_QCOM_RPMH",
            "CONFIG_QCOM_COMMAND_DB",
            "CONFIG_INTERCONNECT_QCOM",
            "CONFIG_INTERCONNECT_QCOM_RPMH_POSSIBLE",
            "CONFIG_INTERCONNECT_QCOM_RPMH",
            "CONFIG_INTERCONNECT_QCOM_BCM_VOTER",
            "CONFIG_INTERCONNECT_QCOM_SM6350",
        ),
        "sources": (
            "drivers/interconnect/qcom/sm6350.c",
            "drivers/interconnect/qcom/sm6350.h",
            "include/dt-bindings/interconnect/qcom,sm6350.h",
        ),
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(errors="replace")
    if marker not in text:
        path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n")


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def adapt_sm6350_provider(path: Path) -> None:
    text = path.read_text(errors="replace")
    text = text.replace(
        "static struct qcom_icc_bcm * const ",
        "static struct qcom_icc_bcm *",
    )
    text = text.replace(
        "static struct qcom_icc_node * const ",
        "static struct qcom_icc_node *",
    )
    text = text.replace(
        "static const struct qcom_icc_desc ",
        "static struct qcom_icc_desc ",
    )

    marker = "static const struct of_device_id qnoc_of_match[] = {"
    if marker not in text:
        raise SystemExit("SM6350 device-match marker not found")

    compatibility = r'''static int qnoc_probe(struct platform_device *pdev)
{
	const struct qcom_icc_desc *desc;
	struct icc_onecell_data *data;
	struct icc_provider *provider;
	struct qcom_icc_node **qnodes;
	struct qcom_icc_provider *qp;
	struct icc_node *node;
	size_t num_nodes, i;
	int ret;

	desc = device_get_match_data(&pdev->dev);
	if (!desc)
		return -EINVAL;

	qnodes = desc->nodes;
	num_nodes = desc->num_nodes;

	qp = devm_kzalloc(&pdev->dev, sizeof(*qp), GFP_KERNEL);
	if (!qp)
		return -ENOMEM;

	data = devm_kcalloc(&pdev->dev, num_nodes, sizeof(*node), GFP_KERNEL);
	if (!data)
		return -ENOMEM;

	provider = &qp->provider;
	provider->dev = &pdev->dev;
	provider->set = qcom_icc_set;
	provider->pre_aggregate = qcom_icc_pre_aggregate;
	provider->aggregate = qcom_icc_aggregate;
	provider->xlate_extended = qcom_icc_xlate_extended;
	INIT_LIST_HEAD(&provider->nodes);
	provider->data = data;

	qp->dev = &pdev->dev;
	qp->bcms = desc->bcms;
	qp->num_bcms = desc->num_bcms;

	qp->voter = of_bcm_voter_get(qp->dev, NULL);
	if (IS_ERR(qp->voter))
		return PTR_ERR(qp->voter);

	ret = icc_provider_add(provider);
	if (ret) {
		dev_err(&pdev->dev, "error adding interconnect provider\n");
		return ret;
	}

	for (i = 0; i < qp->num_bcms; i++)
		qcom_icc_bcm_init(qp->bcms[i], &pdev->dev);

	for (i = 0; i < num_nodes; i++) {
		size_t j;

		if (!qnodes[i])
			continue;

		node = icc_node_create(qnodes[i]->id);
		if (IS_ERR(node)) {
			ret = PTR_ERR(node);
			goto err;
		}

		node->name = qnodes[i]->name;
		node->data = qnodes[i];
		icc_node_add(node, provider);

		for (j = 0; j < qnodes[i]->num_links; j++)
			icc_link_create(node, qnodes[i]->links[j]);

		data->nodes[i] = node;
	}
	data->num_nodes = num_nodes;

	platform_set_drvdata(pdev, qp);
	return 0;
err:
	icc_nodes_remove(provider);
	icc_provider_del(provider);
	return ret;
}

static int qnoc_remove(struct platform_device *pdev)
{
	struct qcom_icc_provider *qp = platform_get_drvdata(pdev);

	icc_nodes_remove(&qp->provider);
	return icc_provider_del(&qp->provider);
}

'''
    text = text.replace(marker, compatibility + marker, 1)
    text = text.replace(".probe = qcom_icc_rpmh_probe,", ".probe = qnoc_probe,")
    text = text.replace(".remove = qcom_icc_rpmh_remove,", ".remove = qnoc_remove,")

    forbidden = (
        "qcom_icc_rpmh_probe",
        "qcom_icc_rpmh_remove",
        "static struct qcom_icc_bcm * const ",
        "static struct qcom_icc_node * const ",
        "static const struct qcom_icc_desc ",
    )
    leftovers = [token for token in forbidden if token in text]
    if leftovers:
        raise SystemExit(f"unsupported SM6350 tokens remain: {leftovers}")
    if "static int qnoc_probe" not in text or ".probe = qnoc_probe" not in text:
        raise SystemExit("GKI 5.10 SM6350 probe compatibility was not installed")
    path.write_text(text)


def stage(args: argparse.Namespace) -> None:
    gki = args.gki.resolve()
    upstream = args.upstream.resolve()
    artifact = args.output.resolve()

    gki_head = output("git", "-C", str(gki), "rev-parse", "HEAD")
    if gki_head != GKI_SHA:
        raise SystemExit(f"unexpected GKI revision: {gki_head}")

    upstream_map = {
        upstream / "drivers/interconnect/qcom/sm6350.c":
            gki / "drivers/interconnect/qcom/sm6350.c",
        upstream / "drivers/interconnect/qcom/sm6350.h":
            gki / "drivers/interconnect/qcom/sm6350.h",
        upstream / "include/dt-bindings/interconnect/qcom,sm6350.h":
            gki / "include/dt-bindings/interconnect/qcom,sm6350.h",
    }
    artifact.mkdir(parents=True, exist_ok=True)
    staged_rows: list[dict[str, str]] = []
    staged_paths: list[str] = []
    for source, destination in upstream_map.items():
        if not source.is_file():
            raise SystemExit(f"missing pinned upstream source: {source}")
        before = sha256(destination) if destination.is_file() else "<absent>"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if destination.name == "sm6350.c":
            adapt_sm6350_provider(destination)
        relative = destination.relative_to(gki).as_posix()
        staged_paths.append(relative)
        staged_rows.append({
            "relative_path": relative,
            "upstream_sha256": sha256(source),
            "gki_before_sha256": before,
            "gki_after_sha256": sha256(destination),
        })

    append_once(
        gki / "drivers/interconnect/qcom/Makefile",
        "qnoc-sm6350-objs",
        "qnoc-sm6350-objs := sm6350.o\n"
        "obj-$(CONFIG_INTERCONNECT_QCOM_SM6350) += qnoc-sm6350.o",
    )
    append_once(
        gki / "drivers/interconnect/qcom/Kconfig",
        "config INTERCONNECT_QCOM_SM6350",
        """config INTERCONNECT_QCOM_SM6350
\ttristate \"Qualcomm SM6350 interconnect driver\"
\tdepends on INTERCONNECT_QCOM_RPMH_POSSIBLE
\tselect INTERCONNECT_QCOM_RPMH
\tselect INTERCONNECT_QCOM_BCM_VOTER
\thelp
\t  Backported SM6350/Lagoon Network-on-Chip provider compile probe.""",
    )
    staged_paths.extend([
        "drivers/interconnect/qcom/Makefile",
        "drivers/interconnect/qcom/Kconfig",
    ])

    write_tsv(
        artifact / "staged-files.tsv",
        ["relative_path", "upstream_sha256", "gki_before_sha256", "gki_after_sha256"],
        staged_rows,
    )

    probe_rows: list[dict[str, str]] = []
    for probe, data in PROBES.items():
        missing = [relative for relative in data["sources"] if not (gki / relative).is_file()]
        probe_rows.append({
            "probe": probe,
            "description": str(data["description"]),
            "target": str(data["target"]),
            "config_symbol": str(data["config"]),
            "enable_symbols": ",".join(data["enable"]),
            "source_files": ",".join(data["sources"]),
            "missing_sources": ",".join(missing),
        })
    write_tsv(
        artifact / "probe-plan.tsv",
        [
            "probe", "description", "target", "config_symbol",
            "enable_symbols", "source_files", "missing_sources",
        ],
        probe_rows,
    )

    subprocess.run(
        ["git", "-C", str(gki), "add", "-N", "--", *sorted(set(staged_paths))],
        check=True,
    )
    patch = output("git", "-C", str(gki), "diff", "--binary", "--no-ext-diff")
    if not patch:
        raise SystemExit("SM6350 interconnect staging produced no GKI diff")
    (artifact / "sm6350-interconnect-port.patch").write_text(patch + "\n")

    metadata = [
        "artifact_type=a52xq-gki-5.10-sm6350-interconnect-compile-probe-not-flashable",
        f"gki_commit={gki_head}",
        f"upstream_linux_commit={UPSTREAM_SHA}",
        f"planned_probes={len(PROBES)}",
        "probe_scope=icc-core,bcm-voter,icc-rpmh,sm6350-provider",
        "provider_source=linux-v6.1-sm6350",
        "compatibility=GKI-5.10-local-probe-remove-and-nonconst-descriptors",
        "output_scope=individual-object-compilation-only",
    ]
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")


def diagnostics(path: Path, limit: int = 20) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    patterns = (
        "error:", "fatal error:", "undefined reference", "No rule to make target",
        "No such file or directory", "implicit declaration", "warning:",
    )
    selected: list[str] = []
    lines = path.read_text(errors="replace").splitlines()
    for line in lines:
        if any(pattern.lower() in line.lower() for pattern in patterns):
            cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        if len(selected) >= limit:
            break
    return selected or [line.strip() for line in lines[-12:] if line.strip()] or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    artifact = args.output.resolve()
    status = args.status_file.resolve()
    with status.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if {row.get("probe") for row in rows} != set(PROBES):
        raise SystemExit("SM6350 interconnect compile status probe set mismatch")
    shutil.copy2(status, artifact / "compile-status.tsv")

    compiled = sum(row.get("result") == "compiled" for row in rows)
    failed = sum(row.get("result") == "compile-failed" for row in rows)
    blocked = sum(row.get("result") == "config-blocked" for row in rows)
    missing = sum(row.get("result") == "source-missing" for row in rows)
    report = [
        "# A52xq GKI 5.10 SM6350 interconnect probe",
        "",
        "## Result",
        "",
        f"- compiled: **{compiled}**",
        f"- compile failures: **{failed}**",
        f"- Kconfig blocked: **{blocked}**",
        f"- source missing: **{missing}**",
        "",
        "The SM6350 provider is backported from the pinned Linux 6.1 release source.",
        "The artifact is an object-level compile probe and is not flashable.",
        "",
    ]
    for row in rows:
        probe = row["probe"]
        report.extend([
            f"### `{probe}`",
            "",
            f"- target: `{row['target']}`",
            f"- symbol: `{row['config_symbol']}` resolved to `{row['resolved_value']}`",
            f"- result: **{row['result']}**",
            f"- exit code: `{row['exit_code']}`",
            f"- object produced: `{row['object_produced']}`",
            "",
            "First diagnostics:",
            "",
        ])
        report.extend(
            f"- `{line.replace('`', chr(39))}`"
            for line in diagnostics(artifact / "logs" / f"{probe}.log")
        )
        report.append("")
    (artifact / "PORTING-PROBE-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = (artifact / "analysis-metadata.txt").read_text().rstrip().splitlines()
    metadata.extend([
        f"compiled_success={compiled}",
        f"compile_failed={failed}",
        f"config_blocked={blocked}",
        f"source_missing={missing}",
    ])
    (artifact / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    files = sorted(
        path for path in artifact.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    with (artifact / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(artifact).as_posix()}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--gki", type=Path, required=True)
    stage_parser.add_argument("--upstream", type=Path, required=True)
    stage_parser.add_argument("--output", type=Path, required=True)
    stage_parser.set_defaults(func=stage)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.add_argument("--status-file", type=Path, required=True)
    finalize_parser.set_defaults(func=finalize)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
