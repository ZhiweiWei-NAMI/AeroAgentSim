"""Ground-level road scope report: structural exclusions and per-class connectivity.

Reads the source ground-filter report (structural exclusions made before network
generation) and a native SUMO network, and reports retained/excluded counts, the
exclusion reasons, name-only (ambiguous) exclusions, and directed connectivity per
vehicle class. It does not modify either input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

NAME_ONLY_PREFIXES = ("tunnel_name_heuristic", "elevated_name_heuristic")
CLASSES = ("passenger", "bicycle", "pedestrian")
OUTSIDE = "<outside-selection>"
BOUNDARY_TOLERANCE_M = 15.0


def _lane_allows(lane: ET.Element, vehicle_class: str) -> bool:
    allow, disallow = lane.get("allow"), lane.get("disallow")
    if allow is not None:
        return vehicle_class in allow.split()
    if disallow is not None:
        return vehicle_class not in disallow.split()
    return vehicle_class != "pedestrian"


def _strongly_connected(nodes: set[str], successors: dict[str, set[str]]) -> list[set[str]]:
    index, low, stack, on_stack, result, counter = {}, {}, [], set(), [], [0]
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(successors.get(root, ()))))]
        index[root] = low[root] = counter[0]; counter[0] += 1
        stack.append(root); on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in nodes:
                    continue
                if child not in index:
                    index[child] = low[child] = counter[0]; counter[0] += 1
                    stack.append(child); on_stack.add(child)
                    work.append((child, iter(sorted(successors.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = set()
                while True:
                    member = stack.pop(); on_stack.discard(member); component.add(member)
                    if member == node:
                        break
                result.append(component)
    return result


def _boundary_dead_ends(root: ET.Element) -> set[str]:
    """Dead-end junctions on the selection boundary, where the crop cut a longer road."""
    location = root.find("location")
    west, south, east, north = map(float, location.get("convBoundary").split(","))
    result = set()
    for junction in root.iter("junction"):
        if junction.get("type") != "dead_end":
            continue
        x, y = float(junction.get("x")), float(junction.get("y"))
        if min(x - west, east - x, y - south, north - y) <= BOUNDARY_TOLERANCE_M:
            result.add(junction.get("id"))
    return result


def network_connectivity(network: bytes) -> dict:
    root = ET.fromstring(network)
    edges = {edge.get("id"): edge for edge in root.iter("edge") if edge.get("function") in (None, "normal")}
    boundary = _boundary_dead_ends(root)
    report = {}
    for vehicle_class in CLASSES:
        usable = {edge_id for edge_id, edge in edges.items()
                  if any(_lane_allows(lane, vehicle_class) for lane in edge.iter("lane"))}
        successors: dict[str, set[str]] = defaultdict(set)
        for connection in root.iter("connection"):
            source, target = connection.get("from"), connection.get("to")
            if source in usable and target in usable:
                successors[source].add(target)
        nodes = set(usable)
        if vehicle_class == "pedestrian":
            # Pedestrians walk both ways and move between edges through walking areas and crossings.
            junction_parts = {edge.get("id") for edge in root.iter("edge")
                              if edge.get("function") in ("walkingarea", "crossing")}
            nodes |= junction_parts
            for connection in root.iter("connection"):
                source, target = connection.get("from"), connection.get("to")
                if source in nodes and target in nodes and (source in junction_parts or target in junction_parts):
                    successors[source].add(target); successors[target].add(source)
            for edge_id in usable:
                successors[edge_id]  # noqa: B018 - register node
        # Roads cut by the selection boundary continue outside it.
        nodes.add(OUTSIDE)
        for edge_id in usable:
            edge = edges[edge_id]
            if edge.get("to") in boundary:
                successors[edge_id].add(OUTSIDE)
            if edge.get("from") in boundary:
                successors[OUTSIDE].add(edge_id)
        full = _strongly_connected(nodes, successors)
        with_outside = next((component for component in full if OUTSIDE in component), {OUTSIDE})
        components = [component & usable for component in full if component & usable]
        largest = max((len(component) for component in components), default=0)
        interior_dead_ends = sorted(edge_id for edge_id in usable if edges[edge_id].get("to") not in boundary
                                    and not (successors.get(edge_id, set()) - {OUTSIDE}))
        no_out = sorted(edge for edge in usable if not successors.get(edge))
        no_in = sorted(edge for edge in usable if not any(edge in targets for source, targets in successors.items() if source != edge))
        report[vehicle_class] = {
            "usable_edges": len(usable), "strong_components": len(components),
            "largest_component_edges": largest,
            "largest_component_share": round(largest / len(usable), 4) if usable else None,
            "boundary_connected_edges": len(with_outside & usable),
            "boundary_connected_share": round(len(with_outside & usable) / len(usable), 4) if usable else None,
            "interior_dead_end_edges": len(interior_dead_ends),
            "sample_interior_dead_ends": interior_dead_ends[:10],
            "edges_without_successor": len(no_out), "edges_without_predecessor": len(no_in),
            "sample_edges_without_successor": no_out[:10],
        }
    return report


def structural_exclusions(filter_report: dict) -> dict:
    raw = filter_report["excluded_ways"]
    items = raw.items() if isinstance(raw, dict) else ((row.get("way_id"), row) for row in raw)
    reasons, highways, name_only = Counter(), Counter(), []
    for way_id, row in items:
        row_reasons = row.get("reasons") or []
        for reason in {entry.split(":")[0] for entry in row_reasons}:
            reasons[reason] += 1
        highways[row.get("highway")] += 1
        if row_reasons and all(entry.startswith(NAME_ONLY_PREFIXES) for entry in row_reasons):
            name_only.append({"way_id": way_id, "highway": row.get("highway"),
                              "names": sorted({entry.split("=", 1)[1] for entry in row_reasons if "=" in entry})})
    return {
        "excluded_way_count": sum(highways.values()),
        "reason_counts": dict(sorted(reasons.items())),
        "excluded_by_highway": dict(sorted(highways.items(), key=lambda item: str(item[0]))),
        "name_only_ambiguous": name_only,
        "network_before": filter_report.get("network_before"),
        "network_after": filter_report.get("network_after"),
        "removed_normal_edge_count": filter_report.get("removed_normal_edge_count"),
        "unrepresented_excluded_way_count": len(filter_report.get("unrepresented_excluded_ways") or []),
        "policy": filter_report.get("filter_policy"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--filter-report", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    filter_bytes, network_bytes = args.filter_report.read_bytes(), args.network.read_bytes()
    document = {
        "schema_version": "aero-bench.ground-road-scope-report/v1",
        "road_scope": "ordinary at-grade roads only; bridges, tunnels, elevated, layered and non-flat ways excluded before network generation",
        "inputs": {"filter_report_sha256": hashlib.sha256(filter_bytes).hexdigest(),
                   "network_sha256": hashlib.sha256(network_bytes).hexdigest()},
        "structural_exclusions": structural_exclusions(json.loads(filter_bytes)),
        "connectivity": network_connectivity(network_bytes),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **{k: document["connectivity"][k]["boundary_connected_share"] for k in CLASSES}}))


if __name__ == "__main__":
    main()
