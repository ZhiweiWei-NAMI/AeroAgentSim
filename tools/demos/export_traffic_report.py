"""Export a verifiable Markdown report from an immutable recorded run prefix."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from aeroagentsim.observations.artifacts import ArtifactStore
from aeroagentsim.observations.contracts import content_digest
from aeroagentsim.services.projector import project
from aeroagentsim.services.storage import RunStorage
from aeroagentsim.services.subjects import projection_context


def cell(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        .replace("|", "\\|")
        .replace("\n", " ")
    )


def export_report(
    run: Path,
    output: Path,
    *,
    decision_schemas: tuple[str, ...] = ("aas.agent.record",),
) -> Path:
    run, output = run.resolve(), output.resolve()
    if output == run or output.is_relative_to(run):
        raise ValueError("report output must be outside the immutable source run")
    storage = RunStorage(run)
    manifest = storage.metadata()
    if manifest["status"] not in {"completed", "stopped", "faulted", "interrupted"}:
        raise ValueError(
            "report requires a finished run with an immutable recorded prefix"
        )
    subjects = projection_context(run, 1)
    commits: list[dict[str, Any]] = []
    cursor = 1
    while batch := storage.records(cursor, 4096):
        for offset, record in enumerate(batch):
            if record["index"] != cursor + offset:
                raise ValueError("report journal has a gap or unordered indexed prefix")
            commits.append(project(record, subjects=subjects))
        cursor = batch[-1]["index"] + 1
    if not commits:
        raise ValueError("run has no committed journal records to report")
    if cursor != manifest["final_cursor"]:
        raise ValueError("run index does not cover the manifest final cursor")
    output.mkdir(parents=True, exist_ok=True)
    journal_hash = content_digest((run / "journal.jsonl").read_bytes())
    lines = [
        "# Traffic accident run report",
        "",
        "All values below come from the pinned manifest, journal/feed projection and verified stored artifacts.",
        "No simulation, model or camera is called during export.",
        "",
        "## Recorded run",
        "",
        "| Property | Recorded value |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| {key} | `{cell(value)}` |" for key, value in sorted(manifest.items())
    )
    lines.extend(
        [
            f"| journal_sha256 | `{journal_hash}` |",
            "",
            "## Timeline",
            "",
            "| Cut | Source time ns / microstep | Record |",
            "| --- | --- | --- |",
        ]
    )
    messages: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    last_facts: dict[tuple[str, Any, str], dict[str, Any]] = {}
    created: Counter[str] = Counter()
    removed = 0
    fact_count = 0
    for commit in commits:
        cut, at = commit["commitIndex"], commit["at"]
        coordinates = f"{at['ns']} / {at['microstep']}"
        for entity in commit["created"]:
            created[entity["typeId"]] += 1
            lines.append(f"| {cut} | {coordinates} | created `{cell(entity)}` |")
        removed += len(commit["removed"])
        for message in commit["messages"]:
            messages.append({**message, "cut": cut})
            lines.append(
                f"| {cut} | {coordinates} | {message['kind']} `{message['schemaId']}`: `{cell(message['payload'])}` |"
            )
        for receipt in commit["receipts"]:
            receipts.append({**receipt, "cut": cut, "at": at})
            lines.append(
                f"| {cut} | {coordinates} | receipt `{receipt['commandId']}`: `{cell(receipt)}` |"
            )
        for fact in commit["facts"]:
            fact_count += 1
            key = (fact["entity"]["id"], fact["entity"]["generation"], fact["fieldId"])
            last_facts[key] = {**fact, "cut": cut}
        for retraction in commit["retracted"]:
            key = (
                retraction["entity"]["id"],
                retraction["entity"]["generation"],
                retraction["fieldId"],
            )
            last_facts[key] = {**retraction, "cut": cut}
    if not messages and not receipts:
        lines.append("No messages or receipts are recorded in this prefix.")
    lines.extend(["", "## Decisions and model records", ""])
    # Schema selection is explicit; the timeline retains all messages, including
    # contracts this exporter has not been configured to categorize.
    decisions = [
        message for message in messages if message["schemaId"] in decision_schemas
    ]
    if not decisions:
        lines.append(
            "No records match the selected decision schemas in this prefix; model call totals are unavailable. All typed messages remain in the timeline."
        )
    else:
        phases = Counter(
            message["payload"]["phase"]
            for message in decisions
            if isinstance(message["payload"], dict)
            and isinstance(message["payload"].get("phase"), str)
        )
        if phases:
            lines.extend(
                [
                    "Recorded phase counts (response records are not inferred model attempts):",
                    "",
                    "```json",
                    json.dumps(dict(phases), sort_keys=True, indent=2),
                    "```",
                    "",
                ]
            )
        for message in decisions:
            payload = message["payload"]
            lines.extend(
                [
                    f"### Cut {message['cut']}: {message['schemaId']}",
                    "",
                    "```json",
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    "```",
                    "",
                ]
            )
            if isinstance(payload, dict) and "data_json" in payload:
                # A malformed recorded payload is a reporting error, never a
                # fabricated successful decision or zero usage count.
                data = json.loads(payload["data_json"])
                lines.extend(
                    [
                        "Recorded phase data:",
                        "",
                        "```json",
                        json.dumps(data, indent=2, ensure_ascii=False),
                        "```",
                        "",
                    ]
                )
    lines.extend(
        ["", "## Receipts", "", "| Status | Recorded receipt count |", "| --- | --- |"]
    )
    if receipts:
        lines.extend(
            f"| {status} | {count} |"
            for status, count in sorted(Counter(r["status"] for r in receipts).items())
        )
    else:
        lines.append("No command receipts are recorded in this prefix.")
    lines.extend(
        [
            "",
            "## Photos and artifact evidence",
            "",
            "Stored bytes alone do not establish upload/edge/task acceptance; use the receipts and typed events above.",
            "",
        ]
    )
    store = ArtifactStore(run)
    artifacts = store.list()
    if not artifacts:
        lines.append("No capture artifact records are stored for this run.")
    else:
        photos = output / "photos"
        photos.mkdir(exist_ok=True)
        for record in artifacts:
            request = record["request"]
            content = record["digest"]
            target = photos / (content + ".png")
            target.write_bytes(store.read(content))
            lines.extend(
                [
                    f"### {request['request_id']}",
                    "",
                    f"![Stored capture](photos/{content}.png)",
                    "",
                    "```json",
                    json.dumps(record, ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
    lines.extend(
        [
            "",
            "## Metrics from recorded facts",
            "",
            f"Committed records in this prefix: {len(commits)}; published facts: {fact_count}; recorded removals: {removed}.",
            "",
            "Created identities by declared type:",
            "",
            "```json",
            json.dumps(dict(created), sort_keys=True, indent=2),
            "```",
            "",
            "The table lists the last **published** value/retraction of each slot with its validity and evidence cut. It is not an interpolated final-state measurement.",
            "",
            "| Entity / generation | Field | Last published evidence |",
            "| --- | --- | --- |",
        ]
    )
    for (entity_id, generation, field), fact in sorted(
        last_facts.items(), key=lambda item: str(item[0])
    ):
        lines.append(f"| {entity_id} / {generation} | {field} | `{cell(fact)}` |")
    target = output / "report.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--decision-schema",
        action="append",
        help="Explicit recorded decision schema; repeat for graph/node contracts",
    )
    parser.add_argument("--docx", action="store_true")
    parser.add_argument("--pdf", action="store_true")
    args = parser.parse_args(argv)
    try:
        target = export_report(
            args.run,
            args.output,
            decision_schemas=tuple(args.decision_schema)
            if args.decision_schema is not None
            else ("aas.agent.record",),
        )
        print(target)
        if args.docx or args.pdf:
            pandoc = shutil.which("pandoc")
            if pandoc is None:
                raise RuntimeError(
                    "optional DOCX/PDF export unavailable: pandoc is not installed; Markdown was exported"
                )
            if args.docx:
                subprocess.run(
                    [pandoc, str(target), "-o", str(target.with_suffix(".docx"))],
                    cwd=target.parent,
                    check=True,
                )
            if args.pdf:
                pdf_engine = shutil.which("xelatex") or shutil.which("pdflatex")
                if pdf_engine is None:
                    raise RuntimeError(
                        "optional PDF export unavailable: no pandoc LaTeX engine; Markdown was exported"
                    )
                subprocess.run(
                    [
                        pandoc,
                        str(target),
                        "--pdf-engine",
                        pdf_engine,
                        "-o",
                        str(target.with_suffix(".pdf")),
                    ],
                    cwd=target.parent,
                    check=True,
                )
        return 0
    except (
        ValueError,
        KeyError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
