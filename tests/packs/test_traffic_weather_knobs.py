"""Traffic weather controls write the actual fact read by the energy model."""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from aeroagentsim.authoring.templates import TemplateRegistry, demo_source
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.models import ModelBuild, Segment, WindConsumption
from aeroagentsim.packs.traffic_accident.knobs import set_wind, weather_knobs
from aeroagentsim.scenario import load_scenario


def test_traffic_descriptors_expose_supported_bilingual_weather_and_no_density() -> None:
    knobs = TemplateRegistry().get("traffic-accident")["knobs"]
    added = {knob["id"]: knob for knob in knobs if knob["id"].startswith("wind_")}
    assert set(added) == {"wind_east_mps", "wind_north_mps"}
    for knob in added.values():
        assert "m/s" in knob["label"] and "米/秒" in knob["label_zh"]
        assert knob["description"] and "能耗" in knob["description_zh"]
        assert knob["default"] == 0.0
    assert not {"traffic_density", "traffic_flow", "vehicle_count", "precipitation", "visibility"} & {knob["id"] for knob in knobs}


def test_knobs_update_initial_weather_and_real_headwind_budget_without_touching_sources() -> None:
    source = demo_source("traffic-accident")
    before = {name: (source / name).read_bytes() for name in ("scenario.yaml", "behaviours.yaml")}
    prepared = TemplateRegistry().apply("traffic-accident", {"wind_east_mps": -6, "wind_north_mps": 4})
    loaded = load_scenario(prepared.document, base=prepared.base)
    weather = prepared.document["engines"]["weather"]["config"]["profiles"]["wind.region"]["oo:digital_twin.wind.horizontalVelocity"]
    assert weather == {"mode": "constant", "value": [[-6.0, 4.0]]}
    assert loaded.initial["wind.region"]["oo:digital_twin.wind.horizontalVelocity"] == weather["value"]
    cfg = loaded.engines["air_motion"]["config"]
    model = WindConsumption(ModelBuild(cfg["consumption_model"]["config"], cfg["energy"], loaded.registry, loaded.manifest.entities))
    assert model.dependencies == ("oo:digital_twin.wind.horizontalVelocity",)
    reads = []

    def get(ref, field):
        reads.append((ref.id, field))
        return loaded.initial[ref.id][field]

    context = SimpleNamespace(get=get)
    actor = next(ref for ref in loaded.manifest.entities if ref.id == "uav.alpha")
    east = Segment((100.0, 0.0, 0.0), 10.0, 100.0)
    west = Segment((-100.0, 0.0, 0.0), 10.0, 100.0)
    headwind = model.budget(context, actor, (east,))
    tailwind = model.budget(context, actor, (west,))
    assert headwind > tailwind
    assert headwind - tailwind == pytest.approx(180.0)
    assert reads == [("wind.region", "oo:digital_twin.wind.horizontalVelocity")] * 2
    assert prepared.knobs["wind_east_mps"] == -6.0
    assert prepared.run_metadata()["knobs"]["wind_north_mps"] == 4.0
    assert {name: (source / name).read_bytes() for name in before} == before


def test_workbench_knob_application_preserves_local_edits_and_pins_actual_values(tmp_path: Path) -> None:
    registry = TemplateRegistry()
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.import_template("traffic-accident", {}, registry=registry)
    draft["scenario"]["run"]["seed"] = 99
    draft = store.save(draft["id"], {"scenario": draft["scenario"], "revision": draft["revision"]})
    saved = store.apply_knobs(draft["id"], {"revision": draft["revision"], "knobs": {"wind_east_mps": 3, "wind_north_mps": -2}}, registry=registry)
    assert saved["scenario"]["run"]["seed"] == 99
    fact = next(entity for entity in saved["scenario"]["entities"] if entity["id"] == "wind.region")["facts"]["oo:digital_twin.wind.horizontalVelocity"]
    profile = saved["scenario"]["engines"]["weather"]["config"]["profiles"]["wind.region"]["oo:digital_twin.wind.horizontalVelocity"]
    assert fact == profile["value"] == [[3.0, -2.0]]
    assert saved["template"]["knobs"]["wind_east_mps"] == 3.0
    assert saved["template"]["knobs"]["wind_north_mps"] == -2.0


@pytest.mark.parametrize("value", [None, True, float("nan"), "4"])
def test_invalid_wind_values_do_not_become_defaults(value) -> None:
    with pytest.raises(ValueError, match="knob wind_east_mps"):
        TemplateRegistry().apply("traffic-accident", {"wind_east_mps": value})


def test_missing_runtime_consumer_does_not_expose_a_fake_parameter() -> None:
    source = demo_source("traffic-accident")
    document = yaml.safe_load((source / "scenario.yaml").read_bytes())
    document["engines"]["air_motion"]["config"].pop("consumption_model")
    assert weather_knobs(document) == []
    with pytest.raises(ValueError, match="runtime consumer"):
        set_wind(document, 0, 5.0)


def test_gust_profile_is_not_overwritten_by_constant_controls() -> None:
    source = demo_source("traffic-accident")
    document = yaml.safe_load((source / "scenario.yaml").read_bytes())
    profile = document["engines"]["weather"]["config"]["profiles"]["wind.region"]["oo:digital_twin.wind.horizontalVelocity"]
    profile.update(mode="gust", gusts=[{"start_ns": 1, "end_ns": 2, "value": [[5.0, 0.0]]}])
    before = copy.deepcopy(document)
    assert weather_knobs(document) == []
    with pytest.raises(ValueError, match="gust intervals"):
        set_wind(document, 0, 5.0)
    assert document == before
