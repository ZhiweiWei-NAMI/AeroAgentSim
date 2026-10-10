"""Sealed verifier rejection tests; these synthetic unit fixtures are not formal evidence."""

import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from aero_bench.artifacts.contracts import ArtifactRecord, SealManifest
from aero_bench.config.models import (
    ArtifactRequirement,
    FileRef,
    NamedValue,
    SchemaBoundFile,
)
from aero_bench.providers.sumo.config import SumoConfig
from aero_bench.providers.sumo.restriction_verifier import verify_sumo_restrictions
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.ledger import EventLedger, ledger_jsonl_bytes
from aero_bench.serialization import canonical_json_bytes
from tests.providers.test_sumo_restrictions import application


def write(root, name, payload):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return FileRef(path=name, sha256=hashlib.sha256(payload).hexdigest())


@pytest.fixture
def sealed_case(tmp_path):
    bundle = tmp_path / "bundle"
    seal_root = tmp_path / "seal"
    bundle.mkdir()
    seal_root.mkdir()
    app = application()
    config = SumoConfig.model_validate(
        {
            "schema_version": "aero-bench.sumo/v2",
            "provider_id": "traffic",
            "sumo": {"version": "1.27.1", "commit": "b" * 40},
            "sumo_binary": "sumo",
            "traci_port": 12345,
            "step_length_ns": 500_000_000,
            "sumo_args": [],
            "required_commands": ["sumo"],
            "command_timeout_ms": 1000,
            "restrictions": [app["request"]],
        }
    )
    config_ref = write(
        bundle, "sumo.json", canonical_json_bytes(config.model_dump(mode="json"))
    )
    schema_ref = write(
        bundle, "schema.json", canonical_json_bytes(SumoConfig.model_json_schema())
    )
    net_ref = write(
        bundle,
        "verification-network.xml",
        b'<net><edge id="A1A2"><lane id="A1A2_0"/></edge>'
        b'<connection from="A0A1" to="A1B1"/><connection from="A1B1" to="B0A0"/></net>',
    )
    route_ref = write(
        bundle,
        "verification-routes.xml",
        b'<routes><route id="ring" edges="A0A1 A1A2 B0A0"/>'
        b'<vehicle id="veh.01" route="ring"/></routes>',
    )
    native_config_ref = FileRef(path="native.sumocfg", sha256="c" * 64)
    requirements = [
        ArtifactRequirement(
            artifact_id="artifact.sumo",
            artifact_type="sumo.traffic.evidence",
            producer_id="traffic",
            visibility="private",
            relative_path="sumo.jsonl",
            max_size_bytes=1_000_000,
            source_asset_id=None,
        ),
        ArtifactRequirement(
            artifact_id="artifact.ledger",
            artifact_type="event.log",
            producer_id="harness",
            visibility="private",
            relative_path="ledger.jsonl",
            max_size_bytes=1_000_000,
            source_asset_id=None,
        ),
    ]
    provider = NS(
        adapter="sumo.traci",
        provider_id="traffic",
        capabilities=("sumo.traffic.restrictions",),
        config=SchemaBoundFile(file=config_ref, schema_file=schema_ref),
        workload=NS(runtime=NS(image="registry/unit-sumo@sha256:" + "d" * 64)),
        artifact_requirements=(requirements[0],),
    )
    assets = [
        NS(
            asset_id=identifier,
            file=reference,
            byte_size=(bundle / reference.path).stat().st_size,
        )
        for identifier, reference in (
            ("asset.sumo-network", net_ref),
            ("asset.sumo-routes", route_ref),
            ("asset.sumo-restriction-network", net_ref),
            ("asset.sumo-restriction-routes", route_ref),
        )
    ]
    assets.append(NS(asset_id="asset.sumo-config", file=native_config_ref))
    run = NS(
        run_id="a" * 64,
        execution_scope="formal_benchmark",
        environment=NS(
            providers=(provider,), clock=NS(step_ns=500_000_000, max_steps=10)
        ),
        scenario=NS(
            assets=assets,
            sumo=NS(
                provider_id="traffic",
                network_asset_id="asset.sumo-network",
                routes_asset_id="asset.sumo-routes",
                config_asset_id="asset.sumo-config",
                object_bindings=(
                    NS(sumo_object_id="veh.01", kind="vehicle", entity_id="vehicle.01"),
                ),
            ),
        ),
        artifact_requirements=requirements,
    )
    records = []
    for tick in range(3):
        snapshot = {
            "simulation_time_ns": tick * 500_000_000,
            "entities": [
                {"entity_id": "vehicle.01", "road_id": "A0A1", "lifecycle": "active"}
            ],
        }
        records.append(
            {
                "schema_version": "aero-bench.sumo-evidence/v3",
                "provider_id": "traffic",
                "run_id": run.run_id,
                "operation": "reset" if tick == 0 else "step_stage",
                "tick": tick,
                "sim_time_ns": tick * 500_000_000,
                "snapshot": snapshot,
                "snapshot_sha256": hashlib.sha256(
                    canonical_json_bytes(snapshot)
                ).hexdigest(),
                "process_streams": {},
                "sumo_version": config.sumo.version,
                "sumo_commit": config.sumo.commit,
                "runtime_image": provider.workload.runtime.image,
                "config_digest": config_ref.sha256,
                "artifact_id": "artifact.sumo",
                "artifact_type": "sumo.traffic.evidence",
                "scenario_config_sha256": native_config_ref.sha256,
                "traffic_restrictions": [app] if tick == 2 else [],
            }
        )
    data = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
    ledger = EventLedger(run_id=run.run_id)
    ledger.append_event(
        source="traffic",
        source_kind="provider",
        provider_id="traffic",
        workload_id="traffic",
        event_type="restriction.restrict.1",
        payload_schema_id="sumo.traffic.restricted.v2",
        time=SimulationTime(tick=2, sim_time_ns=1_000_000_000),
        payload=(
            NamedValue(
                name="application_json", value=canonical_json_bytes(app).decode()
            ),
            NamedValue(name="evidence_sha256", value=hashlib.sha256(data).hexdigest()),
        ),
    )
    ledger.append_event(
        source="harness",
        event_type="stage.barrier-closed",
        time=SimulationTime(tick=2, sim_time_ns=1_000_000_000),
        payload=(
            NamedValue(name="stage", value="motion"),
            NamedValue(name="target_tick", value=2),
            NamedValue(name="provider_ids", value='["traffic"]'),
        ),
    )
    ledger.append_event(
        source="harness",
        event_type="run.completed",
        time=SimulationTime(tick=2, sim_time_ns=1_000_000_000),
    )
    binding = {
        "schema_version": "aero-bench.provider-artifact-finalization/v1",
        "provider_id": "traffic",
        "run_id": run.run_id,
        "terminal_event": "run.completed",
        "event_chain_root": ledger.chain_root,
        "terminal_time": {"tick": 2, "sim_time_ns": 1_000_000_000},
    }
    write(seal_root, "sumo.jsonl", data + canonical_json_bytes(binding) + b"\n")
    write(seal_root, "ledger.jsonl", ledger_jsonl_bytes(ledger.records))
    artifacts = [
        ArtifactRecord(
            artifact_id=req.artifact_id,
            artifact_type=req.artifact_type,
            producer_id=req.producer_id,
            visibility=req.visibility,
            relative_path=req.relative_path,
            sha256=hashlib.sha256(
                (seal_root / req.relative_path).read_bytes()
            ).hexdigest(),
            size_bytes=(seal_root / req.relative_path).stat().st_size,
        )
        for req in requirements
    ]
    body = {
        "schema_version": "aero-bench.seal/v2",
        "run_id": run.run_id,
        "attempt_id": "unit-attempt",
        "execution_scope": run.execution_scope,
        "artifacts": [item.model_dump(mode="json") for item in artifacts],
        "event_chain_root": ledger.chain_root,
    }
    seal = SealManifest(
        **body, manifest_digest=hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    )
    return dict(bundle_root=bundle, run=run, seal=seal, sealed_root=seal_root)


def test_sealed_restriction_fixture_binds_requests_routes_prefix_digest_and_barrier(
    sealed_case,
):
    result = verify_sumo_restrictions(**sealed_case)
    assert (result["scheduled"], result["applied"], result["route_effects"]) == (
        1,
        1,
        1,
    )


@pytest.mark.parametrize(
    "mutation",
    ["capability", "network-pin", "image", "clock", "artifact", "symlink", "chain"],
)
def test_sealed_restriction_gate_rejects_invalid_inputs(sealed_case, mutation):
    case = sealed_case
    run = case["run"]
    if mutation == "capability":
        run.environment.providers[0].capabilities = ()
    elif mutation == "network-pin":
        run.scenario.assets[2] = NS(
            asset_id="asset.sumo-restriction-network",
            file=FileRef(path="verification-network.xml", sha256="f" * 64),
            byte_size=run.scenario.assets[0].byte_size,
        )
    elif mutation == "image":
        run.environment.providers[0].workload.runtime.image = "wrong-image"
    elif mutation == "clock":
        run.environment.clock.step_ns = 100
    elif mutation == "artifact":
        (case["sealed_root"] / "sumo.jsonl").write_bytes(b"tampered\n")
    elif mutation == "symlink":
        path = case["sealed_root"] / "sumo.jsonl"
        path.rename(case["sealed_root"] / "elsewhere.jsonl")
        path.symlink_to("elsewhere.jsonl")
    elif mutation == "chain":
        case["seal"] = case["seal"].model_copy(update={"event_chain_root": "e" * 64})
    with pytest.raises(ValueError):
        verify_sumo_restrictions(**case)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "duplicate", "extra", "wrong-time", "wrong-root", "post-finalization"],
)
def test_sealed_restriction_gate_validates_the_native_finalization_record(
    sealed_case, mutation
):
    case = sealed_case
    path = case["sealed_root"] / "sumo.jsonl"
    rows = path.read_bytes().splitlines()
    binding = json.loads(rows[-1])
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[-1])
    elif mutation == "extra":
        binding["unexpected"] = True
    elif mutation == "wrong-time":
        binding["terminal_time"]["tick"] = 3
    elif mutation == "wrong-root":
        binding["event_chain_root"] = "f" * 64
    elif mutation == "post-finalization":
        rows.append(rows[0])
    if mutation in ("extra", "wrong-time", "wrong-root"):
        rows[-1] = canonical_json_bytes(binding)
    payload = b"\n".join(rows) + b"\n"
    path.write_bytes(payload)
    # Rebuild the unit seal so the semantic finalization gate, not checksum
    # rejection, must catch the defect.
    body = case["seal"].model_dump(mode="json")
    body.pop("manifest_digest")
    body["artifacts"][0].update(
        sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload)
    )
    case["seal"] = SealManifest(
        **body, manifest_digest=hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    )
    with pytest.raises(ValueError):
        verify_sumo_restrictions(**case)
