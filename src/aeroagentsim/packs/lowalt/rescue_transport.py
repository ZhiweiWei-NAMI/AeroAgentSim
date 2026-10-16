"""Application relay: the second native UDP hop waits for the first delivery."""
from __future__ import annotations

import json
from typing import Any


def expand_radio(document: dict, cfg: dict) -> None:
    from aeroagentsim.adapters.schemas import backend_registry
    radio = cfg["radio"]
    known = {m["id"] for m in document["registry"]["messages"]}
    document["registry"]["messages"].extend(m for m in backend_registry("ns3").to_data()["messages"] if m["id"] not in known)
    entities = {row["id"]: row for row in document["entities"]}
    nodes = [{"id": identity, "position_enu": entities[identity]["facts"]["lowalt.position"]} for identity in cfg["vehicles"]]
    nodes.append({"id": radio["center"], "position_enu": entities[radio["center"]]["facts"]["lowalt.site_position"]})
    document["engines"][radio["target"]] = {"plugin": "ns3", "config": {
        "host": radio["host"], "port": radio["port"], "step_ns": 100_000_000,
        "clock_id": "ns3.simulation", "mapping_id": "ns3.elapsed/v1", "event_topic": radio["event_topic"],
        "consumer_lag_ns": 100_000_000,
        "mobility": [{"entity": identity, "field": "lowalt.position", "node": identity} for identity in cfg["vehicles"]],
        "reset": {"nodes": nodes, **radio.get("reset", {})}}}
    document["clock_mappings"] = [{"clock_id": "canonical", "mapping_id": "canonical"},
                                  {"clock_id": "ns3.simulation", "mapping_id": "ns3.elapsed/v1"}]


def engine_config(provider: dict, radio: dict) -> dict:
    from .rescue import engine_config as base_config
    cfg = base_config(provider, "center")
    cfg.update(factory="aeroagentsim.packs.lowalt.rescue_transport:relay_graph", options=radio,
               triggers=[{"schema": "rescue.transport.send", "topic": "rescue.transport"},
                         *[{"schema": "adapters.ns3." + name, "topic": radio["event_topic"]} for name in ("delivery", "drop")]])
    cfg["grants"]["commands"] = [{"schema": "adapters.ns3.send", "target": radio["target"]}]
    cfg["grants"]["events"] = [{"schema": "rescue.transport." + name, "topic": "rescue.results"} for name in ("delivered", "failed")]
    return cfg


def relay_graph(client: Any, options: dict) -> Any:
    """A deterministic graph with no model calls and no synthetic delivery path."""
    from langgraph.graph import END, START, StateGraph

    def transfer(state: dict) -> dict:
        schema = state["trigger"]["schema"]
        payload = state["trigger"]["payload"]
        if schema == "adapters.ns3.drop":
            if "/rescue/" not in payload["packet_id"]:
                return {"outputs": []}
            event, suffix = payload["packet_id"].split("/rescue/", 1)
            return event_output("failed", {"event": event, "kind": suffix.split("/")[0], "reason": payload["reason"], "packet_id": payload["packet_id"]})
        if schema == "rescue.transport.send":
            envelope, hop = json.loads(payload["data_json"]), 0
        else:
            if not payload["payload_ref"].startswith("rescue:"):
                return {"outputs": []}
            envelope = json.loads(payload["payload_ref"][7:])
            hop = envelope.pop("hop") + 1
            if hop == (1 if envelope["source"] == envelope["relay"] else 2):
                return event_output("delivered", {**envelope, "received_ns": payload["received_ns"], "available_ns": payload["available_ns"]})
        route = [envelope["source"], envelope["relay"], envelope["destination"]]
        if route[0] == route[1]:
            route.pop(0)
        encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        return {"outputs": [{"kind": "command", "schema": "adapters.ns3.send", "target": options["target"], "payload": {
            "packet_id": f"{envelope['event']}/rescue/{envelope['kind']}/{hop}", "src": route[hop], "dst": route[hop + 1],
            "size": len(encoded.encode("utf-8")), "payload_ref": "rescue:" + json.dumps({**envelope, "hop": hop}),
            "lifetime_ns": options.get("lifetime_ns", 3_000_000_000)}}]}

    def event_output(kind: str, envelope: dict) -> dict:
        return {"outputs": [{"kind": "event", "schema": "rescue.transport." + kind, "topic": "rescue.results", "payload": {"data_json": json.dumps(envelope)}}]}

    graph = StateGraph(dict)
    graph.add_node("transfer", transfer)
    graph.add_edge(START, "transfer")
    graph.add_edge("transfer", END)
    return graph
