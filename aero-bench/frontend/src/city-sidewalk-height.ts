export type CityGroundPoint = readonly [x: number, z: number];

export interface SidewalkHeightPolygon {
  readonly outline: readonly CityGroundPoint[];
  readonly holes: readonly (readonly CityGroundPoint[])[];
}

interface Bounds {
  readonly minX: number;
  readonly maxX: number;
  readonly minZ: number;
  readonly maxZ: number;
}

interface IndexedPolygon extends Bounds {
  readonly outline: readonly CityGroundPoint[];
  readonly holes: readonly (readonly CityGroundPoint[])[];
}

interface SpatialGrid<T extends Bounds> {
  readonly cells: ReadonlyMap<string, readonly T[]>;
  readonly wide: readonly T[];
}

const GRID_CELL_M = 16;
const MAX_CELLS_PER_FEATURE = 4096;
const BOUNDARY_EPSILON_M = 1e-8;

function gridKey(cellX: number, cellZ: number): string {
  return `${cellX},${cellZ}`;
}

function validatePoint(point: CityGroundPoint, description: string): void {
  if (point.length !== 2 || !Number.isFinite(point[0]) || !Number.isFinite(point[1])) {
    throw new Error(`${description} must contain finite X/Z coordinates`);
  }
}

function validateRing(ring: readonly CityGroundPoint[], description: string): void {
  if (ring.length < 3) throw new Error(`${description} must contain at least three points`);
  for (const point of ring) validatePoint(point, description);
}

function pointBounds(points: readonly CityGroundPoint[]): Bounds {
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (const [x, z] of points) {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minZ = Math.min(minZ, z);
    maxZ = Math.max(maxZ, z);
  }
  return { minX, maxX, minZ, maxZ };
}

function createSpatialGrid<T extends Bounds>(features: readonly T[]): SpatialGrid<T> {
  const cells = new Map<string, T[]>();
  const wide: T[] = [];
  for (const feature of features) {
    const minCellX = Math.floor(feature.minX / GRID_CELL_M);
    const maxCellX = Math.floor(feature.maxX / GRID_CELL_M);
    const minCellZ = Math.floor(feature.minZ / GRID_CELL_M);
    const maxCellZ = Math.floor(feature.maxZ / GRID_CELL_M);
    const cellCount = (maxCellX - minCellX + 1) * (maxCellZ - minCellZ + 1);
    if (!Number.isSafeInteger(cellCount) || cellCount > MAX_CELLS_PER_FEATURE) {
      wide.push(feature);
      continue;
    }
    for (let cellX = minCellX; cellX <= maxCellX; cellX++) {
      for (let cellZ = minCellZ; cellZ <= maxCellZ; cellZ++) {
        const key = gridKey(cellX, cellZ);
        const bucket = cells.get(key) ?? [];
        bucket.push(feature);
        cells.set(key, bucket);
      }
    }
  }
  return { cells, wide };
}

function candidatesAt<T extends Bounds>(grid: SpatialGrid<T>, x: number, z: number): readonly T[] {
  return grid.cells.get(gridKey(Math.floor(x / GRID_CELL_M), Math.floor(z / GRID_CELL_M))) ?? [];
}

function pointInRing(x: number, z: number, ring: readonly CityGroundPoint[]): boolean {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index++) {
    const [ax, az] = ring[index]!, [bx, bz] = ring[previous]!;
    const edgeX = bx - ax, edgeZ = bz - az;
    const edgeLengthSquared = edgeX * edgeX + edgeZ * edgeZ;
    const projection = edgeLengthSquared <= 1e-20 ? 0
      : Math.max(0, Math.min(1, ((x - ax) * edgeX + (z - az) * edgeZ) / edgeLengthSquared));
    const nearestX = ax + projection * edgeX, nearestZ = az + projection * edgeZ;
    if (Math.hypot(x - nearestX, z - nearestZ) <= BOUNDARY_EPSILON_M) return true;
    if ((az > z) !== (bz > z) && x < edgeX * (z - az) / edgeZ + ax) inside = !inside;
  }
  return inside;
}

function polygonContains(x: number, z: number, polygon: IndexedPolygon): boolean {
  if (x < polygon.minX || x > polygon.maxX || z < polygon.minZ || z > polygon.maxZ
      || !pointInRing(x, z, polygon.outline)) return false;
  return !polygon.holes.some(hole => pointInRing(x, z, hole));
}

function indexPolygons(polygons: readonly SidewalkHeightPolygon[]): IndexedPolygon[] {
  return polygons.map((polygon, index) => {
    validateRing(polygon.outline, `Sidewalk polygon ${index} outline`);
    for (const [holeIndex, hole] of polygon.holes.entries()) {
      validateRing(hole, `Sidewalk polygon ${index} hole ${holeIndex}`);
    }
    return { ...pointBounds(polygon.outline), outline: polygon.outline, holes: polygon.holes };
  });
}

/** Create a metre-accurate sampler for the current flat sidewalk and road faces. */
export function createSidewalkHeightSampler(
  polygons: readonly SidewalkHeightPolygon[],
  heightM: number,
  roadHeightM: number,
): (x: number, z: number, sourceGroundY: number) => number {
  if (!Number.isFinite(heightM) || !Number.isFinite(roadHeightM) || heightM < roadHeightM) {
    throw new Error("Sidewalk height must be finite and at least the finite road height");
  }
  const sidewalkPolygons = indexPolygons(polygons);
  const sidewalkGrid = createSpatialGrid(sidewalkPolygons);

  return (x: number, z: number, sourceGroundY: number): number => {
    if (![x, z, sourceGroundY].every(Number.isFinite)) {
      throw new Error("Sidewalk height sample must contain finite X/Z and source ground height");
    }
    let inWalkbed = false;
    for (const polygon of candidatesAt(sidewalkGrid, x, z)) {
      if (polygonContains(x, z, polygon)) {
        inWalkbed = true;
        break;
      }
    }
    if (!inWalkbed) {
      for (const polygon of sidewalkGrid.wide) {
        if (polygonContains(x, z, polygon)) {
          inWalkbed = true;
          break;
        }
      }
    }
    return Math.max(sourceGroundY, inWalkbed ? heightM : roadHeightM);
  };
}
