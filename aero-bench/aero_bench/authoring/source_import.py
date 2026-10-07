"""Normalize a small official OSM API JSON extract for strict scene registration.

The OSM map endpoint includes ways crossing the bbox but may omit members of
relations that reach beyond it. Required geometric relations must be completed
with explicit ``--supplement`` snapshots; incomplete non-geometric relations are
reported and omitted. No way or node is silently fabricated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from aero_bench.authoring.selection import SceneSelectionError, _reject_duplicate_keys, _reject_non_json_constant
from aero_bench.world.scene_compiler import load_osm_json


def _document(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"),
                           object_pairs_hook=_reject_duplicate_keys,
                           parse_constant=_reject_non_json_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneSelectionError(f"OSM snapshot is not readable UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict) or value.get("version") not in ("0.6", 0.6):
        raise SceneSelectionError(f"OSM snapshot must declare version 0.6: {path}")
    if not isinstance(value.get("elements"), list):
        raise SceneSelectionError(f"OSM snapshot lacks elements: {path}")
    return value


def normalized_osm_api_source(primary: Path, supplements: tuple[Path, ...] = ()) -> tuple[bytes, dict]:
    """Return deterministic source bytes and a record of omitted relations."""

    document = _document(primary)
    elements = list(document["elements"])
    seen: dict[tuple[str, int], dict] = {}
    for path, entries in [(primary, elements), *((part, _document(part)["elements"]) for part in supplements)]:
        for item in entries:
            if not isinstance(item, dict) or item.get("type") not in ("node", "way", "relation") \
                    or not isinstance(item.get("id"), int) or isinstance(item["id"], bool):
                raise SceneSelectionError(f"OSM snapshot contains an invalid element: {path}")
            key = item["type"], item["id"]
            previous = seen.get(key)
            if previous is not None:
                if previous != item:
                    raise SceneSelectionError(f"conflicting OSM element snapshots: {key}")
                continue
            seen[key] = item
            if path != primary:
                elements.append(item)
    nodes = {identifier for kind, identifier in seen if kind == "node"}
    ways = {identifier for kind, identifier in seen if kind == "way"}
    relations = {item["id"]: item for item in elements if item["type"] == "relation"}
    for item in elements:
        if item["type"] == "way" and (not isinstance(item.get("nodes"), list)
                                       or any(node not in nodes for node in item["nodes"])):
            raise SceneSelectionError(f"way/{item['id']} has missing node references")
    retained = set(relations)
    while True:
        missing: set[int] = set()
        for identifier in retained:
            item = relations[identifier]
            members = item.get("members")
            if not isinstance(members, list):
                raise SceneSelectionError(f"relation/{identifier} lacks members")
            for member in members:
                if not isinstance(member, dict) or member.get("type") not in ("node", "way", "relation") \
                        or not isinstance(member.get("ref"), int):
                    raise SceneSelectionError(f"relation/{identifier} has invalid member references")
                targets = {"node": nodes, "way": ways, "relation": retained}[member["type"]]
                if member["ref"] not in targets:
                    missing.add(identifier)
                    break
        if not missing:
            break
        retained.difference_update(missing)
    omitted = [item for identifier, item in relations.items() if identifier not in retained]
    for item in omitted:
        tags = item.get("tags") or {}
        if not isinstance(tags, dict):
            raise SceneSelectionError(f"relation/{item['id']} has invalid tags")
        if ("building" in tags or "building:part" in tags
                or tags.get("type") in {"multipolygon", "restriction"}):
            raise SceneSelectionError(
                f"relation/{item['id']} is incomplete geometric/traffic evidence; fetch its missing members")
    result = dict(document)
    result["version"] = 0.6
    result["elements"] = [item for item in elements if item["type"] != "relation" or item["id"] in retained]
    raw = (json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    report = {
        "schema_version": "aero-bench.osm-api-normalization/v1",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_size_bytes": len(raw),
        "source_element_counts": dict(sorted(Counter(item["type"] for item in result["elements"]).items())),
        "omitted_relation_ids": sorted(item["id"] for item in omitted),
        "omitted_relation_types": dict(sorted(Counter((item.get("tags") or {}).get("type", "")
                                                    for item in omitted).items())),
    }
    return raw, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize official OSM API JSON for city source registration")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.report.exists():
        parser.error("output and report paths must be new files")
    raw, report = normalized_osm_api_source(args.input, tuple(args.supplement))
    args.output.write_bytes(raw)
    try:
        load_osm_json(args.output)
    except Exception:
        args.output.unlink()
        raise
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report["source_sha256"])


if __name__ == "__main__":
    main()
