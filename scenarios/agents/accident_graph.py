"""Accident decision compatibility graph; motion and task ownership stay external.

Prompt provenance: READ-ONLY AeroBench traffic_accident/runtime.py at
1044–1049 (report), 1133–1136 (bid), 1207–1209 (award), 1262–1275
(user observation prefix). Graph topology: 429–441. No legacy fallback is ported.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from aeroagentsim.agents.langgraph_client import JournalClient
from aeroagentsim.agents.provider import ProviderError

REPORT_PROMPT = (
    "你是车端事故报告智能体。根据实时观测上报事故，并从reroute_candidates选择真实可用路线。"
    "选择adjacent_lane_bypass，明确临时借道后回原车道；实际车辆仅在运行时确认空隙后出发，"
    "不等待边缘或无人机流程。只输出JSON："
    '{"decision":"report","reason":"一句公开依据","action":"上报动作",'
    '"departure":"reroute_when_clear","route_id":"adjacent_lane_bypass",'
    '"route_reason":"一句公开路线选择依据"}。'
)
BID_PROMPT = (
    "你是候选无人机。根据自己的实时位置、电量和当前任务独立投标。"
    "当前任务interruptible=false时必须拒绝；可中断且航程可行时可接受。"
    '只输出JSON：{"accept":true或false,"reason":"一句公开依据",'
    '"action":"一句动作","alt_target_m":70}。'
)
AWARD_PROMPT = (
    "你是区域边缘协调智能体。依据有效标书和实时ETA确定唯一获胜者。"
    '只输出JSON：{"winner_id":"候选id","reason":"一句公开依据",'
    '"action":"派遣动作"}。winner_id只能来自eligible标书。'
)


def merge_bids(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return sorted(left + right, key=lambda bid: bid["uav_id"])


class GraphState(TypedDict):
    observation: dict[str, Any]
    trigger: dict[str, Any]
    report: dict[str, Any]
    broadcast: dict[str, Any]
    bids: Annotated[list[dict[str, Any]], merge_bids]
    winner_id: str
    outputs: list[dict[str, Any]]


def demo_snapshot(state: GraphState, options: dict[str, Any]) -> dict[str, Any]:
    """Resolve current-task references from granted, committed facts, never fixtures."""
    rows = state["observation"]["fields"]

    def field(identity: str, name: str) -> Any:
        matches = [
            r for r in rows if r["entity"]["id"] == identity and r["field"] == name
        ]
        if len(matches) != 1 or matches[0]["status"] != "known":
            raise ValueError(f"missing committed observation: {identity}.{name}")
        return matches[0]["value"]

    def position(value: list[float]) -> dict[str, float]:
        # The demo is ENU; the legacy prompt uses x/east, y/up, z/north.
        return dict(zip(("x", "z", "y"), value, strict=True))

    trigger = state["trigger"]
    incident = trigger["payload"]["incident"]
    incident_id = incident["$ref"]["id"]
    payload: dict[str, Any] = {
        **trigger["payload"],
        "time_s": int(state["observation"]["valid_at"]["ns"]) / 1e9,
        "incident_ref": incident,
        "incident": {
            "id": incident_id,
            "position": position(field(incident_id, "traffic.incident.position_enu_m")),
        },
        "region_radius_m": options["region_radius_m"],
        "uav_speed_mps": options["uav_speed_mps"],
    }
    if trigger["schema"] == "traffic.incident.detected":
        actor = trigger["payload"]["actor"]
        payload.update(
            actor_ref=actor,
            ego={
                "id": actor["$ref"]["id"],
                "position": position(
                    field(actor["$ref"]["id"], "traffic.road.position_enu_m")
                ),
            },
            reroute_candidates=options["reroute_candidates"],
        )
    elif trigger["schema"] == "traffic.broadcast":
        if field(options["task_id"], "traffic.task.capture_altitude_m") != 70:
            raise ValueError("committed capture altitude differs from prompt contract")
        uavs = []
        for identity in options["candidates"]:
            task = field(identity, "traffic.actor.current_task")["$ref"]["id"]
            uavs.append(
                {
                    "id": identity,
                    "position": position(field(identity, "he.aircraft.position_enu_m")),
                    "energy_j": field(identity, "traffic.uav.energy_j"),
                    "current_task": {
                        "id": task,
                        "kind": field(task, "traffic.task.kind"),
                        "phase": field(task, "traffic.task.phase"),
                        "interruptible": field(task, "traffic.task.interruptible"),
                    },
                }
            )
        payload["uavs"] = uavs
    else:
        raise ValueError("unsupported demo graph trigger")
    return {
        "trigger": {**trigger, "payload": payload},
        "report": {"incident": payload["incident"]},
    }


def public_strings(
    value: dict[str, Any], members: set[str], texts: tuple[str, ...]
) -> None:
    if set(value) != members:
        raise ValueError("unexpected or missing model JSON members")
    if any(not isinstance(value[key], str) or not value[key].strip() for key in texts):
        raise ValueError("public reasons/actions must be nonempty strings")


def validate_report(value: dict[str, Any]) -> None:
    public_strings(
        value,
        {"decision", "reason", "action", "departure", "route_id", "route_reason"},
        ("reason", "action", "route_reason"),
    )
    if (value["decision"], value["departure"], value["route_id"]) != (
        "report",
        "reroute_when_clear",
        "adjacent_lane_bypass",
    ):
        raise ValueError("report requires the available adjacent lane bypass")


def validate_bid(value: dict[str, Any]) -> None:
    public_strings(
        value, {"accept", "reason", "action", "alt_target_m"}, ("reason", "action")
    )
    if (
        type(value["accept"]) is not bool
        or type(value["alt_target_m"]) not in (int, float)
        or value["alt_target_m"] != 70
    ):
        raise ValueError("bid requires a Boolean acceptance and 70m altitude")


def build_graph(
    client: JournalClient, options: dict[str, Any]
) -> StateGraph[GraphState, None, GraphState, GraphState]:
    """Explicit snapshot inputs; parallel bids join before discretionary award."""
    graph = StateGraph(GraphState)
    proposals = options.get("mode") == "proposals"

    async def vehicle_report(state: GraphState) -> dict[str, Any]:
        payload = state["trigger"]["payload"]
        ego, incident = payload["ego"], payload["incident"]
        distance = math.hypot(
            ego["position"]["x"] - incident["position"]["x"],
            ego["position"]["z"] - incident["position"]["z"],
        )
        observation = {
            "time_s": payload["time_s"],
            "ego": ego,
            "incident": incident,
            "distance_m": round(distance, 1),
            "reroute_candidates": payload["reroute_candidates"],
        }
        response = await client.ask_json(
            "vehicle_report",
            REPORT_PROMPT,
            observation,
            validate_report,
            user_prefix="实时观测：",
        )
        result: dict[str, Any] = {
            "report": {
                "incident": incident,
                "reporter_position": ego["position"],
                "model": response,
            }
        }
        if proposals:
            result["outputs"] = [
                {
                    "kind": "event",
                    "schema": "traffic.proposal.report",
                    "topic": "traffic.proposal.report",
                    "payload": {
                        "actor": payload["actor_ref"],
                        "incident": payload["incident_ref"],
                        **response,
                    },
                }
            ]
        return result

    def edge_broadcast(state: GraphState) -> dict[str, Any]:
        payload = state["trigger"]["payload"]
        return {
            "broadcast": {
                "run_id": state["trigger"]["id"],
                "incident_position": state["report"]["incident"]["position"],
                "reported_at_s": payload["time_s"],
                "required_task": "到事故点70米上空拍摄并上传",
                "region_radius_m": payload["region_radius_m"],
            }
        }

    async def uav_bid(state: GraphState, identity: str, key: str) -> dict[str, Any]:
        payload = state["trigger"]["payload"]
        candidates = [uav for uav in payload["uavs"] if uav["id"] == identity]
        if len(candidates) != 1:
            raise ValueError("each candidate must have exactly one current snapshot")
        uav = candidates[0]
        interruptible = uav["current_task"]["interruptible"]
        if type(interruptible) is not bool:
            raise TypeError("interruptibility must be a Boolean")
        incident = payload["incident"]["position"]
        target = {**incident, "y": 70.0}
        distance = math.sqrt(
            sum((uav["position"][axis] - target[axis]) ** 2 for axis in ("x", "y", "z"))
        )
        speed = payload["uav_speed_mps"]
        if type(speed) not in (int, float) or not math.isfinite(speed) or speed <= 0:
            raise ValueError("positive finite measured/configured speed required")
        eta = distance / speed
        observation = {
            "time_s": payload["time_s"],
            "ego": {
                "id": identity,
                "position": uav["position"],
                **(
                    {"energy_j": uav["energy_j"]}
                    if proposals
                    else {"battery_pct": uav["battery_pct"]}
                ),
            },
            "current_task": uav["current_task"],
            "incident": {
                "position": incident,
                "distance_3d_m": round(distance, 1),
                "calculated_eta_s": round(eta, 1),
            },
            "broadcast": state["broadcast"],
        }

        def validate(value: dict[str, Any]) -> None:
            validate_bid(value)
            if proposals and type(value["alt_target_m"]) is not float:
                raise ValueError(
                    "typed proposal alt_target_m requires JSON floating-point 70.0; integer 70 is rejected"
                )
            if value["accept"] and not interruptible and not proposals:
                raise ValueError("noninterruptible current task cannot accept")

        if proposals:
            observation["typed_output_contract"] = {
                "alt_target_m": "必须输出JSON浮点数70.0，整数70不符合kernel number类型；不做类型转换"
            }
        response = await client.ask_json(
            key, BID_PROMPT, observation, validate, user_prefix="实时观测："
        )
        # A provider/validation failure aborts, never becomes a valid refusal.
        return {
            "bids": [
                {
                    "uav_id": identity,
                    "accept": response["accept"],
                    "eligible": response["accept"] and interruptible,
                    "model_ok": True,
                    "reason": response["reason"],
                    "action": response["action"],
                    "distance_m": round(distance, 1),
                    "eta_s": round(eta, 1),
                    "model": response,
                    **(
                        {"energy_j": uav["energy_j"]}
                        if proposals
                        else {"battery_pct": uav["battery_pct"]}
                    ),
                    "task_id": uav["current_task"]["id"],
                    "interruptible": interruptible,
                    "position": uav["position"],
                }
            ]
        }

    async def uav_alpha(state: GraphState) -> dict[str, Any]:
        return await uav_bid(state, "uav.alpha", "uav_alpha")

    async def uav_bravo(state: GraphState) -> dict[str, Any]:
        return await uav_bid(state, "uav.bravo", "uav_bravo")

    async def edge_award(state: GraphState) -> dict[str, Any]:
        bids = state["bids"]
        eligible = sorted(
            (bid for bid in bids if bid["eligible"]),
            key=lambda bid: (bid["eta_s"], bid["uav_id"]),
        )
        if not eligible:
            raise ProviderError(
                "NO_ELIGIBLE_BID", "no eligible current bid; no award produced"
            )
        eligible_ids = {bid["uav_id"] for bid in eligible}

        def validate(value: dict[str, Any]) -> None:
            public_strings(
                value,
                {"winner_id", "reason", "action"},
                ("winner_id", "reason", "action"),
            )
            if value["winner_id"] not in eligible_ids:
                raise ValueError("award winner must come from eligible bids")

        observation = {
            "time_s": state["trigger"]["payload"]["time_s"],
            "bids": bids,
            "eligible_ranked_by_actual_eta": eligible,
            "rule": "winner_id必须来自eligible标书，并优先实际ETA最短者",
        }
        response = await client.ask_json(
            "edge_award", AWARD_PROMPT, observation, validate, user_prefix="实时观测："
        )
        return {
            "winner_id": response["winner_id"],
            "outputs": [
                {
                    "kind": "event",
                    "schema": options["award_schema"],
                    "topic": options["award_topic"],
                    "payload": response,
                }
            ],
        }

    for name, node in (
        ("vehicle_report", vehicle_report),
        ("edge_broadcast", edge_broadcast),
        ("uav_alpha", uav_alpha),
        ("uav_bravo", uav_bravo),
        ("edge_award", edge_award),
    ):
        graph.add_node(name, node)
    if proposals:
        graph.add_node("snapshot", lambda state: demo_snapshot(state, options))

        def publish_bids(state: GraphState) -> dict[str, Any]:
            return {
                "outputs": [
                    {
                        "kind": "event",
                        "schema": "traffic.proposal.bid",
                        "topic": "traffic.proposal.bid",
                        "payload": {
                            "actor": options["candidate_refs"][bid["uav_id"]],
                            "task": options["task_ref"],
                            **bid["model"],
                            "input_cut": state["observation"]["known_at"]["index"],
                            "decision_status": "succeeded",
                        },
                    }
                    for bid in state["bids"]
                ]
            }

        graph.add_node("publish_bids", publish_bids)
        graph.add_edge(START, "snapshot")
        graph.add_conditional_edges(
            "snapshot",
            lambda state: (
                "vehicle_report"
                if state["trigger"]["schema"] == "traffic.incident.detected"
                else "edge_broadcast"
            ),
        )
        graph.add_edge("vehicle_report", END)
        graph.add_edge(["uav_alpha", "uav_bravo"], "publish_bids")
        graph.add_edge("publish_bids", END)
    else:
        graph.add_edge(START, "vehicle_report")
        graph.add_edge("vehicle_report", "edge_broadcast")
        graph.add_edge(["uav_alpha", "uav_bravo"], "edge_award")
    graph.add_edge("edge_broadcast", "uav_alpha")
    graph.add_edge("edge_broadcast", "uav_bravo")
    graph.add_edge("edge_award", END)
    return graph
