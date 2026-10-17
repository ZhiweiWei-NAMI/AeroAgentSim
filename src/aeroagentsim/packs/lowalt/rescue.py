"""Small, event-driven rescue prototype on the existing task and model owners."""
from __future__ import annotations

import json
import math
import time
from typing import Any

from aerokernel.state import Absent

from aeroagentsim.agents.langgraph_client import JournalClient
from aeroagentsim.agents.tools import object_schema, strict_json, validate_strict
from aeroagentsim.packs.common import distance, finite, read, vec

FIELDS = ("rescue.flood_bounds", "rescue.flooded_roads", "rescue.incident_state",
          "rescue.role", "rescue.status", "rescue.task", "rescue.markers", "rescue.links")
EVENTS = ("rescue.local.request", "rescue.bid", "rescue.center.request", "rescue.plan",
          "rescue.event", "rescue.transport.hop", "rescue.transport.send", "rescue.transport.delivered", "rescue.transport.failed")
STRING = {"type": "string"}
NUMBER = {"type": "number"}
VECTOR = {"type": "vector", "length": 3, "items": NUMBER}


def bid_schema() -> dict:
    return object_schema({
        "roles": {"type": "array", "items": {"type": "string", "enum": ["inspect", "relay", "deliver"]}},
        "available_after_s": NUMBER, "reason": STRING})


def participation_config(cfg: dict) -> dict:
    policy = {"mode": "filter" if cfg.get("participation_filter") else "model", "per_role": 1,
              **cfg.get("participation", {})}
    if "filter" not in policy and cfg.get("participation_filter"):
        policy["filter"] = cfg["participation_filter"]
    if policy["mode"] not in {"model", "filter", "all", "distance"}:
        raise ValueError("rescue.participation.mode: expected model, filter, all or distance")
    if type(policy["per_role"]) is not int or policy["per_role"] < 1:
        raise ValueError("rescue.participation.per_role: positive integer required")
    if policy["mode"] == "filter" and not policy.get("filter"):
        raise ValueError("rescue.participation.filter: exported filter path required")
    return policy


def eligible(vehicle: dict, role: str, request: dict) -> bool:
    """Match AeroRescue adapter.eligible; travel/dependencies stay with the planner."""
    if role not in vehicle["capabilities"] or vehicle["soc"] < 0.2 or vehicle["speed_m_s"] <= 0:
        return False
    if role == "deliver" and not request["payload_kg"] <= vehicle["payload_kg"] <= vehicle["payload_capacity_kg"]:
        return False
    busy = (vehicle.get("commitment") or {}).get("busy_until_s", request["now_s"])
    if busy is None or not math.isfinite(busy):
        return False
    ready = max(request["now_s"], busy)
    return ready <= request["deadline_s"]


def deterministic_bid(request: dict, vehicles: list[dict], policy: dict) -> dict:
    vehicle = request["self"]
    roles = []
    for role in ("inspect", "relay", "deliver"):
        candidates = [row for row in vehicles if eligible(row, role, request)]
        if policy["mode"] == "distance":
            candidates.sort(key=lambda row: (distance(tuple(row["position"]), tuple(request["locations"][role])), row["id"]))
            candidates = candidates[:policy["per_role"]]
        if any(row["id"] == vehicle["id"] for row in candidates):
            roles.append(role)
    return {"roles": roles, "available_after_s": max(request["now_s"], (vehicle.get("commitment") or {}).get("busy_until_s", request["now_s"])),
            "reason": policy["mode"] + " physically eligible capabilities"}


def materialize(document: dict) -> None:
    """Explicit lowalt base keeps the prototype YAML readable instead of copying its registry."""
    if any(e.get("config", {}).get("rescue", {}).get("profile") == "native_companion" for e in document["engines"].values()):
        raise ValueError("native_companion uses the dedicated companion.coordinator entry; legacy LowaltTasks cannot own its actions")
    configs = [e["config"] for e in document["engines"].values()
               if e["plugin"] == "lowalt_tasks" and e["config"].get("rescue", {}).get("scene_base") == "lowalt"]
    if not configs:
        return
    from .everyday import _group, _site
    from .template import components_document, vehicle_facts
    cfg = configs[0]["rescue"]
    cfg.pop("scene_base")
    from .rescue_episodes import vary_fleet
    vary_fleet(cfg, document["run"]["seed"])
    base = components_document()
    for name in ("id", "city", "run", "registry"):
        if name == "registry":
            base[name]["snapshot"] = document[name]["snapshot"]
        elif name in document:
            base[name] = document[name]
    for package in base["behaviours"]:
        package["registry"]["snapshot"] = base["registry"]["snapshot"]
    for site in cfg["sites"]:
        _site(base, site["id"], site["position"], site.get("kind", "Site"))
        base["entities"][-1].update(label=site["label"], label_zh=site.get("label_zh", site["label"]))
    for identity, profile in cfg["vehicles"].items():
        base["entities"].append({"id": identity, "type": "lowalt:UAV", "label": identity, "facts": {
            **vehicle_facts(), "lowalt.position": profile["position"], "lowalt.payload_kg": profile.get("payload_kg", 0.0),
            "lowalt.soc": profile.get("soc", 1.0), "lowalt.motion_energy_j": 240000.0 * profile.get("soc", 1.0)}})
    old = cfg["existing_task"]
    _group(base, "medical", "rescue-existing", [0], [old["origin"]], [old["destination"]], priority=50, payload=1.0)
    base["entities"][-1]["id"] = old["id"]
    base["engines"]["tasks"]["config"]["generators"][0]["tasks"] = [old["id"]]
    task_cfg = base["engines"]["tasks"]["config"]
    task_cfg["rescue"] = cfg
    task_cfg["vehicle_task_kinds"] = {identity: ["medical"] if profile.get("protected_task") else ["inspection"] for identity, profile in cfg["vehicles"].items()}
    base["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"]["value"] = [0.0, 0.0, 0.0]
    document.clear()
    document.update(base)


def expand(document: dict) -> None:
    """Only rescue-enabled scenes gain these fields and independent harnesses."""
    if any(row.get("config", {}).get("rescue", {}).get("profile") == "native_companion" for row in document["engines"].values()):
        raise ValueError("native_companion must not construct legacy rescue UAV/centre/transport engines")
    owners = [(name, row["config"]) for name, row in document["engines"].items()
              if row["plugin"] == "lowalt_tasks" and "rescue" in row["config"]]
    if not owners:
        return
    from aeroagentsim.agents.langgraph import record_descriptor
    registry = document["registry"]
    fields = registry.setdefault("fields", [])
    specs = [("lowalt:Environment", "rescue.flood_bounds", {"type": "vector", "length": 6, "items": NUMBER}),
             ("lowalt:Environment", "rescue.flooded_roads", {"type": "array", "items": {
                 "type": "record", "members": {"start": VECTOR, "end": VECTOR}, "required": ["start", "end"], "extra": False}}),
             ("lowalt:Site", "rescue.incident_state", STRING),
             *[("lowalt:UAV", name, STRING) for name in FIELDS[3:6]],
             ("lowalt:Environment", "rescue.markers", {"type": "array", "items": {
                 "type": "record", "members": {"id": STRING, "kind": {"type": "string", "enum": ["inspect", "relay", "command"]}, "position": VECTOR},
                 "required": ["id", "kind", "position"], "extra": False}}),
             ("lowalt:Environment", "rescue.links", {"type": "array", "items": {
                 "type": "record", "members": {"id": STRING, "from": STRING, "to": STRING,
                     "kind": {"type": "string", "enum": ["report", "delivery_ack", "relay_coverage"]},
                     "state": {"type": "string", "enum": ["sending", "delivered", "failed", "holding"]}},
                 "required": ["id", "from", "to", "kind", "state"], "extra": False}})]
    for type_id, name, schema in specs:
        if not any(row["id"] == name for row in fields):
            fields.append({"id": name, "type": type_id, "schema": schema,
                           "metadata": {"role": "observation", "unit": "1", "time_semantics": "committed simulation state"}})
    messages = registry.setdefault("messages", [])
    for name in EVENTS:
        if not any(row["id"] == name for row in messages):
            messages.append({"id": name, "kind": "event", "schema": {
                "type": "record", "members": {"data_json": STRING}, "required": ["data_json"], "extra": False}})
    descriptor = record_descriptor()
    if not any(row["id"] == descriptor["id"] for row in messages):
        messages.append(descriptor)
    for owner, config in owners:
        cfg = config["rescue"]
        profiles = cfg["vehicles"]
        participation = participation_config(cfg)
        entities = {row["id"]: row for row in document["entities"]}
        for identity, profile in profiles.items():
            facts = entities[identity]["facts"]
            facts.update({"rescue.role": "committed" if profile.get("protected_task") else "idle",
                          "rescue.status": "continuing" if profile.get("protected_task") else "idle",
                          "rescue.task": profile.get("protected_task", "")})
        entities[cfg["incident"]]["facts"]["rescue.incident_state"] = "pending"
        environment = next(row for row in document["entities"] if row["type"] == "lowalt:Environment")
        environment["facts"].update({"rescue.flood_bounds": cfg["flood_bounds"], "rescue.flooded_roads": cfg["flooded_roads"],
            "rescue.markers": [
                {"id": "rescue-inspect", "kind": "inspect", "position": cfg["inspect_position"]},
                {"id": "rescue-relay", "kind": "relay", "position": cfg["relay_position"]},
                {"id": "rescue-command", "kind": "command", "position": entities[(cfg.get("radio") or {}).get("center", "command-center")]["facts"]["lowalt.site_position"]}],
            "rescue.links": []})
        for type_id, names in (("lowalt:Environment", FIELDS[:2] + FIELDS[6:]), ("lowalt:Site", FIELDS[2:3]), ("lowalt:UAV", FIELDS[3:6])):
            rule = {"writer": owner, "type": type_id, "fields": list(names)}
            if rule not in document["bindings"]["rules"]:
                document["bindings"]["rules"].append(rule)
        for identity in profiles:
            engine_id = "rescue_uav_" + identity
            if participation["mode"] == "model":
                document["engines"][engine_id] = {"plugin": "langgraph", "config": engine_config(cfg["provider"], "local", identity)}
            else:
                document["engines"].pop(engine_id, None)
        document["engines"]["rescue_center"] = {"plugin": "langgraph", "config": engine_config(cfg["provider"], "center")}
        if cfg.get("radio"):
            from .rescue_transport import engine_config as transport_config, expand_radio
            expand_radio(document, cfg)
            document["engines"]["rescue_transport"] = {"plugin": "langgraph", "config": transport_config(cfg["provider"], cfg["radio"])}


def engine_config(provider: dict, role: str, vehicle: str = "") -> dict:
    from aeroagentsim.algorithms.llm import engine_config as base_config
    cfg = base_config({"provider": {"mode": "live", **provider}})
    topic = "rescue.local." + vehicle if role == "local" else "rescue.center"
    cfg.update(factory="aeroagentsim.packs.lowalt.rescue:decision_graph", options={"role": role, "vehicle": vehicle},
               triggers=[{"schema": "rescue.local.request" if role == "local" else "rescue.center.request", "topic": topic}])
    cfg["grants"]["events"] = [{"schema": "rescue.bid" if role == "local" else "rescue.plan", "topic": "rescue.results"}]
    cfg["budget"].update(wall_timeout_s=120.0, max_calls=2, max_tokens=4000, max_retries=0)
    return cfg


def decision_graph(client: JournalClient, options: dict) -> Any:
    """Each UAV and the center have their own engine, identity, and function tool."""
    from langgraph.graph import END, START, StateGraph
    async def decide(state: dict) -> dict:
        observation = json.loads(state["trigger"]["payload"]["data_json"])
        local = options["role"] == "local"
        action = object_schema({"id": STRING, "role": {"type": "string", "enum": ["inspect", "relay", "deliver"]},
                                "after": {"type": "array", "items": STRING}})
        schema = bid_schema() if local else object_schema({
            "actions": {"type": "array", "items": action, "maxItems": 3}, "reason": STRING})
        name = "propose_roles" if local else "propose_plan"
        prompt = (f"You are the independent UAV harness for {options['vehicle']}. Use only your own observation. "
                  "Propose useful task roles, including complementary relay work. Respect your capabilities, cargo, and existing protected commitment. "
                  "Roles are conditional contributions to the whole mission, not authorization to execute now: a loaded courier may offer delivery pending confirmation. "
                  "A protected task in progress means no immediate participation. Use your propose_roles tool; roles=[] is a valid non-participation decision.") if local else (
                  "You are the rescue command-center harness. Use propose_plan to form a task graph from received evidence and local role proposals. "
                  "Locations and durations belong to the fixed action domain; propose roles and dependencies, not UAV identities. "
                  "Plan the next executable phase: inspect obtains new evidence; relay provides the damaged site's report/ack channel and stays occupied; "
                  "deliver consumes cargo and needs a positive report already received by the center. A suspected person is not confirmed. "
                  "Infer which actions are needed from the mission and evidence, and express their dependencies; independent work may run concurrently. "
                  "Return actions=[] if the evidence rules out rescue or remains too uncertain to authorize further work. Preserve existing commitments. Phase rule: if evidence is suspected, propose ONLY inspect and relay (never deliver). If evidence is positive (positive report already received by the center), propose relay and deliver (deliver after relay); do not re-inspect. Always call propose_plan exactly once with the tool.")
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(observation, ensure_ascii=False)}]
        tool = {"type": "function", "function": {"name": name, "description": "Submit your proposed rescue contribution; execution remains with the simulator.", "strict": True, "parameters": schema}}
        response = await client.complete(name, messages, [tool])
        calls = response["message"].get("tool_calls", [])
        if len(calls) != 1 or calls[0]["function"]["name"] != name:
            raise ValueError("rescue decision must call its single proposal tool")
        proposal = strict_json(calls[0]["function"]["arguments"])
        validate_strict(schema, proposal)
        if local:
            proposal["vehicle"] = options["vehicle"]
            proposal["participation_source"] = "model_api"
        proposal["event"] = observation["event"]
        proposal["phase"] = observation["phase"]
        proposal["now_s"] = observation["now_s"]
        return {"outputs": [{"kind": "event", "schema": "rescue.bid" if local else "rescue.plan", "topic": "rescue.results",
                             "payload": {"data_json": json.dumps(proposal, ensure_ascii=False)}}]}

    graph = StateGraph(dict)
    graph.add_node("decide", decide)
    graph.add_edge(START, "decide")
    graph.add_edge("decide", END)
    return graph


class Rescue:
    """Task-owner extension; movements still use the existing flight receipts."""
    def __init__(self, owner: Any):
        self.owner, self.cfg = owner, owner.cfg["rescue"]
        self.incident = owner.by_id[self.cfg["incident"]]
        self.phase = "pending"
        self.bids: dict[str, dict[str, dict]] = {}
        self.planning_phase = ""
        self.phase_request: dict = {}
        self.participation = participation_config(self.cfg)
        self.filter = None
        if self.participation["mode"] == "filter":
            from aerorescue.adapter import ParticipationFilter
            self.filter = ParticipationFilter.load(self.participation["filter"])
        self.noticed_s: float | None = None
        self.confirmed_s: float | None = None
        self.delivered_s: float | None = None
        self.outcome_emitted = False
        self.links: list[dict] = []
        self.written_links: list[dict] = []
        self.position_checks: dict[str, tuple] = {}
        self.environment = next(ref for ref in owner.build.entities if ref.type_id == "lowalt:Environment")
        self.actions: dict[str, dict] = {}
        self.done: set[str] = set()
        self.relay: str | None = None
        self.relay_ready = False
        self.evidence: str | None = None
        self.received: str | None = None
        self.report_sent = False
        self.plan_pending = False
        self.commitments_finished: set[str] = set()
        self.execution_timeout_ns = round(finite(self.cfg.get("execution_timeout_s", 30), "rescue execution timeout", 1e-9) * 1e9)
        self.excluded_vehicles: set[str] = set()
        self.execution_paused = False
        self.execution_replanned = False

    def event(self, ctx: Any, kind: str, **data: Any) -> None:
        ctx.emit("rescue.event", {"data_json": json.dumps({"event": self.incident.id, "kind": kind, **data}, ensure_ascii=False)}, topic="rescue.results")

    def state(self, ctx: Any, phase: str) -> None:
        if self.phase != phase:
            self.phase = phase
            ctx.set(self.incident, "rescue.incident_state", phase)

    def vehicle_state(self, ctx: Any) -> list[dict]:
        rows = []
        for identity, profile in self.cfg["vehicles"].items():
            flight = self.owner.flights[identity]
            job = flight.job
            rows.append({"id": identity, "position": list(vec(read(ctx, flight.vehicle, "lowalt.position"))),
                         "capabilities": profile["capabilities"], "soc": read(ctx, flight.vehicle, "lowalt.soc"),
                         "payload_kg": read(ctx, flight.vehicle, "lowalt.payload_kg"),
                         "payload_capacity_kg": read(ctx, flight.vehicle, "lowalt.payload_capacity_kg"),
                         "speed_m_s": profile.get("speed_m_s", 15.0),
                         "control": flight.purpose,
                         "commitment": {"task": job.ref.id, "protected": job.ref.id == profile.get("protected_task"),
                                        "phase": job.phase, **self.busy_estimate(ctx, flight, profile)} if job is not None else None})
        return rows

    def label(self, ctx: Any, vehicle: str, role: str, status: str, task: str = "") -> None:
        ref = self.owner.by_id[vehicle]
        for field, value in (("role", role), ("status", status), ("task", task)):
            ctx.set(ref, "rescue." + field, value)

    def busy_estimate(self, ctx: Any, flight: Any, profile: dict) -> dict:
        """Estimate the live remaining job legs, including an in-flight waypoint."""
        job = flight.job
        speed = profile.get("speed_m_s", 15.0)
        if speed <= 0 or not math.isfinite(speed) or job.phase not in {"assigned", "started", "picked_up"} or flight.route_blocked_reason:
            # A finite infinity sentinel survives strict JSON/model serialization.
            return {"busy_until_s": 1e300, "busy_estimate": "unknown"}
        position = vec(read(ctx, flight.vehicle, "lowalt.position"))
        points = []
        if (flight.pending or flight.command_id is not None) and flight.waypoint is not None:
            points.append(flight.waypoint)
        # A control diversion keeps the job's route separately. Paused routes
        # likewise replace, rather than duplicate, the remaining task route.
        if flight.purpose != "task":
            points.extend(flight.route)
            points.extend(flight.saved_route)
        else:
            points.extend(flight.paused_route if flight.paused_route is not None else flight.route)
        if not points and job.work_obligations:
            points.extend(job.work_obligations)
        endpoints = ["origin", "destination"] if job.phase in {"assigned", "started"} else ["destination"]
        for endpoint in endpoints:
            site = self.owner.by_id[read(ctx, job.ref, "lowalt.task_" + endpoint)]
            point = vec(read(ctx, site, "lowalt.site_position"))
            if not points or points[-1] != point:
                points.append(point)
        travel = 0.0
        for point in points:
            travel += distance(position, tuple(point)) / speed
            position = tuple(point)
        now = ctx.now.ns / 1e9
        dwell = max(0.0, (flight.hold_until_ns - ctx.now.ns) / 1e9,
                    ((job.spray_hold_until_ns or ctx.now.ns) - ctx.now.ns) / 1e9)
        return {"busy_until_s": now + travel + dwell, "busy_estimate": "route"}

    def start_phase(self, ctx: Any, phase: str) -> None:
        self.planning_phase = phase
        self.bids[phase] = {}
        self.plan_pending = False
        self.phase_request = {
            "event": self.incident.id, "phase": phase, "evidence": self.received or "suspected",
            "mission": self.cfg["description"],
            "vehicles": [vehicle for vehicle in self.vehicle_state(ctx) if vehicle["id"] not in self.excluded_vehicles],
            "now_s": ctx.now.ns / 1e9,
            "deadline_s": self.cfg.get("deadline_s", 300), "payload_kg": self.cfg.get("payload_kg", 1.0),
            "locations": {"inspect": self.cfg["inspect_position"], "relay": self.cfg["relay_position"],
                          "deliver": list(vec(read(ctx, self.incident, "lowalt.site_position")))},
            "duration_s": {"inspect": self.cfg.get("inspect_s", 5), "relay": 0, "deliver": self.cfg.get("delivery_s", 3)}}
        if self.excluded_vehicles:
            self.phase_request["excluded_vehicles"] = sorted(self.excluded_vehicles)
        requests = [{**{k: v for k, v in self.phase_request.items() if k != "vehicles"}, "self": vehicle}
                    for vehicle in self.phase_request["vehicles"]]
        for request in requests:
            ctx.emit("rescue.local.request", {"data_json": json.dumps(request, ensure_ascii=False)}, topic="rescue.local." + request["self"]["id"])
        if self.participation["mode"] != "model":
            for request in requests:
                try:
                    if self.filter is not None:
                        started = time.perf_counter()
                        try:
                            proposal = self.filter.propose(request)
                        finally:
                            self.record_filter_timing(request, time.perf_counter() - started)
                        bid = {key: proposal[key] for key in ("roles", "available_after_s", "reason")}
                        validate_strict(bid_schema(), bid)
                    else:
                        bid = deterministic_bid(request, self.phase_request["vehicles"], self.participation)
                    bid.update(event=self.incident.id, phase=phase, now_s=request["now_s"], vehicle=request["self"]["id"],
                               participation_source="trained_filter" if self.filter is not None else self.participation["mode"])
                    ctx.emit("rescue.bid", {"data_json": json.dumps(bid, ensure_ascii=False)}, topic="rescue.results")
                except Exception as exc:
                    self.state(ctx, "blocked")
                    self.event(ctx, "model_failed", details={"mode": self.participation["mode"], "message": str(exc)})
                    break
        if not requests:
            self.request_center(ctx)

    def record_filter_timing(self, request: dict, elapsed_s: float) -> None:
        directory = self.owner.build.run_directory
        if directory is not None:
            path = directory / "rescue-filter-timing.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": request["event"], "phase": request["phase"],
                    "vehicle": request["self"]["id"], "now_s": request["now_s"], "latency_s": elapsed_s}) + "\n")

    def request_center(self, ctx: Any) -> None:
        data = {**self.phase_request, "proposals": list(self.bids[self.planning_phase].values())}
        ctx.emit("rescue.center.request", {"data_json": json.dumps(data, ensure_ascii=False)}, topic="rescue.center")
        self.plan_pending = True

    def finish(self, ctx: Any, success: bool) -> None:
        if self.outcome_emitted:
            return
        self.outcome_emitted = True
        self.event(ctx, "outcome", success=success,
                   time_to_confirmation_s=None if self.confirmed_s is None else self.confirmed_s - self.noticed_s,
                   time_to_delivery_s=None if self.delivered_s is None else self.delivered_s - self.noticed_s)

    def expire(self, ctx: Any) -> bool:
        if not self.outcome_emitted and self.noticed_s is not None and ctx.now.ns / 1e9 >= self.cfg.get("deadline_s", 300):
            self.state(ctx, "expired")
            self.event(ctx, "deadline_missed", deadline_s=self.cfg.get("deadline_s", 300))
            for link in self.links:
                if link["state"] == "sending":
                    link["state"] = "failed"
            self.release(ctx)
            self.finish(ctx, False)
        return self.outcome_emitted

    def write_links(self, ctx: Any) -> None:
        # The authored initial value is []; keep a detached copy because hop
        # completion mutates rows in place. Equal writes carry no new state.
        if self.links != self.written_links:
            snapshot = [dict(row) for row in self.links]
            ctx.set(self.environment, "rescue.links", snapshot)
            self.written_links = snapshot

    def distance_to_target(self, ctx: Any, flight: Any, target: tuple) -> float:
        # Still read the authoritative version on every tick (including its
        # provenance). Arrival and the watchdog share one value/target cache
        # per vehicle, independent of elapsed ticks or journal length.
        value = ctx.get(flight.vehicle, "lowalt.position")
        if isinstance(value, Absent):
            raise TypeError(f"pack input {flight.vehicle.id}.lowalt.position: no valid source observation")
        previous = self.position_checks.get(flight.vehicle.id)
        if previous is not None and previous[0] == value and previous[1] == target:
            return previous[2]
        position = vec(value)
        gap = distance(position, target)
        self.position_checks[flight.vehicle.id] = (position, target, gap)
        return gap

    def at_target(self, ctx: Any, flight: Any, target: tuple) -> bool:
        return self.distance_to_target(ctx, flight, target) <= self.owner.radius

    def hop(self, ctx: Any, data: dict) -> None:
        hop = data["hop"]
        if hop > 0:
            for link in self.links:
                if link["id"] == data["message_id"] + "/" + str(hop - 1):
                    link["state"] = "delivered"
        identity = data["message_id"] + "/" + str(hop)
        if not any(link["id"] == identity for link in self.links):
            self.links.append({"id": identity, "from": data["source"], "to": data["destination"], "kind": data["kind"], "state": "sending"})
        self.write_links(ctx)

    def transport_result(self, ctx: Any, data: dict, state: str) -> None:
        prefix = self.incident.id + "/" + data["kind"] + "/"
        for link in self.links:
            if link["id"].startswith(prefix):
                link["state"] = state

    def on_message(self, ctx: Any, schema: str, payload: dict) -> bool:
        if schema == "aas.langgraph.record" and payload["phase"] == "failure" and payload["decision_id"].startswith("rescue_"):
            if not self.expire(ctx):
                self.state(ctx, "blocked")
                self.event(ctx, "model_failed", details=json.loads(payload["data_json"]))
            return True
        if schema not in EVENTS or schema == "rescue.event":
            return False
        data = json.loads(payload["data_json"])
        if data.get("event") != self.incident.id:
            return True
        if self.expire(ctx):
            return True
        if schema == "rescue.bid":
            if data.get("phase") != self.planning_phase or data.get("now_s") != self.phase_request["now_s"]:
                return True
            if data["vehicle"] not in {vehicle["id"] for vehicle in self.phase_request["vehicles"]}:
                return True
            bids = self.bids[self.planning_phase]
            bids[data["vehicle"]] = data
            if len(bids) == len(self.phase_request["vehicles"]) and not self.plan_pending:
                self.request_center(ctx)
        elif schema == "rescue.plan":
            if (data.get("phase") != self.planning_phase or data.get("now_s") != self.phase_request["now_s"]
                    or not self.plan_pending):
                return True
            self.plan_pending = False
            self.apply_plan(ctx, data)
        elif schema == "rescue.transport.hop":
            self.hop(ctx, data)
        elif schema == "rescue.transport.delivered":
            self.transport_result(ctx, data, "delivered")
            self.event(ctx, "message_delivered", message_kind=data["kind"], **{k: v for k, v in data.items() if k not in {"event", "kind"}})
            if data["kind"] == "report":
                self.write_links(ctx)
                self.received = data["evidence"]
                self.confirmed_s = ctx.now.ns / 1e9
                self.start_phase(ctx, "report_received")
            else:
                self.state(ctx, "delivered")
                self.release(ctx)
                self.finish(ctx, True)
        elif schema == "rescue.transport.failed":
            self.transport_result(ctx, data, "failed")
            self.write_links(ctx)
            self.state(ctx, "blocked")
            self.event(ctx, "communication_failed", details=data)
        return True

    def apply_plan(self, ctx: Any, graph: dict) -> None:
        from .rescue_planner import solve_plan
        roles = {a["role"] for a in graph["actions"]}
        if len(roles) != len(graph["actions"]):
            self.event(ctx, "plan_rejected", reason="This incident has at most one action per role")
            self.state(ctx, "blocked")
            return
        if "deliver" in roles and self.received != "positive":
            self.state(ctx, "blocked")
            self.event(ctx, "plan_rejected", reason="Delivery requires a received positive observation")
            return
        if not graph["actions"]:
            if self.received not in {"negative", "uncertain"}:
                self.state(ctx, "blocked")
                self.event(ctx, "plan_rejected", reason="An unresolved rescue mission needs an action plan")
                return
            problem = {"vehicles": self.phase_request["vehicles"], "bids": list(self.bids[self.planning_phase].values()),
                       "now_s": self.phase_request["now_s"], "graph": {**graph, "deadline_s": self.cfg.get("deadline_s", 300), "payload_kg": self.cfg.get("payload_kg", 1.0)}}
            self.event(ctx, "plan_computed", phase=self.planning_phase, now_s=problem["now_s"], problem=problem,
                       candidate_universe=[], plan=solve_plan(**problem))
            self.state(ctx, "negative" if self.received == "negative" else "uncertain")
            self.release(ctx)
            self.finish(ctx, True)
            return
        locations = {"inspect": self.cfg["inspect_position"], "relay": self.cfg["relay_position"],
                     "deliver": list(vec(read(ctx, self.incident, "lowalt.site_position")))}
        expected = {"relay", "deliver" if self.received == "positive" else "inspect"}
        if not expected <= roles:
            self.state(ctx, "blocked")
            self.event(ctx, "plan_rejected", reason="Plan is missing a required rescue role")
            return
        durations = {"inspect": self.cfg.get("inspect_s", 5), "relay": 0, "deliver": self.cfg.get("delivery_s", 3)}
        relay_ids = [a["id"] for a in graph["actions"] if a["role"] == "relay"]
        graph = {**graph, "actions": [{**a, "target": locations[a["role"]], "duration_s": durations[a["role"]],
                                      "after": list(dict.fromkeys(a["after"] + (relay_ids if a["role"] == "deliver" else [])))}
                                     for a in graph["actions"]]}
        vehicles = self.phase_request["vehicles"]
        free = {vehicle["id"] for vehicle in vehicles if vehicle["commitment"] is None and vehicle["control"] in {"task", "rescue"}}
        # Dispatch only currently free vehicles; existing tasks retain their owner.
        problem = {"vehicles": vehicles, "bids": [bid for bid in self.bids[self.planning_phase].values() if bid["vehicle"] in free], "now_s": self.phase_request["now_s"],
                   "graph": {**graph, "deadline_s": self.cfg.get("deadline_s", 300), "payload_kg": self.cfg.get("payload_kg", 1.0)}}
        plan = solve_plan(**problem)
        candidates = [{"vehicle": vehicle["id"], "roles": [role for role in ("inspect", "relay", "deliver")
                       if eligible(vehicle, role, self.phase_request)], "available_after_s": problem["now_s"]}
                      for vehicle in vehicles if vehicle["id"] in free]
        self.event(ctx, "plan_computed", phase=self.planning_phase, now_s=problem["now_s"], problem=problem, candidate_universe=candidates, plan=plan)
        if plan["status"] != "feasible":
            self.state(ctx, "blocked")
            return
        old_relay, old_ready = self.relay, self.relay_ready
        previous = {a["vehicle"] for a in self.actions.values()}
        selected = {a["vehicle"] for a in plan["assignments"]}
        for vehicle in previous - selected:
            self.unreserve(vehicle)
            self.label(ctx, vehicle, "idle", "blocked" if vehicle in self.excluded_vehicles else "completed", self.incident.id)
        self.actions = {row["action"]: {**row, "duration_s": next(a["duration_s"] for a in graph["actions"] if a["id"] == row["action"]),
                                         "after": next(a["after"] for a in graph["actions"] if a["id"] == row["action"])}
                        for row in plan["assignments"]}
        keep_relay = old_ready and any(row["role"] == "relay" and row["vehicle"] == old_relay and distance(
            vec(read(ctx, self.owner.flights[old_relay].vehicle, "lowalt.position")), tuple(row["target"])) <= self.owner.radius
            for row in plan["assignments"])
        if not keep_relay:
            self.links = [row for row in self.links if row["kind"] != "relay_coverage"]
            self.write_links(ctx)
        self.done.clear()
        self.relay, self.relay_ready = None, False
        if self.execution_paused:
            self.state(ctx, "confirmed" if self.received == "positive" else "suspected")
        self.execution_paused = False
        for vehicle in dict.fromkeys(a["vehicle"] for a in self.actions.values()):
            action = next(a for a in self.actions.values() if a["vehicle"] == vehicle)
            flight = self.owner.flights[vehicle]
            if action["role"] == "relay" and vehicle == old_relay and keep_relay:
                self.relay, self.relay_ready = vehicle, True
                self.done.add(action["action"])
                flight.purpose = "rescue"
                self.label(ctx, vehicle, "relay", "holding", self.incident.id + "/" + action["action"])
                continue
            flight.purpose, flight.route, flight.goal = "rescue", (), None
            flight.replan = True
            if action["role"] == "relay":
                self.relay, self.relay_ready = vehicle, False
            self.label(ctx, vehicle, action["role"], "enroute", self.incident.id + "/" + action["action"])
        # The outgoing relay must vacate the incoming relay's point. Only its
        # positioning flight may precede readiness; delivery work still waits.
        handover = next((a for a in self.actions.values() if old_ready and not keep_relay
                         and a["vehicle"] == old_relay and a["role"] == "deliver"), None)
        if handover is not None:
            handover["handover"] = True
            self.event(ctx, "relay_handover", vehicle=old_relay, relay=self.relay)

    def check_progress(self, ctx: Any) -> None:
        if self.execution_paused:
            return
        active: set[str] = set()
        for action in self.actions.values():
            vehicle = action["vehicle"]
            if action["action"] in self.done or vehicle in active:
                continue
            active.add(vehicle)
            if not action.get("handover") and (ctx.now.ns / 1e9 < action["departure_s"]
                    or any(dep not in self.done for dep in action["after"])):
                continue
            if "due_ns" in action and ctx.now.ns <= action["due_ns"]:
                action["progress_ns"] = ctx.now.ns
                continue
            flight = self.owner.flights[vehicle]
            gap = self.distance_to_target(ctx, flight, tuple(action["target"]))
            if action.get("handover") and gap <= self.owner.radius and (ctx.now.ns / 1e9 < action["departure_s"]
                    or any(dep not in self.done for dep in action["after"]) or not self.relay_ready):
                action.pop("closest_m", None)
                continue
            # A new command, route retry or avoidance oscillation is not mission
            # progress. Use a monotonic physical approach to the assigned target.
            if "closest_m" not in action or gap < action["closest_m"] - 1e-3:
                action.update(closest_m=gap, progress_ns=ctx.now.ns)
                action.pop("blocked_reason", None)
            if flight.status in {"failed", "rejected", "canceled"}:
                action["blocked_reason"] = "motion " + flight.status
            if ctx.now.ns - action["progress_ns"] < self.execution_timeout_ns:
                continue
            reason = action.get("blocked_reason") or flight.route_blocked_reason
            if not reason:
                reason = ("control diversion made no progress" if flight.purpose != "rescue" else
                          "no route to assigned target" if not flight.route and not flight.pending and flight.command_id is None else
                          "no progress toward assigned target")
            self.execution_paused = True
            self.excluded_vehicles.add(vehicle)
            self.state(ctx, "blocked")
            self.label(ctx, vehicle, action["role"], "blocked", self.incident.id + "/" + action["action"])
            self.event(ctx, "execution_blocked", vehicle=vehicle, reason=reason)
            if not self.execution_replanned:
                self.execution_replanned = True
                self.start_phase(ctx, "execution_replan")
            return

    def tick(self, ctx: Any) -> None:
        if self.expire(ctx):
            return
        if self.relay_ready:
            relay_action = next(a for a in self.actions.values() if a["role"] == "relay")
            flight = self.owner.flights[self.relay]
            if flight.purpose != "rescue" or not self.at_target(ctx, flight, tuple(relay_action["target"])):
                self.relay_ready = False
                self.done.discard(relay_action["action"])
                relay_action.pop("due_ns", None)
                self.links = [row for row in self.links if row["kind"] != "relay_coverage"]
                self.write_links(ctx)
                self.event(ctx, "relay_interrupted", vehicle=self.relay)
        for identity, profile in self.cfg["vehicles"].items():
            task = profile.get("protected_task")
            if task and identity not in self.commitments_finished and self.owner.jobs[task].phase == "completed":
                self.label(ctx, identity, "committed", "completed", task)
                self.commitments_finished.add(identity)
        if self.phase == "pending" and ctx.now.ns >= self.cfg["at_s"] * 1e9:
            self.state(ctx, "suspected")
            self.event(ctx, "noticed", description=self.cfg["description"])
            self.noticed_s = ctx.now.ns / 1e9
            self.start_phase(ctx, "notice")
        if self.evidence is not None and not self.report_sent and self.relay_ready:
            self.report_sent = True
            observer = next(a["vehicle"] for a in self.actions.values() if a["role"] == "inspect")
            self.send(ctx, "report", observer, evidence=self.evidence)
        self.check_progress(ctx)

    def send(self, ctx: Any, kind: str, source: str, **data: Any) -> None:
        # One report and one acknowledgement per incident; this prototype has no retries.
        if not self.cfg.get("radio"):
            self.state(ctx, "blocked")
            self.event(ctx, "communication_failed", reason="Native ns-3 radio has not been configured", message_kind=kind)
            return
        envelope = {"event": self.incident.id, "kind": kind, "source": source, "relay": self.relay,
                    "destination": self.cfg["radio"]["center"], "message_id": self.incident.id + "/" + kind, **data}
        self.hop(ctx, {"message_id": envelope["message_id"], "hop": 0, "source": source,
                       "destination": self.relay if self.relay and source != self.relay else envelope["destination"], "kind": kind})
        ctx.emit("rescue.transport.send", {"data_json": json.dumps(envelope)}, topic="rescue.transport")

    def flight(self, ctx: Any, flight: Any) -> bool:
        if flight.purpose != "rescue":
            return False
        if self.execution_paused:
            return True
        action = next((a for a in self.actions.values() if a["vehicle"] == flight.vehicle.id and a["action"] not in self.done), None)
        if action is None:
            return True
        role = action["role"]
        waiting = (ctx.now.ns / 1e9 < action["departure_s"] or any(dep not in self.done for dep in action["after"])
                   or role == "deliver" and not self.relay_ready)
        if waiting and not action.get("handover"):
            return True
        goal = tuple(action["target"])
        arrived = not flight.route and flight.goal == goal and flight.status == "succeeded" and self.at_target(ctx, flight, goal)
        if arrived:
            if waiting:
                return True
            if "due_ns" not in action:
                action["due_ns"] = ctx.now.ns + round(action["duration_s"] * 1e9)
                self.label(ctx, flight.vehicle.id, role, "observing" if role == "inspect" else "holding" if role == "relay" else "delivering", self.incident.id + "/" + action["action"])
                self.event(ctx, "arrived", vehicle=flight.vehicle.id, role=role)
                if role == "inspect":
                    self.state(ctx, "observing")
                elif role == "deliver":
                    self.state(ctx, "delivering")
                return True
            if ctx.now.ns < action["due_ns"]:
                return True
            self.done.add(action["action"])
            if role == "relay":
                self.relay = flight.vehicle.id
                self.relay_ready = True
                self.event(ctx, "relay_ready", vehicle=flight.vehicle.id)
                self.links = [row for row in self.links if row["kind"] != "relay_coverage"]
                self.links.append({"id": "rescue-relay-coverage", "from": self.relay,
                    "to": (self.cfg.get("radio") or {}).get("center", "command-center"), "kind": "relay_coverage", "state": "holding"})
                self.write_links(ctx)
                return True
            if role == "inspect":
                self.evidence = self.cfg["observation"]
                self.state(ctx, "confirmed" if self.evidence == "positive" else self.evidence)
                self.event(ctx, "observation", vehicle=flight.vehicle.id, evidence=self.evidence, source="authored_semantic_observation")
            else:
                payload = read(ctx, flight.vehicle, "lowalt.payload_kg")
                ctx.set(flight.vehicle, "lowalt.payload_kg", payload - self.cfg.get("payload_kg", 1.0))
                self.delivered_s = ctx.now.ns / 1e9
                self.event(ctx, "delivery_completed", vehicle=flight.vehicle.id)
                self.send(ctx, "delivery_ack", flight.vehicle.id)
            flight.purpose = "rescue" if any(a["vehicle"] == flight.vehicle.id and a["action"] not in self.done for a in self.actions.values()) else "task"
            flight.goal, flight.route = None, ()
            self.label(ctx, flight.vehicle.id, role, "completed", self.incident.id + "/" + action["action"])
            return True
        action.pop("due_ns", None)
        if flight.replan or flight.goal != goal:
            if not self.owner._route(ctx, flight, goal):
                action["blocked_reason"] = flight.route_blocked_reason or "route planner returned no route"
                return True
        if flight.route:
            target, flight.route = flight.route[0], flight.route[1:]
            self.owner._submit(ctx, flight, target)
        return True

    def unreserve(self, vehicle: str) -> None:
        flight = self.owner.flights[vehicle]
        if flight.purpose == "rescue":
            flight.purpose, flight.goal, flight.route = "task", None, ()
        if flight.control_resume_purpose == "rescue":
            flight.control_resume_purpose, flight.saved_goal, flight.saved_route = "task", None, ()

    def release(self, ctx: Any) -> None:
        for vehicle in dict.fromkeys(a["vehicle"] for a in self.actions.values()):
            action = next(a for a in self.actions.values() if a["vehicle"] == vehicle)
            self.unreserve(vehicle)
            self.label(ctx, vehicle, action["role"], "completed", self.incident.id)
        self.relay_ready = False
        self.links = [row for row in self.links if row["kind"] != "relay_coverage"]
        self.write_links(ctx)
        self.event(ctx, "roles_released")
