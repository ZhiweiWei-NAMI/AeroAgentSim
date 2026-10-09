"""Regenerate explicitly authored E1 scenarios; no live ontology builds."""

from pathlib import Path
from typing import Any

import yaml
from aerokernel import MemoryRegistry
from aerokernel.values import thaw

from aeroagentsim.adapters.schemas import command_descriptors, event_descriptors
from aeroagentsim.integrations.aerograph.model import CompiledRegistry

ROOT = Path(__file__).parent
SECOND = 1_000_000_000


def record(members: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "record",
        "members": members,
        "required": list(members),
        "extra": False,
    }


def main() -> None:
    snapshot = CompiledRegistry(
        MemoryRegistry(()),
        {
            "relations": [],
            "provenance": {"author": "E1 explicitly authored backend demonstrations"},
            "normalizations": [],
            "exclusions": [],
            "producer_hints": {},
            "statistics": {},
            "effective_fields": {},
        },
    )
    snapshot.write_snapshot(ROOT / "authored-registry.json")
    scalar = {"type": "number"}
    string = {"type": "string"}
    vector3 = {"type": "vector", "items": scalar, "length": 3}
    px4_fields = {
        "position": vector3,
        "velocity": vector3,
        "attitude": {"type": "vector", "items": scalar, "length": 4},
        "battery": record({"remaining_fraction": scalar, "voltage_v": scalar}),
        "armed": {"type": "boolean"},
        "mode": string,
        "landed": string,
    }
    # All telemetry IDs are authored examples; these are not substituted values.
    px4_config: dict[str, Any] = {
        "image": "aeroagentsim/px4-gazebo:dev-p2b",
        "host": "127.0.0.1",
        "port": 19001,
        "step_ns": 200_000_000,
        "clock_id": "px4.observation",
        "pose_clock_id": "gazebo.simulation",
        "pose_mapping_id": "gazebo.warmup/v1",
        "mapping_id": "px4.elapsed/v1",
        "event_topic": "native-events",
        "vehicles": {"aircraft": "u1"},
        "fields": {name: "e1.px4." + name for name in px4_fields},
        "reset": {
            "world": "default",
            "warmup": 10 * SECOND,
            "vehicles": [{"id": "u1", "model": "x500", "spawn": [0, 0, 0.2, 0, 0, 0]}],
        },
    }
    sumo_types = {
        "vehicle": "e1:RoadVehicle",
        "person": "e1:Person",
        "tls": "e1:TrafficLight",
    }
    sumo_schemas = {
        "vehicle": {
            "position": record(
                {"xy": {"type": "vector", "items": scalar, "length": 2}}
            ),
            "speed": scalar,
            "route": {"type": "array", "items": string},
        },
        "person": {
            "position": record(
                {"xy": {"type": "vector", "items": scalar, "length": 2}}
            ),
            "speed": scalar,
        },
        "tls": {"state": string, "phase": {"type": "integer"}, "program": string},
    }
    sumo_config: dict[str, Any] = {
        "image": "aeroagentsim/sumo:dev-p3a-5",
        "host": "127.0.0.1",
        "port": 19002,
        "step_ns": 100_000_000,
        "clock_id": "sumo.simulation",
        "mapping_id": "sumo.elapsed/v1",
        "event_topic": "native-events",
        "kinds": {
            kind: {
                "type_id": type_id,
                "prefix": kind + "/",
                "fields": {
                    name: f"e1.sumo.{kind}.{name}" for name in sumo_schemas[kind]
                },
            }
            for kind, type_id in sumo_types.items()
        },
        "reset": {
            "scenario": {
                "kind": "grid",
                "vehicles": 50,
                "persons": 3,
                "depart_interval_s": 0.1,
            }
        },
    }
    ns3_config: dict[str, Any] = {
        "image": "aeroagentsim/ns3:dev-p4b",
        "host": "127.0.0.1",
        "port": 19003,
        "step_ns": 200_000_000,
        "clock_id": "ns3.simulation",
        "mapping_id": "ns3.elapsed/v1",
        "event_topic": "native-events",
        "consumer_lag_ns": 200_000_000,
        "mobility": [{"entity": "aircraft", "field": "e1.px4.position", "node": "uav"}],
        "reset": {
            "nodes": [
                {"id": "uav", "position_enu": [0, 0, 0.2]},
                {"id": "ground", "position_enu": [20, 0, 0]},
            ]
        },
    }
    for name, backends, until in (
        ("px4-flight", ("px4_gazebo",), 100 * SECOND),
        ("sumo-grid", ("sumo",), 60 * SECOND),
        ("coupled", ("px4_gazebo", "sumo", "ns3"), 30 * SECOND),
    ):
        types: list[dict[str, Any]]
        fields: list[dict[str, Any]]
        messages: list[dict[str, Any]]
        entities: list[dict[str, Any]]
        rules: list[dict[str, Any]]
        lifecycle: list[dict[str, Any]]
        engines: dict[str, Any]
        mappings: list[dict[str, Any]]
        types, fields, messages, entities, rules, lifecycle, engines, mappings = (
            [],
            [],
            [],
            [],
            [],
            [],
            {},
            [{"clock_id": "canonical", "mapping_id": "canonical"}],
        )
        for backend in backends:
            for descriptor in command_descriptors(backend) + event_descriptors(backend):
                item = {
                    "id": descriptor.id,
                    "kind": descriptor.kind,
                    "schema": thaw(descriptor.schema),
                }
                if descriptor.result_schema is not None:
                    item["result_schema"] = thaw(descriptor.result_schema)
                messages.append(item)
            config = {"px4_gazebo": px4_config, "sumo": sumo_config, "ns3": ns3_config}[
                backend
            ]
            engine_id = {"px4_gazebo": "flight", "sumo": "traffic", "ns3": "radio"}[
                backend
            ]
            engines[engine_id] = {"plugin": backend, "config": config}
            mappings.append(
                {"clock_id": config["clock_id"], "mapping_id": config["mapping_id"]}
            )
            if backend == "px4_gazebo":
                mappings.append(
                    {
                        "clock_id": "gazebo.simulation",
                        "mapping_id": "gazebo.warmup/v1",
                        "offset_ns": -10 * SECOND,
                    }
                )
                types.append({"id": "e1:Aircraft", "parents": [], "abstract": False})
                entities.append({"id": "aircraft", "type": "e1:Aircraft", "facts": {}})
                for semantic, schema in px4_fields.items():
                    metadata = {
                        "role": "observation",
                        "time_semantics": "backend boundary observation; MAVSDK source acquisition unavailable",
                    }
                    if semantic in ("position", "velocity", "attitude"):
                        metadata["frame"] = "enu"
                    fields.append(
                        {
                            "id": "e1.px4." + semantic,
                            "type": "e1:Aircraft",
                            "schema": schema,
                            "metadata": metadata,
                        }
                    )
                rules.append(
                    {
                        "writer": engine_id,
                        "type": "e1:Aircraft",
                        "fields": list(px4_config["fields"].values()),
                    }
                )
                lifecycle.append({"controller": engine_id, "type": "e1:Aircraft"})
            if backend == "sumo":
                for kind, type_id in sumo_types.items():
                    types.append({"id": type_id, "parents": [], "abstract": False})
                    for semantic, schema in sumo_schemas[kind].items():
                        fields.append(
                            {
                                "id": f"e1.sumo.{kind}.{semantic}",
                                "type": type_id,
                                "schema": schema,
                                "metadata": {
                                    "role": "observation",
                                    "frame": "sumo-network-xy"
                                    if semantic == "position"
                                    else "scalar",
                                },
                            }
                        )
                    rules.append(
                        {
                            "writer": engine_id,
                            "type": type_id,
                            "fields": list(
                                sumo_config["kinds"][kind]["fields"].values()
                            ),
                            "ids": kind + "/*",
                        }
                    )
                    lifecycle.append(
                        {"controller": engine_id, "type": type_id, "ids": kind + "/*"}
                    )
        commands = []
        if "sumo" in backends:
            commands.append(
                {
                    "schema": "adapters.sumo.reroute",
                    "target": "traffic",
                    "at_ns": 5 * SECOND,
                    "payload": {"vehicle": "v0"},
                }
            )
        if "ns3" in backends:
            commands.extend(
                {
                    "schema": "adapters.ns3.send",
                    "target": "radio",
                    "at_ns": i * SECOND,
                    "payload": {
                        "packet_id": f"p{i}",
                        "src": "uav",
                        "dst": "ground",
                        "size": 256,
                        "payload_ref": f"measurement/{i}",
                        "lifetime_ns": SECOND,
                    },
                }
                for i in range(1, 30)
            )
        document = {
            "format": "aeroagentsim.scenario/v1",
            "id": "e1-" + name,
            "registry": {
                "snapshot": "authored-registry.json",
                "types": types,
                "fields": fields,
                "messages": messages,
            },
            "entities": entities,
            "bindings": {"rules": rules, "lifecycle": lifecycle, "commands": commands},
            "engines": engines,
            "clock_mappings": mappings,
            "run": {
                "seed": 42,
                "until_ns": until,
                "advance_ns": SECOND,
                "pacing": "fast",
            },
            "outputs": {"durability": "flush"},
        }
        (ROOT / f"{name}.yaml").write_text(yaml.safe_dump(document, sort_keys=False))


if __name__ == "__main__":
    main()
