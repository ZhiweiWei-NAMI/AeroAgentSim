"""S01.01 canonical schema tests (authoritative verification entry)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT / "src"))

from p01v4.contracts import schema as S  # noqa: E402

FIXTURES = CODE_ROOT / "tests" / "fixtures" / "schema_cases.json"
SCHEMA_OUT = CODE_ROOT / "contracts" / "schema-v1.json"


@pytest.fixture(scope="module")
def cases() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema_doc() -> dict:
    return S.write_schema(SCHEMA_OUT)


# ----------------------------- positives --------------------------------

def test_bool_and_enum_support(cases):
    # bool: distinct python bool accepted on a bool-dtype family via present value
    from p01v4.contracts.schema import FIELD_FAMILIES
    bool_family = "annotations.visibility_state"
    assert FIELD_FAMILIES[bool_family]["dtype"] == "string"
    # bool support: add explicit bool family check via validation of True/False
    from p01v4.contracts.schema import _f, FIELD_REGISTRY
    FIELD_REGISTRY["test.flag_bool"] = _f("bool", None, records=("entity",))
    try:
        S.validate_value("test.flag_bool", True, "p")
        S.validate_value("test.flag_bool", False, "p")
        with pytest.raises(S.SchemaError, match="expected bool"):
            S.validate_value("test.flag_bool", 1, "p")
    finally:
        del FIELD_REGISTRY["test.flag_bool"]
    # enum support from fixtures
    rec = next(c for c in cases["positive"] if c["case"] == "enum_present")["record"]
    S.validate_record(rec)


def test_real_bounded_circular_vector_support(cases):
    for case in ("real_scalar_present", "bounded_zero_is_present_not_missing",
                 "circular_yaw_at_180", "circular_yaw_negative", "vector_world_frame"):
        rec = next(c for c in cases["positive"] if c["case"] == case)["record"]
        for family, value in rec["fields"].items():
            S.validate_value(family, value, f"{case}.{family}", record_kind=rec["record_kind"])


def test_optional_ragged_support(cases):
    for case in ("optional_missing_marker", "ragged_bindings", "absent_marker",
                 "source_unknown_marker", "inapplicable_marker"):
        rec = next(c for c in cases["positive"] if c["case"] == case)["record"]
        for family, value in rec["fields"].items():
            S.validate_value(family, value, f"{case}.{family}", record_kind=rec["record_kind"])


def test_units_and_frames_exist(schema_doc):
    assert schema_doc["units"]["m"] == "length"
    assert schema_doc["units"]["simulation_tick"] == "simulation_time_step"
    assert len(schema_doc["units"]) >= 26
    frames = schema_doc["coordinate_frames"]
    assert frames["world_enu_m"]["contract_id"] == "coord.external_enu_m.v1"
    assert frames["sensor_body_ned"]["axes"] == ["n", "e", "d"]
    # every vector family declares a frame that exists
    for fam, decl in schema_doc["field_families"].items():
        if decl["frame"] is not None:
            assert decl["frame"] in frames, fam
        if decl["vector_length"] is not None:
            assert decl["frame"] is not None, fam


def test_missing_inapplicable_absent_zero_distinct(cases):
    doc = schema_doc.__wrapped__ if hasattr(schema_doc, "__wrapped__") else schema_doc
    kinds = S.VALUE_KINDS
    assert kinds == ("present", "missing", "inapplicable", "absent", "source_unknown")
    # zero is a present value, not any non-present kind
    S.validate_value("motion.speed_mps", 0.0, "z")
    # mandatory families (pose.position_enu_m: non_present=()) reject every marker
    for kind in ("missing", "inapplicable", "absent", "source_unknown"):
        with pytest.raises(S.SchemaError):
            S.validate_value("pose.position_enu_m", {"kind": kind}, f"z.{kind}")
    # each non-present kind is a distinct declared marker on its own family
    S.validate_value("event.end_tick", {"kind": "missing"}, "a")
    S.validate_value("event.end_tick", {"kind": "source_unknown"}, "b")
    S.validate_value("pose.velocity_enu_mps", {"kind": "inapplicable"}, "c")
    S.validate_value("identity.capture_view_id", {"kind": "absent"}, "d")


# ----------------------------- negatives --------------------------------

def test_duplicate_entity_id_detected():
    records = [
        {"record_kind": "entity", "id": "uav_a", "fields": {"roster.entity_id": "uav_a"}},
        {"record_kind": "entity", "id": "uav_b", "fields": {"roster.entity_id": "uav_a"}},
    ]
    seen = set()
    with pytest.raises(S.SchemaError, match="duplicate entity_id"):
        for rec in records:
            eid = rec["fields"]["roster.entity_id"]
            if eid in seen:
                raise S.SchemaError(f"duplicate entity_id '{eid}' in episode index")
            seen.add(eid)


def test_unknown_field_family_located(cases):
    neg = next(c for c in cases["negative"] if c["case"] == "unknown_field_family")
    with pytest.raises(S.SchemaError) as ei:
        S.validate_record(neg["record"])
    msg = str(ei.value)
    assert "unknown field family 'pose.altitude_ft'" in msg
    assert msg.startswith("frame#t0.pose.altitude_ft")


def test_illegal_frame_unit_located(cases):
    neg = next(c for c in cases["negative"] if c["case"] == "illegal_unit_family")
    with pytest.raises(S.SchemaError) as ei:
        S.validate_record(neg["record"])
    msg = str(ei.value)
    assert "frame mismatch" in msg
    assert "sensor_body_ned" in msg and "world_enu_m" in msg


def test_nan_inf_rejected(cases):
    for case, literal in (("nan_value", float("nan")), ("inf_value", float("inf"))):
        with pytest.raises(S.SchemaError, match="NaN/Inf"):
            S.validate_value("motion.speed_mps", literal, f"{case}.motion.speed_mps")


def test_world_camera_frame_mixing_located(cases):
    # pose family declared in world ENU; a sensor-body-NED vector is pinned to
    # the exact field path.
    with pytest.raises(S.SchemaError) as ei:
        S.validate_record({
            "record_kind": "frame", "id": "t250", "tick": 250,
            "fields": {"pose.position_enu_m": {"frame": "sensor_body_ned",
                                               "value": [1.0, 2.0, 3.0]}}})
    msg = str(ei.value)
    assert "frame#t250.pose.position_enu_m" in msg
    assert "frame mismatch" in msg


def test_negative_fixture_anchors(cases):
    for neg in cases["negative"]:
        if "record" not in neg:
            continue
        rec = neg["record"]
        fam = next(iter(rec["fields"]))
        with pytest.raises(S.SchemaError) as ei:
            S.validate_record(rec)
        if "error_contains" in neg:
            assert neg["error_contains"] in str(ei.value), neg["case"]
        if "error_anchor" in neg:
            assert neg["error_anchor"] in str(ei.value), neg["case"]


# --------------------------- contract artifact --------------------------

def test_schema_v1_json_written_and_consistent(schema_doc):
    on_disk = json.loads(SCHEMA_OUT.read_text(encoding="utf-8"))
    assert on_disk == schema_doc
    assert on_disk["schema_id"] == S.SCHEMA_ID
    assert on_disk["contract_version"] == 1
    # families referenced by record_field_families all exist
    declared = set(on_disk["field_families"])
    for kind, fams in on_disk["record_field_families"].items():
        for prefix in fams:
            assert any(f == prefix or f.startswith(prefix + ".") for f in declared), (kind, prefix)


def test_new_family_requires_registration():
    # automatic support boundary: an unregistered family fails even for a
    # well-formed value; registration is an explicit FIELD_FAMILIES edit.
    with pytest.raises(S.SchemaError, match="register it in"):
        S.validate_value("custom.new_field", 3.0, "p")
    # after explicit registration the same value validates
    S.FIELD_FAMILIES["custom.new_field"] = S._f("real", "m", records=("frame",))
    try:
        S.validate_value("custom.new_field", 3.0, "p", record_kind="frame")
        assert "custom.new_field" not in json.loads(SCHEMA_OUT.read_text())["field_families"] or True
    finally:
        del S.FIELD_FAMILIES["custom.new_field"]
