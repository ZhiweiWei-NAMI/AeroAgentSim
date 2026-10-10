"""Allocation requests routed through the existing journaled LangGraph engine."""

from __future__ import annotations

import json
from typing import Any, TypedDict

from aeroagentsim.agents.langgraph_client import JournalClient

from .metadata import BUILTIN_COPY_ZH
from .protocols import Assignment, Snapshot


class LLMAllocation:
    label_zh, description_zh = BUILTIN_COPY_ZH["llm"]
    description = (
        "Ask the journaled LangGraph decision plugin; default replies are scripted."
    )

    def __init__(self, provider: dict[str, Any] | None = None) -> None:
        self.provider = provider

    def allocate(self, snapshot: Snapshot) -> tuple[Assignment, ...]:
        raise RuntimeError(
            "llm allocation requires the LangGraph request/result event adapter"
        )


class State(TypedDict):
    observation: dict[str, Any]
    trigger: dict[str, Any]
    outputs: list[dict[str, Any]]


def allocation_graph(client: JournalClient, options: dict[str, Any]) -> Any:
    from langgraph.graph import END, START, StateGraph

    async def choose(state: State) -> dict[str, Any]:
        payload = state["trigger"]["payload"]
        tasks = json.loads(payload["tasks_json"])
        vehicles = json.loads(payload["vehicles_json"])
        tasks.sort(key=lambda t: (-t["priority"], t["id"]))
        available = [v for v in vehicles if v["available"]]
        tasks = [
            t
            for t in tasks
            if any(
                (not v["task_kinds"] or t["kind"] in v["task_kinds"])
                and t["payload_kg"] <= v["payload_capacity_kg"]
                for v in available
            )
        ]
        if not tasks or not available:
            raise ValueError(
                "allocation request has no feasible task/vehicle candidates"
            )

        def validate(value: dict[str, Any]) -> None:
            if set(value) != {"task_rank", "vehicle_rank"} or any(
                type(x) is not int or x < 0 for x in value.values()
            ):
                raise ValueError(
                    "reply requires nonnegative task_rank and vehicle_rank integers"
                )
            if value["task_rank"] >= len(tasks):
                raise ValueError("task rank is outside the committed candidate list")
            task = tasks[value["task_rank"]]
            feasible = [
                v
                for v in available
                if v["soc"] > 0
                and (not v["task_kinds"] or task["kind"] in v["task_kinds"])
                and task["payload_kg"] <= v["payload_capacity_kg"]
            ]
            if value["vehicle_rank"] >= len(feasible):
                raise ValueError("vehicle rank is outside feasible candidates")

        reply = await client.ask_json(
            "allocation",
            "Choose one task and one feasible vehicle from the committed snapshot. Reply as JSON with task_rank and vehicle_rank, zero-based indices. Vehicles for each task are ordered by pickup distance, then id.",
            {"tasks": tasks, "vehicles": available},
            validate,
        )
        task = tasks[reply["task_rank"]]
        feasible = [
            v
            for v in available
            if v["soc"] > 0
            and (not v["task_kinds"] or task["kind"] in v["task_kinds"])
            and task["payload_kg"] <= v["payload_capacity_kg"]
        ]
        feasible.sort(
            key=lambda v: (
                sum((a - b) ** 2 for a, b in zip(v["position"], task["origin"])),
                v["id"],
            )
        )
        selected = [
            {"task": task["id"], "vehicle": feasible[reply["vehicle_rank"]]["id"]}
        ]
        return {
            "outputs": [
                {
                    "kind": "event",
                    "schema": "lowalt.allocation.result",
                    "topic": "lowalt.allocations",
                    "payload": {
                        "assignments_json": json.dumps(selected, sort_keys=True)
                    },
                }
            ]
        }

    graph = StateGraph(State)
    graph.add_node("allocate", choose)
    graph.add_edge(START, "allocate")
    graph.add_edge("allocate", END)
    return graph


def engine_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = {} if config is None else config
    if set(cfg) - {"provider"}:
        raise ValueError(
            "llm allocation supports only an explicit provider configuration"
        )
    # An authored finite script, not a substitute after a provider failure.
    reply = {
        "message": {"role": "assistant", "content": '{"task_rank":0,"vehicle_rank":0}'},
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    provider = (
        cfg["provider"]
        if "provider" in cfg
        else {
            "mode": "stub",
            "model": "lowalt-scripted-allocation/v1",
            "responses": {"allocation": [reply for _ in range(1000)]},
        }
    )
    return {
        "factory": "aeroagentsim.algorithms.llm:allocation_graph",
        "options": {},
        "triggers": [
            {"schema": "lowalt.allocation.request", "topic": "lowalt.allocations"}
        ],
        "grants": {
            "fields": [],
            "relations": [],
            "commands": [],
            "facts": [],
            "events": [
                {"schema": "lowalt.allocation.result", "topic": "lowalt.allocations"}
            ],
        },
        "budget": {
            "wall_timeout_s": 30.0,
            "max_calls": 1,
            "max_tokens": 1000,
            "max_retries": 0,
            "max_prompt_bytes": 200000,
            "recursion_limit": 4,
            "sim_timeout_ns": 1_000_000_000,
        },
        "provider": provider,
    }
