import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { MeshPackManifest } from "./osm2world/pack";
import { AssetResolver } from "./asset-resolver";
import { resolveStaticStreetLampClearance, resolveStreetLampClearance, type StreetLampLocation, type StreetTrafficData } from "./city-street-clearance";
import type { StaticSignalInventory, VerifiedVisualAssets } from "./city-authoring-api";
import type { CityTimeOfDay } from "./city-lighting-calibration";
import { CITY_SUBSYSTEM_LIGHTING } from "./city-subsystem-lighting";
import { displaySurfaceSha256 } from "./city-surface-identity";
import { applySurfaceWetness, type SurfaceWetnessOptions,
  type SurfaceWetnessUniforms } from "./city-surface-wetness";

type RoadKind = "motor" | "shared" | "cycle" | "walk";
type Point = readonly [number, number];
interface RoadLane {
  readonly id: string;
  readonly width: number;
  readonly kind: RoadKind;
  readonly shape: readonly Point[];
  readonly outer?: boolean;
  readonly divider?: boolean;
}
interface RoadJunction { readonly id: string; readonly kind: "motor" | "walk"; readonly shape: readonly Point[]; }
interface RoadCrossing { readonly id: string; readonly width: number; readonly shape: readonly Point[]; }
interface RoadbedPolygon { readonly outline: readonly Point[]; readonly holes: readonly (readonly Point[])[]; }
interface RoadSurfaceMaterial {
  readonly texture: string;
  readonly source: string;
  readonly texture_asset?: { readonly url: string; readonly sha256: string; readonly size_bytes: number } | null;
  readonly uv_repeat_m?: readonly [number, number];
  readonly source_way_count?: number;
  readonly source_way_triangles?: number;
  readonly source_junction_count?: number;
  readonly source_junction_triangles?: number;
  readonly source_triangle_count?: number;
  readonly rendered_area_m2?: number;
}
interface RoadMarkingWidths {
  readonly lane_edge: number;
  readonly lane_divider: number;
  readonly derived_direction_guide: number;
}
interface DirectionGuide {
  readonly id: string;
  readonly source_way_id: string;
  readonly edge_ids: readonly [string, string];
  readonly marking: "derived_direction_guide";
  readonly source_kind: "sumo-lane-topology-not-surveyed-marking";
  readonly width_m: number;
  readonly shape: readonly Point[];
}
interface StreetMarking {
  readonly kind: string;
  readonly color: "white" | "yellow";
  readonly pattern: "solid" | "dashed";
  readonly width_m: number;
  readonly shape: readonly Point[];
}
interface StreetLayout {
  readonly source_kind: "sumo-topology-with-explicit-derived-urban-design";
  readonly walking_area_source_count: number;
  readonly walking_area_count: number;
  readonly omitted_degenerate_walking_areas: readonly { readonly id: string; readonly reason: string }[];
  readonly sidewalk_provenance: Readonly<Record<string, unknown>>;
  readonly sidewalk_stats: Readonly<Record<string, number>>;
  readonly road_height_m: number;
  readonly sidewalk_height_m: number;
  readonly pavement_edges: readonly (readonly Point[])[];
  readonly derived_walkbed: readonly RoadbedPolygon[];
  readonly median_beds: readonly RoadbedPolygon[];
  readonly markings: readonly StreetMarking[];
  readonly arrows: readonly { readonly outline: readonly Point[] }[];
}
type StreetLamp = StreetLampLocation;
interface RoadData {
  readonly schema_version: "aero-bench.city-road-preview/v2" | "aero-bench.city-road-preview/v3"
    | "aero-bench.city-static-road/v1";
  readonly source_network_sha256: string;
  readonly mesh_pack_source_sha256: string;
  /** Workspace roads (v2, static v1) bind a building placement; canonical v3 roads bind a source context. */
  readonly building_placement_sha256?: string;
  readonly source_context?: Readonly<Record<string, unknown>>;
  readonly displayed_surface_sha256: string;
  readonly road_surface_materials: Readonly<{ asphalt: RoadSurfaceMaterial; concrete: RoadSurfaceMaterial }>;
  readonly concrete_roadbed_area_m2: number;
  readonly roadbed_area_m2: number;
  readonly walkbed_area_m2: number;
  readonly internal_lane_count: number;
  readonly roadbed: readonly RoadbedPolygon[];
  readonly concrete_roadbed: readonly RoadbedPolygon[];
  readonly walkbed: readonly RoadbedPolygon[];
  readonly curb_edges: readonly (readonly Point[])[];
  readonly signal_road_coverage: Readonly<Record<string, number>>;
  readonly lanes: readonly RoadLane[];
  readonly marking_widths_m: RoadMarkingWidths;
  readonly direction_guides: readonly DirectionGuide[];
  readonly junctions: readonly RoadJunction[];
  readonly crossings: readonly RoadCrossing[];
  readonly street_lamps: readonly StreetLamp[];
  readonly street_layout: StreetLayout;
}

export interface VerifiedStaticRoadInput {
  readonly value: unknown;
  readonly sourceOsmSha256: string;
  readonly packManifestSha256: string;
  readonly signalInventorySha256: string;
  readonly signals: StaticSignalInventory;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validPoint(value: unknown): value is Point {
  return Array.isArray(value) && value.length === 2
    && value.every(coordinate => typeof coordinate === "number" && Number.isFinite(coordinate));
}

function validPolygon(value: unknown): value is RoadbedPolygon {
  return isRecord(value) && Array.isArray(value.outline) && value.outline.length >= 3
    && value.outline.every(validPoint) && Array.isArray(value.holes)
    && value.holes.every(hole => Array.isArray(hole) && hole.length >= 3 && hole.every(validPoint));
}

function validSurfaceMaterial(value: unknown, concrete: boolean, staticRoad = false): value is RoadSurfaceMaterial {
  if (!isRecord(value) || typeof value.texture !== "string" || value.texture.length === 0
      || typeof value.source !== "string" || value.source.length === 0) return false;
  if (!concrete) return true;
  const asset = value.texture_asset;
  const validAsset = asset === null || (isRecord(asset)
    && typeof asset.url === "string"
    && (staticRoad
      ? /^\/authoring\/v1\/scenes\/[a-f0-9]{64}\/pack\/assets\/[a-f0-9]{64}$/.test(asset.url)
      : /^\/osm2world\/packs\/[a-zA-Z0-9_.-]+\/assets\/[a-f0-9]{64}$/.test(asset.url))
    && typeof asset.sha256 === "string" && /^[a-f0-9]{64}$/.test(asset.sha256)
    && asset.url.endsWith(asset.sha256)
    && Number.isSafeInteger(asset.size_bytes) && Number(asset.size_bytes) > 0);
  return validAsset
    && Array.isArray(value.uv_repeat_m) && value.uv_repeat_m.length === 2
    && value.uv_repeat_m.every(repeat => typeof repeat === "number" && Number.isFinite(repeat) && repeat > 0)
    && Number.isSafeInteger(value.source_way_count) && Number(value.source_way_count) >= 0
    && Number.isSafeInteger(value.source_way_triangles) && Number(value.source_way_triangles) >= 0
    && Number.isSafeInteger(value.source_junction_count) && Number(value.source_junction_count) >= 0
    && Number.isSafeInteger(value.source_junction_triangles) && Number(value.source_junction_triangles) >= 0
    && Number.isSafeInteger(value.source_triangle_count) && Number(value.source_triangle_count) >= 0
    && typeof value.rendered_area_m2 === "number" && Number.isFinite(value.rendered_area_m2)
    && value.rendered_area_m2 >= 0;
}

/** Bind a road material to the independently verified source pack, before any fetch. */
export function verifiedConcreteAsset(material: RoadSurfaceMaterial,
    pack: Pick<MeshPackManifest, "textures" | "batches">, packBaseUrl: string):
    NonNullable<RoadSurfaceMaterial["texture_asset"]> {
  const expected = pack.textures[material.texture];
  const actual = material.texture_asset;
  if (expected === undefined || actual == null || !packBaseUrl.endsWith("/")
      || !pack.batches.some(batch => batch.layer === "roads"
        && batch.material.base_color_texture === material.texture)
      || actual.sha256 !== expected.sha256 || actual.size_bytes !== expected.size_bytes
      || actual.url !== `${packBaseUrl}assets/${expected.sha256}`) {
    throw new Error("Concrete road texture differs from the verified mesh-pack material");
  }
  return actual;
}

async function loadVerifiedConcreteTexture(asset: NonNullable<RoadSurfaceMaterial["texture_asset"]>):
    Promise<THREE.Texture> {
  const packRoot = new URL("../", new URL(asset.url, window.location.href));
  const resolver = new AssetResolver({ baseHref: packRoot.href });
  try {
    const resolved = await resolver.fetchVerified(`assets/${asset.sha256}`, {
      sha256: asset.sha256, sizeBytes: asset.size_bytes, mediaType: "image/jpeg",
    });
    const texture = await new THREE.TextureLoader().loadAsync(resolved.url);
    resolver.dispose();
    return texture;
  } catch (error) {
    resolver.dispose();
    throw error;
  }
}

/** Reject stale v1 previews and malformed v2 marking/surface contracts at the loader boundary. */
function validateCityRoadGeometry(value: unknown, expectedVersion: RoadData["schema_version"]): RoadData {
  if (!isRecord(value) || value.schema_version !== expectedVersion) {
    throw new Error(expectedVersion === "aero-bench.city-static-road/v1"
      ? "Selected city road must use the static v1 road-surface and marking contract"
      : expectedVersion === "aero-bench.city-road-preview/v3"
        ? "Canonical road must use the v3 road-surface, source-context and marking contract"
        : "SUMO road preview must use the current v2 road-surface and marking contract");
  }
  const canonical = expectedVersion === "aero-bench.city-road-preview/v3";
  const hashFields = ["source_network_sha256", "mesh_pack_source_sha256", "displayed_surface_sha256",
    ...(canonical ? [] : ["building_placement_sha256"] as const)] as const;
  if (hashFields.some(field => typeof value[field] !== "string" || !/^[a-f0-9]{64}$/.test(value[field] as string))) {
    throw new Error("SUMO road preview contains an invalid source digest");
  }
  if (canonical && (!isRecord(value.source_context) || Object.hasOwn(value, "building_placement_sha256")
      || !isRecord(value.physical_clearance) || value.physical_clearance.status !== "PASS")) {
    throw new Error("Canonical road lacks its source context or physical clearance PASS");
  }
  if (expectedVersion === "aero-bench.city-static-road/v1") {
    if (["source_osm_sha256", "mesh_pack_manifest_sha256", "signal_inventory_sha256"].some(field =>
      typeof value[field] !== "string" || !/^[a-f0-9]{64}$/.test(value[field] as string))
      || ["traffic_recorded_building_placement_sha256", "frames", "flight", "traffic"].some(field =>
        Object.hasOwn(value, field))) {
      throw new Error("Selected city road is missing static provenance or contains recorded traffic fields");
    }
  }
  if (!isRecord(value.road_surface_materials)
      || !validSurfaceMaterial(value.road_surface_materials.asphalt, false)
      || !validSurfaceMaterial(value.road_surface_materials.concrete, true,
        expectedVersion === "aero-bench.city-static-road/v1")
      || !Array.isArray(value.roadbed) || value.roadbed.length === 0 || !value.roadbed.every(validPolygon)
      || !Array.isArray(value.concrete_roadbed) || !value.concrete_roadbed.every(validPolygon)
      || !Array.isArray(value.walkbed) || value.walkbed.length === 0 || !value.walkbed.every(validPolygon)) {
    throw new Error("SUMO road preview is missing verified asphalt, concrete, or walking surfaces");
  }
  const concreteMaterial = value.road_surface_materials.concrete as RoadSurfaceMaterial;
  if (value.concrete_roadbed.length > 0 && concreteMaterial.texture_asset == null) {
    throw new Error("SUMO concrete road surface has no manifest-pinned texture asset");
  }
  const widths = value.marking_widths_m;
  if (!isRecord(widths) || !["lane_edge", "lane_divider", "derived_direction_guide"].every(key => {
    const width = widths[key];
    return typeof width === "number" && Number.isFinite(width) && width >= 0.12 && width <= 0.15;
  })) {
    throw new Error("SUMO road preview has missing or implausible lane-marking widths");
  }
  if (!Array.isArray(value.direction_guides) || !value.direction_guides.every(separator =>
    isRecord(separator) && typeof separator.id === "string" && separator.id.length > 0
      && typeof separator.source_way_id === "string" && /^w-?\d+$/.test(separator.source_way_id)
      && Array.isArray(separator.edge_ids) && separator.edge_ids.length === 2
      && separator.edge_ids.every(edgeId => typeof edgeId === "string" && edgeId.length > 0)
      && separator.edge_ids[0] !== separator.edge_ids[1]
      && separator.marking === "derived_direction_guide"
      && separator.source_kind === "sumo-lane-topology-not-surveyed-marking"
      && typeof separator.width_m === "number" && Number.isFinite(separator.width_m)
      && separator.width_m >= 0.12 && separator.width_m <= 0.15
      && Array.isArray(separator.shape) && separator.shape.length >= 2 && separator.shape.every(validPoint))) {
    throw new Error("SUMO road preview has an invalid derived direction guide");
  }
  if (!Array.isArray(value.lanes) || value.lanes.length === 0
      || !Array.isArray(value.curb_edges) || !value.curb_edges.every(edge =>
        Array.isArray(edge) && edge.length >= 2 && edge.every(validPoint))
      || !Array.isArray(value.junctions)
      || !Array.isArray(value.crossings) || !Array.isArray(value.street_lamps)
      || typeof value.roadbed_area_m2 !== "number" || !Number.isFinite(value.roadbed_area_m2)
      || typeof value.walkbed_area_m2 !== "number" || !Number.isFinite(value.walkbed_area_m2)
      || typeof value.concrete_roadbed_area_m2 !== "number" || !Number.isFinite(value.concrete_roadbed_area_m2)
      || typeof value.internal_lane_count !== "number" || !Number.isSafeInteger(value.internal_lane_count)) {
    throw new Error("SUMO road preview omits required v2 road geometry");
  }
  const layout = value.street_layout;
  if (!isRecord(layout) || layout.source_kind !== "sumo-topology-with-explicit-derived-urban-design"
      || layout.road_height_m !== .075 || layout.sidewalk_height_m !== .225
      || !Array.isArray(layout.pavement_edges) || !layout.pavement_edges.every(edge =>
        Array.isArray(edge) && edge.length >= 2 && edge.every(validPoint))
      || !Array.isArray(layout.derived_walkbed) || !layout.derived_walkbed.every(validPolygon)
      || !Array.isArray(layout.median_beds) || !layout.median_beds.every(validPolygon)
      || !Array.isArray(layout.markings) || !layout.markings.every(mark => isRecord(mark)
        && typeof mark.kind === "string" && ["white", "yellow"].includes(String(mark.color))
        && ["solid", "dashed"].includes(String(mark.pattern))
        && typeof mark.width_m === "number" && Number.isFinite(mark.width_m)
        && mark.width_m > 0 && mark.width_m <= .5
        && Array.isArray(mark.shape) && mark.shape.length >= 2 && mark.shape.every(validPoint))
      || !Array.isArray(layout.arrows) || !layout.arrows.every(arrow => isRecord(arrow)
        && Array.isArray(arrow.outline) && arrow.outline.length >= 3 && arrow.outline.every(validPoint))) {
    throw new Error("SUMO road preview lacks the explicit urban street layout");
  }
  const omissions = layout.omitted_degenerate_walking_areas;
  const provenance = layout.sidewalk_provenance, statistics = layout.sidewalk_stats;
  if (!Number.isSafeInteger(layout.walking_area_source_count) || Number(layout.walking_area_source_count) < 0
      || !Number.isSafeInteger(layout.walking_area_count) || Number(layout.walking_area_count) < 0
      || !Array.isArray(omissions) || !omissions.every(item => isRecord(item)
        && typeof item.id === "string" && item.id.length > 0
        && ["fewer_than_three_vertices", "degenerate_area"].includes(String(item.reason)))
      || new Set(omissions.map(item => item.id)).size !== omissions.length
      || layout.walking_area_source_count !== Number(layout.walking_area_count) + omissions.length
      || !isRecord(provenance) || provenance.source_kind !== "derived_osm_eligible_corridor_visual_geometry"
      || provenance.sumo_lane_topology_modified !== false
      || provenance.surface_precision_normalized !== true
      || provenance.sidewalk_kind !== "derived_paved_sidewalk_not_surveyed"
      || provenance.median_kind !== "derived_paved_narrow_enclosed_strip_not_surveyed"
      || provenance.crossing_cuts_affect !== "curb_lines_only"
      || !isRecord(statistics) || !["derived_walkbed_area_m2", "median_beds_area_m2",
        "sidewalk_width_m", "building_clearance_m", "vehicle_clearance_m", "precision_grid_m",
        "effective_building_clearance_m", "effective_vehicle_clearance_m", "raw_roadbed_area_m2",
        "roadbed_normalization_symmetric_difference_m2", "precision_clearance_margin_m",
        "road_walk_separation_m", "normalized_roadbed_area_m2", "normalized_source_walkbed_area_m2"].every(key =>
          typeof statistics[key] === "number" && Number.isFinite(statistics[key]) && Number(statistics[key]) >= 0)) {
    throw new Error("Street layout provenance or area inventory is invalid");
  }
  if (statistics.precision_grid_m !== .001 || statistics.precision_clearance_margin_m !== .003
      || statistics.road_walk_separation_m !== .003
      || Math.abs(Number(statistics.effective_building_clearance_m)
        - Number(statistics.building_clearance_m) - .003) > 1e-9
      || Math.abs(Number(statistics.effective_vehicle_clearance_m)
        - Number(statistics.vehicle_clearance_m) - .003) > 1e-9) {
    throw new Error("Street surface precision policy is inconsistent");
  }
  return value as unknown as RoadData;
}

export function validateCityRoadPreviewPayload(value: unknown): RoadData {
  return validateCityRoadGeometry(value, "aero-bench.city-road-preview/v2");
}

export function validateCanonicalCityRoadPayload(value: unknown): RoadData {
  return validateCityRoadGeometry(value, "aero-bench.city-road-preview/v3");
}

export function validateStaticCityRoadPayload(value: unknown): RoadData {
  return validateCityRoadGeometry(value, "aero-bench.city-static-road/v1");
}

interface Draft { readonly positions: number[]; readonly uvs: number[]; readonly indices: number[]; }
function draft(): Draft { return { positions: [], uvs: [], indices: [] }; }

/** Convert the shared 5 m road UVs to the concrete texture's physical repeat dimensions. */
export function meterTextureUvs(worldUvs: readonly number[], repeatMeters: readonly [number, number]): number[] {
  if (worldUvs.length % 2 !== 0 || repeatMeters.some(value => !Number.isFinite(value) || value <= 0)) {
    throw new Error("Concrete road texture repeat dimensions are invalid");
  }
  return worldUvs.map((coordinate, index) => {
    if (!Number.isFinite(coordinate)) throw new Error("Concrete road UV coordinate is not finite");
    return coordinate * (index % 2 === 0 ? 5 / repeatMeters[0] : 5 / repeatMeters[1]);
  });
}

function vertex(target: Draft, x: number, y: number, z: number): number {
  const index = target.positions.length / 3;
  target.positions.push(x, y, z);
  target.uvs.push(x / 5, z / 5);
  return index;
}

function quad(target: Draft, start: Point, end: Point, width: number, y: number): void {
  const dx = end[0] - start[0], dz = end[1] - start[1];
  const length = Math.hypot(dx, dz);
  if (length < 0.01) return;
  const sideX = -dz / length * width / 2, sideZ = dx / length * width / 2;
  const first = vertex(target, start[0] + sideX, y, start[1] + sideZ);
  vertex(target, start[0] - sideX, y, start[1] - sideZ);
  vertex(target, end[0] + sideX, y, end[1] + sideZ);
  vertex(target, end[0] - sideX, y, end[1] - sideZ);
  target.indices.push(first, first + 2, first + 1, first + 1, first + 2, first + 3);
}

export function laneOffsets(shape: readonly Point[], width: number): { left: Point[]; right: Point[] } {
  // Map axes are east/up/south: a left turn from east points toward negative Z.
  const left: Point[] = [], right: Point[] = [];
  for (let index = 0; index < shape.length; index++) {
    const prior = shape[Math.max(0, index - 1)]!, current = shape[index]!;
    const next = shape[Math.min(shape.length - 1, index + 1)]!;
    const beforeLength = Math.hypot(current[0] - prior[0], current[1] - prior[1]);
    const afterLength = Math.hypot(next[0] - current[0], next[1] - current[1]);
    const beforeX = beforeLength ? (current[1] - prior[1]) / beforeLength : 0;
    const beforeZ = beforeLength ? -(current[0] - prior[0]) / beforeLength : 0;
    const afterX = afterLength ? (next[1] - current[1]) / afterLength : 0;
    const afterZ = afterLength ? -(next[0] - current[0]) / afterLength : 0;
    const tangentX = beforeX + afterX, tangentZ = beforeZ + afterZ;
    const tangentLength = Math.hypot(tangentX, tangentZ);
    const sideX = tangentLength > 0.01 ? tangentX / tangentLength : beforeX || afterX;
    const sideZ = tangentLength > 0.01 ? tangentZ / tangentLength : beforeZ || afterZ;
    const normalX = afterLength ? afterX : beforeX;
    const normalZ = afterLength ? afterZ : beforeZ;
    const miter = Math.min(width, width / (2 * Math.max(0.55, sideX * normalX + sideZ * normalZ)));
    left.push([current[0] + sideX * miter, current[1] + sideZ * miter]);
    right.push([current[0] - sideX * miter, current[1] - sideZ * miter]);
  }
  return { left, right };
}

/** Preserve ribbon coordinates while orienting each top face toward positive Y.
 * Short source segments at sharp joins can reverse one triangle of a quad; a
 * fixed winding then cancels neighbouring vertex normals and creates dark chips. */
export function ribbonTopTriangles(left: readonly Point[], right: readonly Point[]): number[] {
  if (left.length !== right.length || left.length < 2) throw new Error("Ribbon offset arrays differ or are incomplete");
  const points = left.flatMap((point, index) => [point, right[index]!]);
  const indices: number[] = [];
  const append = (a: number, b: number, c: number): void => {
    const p = points[a]!, q = points[b]!, r = points[c]!;
    const normalY = (q[1] - p[1]) * (r[0] - p[0]) - (q[0] - p[0]) * (r[1] - p[1]);
    indices.push(a, normalY < 0 ? c : b, normalY < 0 ? b : c);
  };
  for (let index = 0; index < left.length - 1; index++) {
    const offset = index * 2;
    append(offset, offset + 1, offset + 2);
    append(offset + 1, offset + 3, offset + 2);
  }
  return indices;
}

function ribbon(target: Draft, shape: readonly Point[], width: number, y: number):
    { left: Point[]; right: Point[] } {
  const offsets = laneOffsets(shape, width);
  const first = target.positions.length / 3;
  for (let index = 0; index < shape.length; index++) {
    vertex(target, offsets.left[index]![0], y, offsets.left[index]![1]);
    vertex(target, offsets.right[index]![0], y, offsets.right[index]![1]);
  }
  for (const index of ribbonTopTriangles(offsets.left, offsets.right)) target.indices.push(first + index);
  return offsets;
}

function raisedCurb(target: Draft, shape: readonly Point[]): void {
  const top = 0.24, bottom = 0.075;
  const offsets = ribbon(target, shape, 0.22, top);
  for (const side of [offsets.left, offsets.right]) {
    for (let index = 1; index < side.length; index++) {
      const start = side[index - 1]!, end = side[index]!;
      const first = vertex(target, start[0], bottom, start[1]);
      vertex(target, end[0], bottom, end[1]);
      vertex(target, end[0], top, end[1]);
      vertex(target, start[0], top, start[1]);
      target.indices.push(first, first + 1, first + 2, first, first + 2, first + 3);
    }
  }
}

/** Road surfaces lie in XZ. Their triangle normals must point toward positive Y. */
export function triangulateUpward(points: readonly Point[], holes: readonly (readonly Point[])[] = []): number[] {
  const outline = points.map(point => new THREE.Vector2(point[0], point[1]));
  const holeOutlines = holes.map(hole => hole.map(point => new THREE.Vector2(point[0], point[1])));
  const triangles = THREE.ShapeUtils.triangulateShape(outline, holeOutlines);
  const vertices = [ ...points, ...holes.flatMap(hole => hole) ];
  const indices: number[] = [];
  for (const triangle of triangles) {
    const a = triangle[0]!, b = triangle[1]!, c = triangle[2]!;
    const p = vertices[a]!, q = vertices[b]!, r = vertices[c]!;
    const signed = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    if (Math.abs(signed) < 1e-8) continue;
    if (signed > 0) indices.push(a, c, b);
    else indices.push(a, b, c);
  }
  return indices;
}

function polygon(target: Draft, points: readonly Point[], y: number,
                 holes: readonly (readonly Point[])[] = []): void {
  const triangles = triangulateUpward(points, holes);
  const first = target.positions.length / 3;
  for (const point of [ ...points, ...holes.flatMap(hole => hole) ]) vertex(target, point[0], y, point[1]);
  for (const index of triangles) target.indices.push(first + index);
}

export function crossingStripeSegments(shape: readonly Point[]): [Point, Point][] {
  const segments: [Point, Point][] = [];
  let offset = 0;
  const total = shape.slice(1).reduce((length, p, i) =>
    length + Math.hypot(p[0] - shape[i]![0], p[1] - shape[i]![1]), 0);
  // One phase along the entire crossing, independent of how SUMO splits its
  // polyline into vertices. Short segments must not each restart the pattern.
  for (let index = 1; index < shape.length; index++) {
    const start = shape[index - 1]!, end = shape[index]!;
    const dx = end[0] - start[0], dz = end[1] - start[1];
    const length = Math.hypot(dx, dz);
    if (length < 1e-8) continue;
    for (let stripe = Math.max(0, Math.floor((offset - 0.4) / 0.9)); ; stripe++) {
      const begin = 0.4 + stripe * 0.9, finish = begin + 0.45;
      if (begin >= offset + length || finish > total - 0.2) break;
      const a = Math.max(begin, offset) - offset;
      const b = Math.min(finish, offset + length) - offset;
      if (b <= a) continue;
      segments.push([[start[0] + dx * a / length, start[1] + dz * a / length],
                     [start[0] + dx * b / length, start[1] + dz * b / length]]);
    }
    offset += length;
  }
  return segments;
}

function crossingStripes(target: Draft, crossing: RoadCrossing): void {
  for (const [from, to] of crossingStripeSegments(crossing.shape))
    quad(target, from, to, Math.max(0.5, crossing.width - 0.35), 0.13);
}

function mesh(source: Draft, material: THREE.Material, name: string): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(source.positions, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(source.uvs, 2));
  const normals = new Float32Array(source.positions.length);
  for (let index = 1; index < normals.length; index += 3) normals[index] = 1;
  geometry.setAttribute("normal", new THREE.BufferAttribute(normals, 3));
  geometry.setIndex(source.indices);
  geometry.computeBoundingSphere();
  const result = new THREE.Mesh(geometry, material);
  result.name = name;
  result.receiveShadow = true;
  result.raycast = () => undefined;
  return result;
}

async function bigCityTile(path: string, crop: readonly [number, number, number, number], color: boolean,
                           visualAssets?: VerifiedVisualAssets):
    Promise<THREE.CanvasTexture> {
  const source = await new THREE.TextureLoader().loadAsync(visualAssets === undefined ? path : await visualAssets.url(path));
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 512;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("BigCity road texture canvas is unavailable");
  context.drawImage(source.image as CanvasImageSource, ...crop, 0, 0, 512, 512);
  source.dispose();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = color ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.anisotropy = 8;
  return texture;
}

const ROAD_ATLAS = "/models/bigcity/images/fbb0018506af50b41892e62a735c94d6.webp";
const ROAD_NORMAL = "/models/bigcity/images/7d9bd44cab76fa0419025d0b1b33f451.webp";
// Stay inside the source concrete tile: the old crop crossed a black atlas notch.
export const CITY_CURB_ATLAS_CROP = [960, 20, 300, 300] as const;
const PAVING = "/models/incoming/urban-traffic/images/64d1d14365d8479458d03808ea6b95d5.webp";
const STREET_LAMP = "/models/incoming/furniture/glb/street_light_8.glb";
/** Measured street_light_8 inventory: SG6 is the downward-facing lens under the lamp head. */
const STREET_LAMP_LENS = "street_light_8SG6";
/** SG1 is a blank 0.8 m pole-mounted plate plus the pole finial; the asset carries no sign content. */
const STREET_LAMP_BLANK_PLATE = "street_light_8SG1";

/**
 * Configure one street-lamp part material. Returns true only for the luminous lens.
 * The blank plate receives the same neutral painted-metal finish as the blank signal plate.
 */
export function prepareStreetLampPartMaterial(name: string, material: THREE.MeshStandardMaterial): boolean {
  material.emissive.setHex(0x000000);
  material.emissiveIntensity = 0;
  material.toneMapped = true;
  if (name === STREET_LAMP_LENS) {
    material.emissive.setHex(0xffb66a);
    return true;
  }
  if (name === STREET_LAMP_BLANK_PLATE) {
    material.color.setHex(0x78828a);
    material.metalness = 0.2;
    material.roughness = 0.7;
  }
  return false;
}

function streetLampHaloTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("Street lamp halo canvas is unavailable");
  const glow = context.createRadialGradient(32, 32, 0, 32, 32, 32);
  glow.addColorStop(0, "rgba(255,246,226,0.9)");
  glow.addColorStop(0.14, "rgba(255,226,175,0.48)");
  glow.addColorStop(0.55, "rgba(255,192,127,0.1)");
  glow.addColorStop(1, "rgba(255,192,127,0)");
  context.fillStyle = glow;
  context.fillRect(0, 0, 64, 64);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

async function addStreetLamps(group: THREE.Group, locations: readonly StreetLamp[],
                              walkbed: readonly RoadbedPolygon[], traffic: StreetTrafficData | StaticSignalInventory,
                              crossings: readonly RoadCrossing[], visualAssets?: VerifiedVisualAssets): Promise<void> {
  const source = (await new GLTFLoader().loadAsync(visualAssets === undefined
    ? STREET_LAMP : await visualAssets.url(STREET_LAMP))).scene;
  source.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(source);
  const size = bounds.getSize(new THREE.Vector3());
  if (Math.abs(size.y - 6) > 0.05 || size.x < 3 || size.x > 3.5) {
    throw new Error("Provided street lamp dimensions changed; pole anchor must be remeasured");
  }
  const parts = new Map<string, { material: THREE.MeshStandardMaterial; geometries: THREE.BufferGeometry[] }>();
  source.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    if (Array.isArray(node.material) || !(node.material instanceof THREE.MeshStandardMaterial)) {
      throw new Error("Provided street lamp has an unsupported material layout");
    }
    const name = node.material.name;
    const entry = parts.get(name) ?? { material: node.material.clone(), geometries: [] };
    entry.geometries.push((node.geometry.index === null ? node.geometry.clone() : node.geometry.toNonIndexed())
      .applyMatrix4(node.matrixWorld));
    parts.set(name, entry);
  });
  if (parts.size !== 4 || !parts.has(STREET_LAMP_LENS) || !parts.has(STREET_LAMP_BLANK_PLATE)) {
    throw new Error("Provided street lamp lacks its measured lens and blank plate materials");
  }
  const scale = 7.2 / size.y;
  const poleX = bounds.max.x - 0.2;
  const vertex = new THREE.Vector3();
  let poleMinX = Infinity, poleMaxX = -Infinity, poleMinZ = Infinity, poleMaxZ = -Infinity;
  source.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    const positions = node.geometry.getAttribute("position");
    for (let index = 0; index < positions.count; index++) {
      vertex.fromBufferAttribute(positions, index).applyMatrix4(node.matrixWorld);
      if (vertex.y > bounds.min.y + 1) continue;
      poleMinX = Math.min(poleMinX, vertex.x);
      poleMaxX = Math.max(poleMaxX, vertex.x);
      poleMinZ = Math.min(poleMinZ, vertex.z);
      poleMaxZ = Math.max(poleMaxZ, vertex.z);
    }
  });
  const poleCenterX = (poleMinX + poleMaxX) / 2;
  const poleCenterZ = (poleMinZ + poleMaxZ) / 2;
  if (!Number.isFinite(poleCenterX) || Math.abs(poleCenterZ) > 0.02
      || poleX <= poleCenterX || poleMaxX - poleMinX > 0.35) {
    throw new Error("Provided street lamp pole geometry must be remeasured");
  }
  const sliceHeight = 0.25;
  const slices = Array.from({ length: Math.ceil(size.y * scale / sliceHeight) }, (_, index) => ({
    minY: bounds.min.y * scale + index * sliceHeight,
    maxY: Math.min(bounds.max.y * scale, bounds.min.y * scale + (index + 1) * sliceHeight),
    minX: Infinity, maxX: -Infinity, halfZ: 0,
  }));
  source.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    const positions = node.geometry.getAttribute("position");
    const points = new Float32Array(positions.count * 3);
    for (let index = 0; index < positions.count; index++) {
      vertex.fromBufferAttribute(positions, index).applyMatrix4(node.matrixWorld);
      points[index * 3] = (vertex.x - poleCenterX) * scale;
      points[index * 3 + 1] = vertex.y * scale;
      points[index * 3 + 2] = (vertex.z - poleCenterZ) * scale;
    }
    const indices = node.geometry.index;
    const count = indices?.count ?? positions.count;
    for (let offset = 0; offset + 2 < count; offset += 3) {
      const a = (indices?.getX(offset) ?? offset) * 3;
      const b = (indices?.getX(offset + 1) ?? offset + 1) * 3;
      const c = (indices?.getX(offset + 2) ?? offset + 2) * 3;
      const first = Math.max(0, Math.floor((Math.min(points[a + 1]!, points[b + 1]!, points[c + 1]!)
        - bounds.min.y * scale) / sliceHeight));
      const last = Math.min(slices.length - 1, Math.floor((Math.max(points[a + 1]!, points[b + 1]!, points[c + 1]!)
        - bounds.min.y * scale) / sliceHeight));
      const minX = Math.min(points[a]!, points[b]!, points[c]!);
      const maxX = Math.max(points[a]!, points[b]!, points[c]!);
      const halfZ = Math.max(Math.abs(points[a + 2]!), Math.abs(points[b + 2]!), Math.abs(points[c + 2]!));
      for (let index = first; index <= last; index++) {
        const slice = slices[index]!;
        slice.minX = Math.min(slice.minX, minX);
        slice.maxX = Math.max(slice.maxX, maxX);
        slice.halfZ = Math.max(slice.halfZ, halfZ);
      }
    }
  });
  const measuredSlices = slices.filter(slice => slice.minX !== Infinity);
  if (measuredSlices.length < 20) throw new Error("Provided street lamp height profile is incomplete");
  // Lights, halos and ground pools sit under the measured lens, not at a hand-set arm length.
  const lensBounds = new THREE.Box3();
  for (const fragment of parts.get(STREET_LAMP_LENS)!.geometries) {
    fragment.computeBoundingBox();
    lensBounds.union(fragment.boundingBox!);
  }
  const lensCenter = lensBounds.getCenter(new THREE.Vector3());
  const lensOffset: StreetLampLensOffset = {
    alongArmM: (lensCenter.x - poleX) * scale,
    acrossArmM: lensCenter.z * scale,
    heightM: .225 + (lensBounds.min.y - bounds.min.y) * scale,
  };
  if (![lensOffset.alongArmM, lensOffset.acrossArmM, lensOffset.heightM].every(Number.isFinite)
      || lensOffset.heightM < 4) {
    throw new Error("Provided street lamp lens position must be remeasured");
  }
  const lampGeometry = {
    poleOffsetM: (poleX - poleCenterX) * scale,
    poleRadiusM: Math.max(poleMaxX - poleMinX, poleMaxZ - poleMinZ) * scale / 2,
    slices: measuredSlices,
  };
  const clearance = traffic.schema_version === "aero-bench.city-static-signal-inventory/v1"
    ? resolveStaticStreetLampClearance(locations, walkbed, traffic.signals, lampGeometry, crossings)
    : resolveStreetLampClearance(locations, walkbed, traffic, lampGeometry, crossings);
  const safeLocations = clearance.lamps;
  const axis = new THREE.Vector3(0, 1, 0);
  const unit = new THREE.Vector3(scale, scale, scale);
  const matrix = new THREE.Matrix4();
  const orientation = new THREE.Quaternion();
  const origin = new THREE.Vector3();
  const poleOffset = new THREE.Vector3();
  const fixtures = new THREE.Group();
  fixtures.name = "Provided urban street lamps on SUMO pedestrian paving";
  const litMaterials: THREE.MeshStandardMaterial[] = [];
  for (const [name, part] of parts) {
    const geometry = mergeGeometries(part.geometries);
    for (const fragment of part.geometries) fragment.dispose();
    if (geometry === null) throw new Error(`Street lamp geometry cannot be merged: ${name}`);
    const material = part.material;
    if (prepareStreetLampPartMaterial(name, material)) litMaterials.push(material);
    const instances = new THREE.InstancedMesh(geometry, material, safeLocations.length);
    instances.name = `street_light_8 ${name}`;
    instances.frustumCulled = false;
    instances.raycast = () => undefined;
    for (const [index, lamp] of safeLocations.entries()) {
      orientation.setFromAxisAngle(axis, THREE.MathUtils.degToRad(lamp.rotation_deg));
      poleOffset.set(poleX * scale, 0, 0).applyQuaternion(orientation);
      origin.set(lamp.x - poleOffset.x, .225 - bounds.min.y * scale, lamp.z - poleOffset.z);
      matrix.compose(origin, orientation, unit);
      instances.setMatrixAt(index, matrix);
    }
    instances.instanceMatrix.needsUpdate = true;
    fixtures.add(instances);
  }
  const pools = Array.from({ length: 4 }, () => {
    const light = new THREE.PointLight(0xffb86c, 0, 22, 2);
    fixtures.add(light);
    return light;
  });
  const haloMaterial = new THREE.SpriteMaterial({ map: streetLampHaloTexture(), color: 0xffdbab,
    transparent: true, opacity: 0.62, blending: THREE.AdditiveBlending, depthWrite: false,
    toneMapped: false });
  const halos = Array.from({ length: 16 }, () => {
    const halo = new THREE.Sprite(haloMaterial);
    halo.visible = false;
    halo.scale.set(1.65, 1.65, 1);
    halo.raycast = () => undefined;
    fixtures.add(halo);
    return halo;
  });
  const poolMaterial = new THREE.MeshBasicMaterial({ map: streetLampHaloTexture(), color: 0xffac57,
    transparent: true, opacity: 0.48, blending: THREE.AdditiveBlending,
    depthWrite: false, side: THREE.DoubleSide, toneMapped: false });
  const groundPools = new THREE.InstancedMesh(new THREE.PlaneGeometry(1, 1), poolMaterial, safeLocations.length);
  const groundRotation = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
  const groundScale = new THREE.Vector3(17, 17, 1);
  const head = new THREE.Vector3();
  for (const [index, lamp] of safeLocations.entries()) {
    streetLampHeadPosition(lamp, lensOffset, head);
    matrix.compose(head.setY(0.153), groundRotation, groundScale);
    groundPools.setMatrixAt(index, matrix);
  }
  groundPools.instanceMatrix.needsUpdate = true;
  groundPools.name = "Warm street lamp ground illumination";
  groundPools.frustumCulled = false;
  groundPools.raycast = () => undefined;
  groundPools.visible = false;
  fixtures.add(groundPools);
  group.add(fixtures);
  group.userData.streetLampCount = safeLocations.length;
  group.userData.streetLampLocations = safeLocations;
  group.userData.streetLampClearance = {
    conflictingIndices: clearance.conflictingIndices,
    relocatedIndices: clearance.relocatedIndices,
    omittedIndices: clearance.omittedIndices,
  };
  group.userData.streetLampMaterials = litMaterials;
  group.userData.streetLampLensOffset = lensOffset;
  group.userData.streetLampLights = pools;
  group.userData.streetLampHalos = halos;
  group.userData.streetLampGroundPools = groundPools;
}

/** Measured lens centre relative to the pole axis, in the lamp's own frame (metres). */
export interface StreetLampLensOffset {
  readonly alongArmM: number;
  readonly acrossArmM: number;
  /** Height of the lens's lower face above the ground. */
  readonly heightM: number;
}

/** World position of a lamp's lens: the lamp frame is the model rotated by rotation_deg about +Y. */
export function streetLampHeadPosition(lamp: Pick<StreetLamp, "x" | "z" | "rotation_deg">,
    offset: StreetLampLensOffset, target: THREE.Vector3): THREE.Vector3 {
  const angle = THREE.MathUtils.degToRad(lamp.rotation_deg);
  const cos = Math.cos(angle), sin = Math.sin(angle);
  return target.set(lamp.x + offset.alongArmM * cos + offset.acrossArmM * sin, offset.heightM,
    lamp.z - offset.alongArmM * sin + offset.acrossArmM * cos);
}

/** Keep only the nearest four road lights active; all lamp lenses remain visible. */
export function setCityRoadLighting(group: THREE.Group, camera: THREE.Camera,
                                    timeOfDay: CityTimeOfDay): void {
  const lighting = CITY_SUBSYSTEM_LIGHTING[timeOfDay];
  const materials = group.userData.streetLampMaterials as THREE.MeshStandardMaterial[];
  const lights = group.userData.streetLampLights as THREE.PointLight[];
  const halos = group.userData.streetLampHalos as THREE.Sprite[];
  const groundPools = group.userData.streetLampGroundPools as THREE.InstancedMesh;
  const locations = group.userData.streetLampLocations as readonly StreetLamp[];
  for (const material of materials) material.emissiveIntensity = lighting.roadLampEmissiveIntensity;
  groundPools.visible = timeOfDay !== "day";
  if (groundPools.material instanceof THREE.MeshBasicMaterial) {
    groundPools.material.opacity = lighting.roadPoolOpacity;
  } else throw new Error("Street lamp ground pools require one basic material");
  for (const halo of halos) halo.material.opacity = lighting.roadHaloOpacity;
  const nearby = timeOfDay !== "day" ? locations.map(lamp => ({ lamp,
    distance: (lamp.x - camera.position.x) ** 2 + (lamp.z - camera.position.z) ** 2 }))
    .filter(item => item.distance < 170 ** 2).sort((a, b) => a.distance - b.distance) : [];
  const lensOffset = group.userData.streetLampLensOffset as StreetLampLensOffset | undefined;
  if (lensOffset === undefined) throw new Error("Street lamp lighting requires the measured lens offset");
  const positionLampHead = (lamp: StreetLamp, target: THREE.Vector3): void => {
    streetLampHeadPosition(lamp, lensOffset, target);
  };
  for (const [index, light] of lights.entries()) {
    const item = nearby[index];
    // Removing a light from the render list recompiles every affected material.
    if (item === undefined || item.distance >= 110 ** 2) { light.intensity = 0; continue; }
    positionLampHead(item.lamp, light.position);
    light.intensity = lighting.roadPointLightIntensity;
  }
  for (const [index, halo] of halos.entries()) {
    const item = nearby[index];
    halo.visible = item !== undefined;
    if (item !== undefined) positionLampHead(item.lamp, halo.position);
  }
}

export interface CityRoadWettableMaterials {
  readonly asphalt: readonly THREE.MeshStandardMaterial[];
  readonly paving: readonly THREE.MeshStandardMaterial[];
  readonly crossings: readonly THREE.MeshStandardMaterial[];
}

/**
 * Visual caps for horizontal road films. Asphalt can become glossy, while concrete
 * paving and painted crossings keep higher roughness and less diffuse darkening.
 * Facades, vertical curb walls and road fixtures are outside this material inventory.
 */
export const CITY_ROAD_WETNESS_LIMITS: Readonly<Record<keyof CityRoadWettableMaterials,
  Readonly<Required<SurfaceWetnessOptions>>>> = {
  asphalt: { maxDarkening: 0.34, minRoughness: 0.18 },
  paving: { maxDarkening: 0.22, minRoughness: 0.48 },
  crossings: { maxDarkening: 0.12, minRoughness: 0.38 },
};

/** Apply one shared weather uniform to the horizontal road, paving and crossing materials. */
export function applyCityRoadSurfaceWetness(materials: CityRoadWettableMaterials,
    wetness: SurfaceWetnessUniforms): number {
  if (wetness === null || typeof wetness !== "object" || wetness.uWetness === undefined
      || !Number.isFinite(wetness.uWetness.value) || wetness.uWetness.value < 0 || wetness.uWetness.value > 1) {
    throw new RangeError("City road wetness requires one finite 0..1 shared uniform");
  }
  const applied = new Set<THREE.MeshStandardMaterial>();
  for (const category of ["asphalt", "paving", "crossings"] as const) {
    for (const material of materials[category]) {
      if (applied.has(material)) throw new Error("City road wetness material belongs to multiple surface classes");
      applySurfaceWetness(material, wetness, CITY_ROAD_WETNESS_LIMITS[category]);
      material.userData.cityRoadWetnessClass = category;
      applied.add(material);
    }
  }
  return applied.size;
}

/** Key-order independent JSON identity for documents parsed from JSON. */
export function sameJson(left: unknown, right: unknown): boolean {
  const canonical = (value: unknown): unknown => Array.isArray(value) ? value.map(canonical)
    : value !== null && typeof value === "object"
      ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical((value as Record<string, unknown>)[key])]))
      : value;
  return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
}

/** Workspace roads bind a building placement; canonical roads bind a verified source context
 * and draw only the street lamps that passed the effective-fixture gate. */
export type CityRoadIdentity =
  | { readonly placementSha256: string }
  | { readonly sourceContext: Readonly<Record<string, unknown>>; readonly streetLampIndices: ReadonlySet<number> };

/** Check source identity without assuming the geometry count of a particular region. */
export function verifyCityRoadIdentity(data: RoadData, sourceNetworkSha256: string,
    meshPackSourceSha256: string, identity: CityRoadIdentity, expectedSurfaceSha256: string,
    traffic: Pick<StreetTrafficData, "source_network_sha256" | "mesh_pack_source_sha256"> | null): void {
  if (data.source_network_sha256 !== sourceNetworkSha256
      || data.mesh_pack_source_sha256 !== meshPackSourceSha256
      || ("placementSha256" in identity ? data.building_placement_sha256 !== identity.placementSha256
        : !sameJson(data.source_context, identity.sourceContext))
      || data.displayed_surface_sha256 !== expectedSurfaceSha256
      || (traffic !== null && (traffic.source_network_sha256 !== sourceNetworkSha256
        || traffic.mesh_pack_source_sha256 !== meshPackSourceSha256))
      || data.roadbed_area_m2 <= 0 || data.walkbed_area_m2 <= 0) {
    throw new Error("SUMO road geometry does not match the displayed network and city source");
  }
}

/** SUMO lane ribbons and junctions use supplied BigCity road atlas materials. */
export async function loadCityRoads(sourceNetworkSha256: string, meshPackSourceSha256: string,
                                    identity: CityRoadIdentity, expectedSurfaceSha256: string,
                                    roadSource: string | VerifiedStaticRoadInput,
                                    traffic: StreetTrafficData | null, pack: MeshPackManifest, packBaseUrl: string,
                                    wetness: SurfaceWetnessUniforms,
                                    visualAssets?: VerifiedVisualAssets): Promise<THREE.Group> {
  let data: RoadData;
  if (typeof roadSource === "string") {
    if (traffic === null) throw new Error("Recorded city road requires real SUMO traffic frames");
    const response = await fetch(roadSource);
    if (!response.ok) throw new Error(`SUMO road geometry failed: ${response.status}`);
    const value: unknown = await response.json();
    data = "sourceContext" in identity ? validateCanonicalCityRoadPayload(value) : validateCityRoadPreviewPayload(value);
  } else {
    if (!("placementSha256" in identity)) throw new Error("Static city road requires its building placement identity");
    if (traffic !== null) throw new Error("Static city road cannot accept recorded traffic in place of network signals");
    if (visualAssets === undefined) throw new Error("Selected city road is missing verified visual assets");
    data = validateStaticCityRoadPayload(roadSource.value);
    const staticData = roadSource.value as Record<string, unknown>;
    if (staticData.source_osm_sha256 !== roadSource.sourceOsmSha256
      || staticData.mesh_pack_manifest_sha256 !== roadSource.packManifestSha256
      || staticData.signal_inventory_sha256 !== roadSource.signalInventorySha256
      || roadSource.signals.source_network_sha256 !== sourceNetworkSha256
      || roadSource.signals.mesh_pack_source_sha256 !== meshPackSourceSha256) {
      throw new Error("Selected road, SUMO network, pack and signal inventory are not the same source");
    }
    const concreteAsset = data.road_surface_materials.concrete.texture_asset;
    if (concreteAsset != null && concreteAsset.url !== `${packBaseUrl}assets/${concreteAsset.sha256}`) {
      throw new Error("Selected concrete texture does not belong to this job's verified pack");
    }
  }
  verifyCityRoadIdentity(data, sourceNetworkSha256, meshPackSourceSha256, identity,
    expectedSurfaceSha256, traffic);
  if (await displaySurfaceSha256(data.roadbed, data.walkbed) !== expectedSurfaceSha256) {
    throw new Error("SUMO roadbed and walkbed polygons differ from the certified displayed surfaces");
  }
  const concreteAsset = data.concrete_roadbed.length > 0
    ? verifiedConcreteAsset(data.road_surface_materials.concrete, pack, packBaseUrl) : null;
  const [roadMap, roadNormal, curbMap, walkMap, concreteMap] = await Promise.all([
    bigCityTile(ROAD_ATLAS, [230, 820, 260, 260], true, visualAssets),
    bigCityTile(ROAD_NORMAL, [230, 820, 260, 260], false, visualAssets),
    bigCityTile(ROAD_ATLAS, CITY_CURB_ATLAS_CROP, true, visualAssets),
    new THREE.TextureLoader().loadAsync(visualAssets === undefined ? PAVING : await visualAssets.url(PAVING)),
    data.concrete_roadbed.length > 0 && concreteAsset !== null && concreteAsset !== undefined
      ? loadVerifiedConcreteTexture(concreteAsset) : Promise.resolve(null),
  ]);
  walkMap.colorSpace = THREE.SRGBColorSpace;
  walkMap.wrapS = walkMap.wrapT = THREE.RepeatWrapping;
  walkMap.anisotropy = 8;
  if (concreteMap !== null) {
    concreteMap.flipY = false;
    concreteMap.colorSpace = THREE.SRGBColorSpace;
    concreteMap.wrapS = concreteMap.wrapT = THREE.RepeatWrapping;
    concreteMap.anisotropy = 8;
  }
  const drafts: Record<RoadKind, Draft> = {
    motor: draft(), shared: draft(), cycle: draft(), walk: draft(),
  };
  const concreteSurfaces = draft(), medians = draft(), pavementWalls = draft();
  const lines = draft(), centers = draft(), curbs = draft(), crossings = draft();
  for (const roadbed of data.roadbed) {
    if (roadbed.outline.length < 3 || roadbed.holes.some(hole => hole.length < 3)) {
      throw new Error("SUMO roadbed polygon is invalid");
    }
    polygon(drafts.motor, roadbed.outline, 0.075, roadbed.holes);
  }
  for (const concrete of data.concrete_roadbed) {
    polygon(concreteSurfaces, concrete.outline, 0.083, concrete.holes);
  }
  const [repeatU, repeatV] = data.road_surface_materials.concrete.uv_repeat_m!;
  const concreteTextureSurfaces: Draft = { ...concreteSurfaces,
    uvs: meterTextureUvs(concreteSurfaces.uvs, [repeatU, repeatV]) };
  for (const walkbed of data.walkbed) {
    if (walkbed.outline.length < 3 || walkbed.holes.some(hole => hole.length < 3)) {
      throw new Error("SUMO walking surface polygon is invalid");
    }
    polygon(drafts.walk, walkbed.outline, data.street_layout.sidewalk_height_m, walkbed.holes);
  }
  for (const edge of data.street_layout.pavement_edges) {
    for (let i = 1; i < edge.length; i++) {
      const a = edge[i - 1]!, b = edge[i]!, index = pavementWalls.positions.length / 3;
      vertex(pavementWalls, a[0], .075, a[1]); vertex(pavementWalls, b[0], .075, b[1]);
      vertex(pavementWalls, a[0], .225, a[1]); vertex(pavementWalls, b[0], .225, b[1]);
      pavementWalls.indices.push(index, index + 2, index + 1, index + 1, index + 2, index + 3);
    }
  }
  for (const median of data.street_layout.median_beds) {
    polygon(medians, median.outline, data.street_layout.sidewalk_height_m, median.holes);
  }
  for (const edge of data.curb_edges) {
    if (edge.length < 2) throw new Error("SUMO road/walk curb edge is invalid");
    raisedCurb(curbs, edge);
  }
  for (const lane of data.lanes) {
    if (lane.shape.length < 2 || lane.width <= 0 || !(lane.kind in drafts)) {
      throw new Error(`SUMO visual lane is invalid: ${lane.id}`);
    }
    if (lane.kind === "walk") continue;
    if (lane.kind !== "motor") ribbon(drafts[lane.kind], lane.shape, lane.width, .08);
  }
  for (const mark of data.street_layout.markings) {
    // The builder has already cut dashed paint into explicit 3 m segments.
    ribbon(mark.color === "yellow" ? centers : lines, mark.shape, mark.width_m, .096);
  }
  for (const arrow of data.street_layout.arrows) polygon(lines, arrow.outline, .098);
  for (const junction of data.junctions) {
    if (junction.shape.length < 3) throw new Error(`SUMO visual junction is invalid: ${junction.id}`);
  }
  for (const crossing of data.crossings) crossingStripes(crossings, crossing);
  const group = new THREE.Group();
  group.name = "SUMO road geometry with BigCity road materials";
  const asphalt = new THREE.MeshStandardMaterial({ map: roadMap, normalMap: roadNormal,
    color: 0xc7ced2, roughness: 0.82, metalness: 0.02 });
  const shared = asphalt.clone(); shared.color.setHex(0xc2c4c1);
  const cycle = asphalt.clone(); cycle.color.setHex(0x9e7770);
  const concreteSource = pack.batches.find(batch => batch.layer === "roads"
    && batch.material.base_color_texture === data.road_surface_materials.concrete.texture);
  const concrete = new THREE.MeshStandardMaterial({ map: concreteMap ?? undefined,
    color: concreteMap === null ? new THREE.Color(0x929594)
      : new THREE.Color().setRGB(...concreteSource!.material.color, THREE.SRGBColorSpace),
    roughness: 0.96, metalness: 0 });
  const walkway = new THREE.MeshStandardMaterial({ map: walkMap, color: 0xaeb2ac,
    roughness: 0.95 });
  const crossingMaterial = new THREE.MeshStandardMaterial({ color: 0xe7e9e2, roughness: .8 });
  group.userData.surfaceWetnessMaterialCount = applyCityRoadSurfaceWetness({
    asphalt: [asphalt, shared, cycle], paving: [concrete, walkway], crossings: [crossingMaterial],
  }, wetness);
  group.userData.surfaceWetnessUniforms = wetness;
  group.add(mesh(drafts.motor, asphalt, "SUMO motor lanes and junctions"));
  group.add(mesh(drafts.shared, shared, "SUMO shared streets"));
  group.add(mesh(concreteTextureSurfaces, concrete, "OSM2World concrete motor-road surfaces"));
  group.add(mesh(drafts.cycle, cycle, "SUMO bicycle lanes"));
  group.add(mesh(drafts.walk, walkway, "Source and explicitly derived raised pedestrian pavement"));
  group.add(mesh(medians, walkway, "Derived raised paved median islands"));
  const walls = mesh(pavementWalls, new THREE.MeshStandardMaterial({ color: 0x9d9c96,
    roughness: .93, side: THREE.DoubleSide }), "Vertical 15 cm pavement edges");
  walls.geometry.computeVertexNormals();
  group.add(walls);
  const curbMesh = mesh(curbs, new THREE.MeshStandardMaterial({ map: curbMap, color: 0xb9b9b4,
    roughness: 0.94, side: THREE.DoubleSide }), "BigCity concrete road edges");
  curbMesh.geometry.computeVertexNormals();
  group.add(curbMesh);
  group.add(mesh(lines, new THREE.MeshStandardMaterial({ color: 0xf0eee4, roughness: .8, metalness: 0,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }),
                 "SUMO high-contrast white lane edge and divider markings"));
  group.add(mesh(centers, new THREE.MeshStandardMaterial({ color: 0xe9be35, roughness: .8, metalness: 0,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }),
                 "Designed yellow opposing-flow markings from verified SUMO boundaries"));
  group.add(mesh(crossings, crossingMaterial,
                 "SUMO zebra crossings"));
  if (!Array.isArray(data.street_lamps)) {
    throw new Error("SUMO road scene is missing its street lamp placement list");
  }
  const lamps = "streetLampIndices" in identity
    ? data.street_lamps.filter((_, index) => identity.streetLampIndices.has(index)) : data.street_lamps;
  group.userData.effectiveStreetLampCount = lamps.length;
  await addStreetLamps(group, lamps, data.walkbed,
    traffic ?? (roadSource as VerifiedStaticRoadInput).signals, data.crossings, visualAssets);
  group.userData.laneCount = data.lanes.length;
  group.userData.internalLaneCount = data.internal_lane_count;
  group.userData.roadbedPolygonCount = data.roadbed.length;
  group.userData.concreteRoadbedPolygonCount = data.concrete_roadbed.length;
  group.userData.directionGuideCount = data.direction_guides.length;
  group.userData.roadbed = data.roadbed;
  group.userData.walkbedPolygonCount = data.walkbed.length;
  group.userData.walkbed = data.walkbed;
  group.userData.crossings = data.crossings;
  group.userData.streetLayout = data.street_layout;
  group.userData.signalRoadCoverage = data.signal_road_coverage;
  const focusLane = data.lanes.find(lane => lane.kind === "motor" && lane.shape.length >= 2)
    ?? data.lanes.find(lane => lane.shape.length >= 2);
  if (focusLane !== undefined) {
    const a = focusLane.shape[0]!, b = focusLane.shape[1]!;
    group.userData.staticStreetFocus = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  }
  group.userData.junctionCount = data.junctions.length;
  group.userData.crossingCount = data.crossings.length;
  return group;
}
