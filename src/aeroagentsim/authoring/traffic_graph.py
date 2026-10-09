"""Traffic proposal graph; the behaviour owner still decides admission and awards."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from aeroagentsim.agents.langgraph_client import JournalClient


class State(TypedDict):
    observation: dict[str, Any]
    trigger: dict[str, Any]
    outputs: list[dict[str, Any]]


def build_graph(
    client: JournalClient, options: dict[str, Any]
) -> StateGraph[State, None, State, State]:
    graph = StateGraph(State)

    async def propose(state: State) -> dict[str, Any]:
        trigger, observation = state["trigger"], state["observation"]
        outputs: list[dict[str, Any]] = []
        if trigger["schema"] == "traffic.incident.detected":

            def report(value: dict[str, Any]) -> None:
                if (
                    set(value)
                    != {
                        "decision",
                        "reason",
                        "action",
                        "departure",
                        "route_id",
                        "route_reason",
                    }
                    or value["decision"] != "report"
                    or value["route_id"] != "adjacent_lane_bypass"
                    or value["departure"] != "reroute_when_clear"
                ):
                    raise ValueError(
                        "Report must select the actual adjacent_lane_bypass route"
                    )
                if any(
                    not isinstance(value[key], str) or not value[key] for key in value
                ):
                    raise ValueError("Report public fields must be nonempty strings")

            value = await client.ask_json(
                "vehicle_report",
                "Report the observed accident. Return JSON decision=report, reason, action, departure=reroute_when_clear, route_id=adjacent_lane_bypass, route_reason. Use the committed observation; propose no state edits.",
                {"trigger": trigger, "observation": observation},
                report,
            )
            payload = {
                "actor": trigger["payload"]["actor"],
                "incident": trigger["payload"]["incident"],
                **value,
            }
            outputs.append(
                {
                    "kind": "event",
                    "schema": "traffic.proposal.report",
                    "topic": "traffic.proposal.report",
                    "payload": payload,
                }
            )
        elif trigger["schema"] == "traffic.broadcast":
            for identity in options["candidates"]:
                actor_fields = [
                    row
                    for row in observation["fields"]
                    if row["entity"]["id"] == identity
                ]
                actor = actor_fields[0]["entity"]
                current = next(
                    row["value"]["$ref"]["id"]
                    for row in actor_fields
                    if row["field"] == "traffic.actor.current_task"
                    and row["status"] == "known"
                )
                task_fields = [
                    row
                    for row in observation["fields"]
                    if row["entity"]["id"] == current
                ]
                interruptible = next(
                    row["value"]
                    for row in task_fields
                    if row["field"] == "traffic.task.interruptible"
                    and row["status"] == "known"
                )

                altitude = next(
                    row["value"]
                    for row in observation["fields"]
                    if row["entity"]["id"] == options["task_id"]
                    and row["field"] == "traffic.task.capture_altitude_m"
                    and row["status"] == "known"
                )

                def bid(
                    value: dict[str, Any],
                    allowed: bool = interruptible,
                    expected_altitude: float = altitude,
                ) -> None:
                    if (
                        set(value) != {"accept", "reason", "action", "alt_target_m"}
                        or type(value["accept"]) is not bool
                        or type(value["alt_target_m"]) not in (int, float)
                    ):
                        raise ValueError(
                            "Bid requires typed accept/reason/action/alt_target_m"
                        )
                    if value["alt_target_m"] != expected_altitude:
                        raise ValueError(
                            "Bid altitude differs from the authored capture task"
                        )
                    if not allowed and value["accept"]:
                        raise ValueError("Current task is not interruptible")
                    if any(
                        not isinstance(value[key], str) or not value[key]
                        for key in ("reason", "action")
                    ):
                        raise ValueError("Bid requires public reasons/actions")

                value = await client.ask_json(
                    identity,
                    "Propose a UAV bid using this actor's committed state and current task. Refuse when interruptible=false. Return JSON accept:boolean, reason:string, action:string, alt_target_m:number. Match the capture task's authored altitude.",
                    {
                        "actor": actor_fields,
                        "current_task": task_fields,
                        "capture_task": [
                            row
                            for row in observation["fields"]
                            if row["entity"]["id"] == options["task_id"]
                        ],
                    },
                    bid,
                )
                # The output descriptor declares a strict floating quantity.
                value["alt_target_m"] = float(value["alt_target_m"])
                task = next(
                    row["entity"]
                    for row in observation["fields"]
                    if row["entity"]["id"] == options["task_id"]
                )
                outputs.append(
                    {
                        "kind": "event",
                        "schema": "traffic.proposal.bid",
                        "topic": "traffic.proposal.bid",
                        "payload": {
                            "actor": {"$ref": actor},
                            "task": {"$ref": task},
                            **value,
                            "input_cut": observation["known_at"]["index"],
                            "decision_status": "succeeded",
                        },
                    }
                )
        else:
            raise ValueError("Unsupported traffic decision trigger")
        return {"outputs": outputs}

    graph.add_node("proposals", propose)
    graph.add_edge(START, "proposals")
    graph.add_edge("proposals", END)
    return graph
