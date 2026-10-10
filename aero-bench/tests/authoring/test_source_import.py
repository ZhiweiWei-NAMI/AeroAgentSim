from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from aero_bench.authoring.selection import SceneSelectionError
from aero_bench.authoring.source_import import normalized_osm_api_source


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "validation/city-authoring-stage1-20260928/jingan-source"


def test_official_bbox_snapshot_reconstructs_second_registered_source(tmp_path: Path) -> None:
    primary = tmp_path / "raw.json"
    primary.write_bytes(gzip.decompress((EVIDENCE / "osm-api-raw.json.gz").read_bytes()))
    with pytest.raises(SceneSelectionError, match="incomplete geometric/traffic evidence"):
        normalized_osm_api_source(primary)

    raw, report = normalized_osm_api_source(primary, (
        EVIDENCE / "way-515219905-full.json", EVIDENCE / "way-455842359-full.json",
    ))
    assert raw == (ROOT / "frontend/public/osm2world/shanghai-jingan.osm.json").read_bytes()
    assert report["source_sha256"] == "778c35a2743e4f291d4257765494b6e492613283107524ac8a7ce8359778f7df"
    assert report["source_element_counts"] == {"node": 3182, "relation": 12, "way": 295}
    assert len(report["omitted_relation_ids"]) == 200
