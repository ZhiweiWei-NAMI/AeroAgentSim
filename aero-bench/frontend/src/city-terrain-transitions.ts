/** Terrain transition bands and tree bases, planned from already-loaded ground geometry.
 *
 * Presentation assumptions, not measurements: the class widths, strengths and tints in
 * `TERRAIN_TRANSITIONS_V1` are authored visual parameters chosen for this feature; they
 * encode no surveyed soiling, wetness or occlusion data. See ticket T6.
 *
 * One data-driven transition layer: narrow multiplicative darkening bands on the soft side
 * of each classified edge, and darkened bases under trees standing on grass. Every band is
 * clipped to the soft patch it belongs to, so nothing draws on roads, sidewalks, buildings
 * or water. The classification of each edge comes only from the geometry the caller passes
 * in (buildings, roadbed, walkbed, ground covers, greens). No new texture, no extra render
 * pass: one blended mesh per surface height.
 *
 * Multiplier blending (three.js 0.185.1 `WebGLState`, `MultiplyBlending` with
 * `premultipliedAlpha: true`): `gl.blendFuncSeparate(DST_COLOR, ONE_MINUS_SRC_ALPHA, ZERO,
 * ONE)`. Because the shader writes alpha 1, the result is `multiplier x destination.rgb`
 * with the destination alpha kept unchanged.
 *
 * Boundary topology uses 1 mm endpoint keys and a 4x-width miter limit. Same-class
 * pieces share one strip; convex corners are cut at the vertex bisector. Expanding
 * corners use radial fans so alpha is the distance to the actual boundary vertex,
 * rather than a triangulation-dependent interpolation. The radial attribute extends
 * the original V1 arrays without adding a texture, material, mesh or render pass.
 */

import * as THREE from "three";
import type { EnvironmentPoint, EnvironmentPolygon, EnvironmentTreePlacement } from "./city-environment";
import { insidePolygon } from "./city-environment";
import type { GroundMaterialFamily } from "./city-ground-material-rules";

export interface TerrainTransitionSurface {
  readonly id: string;
  readonly family: GroundMaterialFamily;
  readonly surfaceY: number;
  readonly triangles: readonly (readonly EnvironmentPoint[])[];
}

export interface TerrainTransitionInput {
  /** Every drawn green, woodland floor and ground cover. */
  readonly surfaces: readonly TerrainTransitionSurface[];
  readonly buildings: readonly EnvironmentPolygon[];
  readonly roadbed: readonly EnvironmentPolygon[];
  readonly walkbed: readonly EnvironmentPolygon[];
  readonly trees: readonly EnvironmentTreePlacement[];
}

export interface TerrainTransitionLayer {
  readonly surfaceY: number;
  /** x, surfaceY + 0.0015, z per vertex. */
  readonly positions: Float32Array;
  /** Linear RGB tint per vertex. */
  readonly colors: Float32Array;
  readonly alphas: Float32Array;
  /** Local radial distance field for corner fans: dx, dz, inverse width, strength.
   * Zero inverse width selects the linearly interpolated alpha instead. */
  readonly radial: Float32Array;
}

export interface TerrainTransitionStats {
  readonly pieces: number;
  readonly skippedInterior: number;
  readonly skippedSoft: number;
  readonly byClass: Readonly<Record<TerrainTransitionClass, number>>;
  readonly treeBases: number;
  readonly bandAreaM2: number;
}

export interface TerrainTransitionPlan {
  readonly version: string;
  readonly layers: readonly TerrainTransitionLayer[];
  readonly stats: TerrainTransitionStats;
}

export type TerrainTransitionClass = "building" | "water" | "paved" | "open";

export const TERRAIN_TRANSITIONS_V1: Readonly<{
  version: string;
  softFamilies: readonly GroundMaterialFamily[];
  segmentMaxM: number;
  probeM: number;
  classes: Readonly<Record<TerrainTransitionClass, {
    readonly widthM: number; readonly strength: number; readonly tint: readonly [number, number, number];
  }>>;
  treeBase: Readonly<{ strength: number; tint: readonly [number, number, number]; sides: number }>;
}> = Object.freeze({
  version: "city-terrain-transitions-v1",
  softFamilies: Object.freeze(["grass", "woodland_floor", "soil", "mulch", "sand", "gravel"] as const),
  segmentMaxM: 2.0,
  probeM: 0.25,
  classes: Object.freeze({
    building: Object.freeze({ widthM: 0.9, strength: 0.30, tint: Object.freeze([0x4a / 255, 0x40 / 255, 0x36 / 255] as const) }),
    water: Object.freeze({ widthM: 1.2, strength: 0.40, tint: Object.freeze([0x3a / 255, 0x3a / 255, 0x32 / 255] as const) }),
    paved: Object.freeze({ widthM: 0.35, strength: 0.16, tint: Object.freeze([0x5c / 255, 0x4c / 255, 0x3a / 255] as const) }),
    open: Object.freeze({ widthM: 0.5, strength: 0.10, tint: Object.freeze([0x5c / 255, 0x54 / 255, 0x46 / 255] as const) }),
  }),
  treeBase: Object.freeze({ strength: 0.32, tint: Object.freeze([0x5a / 255, 0x48 / 255, 0x34 / 255] as const), sides: 16 }),
});

const EPS = 1e-9;
const GRID_M = 32;
const LIFT_M = 0.0015;

interface Bounds { minX: number; maxX: number; minZ: number; maxZ: number }

function boundsOf(points: readonly EnvironmentPoint[]): Bounds {
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (const point of points) {
    minX = Math.min(minX, point[0]); maxX = Math.max(maxX, point[0]);
    minZ = Math.min(minZ, point[1]); maxZ = Math.max(maxZ, point[1]);
  }
  return { minX, maxX, minZ, maxZ };
}

function overlapsBounds(a: Bounds, b: Bounds): boolean {
  return a.minX <= b.maxX && a.maxX >= b.minX && a.minZ <= b.maxZ && a.maxZ >= b.minZ;
}

/** Uniform grid index over triangles and polygons, cell 32 m, for every point and
 * overlap query: `at` answers point membership, `query` answers box overlap; both walk
 * only the touched cells and return hits in registration order, independent of cell order. */
export class TerrainTransitionGrid<T> {
  private readonly cells = new Map<string, Set<T>>();
  private readonly boxes = new Map<T, Bounds>();
  private readonly order = new Map<T, number>();

  add(value: T, points: readonly EnvironmentPoint[]): void {
    const box = boundsOf(points);
    if (!this.order.has(value)) this.order.set(value, this.order.size);
    this.boxes.set(value, box);
    for (let x = Math.floor(box.minX / GRID_M); x <= Math.floor(box.maxX / GRID_M); x++) {
      for (let z = Math.floor(box.minZ / GRID_M); z <= Math.floor(box.maxZ / GRID_M); z++) {
        const key = `${x}:${z}`;
        const cell = this.cells.get(key);
        if (cell === undefined) this.cells.set(key, new Set([value]));
        else cell.add(value);
      }
    }
  }

  /** Registered values whose stored bounding box contains the point, in insertion order. */
  at(point: EnvironmentPoint): T[] {
    const hits: T[] = [];
    for (const value of this.cells.get(`${Math.floor(point[0] / GRID_M)}:${Math.floor(point[1] / GRID_M)}`) ?? []) {
      const box = this.boxes.get(value)!;
      if (point[0] >= box.minX && point[0] <= box.maxX && point[1] >= box.minZ && point[1] <= box.maxZ) {
        hits.push(value);
      }
    }
    return hits;
  }

  /** Registered values whose stored bounding box overlaps `box`, in insertion order. */
  query(box: Bounds): T[] {
    const hits: T[] = [];
    const seen = new Set<T>();
    for (let x = Math.floor(box.minX / GRID_M); x <= Math.floor(box.maxX / GRID_M); x++) {
      for (let z = Math.floor(box.minZ / GRID_M); z <= Math.floor(box.maxZ / GRID_M); z++) {
        for (const value of this.cells.get(`${x}:${z}`) ?? []) {
          if (seen.has(value)) continue;
          seen.add(value);
          if (overlapsBounds(this.boxes.get(value)!, box)) hits.push(value);
        }
      }
    }
    return hits.sort((a, b) => this.order.get(a)! - this.order.get(b)!);
  }
}

function checkFinite(rings: readonly (readonly EnvironmentPoint[])[]): void {
  for (const ring of rings) {
    for (const point of ring) {
      if (!Number.isFinite(point[0]) || !Number.isFinite(point[1])) {
        throw new Error(`Terrain transitions coordinate is not finite: [${String(point[0])}, ${String(point[1])}]`);
      }
    }
  }
}

function srgbToLinear(channel: number): number {
  return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
}

function tintOf(tint: readonly [number, number, number]): [number, number, number] {
  return [srgbToLinear(tint[0]), srgbToLinear(tint[1]), srgbToLinear(tint[2])];
}

function orientation(a: EnvironmentPoint, b: EnvironmentPoint, p: EnvironmentPoint): number {
  return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
}

/** Inclusive point-in-triangle test: boundary points count as inside. */
function insideTriangle(point: EnvironmentPoint, triangle: readonly EnvironmentPoint[]): boolean {
  const d1 = orientation(triangle[0]!, triangle[1]!, point);
  const d2 = orientation(triangle[1]!, triangle[2]!, point);
  const d3 = orientation(triangle[2]!, triangle[0]!, point);
  return (d1 >= -EPS && d2 >= -EPS && d3 >= -EPS) || (d1 <= EPS && d2 <= EPS && d3 <= EPS);
}

function orientedArea(points: readonly EnvironmentPoint[]): number {
  let area = 0;
  for (let index = 0; index < points.length; index++) {
    const a = points[index]!, b = points[(index + 1) % points.length]!;
    area += a[0] * b[1] - b[0] * a[1];
  }
  return area / 2;
}

/** Perpendicular distance to the infinite line through `a` and `b`. */
function distanceToLine(point: EnvironmentPoint, a: EnvironmentPoint, b: EnvironmentPoint): number {
  const dx = b[0] - a[0], dz = b[1] - a[1];
  const length = Math.hypot(dx, dz);
  if (length <= EPS) return Math.hypot(point[0] - a[0], point[1] - a[1]);
  return Math.abs((point[0] - a[0]) * dz / length - (point[1] - a[1]) * dx / length);
}

/** Convex-convex clip of `subject` against one triangle, by successive half-planes. Works
 * for either clip-triangle winding: the kept half-plane is chosen from the third vertex. */
function clipConvex(subject: readonly EnvironmentPoint[],
    clipTriangle: readonly EnvironmentPoint[]): EnvironmentPoint[] {
  let output = [...subject];
  for (let index = 0; index < clipTriangle.length && output.length > 0; index++) {
    const a = clipTriangle[index]!, b = clipTriangle[(index + 1) % clipTriangle.length]!;
    const insideSign = Math.sign(orientation(a, b, clipTriangle[(index + 2) % clipTriangle.length]!));
    if (insideSign === 0) continue;
    const side = (point: EnvironmentPoint): number => orientation(a, b, point) * insideSign;
    const next: EnvironmentPoint[] = [];
    for (let vertex = 0; vertex < output.length; vertex++) {
      const start = output[vertex]!, end = output[(vertex + 1) % output.length]!;
      const startSide = side(start), endSide = side(end);
      if (startSide >= 0) next.push(start);
      if ((startSide > 0 && endSide < 0) || (startSide < 0 && endSide > 0)) {
        const t = startSide / (startSide - endSide);
        next.push([start[0] + t * (end[0] - start[0]), start[1] + t * (end[1] - start[1])]);
      }
    }
    output = next;
  }
  return output;
}

/** Clip a convex strip to the side of a vertex bisector containing its edge midpoint. */
function clipBisector(subject: EnvironmentPoint[], vertex: EnvironmentPoint,
    direction: EnvironmentPoint, midpoint: EnvironmentPoint): EnvironmentPoint[] {
  const b = shifted(vertex, direction, 1);
  const sign = Math.sign(orientation(vertex, b, midpoint));
  if (sign === 0) return subject;
  const output: EnvironmentPoint[] = [];
  for (let i = 0; i < subject.length; i++) {
    const a = subject[i]!, c = subject[(i + 1) % subject.length]!;
    const da = sign * orientation(vertex, b, a), dc = sign * orientation(vertex, b, c);
    if (da >= 0) output.push(a);
    if ((da > 0 && dc < 0) || (da < 0 && dc > 0)) {
      const t = da / (da - dc);
      output.push([a[0] + (c[0] - a[0]) * t, a[1] + (c[1] - a[1]) * t]);
    }
  }
  return output;
}

interface BandPiece {
  readonly polygons: readonly (readonly EnvironmentPoint[])[];
  readonly alphaAt: (point: EnvironmentPoint) => number;
  readonly radialCenter?: EnvironmentPoint;
  readonly widthM: number;
  readonly strength: number;
  readonly tint: readonly [number, number, number];
  readonly surfaceY: number;
}

/** Boundary topology is snapped to a 1 mm lattice. Only topology uses snapped
 * positions; emitted bands are clipped to the original, unsnapped triangles.
 * Collapsed/sliver-only rings below 1 mm thickness are discarded. Long edges
 * are split at registered vertices before cancellation, including T-junctions. */
const SNAP_M = 0.001;
const MITER_LIMIT = 4;
interface BoundaryEdge { a: EnvironmentPoint; b: EnvironmentPoint }
export function recoverTerrainTransitionBoundary(triangles: TerrainTransitionSurface["triangles"]): {
  rings: EnvironmentPoint[][]; skippedInterior: number;
} {
  const nodes = new Map<string, EnvironmentPoint>();
  const key = (p: EnvironmentPoint) => `${Math.round(p[0] / SNAP_M)}:${Math.round(p[1] / SNAP_M)}`;
  const node = (p: EnvironmentPoint): EnvironmentPoint => {
    const k = key(p);
    if (!nodes.has(k)) nodes.set(k, p);
    return nodes.get(k)!;
  };
  const edges: BoundaryEdge[] = [];
  for (const triangle of triangles) {
    if (triangle.length !== 3) throw new Error("Terrain transitions requires triangles");
    const points = triangle.map(node);
    if (new Set(points).size < 3 || Math.abs(orientation(points[0]!, points[1]!, points[2]!)) <= EPS) continue;
    if (orientation(points[0]!, points[1]!, points[2]!) < 0) points.reverse();
    for (let i = 0; i < 3; i++) edges.push({ a: points[i]!, b: points[(i + 1) % 3]! });
  }
  const vertexGrid = new TerrainTransitionGrid<EnvironmentPoint>();
  for (const p of nodes.values()) vertexGrid.add(p, [p]);
  const contributions = new Map<string, { edge: BoundaryEdge; balance: number; counts: number }>();
  for (const { a, b } of edges) {
    const dx = b[0] - a[0], dz = b[1] - a[1], length = Math.hypot(dx, dz);
    const box = boundsOf([a, b]);
    const candidates = vertexGrid.query({ minX: box.minX - SNAP_M, maxX: box.maxX + SNAP_M,
      minZ: box.minZ - SNAP_M, maxZ: box.maxZ + SNAP_M });
    const split = candidates.map(p => ({ p, t: ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / (length * length) }))
      .filter(({ p, t }) => p !== a && p !== b && t > EPS && t < 1 - EPS && distanceToLine(p, a, b) <= SNAP_M / 2)
      .concat([{ p: a, t: 0 }, { p: b, t: 1 }])
      .sort((a, b) => a.t - b.t);
    for (let i = 1; i < split.length; i++) {
      const p = split[i - 1]!.p, q = split[i]!.p;
      if (key(p) === key(q)) continue;
      const forward = key(p) < key(q), k = forward ? `${key(p)}|${key(q)}` : `${key(q)}|${key(p)}`;
      const entry = contributions.get(k);
      if (entry) { entry.balance += forward ? 1 : -1; entry.counts++; }
      else contributions.set(k, { edge: { a: forward ? p : q, b: forward ? q : p }, balance: forward ? 1 : -1, counts: 1 });
    }
  }
  const boundary: BoundaryEdge[] = [];
  let skippedInterior = 0;
  for (const { edge, balance, counts } of contributions.values()) {
    const length = Math.hypot(edge.b[0] - edge.a[0], edge.b[1] - edge.a[1]);
    if (balance === 0) skippedInterior += counts * Math.ceil(length / TERRAIN_TRANSITIONS_V1.segmentMaxM);
    else boundary.push(balance > 0 ? edge : { a: edge.b, b: edge.a });
  }
  const outgoing = new Map<EnvironmentPoint, BoundaryEdge[]>();
  for (const e of boundary) {
    if (!outgoing.has(e.a)) outgoing.set(e.a, []);
    outgoing.get(e.a)!.push(e);
  }
  const unused = new Set(boundary), rings: EnvironmentPoint[][] = [];
  for (const first of boundary) {
    if (!unused.has(first)) continue;
    const ring: EnvironmentPoint[] = [];
    let e = first;
    while (true) {
      unused.delete(e); ring.push(e.a);
      if (e.b === first.a) break;
      const next = (outgoing.get(e.b) ?? []).filter(candidate => unused.has(candidate));
      if (next.length === 0) throw new Error(`Terrain transitions boundary is not closed at ${key(e.b)}`);
      // At a point contact, follow the face with material on the left.
      const back = Math.atan2(e.a[1] - e.b[1], e.a[0] - e.b[0]);
      next.sort((a, b) => {
        const turn = (n: BoundaryEdge) => (back - Math.atan2(n.b[1] - n.a[1], n.b[0] - n.a[0]) + 2 * Math.PI) % (2 * Math.PI);
        return turn(a) - turn(b);
      });
      e = next[0]!;
    }
    const perimeter = ring.reduce((s, p, i) => s + Math.hypot(p[0] - ring[(i + 1) % ring.length]![0], p[1] - ring[(i + 1) % ring.length]![1]), 0);
    if (ring.length >= 3 && Math.abs(orientedArea(ring)) > SNAP_M * perimeter / 2) rings.push(ring);
  }
  return { rings, skippedInterior };
}

interface ClassifiedEdge extends BoundaryEdge {
  class_: TerrainTransitionClass | null;
  inward: EnvironmentPoint;
}
const shifted = (p: EnvironmentPoint, v: EnvironmentPoint, w: number): EnvironmentPoint => [p[0] + v[0] * w, p[1] + v[1] * w];
function bisector(a: EnvironmentPoint, b: EnvironmentPoint): EnvironmentPoint {
  const denominator = 1 + a[0] * b[0] + a[1] * b[1];
  if (denominator < EPS) return a;
  return [(a[0] + b[0]) / denominator, (a[1] + b[1]) / denominator];
}

interface FanPiece {
  readonly polygon: readonly EnvironmentPoint[];
  readonly centerX: number;
  readonly centerZ: number;
  readonly radiusM: number;
  readonly surfaceY: number;
}

interface Vertex {
  readonly x: number;
  readonly z: number;
  readonly alpha: number;
  readonly tint: readonly [number, number, number];
  readonly radial: readonly number[];
}
/** Plans the darkening bands and tree bases for one set of drawn soft surfaces. */
export function planTerrainTransitions(input: TerrainTransitionInput): TerrainTransitionPlan {
  if (input === null || typeof input !== "object" || !Array.isArray(input.surfaces)
    || !Array.isArray(input.buildings) || !Array.isArray(input.roadbed)
    || !Array.isArray(input.walkbed) || !Array.isArray(input.trees)) {
    throw new Error("Terrain transitions input lists are invalid");
  }
  const ids = new Set<string>();
  for (const surface of input.surfaces) {
    if (surface === null || typeof surface !== "object" || typeof surface.id !== "string"
      || surface.id.length === 0) {
      throw new Error("Terrain transitions surface id must be a non-empty string");
    }
    if (!Number.isFinite(surface.surfaceY)) {
      throw new Error(`Terrain transitions surface ${surface.id} surfaceY must be finite`);
    }
    if (!Array.isArray(surface.triangles)) {
      throw new Error(`Terrain transitions surface ${surface.id} triangles must be an array`);
    }
    checkFinite(surface.triangles);
    if (ids.has(surface.id)) throw new Error(`Terrain transitions duplicate surface ID: ${surface.id}`);
    ids.add(surface.id);
  }
  for (const tree of input.trees) {
    if (tree === null || typeof tree !== "object" || !Number.isFinite(tree.x)
      || !Number.isFinite(tree.z) || !Number.isFinite(tree.heightM)) {
      throw new Error(`Terrain transitions tree ${String((tree as EnvironmentTreePlacement | null)?.id ?? "(unnamed)")} has a non-finite placement`);
    }
  }
  for (const polygon of [...input.buildings, ...input.roadbed, ...input.walkbed]) {
    checkFinite([polygon.outline, ...polygon.holes]);
  }

  const softFamilies = new Set<string>(TERRAIN_TRANSITIONS_V1.softFamilies);
  const softSurfaces = input.surfaces.filter(surface => softFamilies.has(surface.family));

  const buildingGrid = new TerrainTransitionGrid<EnvironmentPolygon>();
  for (const building of input.buildings) buildingGrid.add(building, building.outline);
  const roadGrid = new TerrainTransitionGrid<EnvironmentPolygon>();
  for (const polygon of [...input.roadbed, ...input.walkbed]) roadGrid.add(polygon, polygon.outline);
  const waterGrid = new TerrainTransitionGrid<readonly EnvironmentPoint[]>();
  const pavedGrid = new TerrainTransitionGrid<readonly EnvironmentPoint[]>();
  const softGrid = new TerrainTransitionGrid<{ readonly triangle: readonly EnvironmentPoint[];
    readonly surface: TerrainTransitionSurface }>();
  for (const surface of input.surfaces) {
    if (surface.family === "water") {
      for (const triangle of surface.triangles) waterGrid.add(triangle, triangle);
    } else if (!softFamilies.has(surface.family)) {
      for (const triangle of surface.triangles) pavedGrid.add(triangle, triangle);
    } else {
      for (const triangle of surface.triangles) softGrid.add({ triangle, surface }, triangle);
    }
  }

  const insideBuilding = (point: EnvironmentPoint): boolean =>
    buildingGrid.at(point).some(polygon => insidePolygon(point, polygon));
  const insideWater = (point: EnvironmentPoint): boolean =>
    waterGrid.at(point).some(triangle => insideTriangle(point, triangle));
  const insidePaved = (point: EnvironmentPoint): boolean =>
    pavedGrid.at(point).some(triangle => insideTriangle(point, triangle))
    || roadGrid.at(point).some(polygon => insidePolygon(point, polygon));

  const classes = TERRAIN_TRANSITIONS_V1.classes;
  let pieces = 0, skippedInterior = 0, skippedSoft = 0;
  const byClass: Record<TerrainTransitionClass, number> = { building: 0, water: 0, paved: 0, open: 0 };
  let bandAreaM2 = 0;
  const bandPieces: BandPiece[] = [];
  const surfaceGrids = new Map<TerrainTransitionSurface, TerrainTransitionGrid<readonly EnvironmentPoint[]>>();
  for (const surface of softSurfaces) {
    const surfaceGrid = new TerrainTransitionGrid<readonly EnvironmentPoint[]>();
    surfaceGrids.set(surface, surfaceGrid);
    for (const triangle of surface.triangles) {
      if (Math.abs(orientation(triangle[0]!, triangle[1]!, triangle[2]!)) > EPS) surfaceGrid.add(triangle, triangle);
    }
    const boundary = recoverTerrainTransitionBoundary(surface.triangles);
    skippedInterior += boundary.skippedInterior;
    const addPolygon = (subject: EnvironmentPoint[], edge: ClassifiedEdge, radialCenter?: EnvironmentPoint): void => {
      const definition = classes[edge.class_!], quadBounds = boundsOf(subject);
      const polygons: EnvironmentPoint[][] = [];
      for (const entry of surfaceGrid.query(quadBounds)) {
        const clipped = clipConvex(subject, entry);
        const area = clipped.length >= 3 ? Math.abs(orientedArea(clipped)) : 0;
        if (area <= EPS) continue;
        polygons.push(clipped); bandAreaM2 += area;
      }
      if (polygons.length === 0) return;
      const alphaAt = (point: EnvironmentPoint) => Math.max(0, Math.min(1, 1 -
        (radialCenter ? Math.hypot(point[0] - radialCenter[0], point[1] - radialCenter[1])
          : distanceToLine(point, edge.a, edge.b)) / definition.widthM)) * definition.strength;
      bandPieces.push({ polygons, alphaAt, radialCenter, widthM: definition.widthM,
        strength: definition.strength, tint: definition.tint, surfaceY: surface.surfaceY });
    };
    for (const ring of boundary.rings) {
      const segments: ClassifiedEdge[] = [];
      for (let index = 0; index < ring.length; index++) {
        const a = ring[index]!, b = ring[(index + 1) % ring.length]!;
        const dx = b[0] - a[0], dz = b[1] - a[1], length = Math.hypot(dx, dz);
        const inward: EnvironmentPoint = [-dz / length, dx / length];
        const count = Math.ceil(length / TERRAIN_TRANSITIONS_V1.segmentMaxM);
        for (let i = 0; i < count; i++) {
          const p0: EnvironmentPoint = [a[0] + dx * i / count, a[1] + dz * i / count];
          const p1: EnvironmentPoint = [a[0] + dx * (i + 1) / count, a[1] + dz * (i + 1) / count];
          const probe = shifted([(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2], inward, -TERRAIN_TRANSITIONS_V1.probeM);
          let class_: TerrainTransitionClass | null;
          // Topology already removed interior edges. Do not probe the host here: narrow
          // slivers across a hole used to suppress valid shoreline contributions.
          if (insideBuilding(probe)) class_ = "building";
          else if (insideWater(probe)) class_ = "water";
          else if (insidePaved(probe)) class_ = "paved";
          else if (softGrid.at(probe).some(entry => entry.surface !== surface && insideTriangle(probe, entry.triangle))) {
            skippedSoft++; class_ = null;
          } else class_ = "open";
          segments.push({ a: p0, b: p1, inward, class_ });
          if (class_) { pieces++; byClass[class_]++; }
        }
      }
      // Collapse collinear pieces of each same-class run into strip spans. Counts
      // retain the <=2 m classification pieces; geometry does not retain their seams.
      const spans: ClassifiedEdge[] = [];
      const canMerge = (a: ClassifiedEdge, b: ClassifiedEdge) => a.class_ === b.class_
        && a.inward[0] * b.inward[0] + a.inward[1] * b.inward[1] > 1 - 1e-14;
      for (const edge of segments) {
        const previous = spans.at(-1);
        if (previous && canMerge(previous, edge)) previous.b = edge.b;
        else spans.push({ ...edge });
      }
      if (spans.length > 1 && canMerge(spans.at(-1)!, spans[0]!)) {
        spans[0]!.a = spans.pop()!.a;
      }
      // Consecutive spans form one strip. Shared endpoints use one bisector, even
      // at a class change, so adjacent strips meet without duplicate darkening.
      for (let i = 0; i < spans.length; i++) {
        const edge = spans[i]!;
        if (!edge.class_) continue;
        const previous = spans[(i + spans.length - 1) % spans.length]!, next = spans[(i + 1) % spans.length]!;
        const width = classes[edge.class_].widthM;
        const start = bisector(previous.inward, edge.inward), end = bisector(edge.inward, next.inward);
        const startTurn = previous.inward[0] * edge.inward[1] - previous.inward[1] * edge.inward[0];
        const endTurn = edge.inward[0] * next.inward[1] - edge.inward[1] * next.inward[0];
        const startMiter = Math.hypot(...start), endMiter = Math.hypot(...end);
        const innerA = shifted(edge.a, edge.inward, width), innerB = shifted(edge.b, edge.inward, width);
        const midpoint: EnvironmentPoint = [(edge.a[0] + edge.b[0]) / 2, (edge.a[1] + edge.b[1]) / 2];
        let strip = [edge.a, edge.b, innerB, innerA];
        strip = clipBisector(strip, edge.a, start, midpoint);
        strip = clipBisector(strip, edge.b, end, midpoint);
        addPolygon(strip, edge);
        const join = (vertex: EnvironmentPoint, normal: EnvironmentPoint, other: EnvironmentPoint,
            miter: EnvironmentPoint, turn: number, length: number): void => {
          if (turn >= -EPS) return;
          if (length <= MITER_LIMIT) {
            addPolygon([vertex, shifted(vertex, normal, width), shifted(vertex, miter, width)], edge, vertex);
          } else {
            // Round fan beyond the 4x-width miter limit, ending at the shared
            // angular bisector. Circumscribed facets cover the complete radius.
            const angle = Math.atan2(normal[1], normal[0]);
            let delta = Math.atan2(other[1], other[0]) - angle;
            delta = Math.atan2(Math.sin(delta), Math.cos(delta)) / 2;
            const count = Math.max(1, Math.ceil(Math.abs(delta) / (Math.PI / 32)));
            const radius = width / Math.cos(delta / count / 2);
            for (let j = 0; j < count; j++) {
              const rim = (t: number): EnvironmentPoint => shifted(vertex, [Math.cos(angle + delta * t), Math.sin(angle + delta * t)], radius);
              addPolygon([vertex, rim(j / count), rim((j + 1) / count)], edge, vertex);
            }
          }
        };
        join(edge.a, edge.inward, previous.inward, start, startTurn, startMiter);
        join(edge.b, edge.inward, next.inward, end, endTurn, endMiter);
      }
    }
  }

  const treeBase = TERRAIN_TRANSITIONS_V1.treeBase;
  const fanPieces: FanPiece[] = [];
  let treeBases = 0;
  for (const tree of input.trees) {
    const placement: EnvironmentPoint = [tree.x, tree.z];
    const host = softGrid.at(placement)
      .find(entry => insideTriangle(placement, entry.triangle) && entry.surface.family === "grass")
      ?.surface;
    if (host === undefined) continue;
    const hostGrid = surfaceGrids.get(host)!;
    const radiusM = Math.min(1.6, Math.max(0.6, 0.18 * tree.heightM));
    treeBases++;
    const angleStep = 2 * Math.PI / treeBase.sides;
    const rim = (index: number): EnvironmentPoint => [tree.x + radiusM * Math.cos(index * angleStep),
      tree.z + radiusM * Math.sin(index * angleStep)];
    for (let side = 0; side < treeBase.sides; side++) {
      const fan = [placement, rim(side), rim(side + 1)] as const;
      const fanBounds = boundsOf([...fan]);
      for (const triangle of hostGrid.query(fanBounds)) {
        if (!overlapsBounds(boundsOf(triangle), fanBounds)) continue;
        const clipped = clipConvex([...fan], triangle);
        if (clipped.length < 3 || Math.abs(orientedArea(clipped)) <= EPS) continue;
        fanPieces.push({ polygon: clipped, centerX: tree.x, centerZ: tree.z, radiusM,
          surfaceY: host.surfaceY });
      }
    }
  }

  const buckets = new Map<number, { vertices: Vertex[] }>();
  const bucketFor = (surfaceY: number): { vertices: Vertex[] } => {
    const existing = buckets.get(surfaceY);
    if (existing !== undefined) return existing;
    const created = { vertices: [] as Vertex[] };
    buckets.set(surfaceY, created);
    return created;
  };
  /** Emits one clipped convex polygon as a fan of triangles (v0, vi, vi+1). Each triangle
   * is swapped into the winding whose three-dimensional right-hand-rule normal points up
   * (normal y > 0), which for an XZ-plane triangle is a NEGATIVE XZ signed area; the swap
   * is applied regardless of the clip's vertex order. The renderer keeps `FrontSide`, so
   * a downward-facing triangle would be culled and the layer would vanish from above. */
  const emitPolygon = (bucket: { vertices: Vertex[] }, polygon: readonly EnvironmentPoint[],
      alphaAt: (point: EnvironmentPoint) => number, tint: readonly [number, number, number],
      radial?: { center: EnvironmentPoint; width: number; strength: number }): void => {
    if (polygon.length < 3) return;
    for (let index = 1; index < polygon.length - 1; index++) {
      const triangle: EnvironmentPoint[] = [polygon[0]!, polygon[index]!, polygon[index + 1]!];
      // Decide winding after float32 quantization; discard collapsed slivers.
      for (let i = 0; i < 3; i++) triangle[i] = [Math.fround(triangle[i]![0]), Math.fround(triangle[i]![1])];
      const area = orientation(triangle[0]!, triangle[1]!, triangle[2]!);
      if (Math.abs(area) <= EPS) continue;
      if (area > 0) {
        const swapped = triangle[1]!;
        triangle[1] = triangle[2]!;
        triangle[2] = swapped;
      }
      for (const vertex of triangle) {
        bucket.vertices.push({ x: vertex[0], z: vertex[1], alpha: alphaAt(vertex), tint, radial: radial
          ? [vertex[0] - radial.center[0], vertex[1] - radial.center[1], 1 / radial.width, radial.strength] : [0, 0, 0, 0] });
      }
    }
  };
  for (const piece of bandPieces) {
    const bucket = bucketFor(piece.surfaceY);
    for (const polygon of piece.polygons) {
      emitPolygon(bucket, polygon, piece.alphaAt, piece.tint, piece.radialCenter
        ? { center: piece.radialCenter, width: piece.widthM, strength: piece.strength } : undefined);
    }
  }
  for (const piece of fanPieces) {
    const bucket = bucketFor(piece.surfaceY);
    emitPolygon(bucket, piece.polygon, (vertex) =>
      Math.max(0, Math.min(1,
        1 - Math.hypot(vertex[0] - piece.centerX, vertex[1] - piece.centerZ) / piece.radiusM))
        * treeBase.strength, treeBase.tint);
  }

  const layers = [...buckets.keys()].sort((x, y) => x - y).map(surfaceY => {
    const vertices = buckets.get(surfaceY)!.vertices;
    const positions = new Float32Array(vertices.length * 3);
    const colors = new Float32Array(vertices.length * 3);
    const alphas = new Float32Array(vertices.length);
    const radial = new Float32Array(vertices.length * 4);
    for (let index = 0; index < vertices.length; index++) {
      const vertex = vertices[index]!;
      positions[index * 3] = vertex.x;
      positions[index * 3 + 1] = surfaceY + LIFT_M;
      positions[index * 3 + 2] = vertex.z;
      const [r, g, b] = tintOf(vertex.tint);
      colors[index * 3] = r;
      colors[index * 3 + 1] = g;
      colors[index * 3 + 2] = b;
      alphas[index] = vertex.alpha;
      radial.set(vertex.radial, index * 4);
    }
    return Object.freeze({ surfaceY, positions, colors, alphas, radial });
  });
  return Object.freeze({ version: TERRAIN_TRANSITIONS_V1.version,
    layers: Object.freeze(layers), stats: Object.freeze({ pieces, skippedInterior, skippedSoft,
      byClass: Object.freeze(byClass), treeBases, bandAreaM2 }) });
}

/** Multiplicative terrain transition renderer: one blended mesh per surface height. */
export interface TerrainTransitionRenderer {
  readonly group: THREE.Group;
  dispose(): void;
}

const TRANSITION_VERTEX_SHADER = /* glsl */`
#include <common>
#include <fog_pars_vertex>
attribute float alpha;
attribute vec3 tint;
attribute vec4 radial;
varying vec4 vRadial;
varying float vAlpha;
varying vec3 vTint;
void main() {
  vRadial = radial;
  vAlpha = alpha;
  vTint = tint;
  vec4 mvPosition = modelViewMatrix * vec4( position, 1.0 );
  gl_Position = projectionMatrix * mvPosition;
  #include <fog_vertex>
}
`;

const TRANSITION_FRAGMENT_SHADER = /* glsl */`
#include <fog_pars_fragment>
varying float vAlpha;
varying vec3 vTint;
varying vec4 vRadial;
void main() {
  float alpha = vRadial.z > 0.0 ? clamp(1.0 - length(vRadial.xy) * vRadial.z, 0.0, 1.0) * vRadial.w : vAlpha;
  vec3 multiplier = mix( vec3( 1.0 ), vTint, alpha );
  #ifdef USE_FOG
    #ifdef FOG_EXP2
      float fogFactor = 1.0 - exp( - fogDensity * fogDensity * vFogDepth * vFogDepth );
    #else
      float fogFactor = smoothstep( fogNear, fogFar, vFogDepth );
    #endif
    multiplier = mix( multiplier, vec3( 1.0 ), fogFactor );
  #endif
  gl_FragColor = vec4( multiplier, 1.0 );
}
`;

/** Creates one `THREE.Mesh` per layer with a multiply-blended shader material. */
export function createTerrainTransitionLayer(plan: TerrainTransitionPlan): TerrainTransitionRenderer {
  const group = new THREE.Group();
  group.name = "Terrain transition bands and tree bases";
  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];
  for (const layer of plan.layers) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(layer.positions, 3));
    geometry.setAttribute("tint", new THREE.BufferAttribute(layer.colors, 3));
    geometry.setAttribute("alpha", new THREE.BufferAttribute(layer.alphas, 1));
    geometry.setAttribute("radial", new THREE.BufferAttribute(layer.radial, 4));
    geometries.push(geometry);
    const material = new THREE.ShaderMaterial({
      name: `city-terrain-transitions:${plan.version}`,
      uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog]),
      vertexShader: TRANSITION_VERTEX_SHADER,
      fragmentShader: TRANSITION_FRAGMENT_SHADER,
      blending: THREE.MultiplyBlending,
      premultipliedAlpha: true,
      transparent: true,
      depthWrite: false,
      toneMapped: false,
      fog: true,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      polygonOffsetUnits: -1,
    });
    materials.push(material);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = `Terrain transitions y=${layer.surfaceY}`;
    mesh.renderOrder = 1;
    mesh.receiveShadow = false;
    mesh.castShadow = false;
    mesh.userData.terrainTransitions = plan.stats;
    mesh.frustumCulled = false;
    group.add(mesh);
  }
  group.userData.terrainTransitions = plan.stats;
  return {
    group,
    dispose: () => {
      geometries.forEach(geometry => geometry.dispose());
      materials.forEach(material => material.dispose());
      group.clear();
    },
  };
}
