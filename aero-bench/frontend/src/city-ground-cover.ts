import * as THREE from "three";
import type { CityEnvironmentSource, EnvironmentPoint, EnvironmentPolygon } from "./city-environment";
import type { GroundMaterialAssignment } from "./city-ground-material-rules";
import { terrainSurfaceGeometry, type TerrainSurfaceKit } from "./city-terrain-surfaces";

export const CITY_GROUND_COVER_KINDS = [
  "pitch", "construction", "brownfield", "parking", "pedestrian_area",
  "square", "water", "explicit_surface",
] as const;
export type CityGroundCoverKind = typeof CITY_GROUND_COVER_KINDS[number];

export interface CityGroundCoverProvenance {
  readonly kind: "osm";
  readonly sourceSha256: string;
  readonly elementType: "way" | "relation";
  readonly elementId: string;
  readonly sourceElementType: "way" | "relation";
  readonly sourceElementId: string;
  readonly tags: Readonly<Record<string, string>>;
}

export interface CityGroundCover extends EnvironmentPolygon {
  readonly id: string;
  readonly kind: CityGroundCoverKind;
  readonly surface: string | null;
  readonly provenance: CityGroundCoverProvenance;
}

export interface CityGroundCoverPlanEntry extends CityGroundCover {
  readonly triangles: readonly (readonly EnvironmentPoint[])[];
  readonly sourceAreaM2: number;
  readonly drawnAreaM2: number;
  readonly removedAreaM2: number;
}

export interface CityGroundCoverPlan {
  readonly schemaVersion: "aero-bench.city-ground-cover-plan/v1";
  readonly geometryId: string;
  readonly sourceCount: number;
  readonly covers: readonly CityGroundCoverPlanEntry[];
  readonly fullyClippedIds: readonly string[];
  readonly stats: {
    readonly sourceAreaM2: number;
    readonly drawnAreaM2: number;
    readonly removedAreaM2: number;
    readonly countsByKind: Readonly<Record<CityGroundCoverKind, number>>;
    readonly drawnAreaM2ByKind: Readonly<Record<CityGroundCoverKind, number>>;
  };
}

export interface VerifiedGroundCoverGeometry {
  readonly geometryId: string;
  readonly roadGeometry: "published";
  readonly roadbed: readonly EnvironmentPolygon[];
  readonly walkbed: readonly EnvironmentPolygon[];
  readonly buildings: readonly EnvironmentPolygon[];
}

const EPS = 1e-7;
const GRID_M = 64;
const ZONING_LANDUSES = new Set(["residential", "commercial", "retail"]);
const WATER_NATURAL_VALUES = new Set(["water"]);
const WATER_WATERWAY_VALUES = new Set(["riverbank", "dock", "canal"]);
const WATER_LANDUSE_VALUES = new Set(["reservoir", "basin"]);

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`City ground cover ${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], label: string): void {
  const expected = new Set(keys);
  if (Object.keys(value).some(key => !expected.has(key)) || keys.some(key => !(key in value))) {
    throw new Error(`City ground cover ${label} fields are invalid`);
  }
}

function stringMap(value: unknown, label: string): Readonly<Record<string, string>> {
  const input = record(value, label);
  if (Object.values(input).some(item => typeof item !== "string")) {
    throw new Error(`City ground cover ${label} values must be strings`);
  }
  return input as Readonly<Record<string, string>>;
}

/** Mirrors city_environment_source.ground_cover_kind. */
export function groundCoverKindFromTags(tags: Readonly<Record<string, string>>): CityGroundCoverKind | null {
  if (ZONING_LANDUSES.has(tags.landuse ?? "") || tags.building !== undefined
    || tags["building:part"] !== undefined) return null;
  if (WATER_NATURAL_VALUES.has(tags.natural ?? "") || WATER_WATERWAY_VALUES.has(tags.waterway ?? "")
    || WATER_LANDUSE_VALUES.has(tags.landuse ?? "")) return "water";
  if (tags.leisure === "pitch") return "pitch";
  if (tags.landuse === "construction") return "construction";
  if (tags.landuse === "brownfield") return "brownfield";
  if (["parking", "parking_space"].includes(tags.amenity ?? "") || tags.landuse === "parking") return "parking";
  if (tags.highway === "pedestrian") return "pedestrian_area";
  if (tags.place === "square") return "square";
  return tags.surface?.trim() ? "explicit_surface" : null;
}

function signedArea(ring: readonly EnvironmentPoint[]): number {
  let area = 0;
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i]!, b = ring[(i + 1) % ring.length]!;
    area += a[0] * b[1] - b[0] * a[1];
  }
  return area / 2;
}

function samePoint(a: EnvironmentPoint, b: EnvironmentPoint): boolean {
  return a[0] === b[0] && a[1] === b[1];
}

function openRing(raw: unknown, label: string): EnvironmentPoint[] {
  if (!Array.isArray(raw)) throw new Error(`City ground cover ${label} must be a ring`);
  const ring = raw.map((value, index): EnvironmentPoint => {
    if (!Array.isArray(value) || value.length !== 2 || !value.every(Number.isFinite)) {
      throw new Error(`City ground cover ${label}[${index}] is invalid`);
    }
    return [value[0] as number, value[1] as number];
  });
  if (ring.length > 1 && samePoint(ring[0]!, ring.at(-1)!)) ring.pop();
  if (ring.length < 3 || ring.some((point, index) => samePoint(point, ring[(index + 1) % ring.length]!))
    || Math.abs(signedArea(ring)) <= EPS) throw new Error(`City ground cover ${label} is degenerate`);
  return ring;
}

function orientation(a: EnvironmentPoint, b: EnvironmentPoint, c: EnvironmentPoint): number {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

function onSegment(point: EnvironmentPoint, a: EnvironmentPoint, b: EnvironmentPoint): boolean {
  return Math.abs(orientation(a, b, point)) <= EPS
    && point[0] >= Math.min(a[0], b[0]) - EPS && point[0] <= Math.max(a[0], b[0]) + EPS
    && point[1] >= Math.min(a[1], b[1]) - EPS && point[1] <= Math.max(a[1], b[1]) + EPS;
}

function segmentsIntersect(a: EnvironmentPoint, b: EnvironmentPoint, c: EnvironmentPoint, d: EnvironmentPoint): boolean {
  const abC = orientation(a, b, c), abD = orientation(a, b, d);
  const cdA = orientation(c, d, a), cdB = orientation(c, d, b);
  if ((abC > EPS && abD < -EPS || abC < -EPS && abD > EPS)
    && (cdA > EPS && cdB < -EPS || cdA < -EPS && cdB > EPS)) return true;
  return Math.abs(abC) <= EPS && onSegment(c, a, b) || Math.abs(abD) <= EPS && onSegment(d, a, b)
    || Math.abs(cdA) <= EPS && onSegment(a, c, d) || Math.abs(cdB) <= EPS && onSegment(b, c, d);
}

function ringSelfIntersects(ring: readonly EnvironmentPoint[]): boolean {
  for (let i = 0; i < ring.length; i++) for (let j = i + 1; j < ring.length; j++) {
    if (j === i + 1 || i === 0 && j === ring.length - 1) continue;
    if (segmentsIntersect(ring[i]!, ring[(i + 1) % ring.length]!, ring[j]!, ring[(j + 1) % ring.length]!)) return true;
  }
  return false;
}

function ringsIntersect(a: readonly EnvironmentPoint[], b: readonly EnvironmentPoint[]): boolean {
  return a.some((start, i) => b.some((other, j) =>
    segmentsIntersect(start, a[(i + 1) % a.length]!, other, b[(j + 1) % b.length]!)));
}

function insideRing(point: EnvironmentPoint, ring: readonly EnvironmentPoint[]): boolean {
  let inside = false;
  for (let i = 0, previous = ring.length - 1; i < ring.length; previous = i++) {
    const a = ring[i]!, b = ring[previous]!;
    if (onSegment(point, a, b)) return false;
    if ((a[1] > point[1]) !== (b[1] > point[1])
      && point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

function parsePolygon(input: Record<string, unknown>, label: string, strictTopology = true): EnvironmentPolygon {
  const outline = openRing(input.outline, `${label}.outline`);
  if (!Array.isArray(input.holes)) throw new Error(`City ground cover ${label}.holes must be an array`);
  const holes = input.holes.map((hole, index) => openRing(hole, `${label}.holes[${index}]`));
  if (strictTopology && (ringSelfIntersects(outline) || holes.some(ringSelfIntersects)
    || holes.some(hole => !insideRing(hole[0]!, outline) || ringsIntersect(outline, hole))
    || holes.some((hole, index) => holes.some((other, otherIndex) => otherIndex > index
      && (ringsIntersect(hole, other) || insideRing(hole[0]!, other) || insideRing(other[0]!, hole)))))) {
    throw new Error(`City ground cover ${label} polygon is invalid`);
  }
  return { outline, holes };
}

function polygonArea(polygon: EnvironmentPolygon): number {
  return Math.abs(signedArea(polygon.outline))
    - polygon.holes.reduce((sum, hole) => sum + Math.abs(signedArea(hole)), 0);
}

/** Triangulate an EnvironmentPolygon while retaining source holes. */
function triangulatePolygon(polygon: EnvironmentPolygon, label: string, strictTopology: boolean): EnvironmentPoint[][] {
  const input = parsePolygon(polygon as unknown as Record<string, unknown>, label, strictTopology);
  const outline = [...input.outline], holes = input.holes.map(hole => [...hole]);
  const points = [...outline, ...holes.flat()];
  const indices = THREE.ShapeUtils.triangulateShape(outline.map(point => new THREE.Vector2(...point)),
    holes.map(hole => hole.map(point => new THREE.Vector2(...point))));
  const triangles = indices.map(triangle => {
    const pointsForTriangle = triangle.map(index => points[index]!);
    return signedArea(pointsForTriangle) > 0 ? pointsForTriangle : pointsForTriangle.reverse();
  });
  const expectedArea = polygonArea(input);
  const triangulatedArea = triangles.reduce((sum, triangle) => sum + Math.abs(signedArea(triangle)), 0);
  if (triangles.length === 0 || expectedArea <= EPS
    || Math.abs(triangulatedArea - expectedArea) > Math.max(1e-5, expectedArea * 1e-8)) {
    throw new Error(`City ground cover ${label} triangulation changed polygon area`);
  }
  return triangles;
}

export function triangulateEnvironmentPolygon(polygon: EnvironmentPolygon,
    label = "polygon"): EnvironmentPoint[][] {
  return triangulatePolygon(polygon, label, true);
}

function provenance(raw: unknown, authoritySha256: string, label: string): CityGroundCoverProvenance {
  const source = record(raw, `${label}.provenance`);
  exactKeys(source, ["kind", "sourceSha256", "elementType", "elementId", "sourceElementType",
    "sourceElementId", "tags"], `${label}.provenance`);
  const tags = stringMap(source.tags, `${label}.provenance.tags`);
  if (source.kind !== "osm" || source.sourceSha256 !== authoritySha256
    || !["way", "relation"].includes(source.elementType as string)
    || !["way", "relation"].includes(source.sourceElementType as string)
    || typeof source.elementId !== "string" || !/^-?\d+$/.test(source.elementId)
    || typeof source.sourceElementId !== "string" || source.sourceElementId.length === 0) {
    throw new Error(`City ground cover ${label} provenance is invalid`);
  }
  return { kind: "osm", sourceSha256: source.sourceSha256 as string,
    elementType: source.elementType as "way" | "relation", elementId: source.elementId,
    sourceElementType: source.sourceElementType as "way" | "relation",
    sourceElementId: source.sourceElementId, tags };
}

type SourceAuthority = Pick<CityEnvironmentSource["source"], "osmSha256" | "objectsSha256" | "origin">;

/** The environment bytes must be digest-verified by the caller before parsing. */
export function parseCityGroundCovers(raw: unknown, expected: SourceAuthority): CityGroundCover[] {
  const document = record(raw, "source document");
  if (document.schemaVersion !== "aero-bench.city-environment-source/v1"
    || document.coordinateFrame !== "x-east,y-up,z-south-meters") {
    throw new Error("City ground cover source schema is invalid");
  }
  const authority = record(document.source, "source authority");
  const origin = record(authority.origin, "source origin");
  if (authority.projection !== "WGS84->ECEF->ENU" || authority.osmSha256 !== expected.osmSha256
    || authority.objectsSha256 !== expected.objectsSha256 || !/^[a-f0-9]{64}$/.test(String(authority.osmSha256))
    || !/^[a-f0-9]{64}$/.test(String(authority.objectsSha256))
    || ["latitude_deg", "longitude_deg", "ellipsoid_height_m"].some(field =>
      !Number.isFinite(origin[field]) || origin[field] !== expected.origin[field as keyof typeof expected.origin])) {
    throw new Error("City ground cover source authority mismatch");
  }
  if (!Array.isArray(document.groundCovers)) throw new Error("City ground cover source list is missing");
  const ids = new Set<string>();
  const covers = document.groundCovers.map((value, index): CityGroundCover => {
    const item = record(value, `groundCovers[${index}]`);
    exactKeys(item, ["id", "kind", "surface", "outline", "holes", "provenance"], `groundCovers[${index}]`);
    const source = provenance(item.provenance, authority.osmSha256 as string, `groundCovers[${index}]`);
    if (typeof item.id !== "string" || !new RegExp(`^osm:${source.elementType}:${source.elementId}:\\d+$`).test(item.id)
      || ids.has(item.id) || !CITY_GROUND_COVER_KINDS.includes(item.kind as CityGroundCoverKind)) {
      throw new Error(`City ground cover groundCovers[${index}] identity is invalid`);
    }
    const inferred = groundCoverKindFromTags(source.tags);
    if (item.kind !== inferred || item.surface !== (source.tags.surface ?? null)
      || item.surface !== null && (typeof item.surface !== "string" || item.surface.trim().length === 0)) {
      throw new Error(`City ground cover groundCovers[${index}] tags disagree with its class`);
    }
    const polygon = parsePolygon(item, `groundCovers[${index}]`);
    triangulateEnvironmentPolygon(polygon, `groundCovers[${index}]`);
    ids.add(item.id);
    return { id: item.id, kind: item.kind as CityGroundCoverKind, surface: item.surface as string | null,
      ...polygon, provenance: source };
  });
  const inspection = record(document.inspection, "inspection");
  const counts = record(inspection.groundCoverCountsByKind, "inspection.groundCoverCountsByKind");
  const areas = record(inspection.groundCoverSourceAreaM2ByKind, "inspection.groundCoverSourceAreaM2ByKind");
  if (inspection.groundCoverCount !== covers.length
    || Object.keys(counts).some(kind => !CITY_GROUND_COVER_KINDS.includes(kind as CityGroundCoverKind))
    || Object.keys(areas).some(kind => !CITY_GROUND_COVER_KINDS.includes(kind as CityGroundCoverKind))) {
    throw new Error("City ground cover inspection inventory is invalid");
  }
  for (const kind of CITY_GROUND_COVER_KINDS) {
    const selected = covers.filter(cover => cover.kind === kind);
    const area = selected.reduce((sum, cover) => sum + polygonArea(cover), 0);
    const reportedCount = counts[kind] ?? 0, reportedArea = areas[kind] ?? 0;
    if (typeof reportedCount !== "number" || reportedCount !== selected.length
      || typeof reportedArea !== "number" || !Number.isFinite(reportedArea) || reportedArea < 0
      || Math.abs(reportedArea - area) > Math.max(1e-5, area * 1e-8)) {
      throw new Error(`City ground cover inspection differs for ${kind}`);
    }
  }
  return covers;
}

interface Bounds { readonly minX: number; readonly maxX: number; readonly minZ: number; readonly maxZ: number; }
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
function overlaps(a: Bounds, b: Bounds): boolean {
  return a.minX <= b.maxX && a.maxX >= b.minX && a.minZ <= b.maxZ && a.maxZ >= b.minZ;
}
class SpatialIndex<T> {
  private readonly cells = new Map<string, Set<T>>();
  add(value: T, area: Bounds): void {
    for (let x = Math.floor(area.minX / GRID_M); x <= Math.floor(area.maxX / GRID_M); x++) {
      for (let z = Math.floor(area.minZ / GRID_M); z <= Math.floor(area.maxZ / GRID_M); z++) {
        const key = `${x}:${z}`;
        if (!this.cells.has(key)) this.cells.set(key, new Set());
        this.cells.get(key)!.add(value);
      }
    }
  }
  query(area: Bounds): Set<T> {
    const result = new Set<T>();
    for (let x = Math.floor(area.minX / GRID_M); x <= Math.floor(area.maxX / GRID_M); x++) {
      for (let z = Math.floor(area.minZ / GRID_M); z <= Math.floor(area.maxZ / GRID_M); z++) {
        for (const value of this.cells.get(`${x}:${z}`) ?? []) result.add(value);
      }
    }
    return result;
  }
}

function halfPlane(polygon: readonly EnvironmentPoint[], a: EnvironmentPoint, b: EnvironmentPoint,
    keepInside: boolean): EnvironmentPoint[] {
  const side = (point: EnvironmentPoint): number => orientation(a, b, point);
  const result: EnvironmentPoint[] = [];
  for (let index = 0; index < polygon.length; index++) {
    const start = polygon[index]!, end = polygon[(index + 1) % polygon.length]!;
    const startDistance = side(start), endDistance = side(end);
    const startInside = keepInside ? startDistance >= -EPS : startDistance <= EPS;
    const endInside = keepInside ? endDistance >= -EPS : endDistance <= EPS;
    if (startInside) result.push(start);
    if (startInside !== endInside) {
      const interpolation = startDistance / (startDistance - endDistance);
      result.push([start[0] + interpolation * (end[0] - start[0]),
        start[1] + interpolation * (end[1] - start[1])]);
    }
  }
  return result;
}

/** Subtract one counter-clockwise convex obstacle from one convex polygon. */
function subtractConvex(polygon: readonly EnvironmentPoint[], obstacle: readonly EnvironmentPoint[]): EnvironmentPoint[][] {
  let remainder = [...polygon];
  const outside: EnvironmentPoint[][] = [];
  for (let index = 0; index < obstacle.length && remainder.length >= 3; index++) {
    const a = obstacle[index]!, b = obstacle[(index + 1) % obstacle.length]!;
    const piece = halfPlane(remainder, a, b, false);
    if (piece.length >= 3 && Math.abs(signedArea(piece)) > EPS) outside.push(piece);
    remainder = halfPlane(remainder, a, b, true);
  }
  return outside;
}

function emptyKindRecord(): Record<CityGroundCoverKind, number> {
  return Object.fromEntries(CITY_GROUND_COVER_KINDS.map(kind => [kind, 0])) as Record<CityGroundCoverKind, number>;
}

/** Clip source ground covers to the verified rendered road and building authority. */
export function planCityGroundCovers(covers: readonly CityGroundCover[],
    geometry: VerifiedGroundCoverGeometry): CityGroundCoverPlan {
  if (!geometry.geometryId || geometry.roadGeometry !== "published" || geometry.roadbed.length === 0
    || geometry.walkbed.length === 0 || geometry.buildings.length === 0) {
    throw new Error("City ground covers require published roadbed, walkbed, and building footprints");
  }
  const obstacleIndex = new SpatialIndex<{ readonly points: EnvironmentPoint[]; readonly bounds: Bounds }>();
  for (const [label, polygons] of [["roadbed", geometry.roadbed], ["walkbed", geometry.walkbed],
    ["building", geometry.buildings]] as const) {
    for (const [index, polygon] of polygons.entries()) {
      // These polygons came from the separately verified road/building authority. Some road
      // parts retain valid sub-centimetre seam holes which touch after display rounding; the
      // area-preserving triangulation check below remains mandatory.
      for (const points of triangulatePolygon(polygon, `${label}[${index}]`, false)) {
        const obstacle = { points, bounds: bounds(points) };
        obstacleIndex.add(obstacle, obstacle.bounds);
      }
    }
  }
  const ids = new Set<string>(), planned: CityGroundCoverPlanEntry[] = [], fullyClippedIds: string[] = [];
  const countsByKind = emptyKindRecord(), drawnAreaM2ByKind = emptyKindRecord();
  for (const cover of covers) {
    if (ids.has(cover.id)) throw new Error(`City ground cover duplicate ID: ${cover.id}`);
    ids.add(cover.id); countsByKind[cover.kind]++;
    const sourceTriangles = triangulateEnvironmentPolygon(cover, cover.id);
    const sourceAreaM2 = sourceTriangles.reduce((sum, triangle) => sum + Math.abs(signedArea(triangle)), 0);
    const retained: EnvironmentPoint[][] = [];
    for (const triangle of sourceTriangles) {
      let pieces: EnvironmentPoint[][] = [triangle];
      for (const obstacle of obstacleIndex.query(bounds(triangle))) {
        pieces = pieces.flatMap(piece => overlaps(bounds(piece), obstacle.bounds)
          ? subtractConvex(piece, obstacle.points) : [piece]);
        if (pieces.length === 0) break;
      }
      for (const piece of pieces) for (let index = 1; index < piece.length - 1; index++) {
        const trianglePiece = [piece[0]!, piece[index]!, piece[index + 1]!];
        if (Math.abs(signedArea(trianglePiece)) > EPS) {
          retained.push(signedArea(trianglePiece) > 0 ? trianglePiece : trianglePiece.reverse());
        }
      }
    }
    const measuredDrawnArea = retained.reduce((sum, triangle) => sum + Math.abs(signedArea(triangle)), 0);
    const drawnAreaM2 = measuredDrawnArea <= EPS ? 0 : measuredDrawnArea;
    if (drawnAreaM2 > sourceAreaM2 + Math.max(1e-5, sourceAreaM2 * 1e-8)) {
      throw new Error(`City ground cover clipping increased area: ${cover.id}`);
    }
    if (drawnAreaM2 === 0) fullyClippedIds.push(cover.id);
    drawnAreaM2ByKind[cover.kind] += drawnAreaM2;
    planned.push({ ...cover, triangles: retained, sourceAreaM2, drawnAreaM2,
      removedAreaM2: Math.max(0, sourceAreaM2 - drawnAreaM2) });
  }
  const sourceAreaM2 = planned.reduce((sum, cover) => sum + cover.sourceAreaM2, 0);
  const drawnAreaM2 = planned.reduce((sum, cover) => sum + cover.drawnAreaM2, 0);
  return { schemaVersion: "aero-bench.city-ground-cover-plan/v1", geometryId: geometry.geometryId,
    sourceCount: covers.length, covers: planned, fullyClippedIds,
    stats: { sourceAreaM2, drawnAreaM2, removedAreaM2: Math.max(0, sourceAreaM2 - drawnAreaM2),
      countsByKind, drawnAreaM2ByKind } };
}

export interface CityGroundCoverRenderer {
  readonly group: THREE.Group;
  readonly drawnSet: CityGroundCoverDrawnSetReport;
  /** Releases the cover geometry; the shared materials belong to the terrain surface kit. */
  dispose(): void;
}

export interface CityGroundCoverDrawnSetReport {
  readonly schema_version: "aero-bench.city-ground-cover-drawn-set/v1";
  readonly status: "pass" | "fail";
  readonly geometry_id: string;
  readonly parser_accepted_ids: readonly string[];
  readonly drawable_ids: readonly string[];
  readonly fully_clipped_ids: readonly string[];
  readonly drawn_ids: readonly string[];
  readonly omitted_ids: readonly string[];
  readonly unexpected_ids: readonly string[];
  readonly omission_count: number;
}

/** Measure source-to-mesh coverage from an instantiated viewer group. */
export function measureCityGroundCoverDrawnSet(plan: CityGroundCoverPlan,
    group: THREE.Object3D): CityGroundCoverDrawnSetReport {
  const accepted = plan.covers.map(cover => cover.id).sort();
  const drawable = plan.covers.filter(cover => cover.triangles.length > 0).map(cover => cover.id).sort();
  const expected = new Set(drawable), drawn = new Set<string>();
  group.traverse(object => {
    const id = object.userData.groundCoverId;
    if (id !== undefined) {
      if (typeof id !== "string" || drawn.has(id)) throw new Error("City ground cover mesh identity is invalid");
      drawn.add(id);
    }
  });
  const drawnIds = [...drawn].sort();
  const omittedIds = drawable.filter(id => !drawn.has(id));
  const unexpectedIds = drawnIds.filter(id => !expected.has(id));
  return { schema_version: "aero-bench.city-ground-cover-drawn-set/v1",
    status: omittedIds.length === 0 && unexpectedIds.length === 0 ? "pass" : "fail",
    geometry_id: plan.geometryId, parser_accepted_ids: accepted, drawable_ids: drawable,
    fully_clipped_ids: [...plan.fullyClippedIds].sort(), drawn_ids: drawnIds,
    omitted_ids: omittedIds, unexpected_ids: unexpectedIds, omission_count: omittedIds.length };
}

/** Create one independently measurable mesh per source polygon, each with the shared
 * material of its assigned material preset. */
export function createCityGroundCoverLayer(plan: CityGroundCoverPlan,
    assignments: ReadonlyMap<string, GroundMaterialAssignment>, kit: TerrainSurfaceKit,
    groundY = 0.012): CityGroundCoverRenderer {
  if (!Number.isFinite(groundY)) throw new Error("City ground cover height is invalid");
  const group = new THREE.Group();
  group.name = "OSM source-supported ground covers";
  const geometries: THREE.BufferGeometry[] = [];
  try {
    for (const cover of plan.covers) {
      if (cover.triangles.length === 0) continue;
      const assignment = assignments.get(cover.id);
      if (assignment === undefined) throw new Error(`City ground cover has no material assignment: ${cover.id}`);
      const geometry = terrainSurfaceGeometry([{ triangles: cover.triangles, assignment }], groundY, kit);
      geometries.push(geometry);
      const mesh = new THREE.Mesh(geometry, kit.material(assignment));
      mesh.name = `Ground cover ${cover.kind}: ${cover.id}`;
      mesh.receiveShadow = assignment.family !== "water";
      mesh.userData.groundCoverId = cover.id;
      mesh.userData.groundCoverKind = cover.kind;
      mesh.userData.surfaceTag = cover.surface;
      mesh.userData.groundMaterial = assignment;
      mesh.userData.provenance = cover.provenance;
      mesh.userData.sourceAreaM2 = cover.sourceAreaM2;
      mesh.userData.drawnAreaM2 = cover.drawnAreaM2;
      group.add(mesh);
    }
  } catch (error) {
    // Every geometry created so far is owned here: release them all and rethrow.
    geometries.forEach(geometry => geometry.dispose());
    throw error;
  }
  group.userData.groundCoverPlan = plan;
  const drawnSet = measureCityGroundCoverDrawnSet(plan, group);
  group.userData.groundCoverDrawnSet = drawnSet;
  if (drawnSet.status !== "pass") {
    geometries.forEach(geometry => geometry.dispose());
    throw new Error("City ground cover layer omitted a drawable source polygon");
  }
  return { group, drawnSet, dispose: () => geometries.forEach(geometry => geometry.dispose()) };
}
