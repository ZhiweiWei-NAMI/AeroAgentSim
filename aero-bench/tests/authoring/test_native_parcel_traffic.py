"""Native parcel traffic counts come from pinned, exactly bound SUMO demand."""

import hashlib
from types import SimpleNamespace

import pytest

from aero_bench.authoring.native_registry import native_parcel_traffic_counts
from aero_bench.config.loader import BundleReader
from aero_bench.world.contracts import SumoEntityBinding


ROUTES = b'''<routes>
<vType id="car" vClass="passenger"/>
<vType id="responder" vClass="emergency"/>
<vType id="two-wheels" vClass="bicycle"/>
<vehicle id="object.a" type="car"><route edges="edge.a"/></vehicle>
<vehicle id="object.b" type="responder"><route edges="edge.a"/></vehicle>
<vehicle id="object.c" type="two-wheels"><route edges="edge.a"/></vehicle>
<person id="object.d"><walk edges="edge.a"/></person>
</routes>'''


def inputs(tmp_path, routes=ROUTES, binding_changes=None):
    path = tmp_path / "routes.rou.xml"
    path.write_bytes(routes)
    kinds = {"object.a": "vehicle", "object.b": "vehicle", "object.c": "vehicle",
             "object.d": "person"}
    if binding_changes:
        kinds.update(binding_changes)
    world = SimpleNamespace(
        sumo=SimpleNamespace(
            routes_asset_id="asset.routes",
            object_bindings=tuple(
                SumoEntityBinding(sumo_object_id=object_id, entity_id=f"entity.{index}", kind=kind)
                for index, (object_id, kind) in enumerate(kinds.items())
            ),
        ),
        assets=(SimpleNamespace(
            artifact=SimpleNamespace(artifact_id="asset.routes", selector=path.name,
                                     sha256=hashlib.sha256(routes).hexdigest()),
            asset_role="sumo_routes", byte_size=len(routes),
        ),),
    )
    return world, BundleReader(tmp_path)


def test_counts_use_vehicle_class_instead_of_id_prefix_or_binding_kind(tmp_path):
    world, reader = inputs(tmp_path)
    assert native_parcel_traffic_counts(world=world, reader=reader) == {
        "vehicles": 2, "pedestrians": 1, "bicycles": 1,
    }


@pytest.mark.parametrize(("routes", "message"), [
    (ROUTES.replace(b' vClass="bicycle"', b""), "explicit id and vClass"),
    (ROUTES.replace(b' type="two-wheels"', b""), "explicit declared vType"),
    (ROUTES.replace(b'type="two-wheels"', b'type="not-declared"'), "explicit declared vType"),
    (ROUTES.replace(b' id="object.c"', b""), "unique explicit object ids"),
    (ROUTES.replace(b'id="object.c"', b'id="object.b"'), "unique explicit object ids"),
    (ROUTES.replace(b'</routes>', b'<person id="unbound"/></routes>'), "exact bindings"),
    (ROUTES.replace(b'</routes>', b'<flow id="unbounded"/></routes>'), "explicit vehicle/person"),
    (ROUTES.replace(b'</routes>', b'<vType id="car" vClass="taxi"/></routes>'), "repeat a vType"),
    (b"<routes>", "valid XML"),
])
def test_missing_ambiguous_or_unbound_demand_is_not_reported_as_zero(tmp_path, routes, message):
    world, reader = inputs(tmp_path, routes)
    with pytest.raises(ValueError, match=message):
        native_parcel_traffic_counts(world=world, reader=reader)


@pytest.mark.parametrize("bindings", [{"object.c": "person"}, {"missing": "vehicle"}])
def test_binding_ids_and_kinds_must_match_route_objects(tmp_path, bindings):
    world, reader = inputs(tmp_path, binding_changes=bindings)
    with pytest.raises(ValueError, match="exact bindings"):
        native_parcel_traffic_counts(world=world, reader=reader)


def test_route_bytes_must_match_the_world_asset_pin(tmp_path):
    world, reader = inputs(tmp_path)
    (tmp_path / "routes.rou.xml").write_bytes(ROUTES.replace(b'vClass="bicycle"', b'vClass="taxi"'))
    with pytest.raises(ValueError, match="sha256 mismatch"):
        native_parcel_traffic_counts(world=world, reader=reader)


def test_missing_sumo_source_is_not_an_empty_traffic_claim(tmp_path):
    with pytest.raises(ValueError, match="declared SUMO inputs"):
        native_parcel_traffic_counts(world=SimpleNamespace(sumo=None), reader=BundleReader(tmp_path))
