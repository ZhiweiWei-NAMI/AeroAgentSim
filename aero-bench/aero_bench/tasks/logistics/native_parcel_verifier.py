"""Replay custody admission from sealed native sources; receipt alone never passes."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path

from aero_bench.providers.logistics_business.native_parcel import NativeParcelBusinessConfig
from aero_bench.providers.px4_gazebo.sealed_motion import load_sealed_px4_motion
from aero_bench.providers.rpc import parse_json_object
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.native_parcel_rpc import NativeParcelRpcComponent, NativeParcelActionRecord
from aero_bench.tasks.logistics.observation_ingress import build_observation_batch, derive_physical_observations
from aero_bench.tasks.logistics.sealed_observations import load_sealed_logistics_observations
from aero_bench.tasks.logistics.facility_physics import facility_physics


@dataclass(frozen=True)
class NativeParcelReplayResult:
    snapshot: dict
    pickup_admitted: bool
    dropoff_admitted: bool
    delivered: bool
    carrier_terminal: bool
    transport_observed: bool
    transport_ticks: tuple[int, ...]
    source_verified: bool

    @property
    def passed(self):
        return (self.source_verified and self.pickup_admitted and self.dropoff_admitted
                and self.transport_observed and self.delivered and self.carrier_terminal)


def replay_native_parcel_batches(*, config, run_id, scenario_digest,
                                business_provider_id, batches, action_records):
    """Pure replay. Only the sealed loader below attests source authority."""
    component = NativeParcelRpcComponent(config=config,run_id=run_id,
        scenario_digest=scenario_digest,business_provider_id=business_provider_id)
    actions = tuple(NativeParcelActionRecord.model_validate(r) for r in action_records)
    keyed = {}
    for action in actions:
        key = (action.action.run_id,action.action.principal_id,action.action.action_id)
        if key in keyed:
            raise ValueError("sealed action journal duplicates a keyed outcome")
        keyed[key] = action
    consumed = set()
    history = []
    for batch in batches:
        component.ingest_closed_stage(batch)
        for key, action in keyed.items():
            if action.action.received_at != batch.at:continue
            replayed = component.transition(action.request,now=batch.at)
            if replayed != action.model_dump(mode="json"):
                raise ValueError("parcel admission/custody differs from sealed source replay")
            consumed.add(key)
        history.append((batch.at, component.machine.state,
                        batch.observations[0].sample))
    if len(consumed) != len(keyed):
        raise ValueError("sealed action lacks its exact closed-stage source")
    if component.batch is None:
        raise ValueError("sealed parcel has no native observation stage")
    admitted = [a for a in actions if a.outcome.status == "admitted"]
    sample = component.batch.observations[0].sample
    destination = next(o for o in component.batch.observations
                       if o.facility_id==config.contract.identities.dropoff_facility_id)
    speed = (sample.velocity_east_m_s**2 + sample.velocity_up_m_s**2
             + sample.velocity_south_m_s**2)**0.5
    pickups = [a for a in admitted if a.action.kind == "pickup"]
    dropoffs = [a for a in admitted if a.action.kind == "dropoff"]
    transport_ticks = ()
    if len(pickups) == len(dropoffs) == 1:
        start, end = pickups[0].action.received_at, dropoffs[0].action.received_at
        pickup_sample = next(s for at, _, s in history if at == start)
        pad = config.dropoff_pad
        pickup_distance_sq = (pickup_sample.x-pad.x)**2 + (pickup_sample.z-pad.z)**2
        transport_ticks = tuple(at.tick for at, state, s in history
            if start.tick < at.tick < end.tick
            and start.sim_time_ns < at.sim_time_ns < end.sim_time_ns
            and state == "in_transit" and s.in_air is True and s.landed is False
            and (s.x-pad.x)**2 + (s.z-pad.z)**2 < pickup_distance_sq
            and (s.velocity_east_m_s**2 + s.velocity_south_m_s**2) > 0)
    return NativeParcelReplayResult(snapshot=component.snapshot(),
        pickup_admitted=len(pickups)==1,
        dropoff_admitted=len(dropoffs)==1,
        delivered=component.machine.state=="delivered",
        transport_observed=bool(transport_ticks), transport_ticks=transport_ticks,
        source_verified=False,
        carrier_terminal=(component.batch.at.tick==config.max_steps
            and sample.landed is True and sample.in_air is False
            and destination.assessment.eligible
            and speed<=config.contract.policy.max_stationary_speed_m_s))


def destination_contact_token(*, config, package, scenario):
    """Bind the selected business pad to its actual Gazebo launch-site contact."""
    pad = config.dropoff_pad
    physics = facility_physics(package.facilities.require(pad.facility_id))
    contact = next(c for c in physics.contacts if c.pad_index == pad.pad_index)
    sites = [s for s in scenario.launch_sites if s.launch_site_id == contact.model_name]
    if len(sites) != 1:
        raise ValueError("destination pad lacks its exact resolved physical launch site")
    site = sites[0]
    position = site.pose.position.enu
    if (config.contract.carrier.native_vehicle_id not in site.allowed_uav_entity_ids
            or position.east_m != contact.east_m or position.north_m != contact.north_m
            or position.up_m != contact.up_top_m + config.contract.carrier.pose_reference_above_contact_m):
        raise ValueError("destination launch site differs from the compiled facility pad")
    return f"launch_pad.{site.launch_site_id}"


def check_native_carrier_evidence(*, result, config, records, destination_contact):
    """Check raw source facts; this pure helper does not attest seal authority."""
    stages = {}
    for record in records:
        if record.operation != "step_stage":
            continue
        if record.tick in stages:
            raise ValueError("native carrier evidence repeats a closed step")
        vehicles = [v for v in record.snapshot.vehicles
                    if v.vehicle_id == config.contract.carrier.native_vehicle_id]
        if len(vehicles) != 1:
            raise ValueError("native carrier evidence lacks its exact vehicle")
        stages[record.tick] = (record, vehicles[0])
    raw_transport = any(tick in stages and stages[tick][1].armed is True
        and stages[tick][1].in_air is True and stages[tick][1].landed is False
        and stages[tick][1].ground_contact is False
        and stages[tick][1].collision_contact is False
        for tick in result.transport_ticks)
    dropoffs = [NativeParcelActionRecord.model_validate(a) for a in result.snapshot["actions"]
                if a["action"]["kind"] == "dropoff" and a["outcome"]["status"] == "admitted"]
    def grounded(tick, sim_time_ns):
        if tick not in stages:
            return False
        record, carrier = stages[tick]
        return (record.sim_time_ns == sim_time_ns and carrier.armed is False
            and carrier.landed is True and carrier.in_air is False
            and carrier.ground_contact is True and carrier.collision_contact is False
            and destination_contact in carrier.contacts)
    terminal = (result.carrier_terminal and len(dropoffs) == 1
        and grounded(dropoffs[0].action.received_at.tick,
                     dropoffs[0].action.received_at.sim_time_ns)
        and grounded(config.max_steps, config.max_steps * config.step_ns))
    return result.transport_observed and raw_transport, terminal


def validate_parcel_command_lineage(*, actions, records, business_provider_id):
    """Match each action to authenticated Gateway issue plus provider RX/lifecycle."""
    for raw in actions:
        action = NativeParcelActionRecord.model_validate(raw)
        request = action.request
        digest = hashlib.sha256(canonical_json_bytes(request.model_dump(mode="json"))).hexdigest()
        joined = []
        for record in records:
            event = record.event
            if (event.command_id != request.command_id or event.agent_id != request.agent_id
                    or event.provider_id != business_provider_id or event.run_id != request.run_id):continue
            payload = {v.name:v.value for v in event.payload}
            if payload.get("request_digest") != digest:continue
            joined.append((event,payload))
        issued = [(e,p) for e,p in joined if e.event_type=="command.issued"
                  and e.source_kind=="agent" and e.time==request.issued_at]
        if not issued or not any(p.get("arguments_json")==canonical_json_bytes(
                [a.model_dump(mode="json") for a in request.arguments]).decode() for _,p in issued):
            raise ValueError("parcel action lacks authenticated sealed command issue")
        phases = [p.get("phase") for e,p in joined if e.event_type=="command.receipt"
                  and e.source_kind=="provider" and e.source==business_provider_id
                  and e.time==action.action.received_at]
        expected = (["received","accepted","applied","completed"]
                    if action.outcome.status=="admitted" else ["received","failed"])
        if phases != expected:
            raise ValueError("parcel action lacks its exact sealed reception/lifecycle")


def verify_sealed_native_parcel(*, run, reader, seal, seal_root:Path, business_provider_id):
    """Verify actual PX4 sources and observation journal before replaying custody."""
    validated = load_sealed_logistics_observations(run=run,reader=reader,seal=seal,
        seal_root=seal_root,business_provider_id=business_provider_id)
    provider = next(p for p in run.environment.providers if p.provider_id==business_provider_id)
    reader.validate_schema_bound_file(provider.config)
    config = NativeParcelBusinessConfig.model_validate(reader.load_document(provider.config.file))
    native = load_sealed_px4_motion(run=run,reader=reader,seal=seal,seal_root=seal_root,
        provider_id=config.native_parcel.contract.carrier.provider_id)
    raw = (seal_root/validated.business_artifact.relative_path).read_bytes()
    artifact = validated.business_artifact
    if len(raw)!=artifact.size_bytes or hashlib.sha256(raw).hexdigest()!=artifact.sha256:
        raise ValueError("parcel business artifact changed after source validation")
    document = parse_json_object(raw)
    parcel = document.get("native_parcel")
    if not isinstance(parcel,dict) or parcel.get("config")!=config.native_parcel.model_dump(mode="json"):
        raise ValueError("sealed parcel config differs from immutable compilation")
    batches = []
    for frame in native.motion.frames:
        observation = config.observation
        records = derive_physical_observations(scene_state=frame.scene_state,events=frame.events,
            stage_barriers=frame.stage_barriers,package=config.task_package,
            bindings=observation.bindings,scenario=run.scenario,spec=observation.spec,
            pose_references=observation.pose_references,tolerances=observation.tolerances,
            expected_run_id=run.run_id,target=frame.scene_state.at)
        batches.append(build_observation_batch(observations=records,run_id=run.run_id,
            scenario_digest=run.scenario.scenario_digest,at=frame.scene_state.at,
            source_scene_state_digest=frame.scene_state.scene_state_digest,
            source_stage_barrier_digest=frame.scene_state.stage_barrier.barrier_digest))
    actions = parcel.get("actions")
    if not isinstance(actions,list):raise ValueError("sealed parcel action journal is missing")
    validate_parcel_command_lineage(actions=actions,records=native.motion.ledger.records,
                                   business_provider_id=business_provider_id)
    result = replay_native_parcel_batches(config=config.native_parcel,run_id=run.run_id,
        scenario_digest=run.scenario.scenario_digest,business_provider_id=business_provider_id,
        batches=tuple(batches),action_records=actions)
    if result.snapshot!=parcel:
        raise ValueError("sealed parcel final snapshot differs from native source replay")
    transport, terminal = check_native_carrier_evidence(result=result,
        config=config.native_parcel, records=native.records,
        destination_contact=destination_contact_token(config=config.native_parcel,
            package=config.task_package, scenario=run.scenario))
    return replace(result, transport_observed=transport, carrier_terminal=terminal,
                   source_verified=True)
