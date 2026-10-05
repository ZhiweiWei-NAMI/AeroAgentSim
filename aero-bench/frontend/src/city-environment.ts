import * as THREE from "three";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { SimplifyModifier } from "three/addons/modifiers/SimplifyModifier.js";
import { mergeGeometries, mergeVertices } from "three/addons/utils/BufferGeometryUtils.js";
import type { PlacementBox } from "./city-workspace-geometry";
import type { GroundMaterialAssignment } from "./city-ground-material-rules";
import { terrainSurfaceGeometry, type TerrainSurfaceKit } from "./city-terrain-surfaces";
import { loadSharedFbx } from "./city-asset-cache";

export type EnvironmentPoint = readonly [x: number, z: number];
export interface EnvironmentPolygon {
  readonly outline: readonly EnvironmentPoint[];
  readonly holes: readonly (readonly EnvironmentPoint[])[];
}
export interface EnvironmentFootprint extends EnvironmentPolygon { readonly id: string; }
export interface EnvironmentCrossing {
  readonly id: string;
  readonly shape: readonly EnvironmentPoint[];
  readonly width: number;
}
export interface EnvironmentStation {
  readonly id: string;
  readonly x: number;
  readonly z: number;
  /** Include the complete pole/head/arm or signal assembly, not just its stem. Trees clear it. */
  readonly radiusM: number;
  /** Ground-contact envelope (at most radiusM). Lawn surface and grass clear only this: an arm
   * or head overhead does not displace the ground under it. */
  readonly groundRadiusM: number;
}
export interface OsmGreenProvenance {
  readonly kind: "osm";
  readonly sourceSha256: string;
  readonly elementType: "way" | "relation";
  /** IDs remain strings because scene-compiler IDs can exceed JS integer precision. */
  readonly elementId: string;
  readonly sourceElementId: string;
  readonly sourceElementType: "way" | "relation";
  readonly tags: Readonly<Record<string, string>>;
}
export interface AuthoredGreenProvenance { readonly kind: "authored"; readonly designId: string; }
export interface EnvironmentGreen extends EnvironmentFootprint {
  readonly provenance: OsmGreenProvenance | AuthoredGreenProvenance;
}
export interface EnvironmentSourceTree {
  readonly id: string;
  readonly x: number;
  readonly z: number;
  readonly sourceSha256: string;
  readonly sourceElementId: string;
}
export interface CityEnvironmentSource {
  readonly schemaVersion: "aero-bench.city-environment-source/v1";
  readonly coordinateFrame: "x-east,y-up,z-south-meters";
  readonly source: {
    readonly osmSha256: string; readonly objectsSha256: string;
    readonly projection: "WGS84->ECEF->ENU";
    readonly origin: { readonly latitude_deg: number; readonly longitude_deg: number; readonly ellipsoid_height_m: number };
  };
  readonly buildings: readonly (EnvironmentFootprint & { readonly geometrySha256: string })[];
  readonly greens: readonly EnvironmentGreen[];
  readonly sourceTrees: readonly EnvironmentSourceTree[];
  readonly inspection: {
    readonly taggedAreaCount: number; readonly greenAreaCount: number; readonly sourceTreeCount: number;
    readonly excludedTags: Readonly<Record<string, number>>; readonly measurementStatus: string;
  };
}

/** The caller also verifies this derivative's own digest before parsing. */
export function parseCityEnvironmentSource(raw: unknown,
    expected: Pick<CityEnvironmentSource["source"], "osmSha256" | "objectsSha256" | "origin">): CityEnvironmentSource {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) throw new Error("City environment source is missing");
  const source = raw as CityEnvironmentSource;
  const authority = source.source;
  if (source.schemaVersion !== "aero-bench.city-environment-source/v1"
    || source.coordinateFrame !== "x-east,y-up,z-south-meters"
    || authority?.projection !== "WGS84->ECEF->ENU" || authority.origin === undefined
    || !/^[a-f0-9]{64}$/.test(authority.osmSha256) || !/^[a-f0-9]{64}$/.test(authority.objectsSha256)
    || authority.osmSha256 !== expected.osmSha256 || authority.objectsSha256 !== expected.objectsSha256
    || ["latitude_deg", "longitude_deg", "ellipsoid_height_m"].some(field => {
      const key = field as keyof typeof authority.origin;
      return !Number.isFinite(authority.origin[key]) || authority.origin[key] !== expected.origin[key];
    })) throw new Error("City environment source authority mismatch");
  if (!Array.isArray(source.buildings) || source.buildings.length === 0
    || !Array.isArray(source.greens) || !Array.isArray(source.sourceTrees)
    || source.inspection?.greenAreaCount !== source.greens.length
    || source.inspection.sourceTreeCount !== source.sourceTrees.length) throw new Error("City environment source inspection is invalid");
  const ids = new Set<string>();
  for (const footprint of source.buildings) {
    checkPolygon(footprint, "source building footprint");
    if (!footprint.id || ids.has(footprint.id) || !/^[a-f0-9]{64}$/.test(footprint.geometrySha256)) {
      throw new Error("City environment source building identity is invalid");
    }
    ids.add(footprint.id);
  }
  for (const green of source.greens) {
    checkPolygon(green, "source green");
    if (!green.id || ids.has(green.id) || green.provenance?.kind !== "osm"
      || green.provenance.sourceSha256 !== authority.osmSha256
      || !["way", "relation"].includes(green.provenance.elementType)
      || !["way", "relation"].includes(green.provenance.sourceElementType)
      || !green.provenance.elementId || !green.provenance.sourceElementId
      || green.provenance.tags === undefined || !recognizedGreen(green.provenance.tags)) {
      throw new Error("City environment source green provenance is invalid");
    }
    ids.add(green.id);
  }
  for (const tree of source.sourceTrees) {
    if (!tree.id || ids.has(tree.id) || tree.sourceSha256 !== authority.osmSha256
      || !tree.sourceElementId || ![tree.x, tree.z].every(Number.isFinite)) throw new Error("City environment source tree is invalid");
    ids.add(tree.id);
  }
  return source;
}
/** Road geometry status: either actually loaded, or explicitly withheld by the native gate. */
export type CityEnvironmentRoadGeometry = "published" | "pending-native-gate";
export interface CityEnvironmentInput {
  /** The actual loaded road and building geometry, including all rendered envelopes. */
  readonly geometryId: string;
  /**
   * "published" means roadbed/walkbed/crossings/junctions carry the loaded city geometry.
   * "pending-native-gate" means no road geometry was produced, so the planner must receive
   * empty arrays and the plan must carry "road-clearance-pending-native-gate" in missing.
   */
  readonly roadGeometry: CityEnvironmentRoadGeometry;
  readonly roadbed: readonly EnvironmentPolygon[];
  readonly walkbed: readonly EnvironmentPolygon[];
  readonly crossings: readonly EnvironmentCrossing[];
  readonly junctions: readonly EnvironmentFootprint[];
  readonly buildings: readonly EnvironmentFootprint[];
  readonly lamps: readonly EnvironmentStation[];
  readonly signals: readonly EnvironmentStation[];
  readonly extent: EnvironmentPolygon;
  /** Null means unavailable. An empty array means inspected and no tagged greens found. */
  readonly greens: readonly EnvironmentGreen[] | null;
  readonly sourceTrees: readonly EnvironmentSourceTree[] | null;
}
export interface EnvironmentTreeAsset {
  readonly id: string;
  readonly title: string;
  readonly modelUrl: string;
  /** Presentation height in metres; source tree heights are not surveyed here. */
  readonly heightM: number;
  readonly widthM: number;
  readonly depthM: number;
  readonly sourceHeight: number;
}

/** Metered, uniformly scaled supplied assets; dimensions come from parsed FBX bounds. */
export const BIGCITY_ENVIRONMENT_TREES: readonly EnvironmentTreeAsset[] = Object.freeze([
  {"id": "bigcity-tree-01", "title": "Tree 01", "modelUrl": "/models/bigcity/fbx/184f4a93531582f4bbaeb7799a98d5b9.fbx", "heightM": 7, "widthM": 6.499796811917563, "depthM": 5.740565056764514, "sourceHeight": 162.7169712031916},
  {"id": "bigcity-tree-02", "title": "Tree 02", "modelUrl": "/models/bigcity/fbx/62937495cd6951749adc27d29bdb1959.fbx", "heightM": 7, "widthM": 4.8265597390400945, "depthM": 5.740565056764514, "sourceHeight": 162.7169712031916},
  {"id": "bigcity-tree-03", "title": "Tree 03", "modelUrl": "/models/bigcity/fbx/4d463bc7e80208c4c8843b2e18708d9f.fbx", "heightM": 6.5, "widthM": 6.249900358556476, "depthM": 5.949606949684326, "sourceHeight": 105.54148912756722},
  {"id": "bigcity-tree-04", "title": "Tree 04", "modelUrl": "/models/bigcity/fbx/a42c53136ee3dfc4aaf157c7ea3af994.fbx", "heightM": 7, "widthM": 5.109258362589007, "depthM": 5.383731812651745, "sourceHeight": 199.20354668931748},
  {"id": "bigcity-tree-05", "title": "Tree 05", "modelUrl": "/models/bigcity/fbx/25e926b682ce08d4eb70de21411bc42e.fbx", "heightM": 6, "widthM": 5.12652827184857, "depthM": 5.021759684885902, "sourceHeight": 114.11916202359606},
  {"id": "bigcity-tree-06", "title": "Tree 06", "modelUrl": "/models/bigcity/fbx/8b2db0d125875db4faf4c67f70f3ba97.fbx", "heightM": 7, "widthM": 8.834957282871377, "depthM": 8.591877423241968, "sourceHeight": 141.67123413085943},
  {"id": "bigcity-tree-07", "title": "Tree 07", "modelUrl": "/models/bigcity/fbx/8bb8a1e2fd84f47499c285d2c27c72e2.fbx", "heightM": 7, "widthM": 8.834957283455099, "depthM": 8.59187742380963, "sourceHeight": 141.67123412149928},
  {"id": "bigcity-tree-08", "title": "Tree 08", "modelUrl": "/models/bigcity/fbx/b8805ed3f824ed14cb18feaf529a0122.fbx", "heightM": 7, "widthM": 2.173399813793461, "depthM": 2.0977831045646242, "sourceHeight": 60.35598286628843},
  {"id": "bigcity-tree-09", "title": "Tree 09", "modelUrl": "/models/bigcity/fbx/886ee408348854d428617ce92582be04.fbx", "heightM": 7, "widthM": 3.341794511623071, "depthM": 3.0541954014942, "sourceHeight": 39.25366489970118},
  {"id": "bigcity-tree-10", "title": "Tree 10", "modelUrl": "/models/bigcity/fbx/becc6a74c566c1f44bf3f40ef88b9ff6.fbx", "heightM": 7, "widthM": 4.528439613843091, "depthM": 4.842757903390529, "sourceHeight": 54.964537139996196},
  {"id": "bigcity-tree-11", "title": "Tree 11", "modelUrl": "/models/bigcity/fbx/d82ec1810caf10f41b12299498f8f159.fbx", "heightM": 7, "widthM": 4.407743397010823, "depthM": 4.375923084225852, "sourceHeight": 32.187263833534786},
  {"id": "bigcity-tree-12", "title": "Tree 12", "modelUrl": "/models/bigcity/fbx/2d97715e43677384c8ca3f6826385d4a.fbx", "heightM": 7, "widthM": 4.48243356803199, "depthM": 4.229198475732807, "sourceHeight": 52.68720001032245}
]);
export interface CityEnvironmentConfig {
  readonly seed: number;
  readonly species: readonly string[];
  readonly density: number;
  readonly spacingM: number;
  readonly scaleRange: readonly [number, number];
  readonly groundY: number;
  readonly grassY: number;
  readonly clearancesM: {
    readonly road: number; readonly walk: number; readonly building: number;
    readonly crossing: number; readonly junction: number; readonly station: number;
    readonly tree: number;
  };
  readonly streetTrees: { readonly kind: "disabled" }
    | { readonly kind: "authored"; readonly designId: string; readonly maxRoadDistanceM: number };
  /** Additional trees in tagged greens are decorative placements, not surveyed tree nodes. */
  readonly greenTrees: boolean;
}
export const CITY_ENVIRONMENT_DEFAULTS: CityEnvironmentConfig = Object.freeze({
  seed: 20260930, species: ["bigcity-tree-05", "bigcity-tree-03"], density: 0.85,
  spacingM: 14, scaleRange: [0.92, 1.06] as const, groundY: 0, grassY: 0.018,
  clearancesM: { road: 0.35, walk: 0.3, building: 0.8, crossing: 2.5,
    junction: 7, station: 2, tree: 0.75 },
  streetTrees: { kind: "disabled" as const }, greenTrees: true,
});

export type TreePlacementProvenance =
  | { readonly kind: "osm-tree"; readonly sourceSha256: string; readonly sourceElementId: string }
  | { readonly kind: "derived-osm-green"; readonly greenId: string; readonly source: OsmGreenProvenance }
  | { readonly kind: "authored-green-tree"; readonly greenId: string; readonly designId: string }
  | { readonly kind: "authored-street-tree"; readonly walkbedIndex: number; readonly edgeIndex: number;
      readonly designId: string };
export interface EnvironmentTreePlacement {
  readonly id: string; readonly assetId: string; readonly x: number; readonly z: number;
  readonly y: number; readonly yaw: number; readonly scale: number;
  readonly heightM: number;
  /** Circle encloses the entire rotated crown and trunk at every height. */
  readonly envelopeRadiusM: number;
  readonly provenance: TreePlacementProvenance;
}
export interface EnvironmentGrassPatch {
  readonly id: string; readonly triangles: readonly (readonly EnvironmentPoint[])[];
  readonly sourceAreaM2: number; readonly areaM2: number;
  readonly provenance: EnvironmentGreen["provenance"];
}
export type EnvironmentRejection = "density" | "extent" | "road" | "walk" | "building"
  | "crossing" | "junction" | "lamp" | "signal" | "tree" | "green-boundary" | "not-roadside";
export interface CityEnvironmentPlan {
  readonly schemaVersion: "aero-bench.city-environment/v1";
  readonly geometryId: string;
  /** Mirrors the input; the renderer surfaces it on group.userData.environmentRoadGeometry. */
  readonly roadGeometry: CityEnvironmentRoadGeometry;
  readonly config: CityEnvironmentConfig;
  readonly assets: readonly EnvironmentTreeAsset[];
  readonly trees: readonly EnvironmentTreePlacement[];
  readonly grass: readonly EnvironmentGrassPatch[];
  /** Woodland ground under the canopy, clipped like lawn; never receives lawn or clumps. */
  readonly woodlandFloor: readonly EnvironmentGrassPatch[];
  readonly missing: readonly string[];
  readonly stats: {
    readonly candidates: number; readonly accepted: number;
    readonly rejected: Readonly<Record<EnvironmentRejection, number>>;
    readonly sourceGreenCount: number; readonly authoredGreenCount: number;
    readonly grassAreaM2: number; readonly grassRemovedAreaM2: number;
    readonly grassFullyExcludedCount: number;
    readonly woodlandFloorAreaM2: number;
  };
}

const EPS = 1e-7;
const GRID_M = 32;
function signedArea(ring: readonly EnvironmentPoint[]): number {
  let area = 0;
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i]!, b = ring[(i + 1) % ring.length]!;
    area += a[0] * b[1] - b[0] * a[1];
  }
  return area / 2;
}
function openRing(ring: readonly EnvironmentPoint[]): EnvironmentPoint[] {
  const result = ring.filter((point, index) => index === 0
    || point[0] !== ring[index - 1]![0] || point[1] !== ring[index - 1]![1]);
  if (result.length > 1 && result[0]![0] === result.at(-1)![0]
    && result[0]![1] === result.at(-1)![1]) result.pop();
  return result;
}
function checkPolygon(polygon: EnvironmentPolygon, label: string): void {
  if (polygon == null || !Array.isArray(polygon.outline) || !Array.isArray(polygon.holes)) {
    throw new Error(`City environment missing polygon: ${label}`);
  }
  for (const ring of [polygon.outline, ...polygon.holes]) {
    if (ring.length < 3 || ring.some((point: EnvironmentPoint) => point.length !== 2 || !point.every(Number.isFinite))
      || Math.abs(signedArea(ring)) < EPS) throw new Error(`City environment invalid polygon: ${label}`);
  }
}
function insideRing(point: EnvironmentPoint, ring: readonly EnvironmentPoint[]): boolean {
  let inside = false;
  for (let i = 0, prev = ring.length - 1; i < ring.length; prev = i++) {
    const a = ring[i]!, b = ring[prev]!;
    if ((a[1] > point[1]) !== (b[1] > point[1])
      && point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}
export function insidePolygon(point: EnvironmentPoint, polygon: EnvironmentPolygon): boolean {
  return insideRing(point, polygon.outline) && !polygon.holes.some(hole => insideRing(point, hole));
}
function segmentDistance(point: EnvironmentPoint, a: EnvironmentPoint, b: EnvironmentPoint): number {
  const dx = b[0] - a[0], dz = b[1] - a[1], lengthSq = dx * dx + dz * dz;
  const t = lengthSq > 0 ? Math.max(0, Math.min(1,
    ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / lengthSq)) : 0;
  return Math.hypot(point[0] - a[0] - dx * t, point[1] - a[1] - dz * t);
}
function boundaryDistance(point: EnvironmentPoint, polygon: EnvironmentPolygon): number {
  let minimum = Infinity;
  for (const ring of [polygon.outline, ...polygon.holes]) {
    for (let i = 0; i < ring.length; i++) minimum = Math.min(minimum,
      segmentDistance(point, ring[i]!, ring[(i + 1) % ring.length]!));
  }
  return minimum;
}
/** Exact disk/polygon test, including holes. No sampled circumference gaps. */
export function environmentDiskIntersects(point: EnvironmentPoint, radiusM: number,
    polygon: EnvironmentPolygon): boolean {
  return insidePolygon(point, polygon) || boundaryDistance(point, polygon) <= radiusM + EPS;
}
function diskContained(point: EnvironmentPoint, radiusM: number, polygon: EnvironmentPolygon): boolean {
  return insidePolygon(point, polygon) && boundaryDistance(point, polygon) > radiusM + EPS;
}
interface Bounds { minX: number; maxX: number; minZ: number; maxZ: number; }
/** Single pass over the ring; Math.min/Math.max keep the spread helper's exact
 * Infinity-for-empty and NaN-poisoning semantics. */
function bounds(points: readonly EnvironmentPoint[]): Bounds {
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (let index = 0; index < points.length; index++) {
    const point = points[index]!;
    minX = Math.min(minX, point[0]); maxX = Math.max(maxX, point[0]);
    minZ = Math.min(minZ, point[1]); maxZ = Math.max(maxZ, point[1]);
  }
  return { minX, maxX, minZ, maxZ };
}
function overlap(a: Bounds, b: Bounds): boolean {
  return a.minX <= b.maxX && a.maxX >= b.minX && a.minZ <= b.maxZ && a.maxZ >= b.minZ;
}
class SpatialIndex<T> {
  private readonly cells = new Map<string, Set<T>>();
  add(value: T, box: Bounds): void {
    for (let x = Math.floor(box.minX / GRID_M); x <= Math.floor(box.maxX / GRID_M); x++) {
      for (let z = Math.floor(box.minZ / GRID_M); z <= Math.floor(box.maxZ / GRID_M); z++) {
        const key = `${x}:${z}`;
        if (!this.cells.has(key)) this.cells.set(key, new Set());
        this.cells.get(key)!.add(value);
      }
    }
  }
  query(box: Bounds): Set<T> {
    const values = new Set<T>();
    for (let x = Math.floor(box.minX / GRID_M); x <= Math.floor(box.maxX / GRID_M); x++) {
      for (let z = Math.floor(box.minZ / GRID_M); z <= Math.floor(box.maxZ / GRID_M); z++) {
        for (const value of this.cells.get(`${x}:${z}`) ?? []) values.add(value);
      }
    }
    return values;
  }
}
function diskBounds(point: EnvironmentPoint, radius: number): Bounds {
  return { minX: point[0] - radius, maxX: point[0] + radius,
    minZ: point[1] - radius, maxZ: point[1] + radius };
}
/** FNV-1a plus the MurmurHash3 fmix32 finaliser, so keys differing only in a trailing
 * purpose ("x"/"z", "scale"/"yaw") give independent variates. */
function hash(text: string): number {
  let value = 2166136261;
  for (let i = 0; i < text.length; i++) value = Math.imul(value ^ text.charCodeAt(i), 16777619);
  value ^= value >>> 16; value = Math.imul(value, 0x85ebca6b);
  value ^= value >>> 13; value = Math.imul(value, 0xc2b2ae35);
  value ^= value >>> 16;
  return value >>> 0;
}
function random(seed: number, id: string, purpose: string): number {
  return hash(`${seed}:${id}:${purpose}`) / 4294967296;
}
function triangulate(polygon: EnvironmentPolygon): EnvironmentPoint[][] {
  const outer = openRing(polygon.outline), holes = polygon.holes.map(openRing);
  const points = [...outer, ...holes.flat()];
  const triangles = THREE.ShapeUtils.triangulateShape(outer.map(point => new THREE.Vector2(...point)),
    holes.map(hole => hole.map(point => new THREE.Vector2(...point))));
  if (triangles.length === 0) throw new Error("City environment polygon triangulation failed");
  return triangles.map(indices => {
    const triangle = indices.map(index => points[index]!);
    return signedArea(triangle) > 0 ? triangle : triangle.reverse();
  });
}
function halfPlane(polygon: readonly EnvironmentPoint[], a: EnvironmentPoint, b: EnvironmentPoint,
    keepInside: boolean): EnvironmentPoint[] {
  const side = (p: EnvironmentPoint): number => (b[0] - a[0]) * (p[1] - a[1])
    - (b[1] - a[1]) * (p[0] - a[0]);
  const result: EnvironmentPoint[] = [];
  for (let i = 0; i < polygon.length; i++) {
    const start = polygon[i]!, end = polygon[(i + 1) % polygon.length]!;
    const ds = side(start), de = side(end);
    const startInside = keepInside ? ds >= 0 : ds <= 0;
    const endInside = keepInside ? de >= 0 : de <= 0;
    if (startInside) result.push(start);
    if (startInside !== endInside) {
      const t = ds / (ds - de);
      result.push([start[0] + t * (end[0] - start[0]), start[1] + t * (end[1] - start[1])]);
    }
  }
  return result;
}
/** Subtract one CCW convex obstacle; retained pieces stay convex and disjoint. */
function subtractConvex(polygon: readonly EnvironmentPoint[], obstacle: readonly EnvironmentPoint[]): EnvironmentPoint[][] {
  let remainder = [...polygon];
  const outside: EnvironmentPoint[][] = [];
  for (let i = 0; i < obstacle.length && remainder.length >= 3; i++) {
    const a = obstacle[i]!, b = obstacle[(i + 1) % obstacle.length]!;
    const piece = halfPlane(remainder, a, b, false);
    if (piece.length >= 3 && Math.abs(signedArea(piece)) > EPS) outside.push(piece);
    remainder = halfPlane(remainder, a, b, true);
  }
  return outside;
}
function crosswalkPolygons(crossing: EnvironmentCrossing): EnvironmentPolygon[] {
  return crossing.shape.slice(1).map<EnvironmentPolygon | null>((b, i) => {
    const a = crossing.shape[i]!, length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (length <= EPS) return null;
    const dx = (b[0] - a[0]) / length, dz = (b[1] - a[1]) / length, h = crossing.width / 2;
    return { outline: [[a[0] - dz * h, a[1] + dx * h], [b[0] - dz * h, b[1] + dx * h],
      [b[0] + dz * h, b[1] - dx * h], [a[0] + dz * h, a[1] - dx * h]] as EnvironmentPoint[], holes: [] };
  }).filter((polygon): polygon is EnvironmentPolygon => polygon !== null);
}
function stationGroundPolygon(station: EnvironmentStation): EnvironmentPolygon {
  // Circumscribed polygon contains the measured disk, including between vertices.
  const sides = 24, radius = station.groundRadiusM / Math.cos(Math.PI / sides);
  return { outline: Array.from({ length: sides }, (_, i): EnvironmentPoint => [
    station.x + Math.cos(i * Math.PI * 2 / sides) * radius,
    station.z + Math.sin(i * Math.PI * 2 / sides) * radius]), holes: [] };
}
function recognizedGreen(tags: Readonly<Record<string, string>>): boolean {
  return ["grass", "meadow", "village_green", "recreation_ground"].includes(tags.landuse ?? "")
    || ["grassland", "wood"].includes(tags.natural ?? "") || ["park", "garden"].includes(tags.leisure ?? "");
}
function validate(input: CityEnvironmentInput, config: CityEnvironmentConfig,
    assets: readonly EnvironmentTreeAsset[]): void {
  if (!input.geometryId || !Number.isSafeInteger(config.seed)
    || !Number.isFinite(config.density) || config.density < 0 || config.density > 1
    || !Number.isFinite(config.spacingM) || config.spacingM <= 0
    || !Number.isFinite(config.groundY) || !Number.isFinite(config.grassY)
    || config.scaleRange.length !== 2 || !config.scaleRange.every(value => Number.isFinite(value) && value > 0)
    || config.scaleRange[0] > config.scaleRange[1]
    || Object.values(config.clearancesM).some(value => !Number.isFinite(value) || value < 0)) {
    throw new Error("City environment configuration is invalid");
  }
  if (config.streetTrees.kind === "authored" && (!config.streetTrees.designId
    || !Number.isFinite(config.streetTrees.maxRoadDistanceM) || config.streetTrees.maxRoadDistanceM <= 0)) {
    throw new Error("Authored street trees need a design ID and road distance");
  }
  if (input.roadGeometry !== "published" && input.roadGeometry !== "pending-native-gate") {
    throw new Error("City environment roadGeometry must be \"published\" or \"pending-native-gate\"");
  }
  if (input.roadGeometry === "pending-native-gate") {
    for (const field of ["roadbed", "walkbed", "crossings", "junctions"] as const) {
      if (!Array.isArray(input[field]) || input[field].length !== 0) {
        throw new Error(`City environment pending-native-gate requires empty ${field}`);
      }
    }
    if (config.streetTrees.kind !== "disabled") {
      throw new Error("City environment pending-native-gate requires disabled street trees");
    }
  }
  const ids = new Set(assets.map(asset => asset.id));
  if (ids.size !== assets.length || config.species.length === 0
    || config.species.some(id => !ids.has(id)) || assets.some(asset => !asset.modelUrl
      || [asset.heightM, asset.widthM, asset.depthM, asset.sourceHeight].some(value => !Number.isFinite(value) || value <= 0))) {
    throw new Error("City environment tree asset metadata is missing or invalid");
  }
  for (const field of ["roadbed", "walkbed", "crossings", "junctions", "buildings", "lamps", "signals"] as const) {
    if (!Array.isArray(input[field])) throw new Error(`City environment missing geometry input: ${field}`);
  }
  if ((input.roadGeometry === "published" && (input.roadbed.length === 0 || input.walkbed.length === 0))
    || input.buildings.length === 0) {
    throw new Error("City environment requires loaded roadbed, walkbed, and real building footprints");
  }
  for (const [label, polygons] of [["roadbed", input.roadbed], ["walkbed", input.walkbed],
    ["building", input.buildings], ["junction", input.junctions]] as const) {
    for (const polygon of polygons) checkPolygon(polygon, label);
  }
  checkPolygon(input.extent, "extent");
  for (const station of [...input.lamps, ...input.signals]) {
    if (!station.id || ![station.x, station.z, station.radiusM, station.groundRadiusM].every(Number.isFinite)
      || station.radiusM < 0 || station.groundRadiusM < 0 || station.groundRadiusM > station.radiusM) {
      throw new Error("City environment station envelope is invalid");
    }
  }
  for (const crossing of input.crossings) {
    if (!crossing.id || crossing.shape.length < 2 || !Number.isFinite(crossing.width) || crossing.width <= 0
      || crossing.shape.some(point => point.length !== 2 || !point.every(Number.isFinite))) {
      throw new Error("City environment crossing geometry is invalid");
    }
  }
  if (input.greens === undefined || input.sourceTrees === undefined) throw new Error("City environment requires explicit green/tree input or null");
  const greenIds = new Set<string>();
  for (const green of input.greens ?? []) {
    checkPolygon(green, green.id);
    if (!green.id || greenIds.has(green.id)) throw new Error("City environment duplicate or missing green ID");
    greenIds.add(green.id);
    if (green.provenance.kind === "osm") {
      if (!/^[a-f0-9]{64}$/.test(green.provenance.sourceSha256)
        || !green.provenance.elementId || !green.provenance.sourceElementId
        || !recognizedGreen(green.provenance.tags)) throw new Error(`Unmarked or unbound OSM green: ${green.id}`);
    } else if (!green.provenance.designId) throw new Error(`Authored green needs a design ID: ${green.id}`);
  }
  for (const tree of input.sourceTrees ?? []) {
    if (!tree.id || ![tree.x, tree.z].every(Number.isFinite) || !tree.sourceElementId
      || !/^[a-f0-9]{64}$/.test(tree.sourceSha256)) throw new Error("City environment invalid source tree");
  }
}

/** Deterministic presentation design from loaded geometry. Never changes formal world inputs. */
function isWoodland(green: EnvironmentGreen): boolean {
  return green.provenance.kind === "osm" && green.provenance.tags.natural === "wood";
}

export function generateCityEnvironment(input: CityEnvironmentInput, config: CityEnvironmentConfig,
    assets: readonly EnvironmentTreeAsset[] = BIGCITY_ENVIRONMENT_TREES): CityEnvironmentPlan {
  validate(input, config, assets);
  const missing: string[] = [];
  if (input.roadGeometry === "pending-native-gate") missing.push("road-clearance-pending-native-gate");
  if (input.greens === null) missing.push("source-green-input-unavailable");
  else if (input.greens.filter(green => green.provenance.kind === "osm").length === 0) missing.push("source-has-no-tagged-greens");
  if (input.sourceTrees === null) missing.push("source-tree-input-unavailable");
  else if (input.sourceTrees.length === 0) missing.push("source-has-no-tagged-tree-nodes");
  const rejected: Record<EnvironmentRejection, number> = { density: 0, extent: 0, road: 0, walk: 0,
    building: 0, crossing: 0, junction: 0, lamp: 0, signal: 0, tree: 0, "green-boundary": 0, "not-roadside": 0 };
  const hard: { reason: EnvironmentRejection; polygon: EnvironmentPolygon; clearance: number }[] = [
    ...input.roadbed.map(polygon => ({ reason: "road" as const, polygon, clearance: config.clearancesM.road })),
    ...input.walkbed.map(polygon => ({ reason: "walk" as const, polygon, clearance: config.clearancesM.walk })),
    ...input.buildings.map(polygon => ({ reason: "building" as const, polygon, clearance: config.clearancesM.building })),
    ...input.crossings.flatMap(crossing => crosswalkPolygons(crossing)
      .map(polygon => ({ reason: "crossing" as const, polygon, clearance: config.clearancesM.crossing }))),
    ...input.junctions.map(polygon => ({ reason: "junction" as const, polygon, clearance: config.clearancesM.junction })),
  ];
  const hardIndex = new SpatialIndex<(typeof hard)[number]>();
  const maximumClearance = Math.max(...hard.map(zone => zone.clearance));
  for (const zone of hard) hardIndex.add(zone, bounds(zone.polygon.outline));
  const treeIndex = new SpatialIndex<EnvironmentTreePlacement>();
  const stationIndex = new SpatialIndex<{ station: EnvironmentStation; reason: "lamp" | "signal" }>();
  for (const [reason, stations] of [["lamp", input.lamps], ["signal", input.signals]] as const) {
    for (const station of stations) stationIndex.add({ station, reason }, diskBounds([station.x, station.z], station.radiusM));
  }
  const trees: EnvironmentTreePlacement[] = [];
  let candidates = 0;
  const selectedAssets = config.species.map(id => assets.find(asset => asset.id === id)!);
  const place = (id: string, position: (radius: number) => EnvironmentPoint,
    provenance: TreePlacementProvenance, green?: EnvironmentGreen): void => {
    candidates++;
    if (random(config.seed, id, "density") >= config.density) { rejected.density++; return; }
    const asset = selectedAssets[hash(`${config.seed}:${id}:species`) % selectedAssets.length]!;
    const scale = config.scaleRange[0] + random(config.seed, id, "scale") * (config.scaleRange[1] - config.scaleRange[0]);
    const radius = Math.hypot(asset.widthM / 2, asset.depthM / 2) * scale;
    const point = position(radius);
    if (!diskContained(point, radius, input.extent)) { rejected.extent++; return; }
    if (green !== undefined && !diskContained(point, radius, green)) { rejected["green-boundary"]++; return; }
    for (const zone of hardIndex.query(diskBounds(point, radius + maximumClearance))) {
      if (environmentDiskIntersects(point, radius + zone.clearance, zone.polygon)) { rejected[zone.reason]++; return; }
    }
    for (const { station, reason } of stationIndex.query(diskBounds(point, radius + config.clearancesM.station))) {
      if (Math.hypot(point[0] - station.x, point[1] - station.z)
        <= radius + station.radiusM + config.clearancesM.station) { rejected[reason]++; return; }
    }
    for (const tree of treeIndex.query(diskBounds(point, radius + config.clearancesM.tree))) {
      if (Math.hypot(point[0] - tree.x, point[1] - tree.z)
        <= radius + tree.envelopeRadiusM + config.clearancesM.tree) { rejected.tree++; return; }
    }
    if (provenance.kind === "authored-street-tree" && config.streetTrees.kind === "authored"
      && !input.roadbed.some(polygon => insidePolygon(point, polygon)
        || boundaryDistance(point, polygon) <= (config.streetTrees as Extract<CityEnvironmentConfig["streetTrees"], {kind: "authored"}>).maxRoadDistanceM)) {
      rejected["not-roadside"]++; return;
    }
    const tree: EnvironmentTreePlacement = { id, assetId: asset.id, x: point[0], z: point[1], y: config.groundY,
      yaw: random(config.seed, id, "yaw") * Math.PI * 2, scale,
      heightM: asset.heightM * scale, envelopeRadiusM: radius, provenance };
    trees.push(tree); treeIndex.add(tree, diskBounds(point, radius));

  };
  for (const tree of [...(input.sourceTrees ?? [])].sort((a, b) => a.id.localeCompare(b.id))) {
    place(`source:${tree.id}`, () => [tree.x, tree.z], { kind: "osm-tree",
      sourceSha256: tree.sourceSha256, sourceElementId: tree.sourceElementId });
  }
  if (config.streetTrees.kind === "authored") {
    const designId = config.streetTrees.designId;
    for (const [walkbedIndex, polygon] of input.walkbed.entries()) {
      const ring = openRing(polygon.outline), orientation = Math.sign(signedArea(ring));
      for (let edgeIndex = 0; edgeIndex < ring.length; edgeIndex++) {
        const a = ring[edgeIndex]!, b = ring[(edgeIndex + 1) % ring.length]!;
        const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
        const dx = (b[0] - a[0]) / length, dz = (b[1] - a[1]) / length;
        if (length < config.spacingM * 0.75) continue;
        const phase = 0.35 + random(config.seed, `edge:${walkbedIndex}:${edgeIndex}`, "phase") * 0.3;
        for (let distance = config.spacingM * phase; distance < length - config.spacingM * 0.25;
          distance += config.spacingM) {
          const index = Math.floor(distance / config.spacingM);
          place(`street:${walkbedIndex}:${edgeIndex}:${index}`, radius => {
            const offset = radius + config.clearancesM.walk + 0.08;
            return [a[0] + dx * distance + orientation * dz * offset,
              a[1] + dz * distance - orientation * dx * offset];
          }, { kind: "authored-street-tree", designId, walkbedIndex, edgeIndex });
        }
      }
    }
  }
  if (config.greenTrees) {
    for (const green of [...(input.greens ?? [])].sort((a, b) => a.id.localeCompare(b.id))) {
      const box = bounds(green.outline);
      // Woodland draws a woodland floor instead of lawn, so its trees carry its canopy. A closed canopy
      // uses half the park spacing (about one crown diameter); small woods stay visible.
      const spacing = isWoodland(green) ? config.spacingM / 2 : config.spacingM;
      for (let x = Math.ceil(box.minX / spacing); x < box.maxX / spacing; x++) {
        for (let z = Math.ceil(box.minZ / spacing); z < box.maxZ / spacing; z++) {
          const id = `green:${green.id}:${x}:${z}`;
          const point: EnvironmentPoint = [(x + (random(config.seed, id, "x") - 0.5) * 0.3) * spacing,
            (z + (random(config.seed, id, "z") - 0.5) * 0.3) * spacing];
          if (!insidePolygon(point, green)) continue;
          const provenance: TreePlacementProvenance = green.provenance.kind === "osm"
            ? { kind: "derived-osm-green", greenId: green.id, source: green.provenance }
            : { kind: "authored-green-tree", greenId: green.id, designId: green.provenance.designId };
          place(id, () => point, provenance, green);
        }
      }
    }
  }
  // Boolean difference at triangle level preserves concavities and source holes.
  const clippingIndex = new SpatialIndex<{ points: EnvironmentPoint[]; box: Bounds }>();
  const grassObstacles = [...hard.map(zone => zone.polygon),
    ...[...input.lamps, ...input.signals].filter(station => station.groundRadiusM > 0).map(stationGroundPolygon)];
  for (const polygon of grassObstacles) for (const points of triangulate(polygon)) {
    const triangle = { points, box: bounds(points) }; clippingIndex.add(triangle, triangle.box);
  }
  const extentTriangles = triangulate(input.extent);
  const grass: EnvironmentGrassPatch[] = [], woodlandFloor: EnvironmentGrassPatch[] = [];
  let grassRemovedAreaM2 = 0, grassFullyExcludedCount = 0;
  const sortedGreens = [...(input.greens ?? [])].sort((a, b) => a.id.localeCompare(b.id));
  // Lawn first, then woodland floor in the ground the lawn left, so lawn coverage is
  // independent of woodland and each point keeps one ground surface.
  for (const green of [...sortedGreens.filter(green => !isWoodland(green)), ...sortedGreens.filter(isWoodland)]) {
    const woodland = isWoodland(green);
    const sourceTriangles = triangulate(green);
    const sourceAreaM2 = sourceTriangles.reduce((sum, triangle) => sum + Math.abs(signedArea(triangle)), 0);
    const clipped: EnvironmentPoint[][] = [];
    for (const triangle of sourceTriangles) {
      // Intersect with each disjoint extent triangle before subtracting hard surfaces.
      for (const extent of extentTriangles) {
        let intersection: EnvironmentPoint[] = triangle;
        for (let i = 0; i < extent.length && intersection.length >= 3; i++) {
          intersection = halfPlane(intersection, extent[i]!, extent[(i + 1) % extent.length]!, true);
        }
        if (intersection.length < 3 || Math.abs(signedArea(intersection)) <= EPS) continue;
        let pieces = [intersection];
        for (const obstacle of clippingIndex.query(bounds(intersection))) {
          pieces = pieces.flatMap(piece => overlap(bounds(piece), obstacle.box)
            ? subtractConvex(piece, obstacle.points) : [piece]);
          if (pieces.length === 0) break;
        }
        for (const piece of pieces) for (let i = 1; i < piece.length - 1; i++) {
          const points = [piece[0]!, piece[i]!, piece[i + 1]!];
          if (Math.abs(signedArea(points)) > EPS) clipped.push(points);
        }
      }
    }
    const areaM2 = clipped.reduce((sum, triangle) => sum + Math.abs(signedArea(triangle)), 0);
    if (woodland) {
      if (clipped.length > 0) woodlandFloor.push({ id: green.id, triangles: clipped, sourceAreaM2, areaM2, provenance: green.provenance });
      else continue;
    } else {
      grassRemovedAreaM2 += Math.max(0, sourceAreaM2 - areaM2);
      if (clipped.length === 0) { grassFullyExcludedCount++; continue; }
      grass.push({ id: green.id, triangles: clipped, sourceAreaM2, areaM2, provenance: green.provenance });
    }
    // Source-labelled regions can overlap. Retain one ground surface at each point.
    for (const points of clipped) {
      const triangle = { points, box: bounds(points) }; clippingIndex.add(triangle, triangle.box);
    }
  }
  return { schemaVersion: "aero-bench.city-environment/v1", geometryId: input.geometryId,
    roadGeometry: input.roadGeometry, config, assets: selectedAssets, trees, grass, woodlandFloor, missing,
    stats: { candidates, accepted: trees.length, rejected,
      sourceGreenCount: (input.greens ?? []).filter(green => green.provenance.kind === "osm").length,
      authoredGreenCount: (input.greens ?? []).filter(green => green.provenance.kind === "authored").length,
      grassAreaM2: grass.reduce((sum, patch) => sum + patch.areaM2, 0), grassRemovedAreaM2, grassFullyExcludedCount,
      woodlandFloorAreaM2: woodlandFloor.reduce((sum, patch) => sum + patch.areaM2, 0) } };
}

interface TreeTemplate {
  readonly asset: EnvironmentTreeAsset;
  readonly parts: readonly { readonly near: THREE.BufferGeometry; readonly far: THREE.BufferGeometry;
    readonly material: THREE.MeshStandardMaterial }[];
}

/** Preserve source triangles on the lower stem so far LOD keeps its ground anchor. */
function treeFarGeometry(near: THREE.BufferGeometry, heightM: number): THREE.BufferGeometry {
  const temporary = new Set<THREE.BufferGeometry>();
  const flat = near.index === null ? near : near.toNonIndexed();
  if (flat !== near) temporary.add(flat);
  try {
    const position = flat.getAttribute("position"), stemIndices: number[] = [], crownIndices: number[] = [];
    for (let i = 0; i < position.count; i += 3) {
      const indices = Math.min(position.getY(i), position.getY(i + 1), position.getY(i + 2)) < heightM * .25
        ? stemIndices : crownIndices;
      indices.push(i, i + 1, i + 2);
    }
    const subset = (indices: readonly number[]): THREE.BufferGeometry => {
      const geometry = new THREE.BufferGeometry(); temporary.add(geometry);
      for (const [name, attribute] of Object.entries(flat.attributes)) {
        const values: number[] = [];
        for (const index of indices) for (let c = 0; c < attribute.itemSize; c++) values.push(attribute.getComponent(index, c));
        geometry.setAttribute(name, new THREE.Float32BufferAttribute(values, attribute.itemSize, attribute.normalized));
      }
      return geometry;
    };
    const stem = subset(stemIndices);
    if (crownIndices.length === 0) { temporary.delete(stem); return stem; }
    const crown = subset(crownIndices), indexed = mergeVertices(crown); temporary.add(indexed);
    const simplified = new SimplifyModifier().modify(indexed, Math.floor(indexed.getAttribute("position").count * .55));
    temporary.add(simplified);
    const flatCrown = simplified.index === null ? simplified : simplified.toNonIndexed(); temporary.add(flatCrown);
    const far = mergeGeometries([stem, flatCrown]);
    if (far === null) throw new Error("City environment stem and crown LOD cannot be merged");
    return far;
  } finally { temporary.forEach(geometry => geometry.dispose()); }
}

export interface CityEnvironmentAssets {
  readonly trees: ReadonlyMap<string, TreeTemplate>;
  dispose(): void;
}
export interface CityEnvironmentAssetReader {
  /** Resolve to verified URLs for bundle mode; public viewer may use existing asset URLs. */
  url(path: string): Promise<string>;
  json(path: string): Promise<unknown>;
}
const publicReader: CityEnvironmentAssetReader = {
  url: async path => path,
  json: async path => {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`City environment asset fetch failed: ${path} ${response.status}`);
    return response.json();
  },
};

/** Load supplied trees once, preserve their texture atlas and scale every axis uniformly. */
export async function loadCityEnvironmentAssets(assets: readonly EnvironmentTreeAsset[],
    reader: CityEnvironmentAssetReader = publicReader): Promise<CityEnvironmentAssets> {
  const treePath = "/models/bigcity/environment/tree-atlas-v1.webp";
  const treeMetadata = await reader.json("/models/bigcity/environment/tree-atlas-v1.json") as {
    schemaVersion: string; mode: string; nativeAlphaZeroPixels: number; sourceGuid: string;
    sourceSha256: string; size: [number, number];
  };
  if (treeMetadata?.schemaVersion !== "aero-bench.city-environment-texture/v1" || treeMetadata.mode !== "RGBA"
    || treeMetadata.nativeAlphaZeroPixels <= 0 || treeMetadata.sourceGuid !== "53cd429b20f63264ca316e2c356575e9"
    || !/^[a-f0-9]{64}$/.test(treeMetadata.sourceSha256) || !Array.isArray(treeMetadata.size)
    || treeMetadata.size.length !== 2) throw new Error("Supplied tree source alpha is unavailable");
  const [treeTextureUrl, modelUrls] = await Promise.all([
    reader.url(treePath), Promise.all(assets.map(asset => reader.url(asset.modelUrl))),
  ]);
  const templates = new Map<string, TreeTemplate>();
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  const dispose = (): void => {
    geometries.forEach(geometry => geometry.dispose()); materials.forEach(material => material.dispose());
    textures.forEach(texture => texture.dispose());
  };
  try {
    // FBXLoader resolves before its TextureLoader image dependencies. Load the
    // atlas first and return that complete texture to every declared FBX map.
    const treeTexture = await new THREE.TextureLoader().loadAsync(treeTextureUrl).catch((cause: unknown) => {
      throw new Error(`City environment texture load failed: ${treeTextureUrl}`, { cause });
    }) as THREE.Texture<HTMLImageElement>;
    textures.add(treeTexture);
    if (treeTexture.image.width !== treeMetadata.size[0] || treeTexture.image.height !== treeMetadata.size[1]) {
      throw new Error("Supplied tree atlas image dimensions changed");
    }
    treeTexture.colorSpace = THREE.SRGBColorSpace; treeTexture.anisotropy = 4;
    class SharedAtlasLoader extends THREE.TextureLoader {
      override load(url: string): THREE.Texture<HTMLImageElement> {
        const name = decodeURIComponent(url.replaceAll("\\", "/").split("/").pop() ?? "").toLowerCase();
        if (name === "tree.tif" || name === "tree.psd") return treeTexture;
        throw new Error(`City environment undeclared FBX texture dependency: ${url}`);
      }
    }
    const manager = new THREE.LoadingManager();
    manager.addHandler(/./, new SharedAtlasLoader(manager));
    const treeMaterial = new THREE.MeshStandardMaterial({ map: treeTexture, color: 0xffffff,
      roughness: 0.95, metalness: 0, side: THREE.DoubleSide, alphaTest: 0.5 });
    materials.add(treeMaterial);
    const loadedModels = await Promise.allSettled(assets.map(async (asset, index) => {
      // The tree trunks load the same FBX files; share their fetched bytes and parse a private copy.
      const model = await loadSharedFbx(new FBXLoader(manager), modelUrls[index]!);
      model.updateMatrixWorld(true);
      model.traverse(node => {
        if (!(node instanceof THREE.Mesh)) return;
        geometries.add(node.geometry);
        for (const material of Array.isArray(node.material) ? node.material : [node.material]) materials.add(material);
      });
      const box = new THREE.Box3().setFromObject(model), size = box.getSize(new THREE.Vector3());
      if (box.isEmpty() || Math.abs(size.y - asset.sourceHeight) > 0.001) {
        throw new Error(`City environment tree source dimensions changed: ${asset.id}`);
      }
      const unitScale = asset.heightM / size.y, center = box.getCenter(new THREE.Vector3());
      if (Math.abs(size.x * unitScale - asset.widthM) > 0.002
        || Math.abs(size.z * unitScale - asset.depthM) > 0.002) throw new Error(`Tree envelope metadata mismatch: ${asset.id}`);
      const normalization = new THREE.Matrix4().makeScale(unitScale, unitScale, unitScale)
        .multiply(new THREE.Matrix4().makeTranslation(-center.x, -box.min.y, -center.z));
      const parts: TreeTemplate["parts"][number][] = [];
      model.traverse(node => {
        if (!(node instanceof THREE.Mesh)) return;
        if (Array.isArray(node.material)) throw new Error(`Tree material groups need explicit asset conversion: ${asset.id}`);
        const near = node.geometry.clone().applyMatrix4(normalization.clone().multiply(node.matrixWorld));
        near.computeBoundingBox(); near.computeBoundingSphere(); geometries.add(near);
        const far = treeFarGeometry(near, asset.heightM);
        far.computeBoundingBox(); far.computeBoundingSphere(); geometries.add(far);
        if (!(node.material instanceof THREE.MeshPhongMaterial) && !(node.material instanceof THREE.MeshStandardMaterial)) {
          throw new Error(`City environment unsupported tree material: ${asset.id}`);
        }
        const source = node.material, map = source.map;
        if (map === null) throw new Error(`Supplied tree atlas is missing: ${asset.id}`);
        if (map !== treeTexture || map.repeat.x !== 1 || map.repeat.y !== 1 || map.offset.x !== 0 || map.offset.y !== 0) {
          throw new Error(`Supplied tree atlas transform changed: ${asset.id}`);
        }
        parts.push({ near, far, material: treeMaterial });
      });
      if (parts.length === 0) throw new Error(`Supplied tree mesh is missing: ${asset.id}`);
      templates.set(asset.id, { asset, parts });
      model.traverse(node => {
        if (!(node instanceof THREE.Mesh)) return;
        node.geometry.dispose(); geometries.delete(node.geometry);
        for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
          material.dispose(); materials.delete(material);
        }
      });
    }));
    for (const [index, result] of loadedModels.entries()) if (result.status === "rejected") {
      throw new Error(`City environment tree asset failed: ${assets[index]!.id}`, { cause: result.reason });
    }
    return { trees: templates, dispose };
  } catch (error) { dispose(); throw error; }
}

export interface CityEnvironmentRenderer {
  readonly group: THREE.Group;
  readonly staticObstacles: readonly PlacementBox[];
  updateLod(camera: THREE.Camera): void;
  /** Releases instance buffers and ground geometry; shared geometry/materials stay in the asset set and kit. */
  dispose(): void;
}

function stationFromBounds(id: string, box: THREE.Box3): EnvironmentStation {
  if (!id || box.isEmpty() || ![...box.min.toArray(), ...box.max.toArray()].every(Number.isFinite)) {
    throw new Error(`City environment fixture envelope is missing: ${id}`);
  }
  const center = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
  // A bounding box has no separate ground measurement, so the whole envelope clears the ground too.
  const radiusM = Math.hypot(size.x, size.z) / 2;
  return { id, x: center.x, z: center.z, radiusM, groundRadiusM: radiusM };
}

/** Measure the whole assembly of each signal or individually loaded fixture. */
export function measureEnvironmentStations(fixtures: readonly { readonly id: string; readonly object: THREE.Object3D }[]): EnvironmentStation[] {
  return fixtures.map(({ id, object }) => {
    object.updateWorldMatrix(true, true);
    if (object instanceof THREE.InstancedMesh) throw new Error("Instanced fixtures require per-instance measurement");
    return stationFromBounds(id, new THREE.Box3().setFromObject(object, true));
  });
}

/** Include each physical part; caller excludes light sprites and ground glow. */
export function measureInstancedEnvironmentStations(parts: readonly THREE.InstancedMesh[], ids: readonly string[]): EnvironmentStation[] {
  if (ids.length === 0 && parts.every(part => part.count === 0)) return [];
  if (parts.length === 0 || parts.some(part => part.count !== ids.length)) {
    throw new Error("City environment instanced fixture inventory does not match");
  }
  const boxes = ids.map(() => new THREE.Box3()), matrix = new THREE.Matrix4();
  for (const part of parts) {
    part.updateWorldMatrix(true, false); part.geometry.computeBoundingBox();
    if (part.geometry.boundingBox === null) throw new Error("City environment fixture source bounds are missing");
    for (let i = 0; i < ids.length; i++) {
      part.getMatrixAt(i, matrix); matrix.premultiply(part.matrixWorld);
      boxes[i]!.union(part.geometry.boundingBox.clone().applyMatrix4(matrix));
    }
  }
  return boxes.map((box, index) => stationFromBounds(ids[index]!, box));
}

/** Shared ground materials and the per-green material assignments for lawn and woodland floor. */
export interface CityEnvironmentSurfaces {
  readonly kit: TerrainSurfaceKit;
  readonly assignments: ReadonlyMap<string, GroundMaterialAssignment>;
}

export function createCityEnvironment(plan: CityEnvironmentPlan, assets: CityEnvironmentAssets,
    surfaces: CityEnvironmentSurfaces,
    settings: { readonly cellSizeM: number; readonly nearDistanceM: number; readonly maxDistanceM: number } =
      { cellSizeM: 80, nearDistanceM: 130, maxDistanceM: 1100 }): CityEnvironmentRenderer {
  if (![settings.cellSizeM, settings.nearDistanceM, settings.maxDistanceM].every(value => Number.isFinite(value) && value > 0)
    || settings.maxDistanceM < settings.nearDistanceM) throw new Error("City environment LOD settings are invalid");
  const group = new THREE.Group(); group.name = "Automatic city vegetation with source and authored provenance";
  // Everything this function creates (instanced meshes, ground geometries) hangs off `group`
  // or is listed here; on any construction failure they are disposed before the error is
  // rethrown, because the caller never receives a renderer to dispose.
  const groundGeometries: THREE.BufferGeometry[] = [];
  try {
    return createCityEnvironmentInto(plan, assets, surfaces, settings, group, groundGeometries);
  } catch (error) {
    group.traverse(node => { if (node instanceof THREE.InstancedMesh) node.dispose(); });
    groundGeometries.forEach(geometry => geometry.dispose());
    group.clear();
    throw error;
  }
}

function createCityEnvironmentInto(plan: CityEnvironmentPlan, assets: CityEnvironmentAssets,
    surfaces: CityEnvironmentSurfaces,
    settings: { readonly cellSizeM: number; readonly nearDistanceM: number; readonly maxDistanceM: number },
    group: THREE.Group, groundGeometries: THREE.BufferGeometry[]): CityEnvironmentRenderer {
  const chunks = new Map<string, EnvironmentTreePlacement[]>();
  const assetCounts = new Map<string, number>();
  for (const tree of plan.trees) {
    if (!assets.trees.has(tree.assetId)) throw new Error(`City environment asset was not loaded: ${tree.assetId}`);
    assetCounts.set(tree.assetId, (assetCounts.get(tree.assetId) ?? 0) + 1);
    const key = `${tree.assetId}:${Math.floor(tree.x / settings.cellSizeM)}:${Math.floor(tree.z / settings.cellSizeM)}`;
    if (!chunks.has(key)) chunks.set(key, []);
    chunks.get(key)!.push(tree);
  }
  type TreeInstance = { readonly matrix: THREE.Matrix4; readonly sphere: THREE.Sphere };
  type FarSet = { readonly meshes: THREE.InstancedMesh[]; count: number };
  const farSets = new Map<string, FarSet>();
  const lods: { near: THREE.Group; box: THREE.Box3; trees: TreeInstance[]; farSet: FarSet }[] = [];
  for (const [assetId, count] of assetCounts) {
    const template = assets.trees.get(assetId)!;
    const meshes = template.parts.map((part, index) => {
      const mesh = new THREE.InstancedMesh(part.far, part.material, count);
      group.add(mesh);
      mesh.name = `${assetId}:far:${index}:instanced`; mesh.castShadow = false; mesh.receiveShadow = true;
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage); mesh.count = 0; mesh.visible = false;
      return mesh;
    });
    farSets.set(assetId, { meshes, count: 0 });
  }
  const rotation = new THREE.Quaternion(), position = new THREE.Vector3(), scale = new THREE.Vector3();
  const yAxis = new THREE.Vector3(0, 1, 0);
  for (const [key, placements] of chunks) {
    const template = assets.trees.get(placements[0]!.assetId)!;
    const near = new THREE.Group();
    group.add(near);
    near.name = `${key}:near`;
    const box = new THREE.Box3();
    for (const tree of placements) box.union(new THREE.Box3(
      new THREE.Vector3(tree.x - tree.envelopeRadiusM, tree.y, tree.z - tree.envelopeRadiusM),
      new THREE.Vector3(tree.x + tree.envelopeRadiusM, tree.y + tree.heightM, tree.z + tree.envelopeRadiusM)));
    // Static world matrices and actual far-geometry bounds are shared by all camera updates.
    const trees: TreeInstance[] = placements.map(tree => {
      position.set(tree.x, tree.y, tree.z); rotation.setFromAxisAngle(yAxis, tree.yaw); scale.setScalar(tree.scale);
      const matrix = new THREE.Matrix4().compose(position, rotation, scale), sphere = new THREE.Sphere().makeEmpty();
      for (const part of template.parts) {
        if (part.far.boundingSphere === null) part.far.computeBoundingSphere();
        sphere.union(part.far.boundingSphere!.clone().applyMatrix4(matrix));
      }
      return { matrix, sphere };
    });
    for (const part of template.parts) {
      const mesh = new THREE.InstancedMesh(part.near, part.material, placements.length);
      near.add(mesh);
      mesh.name = `${key}:near:instanced`; mesh.castShadow = true; mesh.receiveShadow = true;
      for (const [i, tree] of trees.entries()) mesh.setMatrixAt(i, tree.matrix);
      mesh.instanceMatrix.needsUpdate = true; mesh.instanceMatrix.setUsage(THREE.StaticDrawUsage);
      mesh.computeBoundingBox(); mesh.computeBoundingSphere();
    }
    lods.push({ near, box, trees, farSet: farSets.get(placements[0]!.assetId)! });
  }
  // One merged mesh per material; per-green uv offsets and tints are vertex attributes.
  for (const [patches, woodland] of [[plan.grass, false], [plan.woodlandFloor, true]] as const) {
    const byMaterial = new Map<THREE.Material, { assignment: GroundMaterialAssignment; triangles: EnvironmentGrassPatch["triangles"] }[]>();
    for (const patch of patches) {
      const assignment = surfaces.assignments.get(patch.id);
      if (assignment === undefined) throw new Error(`City environment green has no material assignment: ${patch.id}`);
      const material = surfaces.kit.material(assignment);
      if (!byMaterial.has(material)) byMaterial.set(material, []);
      byMaterial.get(material)!.push({ assignment, triangles: patch.triangles });
    }
    for (const [material, polygons] of byMaterial) {
      const geometry = terrainSurfaceGeometry(polygons, plan.config.grassY, surfaces.kit);
      groundGeometries.push(geometry);
      const mesh = new THREE.Mesh(geometry, material);
      const { family, preset } = polygons[0]!.assignment;
      mesh.name = woodland ? "Woodland floor clipped to labelled woodland"
        : family === "grass" ? "Grass clipped to labelled green areas"
          : `Green-area ground ${preset} clipped to labelled green areas`;
      mesh.userData.terrainPreset = preset;
      mesh.userData.groundRole = woodland ? "woodland-floor" : "lawn";
      mesh.userData.greenIds = polygons.map(polygon => polygon.assignment.polygonId);
      mesh.receiveShadow = true; group.add(mesh);
    }
  }
  const staticObstacles: PlacementBox[] = plan.trees.map(tree => ({ id: tree.id, kind: "street_asset",
    x: tree.x, z: tree.z, widthM: tree.envelopeRadiusM * 2, depthM: tree.envelopeRadiusM * 2,
    heightM: tree.heightM, baseY: tree.y, rotationDeg: 0 }));
  group.userData.environmentPlan = plan; group.userData.staticObstacles = staticObstacles;
  group.userData.environmentRoadGeometry = plan.roadGeometry;
  group.userData.treeCount = plan.trees.length; group.userData.treeInstanceCount = plan.trees.length;
  group.userData.grassAreaM2 = plan.stats.grassAreaM2; group.userData.environmentMissing = plan.missing;
  group.userData.woodlandFloorAreaM2 = plan.stats.woodlandFloorAreaM2;
  const cameraPosition = new THREE.Vector3(), viewProjection = new THREE.Matrix4(), frustum = new THREE.Frustum();
  return { group, staticObstacles,
    updateLod: camera => {
      camera.updateWorldMatrix(true, false); camera.getWorldPosition(cameraPosition);
      viewProjection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
      frustum.setFromProjectionMatrix(viewProjection, camera.coordinateSystem, camera.reversedDepth);
      for (const set of farSets.values()) set.count = 0;
      for (const lod of lods) {
        const distance = lod.box.distanceToPoint(cameraPosition);
        lod.near.visible = distance <= settings.nearDistanceM;
        // Keep the original near cells, including off-camera shadow casters.
        // Far trees do not cast shadows, so they can be packed by the active view.
        if (!lod.near.visible && distance <= settings.maxDistanceM) for (const tree of lod.trees) {
          if (!frustum.intersectsSphere(tree.sphere)) continue;
          for (const mesh of lod.farSet.meshes) mesh.setMatrixAt(lod.farSet.count, tree.matrix);
          lod.farSet.count++;
        }
      }
      for (const set of farSets.values()) for (const mesh of set.meshes) {
        mesh.count = set.count; mesh.visible = set.count > 0;
        if (set.count > 0) {
          mesh.instanceMatrix.clearUpdateRanges(); mesh.instanceMatrix.addUpdateRange(0, set.count * 16);
          mesh.instanceMatrix.needsUpdate = true; mesh.computeBoundingSphere();
        }
      }
    },
    dispose: () => {
      group.traverse(node => { if (node instanceof THREE.InstancedMesh) node.dispose(); });
      groundGeometries.forEach(geometry => geometry.dispose()); group.clear();
    } };
}
