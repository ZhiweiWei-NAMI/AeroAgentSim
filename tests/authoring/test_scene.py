"""Real OSM compilation and precompiled SUMO fixture; no Docker required."""

from __future__ import annotations

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from importlib.util import spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

from aeroagentsim.authoring.scene import (
    compile_scene,
    crop_osm,
    extract_bounds,
    generate_sumo,
)

FIXTURE = Path(__file__).parent / "fixtures" / "wujiaochang.osm.xml"


def test_real_region_enu_and_heights(tmp_path: Path) -> None:
    bounds = extract_bounds(FIXTURE)
    scene = compile_scene(FIXTURE, bounds, alt=20.0, level_height_m=3.5)
    assert scene["origin"] == {
        "lat": (bounds[1] + bounds[3]) / 2,
        "lon": (bounds[0] + bounds[2]) / 2,
        "alt": 20.0,
    }
    buildings = [
        f for f in scene["geojson"]["features"] if f["geometry"]["type"] == "Polygon"
    ]
    assert len(buildings) == 3 and scene["roads"]
    assert any(f["properties"].get("height_source") == "osm.height" for f in buildings)
    levels = next(
        f["properties"] for f in buildings if "level_height_m" in f["properties"]
    )
    assert levels["height"] == float(levels["building:levels"]) * 3.5
    assert any("height" not in f["properties"] for f in buildings)
    assert any("missing height" in d for d in scene["diagnostics"])
    assert scene["ground"]["width_m"] > 0 and scene["ground"]["depth_m"] > 0
    road = scene["roads"][0]["points"]
    assert all(len(p) == 3 for p in road)
    out = tmp_path / "region.osm.xml"
    crop_osm(FIXTURE, bounds, out)
    assert extract_bounds(out) == bounds


@pytest.mark.parametrize(
    "bounds",
    [[120, 30, 120, 31], [None, 30, 121, 31], [121, 30, 120, 31], [0, 0, 1, 1]],
)
def test_invalid_or_uncovered_region(bounds: list[Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        compile_scene(FIXTURE, bounds, alt=0.0, level_height_m=3.0)


def test_missing_reference_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "bad.osm"
    path.write_text(
        '<osm><node id="1" lon="0" lat="0"/><node id="2" lon="1" lat="1"/><way id="3"><nd ref="1"/><nd ref="999"/><tag k="highway" v="service"/></way></osm>'
    )
    with pytest.raises(ValueError, match="missing node references"):
        compile_scene(path, [0, 0, 1, 1], alt=0.0, level_height_m=3.0)


def test_precompiled_network_checked_and_failures_preserve_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = FIXTURE.with_name("wujiaochang.net.xml")
    home = tmp_path / "sumo"
    typemap = home / "data/typemap/osmNetconvert.typ.xml"
    typemap.parent.mkdir(parents=True)
    typemap.write_text("<types/>")
    monkeypatch.setenv("SUMO_HOME", str(home))

    def converter(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        shutil.copyfile(fixture, Path(command[command.index("--output-file") + 1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", converter)
    output = tmp_path / "network.net.xml"
    result = generate_sumo(FIXTURE, output)
    assert result["lane_count"] > 0 and result["edge_count"] > 0
    assert output.read_bytes() == fixture.read_bytes()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 2, "", "bad network"),
    )
    with pytest.raises(ValueError, match="bad network"):
        generate_sumo(FIXTURE, output)
    assert output.read_bytes() == fixture.read_bytes()


@pytest.mark.skipif(
    shutil.which("netconvert") is None, reason="SUMO netconvert is not installed"
)
def test_real_network_generation_without_sumo_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUMO_HOME", raising=False)
    output = tmp_path / "network.net.xml"
    result = generate_sumo(FIXTURE, output)
    assert result["edge_count"] > 0 and result["lane_count"] > 0
    network = ET.parse(output).getroot()
    edges = [
        edge for edge in network.findall("edge") if edge.get("function") != "internal"
    ]
    assert result["edge_count"] == len(edges)
    assert result["lane_count"] == sum(len(edge.findall("lane")) for edge in edges)
    assert "--type-files" in result["command"]
    assert "SUMO_HOME" not in os.environ
    assert "SUMO_HOME is not set" not in result["stderr"]


@pytest.mark.parametrize("source", ["environment", "prefix", "symlink", "package"])
def test_sumo_home_resolution_order_and_subprocess_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    homes = {
        "environment": tmp_path / "configured-sumo",
        "prefix": tmp_path / "installation/share/sumo",
        "symlink": tmp_path / "linked/share/sumo",
        "package": tmp_path / "site-packages/sumo",
    }
    typemap = Path("data/typemap/osmNetconvert.typ.xml")
    for home in homes.values():
        (home / typemap).parent.mkdir(parents=True)
        (home / typemap).write_text("<types/>")
    # Remove higher-priority data while leaving SUMO_HOME configured, so an
    # incomplete installation cannot hide a later usable source.
    for key in homes:
        if key == source:
            break
        (homes[key] / typemap).unlink()
    monkeypatch.setenv("SUMO_HOME", str(homes["environment"]))
    monkeypatch.setenv("AAS_SUMO_TEST_ENV", "preserved")
    binary = tmp_path / "installation/bin/netconvert"
    binary.parent.mkdir(parents=True)
    binary.touch()
    linked = tmp_path / "linked/bin/netconvert"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(binary)
    monkeypatch.setattr(
        "aeroagentsim.authoring.scene.shutil.which", lambda _: str(linked)
    )
    package_init = homes["package"] / "__init__.py"
    package_init.write_text("raise AssertionError('package must not be imported')")
    package = spec_from_file_location("sumo", package_init)
    monkeypatch.setattr(
        "aeroagentsim.authoring.scene.importlib.util.find_spec", lambda _: package
    )

    def converter(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        assert kwargs["env"]["SUMO_HOME"] == str(homes[source])
        assert kwargs["env"]["AAS_SUMO_TEST_ENV"] == "preserved"
        assert command[command.index("--type-files") + 1] == str(homes[source] / typemap)
        shutil.copyfile(
            FIXTURE.with_name("wujiaochang.net.xml"),
            command[command.index("--output-file") + 1],
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", converter)
    result = generate_sumo(FIXTURE, tmp_path / "network.net.xml")
    assert result["edge_count"] > 0
    assert os.environ["SUMO_HOME"] == str(homes["environment"])


def test_missing_sumo_typemap_names_sumo_home_and_preserves_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUMO_HOME", raising=False)
    monkeypatch.setattr(
        "aeroagentsim.authoring.scene.shutil.which",
        lambda _: str(tmp_path / "bin/netconvert"),
    )
    monkeypatch.setattr(
        "aeroagentsim.authoring.scene.importlib.util.find_spec", lambda _: None
    )
    output = tmp_path / "network.net.xml"
    fixture = FIXTURE.with_name("wujiaochang.net.xml")
    shutil.copyfile(fixture, output)
    with pytest.raises(ValueError, match="Set SUMO_HOME.*installation directory"):
        generate_sumo(FIXTURE, output)
    assert output.read_bytes() == fixture.read_bytes()


def test_multipolygon_preserves_outer_inner_rings_and_source_relations(
    tmp_path: Path,
) -> None:
    source = tmp_path / "multipolygon.osm"
    source.write_text("""<osm version="0.6">
    <node id="1" lon="0" lat="0"/><node id="2" lon="1" lat="0"/><node id="3" lon="1" lat="1"/><node id="4" lon="0" lat="1"/>
    <node id="5" lon="0.2" lat="0.2"/><node id="6" lon="0.8" lat="0.2"/><node id="7" lon="0.8" lat="0.8"/><node id="8" lon="0.2" lat="0.8"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/></way><way id="11"><nd ref="3"/><nd ref="4"/><nd ref="1"/></way>
    <way id="12"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/></way>
    <relation id="20"><member type="way" ref="10" role="outer"/><member type="way" ref="11" role="outer"/><member type="way" ref="12" role="inner"/><tag k="type" v="multipolygon"/><tag k="building" v="yes"/><tag k="height" v="12 m"/></relation></osm>""")
    scene = compile_scene(source, [0, 0, 1, 1], alt=0.0, level_height_m=3.0)
    building = scene["geojson"]["features"][0]
    assert len(building["geometry"]["coordinates"]) == 2
    assert building["properties"]["height"] == 12.0
    cropped = tmp_path / "crop.osm"
    crop_osm(source, [0.1, 0.1, 0.9, 0.9], cropped)
    assert "<relation" in cropped.read_text()
