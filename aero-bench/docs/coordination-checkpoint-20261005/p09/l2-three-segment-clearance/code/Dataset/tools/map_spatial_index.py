"""Spatial policy helpers for Donghu pedestrian generation and validation.

The traffic bundle is the authoritative ENU frame. Source GeoJSON layers are
only used after their lon/lat coordinates are transformed through the frozen
road GeoJSON fit.  Road-derived bundles keep physical lane samples separate
from road-center samples so GeoJSON fitting does not depend on lane edge ids.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.ops import unary_union
from shapely.prepared import prep


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP_ID = "donghu_road_topo"
DEFAULT_MAP_PACKAGE = ROOT / "Config" / "LowAltitude" / "Maps" / DEFAULT_MAP_ID / "map_package.json"
LANE_HALF_WIDTH_M = 1.9
PEDESTRIAN_ROAD_BUFFER_M = 0.25
CROWD_ROAD_BUFFER_M = 0.5
SIDEWALK_MIN_OFFSET_FROM_CURB_M = 1.2
GATHERING_MIN_OFFSET_FROM_CURB_M = 4.5
GROUND_Z_M = 0.0
MAX_PEDESTRIAN_SEGMENT_M = 18.0
WEB_MERCATOR_ORIGIN_SHIFT = 20037508.342789244


class SpatialValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class LaneSample:
    edge_id: str
    lane_id: str
    lane_index: int
    s_m: float
    x_m: float
    y_m: float
    z_m: float
    yaw_deg: float


@dataclass(frozen=True)
class GeoJsonBundleFit:
    matrix: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    mean_error_m: float
    max_error_m: float
    pair_count: int

    def transform_local_xy(self, x_m: float, y_m: float) -> tuple[float, float]:
        return (
            x_m * self.matrix[0][0] + y_m * self.matrix[1][0] + self.matrix[2][0],
            x_m * self.matrix[0][1] + y_m * self.matrix[1][1] + self.matrix[2][1],
        )


@dataclass(frozen=True)
class PlannedSidewalkAnchor:
    position_enu_m: list[float]
    sample: LaneSample
    offset_from_curb_m: float
    resolved_lateral_from_center_m: float
    placement_semantics: str


@dataclass(frozen=True)
class PlannedCrossing:
    start_position_enu_m: list[float]
    roadway_center_position_enu_m: list[float]
    opposite_curb_position_enu_m: list[float]
    sample: LaneSample
    offset_from_curb_m: float
    resolved_lateral_from_center_m: float


class LaneResolver:
    def __init__(self, lane_samples_csv: Path) -> None:
        self.path = lane_samples_csv
        self.samples: list[LaneSample] = []
        self.by_edge: dict[str, list[LaneSample]] = {}
        with lane_samples_csv.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = {"edge_id", "lane_id", "lane_index", "s_m", "x_m", "y_m", "yaw_deg"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise SpatialValidationError(
                    f"{lane_samples_csv} is not lane_center_samples.csv; missing {sorted(missing)}"
                )
            for row in reader:
                sample = LaneSample(
                    edge_id=str(row["edge_id"]),
                    lane_id=str(row["lane_id"]),
                    lane_index=int(row["lane_index"]),
                    s_m=float(row["s_m"]),
                    x_m=float(row["x_m"]),
                    y_m=float(row["y_m"]),
                    z_m=float(row.get("z_m") or 0.0),
                    yaw_deg=float(row.get("yaw_deg") or 0.0),
                )
                self.samples.append(sample)
                self.by_edge.setdefault(sample.edge_id, []).append(sample)
        for rows in self.by_edge.values():
            rows.sort(key=lambda item: item.s_m)
        if not self.samples:
            raise SpatialValidationError(f"{lane_samples_csv} contains no lane samples")
        self.min_x = min(sample.x_m for sample in self.samples)
        self.max_x = max(sample.x_m for sample in self.samples)
        self.min_y = min(sample.y_m for sample in self.samples)
        self.max_y = max(sample.y_m for sample in self.samples)
        self._grid_cell_m = 25.0
        self._grid: dict[tuple[int, int], list[LaneSample]] = {}
        for sample in self.samples:
            self._grid.setdefault(self._grid_key(sample.x_m, sample.y_m), []).append(sample)

    def _grid_key(self, x_m: float, y_m: float) -> tuple[int, int]:
        return (int(math.floor(float(x_m) / self._grid_cell_m)), int(math.floor(float(y_m) / self._grid_cell_m)))

    def nearest_to_xy(self, x_m: float, y_m: float) -> LaneSample:
        cx, cy = self._grid_key(x_m, y_m)
        candidates: list[LaneSample] = []
        for radius in range(0, 9):
            candidates.clear()
            for gx in range(cx - radius, cx + radius + 1):
                for gy in range(cy - radius, cy + radius + 1):
                    candidates.extend(self._grid.get((gx, gy), ()))
            if candidates:
                return min(candidates, key=lambda item: (item.x_m - x_m) ** 2 + (item.y_m - y_m) ** 2)
        return min(self.samples, key=lambda item: (item.x_m - x_m) ** 2 + (item.y_m - y_m) ** 2)

    def nearest(self, pos_enu_m: Sequence[float]) -> LaneSample:
        return self.nearest_to_xy(float(pos_enu_m[0]), float(pos_enu_m[1]))

    def nearby_diverse_samples(
        self,
        x_m: float,
        y_m: float,
        *,
        max_radius_m: float,
        longitudinal_bucket_m: float,
        max_count: int,
    ) -> list[LaneSample]:
        """Return spatially near lane samples without dense same-edge domination."""

        cell_radius = int(math.ceil(float(max_radius_m) / self._grid_cell_m)) + 1
        cx, cy = self._grid_key(x_m, y_m)
        candidates = [
            sample
            for gx in range(cx - cell_radius, cx + cell_radius + 1)
            for gy in range(cy - cell_radius, cy + cell_radius + 1)
            for sample in self._grid.get((gx, gy), ())
            if math.hypot(sample.x_m - float(x_m), sample.y_m - float(y_m))
            <= float(max_radius_m)
        ]
        candidates.sort(
            key=lambda sample: (
                (sample.x_m - float(x_m)) ** 2 + (sample.y_m - float(y_m)) ** 2,
                sample.edge_id,
                sample.s_m,
            )
        )
        selected: list[LaneSample] = []
        seen_buckets: set[tuple[str, int]] = set()
        for sample in candidates:
            key = (
                sample.edge_id,
                int(math.floor(float(sample.s_m) / float(longitudinal_bucket_m))),
            )
            if key in seen_buckets:
                continue
            seen_buckets.add(key)
            selected.append(sample)
            if len(selected) >= int(max_count):
                break
        return selected

    def resolve_edge_s(self, edge_id: str, s_m: float) -> LaneSample:
        samples = self.by_edge.get(edge_id)
        if not samples:
            raise SpatialValidationError(f"Unknown lane edge_id: {edge_id}")
        return min(samples, key=lambda item: abs(item.s_m - s_m))

    def interpolate_edge_s(
        self,
        edge_id: str,
        lane_id: str,
        s_m: float,
    ) -> LaneSample:
        samples = [
            sample
            for sample in self.by_edge.get(edge_id, ())
            if sample.lane_id == lane_id
        ]
        if not samples:
            raise SpatialValidationError(f"Unknown lane_id: {lane_id}")
        if s_m <= samples[0].s_m:
            return samples[0]
        if s_m >= samples[-1].s_m:
            return samples[-1]
        for left, right in zip(samples, samples[1:]):
            if left.s_m <= s_m <= right.s_m:
                span_m = right.s_m - left.s_m
                if span_m <= 0.0:
                    return left
                ratio = (s_m - left.s_m) / span_m
                left_yaw = math.radians(left.yaw_deg)
                right_yaw = math.radians(right.yaw_deg)
                yaw_deg = math.degrees(
                    math.atan2(
                        (1.0 - ratio) * math.sin(left_yaw)
                        + ratio * math.sin(right_yaw),
                        (1.0 - ratio) * math.cos(left_yaw)
                        + ratio * math.cos(right_yaw),
                    )
                )
                return LaneSample(
                    edge_id=edge_id,
                    lane_id=lane_id,
                    lane_index=left.lane_index,
                    s_m=float(s_m),
                    x_m=left.x_m + ratio * (right.x_m - left.x_m),
                    y_m=left.y_m + ratio * (right.y_m - left.y_m),
                    z_m=left.z_m + ratio * (right.z_m - left.z_m),
                    yaw_deg=yaw_deg,
                )
        raise SpatialValidationError(
            f"Cannot interpolate lane {lane_id} at longitudinal position {s_m}"
        )

    def edge_s_bounds(self, edge_id: str) -> tuple[float, float]:
        samples = self.by_edge.get(edge_id)
        if not samples:
            raise SpatialValidationError(f"Unknown lane edge_id: {edge_id}")
        return samples[0].s_m, samples[-1].s_m


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SpatialValidationError(f"Required map file does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _project_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _lonlat_to_web_mercator(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    clamped_lat = max(-85.05112878, min(85.05112878, float(lat_deg)))
    x_m = float(lon_deg) * WEB_MERCATOR_ORIGIN_SHIFT / 180.0
    rad = math.radians(clamped_lat)
    y_m = math.log(math.tan(math.pi * 0.25 + 0.5 * rad)) * WEB_MERCATOR_ORIGIN_SHIFT / math.pi
    return x_m, y_m


def _bounds_center_mercator(bounds_geojson_path: Path) -> tuple[float, float]:
    data = _load_json(bounds_geojson_path)
    features = data.get("features") or []
    if not features:
        raise SpatialValidationError(f"bounds.geojson contains no features: {bounds_geojson_path}")
    props = dict(features[0].get("properties") or {})
    bbox_value = props.get("bbox")
    if not isinstance(bbox_value, list) or len(bbox_value) < 4:
        raise SpatialValidationError(f"bounds.geojson lacks properties.bbox: {bounds_geojson_path}")
    return (
        0.5 * (float(bbox_value[0]) + float(bbox_value[2])),
        0.5 * (float(bbox_value[1]) + float(bbox_value[3])),
    )


def _geojson_coord_to_local_xy(coord: Sequence[Any], center_xy_m: tuple[float, float]) -> tuple[float, float]:
    if len(coord) < 2:
        raise SpatialValidationError(f"Invalid coordinate: {coord}")
    lon = float(coord[0])
    lat = float(coord[1])
    mx, my = _lonlat_to_web_mercator(lon, lat)
    return mx - center_xy_m[0], my - center_xy_m[1]


def _solve_affine(pairs: list[tuple[tuple[float, float], tuple[float, float]]]) -> GeoJsonBundleFit:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - numpy is available in this workspace.
        raise SpatialValidationError("numpy is required for GeoJSON traffic-bundle fitting") from exc

    if len(pairs) < 3:
        raise SpatialValidationError(f"Need at least 3 road/lane pairs for GeoJSON fitting; got {len(pairs)}")
    source = np.array([item[0] for item in pairs], dtype=float)
    target = np.array([item[1] for item in pairs], dtype=float)
    design = np.column_stack([source, np.ones(len(source))])
    matrix, *_ = np.linalg.lstsq(design, target, rcond=None)
    predicted = design @ matrix
    errors = np.linalg.norm(predicted - target, axis=1)
    return GeoJsonBundleFit(
        matrix=(
            (float(matrix[0][0]), float(matrix[0][1])),
            (float(matrix[1][0]), float(matrix[1][1])),
            (float(matrix[2][0]), float(matrix[2][1])),
        ),
        mean_error_m=float(errors.mean()),
        max_error_m=float(errors.max()),
        pair_count=len(pairs),
    )


def _load_geojson_bundle_fit(path: Path) -> GeoJsonBundleFit:
    payload = _load_json(path)
    matrix = payload.get("matrix")
    if not isinstance(matrix, list) or len(matrix) != 3:
        raise SpatialValidationError(f"Invalid road_geojson_transform matrix: {path}")
    return GeoJsonBundleFit(
        matrix=(
            (float(matrix[0][0]), float(matrix[0][1])),
            (float(matrix[1][0]), float(matrix[1][1])),
            (float(matrix[2][0]), float(matrix[2][1])),
        ),
        mean_error_m=float(payload.get("mean_error_m") or 0.0),
        max_error_m=float(payload.get("max_error_m") or 0.0),
        pair_count=int(payload.get("pair_count") or 0),
    )


def _load_first_center_samples(samples_csv: Path) -> dict[str, tuple[float, float]]:
    first_samples: dict[str, tuple[float, float]] = {}
    with samples_csv.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            edge_id = str(row.get("edge_id") or "")
            if not edge_id and row.get("road_id") not in (None, ""):
                edge_id = f"cg_edge_{int(float(row['road_id']))}"
            if edge_id and edge_id not in first_samples:
                first_samples[edge_id] = (float(row["x_m"]), float(row["y_m"]))
    return first_samples


def fit_geojson_to_bundle(
    *,
    road_geojson_path: Path,
    bounds_geojson_path: Path,
    lane_center_samples_csv: Path | None = None,
    road_center_samples_csv: Path | None = None,
    transform_json_path: Path | None = None,
    max_fit_error_m: float = 0.05,
) -> GeoJsonBundleFit:
    if transform_json_path is not None and transform_json_path.exists():
        fit = _load_geojson_bundle_fit(transform_json_path)
        if fit.max_error_m > max_fit_error_m:
            raise SpatialValidationError(
                f"Stored GeoJSON to traffic_bundle fit is not reliable: max_error={fit.max_error_m:.3f}m "
                f"> {max_fit_error_m:.3f}m pairs={fit.pair_count}"
            )
        return fit

    road = _load_json(road_geojson_path)
    center_xy = _bounds_center_mercator(bounds_geojson_path)
    samples_csv = road_center_samples_csv if road_center_samples_csv is not None and road_center_samples_csv.exists() else lane_center_samples_csv
    if samples_csv is None:
        raise SpatialValidationError("Either road_center_samples_csv or lane_center_samples_csv is required for GeoJSON fitting")
    first_samples = _load_first_center_samples(samples_csv)
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for index, feature in enumerate(road.get("features") or []):
        edge_id = f"cg_edge_{index}"
        lane_xy = first_samples.get(edge_id)
        geometry = dict(feature.get("geometry") or {})
        coords = geometry.get("coordinates") or []
        if lane_xy is None or str(geometry.get("type") or "") != "LineString" or not coords:
            continue
        pairs.append((_geojson_coord_to_local_xy(coords[0], center_xy), lane_xy))
    fit = _solve_affine(pairs)
    if fit.max_error_m > max_fit_error_m:
        raise SpatialValidationError(
            f"GeoJSON to traffic_bundle fit is not reliable: max_error={fit.max_error_m:.3f}m "
            f"> {max_fit_error_m:.3f}m pairs={fit.pair_count}"
        )
    return fit


def _transform_coordinate(coord: Sequence[Any], center_xy_m: tuple[float, float], fit: GeoJsonBundleFit) -> tuple[float, float]:
    local_x, local_y = _geojson_coord_to_local_xy(coord, center_xy_m)
    return fit.transform_local_xy(local_x, local_y)


def _transform_geojson_geometry(geometry: dict[str, Any], center_xy_m: tuple[float, float], fit: GeoJsonBundleFit):
    geom_type = str(geometry.get("type") or "")

    def coord_xy(raw: Sequence[Any]) -> tuple[float, float]:
        return _transform_coordinate(raw, center_xy_m, fit)

    if geom_type == "Polygon":
        rings = geometry.get("coordinates") or []
        if not rings:
            return None
        exterior = [coord_xy(item) for item in rings[0]]
        holes = [[coord_xy(item) for item in ring] for ring in rings[1:]]
        return Polygon(exterior, holes)
    if geom_type == "MultiPolygon":
        polygons = []
        for rings in geometry.get("coordinates") or []:
            if not rings:
                continue
            exterior = [coord_xy(item) for item in rings[0]]
            holes = [[coord_xy(item) for item in ring] for ring in rings[1:]]
            polygons.append(Polygon(exterior, holes))
        return unary_union([item for item in polygons if not item.is_empty]) if polygons else None
    if geom_type == "LineString":
        return LineString([coord_xy(item) for item in geometry.get("coordinates") or []])
    if geom_type == "MultiLineString":
        lines = [LineString([coord_xy(item) for item in group]) for group in geometry.get("coordinates") or []]
        return unary_union([item for item in lines if not item.is_empty]) if lines else None
    return shape(geometry)


def _load_geojson_union(path: Path, center_xy_m: tuple[float, float], fit: GeoJsonBundleFit):
    data = _load_json(path)
    geometries = []
    for feature in data.get("features") or []:
        geometry = _transform_geojson_geometry(dict(feature.get("geometry") or {}), center_xy_m, fit)
        if geometry is not None and not geometry.is_empty:
            geometries.append(geometry)
    if not geometries:
        raise SpatialValidationError(f"GeoJSON contains no usable geometries: {path}")
    return unary_union(geometries)


def lane_normal(sample: LaneSample) -> tuple[float, float]:
    yaw_rad = math.radians(sample.yaw_deg)
    return -math.sin(yaw_rad), math.cos(yaw_rad)


def side_sign_for_point(sample: LaneSample, point_enu_m: Sequence[float]) -> float:
    nx, ny = lane_normal(sample)
    dx = float(point_enu_m[0]) - sample.x_m
    dy = float(point_enu_m[1]) - sample.y_m
    return 1.0 if dx * nx + dy * ny >= 0.0 else -1.0


def offset_from_lane(sample: LaneSample, lateral_m: float, z_m: float = GROUND_Z_M) -> list[float]:
    nx, ny = lane_normal(sample)
    return [round(sample.x_m + lateral_m * nx, 3), round(sample.y_m + lateral_m * ny, 3), round(z_m, 3)]


def dist_xy(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _dedupe_points(points: Iterable[Sequence[float]]) -> list[list[float]]:
    result: list[list[float]] = []
    for raw in points:
        point = [round(float(raw[0]), 3), round(float(raw[1]), 3), round(float(raw[2] if len(raw) > 2 else GROUND_Z_M), 3)]
        if not result or dist_xy(result[-1], point) > 1e-3 or abs(result[-1][2] - point[2]) > 1e-3:
            result.append(point)
    return result


def _densify_segment(a: Sequence[float], b: Sequence[float], max_step_m: float = MAX_PEDESTRIAN_SEGMENT_M) -> list[list[float]]:
    distance = dist_xy(a, b)
    steps = max(1, int(math.ceil(distance / max_step_m)))
    points = []
    for index in range(steps + 1):
        t = index / float(steps)
        points.append(
            [
                round(float(a[0]) + (float(b[0]) - float(a[0])) * t, 3),
                round(float(a[1]) + (float(b[1]) - float(a[1])) * t, 3),
                round(float(a[2] if len(a) > 2 else GROUND_Z_M) + (float(b[2] if len(b) > 2 else GROUND_Z_M) - float(a[2] if len(a) > 2 else GROUND_Z_M)) * t, 3),
            ]
        )
    return points


class MapSpatialIndex:
    def __init__(
        self,
        *,
        project_root: Path = ROOT,
        map_package_path: Path = DEFAULT_MAP_PACKAGE,
        map_id: str = DEFAULT_MAP_ID,
    ) -> None:
        self.project_root = project_root
        self.map_id = map_id
        self.map_package_path = map_package_path
        self.map_package = _load_json(map_package_path)
        source_geojson = dict(self.map_package.get("source_geojson") or {})
        required_layers = {"road", "building", "water", "green", "block", "bounds"}
        missing_layers = required_layers - set(source_geojson)
        if missing_layers:
            raise SpatialValidationError(f"map_package source_geojson missing layers: {sorted(missing_layers)}")

        self.traffic_bundle_dir = _project_path(project_root, str(self.map_package["traffic_bundle_dir"]))
        self.lane_center_samples_path = self.traffic_bundle_dir / "lane_center_samples.csv"
        self.road_center_samples_path = self.traffic_bundle_dir / "road_center_samples.csv"
        self.road_geojson_transform_path = self.traffic_bundle_dir / "road_geojson_transform.json"
        self.lanes = LaneResolver(self.lane_center_samples_path)
        self.source_geojson_paths = {
            layer: _project_path(project_root, str(source_geojson[layer])) for layer in sorted(required_layers)
        }
        for layer, path in self.source_geojson_paths.items():
            if not path.exists():
                raise SpatialValidationError(f"map_package layer {layer} path does not exist: {path}")

        self.geojson_center_xy_m = _bounds_center_mercator(self.source_geojson_paths["bounds"])
        self.geojson_fit = fit_geojson_to_bundle(
            road_geojson_path=self.source_geojson_paths["road"],
            bounds_geojson_path=self.source_geojson_paths["bounds"],
            lane_center_samples_csv=self.lane_center_samples_path,
            road_center_samples_csv=self.road_center_samples_path,
            transform_json_path=self.road_geojson_transform_path,
        )
        self.bounds_union = _load_geojson_union(self.source_geojson_paths["bounds"], self.geojson_center_xy_m, self.geojson_fit)
        self.water_union = _load_geojson_union(self.source_geojson_paths["water"], self.geojson_center_xy_m, self.geojson_fit)
        self.building_union = _load_geojson_union(self.source_geojson_paths["building"], self.geojson_center_xy_m, self.geojson_fit)
        self.green_union = _load_geojson_union(self.source_geojson_paths["green"], self.geojson_center_xy_m, self.geojson_fit)
        self.block_union = _load_geojson_union(self.source_geojson_paths["block"], self.geojson_center_xy_m, self.geojson_fit)
        self.bounds_prepared = prep(self.bounds_union)
        self.water_prepared = prep(self.water_union)
        self.building_prepared = prep(self.building_union)
        self.green_prepared = prep(self.green_union)

    @classmethod
    def default(cls, project_root: Path = ROOT) -> "MapSpatialIndex":
        return cls(project_root=project_root, map_package_path=project_root / DEFAULT_MAP_PACKAGE.relative_to(ROOT))

    def nearest_lane_clearance(self, point_enu_m: Sequence[float]) -> float:
        sample = self.lanes.nearest(point_enu_m)
        return dist_xy(point_enu_m, [sample.x_m, sample.y_m, point_enu_m[2] if len(point_enu_m) > 2 else GROUND_Z_M])

    def _point_geometry(self, point_enu_m: Sequence[float]) -> Point:
        return Point(float(point_enu_m[0]), float(point_enu_m[1]))

    def validation_errors_for_point(
        self,
        point_enu_m: Sequence[float],
        *,
        context: str,
        allow_road: bool = False,
        allow_green: bool = False,
        road_buffer_m: float = PEDESTRIAN_ROAD_BUFFER_M,
    ) -> list[str]:
        point = self._point_geometry(point_enu_m)
        errors: list[str] = []
        if not self.bounds_prepared.covers(point):
            errors.append(f"{context} outside bounds.geojson: {list(point_enu_m)}")
        if self.water_prepared.covers(point):
            errors.append(f"{context} inside water.geojson: {list(point_enu_m)}")
        if self.building_prepared.covers(point):
            errors.append(f"{context} inside building.geojson: {list(point_enu_m)}")
        if not allow_green and self.green_prepared.covers(point):
            errors.append(f"{context} inside green.geojson without explicit green allowance: {list(point_enu_m)}")
        if not allow_road and self.nearest_lane_clearance(point_enu_m) < LANE_HALF_WIDTH_M + road_buffer_m:
            errors.append(f"{context} inside roadway clearance: {list(point_enu_m)}")
        return errors

    def validate_point(self, point_enu_m: Sequence[float], **kwargs: Any) -> None:
        errors = self.validation_errors_for_point(point_enu_m, **kwargs)
        if errors:
            raise SpatialValidationError("; ".join(errors))

    def validation_errors_for_segment(
        self,
        a_enu_m: Sequence[float],
        b_enu_m: Sequence[float],
        *,
        context: str,
        allow_road: bool = False,
        allow_green: bool = False,
        road_buffer_m: float = PEDESTRIAN_ROAD_BUFFER_M,
        sample_step_m: float = 2.0,
    ) -> list[str]:
        line = LineString([(float(a_enu_m[0]), float(a_enu_m[1])), (float(b_enu_m[0]), float(b_enu_m[1]))])
        errors: list[str] = []
        if not self.bounds_prepared.covers(line):
            errors.append(f"{context} segment leaves bounds.geojson")
        if self.water_prepared.intersects(line):
            errors.append(f"{context} segment intersects water.geojson")
        if self.building_prepared.intersects(line):
            errors.append(f"{context} segment intersects building.geojson")
        if not allow_green and self.green_prepared.intersects(line):
            errors.append(f"{context} segment intersects green.geojson without explicit green allowance")
        for point in _densify_segment(a_enu_m, b_enu_m, max(sample_step_m, 0.5)):
            for error in self.validation_errors_for_point(
                point,
                context=context,
                allow_road=allow_road,
                allow_green=allow_green,
                road_buffer_m=road_buffer_m,
            ):
                if "inside roadway clearance" in error:
                    errors.append(f"{context} segment enters roadway clearance")
                    return list(dict.fromkeys(errors))
                errors.append(error)
        return list(dict.fromkeys(errors))

    def validate_segment(self, a_enu_m: Sequence[float], b_enu_m: Sequence[float], **kwargs: Any) -> None:
        errors = self.validation_errors_for_segment(a_enu_m, b_enu_m, **kwargs)
        if errors:
            raise SpatialValidationError("; ".join(errors))

    def spawn_envelope_polygon(self, origin_enu_m: Sequence[float], extent_cm: Sequence[float]):
        half_x = float(extent_cm[0] if len(extent_cm) > 0 else 0.0) / 200.0
        half_y = float(extent_cm[1] if len(extent_cm) > 1 else 0.0) / 200.0
        return box(float(origin_enu_m[0]) - half_x, float(origin_enu_m[1]) - half_y, float(origin_enu_m[0]) + half_x, float(origin_enu_m[1]) + half_y)

    def validation_errors_for_spawn_envelope(
        self,
        origin_enu_m: Sequence[float],
        extent_cm: Sequence[float],
        *,
        context: str,
        allow_green: bool = False,
    ) -> list[str]:
        envelope = self.spawn_envelope_polygon(origin_enu_m, extent_cm)
        errors: list[str] = []
        if not self.bounds_prepared.covers(envelope):
            errors.append(f"{context} spawn envelope leaves bounds.geojson")
        if self.water_prepared.intersects(envelope):
            errors.append(f"{context} spawn envelope intersects water.geojson")
        if self.building_prepared.intersects(envelope):
            errors.append(f"{context} spawn envelope intersects building.geojson")
        if not allow_green and self.green_prepared.intersects(envelope):
            errors.append(f"{context} spawn envelope intersects green.geojson without explicit green allowance")
        half_extent_m = max(float(extent_cm[0] if len(extent_cm) > 0 else 0.0), float(extent_cm[1] if len(extent_cm) > 1 else 0.0)) / 200.0
        required = LANE_HALF_WIDTH_M + half_extent_m + CROWD_ROAD_BUFFER_M
        clearance = self.nearest_lane_clearance(origin_enu_m)
        if clearance < required:
            errors.append(f"{context} spawn envelope overlaps roadway clearance ({clearance:.2f}m < {required:.2f}m)")
        return errors

    def validate_spawn_envelope(self, origin_enu_m: Sequence[float], extent_cm: Sequence[float], **kwargs: Any) -> None:
        errors = self.validation_errors_for_spawn_envelope(origin_enu_m, extent_cm, **kwargs)
        if errors:
            raise SpatialValidationError("; ".join(errors))

    def _candidate_samples(self, hint_pos_enu_m: Sequence[float], edge_id_hint: str | None, s_hint: float | None) -> list[LaneSample]:
        samples: list[LaneSample] = []
        s_deltas = (0.0, -1.0, 1.0, -2.0, 2.0, -5.0, 5.0, -10.0, 10.0, -15.0, 15.0, -25.0, 25.0, -40.0, 40.0, -60.0, 60.0, -85.0, 85.0)
        nearest = self.lanes.nearest(hint_pos_enu_m)
        if edge_id_hint:
            base_s = float(s_hint or 0.0)
            min_s, max_s = self.lanes.edge_s_bounds(edge_id_hint)
            edge_nearest = (
                nearest
                if nearest.edge_id == edge_id_hint
                else self.lanes.resolve_edge_s(edge_id_hint, base_s)
            )
            for delta_s in s_deltas:
                samples.append(
                    self.lanes.interpolate_edge_s(
                        edge_id_hint,
                        edge_nearest.lane_id,
                        max(min_s, min(max_s, base_s + delta_s)),
                    )
                )
        if edge_id_hint:
            if nearest.edge_id != edge_id_hint:
                min_s, max_s = self.lanes.edge_s_bounds(nearest.edge_id)
                for delta_s in s_deltas[:9]:
                    samples.append(self.lanes.resolve_edge_s(nearest.edge_id, max(min_s, min(max_s, nearest.s_m + delta_s))))
            deduped: list[LaneSample] = []
            seen: set[tuple[str, float]] = set()
            for sample in samples:
                key = (sample.edge_id, round(sample.s_m, 3))
                if key not in seen:
                    seen.add(key)
                    deduped.append(sample)
            return deduped
        nearby_base_samples: list[LaneSample] = [nearest]
        seen_edges = {nearest.edge_id}
        for sample in sorted(
            self.lanes.samples,
            key=lambda item: (item.x_m - float(hint_pos_enu_m[0])) ** 2 + (item.y_m - float(hint_pos_enu_m[1])) ** 2,
        ):
            if sample.edge_id in seen_edges:
                continue
            seen_edges.add(sample.edge_id)
            nearby_base_samples.append(sample)
            if len(nearby_base_samples) >= 16:
                break
        for base in nearby_base_samples:
            min_s, max_s = self.lanes.edge_s_bounds(base.edge_id)
            for delta_s in s_deltas:
                samples.append(self.lanes.resolve_edge_s(base.edge_id, max(min_s, min(max_s, base.s_m + delta_s))))
        deduped: list[LaneSample] = []
        seen: set[tuple[str, float]] = set()
        for sample in samples:
            key = (sample.edge_id, round(sample.s_m, 3))
            if key not in seen:
                seen.add(key)
                deduped.append(sample)
        return deduped

    def plan_sidewalk_anchor(
        self,
        desired_pos_enu_m: Sequence[float],
        *,
        edge_id_hint: str | None = None,
        s_hint: float | None = None,
        offset_from_curb_m: float = SIDEWALK_MIN_OFFSET_FROM_CURB_M,
        allow_green: bool = False,
        placement_semantics: str = "sidewalk",
    ) -> PlannedSidewalkAnchor:
        preferred_sample = self.lanes.nearest(desired_pos_enu_m)
        preferred_sign = side_sign_for_point(preferred_sample, desired_pos_enu_m)
        signs = [preferred_sign, -preferred_sign]
        base_offset = max(abs(float(offset_from_curb_m)), SIDEWALK_MIN_OFFSET_FROM_CURB_M)
        offsets = [base_offset, base_offset + 0.8, base_offset + 1.6, base_offset + 3.0, base_offset + 5.0, base_offset + 8.0]
        errors: list[str] = []
        for sample in self._candidate_samples(desired_pos_enu_m, edge_id_hint, s_hint):
            for sign in signs:
                for offset in offsets:
                    lateral = sign * (LANE_HALF_WIDTH_M + offset)
                    point = offset_from_lane(sample, lateral, float(desired_pos_enu_m[2] if len(desired_pos_enu_m) > 2 else GROUND_Z_M))
                    point_errors = self.validation_errors_for_point(
                        point,
                        context=f"{placement_semantics} candidate",
                        allow_road=False,
                        allow_green=allow_green,
                    )
                    if not point_errors:
                        return PlannedSidewalkAnchor(
                            position_enu_m=point,
                            sample=sample,
                            offset_from_curb_m=round(offset, 3),
                            resolved_lateral_from_center_m=round(lateral, 3),
                            placement_semantics=placement_semantics,
                        )
                    errors.extend(point_errors[:2])
        raise SpatialValidationError(
            f"No legal sidewalk anchor for desired={list(desired_pos_enu_m)} edge_hint={edge_id_hint} "
            f"s_hint={s_hint}; first_errors={errors[:6]}"
        )

    def plan_crowd_zone(
        self,
        desired_origin_enu_m: Sequence[float],
        extent_cm: Sequence[float],
        *,
        allow_green: bool = True,
    ) -> list[float]:
        half_extent_m = max(float(extent_cm[0] if len(extent_cm) > 0 else 0.0), float(extent_cm[1] if len(extent_cm) > 1 else 0.0)) / 200.0
        base_offset = max(GATHERING_MIN_OFFSET_FROM_CURB_M, half_extent_m + 1.0)
        errors: list[str] = []
        for extra in (0.0, 1.5, 3.0, 5.0, 8.0, 12.0):
            try:
                anchor = self.plan_sidewalk_anchor(
                    desired_origin_enu_m,
                    offset_from_curb_m=base_offset + extra,
                    allow_green=allow_green,
                    placement_semantics="gathering_zone",
                )
                self.validate_spawn_envelope(
                    anchor.position_enu_m,
                    extent_cm,
                    context="gathering_zone candidate",
                    allow_green=allow_green,
                )
                return anchor.position_enu_m
            except SpatialValidationError as exc:
                errors.append(str(exc))
        grid_steps = (0.0, 8.0, -8.0, 16.0, -16.0, 28.0, -28.0, 42.0, -42.0, 60.0, -60.0, 85.0, -85.0)
        candidates: list[list[float]] = []
        for dx in grid_steps:
            for dy in grid_steps:
                candidates.append(
                    [
                        round(float(desired_origin_enu_m[0]) + dx, 3),
                        round(float(desired_origin_enu_m[1]) + dy, 3),
                        round(float(desired_origin_enu_m[2] if len(desired_origin_enu_m) > 2 else GROUND_Z_M), 3),
                    ]
                )
        candidates.sort(key=lambda item: dist_xy(item, desired_origin_enu_m))
        for candidate in candidates:
            envelope_errors = self.validation_errors_for_spawn_envelope(
                candidate,
                extent_cm,
                context="gathering_zone candidate",
                allow_green=allow_green,
            )
            if not envelope_errors:
                return candidate
            errors.extend(envelope_errors[:2])
        raise SpatialValidationError(
            f"No legal crowd zone for desired={list(desired_origin_enu_m)} extent_cm={list(extent_cm)}; "
            f"first_errors={errors[:4]}"
        )

    def plan_crossing_route(
        self,
        desired_pos_enu_m: Sequence[float],
        *,
        offset_from_curb_m: float = SIDEWALK_MIN_OFFSET_FROM_CURB_M,
    ) -> PlannedCrossing:
        preferred_sample = self.lanes.nearest(desired_pos_enu_m)
        preferred_sign = side_sign_for_point(preferred_sample, desired_pos_enu_m)
        signs = [preferred_sign, -preferred_sign]
        base_offset = max(abs(float(offset_from_curb_m)), SIDEWALK_MIN_OFFSET_FROM_CURB_M)
        errors: list[str] = []
        for sample in self._candidate_samples(desired_pos_enu_m, preferred_sample.edge_id, preferred_sample.s_m):
            for sign in signs:
                for offset in (base_offset, base_offset + 0.8, base_offset + 1.6, base_offset + 3.0):
                    lateral = sign * (LANE_HALF_WIDTH_M + offset)
                    start = offset_from_lane(sample, lateral, GROUND_Z_M)
                    road = offset_from_lane(sample, 0.0, GROUND_Z_M)
                    opposite = offset_from_lane(sample, -lateral, GROUND_Z_M)
                    checks = []
                    checks.extend(self.validation_errors_for_point(start, context="crossing start", allow_road=False, allow_green=False))
                    checks.extend(self.validation_errors_for_point(opposite, context="crossing opposite curb", allow_road=False, allow_green=False))
                    checks.extend(self.validation_errors_for_segment(start, road, context="crossing curb to road", allow_road=True, allow_green=False))
                    checks.extend(self.validation_errors_for_segment(road, opposite, context="crossing road to opposite curb", allow_road=True, allow_green=False))
                    if not checks:
                        return PlannedCrossing(
                            start_position_enu_m=start,
                            roadway_center_position_enu_m=road,
                            opposite_curb_position_enu_m=opposite,
                            sample=sample,
                            offset_from_curb_m=round(offset, 3),
                            resolved_lateral_from_center_m=round(lateral, 3),
                        )
                    errors.extend(checks[:2])
        raise SpatialValidationError(
            f"No legal crossing route for desired={list(desired_pos_enu_m)}; first_errors={errors[:6]}"
        )

    def _lane_side_path(self, start: Sequence[float], end: Sequence[float], *, allow_green: bool, context: str) -> list[list[float]]:
        start_sample = self.lanes.nearest(start)
        end_sample = self.lanes.nearest(end)
        if start_sample.edge_id != end_sample.edge_id:
            raise SpatialValidationError(f"{context}: cannot plan lane-side path across disconnected edges {start_sample.edge_id}->{end_sample.edge_id}")
        sign = side_sign_for_point(start_sample, start)
        if sign != side_sign_for_point(end_sample, end):
            raise SpatialValidationError(f"{context}: start/end are on opposite lane sides")
        start_clearance = max(self.nearest_lane_clearance(start), LANE_HALF_WIDTH_M + SIDEWALK_MIN_OFFSET_FROM_CURB_M)
        end_clearance = max(self.nearest_lane_clearance(end), LANE_HALF_WIDTH_M + SIDEWALK_MIN_OFFSET_FROM_CURB_M)
        s0 = start_sample.s_m
        s1 = end_sample.s_m
        distance_s = abs(s1 - s0)
        steps = max(1, int(math.ceil(distance_s / MAX_PEDESTRIAN_SEGMENT_M)))
        base_offset = max(SIDEWALK_MIN_OFFSET_FROM_CURB_M, 0.5 * (start_clearance + end_clearance) - LANE_HALF_WIDTH_M)
        errors: list[str] = []
        for offset in (base_offset, base_offset + 1.0, base_offset + 2.0, base_offset + 4.0, base_offset + 8.0, base_offset + 12.0):
            points: list[list[float]] = [list(start)]
            for index in range(1, steps):
                s = s0 + (s1 - s0) * index / float(steps)
                sample = self.lanes.resolve_edge_s(start_sample.edge_id, s)
                points.append(offset_from_lane(sample, sign * (LANE_HALF_WIDTH_M + offset), GROUND_Z_M))
            points.append(list(end))
            points = _dedupe_points(points)
            segment_errors: list[str] = []
            for index, (a, b) in enumerate(zip(points, points[1:])):
                segment_errors.extend(
                    self.validation_errors_for_segment(
                        a,
                        b,
                        context=f"{context} lane-side segment {index}",
                        allow_road=False,
                        allow_green=allow_green,
                    )
                )
            if not segment_errors:
                return points
            errors.extend(segment_errors[:3])
        raise SpatialValidationError(f"{context}: no valid lane-side offset; first_errors={errors[:6]}")

    def plan_sidewalk_route(
        self,
        waypoints_enu_m: Sequence[Sequence[float]],
        *,
        allow_green: bool = False,
        context: str = "pedestrian sidewalk route",
    ) -> list[list[float]]:
        raw_points = _dedupe_points(waypoints_enu_m)
        if len(raw_points) < 2:
            return raw_points
        planned: list[list[float]] = [raw_points[0]]
        for segment_index, (a, b) in enumerate(zip(raw_points, raw_points[1:])):
            direct = _densify_segment(a, b)
            direct_errors = []
            for index, (pa, pb) in enumerate(zip(direct, direct[1:])):
                direct_errors.extend(
                    self.validation_errors_for_segment(
                        pa,
                        pb,
                        context=f"{context} direct segment {segment_index}.{index}",
                        allow_road=False,
                        allow_green=allow_green,
                    )
                )
            if not direct_errors:
                segment_points = direct
            else:
                segment_points = self._lane_side_path(a, b, allow_green=allow_green, context=f"{context} {segment_index}")
            planned.extend(segment_points[1:])
        return _dedupe_points(planned)

    def plan_crossing_path(
        self,
        waypoints_enu_m: Sequence[Sequence[float]],
        *,
        context: str = "pedestrian crossing route",
    ) -> list[list[float]]:
        raw_points = _dedupe_points(waypoints_enu_m)
        if len(raw_points) < 2:
            return raw_points
        planned: list[list[float]] = [raw_points[0]]
        for segment_index, (a, b) in enumerate(zip(raw_points, raw_points[1:])):
            segment = _densify_segment(a, b)
            for index, (pa, pb) in enumerate(zip(segment, segment[1:])):
                self.validate_segment(
                    pa,
                    pb,
                    context=f"{context} segment {segment_index}.{index}",
                    allow_road=True,
                    allow_green=False,
                )
            planned.extend(segment[1:])
        return _dedupe_points(planned)
