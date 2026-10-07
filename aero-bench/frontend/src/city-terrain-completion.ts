import type { EnvironmentPoint, EnvironmentPolygon } from "./city-environment";
import { CITY_AUTHORED_LANDSCAPE_KINDS, parseCityAuthoredLandscape,
  type CityAuthoredLandscapeItem, type CityAuthoredLandscapeKind } from "./city-authored-landscape";

export const TERRAIN_COMPLETION_V1 = Object.freeze({
  version: "city-terrain-completion-v1", cellM: 2, minAreaM2: 40, maxItems: 400,
  idPrefix: "auto-completion-v1-",
});
export interface TerrainCompletionInput {
  readonly extent: EnvironmentPolygon;
  readonly roadbed: readonly EnvironmentPolygon[];
  readonly walkbed: readonly EnvironmentPolygon[];
  readonly buildings: readonly EnvironmentPolygon[];
  /** Every drawn source ground triangle and existing authored items. */
  readonly occupied: readonly (readonly EnvironmentPoint[])[];
}
const KIND_LABELS = { green: "绿地设计", plaza: "广场设计", planting_strip: "种植带设计" };
const INDEX_CELL_M = 32;
const EPS = 1e-7;

function onSegment(p: EnvironmentPoint, a: EnvironmentPoint, b: EnvironmentPoint): boolean {
  return Math.abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) <= EPS
    && p[0] >= Math.min(a[0], b[0]) - EPS && p[0] <= Math.max(a[0], b[0]) + EPS
    && p[1] >= Math.min(a[1], b[1]) - EPS && p[1] <= Math.max(a[1], b[1]) + EPS;
}
function insideRing(p: EnvironmentPoint, ring: readonly EnvironmentPoint[]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[j]!, b = ring[i]!;
    if (onSegment(p, a, b)) return true;
    if ((a[1] > p[1]) !== (b[1] > p[1])
      && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}
function insidePolygon(p: EnvironmentPoint, polygon: EnvironmentPolygon): boolean {
  return insideRing(p, polygon.outline) && !polygon.holes.some(hole => insideRing(p, hole));
}
function bounds(ring: readonly EnvironmentPoint[]) {
  let minX = Infinity, minZ = Infinity, maxX = -Infinity, maxZ = -Infinity;
  for (const [x, z] of ring) {
    minX = Math.min(minX, x); minZ = Math.min(minZ, z);
    maxX = Math.max(maxX, x); maxZ = Math.max(maxZ, z);
  }
  return { minX, minZ, maxX, maxZ };
}

/** A uniform 32 m index; cells query only nearby polygons, never the whole source. */
class PolygonIndex {
  private readonly buckets = new Map<string, EnvironmentPolygon[]>();
  constructor(polygons: readonly EnvironmentPolygon[]) {
    for (const polygon of polygons) {
      const box = bounds(polygon.outline);
      for (let z = Math.floor(box.minZ / INDEX_CELL_M); z <= Math.floor(box.maxZ / INDEX_CELL_M); z++)
        for (let x = Math.floor(box.minX / INDEX_CELL_M); x <= Math.floor(box.maxX / INDEX_CELL_M); x++) {
          const key = `${x}:${z}`, bucket = this.buckets.get(key);
          if (bucket === undefined) this.buckets.set(key, [polygon]); else bucket.push(polygon);
        }
    }
  }
  query(x: number, z: number, size: number): Set<EnvironmentPolygon> {
    const result = new Set<EnvironmentPolygon>();
    for (let iz = Math.floor(z / INDEX_CELL_M); iz <= Math.floor((z + size) / INDEX_CELL_M); iz++)
      for (let ix = Math.floor(x / INDEX_CELL_M); ix <= Math.floor((x + size) / INDEX_CELL_M); ix++)
        for (const polygon of this.buckets.get(`${ix}:${iz}`) ?? []) result.add(polygon);
    return result;
  }
}

/** Detect source edges crossing a cell even when all five samples miss a thin triangle. */
function ringTouchesCell(ring: readonly EnvironmentPoint[], x: number, z: number, size: number): boolean {
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i]!, b = ring[(i + 1) % ring.length]!;
    let low = 0, high = 1;
    for (const [start, delta, min, max] of [
      [a[0], b[0] - a[0], x, x + size], [a[1], b[1] - a[1], z, z + size],
    ] as const) {
      if (Math.abs(delta) <= EPS) {
        if (start < min - EPS || start > max + EPS) { high = -1; break; }
      } else {
        const t1 = (min - start) / delta, t2 = (max - start) / delta;
        low = Math.max(low, Math.min(t1, t2)); high = Math.min(high, Math.max(t1, t2));
      }
    }
    if (low <= high + EPS) return true;
  }
  return false;
}

/**
 * Design suggestions only: source classifications and geometry stay unchanged.
 * The version and fixed world-grid origin replace a random seed. Cells partially
 * covered by source ground are excluded, so gaps of up to one cell remain next
 * to source polygons. This is an approximation, not a classification. Precise
 * road/building/extent clipping remains the authored landscape planner's job.
 */
export interface TerrainCompletionOptions {
  /** Test seam; production callers use the TERRAIN_COMPLETION_V1 default. */
  readonly maxItems?: number;
}
export interface TerrainCompletionStats {
  readonly residualCells: number;
  readonly rectangles: number;
  readonly areaM2: number;
  readonly droppedSmall: number;
  readonly truncated: boolean;
}
export function proposeTerrainCompletion(input: TerrainCompletionInput, kind: CityAuthoredLandscapeKind,
  options: TerrainCompletionOptions = {}): {
  readonly version: string;
  readonly kind: CityAuthoredLandscapeKind;
  readonly items: readonly CityAuthoredLandscapeItem[];
  readonly stats: TerrainCompletionStats;
} {
  if (!CITY_AUTHORED_LANDSCAPE_KINDS.includes(kind)) throw new Error("Terrain completion requires an explicit valid kind");
  if (options.maxItems !== undefined && (!Number.isInteger(options.maxItems) || options.maxItems <= 0))
    throw new Error("Terrain completion cap must be a positive integer");
  const { cellM, minAreaM2, version, idPrefix } = TERRAIN_COMPLETION_V1;
  const maxItems = options.maxItems ?? TERRAIN_COMPLETION_V1.maxItems;
  const box = bounds(input.extent.outline);
  const obstacles = new PolygonIndex([...input.roadbed, ...input.walkbed, ...input.buildings]);
  const occupied = new PolygonIndex(input.occupied.map(outline => ({ outline, holes: [] })));
  const rows = new Map<number, Map<string, { ix: number; end: number; used: boolean }>>();
  let residualCells = 0;
  for (let iz = Math.floor(box.minZ / cellM); iz < Math.ceil(box.maxZ / cellM); iz++) {
    const row = new Map<string, { ix: number; end: number; used: boolean }>();
    let run: number | null = null;
    const endX = Math.ceil(box.maxX / cellM);
    for (let ix = Math.floor(box.minX / cellM); ix <= endX; ix++) {
      let residual = false;
      if (ix < endX) {
        const x = ix * cellM, z = iz * cellM;
        const points: EnvironmentPoint[] = [[x, z], [x + cellM, z], [x + cellM, z + cellM],
          [x, z + cellM], [x + cellM / 2, z + cellM / 2]];
        residual = points.every(p => insidePolygon(p, input.extent))
          && ![...obstacles.query(x, z, cellM)].some(polygon => points.some(p => insidePolygon(p, polygon)))
          && ![...occupied.query(x, z, cellM)].some(polygon =>
            points.some(p => insidePolygon(p, polygon)) || ringTouchesCell(polygon.outline, x, z, cellM));
      }
      if (residual) { residualCells++; if (run === null) run = ix; }
      else if (run !== null) { row.set(`${run}:${ix}`, { ix: run, end: ix, used: false }); run = null; }
    }
    rows.set(iz, row);
  }
  const rectangles: { ix: number; iz: number; endX: number; endZ: number; area: number }[] = [];
  let droppedSmall = 0;
  for (const [iz, row] of rows) for (const [key, run] of row) {
    if (run.used) continue;
    run.used = true;
    let endZ = iz + 1;
    for (;;) {
      const next = rows.get(endZ)?.get(key);
      if (next === undefined || next.used) break;
      next.used = true; endZ++;
    }
    const area = (run.end - run.ix) * (endZ - iz) * cellM ** 2;
    if (area < minAreaM2) droppedSmall++;
    else rectangles.push({ ix: run.ix, iz, endX: run.end, endZ, area });
  }
  // Grid order (z, then x) fixes rectangle identity; when the cap truncates, the
  // retained set keeps the largest areas so one band of the city is not favoured.
  rectangles.sort((a, b) => a.iz - b.iz || a.ix - b.ix);
  const ordered = rectangles.length > maxItems
    ? [...rectangles].sort((a, b) => b.area - a.area || a.iz - b.iz || a.ix - b.ix).slice(0, maxItems).sort((a, b) => a.iz - b.iz || a.ix - b.ix)
    : rectangles;
  const items = parseCityAuthoredLandscape(ordered.map((r, index): CityAuthoredLandscapeItem => ({
    id: `${idPrefix}${kind}-${r.ix}-${r.iz}`, label: `自动补全·${KIND_LABELS[kind]} ${index + 1}`,
    provenance: "authored", kind,
    polygon: [{ x: r.ix * cellM, z: r.iz * cellM }, { x: r.endX * cellM, z: r.iz * cellM },
      { x: r.endX * cellM, z: r.endZ * cellM }, { x: r.ix * cellM, z: r.endZ * cellM }],
  })));
  return { version, kind, items, stats: { residualCells, rectangles: rectangles.length,
    areaM2: ordered.reduce((sum, r) => sum + r.area, 0), droppedSmall, truncated: rectangles.length > maxItems } };
}
export function isTerrainCompletionItem(item: CityAuthoredLandscapeItem): boolean {
  return item.id.startsWith(TERRAIN_COMPLETION_V1.idPrefix);
}
