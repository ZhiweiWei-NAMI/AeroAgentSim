#!/usr/bin/env python3
"""Restricted urban policy process: authorized inputs and Gateway calls only."""
from __future__ import annotations

import argparse
import json
import sys

from aero_bench.agent.runtime import AgentContext, AgentFrameworkError
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.participant import UrbanParticipant, UrbanParticipantConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one authorized urban recovery participant")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config-asset", required=True)
    run.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args(argv)
    try:
        context = AgentContext.from_environment()
        if args.config_asset != f"asset.participant.{context.agent_id}":
            raise AgentFrameworkError("participant can load only its own declared config asset")
        # AgentContext verifies the declared file digest; no arbitrary filesystem
        # path, task package, verifier input, or Provider-private state is read.
        context.instruction_bytes()
        payload = context.asset_bytes(args.config_asset)
        document = json.loads(payload)
        if canonical_json_bytes(document) + b"\n" != payload:
            raise AgentFrameworkError("participant config must contain canonical JSON")
        config = UrbanParticipantConfig.model_validate(document)
        UrbanParticipant(context, config).run(timeout_seconds=args.timeout_seconds)
    except (AgentFrameworkError, OSError, TypeError, ValueError) as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
