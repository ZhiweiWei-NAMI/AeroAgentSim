from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from aero_bench.world.ground_roads import (
    audit_demand_routes, audit_ground_network, audit_retained_lane_rights, classify_roadways, filtered_effective_json,
    filtered_osm_xml, prepare_ground_sources, sha256, way_filter_reasons,
)


PROJECTION = "+proj=aeqd +lat_0=31 +lon_0=121"
SOURCE = b'''<osm version="0.6">
  <node id="1" lat="31" lon="121"/><node id="2" lat="31" lon="121.01"/>
  <node id="3" lat="31" lon="121"/><node id="4" lat="31" lon="121.01"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/>
    <tag k="lanes" v="4"/><tag k="cycleway" v="lane"/><tag k="aero_bench:source_id" v="110"/></way>
  <way id="11"><nd ref="3"/><nd ref="4"/><tag k="highway" v="primary"/>
    <tag k="level" v="1"/><tag k="aero_bench:source_id" v="111"/></way>
  <way id="20"><nd ref="1"/><nd ref="2"/><tag k="building" v="yes"/>
    <tag k="height" v="40"/><tag k="level" v="5"/></way>
  <relation id="21"><member type="way" ref="20" role="outer"/>
    <tag k="building" v="yes"/><tag k="height" v="100"/></relation>
</osm>'''
NETWORK = b'''<net>
  <location netOffset="0,0" projParameter="+proj=aeqd +lat_0=31 +lon_0=121"/>
  <edge id="10" from="1" to="2"><lane id="10_0" index="0" allow="pedestrian" shape="0,0 1,0"/>
    <lane id="10_1" index="1" allow="passenger bicycle bus" shape="0,1 1,1"/>
    <lane id="10_2" index="2" allow="passenger bus" shape="0,2 1,2"/></edge>
  <edge id="-10" from="2" to="1"><lane id="-10_0" index="0" allow="pedestrian" shape="1,0 0,0"/>
    <lane id="-10_1" index="1" allow="passenger bicycle bus" shape="1,-1 0,-1"/>
    <lane id="-10_2" index="2" allow="passenger bus" shape="1,-2 0,-2"/></edge>
  <edge id=":1_0" function="internal"><lane id=":1_0_0" index="0" shape="1,1 1,-1"/></edge>
  <edge id=":1_c0" function="crossing" crossingEdges="10 -10"><lane id=":1_c0_0" index="0" allow="pedestrian" shape="1,2 1,-2"/></edge>
  <connection from="10" to="-10" fromLane="1" toLane="1" via=":1_0_0"/>
  <connection from=":1_0" to="-10" fromLane="0" toLane="1"/>
  <junction id="1" incLanes="-10_0 -10_1 -10_2" intLanes=":1_0_0" shape="0,0 0,1"/>
  <junction id="2" incLanes="10_0 10_1 10_2" intLanes="" shape="1,0 1,1"/>
</net>'''


def effective_source() -> bytes:
    elements = []
    for element in ET.fromstring(SOURCE):
        record = {"type": element.tag, "id": int(element.get("id"))}
        if element.tag == "node":
            record.update(lat=float(element.get("lat")), lon=float(element.get("lon")))
        elif element.tag == "way":
            record["nodes"] = [int(nd.get("ref")) for nd in element.findall("nd")]
        elif element.tag == "relation":
            record["members"] = [{"type": member.get("type"), "ref": int(member.get("ref")),
                                  "role": member.get("role")} for member in element.findall("member")]
        record["tags"] = {tag.get("k"): tag.get("v") for tag in element.findall("tag")}
        elements.append(record)
    return json.dumps({"version": 0.6, "generator": "source fixture", "elements": elements}).encode()


@pytest.mark.parametrize("tags", [
    {"bridge": "yes"}, {"tunnel": "building_passage"}, {"elevated": "yes"},
    {"layer": "-1"}, {"layer": "0;2"}, {"level": "1"}, {"level": "mezzanine"},
    {"location": "underground"}, {"incline": "2%"}, {"incline": "down"},
    {"ele": "3;7"}, {"ele": "unknown"}, {"highway": "steps"},
    {"highway": "elevator"}, {"name": "An elevated road"},
])
def test_declared_non_ground_or_nonflat_roads_are_excluded(tags: dict) -> None:
    assert way_filter_reasons(tags)


@pytest.mark.parametrize("tags", [
    {"layer": "0", "level": "0", "incline": "0%"},
    {"ele": "30m"}, {"bridge": "no", "tunnel": "no", "elevated": "false"},
    {"height": "150", "min_height": "10"}, {},
])
def test_absolute_height_or_missing_grade_does_not_invent_non_ground_evidence(tags: dict) -> None:
    assert way_filter_reasons(tags) == []


def test_non_routing_platform_is_explicitly_unsupported_without_a_height_claim() -> None:
    assert way_filter_reasons({"highway": "platform"}) == ["unsupported_highway:platform"]


def test_node_elevation_profile_excludes_nonflat_way_but_constant_absolute_ele_is_retained() -> None:
    assert way_filter_reasons({}, {"1": "30m", "2": "30m"}) == []
    assert "nonflat_ele_profile:30.0..31.0" in way_filter_reasons({}, {"1": "30m", "2": "31m"})


def test_source_filter_keeps_buildings_heights_node_identity_and_road_rights() -> None:
    selection = classify_roadways(SOURCE)
    assert set(selection["excluded_ways"]) == {"11"}
    assert selection["ignored_non_highway_grade_way_count"] == 1
    filtered = filtered_osm_xml(SOURCE, {"11"})
    before, after = ET.fromstring(SOURCE), ET.fromstring(filtered)
    assert [node.attrib for node in before.findall("node")] == [node.attrib for node in after.findall("node")]
    assert ET.tostring(before.find("way[@id='10']")) == ET.tostring(after.find("way[@id='10']"))
    assert ET.tostring(before.find("way[@id='20']")) == ET.tostring(after.find("way[@id='20']"))
    assert ET.tostring(before.find("relation")) == ET.tostring(after.find("relation"))
    proof = audit_ground_network(NETWORK, filtered, expected_projection=PROJECTION)
    assert proof["same_xy_distinct_node_groups"] == [["1", "3"], ["2", "4"]]
    assert proof["ways"]["10"]["normal_edge_ids"] == ["-10", "10"]
    assert len(proof["normal_edges"]["10"]["lanes"]) == 3
    assert proof["normal_edges"]["10"]["lanes"][1]["allow"] == "passenger bicycle bus"
    assert proof["normal_edges"]["10"]["direction"] == "forward"
    assert proof["normal_edges"]["-10"]["direction"] == "reverse"
    assert set(proof["ways"]["10"]["generated_edge_ids"]) == {":1_0", ":1_c0"}
    assert proof["ways"]["10"]["connection_indices"] == [0, 1]


@pytest.mark.parametrize("change, message", [
    (lambda root: root.find("connection").set("to", "11"), "Dangling SUMO connection edge"),
    (lambda root: root.find("connection").set("via", ":removed_0_0"), "Dangling SUMO via lane"),
    (lambda root: root.find("connection").set("fromLane", "9"), "Dangling SUMO connection lane"),
    (lambda root: root.find("edge[@function='crossing']").set("crossingEdges", "11"), "Dangling crossing"),
    (lambda root: root.find("edge/lane").set("shape", "0,0,1 1,0,1"), "Non-ground SUMO Z"),
    (lambda root: ET.SubElement(root, "edge", id=":orphan", function="internal"), "no source road closure"),
    (lambda root: root.find("edge").set("id", "11"), "cannot be mapped uniquely"),
    (lambda root: root.find("edge").set("from", "missing"), "endpoint junction"),
    (lambda root: root.find("edge").set("to", "missing"), "endpoint junction"),
    (lambda root: root.find("edge").set("shape", "0,0,1 1,0,1"), "Non-ground SUMO Z"),
    (lambda root: root.find("junction").set("shape", "0,0,7 1,0,7"), "Non-ground SUMO Z"),
    (lambda root: root.find("junction").set("shape", "nan,0 1,0"), "Invalid SUMO shape"),
    (lambda root: root.find("junction").set("incLanes", "missing_lane"), "Dangling SUMO junction lanes"),
    (lambda root: root.find("junction[@id='2']").set("id", "1"), "duplicate SUMO junction"),
])
def test_stale_edges_connections_internals_and_nonzero_z_fail(change, message: str) -> None:
    root = ET.fromstring(NETWORK)
    change(root)
    with pytest.raises(ValueError, match=message):
        audit_ground_network(ET.tostring(root), filtered_osm_xml(SOURCE, {"11"}),
                             expected_projection=PROJECTION)


def test_audit_refuses_unfiltered_source_even_if_network_has_no_matching_edge() -> None:
    with pytest.raises(ValueError, match="still contains unsupported"):
        audit_ground_network(NETWORK, SOURCE, expected_projection=PROJECTION)


def test_actual_sumo_split_internal_edge_uses_lane_sequence_despite_repeated_index_metadata() -> None:
    root = ET.fromstring(NETWORK)
    internal = root.find("edge[@function='internal']")
    ET.SubElement(internal, "lane", id=":1_0_1", index="0", allow="bus", shape="1,2 1,-2")
    ET.SubElement(root, "connection", **{"from": ":1_0", "to": "-10", "fromLane": "1", "toLane": "2"})
    proof = audit_ground_network(ET.tostring(root), filtered_osm_xml(SOURCE, {"11"}),
                                 expected_projection=PROJECTION)
    assert proof["connections"][-1]["source_way_ids"] == ["10"]
    assert proof["connections"][-1]["attributes"]["fromLane"] == "1"
    internal.findall("lane")[1].set("id", ":1_0_0")
    with pytest.raises(ValueError, match="duplicate SUMO lane identity"):
        audit_ground_network(ET.tostring(root), filtered_osm_xml(SOURCE, {"11"}),
                             expected_projection=PROJECTION)


def test_network_removal_preserves_directional_lane_count_permissions_width_and_speed() -> None:
    assert audit_retained_lane_rights(NETWORK, NETWORK, set())["retained_lanes"] == 6
    changed = ET.fromstring(NETWORK)
    changed.find("edge/lane").set("allow", "passenger")
    with pytest.raises(ValueError, match="changed retained lane rights"):
        audit_retained_lane_rights(NETWORK, ET.tostring(changed), set())
    changed = ET.fromstring(NETWORK)
    changed.find("edge").remove(changed.find("edge/lane"))
    with pytest.raises(ValueError, match="changed retained lane count"):
        audit_retained_lane_rights(NETWORK, ET.tostring(changed), set())
    changed = ET.fromstring(NETWORK)
    changed.remove(changed.find("edge"))
    with pytest.raises(ValueError, match="retained normal edge set"):
        audit_retained_lane_rights(NETWORK, ET.tostring(changed), set())
    changed = ET.fromstring(NETWORK)
    changed.find("edge").set("from", "synthetic-xy-intersection")
    with pytest.raises(ValueError, match="changed retained junction node IDs"):
        audit_retained_lane_rights(NETWORK, ET.tostring(changed), set())


@pytest.mark.parametrize("route, error", [
    ("10 -10", None), ("10 11", "Illegal passenger demand edge"),
    ("-10 10", "Disconnected passenger demand route"),
])
def test_demand_route_checks_lane_permissions_and_generated_connections(route: str, error: str | None) -> None:
    demand = f'<routes><vType id="car" vClass="passenger"/><vehicle id="v" type="car"><route edges="{route}"/></vehicle><person id="p"><walk edges="10"/></person></routes>'.encode()
    if error:
        with pytest.raises(ValueError, match=error):
            audit_demand_routes(NETWORK, demand)
    else:
        result = audit_demand_routes(NETWORK, demand)
        assert result["vehicles"] == result["persons"] == 1
        assert result["traffic_recorded"] is False
        blocked = ET.fromstring(NETWORK)
        blocked.find("edge[@function='internal']/lane").set("allow", "pedestrian")
        with pytest.raises(ValueError, match="Disconnected passenger demand route"):
            audit_demand_routes(ET.tostring(blocked), demand)


def test_demand_audit_refuses_an_unsupported_flow_and_implicit_person_routing() -> None:
    with pytest.raises(ValueError, match="explicit vType"):
        audit_demand_routes(NETWORK, b'<routes><flow id="f" from="10" to="-10"/></routes>')
    with pytest.raises(ValueError, match="explicit walk edges"):
        audit_demand_routes(NETWORK, b'<routes><person id="p"><walk from="10" to="-10"/></person></routes>')


def test_filtered_effective_source_is_distinct_and_retains_every_non_road_element() -> None:
    original = effective_source()
    filtered = filtered_effective_json(original, SOURCE, {"11"})
    before, after = json.loads(original), json.loads(filtered)
    assert after["elements"] == [element for element in before["elements"] if element["id"] != 11]
    assert sha256(original) != sha256(filtered)
    before["elements"][4]["tags"]["lanes"] = "1"
    with pytest.raises(ValueError, match="source road nodes/tags differ"):
        filtered_effective_json(json.dumps(before).encode(), SOURCE, {"11"})


def preparation_pins(network: bytes = NETWORK) -> dict:
    return {
        "expected_source_osm_sha256": sha256(SOURCE), "expected_network_sha256": sha256(network),
        "expected_effective_osm_sha256": sha256(effective_source()),
        "expected_filtered_osm_sha256": sha256(filtered_osm_xml(SOURCE, {"11"})),
        "expected_projection": PROJECTION,
    }


def test_prepared_sources_record_distinct_pins_and_copy_verified_network_without_mutation(tmp_path: Path) -> None:
    source, net, effective = tmp_path / "source.osm", tmp_path / "source.net.xml", tmp_path / "effective.json"
    original = effective_source()
    for path, raw in ((source, SOURCE), (net, NETWORK), (effective, original)):
        path.write_bytes(raw)
    output = tmp_path / "prepared"
    receipt = prepare_ground_sources(source, net, effective, output, **preparation_pins())
    assert receipt["schema_version"] == "aero-bench.ground-road-sources/v2"
    assert receipt["unsupported_road_highways"] == ["platform"]
    assert receipt["expected_pins"]["network_sha256"] == sha256(NETWORK)
    assert receipt["original_building_source_pin_unchanged"] == sha256(original)
    assert receipt["network"]["regenerated"] is False
    assert receipt["network"]["traffic_recorded"] is False
    assert (output / "network.net.xml").read_bytes() == net.read_bytes() == NETWORK
    assert source.read_bytes() == SOURCE and effective.read_bytes() == original
    assert receipt["outputs"]["ground.osm"]["sha256"] != sha256(SOURCE)
    assert receipt["retained_effective_elements_unchanged"] is True
    with pytest.raises(FileExistsError):
        prepare_ground_sources(source, net, effective, output, **preparation_pins())


def test_preparation_fails_before_writes_when_source_and_network_disagree(tmp_path: Path) -> None:
    source, net, effective = tmp_path / "source.osm", tmp_path / "source.net.xml", tmp_path / "effective.json"
    for path, raw in ((source, SOURCE), (net, NETWORK.replace(b'id="-10"', b'id="11"')),
                      (effective, effective_source())):
        path.write_bytes(raw)
    with pytest.raises(ValueError, match="cannot be mapped uniquely"):
        prepare_ground_sources(source, net, effective, tmp_path / "prepared",
                               **preparation_pins(net.read_bytes()))
    assert not (tmp_path / "prepared").exists()


def test_audit_rejects_a_retained_source_way_with_no_normal_edge() -> None:
    ground = ET.fromstring(filtered_osm_xml(SOURCE, {"11"}))
    road = ET.SubElement(ground, "way", id="12")
    ET.SubElement(road, "nd", ref="1")
    ET.SubElement(road, "nd", ref="2")
    ET.SubElement(road, "tag", k="highway", v="residential")
    with pytest.raises(ValueError, match="Retained ground ways have no normal SUMO edge.*12"):
        audit_ground_network(NETWORK, ET.tostring(ground), expected_projection=PROJECTION)


@pytest.mark.parametrize("change, message", [
    (lambda root: root.remove(root.find("location")), "requires one location"),
    (lambda root: root.find("location").set("projParameter", "+proj=merc"), "projection differs"),
    (lambda root: root.find("location").set("netOffset", "999,888"), "netOffset=0,0"),
    (lambda root: root.find("location").set("netOffset", "nan,0"), "netOffset=0,0"),
])
def test_audit_rejects_missing_or_wrong_coordinate_contract(change, message: str) -> None:
    root = ET.fromstring(NETWORK)
    change(root)
    with pytest.raises(ValueError, match=message):
        audit_ground_network(ET.tostring(root), filtered_osm_xml(SOURCE, {"11"}),
                             expected_projection=PROJECTION)


@pytest.mark.parametrize("pin, message", [
    ("expected_source_osm_sha256", "source OSM digest differs"),
    ("expected_network_sha256", "network digest differs"),
    ("expected_effective_osm_sha256", "effective OSM digest differs"),
    ("expected_filtered_osm_sha256", "filtered OSM digest differs"),
    ("expected_projection", "projection differs"),
])
def test_preparation_requires_authoritative_hash_and_projection_pins_before_writes(
    tmp_path: Path, pin: str, message: str,
) -> None:
    source, net, effective = tmp_path / "source.osm", tmp_path / "source.net.xml", tmp_path / "effective.json"
    for path, raw in ((source, SOURCE), (net, NETWORK), (effective, effective_source())):
        path.write_bytes(raw)
    pins = preparation_pins()
    pins[pin] = "+proj=merc" if pin == "expected_projection" else "f" * 64
    with pytest.raises(ValueError, match=message):
        prepare_ground_sources(source, net, effective, tmp_path / "prepared", **pins)
    assert not (tmp_path / "prepared").exists()


def test_preparation_rejects_same_way_ids_with_replacement_nodes_against_source_pin(tmp_path: Path) -> None:
    root = ET.fromstring(SOURCE)
    root.find("node[@id='1']").set("id", "1001")
    assert root.find("node[@id='1']") is None
    for nd in root.findall("way/nd[@ref='1']"):
        nd.set("ref", "1001")
    source, net, effective = tmp_path / "source.osm", tmp_path / "source.net.xml", tmp_path / "effective.json"
    for path, raw in ((source, ET.tostring(root)), (net, NETWORK), (effective, effective_source())):
        path.write_bytes(raw)
    assert [way.get("id") for way in root.findall("way")] == ["10", "11", "20"]
    with pytest.raises(ValueError, match="source OSM digest differs"):
        prepare_ground_sources(source, net, effective, tmp_path / "prepared", **preparation_pins())
    assert not (tmp_path / "prepared").exists()


def test_effective_and_sumo_node_coordinates_must_match_compiler_serialization() -> None:
    root = ET.fromstring(SOURCE)
    root.find("node[@id='1']").set("lat", "32")
    with pytest.raises(ValueError, match="source road coordinates differ"):
        filtered_effective_json(effective_source(), ET.tostring(root), {"11"})
