"""Small flood-rescue scheduler using straight-line, constant-speed travel.

SOC is an eligibility threshold, not an energy prediction. Relay duration is
setup; its UAV stays occupied until mission end, extended by runtime until ack.
"""

from __future__ import annotations

from itertools import permutations, product
from math import dist, inf, isfinite


def solve_plan(vehicles: list[dict], bids: list[dict], graph: dict, now_s: float) -> dict:
    """Enumerate assignments and orders for the small, schema-validated graph.

    Departures wait for commitments and dependencies, with relay setup as its
    readiness milestone. Assignment times are absolute simulation seconds;
    objective_s is elapsed makespan. This is not a general-purpose PDDL planner.
    """
    evaluated = 0

    def infeasible(reason: str) -> dict:
        return {"status": "infeasible", "assignments": [], "objective_s": None,
                "evaluated": evaluated, "reason": reason}

    actions = graph["actions"]
    ids = [action["id"] for action in actions]
    if not actions or len(set(ids)) != len(ids):
        return infeasible("Actions must have unique IDs and cannot be empty.")
    for action in actions:
        if action["role"] not in {"inspect", "relay", "deliver"}:
            return infeasible("Unsupported rescue action role.")
        if any(parent not in ids for parent in action["after"]):
            return infeasible("Every dependency must name a supplied action.")
    deadline, payload = graph.get("deadline_s", inf), graph.get("payload_kg", 1.0)
    fleet = {vehicle["id"]: vehicle for vehicle in vehicles}
    busy = {identity: (vehicle.get("commitment") or {}).get("busy_until_s", now_s)
            for identity, vehicle in fleet.items()}
    availability: dict[tuple[str, str], float] = {}
    for bid in bids:
        for role in bid["roles"]:
            key = (bid["vehicle"], role)
            availability[key] = min(availability.get(key, inf), bid["available_after_s"])

    choices = []
    for action in actions:
        role = action["role"]
        candidates = [identity for identity, vehicle in fleet.items()
                      if role in vehicle["capabilities"] and vehicle["soc"] >= 0.2
                      and vehicle["speed_m_s"] > 0 and isfinite(busy[identity])
                      and isfinite(availability.get((identity, role), inf))
                      and (role != "deliver" or payload <= vehicle["payload_kg"] <= vehicle["payload_capacity_kg"])]
        if not candidates:
            return infeasible(f"No eligible bid for action {action['id']} ({role}).")
        choices.append(sorted(candidates))

    orders = [order for order in permutations(range(len(actions)))
              if all(order.index(ids.index(parent)) < order.index(index)
                     for index, action in enumerate(actions) for parent in action["after"])]
    if not orders:
        return infeasible("The rescue action graph contains a dependency cycle.")
    best = None
    best_end = inf
    for selected in product(*choices):
        for order in orders:
            evaluated += 1
            state = {identity: [max(now_s, busy[identity]), vehicle["position"], vehicle["payload_kg"]]
                     for identity, vehicle in fleet.items()}
            ready, held, schedule = {}, set(), []
            for index in order:
                action, identity = actions[index], selected[index]
                role, vehicle = action["role"], fleet[identity]
                if identity in held or role == "deliver" and state[identity][2] < payload:
                    break
                free_at, position, remaining = state[identity]
                departure = max(free_at, availability[(identity, role)],
                                max((ready[parent] for parent in action["after"]), default=now_s))
                start = departure + dist(position, action["target"]) / vehicle["speed_m_s"]
                end = start + action["duration_s"]
                ready[action["id"]] = end
                state[identity] = [end, action["target"], remaining - (payload if role == "deliver" else 0)]
                schedule.append({"action": action["id"], "vehicle": identity, "role": role,
                                 "departure_s": departure, "start_s": start, "end_s": end,
                                 "target": list(action["target"])})
                if role == "relay":
                    held.add(identity)
            else:
                finish = max(row["end_s"] for row in schedule)
                if finish <= deadline and finish < best_end:
                    for row in schedule:
                        if row["role"] == "relay":
                            row["end_s"] = finish
                    best, best_end = schedule, finish
    if best is None:
        return infeasible("No schedule satisfies dependencies, UAV occupancy and deadline.")
    return {"status": "feasible", "assignments": best, "objective_s": best_end - now_s,
            "evaluated": evaluated, "reason": "Minimum estimated makespan within the bounded rescue model."}
