#!/usr/bin/env python3
"""Check a public replay's contracts, byte identities and closed file inventory.

This read-only check does not run a benchmark Verifier or create task success.
The optional report records the existing public verdict and integrity checks;
it must be written outside the replay publication, to a new file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from aero_bench.config.loader import sha256_file  # noqa: E402
from aero_bench.providers.rpc import parse_json_object  # noqa: E402
from aero_bench.runner.execution import RunnerError, _closed_source  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.trace import PublicReplayIndex, PublicReplayManifest, PublicTrace  # noqa: E402


class PublicReplayIntegrityError(ValueError):
    pass


def _source(root: Path, relative_path: str) -> Path:
    if (root / relative_path).is_symlink():
        raise PublicReplayIntegrityError(f"replay file is a symlink: {relative_path}")
    try:
        return _closed_source(
            root=root, relative_path=relative_path, label="public replay file"
        )
    except RunnerError as error:
        raise PublicReplayIntegrityError(
            f"replay file is unavailable or outside its root: {relative_path}"
        ) from error


def _document(model, path: Path):
    try:
        return model.model_validate(parse_json_object(path.read_bytes()))
    except ValidationError as error:
        locations = [
            "/".join(str(part) for part in item["loc"])
            for item in error.errors(include_input=False)
        ]
        raise PublicReplayIntegrityError(
            f"{model.__name__} contract errors at {', '.join(locations)}"
        ) from error


def audit_public_replay(replay_root: Path) -> dict[str, object]:
    if replay_root.is_symlink():
        raise PublicReplayIntegrityError("replay root must not be a symlink")
    root = replay_root.resolve(strict=True)
    manifest_path = _source(root, "replay-manifest.json")
    manifest = _document(PublicReplayManifest, manifest_path)
    inventory = {item.relative_path: item for item in manifest.files}
    for item in manifest.files:
        path = _source(root, item.relative_path)
        if path.stat().st_size != item.size_bytes or sha256_file(path) != item.sha256:
            raise PublicReplayIntegrityError(
                f"public replay bytes differ: {item.relative_path}"
            )
    trace_path = root / "public-trace.json"
    trace = _document(PublicTrace, trace_path)
    if (manifest.run_id, manifest.scenario_digest, manifest.event_chain_root) != (
        trace.run_id,
        trace.scenario_digest,
        trace.event_chain_root,
    ):
        raise PublicReplayIntegrityError(
            "replay manifest identity differs from its trace"
        )
    if manifest.replay_mode != trace.scenario.replay_mode:
        raise PublicReplayIntegrityError("replay mode differs from its public scenario")

    expected = {"public-trace.json": (manifest.trace_sha256, trace_path.stat().st_size)}

    def bind(path: str, digest: str, size: int) -> None:
        previous = expected.get(path)
        if previous is not None and previous != (digest, size):
            raise PublicReplayIntegrityError(
                "public replay references conflicting bytes"
            )
        expected[path] = (digest, size)

    for asset in trace.scenario.assets:
        bind(asset.replay_path, asset.sha256, asset.size_bytes)
        if asset.license_replay_path is not None:
            bind(
                asset.license_replay_path,
                asset.license_sha256,
                asset.license_size_bytes,
            )
    for artifact in trace.runtime_artifacts:
        bind(artifact.replay_path, artifact.sha256, artifact.size_bytes)
    history = next(
        artifact
        for artifact in trace.runtime_artifacts
        if artifact.artifact_id == trace.scene_state_history_artifact.artifact_id
    )
    if manifest.scene_state_history is not None and (
        manifest.scene_state_history.relative_path,
        manifest.scene_state_history.sha256,
        manifest.scene_state_history.size_bytes,
    ) != (history.replay_path, history.sha256, history.size_bytes):
        raise PublicReplayIntegrityError(
            "replay history differs from its trace artifact"
        )

    if manifest.replay_index is not None:
        reference = manifest.replay_index
        index = _document(PublicReplayIndex, root / reference.relative_path)
        if (index.run_id, index.scenario_digest, index.event_chain_root) != (
            trace.run_id,
            trace.scenario_digest,
            trace.event_chain_root,
        ):
            raise PublicReplayIntegrityError(
                "replay index identity differs from its trace"
            )
        if index.scene_state_count != len(trace.scene_states):
            raise PublicReplayIntegrityError(
                "replay index count differs from its trace"
            )
        bind(reference.relative_path, reference.sha256, reference.size_bytes)
        shard_bytes = []
        for shard in index.shards:
            bind(shard.relative_path, shard.sha256, shard.size_bytes)
            payload = _source(root, shard.relative_path).read_bytes()
            states = trace.scene_states[shard.first_tick - 1 : shard.last_tick]
            expected_payload = b"".join(
                canonical_json_bytes(state.model_dump(mode="json")) + b"\n"
                for state in states
            )
            if payload != expected_payload:
                raise PublicReplayIntegrityError(
                    "replay shard bytes differ from their declared tick range"
                )
            shard_bytes.append(payload)
        if b"".join(shard_bytes) != (root / history.replay_path).read_bytes():
            raise PublicReplayIntegrityError(
                "replay shards differ from the complete history"
            )

    if set(expected) != set(inventory) or any(
        (inventory[path].sha256, inventory[path].size_bytes) != identity
        for path, identity in expected.items()
    ):
        raise PublicReplayIntegrityError(
            "replay inventory does not close over its trace"
        )
    published = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PublicReplayIntegrityError("replay publication contains a symlink")
        if path.is_file():
            published.add(path.relative_to(root).as_posix())
    if published != set(inventory) | {"replay-manifest.json"}:
        raise PublicReplayIntegrityError(
            "replay publication contains missing or undeclared files"
        )

    return {
        "schema_version": "aero-bench.public-replay-audit/v1",
        "integrity_status": "checked",
        "run_id": trace.run_id,
        "scenario_digest": trace.scenario_digest,
        "event_chain_root": trace.event_chain_root,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "trace_sha256": manifest.trace_sha256,
        "execution_scope": trace.execution_scope,
        "trace_phase": trace.phase,
        "public_verdict": None
        if trace.verifier_public is None
        else trace.verifier_public.status,
        "benchmark_verifier_executed": False,
        "file_count": len(manifest.files),
        "total_size_bytes": sum(item.size_bytes for item in manifest.files),
        "scene_state_count": len(trace.scene_states),
        "public_event_count": len(trace.events),
        "query_event_count": sum(event.query_id is not None for event in trace.events),
        "last_tick": trace.time.tick,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report is not None and args.report.resolve().is_relative_to(
        args.replay_root.resolve()
    ):
        raise PublicReplayIntegrityError(
            "audit report must stay outside the replay publication"
        )
    result = audit_public_replay(args.replay_root)
    raw = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    if args.report is not None:
        with args.report.open("x", encoding="utf-8") as report:
            report.write(raw)
    print(raw, end="")


if __name__ == "__main__":
    main()
