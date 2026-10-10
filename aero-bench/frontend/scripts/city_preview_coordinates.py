"""Shared source-bound ENU coordinates for rendered city roads and SUMO replay.

The current urban-scene objects/v1 building contract supplies one outer ring.
It contains no courtyard-hole field. That limitation stays explicit; callers
with an explicit hole inventory can use ``footprint_from_enu_rings`` directly.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import sys
import struct
from types import MappingProxyType
import xml.etree.ElementTree as ET

from pyproj import CRS, Transformer
from shapely import get_coordinates
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from city_fixture_geometry import glb_triangles, projected_fixture

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from aero_bench.world.frame_math import EnuTransform

ORIGIN_FIELDS = ("latitude_deg", "longitude_deg", "ellipsoid_height_m", "amsl_m", "geoid_undulation_m")
PLACEMENT_RULE = "ENU_east = anchor_east_m + X; ENU_north = anchor_north_m - Z; ENU_up = base_enu_up_m + Y"
VIEWER_FRAME = "x-east,y-up,z-south-meters"
METRIC_MAP_EARTH_CIRCUMFERENCE_M = 40075016.686


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)


def canonical_origin(value: Mapping) -> Mapping[str, float]:
    if not isinstance(value, Mapping) or any(key not in value for key in ORIGIN_FIELDS):
        raise ValueError("Canonical city origin requires the five geographic/vertical fields")
    result = {key: _finite(value[key], f"origin.{key}") for key in ORIGIN_FIELDS}
    if not -90 < result["latitude_deg"] < 90 or not -180 <= result["longitude_deg"] < 180:
        raise ValueError("Canonical city origin is outside WGS84 coordinates")
    if abs(result["ellipsoid_height_m"] - result["amsl_m"] - result["geoid_undulation_m"]) > 1e-9:
        raise ValueError("Canonical city vertical relation is inconsistent")
    return MappingProxyType(result)


def _viewer_ring(points: Sequence[Sequence[float]], label: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(points, (list, tuple)) or len(points) < 4 or points[0] != points[-1]:
        raise ValueError(f"{label} must be an explicitly closed ENU ring")
    result = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"{label} must contain east/north pairs")
        result.append((_finite(point[0], f"{label}.east"), -_finite(point[1], f"{label}.north")))
    if len(set(result[:-1])) < 3:
        raise ValueError(f"{label} has fewer than three distinct vertices")
    return tuple(result)


@dataclass(frozen=True)
class CanonicalBuildingFootprint:
    object_id: str
    outline: tuple[tuple[float, float], ...]
    holes: tuple[tuple[tuple[float, float], ...], ...]
    base_up_m: float
    top_up_m: float
    geometry_sha256: str
    model_polygon: BaseGeometry | None = None

    @property
    def polygon(self) -> Polygon:
        return Polygon(self.outline, self.holes)

    @property
    def rendered_polygon(self) -> BaseGeometry:
        if self.model_polygon is None:
            raise ValueError(f"{self.object_id}: actual render GLB has not been verified")
        return self.model_polygon


def footprint_from_enu_rings(*, object_id: str, outline: Sequence[Sequence[float]],
                             holes: Sequence[Sequence[Sequence[float]]], base_up_m: float,
                             top_up_m: float, geometry_sha256: str) -> CanonicalBuildingFootprint:
    if not isinstance(object_id, str) or not object_id:
        raise ValueError("Canonical building requires a source object id")
    if not isinstance(holes, (list, tuple)):
        raise ValueError(f"{object_id}: holes must be explicitly declared")
    outer = _viewer_ring(outline, f"{object_id}.outline")
    inner = tuple(_viewer_ring(ring, f"{object_id}.holes[{index}]") for index, ring in enumerate(holes))
    base = _finite(base_up_m, f"{object_id}.base_up_m")
    top = _finite(top_up_m, f"{object_id}.top_up_m")
    if top <= base or not isinstance(geometry_sha256, str) or len(geometry_sha256) != 64 \
            or any(char not in "0123456789abcdef" for char in geometry_sha256):
        raise ValueError(f"{object_id}: source geometry/height is invalid")
    result = CanonicalBuildingFootprint(object_id, outer, inner, base, top, geometry_sha256)
    if result.polygon.is_empty or not result.polygon.is_valid or result.polygon.area <= 0:
        raise ValueError(f"{object_id}: invalid source footprint; no geometry repair is permitted")
    return result


@dataclass(frozen=True)
class CanonicalCityGeometry:
    scene_id: str
    origin: Mapping[str, float]
    footprints: tuple[CanonicalBuildingFootprint, ...]
    objects_sha256: str
    render_manifest_sha256: str
    mesh_pack_manifest_sha256: str
    mesh_pack_source_sha256: str
    model_footprint_audit: Mapping
    mesh_pack_projection: CityMeshPackProjection
    footprint_contract: str = "urban-scene-objects/v1-outer-rings-with-no-hole-field"

    def source_context(self, network_path: Path, source_osm_path: Path,
                       engineering_inputs_path: Path | None = None) -> dict:
        network_path = Path(network_path)
        network_bytes, osm_bytes = network_path.read_bytes(), Path(source_osm_path).read_bytes()
        network = ET.fromstring(network_bytes)
        if network.tag != "net" or ET.fromstring(osm_bytes).tag != "osm":
            raise ValueError("Canonical SUMO requires the actual network and OSM XML inputs")
        projection = CityEnuProjection(network, self.origin)
        engineering_path = Path(engineering_inputs_path) if engineering_inputs_path is not None \
            else network_path.parent / "engineering-inputs.json"
        engineering_bytes = engineering_path.read_bytes()
        engineering = json.loads(engineering_bytes)
        network_sha256, osm_sha256 = (hashlib.sha256(raw).hexdigest() for raw in (network_bytes, osm_bytes))
        if engineering.get("schema_version") != "aero-bench.urban-engineering-traffic-inputs/v1" \
                or engineering.get("network_sha256") != network_sha256 \
                or engineering.get("source_osm_sha256") != osm_sha256:
            raise ValueError("Actual network/OSM bytes differ from the engineering inputs")
        if engineering.get("projection") != projection.projection:
            raise ValueError("Actual SUMO projection differs from the engineering inputs")
        offset = engineering.get("net_offset")
        if not isinstance(offset, str) or len(offset.split(",")) != 2:
            raise ValueError("Engineering inputs require an explicit SUMO net_offset")
        declared_offset = tuple(_finite(float(value), "engineering net_offset") for value in offset.split(","))
        if declared_offset != projection.offset:
            raise ValueError("Actual SUMO netOffset differs from the engineering inputs")
        required_offset = engineering.get("net_offset_required")
        if not isinstance(required_offset, str) or len(required_offset.split(",")) != 2:
            raise ValueError("Engineering inputs require the declared net_offset_required")
        required_values = tuple(_finite(float(value), "engineering required net_offset") for value in required_offset.split(","))
        if required_values != projection.offset:
            raise ValueError("Actual SUMO netOffset violates the engineering profile requirement")
        return {"schema_version":"aero-bench.city-rendered-source-context/v1", "scene_id":self.scene_id,
            "objects_json_sha256":self.objects_sha256, "building_render_manifest_sha256":self.render_manifest_sha256,
            "mesh_pack_manifest_sha256":self.mesh_pack_manifest_sha256, "mesh_pack_source_sha256":self.mesh_pack_source_sha256,
            "source_network_sha256":network_sha256, "source_osm_sha256":osm_sha256,
            "engineering_inputs_sha256":hashlib.sha256(engineering_bytes).hexdigest(),
            "origin_wgs84":dict(self.origin), "projection":projection.metadata(),
            "mesh_pack_projection":self.mesh_pack_projection.metadata(),
            "building_geometry":{"count":len(self.footprints), "source_footprint_contract":self.footprint_contract,
                                 "actual_glb_footprint_audit":dict(self.model_footprint_audit)}}


def source_context_bytes(context: Mapping) -> bytes:
    """One exact JSON artifact shared by road, effective fixtures and real traffic recording."""
    return (json.dumps(dict(context), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode("utf-8")


def rendered_model_identity_sha256(records: Sequence[tuple[str, str, int]]) -> str:
    """ID length:uint32LE, ID UTF-8, SHA bytes, byte size:uint64LE; records sorted by ID."""
    ids = [record[0] for record in records]
    if ids != sorted(set(ids)):
        raise ValueError("Rendered model identity inventory must be sorted and unique")
    stream = bytearray(b"AERO-BENCH-RENDERED-MODEL-IDENTITY\0v1\0")
    for object_id, sha256, size_bytes in records:
        encoded = object_id.encode("utf-8")
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256) \
                or isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 1:
            raise ValueError("Rendered model identity requires actual byte digests and sizes")
        stream.extend(struct.pack("<I", len(encoded))); stream.extend(encoded)
        stream.extend(bytes.fromhex(sha256)); stream.extend(struct.pack("<Q", size_bytes))
    return hashlib.sha256(stream).hexdigest()


def load_canonical_city_geometry(objects_path: Path, render_manifest_path: Path,
                                 pack_path: Path) -> CanonicalCityGeometry:
    objects_bytes, render_bytes, pack_bytes = [Path(path).read_bytes() for path in (objects_path, render_manifest_path, pack_path)]
    objects, render, pack = [json.loads(raw) for raw in (objects_bytes, render_bytes, pack_bytes)]
    if objects.get("schema_version") != "aero-bench.urban-scene-objects/v1" or objects.get("coordinate_frame") != "ENU" \
            or render.get("schema_version") != "aero-bench.building-render-runtime/v2" \
            or pack.get("schema_version") != "aero-bench.osm2world-mesh-pack/v1":
        raise ValueError("Canonical city geometry has an unsupported source contract")
    scene = render["scene"]
    objects_digest, render_digest, pack_digest = [hashlib.sha256(raw).hexdigest() for raw in (objects_bytes, render_bytes, pack_bytes)]
    if scene.get("coordinate_frame") != "ENU" or scene.get("placement_rule") != PLACEMENT_RULE \
            or scene.get("objects_json_sha256") != objects_digest \
            or scene.get("mesh_pack_manifest_sha256") != pack_digest \
            or scene.get("mesh_pack_source_sha256") != pack["source"]["sha256"]:
        raise ValueError("Canonical city objects, render and mesh pack byte bindings differ")
    if not isinstance(scene.get("id"), str) or not scene["id"]:
        raise ValueError("Canonical render has no scene identity")
    origin = canonical_origin(objects["origin"])
    if objects["origin"].get("frame") != "WGS84->ECEF->ENU" \
            or dict(origin) != dict(canonical_origin(scene["origin_wgs84"])) \
            or any(pack["projection"]["origin"][key] != origin[key] for key in ("latitude_deg", "longitude_deg")):
        raise ValueError("Canonical city geographic/vertical origins differ")
    source_ref = pack["source"]
    source_bytes = (Path(pack_path).parent / "assets" / source_ref["sha256"]).read_bytes()
    if len(source_bytes) != source_ref["size_bytes"] or hashlib.sha256(source_bytes).hexdigest() != source_ref["sha256"]:
        raise ValueError("Actual mesh pack source bytes differ from its manifest")
    mesh_projection = CityMeshPackProjection(pack, json.loads(source_bytes), origin)
    source_buildings, rendered = objects["buildings"], render["buildings"]
    ids = [item["object_id"] for item in source_buildings]
    if not ids or len(ids) != len(set(ids)) or len(ids) != render["counts"]["buildings"] \
            or len(rendered) != len(ids) or {item["object_id"] for item in rendered} != set(ids):
        raise ValueError("Canonical source/render building inventories differ")
    by_id = {item["object_id"]: item for item in rendered}
    footprints = []
    model_records = []
    maximum_boundary_error = maximum_area_difference = 0.0
    for item in source_buildings:
        if item.get("kind") != "building" or "footprint_holes_enu_m" in item:
            raise ValueError("Urban-scene objects/v1 footprint fields differ from its outer-ring contract")
        footprint = footprint_from_enu_rings(object_id=item["object_id"], outline=item["footprint_enu_m"], holes=[],
            base_up_m=item["base_enu_up_m"], top_up_m=item["top_enu_up_m"], geometry_sha256=item["effective_geometry_sha256"])
        entry = by_id[footprint.object_id]
        envelope = entry["envelope"]
        min_e, min_z, max_e, max_z = footprint.polygon.bounds
        actual = {"min_e":min_e,"min_n":-max_z,"max_e":max_e,"max_n":-min_z,"base_up":footprint.base_up_m,"top_up":footprint.top_up_m}
        if entry["osm"] != item["osm"] or abs(entry["height_m"] - footprint.top_up_m + footprint.base_up_m) > 1e-3 \
                or any(abs(envelope[key] - value) > 1e-3 for key, value in actual.items()):
            raise ValueError(f"{footprint.object_id}: render and source placement metres differ")
        model = entry["derived_glb"]
        if model.get("path") != f"assets/{model.get('sha256')}":
            raise ValueError(f"{footprint.object_id}: render model is not content-addressed")
        model_path = render_manifest_path.parent / model["path"]
        model_bytes = model_path.read_bytes()
        if len(model_bytes) != model["bytes"] or hashlib.sha256(model_bytes).hexdigest() != model["sha256"]:
            raise ValueError(f"{footprint.object_id}: actual render model bytes differ from the manifest")
        triangles = glb_triangles(model_path)
        triangles[:, :, 0] += entry["anchor_east_m"]
        triangles[:, :, 1] += entry["base_enu_up_m"]
        triangles[:, :, 2] -= entry["anchor_north_m"]
        actual_polygon = projected_fixture(triangles)
        if actual_polygon.is_empty or not actual_polygon.is_valid:
            raise ValueError(f"{footprint.object_id}: actual model projection is invalid")
        # GEOS polygon Hausdorff measures interior triangle seam rings against
        # the source exterior. A Float32 roof seam of almost zero area can thus
        # report a many-metre error even when its filled footprint agrees.
        # Compare occupied sets with a fixed metric precision tolerance and
        # keep the actual projected GLB geometry unchanged.
        boundary_error = max(
            *(Point(x,z).distance(footprint.polygon) for x,z in get_coordinates(actual_polygon)),
            *(Point(x,z).distance(actual_polygon) for x,z in get_coordinates(footprint.polygon)),
        )
        area_difference = actual_polygon.symmetric_difference(footprint.polygon).area
        if boundary_error > 1e-3 or area_difference > max(1e-6, footprint.polygon.length * 1e-3) \
                or not actual_polygon.buffer(1e-3).covers(footprint.polygon) \
                or not footprint.polygon.buffer(1e-3).covers(actual_polygon) \
                or abs(float(triangles[:, :, 1].min()) - footprint.base_up_m) > 1e-3 \
                or abs(float(triangles[:, :, 1].max()) - footprint.top_up_m) > 1e-3:
            raise ValueError(f"{footprint.object_id}: actual GLB footprint/height differs from the source rings")
        maximum_boundary_error = max(maximum_boundary_error, boundary_error)
        maximum_area_difference = max(maximum_area_difference, area_difference)
        model_records.append((footprint.object_id, model["sha256"], model["bytes"]))
        footprints.append(replace(footprint, model_polygon=actual_polygon))
    audit = MappingProxyType({"policy":"byte-verified-derived-glb-transforms-projected-xz-joined-to-source-rings",
        "count":len(model_records), "model_identity_sha256":rendered_model_identity_sha256(sorted(model_records)),
        "model_identity_encoding":"id-length-u32le-id-utf8-sha256-bytes-size-u64le-sorted-by-id/v1",
        "maximum_boundary_error_m":maximum_boundary_error, "maximum_symmetric_difference_m2":maximum_area_difference,
        "boundary_tolerance_m":1e-3, "boundary_tolerance_method":"mutual-occupied-set-buffer-coverage-actual-geometry-retained",
        "area_tolerance":"max(1e-6m2,source-perimeter-m*1e-3m)"})
    return CanonicalCityGeometry(scene["id"], origin, tuple(footprints), objects_digest, render_digest, pack_digest, pack["source"]["sha256"], audit, mesh_projection)


class CityMeshPackProjection:
    """Invert the actual OSM2World converter frame and pack-origin translation.

    build-osm2world-pack.mjs converts using all source node bounds, then shifts
    the Float32 mesh arrays to the declared pack origin. Those two origins
    need not coincide. A source mesh retains its MetricMapProjection identity;
    only this explicit conversion produces the canonical ENU coordinates.
    """
    def __init__(self, pack: Mapping, source: Mapping, origin: Mapping[str, float]):
        self.origin = canonical_origin(origin)
        projection = pack.get("projection", {})
        if projection.get("name") != "MetricMapProjection" or projection.get("axes") != "east-up-south":
            raise ValueError("Canonical mesh conversion requires the declared MetricMapProjection frame")
        declared = projection.get("origin", {})
        if any(declared.get(key) != self.origin[key] for key in ("latitude_deg","longitude_deg")):
            raise ValueError("Mesh pack and canonical geographic origins differ")
        nodes = source.get("elements")
        if not isinstance(nodes, list):
            raise ValueError("Actual OSM2World source requires its element inventory")
        coordinates=[]
        for node in nodes:
            if node.get("type") != "node":
                continue
            latitude, longitude = _finite(node.get("lat"),"source node latitude"), _finite(node.get("lon"),"source node longitude")
            if not -90 < latitude < 90 or not -180 <= longitude < 180:
                raise ValueError("Source node is outside the supported WGS84 MetricMapProjection")
            coordinates.append((latitude,longitude))
        if not coordinates:
            raise ValueError("Actual OSM2World source has no nodes for its converter origin")
        self.converter_origin = MappingProxyType({
            "latitude_deg":(min(p[0] for p in coordinates)+max(p[0] for p in coordinates))/2,
            "longitude_deg":(min(p[1] for p in coordinates)+max(p[1] for p in coordinates))/2,
        })
        self.scale = METRIC_MAP_EARTH_CIRCUMFERENCE_M * math.cos(math.radians(self.converter_origin["latitude_deg"]))
        declared_scale = METRIC_MAP_EARTH_CIRCUMFERENCE_M * math.cos(math.radians(self.origin["latitude_deg"]))
        self.converter_mercator_y = self._mercator_y(self.converter_origin["latitude_deg"])
        self.translation_xz = (
            declared_scale*(self.converter_origin["longitude_deg"]-self.origin["longitude_deg"])/360,
            -declared_scale*(self.converter_mercator_y-self._mercator_y(self.origin["latitude_deg"])),
        )
        contract = pack.get("coordinate_contract")
        if not isinstance(contract, Mapping) \
                or contract.get("schema_version") != "aero-bench.osm2world-source-coordinates/v1" \
                or contract.get("recipe") != "source-node-bounds-local-Mercator-then-declared-origin-translation" \
                or contract.get("converter_origin") != dict(self.converter_origin) \
                or contract.get("source_json_sha256") != pack["source"]["sha256"] \
                or contract.get("earth_circumference_m") != METRIC_MAP_EARTH_CIRCUMFERENCE_M \
                or contract.get("native_point_quantization_m") != .001 \
                or contract.get("storage") != "source-mesh-float32-converter-coordinates-then-declared-origin-translation":
            raise ValueError("Mesh pack requires its actual producer-bound source coordinate contract")
        for name in ("producer_sha256","projection_helper_sha256"):
            digest = contract.get(name)
            if not isinstance(digest,str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("Mesh pack source coordinates require the producer byte identities")
        declared_translation = contract.get("stored_translation_xz_m")
        if not isinstance(declared_translation,(list,tuple)) or len(declared_translation) != 2 \
                or any(abs(_finite(value,"declared mesh translation")-expected) > 1e-8
                       for value,expected in zip(declared_translation,self.translation_xz)):
            raise ValueError("Mesh pack source coordinate translation differs from its actual recipe")
        self.translation_xz = tuple(declared_translation)
        self.coordinate_contract = MappingProxyType(dict(contract))
        self.enu = EnuTransform.from_origin(longitude_deg=self.origin["longitude_deg"],
            latitude_deg=self.origin["latitude_deg"],altitude_m=self.origin["ellipsoid_height_m"])

    @staticmethod
    def _mercator_y(latitude: float) -> float:
        sine = math.sin(math.radians(latitude))
        return math.log((1+sine)/(1-sine))/(4*math.pi)+.5

    def point(self, mesh_x: float, mesh_z: float) -> tuple[float,float]:
        x, z = _finite(mesh_x,"MetricMapProjection x"), _finite(mesh_z,"MetricMapProjection z")
        longitude = self.converter_origin["longitude_deg"]+(x-self.translation_xz[0])*360/self.scale
        normalized_y = self.converter_mercator_y-(z-self.translation_xz[1])/self.scale
        latitude = math.degrees(math.atan(math.sinh((normalized_y-.5)*2*math.pi)))
        enu = self.enu.geodetic_to_enu(longitude_deg=longitude,latitude_deg=latitude,
            altitude_m=self.origin["ellipsoid_height_m"])
        return enu.x,-enu.y

    def metadata(self) -> dict:
        return {"source_projection":"MetricMapProjection", "source_axes":"east-up-south",
            "declared_origin":{key:self.origin[key] for key in ("latitude_deg","longitude_deg")},
            "converter_origin":dict(self.converter_origin), "converter_scale_m":self.scale,
            "stored_translation_xz_m":list(self.translation_xz),
            "earth_circumference_m":METRIC_MAP_EARTH_CIRCUMFERENCE_M,
            "native_point_quantization_m":self.coordinate_contract["native_point_quantization_m"],
            "producer_sha256":self.coordinate_contract["producer_sha256"],
            "projection_helper_sha256":self.coordinate_contract["projection_helper_sha256"],
            "storage":"source-mesh-float32-converter-coordinates-then-declared-origin-translation",
            "conversion":"inverse-converter-Mercator-then-WGS84-ECEF-ENU",
            "output_axes":VIEWER_FRAME, "output_rounding":"none; preserve source triangle precision"}


class CityEnuProjection:
    """Convert actual SUMO projected positions/directions to the canonical ENU viewer."""
    def __init__(self, network: ET.Element, origin: Mapping[str, float], decimals: int = 2):
        self.origin = canonical_origin(origin)
        self.decimals = decimals
        location = network.find("location")
        if location is None or not location.get("projParameter") or location.get("projParameter") in ("!", "-", ".") \
                or location.get("netOffset") is None:
            raise ValueError("SUMO network requires an explicit geographic projection and netOffset")
        offsets = location.get("netOffset").split(",")
        if len(offsets) != 2:
            raise ValueError("SUMO netOffset must contain two coordinates")
        try:
            self.offset = tuple(_finite(float(value), "SUMO netOffset") for value in offsets)
        except ValueError as error:
            raise ValueError("SUMO netOffset is invalid") from error
        self.projection = location.get("projParameter")
        source_crs = CRS.from_proj4(self.projection)
        expected_crs = CRS.from_proj4(f"+proj=aeqd +lat_0={self.origin['latitude_deg']} +lon_0={self.origin['longitude_deg']} +datum=WGS84 +units=m +no_defs")
        if not source_crs.equals(expected_crs):
            raise ValueError("SUMO projection differs from the canonical origin's WGS84 local metric frame")
        self.to_geographic = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)
        self.enu = EnuTransform.from_origin(longitude_deg=self.origin["longitude_deg"], latitude_deg=self.origin["latitude_deg"], altitude_m=self.origin["ellipsoid_height_m"])

    def _point(self, native_x: float, native_y: float) -> tuple[float, float]:
        x, y = _finite(native_x, "SUMO x"), _finite(native_y, "SUMO y")
        longitude, latitude = self.to_geographic.transform(x-self.offset[0], y-self.offset[1], errcheck=True)
        enu = self.enu.geodetic_to_enu(longitude_deg=longitude, latitude_deg=latitude, altitude_m=self.origin["ellipsoid_height_m"])
        return enu.x, -enu.y

    def point(self, native_x: float, native_y: float) -> tuple[float, float]:
        return tuple(round(value, self.decimals) for value in self._point(native_x, native_y))

    def shape(self, value: str | None) -> list[list[float]]:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("SUMO shape is missing")
        result = []
        for token in value.split():
            coordinates = token.split(",")
            if len(coordinates) not in (2, 3):
                raise ValueError("SUMO shape vertex must contain two or three coordinates")
            if len(coordinates) == 3 and _finite(float(coordinates[2]), "SUMO shape z") != 0:
                raise ValueError("Ground-only SUMO shape contains nonzero elevation")
            point = list(self.point(float(coordinates[0]), float(coordinates[1])))
            if not result or point != result[-1]:
                result.append(point)
        return result

    def heading(self, native_x: float, native_y: float, clockwise_from_north_deg: float) -> float:
        angle = math.radians(_finite(clockwise_from_north_deg, "SUMO heading"))
        first = self._point(native_x, native_y)
        second = self._point(native_x + math.sin(angle), native_y + math.cos(angle))
        dx, dz = second[0]-first[0], second[1]-first[1]
        if math.hypot(dx, dz) < 1e-6:
            raise ValueError("SUMO heading collapsed during ENU projection")
        return round(math.degrees(math.atan2(dx, -dz)) % 360, 1) % 360

    def metadata(self) -> dict:
        return {"name":"WGS84->ECEF->ENU", "origin":dict(self.origin), "axes":VIEWER_FRAME,
                "sumo_transform":self.projection, "sumo_net_offset_m":list(self.offset),
                "position_precision_m":10 ** -self.decimals, "heading_precision_deg":0.1, "heading":"clockwise-from-north-reprojected-local-direction",
                "horizontal_altitude_basis":"source-origin-ellipsoid-height"}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("objects", "render", "pack", "network", "source-osm", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--engineering-inputs", type=Path,
                        help="Actual receipt; defaults to engineering-inputs.json beside the network")
    arguments = parser.parse_args()
    geometry = load_canonical_city_geometry(arguments.objects, arguments.render, arguments.pack)
    context = geometry.source_context(arguments.network, arguments.source_osm, arguments.engineering_inputs)
    raw = source_context_bytes(context)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(raw)
    print(json.dumps({"path":str(arguments.output), "sha256":hashlib.sha256(raw).hexdigest(),
        "size_bytes":len(raw), "buildings":len(geometry.footprints), "actual_glb_footprint_audit":dict(geometry.model_footprint_audit)}))


if __name__ == "__main__":
    main()
