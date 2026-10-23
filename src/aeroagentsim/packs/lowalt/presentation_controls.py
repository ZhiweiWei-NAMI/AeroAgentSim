"""Pack-owned, read-only inverses of lowalt and rescue authoring controls."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from aeroagentsim.presentation_controls import Known, Unavailable, known


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def read_operator_mode(document: Mapping[str, Any]) -> Known | Unavailable:
    streams = document.get("ingress_streams", ())
    if not isinstance(streams, (list, tuple)):
        return Unavailable("Pinned operator ingress streams are not readable")
    operators = [row for row in streams if isinstance(row, Mapping) and row.get("id") == "operator"]
    until = _mapping(document.get("run")).get("until_ns")
    if len(operators) != 1 or type(until) is not int:
        return Unavailable("A unique operator stream and explicit run horizon are required")
    watermark = operators[0].get("initial_watermark_ns")
    if type(watermark) is not int:
        return Unavailable("The operator initial watermark is not explicitly pinned")
    return known(
        "interactive" if watermark < until else "scheduled",
        {"reader": "lowalt.operator_mode", "initial_watermark_ns": watermark, "until_ns": until},
    )


def _rescue_source(
    document: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any]] | Unavailable:
    sources = []
    for name, row in _mapping(document.get("engines")).items():
        row = _mapping(row)
        config = _mapping(row.get("config"))
        if row.get("plugin") == "lowalt_tasks" and "rescue" in config:
            if not isinstance(config["rescue"], Mapping):
                return Unavailable("Pinned rescue configuration is not a mapping")
            sources.append((name, config["rescue"]))
    if not sources:
        return Unavailable("No explicit pinned lowalt rescue configuration")
    if len(sources) != 1:
        return Unavailable("Multiple pinned rescue configurations are ambiguous")
    return sources[0]


def _rescue_choice(
    document: Mapping[str, Any], field: str, options: tuple[str, ...],
) -> Known | Unavailable:
    source = _rescue_source(document)
    if isinstance(source, Unavailable):
        return source
    owner, config = source
    value = config.get(field)
    if not isinstance(value, str) or value not in options:
        return Unavailable(f"Rescue {field} is not explicitly pinned to a supported value")
    return known(value, {"engine": owner, "plugin": "lowalt_tasks", "rescue": {field: value}})


def read_rescue_participation(document: Mapping[str, Any]) -> Known | Unavailable:
    source = _rescue_source(document)
    if isinstance(source, Unavailable):
        return source
    owner, config = source
    participation = _mapping(config.get("participation"))
    mode = participation.get("mode")
    if not isinstance(mode, str) or mode not in {"model", "filter", "all", "distance"}:
        return Unavailable("Rescue participation mode is not explicitly pinned")
    # Do not call participation_config: it silently supplies runtime defaults.
    return known(mode, {"engine": owner, "plugin": "lowalt_tasks", "participation": participation})


def read_rescue_observation(document: Mapping[str, Any]) -> Known | Unavailable:
    return _rescue_choice(document, "observation", ("positive", "negative", "uncertain"))


def _has_unique_binding(rows: Any, schema: str, member: str, expected: str) -> bool:
    if not isinstance(rows, (list, tuple)) or any(not isinstance(row, Mapping) for row in rows):
        return False
    matches = [row for row in rows if row.get("schema") == schema]
    return (len(matches) == 1 and set(matches[0]) == {"schema", member}
            and matches[0].get(member) == expected)


def _radio_source(
    document: Mapping[str, Any], owner: str, rescue: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]] | Unavailable:
    radio = rescue.get("radio")
    if not isinstance(radio, Mapping) or not radio:
        return Unavailable("No explicit rescue radio configuration; no radio port is effective")
    target = radio.get("target")
    if not isinstance(target, str) or not target:
        return Unavailable("Rescue radio target is not explicitly pinned")
    engines = _mapping(document.get("engines"))
    engine = _mapping(engines.get(target))
    if engine.get("plugin") != "ns3" or not isinstance(engine.get("config"), Mapping):
        return Unavailable(f"Rescue radio target for {owner!r} is not a pinned ns3 engine")
    config = engine["config"]
    event_topic = config.get("event_topic")
    if not isinstance(event_topic, str) or not event_topic:
        return Unavailable("The effective ns3 radio event topic is not explicitly pinned")

    # Tasks emits to this topic; the actual subscriber, not its historical engine
    # name or the authored radio copy, determines which native target is used.
    consumers = []
    for name, row in engines.items():
        row = _mapping(row)
        relay = _mapping(row.get("config"))
        triggers = relay.get("triggers")
        if row.get("plugin") == "langgraph" and isinstance(triggers, (list, tuple)) and any(
            isinstance(trigger, Mapping) and trigger.get("schema") == "rescue.transport.send"
            and trigger.get("topic") == "rescue.transport" for trigger in triggers
        ):
            consumers.append((name, relay))
    if len(consumers) != 1:
        return Unavailable("A unique pinned rescue transport send consumer is required")
    relay_id, relay = consumers[0]
    factory = "aeroagentsim.packs.lowalt.rescue_transport:relay_graph"
    if relay.get("factory") != factory:
        return Unavailable("The pinned rescue transport consumer is not the supported relay adapter")
    options = _mapping(relay.get("options"))
    if options.get("target") != target:
        return Unavailable("The pinned rescue relay target does not match the rescue radio binding")
    triggers = relay.get("triggers")
    if not all(_has_unique_binding(triggers, schema, "topic", topic) for schema, topic in (
        ("rescue.transport.send", "rescue.transport"),
        ("adapters.ns3.delivery", event_topic), ("adapters.ns3.drop", event_topic),
    )):
        return Unavailable("The pinned rescue relay send/result trigger chain is missing or ambiguous")
    grants = _mapping(relay.get("grants"))
    if not _has_unique_binding(grants.get("commands"), "adapters.ns3.send", "target", target):
        return Unavailable("The pinned rescue relay native send grant is missing or ambiguous")
    if not all(_has_unique_binding(grants.get("events"), "rescue.transport." + event, "topic", "rescue.results")
               for event in ("hop", "delivered", "failed")):
        return Unavailable("The pinned rescue relay result grants are missing or ambiguous")
    return target, config, {
        "engine": relay_id, "plugin": "langgraph", "factory": factory,
        "options_target": options["target"], "triggers": triggers, "grants": grants,
    }


def read_rescue_radio(document: Mapping[str, Any]) -> Known | Unavailable:
    source = _rescue_source(document)
    if isinstance(source, Unavailable):
        return source
    owner, rescue = source
    if "radio" not in rescue:
        # Absence is meaningful only inside the explicit rescue owner config:
        # its sender records communication failure rather than synthetic delivery.
        return known("off", {"engine": owner, "plugin": "lowalt_tasks", "rescue_radio_present": False})
    radio = _radio_source(document, owner, rescue)
    if isinstance(radio, Unavailable):
        return radio
    target, config, relay = radio
    return known("on", {
        "engine": owner, "rescue_radio": rescue["radio"], "relay": relay,
        "target": target, "target_plugin": "ns3", "target_config": config,
    })


def read_rescue_radio_port(document: Mapping[str, Any]) -> Known | Unavailable:
    source = _rescue_source(document)
    if isinstance(source, Unavailable):
        return source
    owner, rescue = source
    radio = _radio_source(document, owner, rescue)
    if isinstance(radio, Unavailable):
        return radio
    target, config, relay = radio
    port = config.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        return Unavailable("The effective ns3 radio port is not explicitly pinned")
    # Expansion copies rescue.radio.port into this actual runtime consumer. A
    # later editor may have changed the consumer; never report the stale copy.
    return known(port, {"engine": owner, "target": target, "plugin": "ns3", "port": port,
                        "event_topic": config["event_topic"], "relay": relay})


def read_rescue_deadline(document: Mapping[str, Any]) -> Known | Unavailable:
    source = _rescue_source(document)
    if isinstance(source, Unavailable):
        return source
    owner, config = source
    deadline = config.get("deadline_s")
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        return Unavailable("Rescue deadline_s is not explicitly pinned as a finite number")
    return known(deadline, {"engine": owner, "plugin": "lowalt_tasks", "deadline_s": deadline})


def read_rescue_fleet_variation(document: Mapping[str, Any]) -> Known | Unavailable:
    # Read the pinned mode, never re-sample or infer it from the resulting fleet.
    return _rescue_choice(document, "fleet_variation", ("authored", "seeded"))


CONTROL_READERS = {
    "lowalt.operator_mode": read_operator_mode,
    "rescue.participation": read_rescue_participation,
    "rescue.observation": read_rescue_observation,
    "rescue.radio_port": read_rescue_radio_port,
    "rescue.radio": read_rescue_radio,
    "rescue.deadline_s": read_rescue_deadline,
    "rescue.fleet_variation": read_rescue_fleet_variation,
}


__all__ = ["CONTROL_READERS"]
