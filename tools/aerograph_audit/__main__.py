"""CLI: .venv/bin/python -m tools.aerograph_audit AEROGRAPH --out docs/audit."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from . import entities, report, semantics, states
from .core import Audit

WORKSPACE = Path(__file__).resolve().parents[2]


def audit_tree(root: Path | str) -> tuple[Audit, dict]:
    audit = Audit(root)
    git = audit.git_state()
    audit.load()
    states.run(audit)
    semantics.prepare(audit)
    entities.run(audit)
    semantics.run(audit)
    # Detect a concurrently edited input snapshot without mutating or rerunning upstream builds.
    import hashlib

    for path, entry in audit.inventory.items():
        current = audit.root / path
        if (
            not current.is_file()
            or hashlib.sha256(current.read_bytes()).hexdigest() != entry["sha256"]
        ):
            audit.add(
                "input.changed_during_audit",
                "blocker",
                path,
                "Input changed during the read-only audit; rerun for a consistent snapshot",
            )
    audit.finish()
    report.summarize(audit)
    return audit, git


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, default=Path("docs/audit"))
    parser.add_argument("--examples", type=int, default=3)
    parser.add_argument(
        "--fail-on-blocker",
        action="store_true",
        help="Return 1 when blocker findings exist (default is success after report generation).",
    )
    args = parser.parse_args(argv)
    output = args.out.resolve()
    if not output.is_relative_to(WORKSPACE) or output.is_relative_to(
        args.root.resolve()
    ):
        parser.error(
            "Output must be inside the aerokernel workspace and outside the audited source root"
        )
    if args.examples < 1:
        parser.error("--examples must be at least 1")
    try:
        audit, git = audit_tree(args.root)
        report.write(audit, git, output, args.examples)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f"Audit could not complete: {error}\n")
    counts = Counter(f["severity"] for f in audit.findings)
    print(
        json.dumps(
            {
                "entities": len(audit.entities),
                "fields": len(audit.fields),
                "relations": len(audit.relations),
                "findings": len(audit.findings),
                "severity": dict(sorted(counts.items())),
                "out": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if args.fail_on_blocker and counts["blocker"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
