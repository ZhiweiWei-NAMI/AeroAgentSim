"""Bounded flood-rescue scheduler with optional received-only feasible leg costs.

Legacy authored scenes retain straight-line, constant-speed travel.

SOC is an eligibility threshold, not an energy prediction. Relay duration is
setup; its UAV stays occupied until mission end, extended by runtime until ack.
"""

from __future__ import annotations

from itertools import permutations, product
from math import dist, inf, isfinite


def solve_plan(vehicles: list[dict], bids: list[dict], graph: dict, now_s: float, *, leg_costs: dict | None = None) -> dict:
    """Enumerate assignments and orders for the small, schema-validated graph.

    Departures wait for commitments and dependencies, with relay setup as its
    readiness milestone. Assignment times are absolute simulation seconds;
    objective_s is elapsed makespan. This is not a general-purpose PDDL planner.
    """
    # Native companions supply measured/planned local leg costs. The legacy
    # authored profile retains its original constant-speed estimate.
    evaluated = 0

    def infeasible(reason: str) -> dict:
        return {"status": "infeasible", "assignments": [], "objective_s": None,
                "evaluated": evaluated, "reason": reason}

    actions = graph["actions"]
    ids = [action["id"] for action in actions]
    if not actions:
        return {"status": "feasible", "assignments": [], "objective_s": 0.0,
                "evaluated": 0, "reason": "No actions required by the resolved evidence."}
    if len(set(ids)) != len(ids):
        return infeasible("Actions must have unique IDs.")
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

    indices = {identity: index for index, identity in enumerate(ids)}
    parents = [tuple(indices[parent] for parent in action["after"]) for action in actions]
    orders = []
    for order in permutations(range(len(actions))):
        rank = [0] * len(actions)
        for offset, index in enumerate(order):
            rank[index] = offset
        if all(rank[parent] < rank[index] for index, dependencies in enumerate(parents) for parent in dependencies):
            orders.append(order)
    if not orders:
        return infeasible("The rescue action graph contains a dependency cycle.")
    # Reuse immutable initial state; candidates only replace the selected rows.
    # Keep enumeration, arithmetic and tie order unchanged for research labels.
    initial = {identity: (max(now_s, busy[identity]), vehicle["position"], vehicle["payload_kg"])
               for identity, vehicle in fleet.items()}
    best = None
    best_end = inf
    for selected in product(*choices):
        for order in orders:
            evaluated += 1
            state = initial.copy()
            previous = {identity: "current" for identity in fleet}
            remaining_energy = {identity: vehicle.get("available_energy_j") for identity, vehicle in fleet.items()}
            ready, held, schedule = [now_s] * len(actions), set(), []
            for index in order:
                action, identity = actions[index], selected[index]
                role, vehicle = action["role"], fleet[identity]
                if identity in held or role == "deliver" and state[identity][2] < payload:
                    break
                free_at, position, remaining = state[identity]
                dependency = max(ready[parent] for parent in parents[index]) if parents[index] else now_s
                departure = max(free_at, availability[(identity, role)], dependency)
                if leg_costs is None:
                    travel_s = dist(position, action["target"]) / vehicle["speed_m_s"]
                else:
                    leg = leg_costs.get((identity, previous[identity], action["id"]))
                    if leg is None or leg.get("travel_s") is None or leg.get("energy_j") is None:
                        break
                    travel_s = leg["travel_s"]
                    if not isfinite(travel_s) or travel_s < 0 or not isfinite(leg["energy_j"]) or leg["energy_j"] < 0:
                        break
                    budget = remaining_energy[identity]
                    if budget is not None:
                        if not isfinite(budget) or leg["energy_j"] > budget:
                            break
                        remaining_energy[identity] = budget - leg["energy_j"]
                start = departure + travel_s
                previous[identity] = action["id"]
                end = start + action["duration_s"]
                ready[index] = end
                state[identity] = (end, action["target"], remaining - (payload if role == "deliver" else 0))
                schedule.append((index, identity, departure, start, end))
                if role == "relay":
                    held.add(identity)
            else:
                finish = max(ready)
                if finish <= deadline and finish < best_end:
                    best = [{"action": actions[index]["id"], "vehicle": identity, "role": actions[index]["role"],
                             "departure_s": departure, "start_s": start,
                             "end_s": finish if actions[index]["role"] == "relay" else end,
                             "target": list(actions[index]["target"])}
                            for index, identity, departure, start, end in schedule]
                    best_end = finish
    if best is None:
        return infeasible("No schedule satisfies dependencies, UAV occupancy and deadline.")
    return {"status": "feasible", "assignments": best, "objective_s": best_end - now_s,
            "evaluated": evaluated, "reason": "Minimum estimated makespan within the bounded rescue model."}
