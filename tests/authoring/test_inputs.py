"""Source configuration cannot silently substitute unrelated maps or ontologies."""

from pathlib import Path

import pytest

from aeroagentsim.authoring.inputs import configured_extracts, configured_ontology
from aeroagentsim.authoring.scene import compile_scene, extract_bounds
from aeroagentsim.scenario.paths import source_path


def test_packaged_map_is_real_and_compilable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEROAGENTSIM_OSM_EXTRACTS", raising=False)
    path = configured_extracts()["wujiaochang"]
    scene = compile_scene(path, extract_bounds(path), alt=0.0, level_height_m=3.5)
    assert scene["roads"] and len(scene["geojson"]["features"]) == 4
    assert scene["attribution"] == "© OpenStreetMap contributors · ODbL"


@pytest.mark.parametrize(
    "value", ["", "null", "{}", '{"city": null}', '{"city": "/missing/map.osm"}']
)
def test_invalid_explicit_map_never_uses_packaged_input(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AEROAGENTSIM_OSM_EXTRACTS", value)
    with pytest.raises((ValueError, FileNotFoundError)):
        configured_extracts()


def test_configured_map_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_OSM_EXTRACTS", raising=False)
    path = tmp_path / "selected.osm"
    path.write_bytes(configured_extracts()["wujiaochang"].read_bytes())
    monkeypatch.setenv("AEROAGENTSIM_OSM_EXTRACTS", '{"selected": "' + str(path) + '"}')
    assert configured_extracts() == {"selected": path}


def test_ontology_and_scenario_paths_require_explicit_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    with pytest.raises(ValueError, match="AEROAGENTSIM_AEROGRAPH_ROOT"):
        configured_ontology()
    with pytest.raises(ValueError, match="AEROAGENTSIM_AEROGRAPH_ROOT"):
        source_path("${AEROAGENTSIM_AEROGRAPH_ROOT}/file")
    monkeypatch.setenv("AEROAGENTSIM_AEROGRAPH_ROOT", "/explicit/ontology")
    assert source_path("${AEROAGENTSIM_AEROGRAPH_ROOT}/file") == Path(
        "/explicit/ontology/file"
    )
    with pytest.raises(ValueError, match="no ontology source"):
        configured_ontology()
