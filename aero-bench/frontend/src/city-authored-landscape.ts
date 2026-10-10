import * as THREE from "three";
import { authoredGroundMaterialInput } from "./city-ground-material-rules";
import { assignCityGroundMaterial, terrainSurfaceGeometry, type TerrainSurfaceKit } from "./city-terrain-surfaces";
import type { EnvironmentPoint, EnvironmentPolygon } from "./city-environment";
import {
  applySurfaceWetness,
  type SurfaceWetnessUniforms,
} from "./city-surface-wetness";
import {
  applyVegetationWind,
  type VegetationWindUniforms,
} from "./city-vegetation-wind";

/**
 * Authored landscape is an explicit visual design layer. It is never OSM evidence,
 * surveyed physical truth, or a Provider sample.
 */
export const CITY_AUTHORED_LANDSCAPE_KINDS = [
  "green",
  "plaza",
  "planting_strip",
] as const;
export type CityAuthoredLandscapeKind =
  (typeof CITY_AUTHORED_LANDSCAPE_KINDS)[number];

export interface CityAuthoredLandscapePoint {
  readonly x: number;
  readonly z: number;
}

export interface CityAuthoredLandscapeItem {
  readonly id: string;
  readonly label: string;
  readonly provenance: "authored";
  readonly kind: CityAuthoredLandscapeKind;
  /** Parser output is always an open ring, even when the input ring is explicitly closed. */
  readonly polygon: readonly CityAuthoredLandscapePoint[];
}

export interface CityAuthoredLandscapePlanEntry extends CityAuthoredLandscapeItem {
  readonly triangles: readonly (readonly EnvironmentPoint[])[];
  readonly sourceAreaM2: number;
  readonly drawnAreaM2: number;
  readonly removedAreaM2: number;
}

export interface CityAuthoredLandscapePlan {
  readonly schemaVersion: "aero-bench.city-authored-landscape-plan/v1";
  readonly displayedSurfaceSha256: string;
  readonly items: readonly CityAuthoredLandscapePlanEntry[];
  readonly fullyClippedIds: readonly string[];
  readonly stats: {
    readonly sourceAreaM2: number;
    readonly drawnAreaM2: number;
    readonly removedAreaM2: number;
    readonly countsByKind: Readonly<Record<CityAuthoredLandscapeKind, number>>;
    readonly drawnAreaM2ByKind: Readonly<
      Record<CityAuthoredLandscapeKind, number>
    >;
  };
}

export interface VerifiedAuthoredLandscapeGeometry {
  readonly displayedSurfaceSha256: string;
  readonly roadGeometry: "published";
  readonly extent: EnvironmentPolygon;
  readonly roadbed: readonly EnvironmentPolygon[];
  readonly walkbed: readonly EnvironmentPolygon[];
  readonly buildings: readonly EnvironmentPolygon[];
}

const EPS = 1e-7;
const GRID_M = 64;

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`City authored landscape ${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  fields: readonly string[],
  label: string,
): void {
  const accepted = new Set(fields);
  if (
    Object.keys(value).some((field) => !accepted.has(field)) ||
    fields.some((field) => !(field in value))
  ) {
    throw new Error(`City authored landscape ${label} fields are invalid`);
  }
}

function samePoint(
  a: CityAuthoredLandscapePoint,
  b: CityAuthoredLandscapePoint,
): boolean {
  return a.x === b.x && a.z === b.z;
}

function signedArea(points: readonly EnvironmentPoint[]): number {
  let area = 0;
  for (let index = 0; index < points.length; index++) {
    const current = points[index]!,
      next = points[(index + 1) % points.length]!;
    area += current[0] * next[1] - next[0] * current[1];
  }
  return area / 2;
}

function orientation(
  a: CityAuthoredLandscapePoint,
  b: CityAuthoredLandscapePoint,
  c: CityAuthoredLandscapePoint,
): number {
  return (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x);
}

function onSegment(
  point: CityAuthoredLandscapePoint,
  a: CityAuthoredLandscapePoint,
  b: CityAuthoredLandscapePoint,
): boolean {
  return (
    Math.abs(orientation(a, b, point)) <= EPS &&
    point.x >= Math.min(a.x, b.x) - EPS &&
    point.x <= Math.max(a.x, b.x) + EPS &&
    point.z >= Math.min(a.z, b.z) - EPS &&
    point.z <= Math.max(a.z, b.z) + EPS
  );
}

function segmentsMeet(
  a: CityAuthoredLandscapePoint,
  b: CityAuthoredLandscapePoint,
  c: CityAuthoredLandscapePoint,
  d: CityAuthoredLandscapePoint,
): boolean {
  const abC = orientation(a, b, c),
    abD = orientation(a, b, d);
  const cdA = orientation(c, d, a),
    cdB = orientation(c, d, b);
  if (
    ((abC > EPS && abD < -EPS) || (abC < -EPS && abD > EPS)) &&
    ((cdA > EPS && cdB < -EPS) || (cdA < -EPS && cdB > EPS))
  )
    return true;
  return (
    (Math.abs(abC) <= EPS && onSegment(c, a, b)) ||
    (Math.abs(abD) <= EPS && onSegment(d, a, b)) ||
    (Math.abs(cdA) <= EPS && onSegment(a, c, d)) ||
    (Math.abs(cdB) <= EPS && onSegment(b, c, d))
  );
}

function parseRing(
  value: unknown,
  label: string,
): CityAuthoredLandscapePoint[] {
  if (!Array.isArray(value))
    throw new Error(`City authored landscape ${label} must be a point array`);
  const parsed = value.map((entry, index): CityAuthoredLandscapePoint => {
    const point = record(entry, `${label}[${index}]`);
    exactKeys(point, ["x", "z"], `${label}[${index}]`);
    if (
      typeof point.x !== "number" ||
      !Number.isFinite(point.x) ||
      typeof point.z !== "number" ||
      !Number.isFinite(point.z)
    ) {
      throw new Error(
        `City authored landscape ${label}[${index}] coordinates must be finite`,
      );
    }
    return {
      x: Object.is(point.x, -0) ? 0 : point.x,
      z: Object.is(point.z, -0) ? 0 : point.z,
    };
  });
  if (parsed.length > 1 && samePoint(parsed[0]!, parsed.at(-1)!)) parsed.pop();
  const unique = new Set(parsed.map((point) => `${point.x}\u0000${point.z}`));
  if (
    parsed.length < 3 ||
    unique.size !== parsed.length ||
    parsed.some((point, index) =>
      samePoint(point, parsed[(index + 1) % parsed.length]!),
    )
  ) {
    throw new Error(
      `City authored landscape ${label} needs three distinct vertices`,
    );
  }
  for (let first = 0; first < parsed.length; first++) {
    for (let second = first + 1; second < parsed.length; second++) {
      if (second === first + 1 || (first === 0 && second === parsed.length - 1))
        continue;
      if (
        segmentsMeet(
          parsed[first]!,
          parsed[(first + 1) % parsed.length]!,
          parsed[second]!,
          parsed[(second + 1) % parsed.length]!,
        )
      ) {
        throw new Error(`City authored landscape ${label} self-intersects`);
      }
    }
  }
  const tuples = parsed.map((point): EnvironmentPoint => [point.x, point.z]);
  if (Math.abs(signedArea(tuples)) <= EPS) {
    throw new Error(`City authored landscape ${label} is degenerate`);
  }
  return parsed;
}

/** Parse the exact workspace-v3 authoredLandscape array; no source fields are accepted. */
export function parseCityAuthoredLandscape(
  value: unknown,
): CityAuthoredLandscapeItem[] {
  if (!Array.isArray(value))
    throw new Error("City authored landscape must be an array");
  const ids = new Set<string>();
  return value.map((entry, index): CityAuthoredLandscapeItem => {
    const item = record(entry, `item[${index}]`);
    exactKeys(
      item,
      ["id", "label", "provenance", "kind", "polygon"],
      `item[${index}]`,
    );
    if (
      typeof item.id !== "string" ||
      item.id.trim().length === 0 ||
      ids.has(item.id)
    ) {
      throw new Error(
        `City authored landscape item[${index}] ID is invalid or duplicated`,
      );
    }
    if (typeof item.label !== "string" || item.label.trim().length === 0) {
      throw new Error(
        `City authored landscape item[${index}] label is invalid`,
      );
    }
    if (item.provenance !== "authored") {
      throw new Error(
        `City authored landscape item[${index}] provenance must be authored`,
      );
    }
    if (
      !CITY_AUTHORED_LANDSCAPE_KINDS.includes(
        item.kind as CityAuthoredLandscapeKind,
      )
    ) {
      throw new Error(`City authored landscape item[${index}] kind is invalid`);
    }
    ids.add(item.id);
    return {
      id: item.id,
      label: item.label,
      provenance: "authored",
      kind: item.kind as CityAuthoredLandscapeKind,
      polygon: parseRing(item.polygon, `item[${index}].polygon`),
    };
  });
}

function asTuple(point: CityAuthoredLandscapePoint): EnvironmentPoint {
  return [point.x, point.z];
}

function openTupleRing(
  points: readonly EnvironmentPoint[],
): EnvironmentPoint[] {
  const result = points.map(
    (point) => [point[0], point[1]] as EnvironmentPoint,
  );
  if (
    result.length > 1 &&
    result[0]![0] === result.at(-1)![0] &&
    result[0]![1] === result.at(-1)![1]
  )
    result.pop();
  return result;
}

function polygonArea(polygon: EnvironmentPolygon): number {
  const outline = openTupleRing(polygon.outline);
  const holes = polygon.holes.map(openTupleRing);
  return (
    Math.abs(signedArea(outline)) -
    holes.reduce((sum, hole) => sum + Math.abs(signedArea(hole)), 0)
  );
}

/** Verified geometry may retain display-rounding seams, so this checks area preservation
 * but does not reinterpret or repair its rings. */
function triangulatePolygon(
  polygon: EnvironmentPolygon,
  label: string,
): EnvironmentPoint[][] {
  if (
    polygon === null ||
    !Array.isArray(polygon.outline) ||
    !Array.isArray(polygon.holes)
  ) {
    throw new Error(`City authored landscape ${label} polygon is missing`);
  }
  const outline = openTupleRing(polygon.outline),
    holes = polygon.holes.map(openTupleRing);
  for (const [ringIndex, ring] of [outline, ...holes].entries()) {
    if (
      ring.length < 3 ||
      ring.some((point) => point.length !== 2 || !point.every(Number.isFinite))
    ) {
      throw new Error(
        `City authored landscape ${label} ring ${ringIndex} is invalid`,
      );
    }
  }
  const vertices = [...outline, ...holes.flat()];
  const indices = THREE.ShapeUtils.triangulateShape(
    outline.map((point) => new THREE.Vector2(...point)),
    holes.map((hole) => hole.map((point) => new THREE.Vector2(...point))),
  );
  const triangles = indices
    .map((indicesForTriangle) => {
      const triangle = indicesForTriangle.map((index) => vertices[index]!);
      return signedArea(triangle) > 0 ? triangle : triangle.reverse();
    })
    .filter((triangle) => Math.abs(signedArea(triangle)) > EPS);
  const expectedArea = polygonArea({ outline, holes });
  const actualArea = triangles.reduce(
    (sum, triangle) => sum + Math.abs(signedArea(triangle)),
    0,
  );
  if (
    expectedArea <= EPS ||
    triangles.length === 0 ||
    Math.abs(expectedArea - actualArea) > Math.max(1e-5, expectedArea * 1e-8)
  ) {
    throw new Error(
      `City authored landscape ${label} triangulation changed polygon area`,
    );
  }
  return triangles;
}

function halfPlane(
  polygon: readonly EnvironmentPoint[],
  a: EnvironmentPoint,
  b: EnvironmentPoint,
  keepInside: boolean,
): EnvironmentPoint[] {
  const side = (point: EnvironmentPoint): number =>
    (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]);
  const result: EnvironmentPoint[] = [];
  for (let index = 0; index < polygon.length; index++) {
    const start = polygon[index]!,
      end = polygon[(index + 1) % polygon.length]!;
    const startDistance = side(start),
      endDistance = side(end);
    const startInside = keepInside
      ? startDistance >= -EPS
      : startDistance <= EPS;
    const endInside = keepInside ? endDistance >= -EPS : endDistance <= EPS;
    if (startInside) result.push(start);
    if (startInside !== endInside) {
      const interpolation = startDistance / (startDistance - endDistance);
      result.push([
        start[0] + interpolation * (end[0] - start[0]),
        start[1] + interpolation * (end[1] - start[1]),
      ]);
    }
  }
  return result;
}

function intersectConvex(
  subject: readonly EnvironmentPoint[],
  clip: readonly EnvironmentPoint[],
): EnvironmentPoint[] {
  let result = [...subject];
  for (let index = 0; index < clip.length && result.length >= 3; index++) {
    result = halfPlane(
      result,
      clip[index]!,
      clip[(index + 1) % clip.length]!,
      true,
    );
  }
  return result;
}

function subtractConvex(
  subject: readonly EnvironmentPoint[],
  obstacle: readonly EnvironmentPoint[],
): EnvironmentPoint[][] {
  let remainder = [...subject];
  const outside: EnvironmentPoint[][] = [];
  for (
    let index = 0;
    index < obstacle.length && remainder.length >= 3;
    index++
  ) {
    const a = obstacle[index]!,
      b = obstacle[(index + 1) % obstacle.length]!;
    const piece = halfPlane(remainder, a, b, false);
    if (piece.length >= 3 && Math.abs(signedArea(piece)) > EPS)
      outside.push(piece);
    remainder = halfPlane(remainder, a, b, true);
  }
  return outside;
}

interface Bounds {
  readonly minX: number;
  readonly maxX: number;
  readonly minZ: number;
  readonly maxZ: number;
}
/** Single pass over the ring; Math.min/Math.max keep the spread helper's exact
 * Infinity-for-empty and NaN-poisoning semantics. */
function bounds(points: readonly EnvironmentPoint[]): Bounds {
  let minX = Infinity,
    maxX = -Infinity,
    minZ = Infinity,
    maxZ = -Infinity;
  for (let index = 0; index < points.length; index++) {
    const point = points[index]!;
    minX = Math.min(minX, point[0]);
    maxX = Math.max(maxX, point[0]);
    minZ = Math.min(minZ, point[1]);
    maxZ = Math.max(maxZ, point[1]);
  }
  return { minX, maxX, minZ, maxZ };
}
function overlaps(a: Bounds, b: Bounds): boolean {
  return (
    a.minX <= b.maxX && a.maxX >= b.minX && a.minZ <= b.maxZ && a.maxZ >= b.minZ
  );
}
class SpatialIndex<T> {
  private readonly cells = new Map<string, Set<T>>();
  add(value: T, area: Bounds): void {
    for (
      let x = Math.floor(area.minX / GRID_M);
      x <= Math.floor(area.maxX / GRID_M);
      x++
    ) {
      for (
        let z = Math.floor(area.minZ / GRID_M);
        z <= Math.floor(area.maxZ / GRID_M);
        z++
      ) {
        const key = `${x}:${z}`;
        if (!this.cells.has(key)) this.cells.set(key, new Set());
        this.cells.get(key)!.add(value);
      }
    }
  }
  query(area: Bounds): Set<T> {
    const result = new Set<T>();
    for (
      let x = Math.floor(area.minX / GRID_M);
      x <= Math.floor(area.maxX / GRID_M);
      x++
    ) {
      for (
        let z = Math.floor(area.minZ / GRID_M);
        z <= Math.floor(area.maxZ / GRID_M);
        z++
      ) {
        for (const value of this.cells.get(`${x}:${z}`) ?? [])
          result.add(value);
      }
    }
    return result;
  }
}

function triangulatePieces(
  pieces: readonly (readonly EnvironmentPoint[])[],
): EnvironmentPoint[][] {
  const triangles: EnvironmentPoint[][] = [];
  for (const piece of pieces)
    for (let index = 1; index < piece.length - 1; index++) {
      const triangle = [piece[0]!, piece[index]!, piece[index + 1]!];
      const area = signedArea(triangle);
      if (Math.abs(area) > EPS)
        triangles.push(area > 0 ? triangle : triangle.reverse());
    }
  return triangles;
}

function emptyKindRecord(): Record<CityAuthoredLandscapeKind, number> {
  return { green: 0, plaza: 0, planting_strip: 0 };
}

/** Plan visual geometry only from explicit authored polygons and verified scene geometry. */
export function planCityAuthoredLandscape(
  items: readonly CityAuthoredLandscapeItem[],
  geometry: VerifiedAuthoredLandscapeGeometry,
): CityAuthoredLandscapePlan {
  if (
    !/^[a-f0-9]{64}$/.test(geometry.displayedSurfaceSha256) ||
    geometry.roadGeometry !== "published" ||
    geometry.roadbed.length === 0 ||
    geometry.walkbed.length === 0 ||
    !Array.isArray(geometry.buildings)
  ) {
    throw new Error(
      "City authored landscape requires verified published surface geometry",
    );
  }
  const canonical = parseCityAuthoredLandscape(items);
  const extentTriangles = triangulatePolygon(geometry.extent, "scene extent");
  // The extent is fixed for the whole plan, so its triangle boxes are computed once
  // instead of once per (source triangle, extent triangle) pair.
  const extentTriangleBounds = extentTriangles.map((triangle) => bounds(triangle));
  const obstacleIndex = new SpatialIndex<{
    readonly points: EnvironmentPoint[];
    readonly bounds: Bounds;
  }>();
  for (const [label, polygons] of [
    ["roadbed", geometry.roadbed],
    ["walkbed", geometry.walkbed],
    ["building", geometry.buildings],
  ] as const) {
    for (const [index, polygon] of polygons.entries()) {
      for (const points of triangulatePolygon(polygon, `${label}[${index}]`)) {
        const obstacle = { points, bounds: bounds(points) };
        obstacleIndex.add(obstacle, obstacle.bounds);
      }
    }
  }
  const countsByKind = emptyKindRecord(),
    drawnAreaM2ByKind = emptyKindRecord();
  const fullyClippedIds: string[] = [];
  const planned = canonical.map((item): CityAuthoredLandscapePlanEntry => {
    countsByKind[item.kind]++;
    const sourcePolygon: EnvironmentPolygon = {
      outline: item.polygon.map(asTuple),
      holes: [],
    };
    const sourceTriangles = triangulatePolygon(sourcePolygon, item.id);
    const sourceAreaM2 = sourceTriangles.reduce(
      (sum, triangle) => sum + Math.abs(signedArea(triangle)),
      0,
    );
    const insideExtent: EnvironmentPoint[][] = [];
    for (const triangle of sourceTriangles) {
      // Both polygons are fixed while they are tested, so compute each box once.
      const triangleBounds = bounds(triangle);
      for (let extentIndex = 0; extentIndex < extentTriangles.length; extentIndex++) {
        if (!overlaps(triangleBounds, extentTriangleBounds[extentIndex]!)) continue;
        const intersection = intersectConvex(triangle, extentTriangles[extentIndex]!);
        if (
          intersection.length >= 3 &&
          Math.abs(signedArea(intersection)) > EPS
        )
          insideExtent.push(intersection);
      }
    }
    const retained: EnvironmentPoint[][] = [];
    for (const extentPiece of insideExtent) {
      let pieces: EnvironmentPoint[][] = [extentPiece];
      for (const obstacle of obstacleIndex.query(bounds(extentPiece))) {
        pieces = pieces.flatMap((piece) =>
          overlaps(bounds(piece), obstacle.bounds)
            ? subtractConvex(piece, obstacle.points)
            : [piece],
        );
        if (pieces.length === 0) break;
      }
      retained.push(...triangulatePieces(pieces));
    }
    const measuredArea = retained.reduce(
      (sum, triangle) => sum + Math.abs(signedArea(triangle)),
      0,
    );
    const drawnAreaM2 = measuredArea <= EPS ? 0 : measuredArea;
    if (drawnAreaM2 > sourceAreaM2 + Math.max(1e-5, sourceAreaM2 * 1e-8)) {
      throw new Error(
        `City authored landscape clipping increased area: ${item.id}`,
      );
    }
    if (drawnAreaM2 === 0) fullyClippedIds.push(item.id);
    drawnAreaM2ByKind[item.kind] += drawnAreaM2;
    return {
      ...item,
      triangles: retained,
      sourceAreaM2,
      drawnAreaM2,
      removedAreaM2: Math.max(0, sourceAreaM2 - drawnAreaM2),
    };
  });
  const sourceAreaM2 = planned.reduce(
    (sum, item) => sum + item.sourceAreaM2,
    0,
  );
  const drawnAreaM2 = planned.reduce((sum, item) => sum + item.drawnAreaM2, 0);
  return {
    schemaVersion: "aero-bench.city-authored-landscape-plan/v1",
    displayedSurfaceSha256: geometry.displayedSurfaceSha256,
    items: planned,
    fullyClippedIds,
    stats: {
      sourceAreaM2,
      drawnAreaM2,
      removedAreaM2: Math.max(0, sourceAreaM2 - drawnAreaM2),
      countsByKind,
      drawnAreaM2ByKind,
    },
  };
}

/** Explicit design tags select materials through the same versioned rules as source ground. */
export function authoredLandscapeMaterial(item: Pick<CityAuthoredLandscapeItem, "id" | "kind">) {
  const tags: Readonly<Record<string, string>> = item.kind === "green" ? { landuse: "grass" }
    : item.kind === "planting_strip" ? { surface: "woodchips" } : { surface: "paving_stones" };
  return assignCityGroundMaterial(authoredGroundMaterialInput({ id: item.id,
    provenance: { kind: "authored", designId: item.id } }, tags));
}

interface AuthoredLandscapeStyle {
  readonly color: number;
  readonly roughness: number;
  readonly metalness: number;
}
const STYLES: Readonly<
  Record<CityAuthoredLandscapeKind, AuthoredLandscapeStyle>
> = {
  green: { color: 0x66874c, roughness: 0.98, metalness: 0 },
  plaza: { color: 0xa59f94, roughness: 0.86, metalness: 0.01 },
  planting_strip: { color: 0x5f7848, roughness: 1, metalness: 0 },
};

export interface CityAuthoredLandscapeVisualState {
  /** Shared presentation uniform controlled by the viewer's visual weather state. */
  readonly wetness: SurfaceWetnessUniforms;
  /** Shared presentation uniform controlled by the viewer's visual wind state. */
  readonly wind: VegetationWindUniforms;
  /** Borrowed kit: its materials, textures and wetness remain owned by vegetation. */
  readonly surfaces?: TerrainSurfaceKit;
  readonly labelTextureFactory?: (label: string) => THREE.Texture;
}

export interface CityAuthoredLandscapeRenderer {
  readonly group: THREE.Group;
  readonly materials: readonly THREE.Material[];
  dispose(): void;
}

function defaultLabelTexture(label: string): THREE.Texture {
  if (typeof document === "undefined") {
    throw new Error(
      "City authored landscape labels require a browser or an injected label texture factory",
    );
  }
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 96;
  const context = canvas.getContext("2d");
  if (context === null)
    throw new Error("City authored landscape label canvas is unavailable");
  context.fillStyle = "rgba(8, 28, 35, 0.86)";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "rgba(155, 214, 216, 0.92)";
  context.lineWidth = 4;
  context.strokeRect(2, 2, canvas.width - 4, canvas.height - 4);
  context.fillStyle = "#e8f5f4";
  context.font = "600 32px system-ui, sans-serif";
  context.textAlign = "center";
  context.textBaseline = "middle";
  const display = label.length > 22 ? `${label.slice(0, 21)}…` : label;
  context.fillText(`创作 · ${display}`, canvas.width / 2, canvas.height / 2);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function triangleCentroid(
  triangle: readonly EnvironmentPoint[],
): EnvironmentPoint {
  return [
    (triangle[0]![0] + triangle[1]![0] + triangle[2]![0]) / 3,
    (triangle[0]![1] + triangle[1]![1] + triangle[2]![1]) / 3,
  ];
}

function planCentroid(item: CityAuthoredLandscapePlanEntry): EnvironmentPoint {
  let selected: readonly EnvironmentPoint[] | null = null,
    selectedArea = 0;
  for (const triangle of item.triangles) {
    const area = Math.abs(signedArea(triangle));
    if (area > selectedArea) {
      selected = triangle;
      selectedArea = area;
    }
  }
  if (selected === null || selectedArea <= EPS) {
    throw new Error(
      `City authored landscape ${item.id} has no drawable centroid`,
    );
  }
  // A whole-polygon centroid can fall into a clipped road or concavity. The
  // largest retained triangle's centroid is guaranteed to remain drawable.
  return triangleCentroid(selected);
}

const PLANT_DETAIL_CLEARANCE_M = 0.5;
function segmentDistance(
  point: EnvironmentPoint,
  a: EnvironmentPoint,
  b: EnvironmentPoint,
): number {
  const dx = b[0] - a[0],
    dz = b[1] - a[1];
  const lengthSq = dx * dx + dz * dz;
  const along =
    lengthSq === 0
      ? 0
      : Math.max(
          0,
          Math.min(
            1,
            ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / lengthSq,
          ),
        );
  return Math.hypot(point[0] - a[0] - along * dx, point[1] - a[1] - along * dz);
}

function vegetationPoints(
  item: CityAuthoredLandscapePlanEntry,
): EnvironmentPoint[] {
  const result: EnvironmentPoint[] = [];
  for (const [triangleIndex, triangle] of item.triangles.entries()) {
    const area = Math.abs(signedArea(triangle));
    const count = Math.min(64, Math.max(1, Math.ceil(area / 24)));
    for (let index = 0; index < count; index++) {
      let a = ((index + 1) * 0.61803398875 + triangleIndex * 0.137) % 1;
      let b = ((index + 1) * 0.41421356237 + triangleIndex * 0.271) % 1;
      if (a + b > 1) {
        a = 1 - a;
        b = 1 - b;
      }
      const c = 1 - a - b;
      const point: EnvironmentPoint = [
        triangle[0]![0] * a + triangle[1]![0] * b + triangle[2]![0] * c,
        triangle[0]![1] * a + triangle[1]![1] * b + triangle[2]![1] * c,
      ];
      // The blade half-width plus the authored wind envelope stays inside the
      // clipped triangle, so decorative vegetation cannot bend into the motor band.
      if (
        triangle.every(
          (start, edge) =>
            segmentDistance(
              point,
              start,
              triangle[(edge + 1) % triangle.length]!,
            ) >= PLANT_DETAIL_CLEARANCE_M,
        )
      )
        result.push(point);
      if (result.length >= 512) return result;
    }
  }
  return result;
}

/** Create a visual-only layer. Weather and wind uniforms remain owned by the viewer. */
export function createCityAuthoredLandscapeLayer(
  plan: CityAuthoredLandscapePlan,
  visual: CityAuthoredLandscapeVisualState,
  groundY = 0.02,
): CityAuthoredLandscapeRenderer {
  if (!Number.isFinite(groundY))
    throw new Error("City authored landscape ground height is invalid");
  const group = new THREE.Group();
  group.name = "Authored landscape (visual design, not source truth)";
  const geometries: THREE.BufferGeometry[] = [],
    materials: THREE.Material[] = [],
    textures: THREE.Texture[] = [];
  const dispose = (): void => {
    geometries.forEach((geometry) => geometry.dispose());
    materials.forEach((material) => material.dispose());
    textures.forEach((texture) => texture.dispose());
    group.clear();
  };
  try {
    for (const item of plan.items) {
      if (item.triangles.length === 0) continue;
      const assignment = authoredLandscapeMaterial(item);
      let geometry: THREE.BufferGeometry;
      let material: THREE.MeshStandardMaterial | THREE.MeshPhysicalMaterial;
      if (visual.surfaces !== undefined) {
        geometry = terrainSurfaceGeometry([{ triangles: item.triangles, assignment }], groundY, visual.surfaces);
        material = visual.surfaces.material(assignment);
      } else {
        const vertices: number[] = [];
        for (const triangle of item.triangles)
          for (const point of [...triangle].reverse()) vertices.push(point[0], groundY, point[1]);
        geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
        geometry.computeVertexNormals();
        const style = STYLES[item.kind];
        material = new THREE.MeshStandardMaterial({ ...style, side: THREE.FrontSide });
        applySurfaceWetness(material, visual.wetness, item.kind === "plaza"
          ? { maxDarkening: 0.42, minRoughness: 0.2 } : { maxDarkening: 0.26, minRoughness: 0.58 });
        materials.push(material);
      }
      geometries.push(geometry);
      const surface = new THREE.Mesh(geometry, material);
      surface.name = `Authored ${item.kind}: ${item.label}`;
      surface.receiveShadow = true;
      if (visual.surfaces !== undefined) surface.userData.groundMaterial = assignment;
      surface.userData.authoredLandscapeId = item.id;
      surface.userData.authoredLandscapeLabel = item.label;
      surface.userData.authoredLandscapeKind = item.kind;
      surface.userData.provenance = "authored";
      surface.userData.visualOnly = true;
      group.add(surface);

      if (item.kind !== "plaza") {
        const points = vegetationPoints(item);
        const bladeGeometry = new THREE.PlaneGeometry(
          0.34,
          item.kind === "planting_strip" ? 0.62 : 0.42,
        );
        bladeGeometry.translate(
          0,
          item.kind === "planting_strip" ? 0.31 : 0.21,
          0,
        );
        geometries.push(bladeGeometry);
        const bladeMaterial = new THREE.MeshStandardMaterial({
          color: item.kind === "planting_strip" ? 0x789c55 : 0x82a962,
          roughness: 1,
          metalness: 0,
          side: THREE.DoubleSide,
        });
        applyVegetationWind(bladeMaterial, visual.wind, "grass");
        materials.push(bladeMaterial);
        const blades = new THREE.InstancedMesh(
          bladeGeometry,
          bladeMaterial,
          points.length,
        );
        const matrix = new THREE.Matrix4();
        for (const [index, point] of points.entries()) {
          matrix.makeRotationY(
            (index * 2.399963229728653 + item.id.length) % (Math.PI * 2),
          );
          matrix.setPosition(point[0], groundY, point[1]);
          blades.setMatrixAt(index, matrix);
        }
        blades.instanceMatrix.needsUpdate = true;
        blades.name = `Authored planting detail: ${item.label}`;
        blades.userData.authoredLandscapeId = item.id;
        blades.userData.provenance = "authored";
        blades.userData.visualOnly = true;
        group.add(blades);
      }

      const texture = (visual.labelTextureFactory ?? defaultLabelTexture)(
        item.label,
      );
      textures.push(texture);
      const labelMaterial = new THREE.SpriteMaterial({
        map: texture,
        transparent: true,
        depthWrite: false,
      });
      materials.push(labelMaterial);
      const label = new THREE.Sprite(labelMaterial);
      const center = planCentroid(item);
      label.position.set(center[0], groundY + 0.7, center[1]);
      label.scale.set(
        Math.max(4.5, Math.min(10, item.label.length * 0.58 + 3.5)),
        1.55,
        1,
      );
      label.center.set(0.5, 0);
      label.name = `Authored landscape label: ${item.label}`;
      label.userData.authoredLandscapeId = item.id;
      label.userData.provenance = "authored";
      label.userData.visualOnly = true;
      group.add(label);
    }
    group.userData.authoredLandscapePlan = plan;
    group.userData.provenance = "authored";
    group.userData.visualOnly = true;
    return { group, materials, dispose };
  } catch (error) {
    dispose();
    throw error;
  }
}
