"""Queued commands, with terminal outcomes from later native observations."""

from dataclasses import dataclass, field
from decimal import Decimal

from .wire import fields, integer, number, string

SECOND = 1_000_000_000
# SUMO's documented vehicle classes, including custom classes.
CLASSES = frozenset(
    [
        "ignoring",
        "private",
        "emergency",
        "authority",
        "army",
        "vip",
        "pedestrian",
        "passenger",
        "hov",
        "taxi",
        "bus",
        "coach",
        "delivery",
        "truck",
        "trailer",
        "motorcycle",
        "moped",
        "bicycle",
        "evehicle",
        "tram",
        "rail_urban",
        "rail",
        "rail_electric",
        "rail_fast",
        "ship",
        "container",
        "cable_car",
        "subway",
        "aircraft",
        "wheelchair",
        "scooter",
        "drone",
        "custom1",
        "custom2",
    ]
)


@dataclass
class Pending:
    ident: str
    action: str
    params: dict
    due: int
    deadline: int
    applied: bool = False
    expected: dict = field(default_factory=dict)


class Commands:
    def __init__(self, connection, step_ns):
        self.c = connection
        self.step_ns = step_ns
        self.ids = set()
        self.pending = []
        self.removed = []

    def active(self, vehicle):
        if vehicle not in self.c.vehicle.getIDList():
            raise ValueError(f"vehicle is not active: {vehicle}")

    def validate(self, action, params, current):
        required = {
            "set_speed": ("vehicle", "speed"),
            "reroute": ("vehicle",),
            "change_target": ("vehicle", "edge"),
            "lane_restriction": ("lane", "disallowed"),
            "tls_phase": ("tls", "phase"),
            "add_vehicle": ("vehicle", "route_id"),
            "remove_vehicle": ("vehicle",),
        }
        optional = {
            "reroute": ("edges",),
            "lane_restriction": ("at_sim_ns",),
            "tls_phase": ("duration_s",),
            "add_vehicle": ("type", "depart"),
        }
        if action not in required:
            raise ValueError(f"unsupported action: {action}")
        fields(params, required[action], optional.get(action, ()))
        if "vehicle" in params:
            string(params["vehicle"], "vehicle")
            if action == "add_vehicle":
                # Loaded includes pending departures, not only active objects.
                if params["vehicle"] in self.c.vehicle.getLoadedIDList():
                    raise ValueError("vehicle ID is already loaded")
            else:
                self.active(params["vehicle"])
        if action == "set_speed" and number(params["speed"], "speed") < 0:
            raise ValueError("speed must be nonnegative")
        if action == "reroute" and "edges" in params:
            edges = params["edges"]
            if not isinstance(edges, list) or not edges:
                raise ValueError("edges must be a nonempty list")
            known = set(self.c.edge.getIDList())
            for edge in edges:
                if string(edge, "edge") not in known or edge.startswith(":"):
                    raise ValueError(f"unknown/non-road edge: {edge}")
            if edges[0] != self.c.vehicle.getRoadID(params["vehicle"]):
                raise ValueError("explicit route must start on current road")
        if (
            action == "change_target"
            and string(params["edge"], "edge") not in self.c.edge.getIDList()
        ):
            raise ValueError("unknown destination edge")
        if action == "lane_restriction":
            if string(params["lane"], "lane") not in self.c.lane.getIDList():
                raise ValueError("unknown lane")
            classes = params["disallowed"]
            if not isinstance(classes, list) or len(classes) != len(set(classes)):
                raise ValueError("disallowed must be a unique class list")
            for cls in classes:
                if string(cls, "vehicle class") not in CLASSES:
                    raise ValueError(f"unsupported SUMO vehicle class: {cls}")
            if "at_sim_ns" in params:
                at = integer(params["at_sim_ns"], "at_sim_ns")
                if at < current or at % self.step_ns:
                    raise ValueError(
                        "restriction time must be future/current and aligned"
                    )
        if action == "tls_phase":
            tls = string(params["tls"], "tls")
            if tls not in self.c.trafficlight.getIDList():
                raise ValueError("unknown traffic light")
            phase = integer(params["phase"], "phase")
            program = self.c.trafficlight.getProgram(tls)
            logic = [
                x
                for x in self.c.trafficlight.getAllProgramLogics(tls)
                if x.programID == program
            ]
            if len(logic) != 1 or phase >= len(logic[0].phases):
                raise ValueError("phase is outside current native program")
            if (
                "duration_s" in params
                and number(params["duration_s"], "duration_s") <= 0
            ):
                raise ValueError("duration_s must be positive")
        if action == "add_vehicle":
            if string(params["route_id"], "route_id") not in self.c.route.getIDList():
                raise ValueError("unknown route")
            if (
                "type" in params
                and string(params["type"], "type") not in self.c.vehicletype.getIDList()
            ):
                raise ValueError("unknown vehicle type")
            if (
                "depart" in params
                and number(params["depart"], "depart") < current / SECOND
            ):
                raise ValueError("departure is in the past")

    def submit(self, payload, current_ns):
        # Envelope malformation is an RPC fault; business rejection is typed.
        fields(payload, ("command_id", "action", "params"))
        ident = string(payload["command_id"], "command_id")
        action = string(payload["action"], "action")
        if ident in self.ids:
            return {
                "command_id": ident,
                "status": "rejected",
                "reason": "duplicate command_id",
            }
        self.ids.add(ident)
        try:
            self.validate(action, payload["params"], current_ns)
        except (ValueError, TypeError) as error:
            return {"command_id": ident, "status": "rejected", "reason": str(error)}
        params = payload["params"].copy()
        due = params.get("at_sim_ns", current_ns)
        depart_ns = (
            int(Decimal(str(params["depart"])) * SECOND) if "depart" in params else due
        )
        deadline = max(due, depart_ns) + 30 * SECOND
        self.pending.append(Pending(ident, action, params, due, deadline))
        return {"command_id": ident, "status": "accepted", "due_sim_ns": due}

    @staticmethod
    def update(item, status, at, observation):
        return {
            "command_id": item.ident,
            "action": item.action,
            "status": status,
            "sim_ns": at,
            "observation": observation,
        }

    def apply(self, item, current):
        p, c = item.params, self.c
        action = item.action
        if action in ("set_speed", "reroute", "change_target", "remove_vehicle"):
            self.active(p["vehicle"])
        if action == "set_speed":
            c.vehicle.setSpeed(p["vehicle"], p["speed"])
        elif action == "reroute":
            item.expected["before_route"] = list(c.vehicle.getRoute(p["vehicle"]))
            if "edges" in p:
                c.vehicle.setRoute(p["vehicle"], p["edges"])
            else:
                c.vehicle.rerouteTraveltime(p["vehicle"], currentTravelTimes=True)
            item.expected["route"] = list(c.vehicle.getRoute(p["vehicle"]))
        elif action == "change_target":
            c.vehicle.changeTarget(p["vehicle"], p["edge"])
        elif action == "lane_restriction":
            before = sorted(c.lane.getDisallowed(p["lane"]))
            requested = sorted(set(before) | set(p["disallowed"]))
            c.lane.setDisallowed(p["lane"], requested)
            item.expected.update(before_disallowed=before, disallowed=requested)
        elif action == "tls_phase":
            c.trafficlight.setPhase(p["tls"], p["phase"])
            if "duration_s" in p:
                c.trafficlight.setPhaseDuration(p["tls"], p["duration_s"])
        elif action == "add_vehicle":
            kwargs = {"depart": str(p["depart"]) if "depart" in p else "now"}
            if "type" in p:
                kwargs["typeID"] = p["type"]
            c.vehicle.add(p["vehicle"], p["route_id"], **kwargs)
        elif action == "remove_vehicle":
            if p["vehicle"] in c.vehicle.getAllSubscriptionResults():
                c.vehicle.unsubscribe(p["vehicle"])
            c.vehicle.remove(p["vehicle"])
            self.removed.append(
                {"id": p["vehicle"], "kind": "vehicle", "sim_ns": current}
            )

    def before_step(self, current_ns):
        import traci.exceptions

        updates, keep = [], []
        for item in self.pending:
            if not item.applied and item.due <= current_ns:
                try:
                    self.apply(item, current_ns)
                    item.applied = True
                    updates.append(
                        self.update(
                            item,
                            "executing",
                            current_ns,
                            {"applied_at_sim_ns": current_ns},
                        )
                    )
                except (ValueError, traci.exceptions.TraCIException) as error:
                    updates.append(
                        self.update(item, "failed", current_ns, {"reason": str(error)})
                    )
                    continue
            keep.append(item)
        self.pending = keep
        return updates

    def observe(self, item, active):
        p, c, action = item.params, self.c, item.action
        if (
            action in ("set_speed", "reroute", "change_target")
            and p["vehicle"] not in active
        ):
            return False, {"reason": "vehicle no longer active"}, True
        if action == "set_speed":
            speed = c.vehicle.getSpeed(p["vehicle"])
            return (
                abs(speed - p["speed"]) <= 0.05,
                {"speed": speed, "requested_speed": p["speed"]},
                False,
            )
        if action == "reroute":
            route = list(c.vehicle.getRoute(p["vehicle"]))
            return (
                route == item.expected["route"],
                {
                    "route": route,
                    "expected_route": item.expected["route"],
                    "before_route": item.expected["before_route"],
                },
                False,
            )
        if action == "change_target":
            route = list(c.vehicle.getRoute(p["vehicle"]))
            return (
                bool(route) and route[-1] == p["edge"],
                {"route": route, "requested_target": p["edge"]},
                False,
            )
        if action == "lane_restriction":
            disallowed = sorted(c.lane.getDisallowed(p["lane"]))
            return (
                disallowed == item.expected["disallowed"],
                {
                    "lane": p["lane"],
                    "disallowed": disallowed,
                    "before_disallowed": item.expected["before_disallowed"],
                    "requested_disallowed": item.expected["disallowed"],
                },
                False,
            )
        if action == "tls_phase":
            phase = c.trafficlight.getPhase(p["tls"])
            return (
                phase == p["phase"],
                {
                    "tls": p["tls"],
                    "phase": phase,
                    "requested_phase": p["phase"],
                    "state": c.trafficlight.getRedYellowGreenState(p["tls"]),
                },
                False,
            )
        if action == "add_vehicle":
            present = p["vehicle"] in active
            observation = {"vehicle": p["vehicle"], "present": present}
            if present:
                observation["route"] = list(c.vehicle.getRoute(p["vehicle"]))
                observation["type"] = c.vehicle.getTypeID(p["vehicle"])
            return present, observation, False
        if action == "remove_vehicle":
            present = p["vehicle"] in active
            return not present, {"vehicle": p["vehicle"], "present": present}, False
        raise RuntimeError(f"unhandled accepted action: {action}")

    def after_step(self, sim_ns, events):
        import traci.exceptions

        updates, keep = [], []
        active = set(self.c.vehicle.getIDList())
        for item in self.pending:
            if not item.applied:
                keep.append(item)
                continue
            try:
                done, observation, failed = self.observe(item, active)
            except traci.exceptions.TraCIException as error:
                done, observation, failed = False, {"reason": str(error)}, True
            if done:
                updates.append(self.update(item, "succeeded", sim_ns, observation))
            elif failed or sim_ns >= item.deadline:
                if not failed:
                    observation["reason"] = "observed effect deadline exceeded"
                updates.append(self.update(item, "failed", sim_ns, observation))
            else:
                keep.append(item)
        self.pending = keep
        return updates

    def take_removed(self):
        result, self.removed = self.removed, []
        return result
