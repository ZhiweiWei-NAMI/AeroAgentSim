"""Independently check native restriction witnesses in the sealed SUMO artifact."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.config.loader import BundleReader, sha256_file
from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.rpc import parse_json_object
from aero_bench.providers.sumo.config import SumoConfig, TRAFFIC_RESTRICTION_CAPABILITY
from aero_bench.providers.sumo.restriction_evidence import (
    SumoRestrictionApplication, validate_restriction_application,
)
from aero_bench.runtime.events import RUN_EVENT_CHAIN_ROOT
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.ledger import LedgerRecord
from aero_bench.serialization import canonical_json_bytes


class NativeFinalizationBinding(StrictModel):
    schema_version: Literal["aero-bench.provider-artifact-finalization/v1"]
    run_id: Sha256
    provider_id: Identifier
    terminal_event: Literal["run.completed"]
    terminal_time: SimulationTime
    event_chain_root: Sha256


def _sealed_path(root: Path, seal: SealManifest, requirement) -> Path:
    matches = [item for item in seal.artifacts if item.artifact_id == requirement.artifact_id]
    if len(matches) != 1:
        raise ValueError("restriction verification requires its exact sealed artifact")
    artifact = matches[0]
    for key in ("artifact_type", "producer_id", "visibility", "relative_path"):
        if getattr(artifact, key) != getattr(requirement, key):
            raise ValueError("restriction artifact differs from its declared requirement")
    if artifact.visibility != "private" or artifact.size_bytes > requirement.max_size_bytes:
        raise ValueError("restriction artifact visibility or size is invalid")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("restriction seal root must be a regular directory")
    root = root.resolve(strict=True)
    path = root
    for part in Path(artifact.relative_path).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("restriction evidence path contains a symbolic link")
    if not stat.S_ISREG(path.stat().st_mode) or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("restriction evidence must be a regular sealed file")
    if path.stat().st_size != artifact.size_bytes or sha256_file(path) != artifact.sha256:
        raise ValueError("restriction evidence size or digest differs from the seal")
    return path


def verify_sumo_restrictions(*, bundle_root: Path, run: ResolvedRunSpec,
                             seal: SealManifest, sealed_root: Path) -> dict:
    reader = BundleReader(bundle_root)
    if seal.run_id != run.run_id or seal.execution_scope != run.execution_scope:
        raise ValueError("restriction seal does not bind this ResolvedRun")
    providers = [item for item in run.environment.providers if item.adapter == "sumo.traci"]
    if len(providers) != 1:
        raise ValueError("restriction verification requires exactly one declared SUMO Provider")
    provider = providers[0]
    config = SumoConfig.model_validate(reader.validate_schema_bound_file(provider.config))
    if not config.restrictions:
        return {"scheduled": 0, "applied": 0, "route_effects": 0}
    if TRAFFIC_RESTRICTION_CAPABILITY not in provider.capabilities:
        raise ValueError("scheduled restriction lacks its declared SUMO capability")
    if config.step_length_ns != run.environment.clock.step_ns:
        raise ValueError("restriction configuration and Provider clock differ")
    if any(item.at_tick > run.environment.clock.max_steps for item in config.restrictions):
        raise ValueError("restriction schedule exceeds the clock horizon")
    if run.scenario.sumo is None or run.scenario.sumo.provider_id != provider.provider_id:
        raise ValueError("restriction Provider differs from the native scenario")
    network_asset = next(item for item in run.scenario.assets
                         if item.asset_id == run.scenario.sumo.network_asset_id)
    verification_network = next(item for item in run.scenario.assets
                                if item.asset_id == "asset.sumo-restriction-network")
    if verification_network.file.sha256 != network_asset.file.sha256 or verification_network.byte_size != network_asset.byte_size:
        raise ValueError("restriction verifier network differs from the pinned native network")
    network = ElementTree.parse(reader.resolve_file(verification_network.file))
    edge_lanes = {edge.attrib["id"]: tuple(sorted(lane.attrib["id"] for lane in edge.findall("lane")))
                  for edge in network.findall("edge") if edge.attrib.get("function") != "internal"}
    connections = {(item.attrib["from"], item.attrib["to"]) for item in network.findall("connection")}
    vehicle_ids = {item.sumo_object_id for item in run.scenario.sumo.object_bindings if item.kind == "vehicle"}
    routes_asset = next(item for item in run.scenario.assets
                        if item.asset_id == run.scenario.sumo.routes_asset_id)
    verification_routes = next(item for item in run.scenario.assets
                               if item.asset_id == "asset.sumo-restriction-routes")
    if verification_routes.file.sha256 != routes_asset.file.sha256 or verification_routes.byte_size != routes_asset.byte_size:
        raise ValueError("restriction verifier routes differ from the pinned native routes")
    routes = ElementTree.parse(reader.resolve_file(verification_routes.file))
    named_routes = {item.attrib["id"]: tuple(item.attrib["edges"].split()) for item in routes.findall("route")}
    source_routes = {item.attrib["id"]: named_routes[item.attrib["route"]]
                     for item in routes.findall("vehicle")}
    if len(provider.artifact_requirements) != 1:
        raise ValueError("restriction Provider evidence inventory must be exact")
    path = _sealed_path(sealed_root, seal, provider.artifact_requirements[0])
    expected = {item.event_id: item for item in config.restrictions}
    applications = {}
    prefix = hashlib.sha256()
    last_tick = -1
    fields = {"schema_version", "provider_id", "run_id", "operation", "tick", "sim_time_ns",
              "snapshot", "snapshot_sha256", "process_streams", "sumo_version", "sumo_commit",
              "runtime_image", "config_digest", "artifact_id", "artifact_type",
              "scenario_config_sha256", "traffic_restrictions"}
    scenario_config = next(item for item in run.scenario.assets
                           if item.asset_id == run.scenario.sumo.config_asset_id)
    route_count = 0
    finalization = None
    with path.open("rb") as stream:
        for line in stream:
            prefix.update(line)
            record = parse_json_object(line)
            if canonical_json_bytes(record) + b"\n" != line:
                raise ValueError("restriction native evidence is not an exact canonical record")
            if record.get("schema_version") == "aero-bench.provider-artifact-finalization/v1":
                if finalization is not None:
                    raise ValueError("restriction artifact repeats its finalization binding")
                finalization = NativeFinalizationBinding.model_validate(record)
                if (finalization.run_id != run.run_id or finalization.provider_id != provider.provider_id
                        or finalization.event_chain_root != seal.event_chain_root
                        or finalization.terminal_time.tick != last_tick
                        or finalization.terminal_time.sim_time_ns != last_tick * config.step_length_ns):
                    raise ValueError("restriction artifact finalization does not bind its terminal tick and seal")
                continue
            if finalization is not None or set(record) != fields:
                raise ValueError("restriction native record follows finalization or has invalid fields")
            if any(record[key] != value for key, value in {
                "schema_version": "aero-bench.sumo-evidence/v3", "provider_id": provider.provider_id,
                "run_id": run.run_id, "runtime_image": provider.workload.runtime.image,
                "config_digest": provider.config.file.sha256,
                "artifact_id": provider.artifact_requirements[0].artifact_id,
                "artifact_type": "sumo.traffic.evidence", "sumo_version": config.sumo.version,
                "sumo_commit": config.sumo.commit, "scenario_config_sha256": scenario_config.file.sha256,
            }.items()):
                raise ValueError("restriction native evidence does not bind pinned Provider inputs")
            tick = record["tick"]
            if type(tick) is not int or not 0 <= tick <= run.environment.clock.max_steps:
                raise ValueError("restriction evidence tick is invalid")
            if record["sim_time_ns"] != tick * config.step_length_ns or tick < last_tick:
                raise ValueError("restriction evidence clock is invalid")
            if record["operation"] == "reset":
                if last_tick != -1 or tick != 0:
                    raise ValueError("restriction evidence has an unexpected reset")
            elif record["operation"] == "step_stage":
                if tick != last_tick + 1:
                    raise ValueError("restriction evidence skips or repeats a Provider step")
            elif record["operation"] == "snapshot":
                if tick != last_tick:
                    raise ValueError("restriction snapshot is outside its Provider tick")
            else:
                raise ValueError("restriction evidence operation is invalid")
            last_tick = tick
            snapshot = record["snapshot"]
            if hashlib.sha256(canonical_json_bytes(snapshot)).hexdigest() != record["snapshot_sha256"]:
                raise ValueError("restriction snapshot digest is invalid")
            if snapshot["simulation_time_ns"] != record["sim_time_ns"]:
                raise ValueError("restriction native snapshot clock differs")
            raw_apps = record["traffic_restrictions"]
            if not isinstance(raw_apps, list):
                raise ValueError("restriction application inventory must be an array")
            due = {item.event_id for item in config.restrictions
                   if item.at_tick == tick and record["operation"] == "step_stage"}
            observed = set()
            for raw in raw_apps:
                app = SumoRestrictionApplication.model_validate(raw)
                event_id = app.request.event_id
                if event_id not in expected or event_id in applications:
                    raise ValueError("restriction application is unrequested or repeated")
                validate_restriction_application(app, expected=expected[event_id],
                    step_ns=config.step_length_ns, require_route_effect=True)
                if tuple(item.lane_id for item in app.permissions) != edge_lanes.get(app.request.edge_id):
                    raise ValueError("restriction did not cover every pinned native edge lane")
                for effect in app.route_effects:
                    if effect.vehicle_id not in vehicle_ids or effect.before_route != source_routes.get(effect.vehicle_id):
                        raise ValueError("restriction route witness is not bound to the pinned vehicle route")
                    if any(pair not in connections for pair in zip(effect.after_route, effect.after_route[1:])):
                        raise ValueError("restriction reroute is not connected in the pinned native network")
                    entity_id = next(item.entity_id for item in run.scenario.sumo.object_bindings
                                     if item.sumo_object_id == effect.vehicle_id)
                    state = next(item for item in snapshot["entities"] if item["entity_id"] == entity_id)
                    if state["road_id"] != effect.after_road_id or state["lifecycle"] != effect.lifecycle:
                        raise ValueError("restriction reroute differs from the native snapshot")
                    source_routes[effect.vehicle_id] = effect.after_route
                observed.add(event_id)
                route_count += len(app.route_effects)
                applications[event_id] = (app, prefix.hexdigest())
            if observed != due:
                raise ValueError("restriction applications do not match the scheduled Provider tick")
    if finalization is None:
        raise ValueError("restriction artifact lacks its native finalization binding")
    if set(applications) != set(expected):
        raise ValueError("restriction run did not consume its complete schedule")
    requirements = [item for item in run.artifact_requirements if item.artifact_type == "event.log"]
    if len(requirements) != 1:
        raise ValueError("restriction verification requires one sealed event ledger")
    ledger_path = _sealed_path(sealed_root, seal, requirements[0])
    previous = RUN_EVENT_CHAIN_ROOT
    sequence = 0
    emitted = set()
    barriers = set()
    with ledger_path.open("rb") as stream:
        for line in stream:
            record = LedgerRecord.model_validate(parse_json_object(line))
            event = record.event
            if record.sequence != sequence or record.previous_hash != previous or event.run_id != run.run_id:
                raise ValueError("restriction ledger chain does not bind this run")
            previous = record.event_hash
            sequence += 1
            payload = {item.name: item.value for item in event.payload}
            if event.payload_schema_id == "sumo.traffic.restricted.v2":
                app = SumoRestrictionApplication.model_validate(parse_json_object(payload["application_json"].encode()))
                event_id = app.request.event_id
                if event_id not in applications or event_id in emitted:
                    raise ValueError("restriction ledger contains an unbound or repeated application")
                if (event.source_kind != "provider" or event.provider_id != provider.provider_id
                        or event.source != provider.provider_id or event.workload_id != provider.provider_id
                        or event.event_type != f"restriction.{event_id}"
                        or event.time.tick != app.request.at_tick
                        or event.time.sim_time_ns != app.native_sim_time_ns
                        or applications[event_id] != (app, payload["evidence_sha256"])):
                    raise ValueError("restriction ledger application differs from sealed native evidence")
                emitted.add(event_id)
            elif event.event_type == "stage.barrier-closed" and payload.get("stage") == "motion":
                tick = payload["target_tick"]
                if tick in {item.at_tick for item in config.restrictions}:
                    ids = parse_json_object(b'{"ids":' + payload["provider_ids"].encode() + b'}')["ids"]
                    if provider.provider_id not in ids:
                        raise ValueError("restriction motion barrier lacks its SUMO Provider")
                    barriers.add(tick)
    if (previous != seal.event_chain_root or emitted != set(expected)
            or barriers != {item.at_tick for item in config.restrictions}
            or sequence == 0 or event.event_type != finalization.terminal_event
            or event.time != finalization.terminal_time or event.source_kind != "harness"):
        raise ValueError("restriction ledger seal, application or closed barrier is incomplete")
    return {"scheduled": len(expected), "applied": len(applications), "route_effects": route_count,
            "provider_id": provider.provider_id, "evidence_sha256": sha256_file(path),
            "event_chain_root": previous}
