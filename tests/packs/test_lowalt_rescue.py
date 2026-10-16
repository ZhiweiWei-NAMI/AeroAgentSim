"""Rescue contracts and authored run regressions. Written for external validation."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from aerokernel import EntityRef, Fact, Journal, replay, thaw

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.packs.lowalt.rescue import Rescue, deterministic_bid, eligible
from aeroagentsim.packs.lowalt.tasks import Flight, Job, LowaltTasks
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.kpis import KPIProjector
from aeroagentsim.services.projector import project

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "scenarios/rescue"
SECOND = 1_000_000_000


def document() -> dict:
    return yaml.safe_load((BASE / "rooftop-flood-stub-noradio.yaml").read_text())


def events(records: list[dict], schema: str) -> list[dict]:
    return [m for r in records for m in r["messages"] if m["schemaId"] == schema]


def data(message: dict) -> dict:
    return json.loads(message["payload"]["data_json"])


def install_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    package = types.ModuleType("aerorescue")
    adapter = types.ModuleType("aerorescue.adapter")

    class ParticipationFilter:
        @classmethod
        def load(cls, path: str):
            assert path == "independently-exported-filter.json"
            return cls()

        def propose(self, request: dict) -> dict:
            # An explicit test double at the external inference boundary.
            roles = [role for role in ("inspect", "relay", "deliver") if eligible(request["self"], role, request)]
            return {"roles": roles, "available_after_s": max(request["now_s"],
                (request["self"].get("commitment") or {}).get("busy_until_s", request["now_s"])), "reason": "test filter"}

    adapter.ParticipationFilter = ParticipationFilter
    monkeypatch.setitem(sys.modules, "aerorescue", package)
    monkeypatch.setitem(sys.modules, "aerorescue.adapter", adapter)


def test_planner_imported_by_path_has_the_research_contract() -> None:
    path = ROOT / "src/aeroagentsim/packs/lowalt/rescue_planner.py"
    spec = importlib.util.spec_from_file_location("independent_rescue_planner", path)
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)
    result = planner.solve_plan(
        [{"id": "u", "position": [0, 0, 0], "capabilities": ["inspect"], "soc": 1,
          "speed_m_s": 10, "payload_kg": 0, "payload_capacity_kg": 3, "commitment": None}],
        [{"vehicle": "u", "roles": ["inspect"], "available_after_s": 5}],
        {"actions": [{"id": "inspect", "role": "inspect", "after": [], "target": [100, 0, 0], "duration_s": 2}], "deadline_s": 30}, 5)
    assert set(result) == {"status", "assignments", "objective_s", "evaluated", "reason"}
    assert result["status"] == "feasible"
    assert result["assignments"][0]["end_s"] == 17
    assert result["objective_s"] == 12


@pytest.mark.parametrize("mode", ["model", "filter", "all", "distance"])
def test_each_policy_on_authored_noradio_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    pytest.importorskip("langgraph")
    doc = document()
    cfg = doc["engines"]["tasks"]["config"]["rescue"]
    cfg["participation"] = {"mode": mode, "per_role": 1}
    if mode == "filter":
        install_filter(monkeypatch)
        cfg["participation_filter"] = "independently-exported-filter.json"
    scenario = load_scenario(doc, base=BASE)
    local_engines = [name for name in scenario.document["engines"] if name.startswith("rescue_uav_")]
    assert bool(local_engines) is (mode == "model")
    sim = Simulation(scenario, run_directory=tmp_path)
    try:
        sim.start()
        sim.run_until(scenario.until_ns)
        raw = list(sim.kernel.iter_records())
        records = [project(r) for r in raw]
        bids = [data(m) for m in events(records, "rescue.bid")]
        assert len(bids) == 4
        assert {bid["participation_source"] for bid in bids} == {"model_api" if mode == "model" else "trained_filter" if mode == "filter" else mode}
        plans = [data(m) for m in events(records, "rescue.event") if data(m)["kind"] == "plan_computed"]
        assert plans[0]["plan"]["status"] == "feasible"
        assert {row["vehicle"] for row in plans[0]["plan"]["assignments"]} == {"U2", "U3"}
        requests = [data(m) for m in events(records, "rescue.local.request")]
        assert {r["now_s"] for r in requests} == {plans[0]["now_s"]}
        assert all(r["phase"] == "notice" for r in requests)
        protected = next(r["self"]["commitment"] for r in requests if r["self"]["id"] == "U1")
        assert protected["busy_estimate"] == "route"
        assert requests[0]["now_s"] < protected["busy_until_s"] < cfg["deadline_s"]
        kinds = [data(m)["kind"] for m in events(records, "rescue.event")]
        assert "communication_failed" in kinds
        assert kinds.count("deadline_missed") == kinds.count("outcome") == 1
        outcome = next(data(m) for m in events(records, "rescue.event") if data(m)["kind"] == "outcome")
        assert outcome == {"event": "R7", "kind": "outcome", "success": False,
                           "time_to_confirmation_s": None, "time_to_delivery_s": None}
        failures = [m for m in events(records, "aas.langgraph.record") if m["payload"]["phase"] == "failure"]
        assert not failures
        calls = [m for m in events(records, "aas.langgraph.record") if m["payload"]["phase"] == "model_call"]
        assert len(calls) == (5 if mode == "model" else 1)
        projector = KPIProjector(scenario.registry)
        for row in raw:
            projector.consume(row)
        values = projector.snapshot()
        assert values["rescue_success"]["value"] == 0
        assert values["rescue_bids"]["value"] == 4
        assert values["rescue_radio_packets"]["value"] == 0
        assert values["rescue_model_calls"]["value"] == len(calls)
        if mode == "filter":
            timings = [json.loads(line) for line in (tmp_path / "rescue-filter-timing.jsonl").read_text().splitlines()]
            assert len(timings) == 4
            assert all(row["latency_s"] >= 0 for row in timings)
            assert all("latency_s" not in r for r in bids)
    finally:
        sim.close()


class Context:
    def __init__(self, values: dict, now_s: float = 5):
        self.values, self.now, self.emitted = values, SimpleNamespace(ns=round(now_s * SECOND)), []

    def get(self, ref, field):
        return self.values[(ref.id, field)]

    def set(self, ref, field, value):
        self.values[(ref.id, field)] = copy.deepcopy(value)

    def emit(self, schema, payload, *, topic):
        self.emitted.append((schema, json.loads(payload["data_json"]), topic))


def harness() -> tuple[Rescue, Context]:
    cfg = document()["engines"]["tasks"]["config"]["rescue"]
    cfg["participation"] = {"mode": "all"}
    refs = {site["id"]: EntityRef("r", "0", site["id"], 0, "lowalt:Site") for site in cfg["sites"]}
    refs.update({identity: EntityRef("r", "0", identity, 0, "lowalt:UAV") for identity in cfg["vehicles"]})
    refs["weather"] = EntityRef("r", "0", "weather", 0, "lowalt:Environment")
    values = {(site["id"], "lowalt.site_position"): site["position"] for site in cfg["sites"]}
    for identity, profile in cfg["vehicles"].items():
        values.update({(identity, "lowalt.position"): profile["position"], (identity, "lowalt.soc"): 1.0,
                       (identity, "lowalt.payload_kg"): profile.get("payload_kg", 0.0), (identity, "lowalt.payload_capacity_kg"): 3.0})
    owner = SimpleNamespace(cfg={"rescue": cfg}, by_id=refs, radius=2,
        build=SimpleNamespace(entities=tuple(refs.values()), run_directory=None),
        flights={identity: Flight(refs[identity]) for identity in cfg["vehicles"]})
    return Rescue(owner), Context(values)


def collect(rescue: Rescue, ctx: Context) -> None:
    for schema, payload, _ in list(ctx.emitted):
        if schema == "rescue.bid" and payload["phase"] == rescue.planning_phase:
            rescue.on_message(ctx, schema, {"data_json": json.dumps(payload)})


def accept_plan(rescue: Rescue, ctx: Context, actions: list[dict]) -> None:
    rescue.on_message(ctx, "rescue.plan", {"data_json": json.dumps({"event": "R7", "phase": rescue.planning_phase,
        "now_s": rescue.phase_request["now_s"], "actions": actions, "reason": "explicit fixture plan"})})


def test_second_phase_refreshes_bids_and_keeps_the_ready_relay() -> None:
    rescue, ctx = harness()
    rescue.noticed_s = 5
    rescue.start_phase(ctx, "notice")
    collect(rescue, ctx)
    accept_plan(rescue, ctx, [{"id": "i", "role": "inspect", "after": []}, {"id": "r", "role": "relay", "after": []}])
    old_bid = next(row[1] for row in ctx.emitted if row[0] == "rescue.bid")
    relay = rescue.owner.flights["U3"]
    ctx.values[("U3", "lowalt.position")] = rescue.cfg["relay_position"]
    relay.goal, relay.route, relay.status, relay.replan = tuple(rescue.cfg["relay_position"]), (), "succeeded", False
    rescue.relay, rescue.relay_ready = "U3", True
    ctx.now.ns = 30 * SECOND
    rescue.on_message(ctx, "rescue.transport.delivered", {"data_json": json.dumps({"event": "R7", "kind": "report", "evidence": "positive"})})
    rescue.on_message(ctx, "rescue.bid", {"data_json": json.dumps(old_bid)})
    assert rescue.bids["report_received"] == {}
    collect(rescue, ctx)
    accept_plan(rescue, ctx, [{"id": "r2", "role": "relay", "after": []}, {"id": "d", "role": "deliver", "after": ["r2"]}])
    plans = [row[1] for row in ctx.emitted if row[0] == "rescue.event" and row[1]["kind"] == "plan_computed"]
    assert len(plans) == 2
    for plan in plans:
        requests = [row[1] for row in ctx.emitted if row[0] == "rescue.local.request" and row[1]["phase"] == plan["phase"]]
        assert len(requests) == 4
        assert {r["now_s"] for r in requests} == {plan["now_s"]} == {plan["problem"]["now_s"]}
        assert {bid["phase"] for bid in plan["problem"]["bids"]} == {plan["phase"]}
    assert next(a for a in plans[1]["plan"]["assignments"] if a["role"] == "deliver")["vehicle"] == "U4"
    assert rescue.relay_ready and "r2" in rescue.done
    assert relay.goal == tuple(rescue.cfg["relay_position"]) and not relay.replan
    assert rescue.flight(ctx, relay)
    assert ctx.values[("U3", "rescue.status")] == "holding"
    rescue.delivered_s = 60
    ctx.now.ns = 62 * SECOND
    rescue.on_message(ctx, "rescue.transport.delivered", {"data_json": json.dumps({"event": "R7", "kind": "delivery_ack"})})
    rescue.tick(ctx)
    outcome = [row[1] for row in ctx.emitted if row[0] == "rescue.event" and row[1]["kind"] == "outcome"]
    assert outcome == [{"event": "R7", "kind": "outcome", "success": True,
                        "time_to_confirmation_s": 25, "time_to_delivery_s": 55}]


def test_busy_estimate_uses_inflight_leg_remaining_route_and_dwell() -> None:
    rescue, ctx = harness()
    ref = EntityRef("r", "0", "R3", 0, "lowalt:Task")
    flight = rescue.owner.flights["U1"]
    flight.job = Job(ref, 0, phase="started", spray_hold_until_ns=8 * SECOND)
    flight.pending, flight.waypoint, flight.route = True, (30.0, 0.0, 0.0), ((60.0, 0.0, 0.0),)
    ctx.values.update({("U1", "lowalt.position"): [0, 0, 0], ("R3", "lowalt.task_origin"): "R3-origin",
        ("R3", "lowalt.task_destination"): "R3-destination", ("R3-origin", "lowalt.site_position"): [60, 0, 0],
        ("R3-destination", "lowalt.site_position"): [160, 0, 0]})
    assert rescue.busy_estimate(ctx, flight, {"speed_m_s": 10}) == {"busy_until_s": 24, "busy_estimate": "route"}
    estimate = rescue.busy_estimate(ctx, flight, {"speed_m_s": 0})
    assert estimate["busy_estimate"] == "unknown" and estimate["busy_until_s"] > rescue.cfg["deadline_s"]
    json.dumps(estimate, allow_nan=False)


def test_markers_labels_and_link_hop_states() -> None:
    scene = load_scenario(document(), base=BASE).document
    entities = {e["id"]: e for e in scene["entities"]}
    environment = next(e for e in scene["entities"] if e["type"] == "lowalt:Environment")
    assert {m["kind"] for m in environment["facts"]["rescue.markers"]} == {"inspect", "relay", "command"}
    assert entities["R7"]["label"] == "R7 rooftop rescue site"
    assert entities["R7"]["label_zh"] == "R7·屋顶疑似受困点"
    rescue, ctx = harness()
    rescue.hop(ctx, {"message_id": "R7/report", "hop": 0, "source": "U2", "destination": "U3", "kind": "report"})
    rescue.hop(ctx, {"message_id": "R7/report", "hop": 1, "source": "U3", "destination": "command-center", "kind": "report"})
    assert [r["state"] for r in rescue.links] == ["delivered", "sending"]
    rescue.transport_result(ctx, {"kind": "report"}, "delivered")
    assert [r["state"] for r in rescue.links] == ["delivered", "delivered"]
    rescue.links.append({"id": "coverage", "from": "U3", "to": "command-center", "kind": "relay_coverage", "state": "holding"})
    rescue.release(ctx)
    assert len(rescue.links) == 2
    rescue.transport_result(ctx, {"kind": "report"}, "failed")
    assert all(r["state"] == "failed" for r in rescue.links)


@pytest.mark.parametrize("evidence", ["negative", "uncertain"])
def test_resolved_nonpositive_report_has_one_successful_outcome(evidence: str) -> None:
    rescue, ctx = harness()
    rescue.noticed_s, rescue.confirmed_s, rescue.received = 5, 20, evidence
    ctx.now.ns = 20 * SECOND
    rescue.start_phase(ctx, "report_received")
    collect(rescue, ctx)
    accept_plan(rescue, ctx, [])
    ctx.now.ns = 300 * SECOND
    rescue.tick(ctx)
    kinds = [row[1]["kind"] for row in ctx.emitted if row[0] == "rescue.event"]
    assert kinds.count("outcome") == 1 and "deadline_missed" not in kinds
    assert rescue.phase == evidence


def test_seeded_template_varies_after_experiment_seed_is_applied() -> None:
    registry = TemplateRegistry(ROOT / "scenarios/templates")
    prepared = registry.apply("flood-rescue", {"participation": "all"})

    def fleet(seed):
        doc = copy.deepcopy(prepared.document)
        doc["run"]["seed"] = seed
        scene = load_scenario(doc, base=prepared.base).document
        return {e["id"]: e["facts"] for e in scene["entities"] if e["type"] == "lowalt:UAV"}, scene["engines"]["tasks"]["config"]["rescue"]

    first, cfg = fleet(1)
    assert (first, cfg) == fleet(1)
    assert first != fleet(2)[0]
    roles = set()
    for profile in cfg["vehicles"].values():
        assert profile["capabilities"]
        assert -540 <= profile["position"][0] <= 540 and -540 <= profile["position"][1] <= 540
        assert 130 <= profile["position"][2] <= 180
        if not profile.get("protected_task"):
            roles.update(profile["capabilities"])
    assert roles == {"inspect", "relay", "deliver"}
    authored = registry.apply("flood-rescue", {"fleet_variation": "authored"})
    scene = load_scenario(authored.document, base=authored.base).document
    assert next(e for e in scene["entities"] if e["id"] == "U2")["facts"]["lowalt.position"] == [-100.0, -260.0, 70.0]


def test_seeded_payload_is_absent_without_a_deliver_role() -> None:
    registry = TemplateRegistry(ROOT / "scenarios/templates")

    def fleet(seed):
        prepared = registry.apply("flood-rescue", {"participation": "all"})
        doc = copy.deepcopy(prepared.document)
        doc["run"]["seed"] = seed
        scene = load_scenario(doc, base=prepared.base).document
        return {e["id"]: e["facts"] for e in scene["entities"] if e["type"] == "lowalt:UAV"}, \
            scene["engines"]["tasks"]["config"]["rescue"]

    for seed in range(10):
        profiles, cfg = fleet(seed)
        for identity, profile in cfg["vehicles"].items():
            if "deliver" not in profile["capabilities"] and not profile.get("protected_task"):
                assert profile["payload_kg"] == 0.0
                assert profiles[identity]["lowalt.payload_kg"] == 0.0

    # Captured from the pre-fix code: the coverage pass still decides the role
    # holder, so positions, capabilities and SOC stay identical for a seed.
    _profiles, cfg = fleet(1)
    assert {identity: {"position": profile["position"], "capabilities": profile["capabilities"],
                       "soc": profile["soc"]} for identity, profile in cfg["vehicles"].items()} == {
        "U1": {"position": [-134.691, -55.77, 168.189], "capabilities": ["inspect", "relay", "deliver"], "soc": 0.7561},
        "U2": {"position": [-221.842, -401.496, 171.788], "capabilities": ["inspect", "deliver"], "soc": 0.6118},
        "U3": {"position": [-21.371, 33.581, 175.071], "capabilities": ["inspect", "relay"], "soc": 0.9574},
        "U4": {"position": [34.98, -273.365, 131.452], "capabilities": ["inspect", "relay", "deliver"], "soc": 0.4632},
    }
    assert cfg["vehicles"]["U3"]["payload_kg"] == 0.0
    # A protected vehicle keeps the seeded cargo for its existing mission.
    assert cfg["vehicles"]["U1"]["protected_task"] == "R3" and cfg["vehicles"]["U1"]["payload_kg"] > 0


@pytest.mark.parametrize("mode,obstruction", [("all", None), ("distance", None), ("filter", None),
                                             ("all", "no_route"), ("all", "rejected")])
def test_seed_zero_rescue_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   mode: str, obstruction: str | None) -> None:
    pytest.importorskip("langgraph")
    prepared = TemplateRegistry(ROOT / "scenarios/templates").apply("flood-rescue", {
        "participation": "all" if mode == "filter" else mode, "fleet_variation": "seeded", "radio": "off"})
    prepared.document["run"]["seed"] = 0
    cfg = prepared.document["engines"]["tasks"]["config"]["rescue"]
    if mode == "filter":
        install_filter(monkeypatch)
        cfg["participation"].update(mode="filter", filter="independently-exported-filter.json")
    if obstruction is not None:
        # A recovery decision is an explicit third stub response, not a fallback
        # from an exhausted model script.
        responses = cfg["provider"]["responses"]["propose_plan"]
        responses.append(copy.deepcopy(responses[-1]))

    def delivered(self, ctx, kind, source, **details):
        # Replace transport at its boundary. The flight and safety engines run
        # normally except for the explicitly injected rescue command faults.
        ctx.emit("rescue.transport.delivered", {"data_json": json.dumps({
            "event": self.incident.id, "kind": kind, "source": source, **details})}, topic="rescue.results")

    monkeypatch.setattr(Rescue, "send", delivered)
    original_route, original_submit = LowaltTasks._route, LowaltTasks._submit

    def route(self, ctx, flight, goal):
        if (obstruction == "no_route" and flight.vehicle.id == "U3"
                and flight.purpose == "rescue" and self.rescue.received == "positive"):
            flight.route_blocked_reason = "regression: route unavailable"
            return False
        return original_route(self, ctx, flight, goal)

    def submit(self, ctx, flight, goal):
        if (obstruction == "rejected" and flight.vehicle.id == "U3" and flight.purpose == "rescue"
                and self.rescue.received == "positive" and goal is not None):
            flight.status, flight.route, flight.replan = "rejected", (), True
            return
        return original_submit(self, ctx, flight, goal)

    monkeypatch.setattr(LowaltTasks, "_route", route)
    monkeypatch.setattr(LowaltTasks, "_submit", submit)
    scenario = load_scenario(prepared.document, base=prepared.base)
    sim = Simulation(scenario, run_directory=tmp_path)
    try:
        sim.start()
        sim.run_until(scenario.until_ns)
        records = [project(row) for row in sim.kernel.iter_records()]
    finally:
        sim.close()
    rescue_events = [data(message) for message in events(records, "rescue.event")]
    plans = [row for row in rescue_events if row["kind"] == "plan_computed"]
    kinds = [row["kind"] for row in rescue_events]
    outcomes = [row for row in rescue_events if row["kind"] == "outcome"]
    if mode == "distance":
        # The original seed-0 WAL also rejects this notice plan: its sole nearest
        # inspector is U1, which is still committed to R3. Preserve that result.
        assert len(plans) == 1 and plans[0]["plan"]["status"] == "infeasible"
        assert plans[0]["plan"]["reason"] == "No eligible bid for action a1 (inspect)."
        assert "execution_blocked" not in kinds and kinds.count("deadline_missed") == 1
        assert len(outcomes) == 1 and outcomes[0]["success"] is False
        return
    assert [(a["vehicle"], a["role"]) for a in plans[0]["plan"]["assignments"]] == [("U3", "inspect"), ("U2", "relay")]
    assert [(a["vehicle"], a["role"]) for a in plans[1]["plan"]["assignments"]] == [("U3", "relay"), ("U2", "deliver")]
    assert any(row["kind"] == "relay_handover" and row["vehicle"] == "U2" and row["relay"] == "U3" for row in rescue_events)
    blocked = [row for row in rescue_events if row["kind"] == "execution_blocked"]
    if obstruction is None:
        assert not blocked and len(plans) == 2
    else:
        assert len(blocked) == 1 and blocked[0]["vehicle"] == "U3"
        assert blocked[0]["reason"] == ("regression: route unavailable" if obstruction == "no_route" else "motion rejected")
        requests = [data(message) for message in events(records, "rescue.center.request")]
        recovery = [row for row in requests if row["phase"] == "execution_replan"]
        assert len(recovery) == 1 and recovery[0]["excluded_vehicles"] == ["U3"]
        assert "U3" not in {row["id"] for row in recovery[0]["vehicles"]}
        assert len(plans) == 3 and all(a["vehicle"] != "U3" for a in plans[-1]["plan"]["assignments"])
    assert kinds.index("delivery_completed") > max(i for i, kind in enumerate(kinds) if kind == "relay_ready")
    assert len(outcomes) == 1 and outcomes[0]["success"] is True
    assert outcomes[0]["time_to_delivery_s"] < cfg["deadline_s"] - cfg["at_s"]
    assert not {"deadline_missed", "plan_rejected", "model_failed"} & set(kinds)


def test_rescue_journal_replays_without_models_and_runs_deterministically(tmp_path: Path) -> None:
    pytest.importorskip("langgraph")
    records = []
    for index in range(2):
        doc = document()
        doc["engines"]["tasks"]["config"]["rescue"]["participation"] = {"mode": "all"}
        scenario = load_scenario(doc, base=BASE)
        path = tmp_path / f"{index}.jsonl"
        sim = Simulation(scenario, journal=Journal(path))
        try:
            sim.start()
            sim.run_until(scenario.until_ns)
            records.append([project(row) for row in sim.kernel.iter_records()])
            view = sim.kernel.view()
            expected = {key: thaw(value.value) for key in view._store.writers
                for value in (view.field(key, view.instant),) if isinstance(value, Fact)}
            instant = view.instant
        finally:
            sim.close()
        restored = replay(path).view()
        assert restored.instant == instant
        assert {(ref, field): thaw(restored.field((ref, field), instant).value) for ref, field in expected} == expected
    assert records[0] == records[1]


@pytest.mark.parametrize("relay", [None, "U2", "U3"])
def test_transport_hops_wait_for_native_delivery_and_publish_progress(relay: str | None) -> None:
    pytest.importorskip("langgraph")
    from aeroagentsim.packs.lowalt.rescue_transport import relay_graph

    graph = relay_graph(None, {"target": "radio"}).compile()
    envelope = {"event": "R7", "kind": "report", "message_id": "R7/report", "source": "U2",
                "relay": relay, "destination": "command-center", "evidence": "positive"}
    result = graph.invoke({"trigger": {"schema": "rescue.transport.send", "payload": {"data_json": json.dumps(envelope)}}})
    outputs = result["outputs"]
    assert outputs[0]["schema"] == "rescue.transport.hop"
    packet = outputs[1]["payload"]
    assert packet["src"] == "U2" and packet["dst"] == ("U3" if relay == "U3" else "command-center")
    delivered = {"schema": "adapters.ns3.delivery", "payload": {"payload_ref": packet["payload_ref"],
        "received_ns": 21 * SECOND, "available_ns": 22 * SECOND}}
    result = graph.invoke({"trigger": delivered})
    if relay == "U3":
        outputs = result["outputs"]
        assert outputs[0]["schema"] == "rescue.transport.hop"
        assert json.loads(outputs[0]["payload"]["data_json"])["hop"] == 1
        packet = outputs[1]["payload"]
        assert packet["src"] == "U3" and packet["dst"] == "command-center"
        delivered["payload"]["payload_ref"] = packet["payload_ref"]
        result = graph.invoke({"trigger": delivered})
    assert [output["schema"] for output in result["outputs"]] == ["rescue.transport.delivered"]
    failure = graph.invoke({"trigger": {"schema": "adapters.ns3.drop", "payload": {
        "packet_id": "R7/rescue/report/0", "reason": "delivery_timeout"}}})
    assert [output["schema"] for output in failure["outputs"]] == ["rescue.transport.failed"]


def test_distance_selects_nearest_eligible_aircraft_for_each_role() -> None:
    rescue, ctx = harness()
    rescue.start_phase(ctx, "notice")
    request = rescue.phase_request
    fleet = rescue.vehicle_state(ctx)
    near = copy.deepcopy(next(v for v in fleet if v["id"] == "U2"))
    near.update(id="near", position=request["locations"]["inspect"], soc=0.19)
    farther = copy.deepcopy(near)
    farther.update(id="farther", soc=1.0, position=[-200, -300, 70])
    fleet.extend([near, farther])
    results = {v["id"]: deterministic_bid({**request, "self": v}, fleet, {"mode": "distance", "per_role": 1}) for v in fleet}
    assert "inspect" in results["U2"]["roles"]
    assert "inspect" not in results["near"]["roles"] + results["farther"]["roles"]
    both = deterministic_bid({**request, "self": farther}, fleet, {"mode": "distance", "per_role": 2})
    assert "inspect" in both["roles"]


@pytest.mark.parametrize("observation", ["positive", "negative", "uncertain"])
def test_template_observation_and_radio_knobs_pin_honest_inputs(observation: str) -> None:
    registry = TemplateRegistry(ROOT / "scenarios/templates")
    prepared = registry.apply("flood-rescue", {"observation": observation, "radio": "off", "deadline_s": 90})
    cfg = prepared.document["engines"]["tasks"]["config"]["rescue"]
    assert cfg["observation"] == observation and "radio" not in cfg
    assert cfg["deadline_s"] == 90 and prepared.document["run"]["until_ns"] == 91 * SECOND
    response = cfg["provider"]["responses"]["propose_plan"][1]
    actions = json.loads(response["message"]["tool_calls"][0]["function"]["arguments"])["actions"]
    assert {a["role"] for a in actions} == ({"relay", "deliver"} if observation == "positive" else set())
    enabled = registry.apply("flood-rescue", {"radio": "on"})
    assert enabled.document["engines"]["tasks"]["config"]["rescue"]["radio"]["target"] == "radio"
