import hashlib

import pytest

from aeroagentsim.integration import ContractError, MappingArtifactReader, RunIdentity
from aeroagentsim.integration.bench import BenchContext
from aeroagentsim.integration.bench_replay import (
    normalize_bench_replay_index,
    read_verified_shard,
    safe_relative_path,
    validate_bench_manifest_header,
)


def source_index():
    # Opaque authored shard bodies; no claim to be native scene-state shard JSON.
    bodies = (b"synthetic first shard", b"synthetic second shard")
    ctx = BenchContext(RunIdentity("synthetic-attachment", "a" * 64, "epoch-1", "manifest-1"), "b" * 64, "0")
    index = {
        "schema_version": "aero-bench.public-replay-index/v1",
        "run_id": ctx.run.run_id,
        "scenario_digest": ctx.scenario_digest,
        "event_chain_root": "c" * 64,
        "first_tick": 1,
        "last_tick": 4,
        "scene_state_count": 4,
        "shards": [
            {
                "relative_path": "history/shard-{}.json".format(i),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "first_tick": 1 + 2 * i,
                "last_tick": 2 + 2 * i,
                "scene_state_count": 2,
            }
            for i, raw in enumerate(bodies)
        ],
    }
    return ctx, index, bodies


def test_index_shard_bounds_integrity_and_full_history_without_sse_cursors():
    ctx, data, bodies = source_index()
    index = normalize_bench_replay_index(data, ctx, "c" * 64)
    assert index.history_shards(4) == index.shards
    assert index.history_shards(1) == index.shards[:1]
    assert not hasattr(index, "after_scene_tick")
    reader = MappingArtifactReader({"shard_0": bodies[0], "shard_1": bodies[1]})
    authorized = {shard.relative_path: "shard_{}".format(i) for i, shard in enumerate(index.shards)}
    raw = read_verified_shard(index, index.shards[0], reader, authorized, lambda body, shard: body == bodies[0])
    assert raw == bodies[0]
    with pytest.raises(ContractError, match="authorized opaque"):
        read_verified_shard(index, index.shards[0], reader, {}, lambda *_: True)
    with pytest.raises(ContractError, match="size/SHA256"):
        read_verified_shard(index, index.shards[0], MappingArtifactReader({"shard_0": b"bad"}), authorized, lambda *_: True)
    with pytest.raises(ContractError, match="native shard"):
        read_verified_shard(index, index.shards[0], reader, authorized, lambda *_: False)
    with pytest.raises(ContractError, match="source range"):
        index.history_shards(5)


def test_empty_sealed_source_index_is_preserved():
    ctx, data, _ = source_index()
    data.update(first_tick=0, last_tick=0, scene_state_count=0, shards=[])
    index = normalize_bench_replay_index(data, ctx, "c" * 64)
    assert index.history_shards(0) == ()


@pytest.mark.parametrize(
    "path",
    ["../private.json", "/tmp/private", "history/../private", "https://host/data", "file:///tmp/x", "x\\y", "x//y", "x/./y"],
)
def test_shard_paths_do_not_escape_allowlisted_materialization(path):
    with pytest.raises(ContractError, match="unsafe"):
        safe_relative_path(path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(run_id="d" * 64),
        lambda value: value.update(scenario_digest="d" * 64),
        lambda value: value.update(event_chain_root="d" * 64),
        lambda value: value["shards"][0].update(scene_state_count=257),
        lambda value: value["shards"][1].update(first_tick=2),
        lambda value: value["shards"][0].update(size_bytes=0),
        lambda value: value.update(scene_state_count=5),
    ],
)
def test_sealed_index_identity_count_and_order_fail_closed(mutation):
    ctx, data, _ = source_index()
    mutation(data)
    with pytest.raises(ContractError):
        normalize_bench_replay_index(data, ctx, "c" * 64)


def test_native_file_reference_schema_remains_injected_and_indexed_fields_required():
    ctx, data, _ = source_index()
    index = normalize_bench_replay_index(data, ctx, "c" * 64)
    manifest = {
        "schema_version": "aero-bench.public-replay-manifest/v1",
        "run_id": ctx.run.run_id,
        "scenario_digest": ctx.scenario_digest,
        "event_chain_root": index.event_chain_root,
        "trace_sha256": "d" * 64,
        "files": [{"fixture_only": True}],
        "replay_mode": "indexed",
    }
    with pytest.raises(ContractError, match="requires replay_index"):
        validate_bench_manifest_header(manifest, index, lambda value: True)
    manifest.update(replay_index={"fixture_only": True}, scene_state_history={"fixture_only": True})
    calls = []

    def verifier(value):
        calls.append(value)
        return value == {"fixture_only": True}

    validate_bench_manifest_header(manifest, index, verifier)
    assert len(calls) == 3
    with pytest.raises(ContractError, match="file reference"):
        validate_bench_manifest_header(manifest, index, lambda value: False)
