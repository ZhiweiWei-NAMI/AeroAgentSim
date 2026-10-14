"""Small, event-driven rescue prototype on the existing task and model owners."""
from __future__ import annotations

import json
from typing import Any

from aeroagentsim.agents.langgraph_client import JournalClient
from aeroagentsim.agents.tools import object_schema, strict_json, validate_strict
from aeroagentsim.packs.common import distance, read, vec

FIELDS = ("rescue.flood_bounds", "rescue.flooded_roads", "rescue.incident_state",
          "rescue.role", "rescue.status", "rescue.task")
EVENTS = ("rescue.local.request", "rescue.bid", "rescue.center.request", "rescue.plan",
          "rescue.event", "rescue.transport.send", "rescue.transport.delivered", "rescue.transport.failed")
STRING = {"type": "string"}
NUMBER = {"type": "number"}
VECTOR = {"type": "vector", "length": 3, "items": NUMBER}


def materialize(document: dict) -> None:
    """Explicit lowalt base keeps the prototype YAML readable instead of copying its registry."""
    configs = [e["config"] for e in document["engines"].values()
               if e["plugin"] == "lowalt_tasks" and e["config"].get("rescue", {}).get("scene_base") == "lowalt"]
    if not configs:
        return
    from .everyday import _group, _site
    from .template import components_document, vehicle_facts
    cfg = configs[0]["rescue"]
    cfg.pop("scene_base")
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
        base["entities"][-1].update(label=site["label"], label_zh=site["label"])
    for identity, profile in cfg["vehicles"].items():
        base["entities"].append({"id": identity, "type": "lowalt:UAV", "label": identity, "facts": {
            **vehicle_facts(), "lowalt.position": profile["position"], "lowalt.payload_kg": profile.get("payload_kg", 0.0)}})
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
             *[("lowalt:UAV", name, STRING) for name in FIELDS[3:]]]
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
        entities = {row["id"]: row for row in document["entities"]}
        for identity, profile in profiles.items():
            facts = entities[identity]["facts"]
            facts.update({"rescue.role": "committed" if profile.get("protected_task") else "idle",
                          "rescue.status": "continuing" if profile.get("protected_task") else "idle",
                          "rescue.task": profile.get("protected_task", "")})
        entities[cfg["incident"]]["facts"]["rescue.incident_state"] = "pending"
        environment = next(row for row in document["entities"] if row["type"] == "lowalt:Environment")
        environment["facts"].update({"rescue.flood_bounds": cfg["flood_bounds"], "rescue.flooded_roads": cfg["flooded_roads"]})
        for type_id, names in (("lowalt:Environment", FIELDS[:2]), ("lowalt:Site", FIELDS[2:3]), ("lowalt:UAV", FIELDS[3:])):
            rule = {"writer": owner, "type": type_id, "fields": list(names)}
            if rule not in document["bindings"]["rules"]:
                document["bindings"]["rules"].append(rule)
        for identity in profiles:
            document["engines"]["rescue_uav_" + identity] = {"plugin": "langgraph", "config": engine_config(
                cfg["provider"], "local", identity)}
            if cfg.get("participation_filter"):
                document["engines"]["rescue_uav_" + identity]["config"]["options"]["participation_filter"] = cfg["participation_filter"]
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
    participation = None
    if options.get("participation_filter"):
        from aerorescue.adapter import ParticipationFilter
        participation = ParticipationFilter.load(options["participation_filter"])

    async def decide(state: dict) -> dict:
        observation = json.loads(state["trigger"]["payload"]["data_json"])
        local = options["role"] == "local"
        action = object_schema({"id": STRING, "role": {"type": "string", "enum": ["inspect", "relay", "deliver"]},
                                "after": {"type": "array", "items": STRING}})
        schema = object_schema({"roles": {"type": "array", "items": {"type": "string", "enum": ["inspect", "relay", "deliver"]}},
                                "available_after_s": NUMBER, "reason": STRING}) if local else object_schema({
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
                  "Return actions=[] if the evidence rules out rescue or remains too uncertain to authorize further work. Preserve existing commitments.")
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(observation, ensure_ascii=False)}]
        tool = {"type": "function", "function": {"name": name, "description": "Submit your proposed rescue contribution; execution remains with the simulator.", "strict": True, "parameters": schema}}
        if participation is not None:
            learned = participation.propose(observation)
            proposal = {key: learned[key] for key in ("roles", "available_after_s", "reason")}
        else:
            response = await client.complete(name, messages, [tool])
            calls = response["message"].get("tool_calls", [])
            if len(calls) != 1 or calls[0]["function"]["name"] != name:
                raise ValueError("rescue decision must call its single proposal tool")
            proposal = strict_json(calls[0]["function"]["arguments"])
        validate_strict(schema, proposal)
        if local:
            proposal["vehicle"] = options["vehicle"]
            proposal["participation_source"] = "trained_filter" if participation is not None else "model_api"
        proposal["event"] = observation["event"]
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
        self.bids: dict[str, dict] = {}
        self.actions: dict[str, dict] = {}
        self.done: set[str] = set()
        self.relay: str | None = None
        self.relay_ready = False
        self.evidence: str | None = None
        self.received: str | None = None
        self.report_sent = False
        self.plan_pending = False
        self.commitments_finished: set[str] = set()

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
                                        "phase": job.phase, "busy_until_s": self.cfg.get("deadline_s", 300)} if job is not None else None})
        return rows

    def label(self, ctx: Any, vehicle: str, role: str, status: str, task: str = "") -> None:
        ref = self.owner.by_id[vehicle]
        for field, value in (("role", role), ("status", status), ("task", task)):
            ctx.set(ref, "rescue." + field, value)

    def request_center(self, ctx: Any) -> None:
        evidence = self.received or "suspected"
        data = {"event": self.incident.id, "evidence": evidence, "mission": self.cfg["description"],
                "vehicles": self.vehicle_state(ctx), "proposals": list(self.bids.values()), "now_s": ctx.now.ns / 1e9,
                "locations": {"inspect": self.cfg["inspect_position"], "relay": self.cfg["relay_position"],
                              "deliver": list(vec(read(ctx, self.incident, "lowalt.site_position")))},
                "duration_s": {"inspect": self.cfg.get("inspect_s", 5), "relay": 0, "deliver": self.cfg.get("delivery_s", 3)}}
        ctx.emit("rescue.center.request", {"data_json": json.dumps(data, ensure_ascii=False)}, topic="rescue.center")
        self.plan_pending = True

    def on_message(self, ctx: Any, schema: str, payload: dict) -> bool:
        if schema == "aas.langgraph.record" and payload["phase"] == "failure" and payload["decision_id"].startswith("rescue_"):
            self.state(ctx, "blocked")
            self.event(ctx, "model_failed", details=json.loads(payload["data_json"]))
            return True
        if schema not in EVENTS or schema == "rescue.event":
            return False
        data = json.loads(payload["data_json"])
        if data.get("event") != self.incident.id:
            return True
        if schema == "rescue.bid":
            self.bids[data["vehicle"]] = data
            if len(self.bids) == len(self.cfg["vehicles"]) and not self.plan_pending:
                self.request_center(ctx)
        elif schema == "rescue.plan":
            self.plan_pending = False
            self.apply_plan(ctx, data)
        elif schema == "rescue.transport.delivered":
            self.event(ctx, "message_delivered", message_kind=data["kind"], **{k: v for k, v in data.items() if k not in {"event", "kind"}})
            if data["kind"] == "report":
                self.received = data["evidence"]
                self.request_center(ctx)
            else:
                self.state(ctx, "delivered")
                self.release(ctx)
        elif schema == "rescue.transport.failed":
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
            self.state(ctx, "negative" if self.received == "negative" else "uncertain")
            self.release(ctx)
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
        vehicles = self.vehicle_state(ctx)
        free = {vehicle["id"] for vehicle in vehicles if vehicle["commitment"] is None and vehicle["control"] in {"task", "rescue"}}
        # Dispatch only currently free vehicles; existing tasks retain their owner.
        problem = {"vehicles": vehicles, "bids": [bid for bid in self.bids.values() if bid["vehicle"] in free], "now_s": ctx.now.ns / 1e9,
                   "graph": {**graph, "deadline_s": self.cfg.get("deadline_s", 300), "payload_kg": self.cfg.get("payload_kg", 1.0)}}
        plan = solve_plan(**problem)
        candidates = [{"vehicle": vehicle["id"], "roles": vehicle["capabilities"], "available_after_s": problem["now_s"]}
                      for vehicle in vehicles if vehicle["id"] in free]
        self.event(ctx, "plan_computed", problem=problem, candidate_universe=candidates, plan=plan)
        if plan["status"] != "feasible":
            self.state(ctx, "blocked")
            return
        previous = {a["vehicle"] for a in self.actions.values()}
        selected = {a["vehicle"] for a in plan["assignments"]}
        for vehicle in previous - selected:
            self.unreserve(vehicle)
            self.label(ctx, vehicle, "idle", "completed", self.incident.id)
        self.actions = {row["action"]: {**row, "duration_s": next(a["duration_s"] for a in graph["actions"] if a["id"] == row["action"]),
                                         "after": next(a["after"] for a in graph["actions"] if a["id"] == row["action"])}
                        for row in plan["assignments"]}
        self.done.clear()
        self.relay, self.relay_ready = None, False
        for vehicle in dict.fromkeys(a["vehicle"] for a in self.actions.values()):
            action = next(a for a in self.actions.values() if a["vehicle"] == vehicle)
            flight = self.owner.flights[vehicle]
            flight.purpose, flight.route, flight.goal = "rescue", (), None
            flight.replan = True
            if action["role"] == "relay":
                self.relay, self.relay_ready = vehicle, False
            self.label(ctx, vehicle, action["role"], "enroute", self.incident.id + "/" + action["action"])

    def tick(self, ctx: Any) -> None:
        if self.relay_ready:
            relay_action = next(a for a in self.actions.values() if a["role"] == "relay")
            flight = self.owner.flights[self.relay]
            if flight.purpose != "rescue" or distance(vec(read(ctx, flight.vehicle, "lowalt.position")), tuple(relay_action["target"])) > self.owner.radius:
                self.relay_ready = False
                self.done.discard(relay_action["action"])
                relay_action.pop("due_ns", None)
                self.event(ctx, "relay_interrupted", vehicle=self.relay)
        for identity, profile in self.cfg["vehicles"].items():
            task = profile.get("protected_task")
            if task and identity not in self.commitments_finished and self.owner.jobs[task].phase == "completed":
                self.label(ctx, identity, "committed", "completed", task)
                self.commitments_finished.add(identity)
        if self.phase == "pending" and ctx.now.ns >= self.cfg["at_s"] * 1e9:
            self.state(ctx, "suspected")
            self.event(ctx, "noticed", description=self.cfg["description"])
            for vehicle in self.vehicle_state(ctx):
                data = {"event": self.incident.id, "mission": self.cfg["description"], "evidence": "suspected",
                        "self": vehicle, "now_s": ctx.now.ns / 1e9,
                        "deadline_s": self.cfg.get("deadline_s", 300), "payload_kg": self.cfg.get("payload_kg", 1.0),
                        "locations": {"inspect": self.cfg["inspect_position"], "relay": self.cfg["relay_position"],
                                      "deliver": list(vec(read(ctx, self.incident, "lowalt.site_position")))},
                        "duration_s": {"inspect": self.cfg.get("inspect_s", 5), "relay": 0, "deliver": self.cfg.get("delivery_s", 3)}}
                ctx.emit("rescue.local.request", {"data_json": json.dumps(data, ensure_ascii=False)}, topic="rescue.local." + vehicle["id"])
        if self.evidence is not None and not self.report_sent and self.relay_ready:
            self.report_sent = True
            observer = next(a["vehicle"] for a in self.actions.values() if a["role"] == "inspect")
            self.send(ctx, "report", observer, evidence=self.evidence)

    def send(self, ctx: Any, kind: str, source: str, **data: Any) -> None:
        # One report and one acknowledgement per incident; this prototype has no retries.
        if not self.cfg.get("radio"):
            self.state(ctx, "blocked")
            self.event(ctx, "communication_unavailable", reason="Native ns-3 radio has not been configured", message_kind=kind)
            return
        envelope = {"event": self.incident.id, "kind": kind, "source": source, "relay": self.relay,
                    "destination": self.cfg["radio"]["center"], "message_id": self.incident.id + "/" + kind, **data}
        ctx.emit("rescue.transport.send", {"data_json": json.dumps(envelope)}, topic="rescue.transport")

    def flight(self, ctx: Any, flight: Any) -> bool:
        if flight.purpose != "rescue":
            return False
        action = next((a for a in self.actions.values() if a["vehicle"] == flight.vehicle.id and a["action"] not in self.done), None)
        if action is None:
            return True
        role = action["role"]
        if ctx.now.ns / 1e9 < action["departure_s"] or any(dep not in self.done for dep in action["after"]):
            return True
        if role == "deliver" and not self.relay_ready:
            return True
        goal = tuple(action["target"])
        arrived = not flight.route and flight.goal == goal and flight.status == "succeeded" and distance(vec(read(ctx, flight.vehicle, "lowalt.position")), goal) <= self.owner.radius
        if arrived:
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
                return True
            if role == "inspect":
                self.evidence = self.cfg["observation"]
                self.state(ctx, "confirmed" if self.evidence == "positive" else self.evidence)
                self.event(ctx, "observation", vehicle=flight.vehicle.id, evidence=self.evidence, source="authored_semantic_observation")
            else:
                payload = read(ctx, flight.vehicle, "lowalt.payload_kg")
                ctx.set(flight.vehicle, "lowalt.payload_kg", payload - self.cfg.get("payload_kg", 1.0))
                self.event(ctx, "delivery_completed", vehicle=flight.vehicle.id)
                self.send(ctx, "delivery_ack", flight.vehicle.id)
            flight.purpose = "rescue" if any(a["vehicle"] == flight.vehicle.id and a["action"] not in self.done for a in self.actions.values()) else "task"
            flight.goal, flight.route = None, ()
            self.label(ctx, flight.vehicle.id, role, "completed", self.incident.id + "/" + action["action"])
            return True
        action.pop("due_ns", None)
        if flight.replan or flight.goal != goal:
            if not self.owner._route(ctx, flight, goal):
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
        self.event(ctx, "roles_released")
