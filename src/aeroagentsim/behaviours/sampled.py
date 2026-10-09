"""Lower a predeclared finite role pool to Q6's existing sampled partitions."""

from __future__ import annotations

from typing import Any

from aeroagentsim.engines.predicate import FORMAT, VERSION, digest, prepare

from .bindings import stable_id
from .schema import CompileError


def expand_pool(document: dict[str, Any], packages: list[dict[str, Any]]) -> None:
    """Expand once at load; never create or rebind native contexts during execution."""
    engines = document["engines"]
    samples = document["bindings"].setdefault("samples", [])
    for package in packages:
        p = package["document"]
        for slot_index, slot in enumerate(p.get("sampled_contexts", [])):
            for predicate_id in slot["predicates"]:
                definition = p["predicates"][predicate_id]
                context_id = slot["id"] + "/" + predicate_id
                partition_id = stable_id([p["id"], context_id], "sampled")
                path = f"$.sampled_contexts[{slot_index}]"
                if any(s["context"] == context_id for s in samples):
                    # Loading a frozen resolved scenario must preserve its pool,
                    # rather than append a second producer for the same context.
                    matching = [s for s in samples if s["context"] == context_id]
                    if (
                        len(matching) != 1
                        or matching[0]["partition"] != partition_id
                        or any(
                            matching[0][key] != expected
                            for key, expected in (
                                ("bindings", slot["roles"]),
                                ("sources", slot["sources"]),
                                ("clocks", slot["clocks"]),
                                ("parameters", definition.get("parameters", {})),
                            )
                        )
                    ):
                        raise CompileError(
                            package["source"], path, "sample context identity collision"
                        )
                    continue
                if partition_id in engines:
                    raise CompileError(
                        package["source"], path, "sample partition identity collision"
                    )
                native = p.get("evaluator", {}).get("native_references")
                if not native:
                    raise CompileError(
                        package["source"],
                        path,
                        "finite pool requires pinned evaluator.native_references",
                    )
                definitions = {
                    predicate_id: {
                        "schemaVersion": FORMAT,
                        "kind": "predicate",
                        "execution": {
                            "nativeDialect": "original_compact_ast"
                            if p.get("evaluator", {}).get("dialect", "original")
                            == "original"
                            else "expanded_typed_ast"
                        },
                        "expression": definition["expression"],
                    }
                }
                config = {
                    "version": VERSION,
                    "definitions": definitions,
                    "definitions_sha256": digest(definitions),
                    "target": predicate_id,
                    "context": context_id,
                    "parameters": definition.get("parameters", {}),
                    "temporal_unit": "ns",
                    "event": definition["adapter"]["event"],
                    "topic": "aas.behaviour.native_sample",
                    "transition": "level",
                    "native_references": native,
                }
                try:
                    prepare(config)
                except (ValueError, TypeError, KeyError) as exc:
                    raise CompileError(package["source"], path, str(exc)) from exc
                engines[partition_id] = {"plugin": "predicate", "config": config}
                samples.append(
                    {
                        "context": context_id,
                        "partition": partition_id,
                        # The shared executor reads all its authored guards. Its
                        # upstream cone therefore spans ordinary scenario owners;
                        # kernel admission still checks cycles and the complete cone.
                        "upstream": sorted(
                            name
                            for name, item in engines.items()
                            if item["plugin"] != "predicate"
                        ),
                        "bindings": slot["roles"],
                        "sources": slot["sources"],
                        "clocks": slot["clocks"],
                        "parameters": definition.get("parameters", {}),
                    }
                )
