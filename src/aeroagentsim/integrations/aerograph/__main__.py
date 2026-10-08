"""Compile and inspect pinned AeroGraph registry snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aerokernel.errors import KernelError
from aerokernel.values import thaw

from . import (
    CompiledRegistry,
    CompileError,
    Policy,
    Selection,
    compile_registry,
    read_snapshot,
)


def csv(value: str | None) -> tuple[str, ...] | None:
    return (
        None
        if value is None
        else tuple(x.strip() for x in value.split(",") if x.strip())
    )


def report(compiled: CompiledRegistry) -> str:
    lines = [
        "# AeroGraph registry compilation",
        "",
        f"Digest: `{compiled.digest}`",
        f"Kernel digest: `{compiled.registry.digest}`",
        "",
        "| Statistic | Count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {k} | {v} |" for k, v in compiled.statistics.items())
    lines.extend(
        [
            "",
            "## Source provenance",
            "",
            json.dumps(
                thaw(compiled.provenance["git"]), ensure_ascii=False, sort_keys=True
            ),
            "",
            "## Research disposition",
            "",
            "Admission preserves source review; producer hints grant no runtime authority.",
            "",
            "## Exclusions",
            "",
        ]
    )
    lines.extend(f"- `{x['id']}`: {x['reason']}" for x in compiled.exclusions)
    lines.extend(["", "## Normalizations", ""])
    lines.extend(
        f"- `{x['id']}`: {x['rule']} at `{x['file']}#{x['pointer']}`"
        for x in compiled.normalizations
    )
    return "\n".join(lines) + "\n"


def inspect_type(compiled: CompiledRegistry, id: str) -> dict[str, Any]:
    return {
        "type": id,
        "digest": compiled.digest,
        "fields": [
            {
                "id": f.id,
                "declaring_type": f.declaring_type,
                "schema": thaw(f.schema),
                "unit": thaw(f.metadata["unit"]),
                "numeric_units": thaw(f.metadata["numeric_units"]),
                "frame": thaw(f.metadata["frame"]),
                "role": f.metadata["role"],
                "time": thaw(f.metadata["time"]),
                "temporal_declarations": thaw(f.metadata["temporal_declarations"]),
                "research_admitted": f.metadata["research_admitted"],
                "producer_hints": thaw(compiled.producer_hints[f.id]),
            }
            for f in compiled.effective_fields(id)
        ],
        "relations": [thaw(r) for r in compiled.effective_relations(id)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compile_cmd = commands.add_parser("compile", help="compile a selected source slice")
    compile_cmd.add_argument("--root", required=True, type=Path)
    compile_cmd.add_argument("--select", required=True)
    compile_cmd.add_argument("--fields", help="comma-separated field ID allow-list")
    compile_cmd.add_argument(
        "--relations", help="comma-separated relation ID allow-list"
    )
    compile_cmd.add_argument(
        "--admit-proposed",
        action="store_true",
        help="explicit research admission; retains review status",
    )
    compile_cmd.add_argument(
        "--admit", help="comma-separated individually admitted IDs"
    )
    compile_cmd.add_argument(
        "--exclude", help="comma-separated intentionally excluded IDs"
    )
    compile_cmd.add_argument("--out", required=True, type=Path)
    compile_cmd.add_argument("--report", type=Path)
    inspect_cmd = commands.add_parser(
        "inspect", help="list effective fields and relations from a snapshot"
    )
    inspect_cmd.add_argument("snapshot", nargs="?", type=Path)
    inspect_cmd.add_argument("--snapshot", dest="snapshot_option", type=Path)
    inspect_cmd.add_argument("--type", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            compiled = compile_registry(
                args.root,
                Selection(
                    csv(args.select) or (), csv(args.fields), csv(args.relations)
                ),
                Policy(
                    args.admit_proposed, csv(args.admit) or (), csv(args.exclude) or ()
                ),
            )
            compiled.write_snapshot(args.out)
            if args.report is not None:
                args.report.write_text(report(compiled), encoding="utf-8")
            print(
                json.dumps(
                    {"digest": compiled.digest, **dict(compiled.statistics)},
                    sort_keys=True,
                )
            )
        else:
            path = args.snapshot_option or args.snapshot
            if path is None:
                raise ValueError("inspect requires a snapshot path or --snapshot")
            print(
                json.dumps(
                    inspect_type(read_snapshot(path), args.type),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
            )
    except (CompileError, KernelError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
