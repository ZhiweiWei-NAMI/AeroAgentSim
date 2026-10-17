#!/usr/bin/env python3
"""Export all rescue attempts and missing case coverage without running a service."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import shutil
import tarfile
from collections import Counter
from pathlib import Path

from run_case import ROOT, cases, write_json
from verify_run import verify


def required_cases() -> list[tuple]:
    required = []
    for case in cases():
        for mode in case["required_agent_modes"]:
            seeds = (
                case.get("model_mode_minimum_seeds", case["seeds"])
                if mode == "model"
                else case["seeds"]
            )
            variants = (
                case.get("model_mode_minimum_variants", case.get("variants", [None]))
                if mode == "model"
                else case.get("variants", [None])
            )
            required.extend(
                (case["id"], seed, mode, variant)
                for seed in seeds
                for variant in variants
            )
    return required


def export(root: Path, output: Path, archive: bool) -> dict:
    root, output = root.resolve(), output.resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError("export destination must be outside the retained run root")
    if not root.is_dir():
        raise ValueError("run root does not exist; there is no evidence to export")
    output.mkdir(parents=True, exist_ok=False)
    reports = []
    for path in sorted(root.glob("*/run-manifest.json")):
        report = verify(path.parent)
        report["directory"] = path.parent.relative_to(root).as_posix()
        reports.append(report)
    covered = {
        (r.get("case_id"), r.get("seed"), r.get("agent_mode"), r.get("variant_id"))
        for r in reports
        if r.get("execution_status") == "FINISHED"
        and r.get("execution_scope") not in {"replay_only", "scoring_only"}
    }
    missing = [
        {"case_id": c, "seed": s, "agent_mode": m, "variant_id": v}
        for c, s, m, v in required_cases()
        if (c, s, m, v) not in covered
    ]
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(
                f"refusing symlink in exported evidence: {path.relative_to(root)}"
            )
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                }
            )
    # Preserve the human-reviewed matrix verbatim. A successful import, zero
    # checks, or process exit code cannot upgrade NOT_RUN rows to PASS.
    matrix = ROOT / "docs/rescue-companion/acceptance.csv"
    statuses = Counter()
    if matrix.is_file():
        shutil.copyfile(matrix, output / "acceptance.csv")
        with matrix.open(encoding="utf-8-sig", newline="") as stream:
            statuses.update(row["status"] for row in csv.DictReader(stream))
    summary = {
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "FAIL" if any(r["status"] == "FAIL" for r in reports) else "BLOCKED",
        "note": "All attempts, including blocked and failed, are retained. Partial artifact checks do not establish whole-case completion.",
        "runs": reports,
        "missing_executed_cases": missing,
        "required_case_combinations": len(required_cases()),
        "acceptance_row_counts": dict(statuses),
        "files": files,
        "total_bytes": sum(row["bytes"] for row in files),
    }
    write_json(output / "evidence-index.json", summary)
    lines = [
        "# Rescue companion evidence",
        "",
        f"Status: {summary['status']}",
        "",
        "This is an inventory of preserved execution evidence, not a passed acceptance claim.",
        f"Retained attempts: {len(reports)}. Required combinations without a finished execution: {len(missing)}.",
        "",
        "## Attempts",
        "",
    ]
    for report in reports:
        lines.append(
            f"- {report['run_id']}: execution {report.get('execution_status', 'unknown')}; evidence {report['status']}. {report.get('reason', '')}"
        )
    if not reports:
        lines.append(
            "No native case attempts were recorded. Host/build diagnostics may still be included."
        )
    lines += [
        "",
        "## Review and recovery",
        "",
        "Read evidence-index.json for every file and missing case combination; acceptance.csv retains the current review state.",
        "Keep earlier failures when adding a new attempt. Source logs and sensor data are never modified by this export.",
        "Runtime secrets must not be stored in run directories. Review artifact contents before sharing outside the owner's private storage.",
        "",
    ]
    if archive:
        with tarfile.open(output / "runs.tar.gz", "w:gz") as bundle:
            for row in files:
                bundle.add(
                    root / row["path"], arcname="runs/" + row["path"], recursive=False
                )
        lines.append(
            "runs.tar.gz contains the listed source files, including baseline/build diagnostics."
        )
    (output / "README.md").write_text("\n".join(lines) + "\n")
    return {
        "output": str(output),
        "status": summary["status"],
        "retained_attempts": len(reports),
        "missing_executed_cases": len(missing),
        "total_bytes": summary["total_bytes"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=ROOT / "runs/acceptance")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--archive",
        action="store_true",
        help="Also copy all retained raw evidence into runs.tar.gz",
    )
    args = parser.parse_args()
    destination = args.out or ROOT / "runs/exports" / dt.datetime.now(
        dt.timezone.utc
    ).strftime("rescue-%Y%m%dT%H%M%S%fZ")
    try:
        print(json.dumps(export(args.run_root, destination, args.archive), indent=2))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0  # The export succeeded; its recorded acceptance status may be BLOCKED.


if __name__ == "__main__":
    raise SystemExit(main())
