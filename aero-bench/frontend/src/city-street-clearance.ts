type Point = readonly [number, number];

export interface StreetLampLocation {
  readonly x: number;
  readonly z: number;
  readonly rotation_deg: number;
}

export interface StreetPavingPolygon {
  readonly outline: readonly Point[];
  readonly holes: readonly (readonly Point[])[];
}

export interface StreetCrossing {
  readonly shape: readonly Point[];
  readonly width: number;
}

function poleInCrossing(pole: StreetLampLocation, radius: number, crossings: readonly StreetCrossing[]): boolean {
  return crossings.some(crossing => crossing.shape.slice(1).some((b, i) => {
    const a = crossing.shape[i]!, dx = b[0] - a[0], dz = b[1] - a[1];
    const squared = dx * dx + dz * dz;
    const t = squared > 0 ? Math.max(0, Math.min(1, ((pole.x - a[0]) * dx + (pole.z - a[1]) * dz) / squared)) : 0;
    return Math.hypot(pole.x - a[0] - t * dx, pole.z - a[1] - t * dz) < crossing.width / 2 + .45 + radius;
  }));
}

type VehicleSample = readonly [id: string, x: number, z: number, heading: number, type: string, groundY?: number];
type PersonSample = readonly [id: string, x: number, z: number, heading: number, groundY?: number];

export interface StreetTrafficData {
  readonly schema_version: "aero-bench.city-sumo-preview/v1" | "aero-bench.city-sumo-preview/v2";
  readonly source_network_sha256: string;
  readonly mesh_pack_source_sha256: string;
  readonly step_seconds: number;
  readonly frames: readonly {
    readonly vehicles: readonly VehicleSample[];
    readonly persons: readonly PersonSample[];
  }[];
  readonly signals: readonly { readonly x: number; readonly z: number }[];
}

export interface StreetLampGeometry {
  /** The pole centre is this far from the generated placement toward the road. */
  readonly poleOffsetM: number;
  readonly poleRadiusM: number;
  /** Conservative mesh XZ bounds within each rendered height band, relative to the pole. */
  readonly slices: readonly {
    readonly minY: number;
    readonly maxY: number;
    readonly minX: number;
    readonly maxX: number;
    readonly halfZ: number;
  }[];
}

export interface StreetLampClearance {
  readonly lamps: readonly StreetLampLocation[];
  readonly conflictingIndices: readonly number[];
  readonly relocatedIndices: readonly number[];
  readonly omittedIndices: readonly number[];
}

export interface StaticStreetSignal { readonly x: number; readonly z: number; }

interface BusPose {
  readonly x: number;
  readonly z: number;
  readonly cos: number;
  readonly sin: number;
  readonly bottomY: number;
  readonly topY: number;
}

interface IndexedPolygon extends StreetPavingPolygon {
  readonly minX: number;
  readonly maxX: number;
  readonly minZ: number;
  readonly maxZ: number;
}

// These are the displayed bus collision-box dimensions in city-presentation.ts.
const BUS_HALF_WIDTH_M = 2.5 / 2;
const BUS_HALF_LENGTH_M = 11.5 / 2;
const BUS_HEIGHT_M = 3.3;
const MAX_TRANSLATION_STEP_M = 0.2;
const MAX_HEADING_STEP_DEG = 2;
const CLEARANCE_M = 0.15;
const GRID_M = 20;

function gridKey(x: number, z: number): string {
  return `${Math.floor(x / GRID_M)},${Math.floor(z / GRID_M)}`;
}

function busPoseGrid(traffic: StreetTrafficData): Map<string, BusPose[]> {
  const grid = new Map<string, BusPose[]>();
  for (let frameIndex = 0; frameIndex < traffic.frames.length; frameIndex++) {
    const frame = traffic.frames[frameIndex]!;
    const following = traffic.frames[Math.min(frameIndex + 1, traffic.frames.length - 1)]!;
    const nextBuses = new Map(following.vehicles.filter(sample => sample[4] === "bus")
      .map(sample => [sample[0], sample]));
    for (const sample of frame.vehicles) {
      if (sample[4] !== "bus") continue;
      const after = nextBuses.get(sample[0]) ?? sample;
      const dx = after[1] - sample[1], dz = after[2] - sample[2];
      const headingDelta = ((after[3] - sample[3] + 540) % 360) - 180;
      const steps = Math.max(1, Math.ceil(Math.hypot(dx, dz) / MAX_TRANSLATION_STEP_M),
        Math.ceil(Math.abs(headingDelta) / MAX_HEADING_STEP_DEG));
      for (let step = 0; step < steps; step++) {
        const fraction = step / steps;
        const heading = (sample[3] + headingDelta * fraction) * Math.PI / 180;
        const bottomY = (sample[5] ?? 0.1) + ((after[5] ?? 0.1) - (sample[5] ?? 0.1)) * fraction + 0.01;
        const pose: BusPose = { x: sample[1] + dx * fraction, z: sample[2] + dz * fraction,
          cos: Math.cos(heading), sin: Math.sin(heading), bottomY, topY: bottomY + BUS_HEIGHT_M };
        const key = gridKey(pose.x, pose.z);
        const bucket = grid.get(key) ?? [];
        bucket.push(pose);
        grid.set(key, bucket);
      }
    }
  }
  return grid;
}

/** Segment intersection with a bus box in bus-local XZ coordinates. */
function segmentHitsBox(ax: number, az: number, bx: number, bz: number,
                        halfWidth: number, halfLength: number): boolean {
  let enter = 0, leave = 1;
  const dx = bx - ax, dz = bz - az;
  for (const [slope, distance] of [
    [-dx, ax + halfWidth], [dx, halfWidth - ax],
    [-dz, az + halfLength], [dz, halfLength - az],
  ] as const) {
    if (Math.abs(slope) < 1e-10) {
      if (distance < 0) return false;
      continue;
    }
    const edge = distance / slope;
    if (slope < 0) enter = Math.max(enter, edge);
    else leave = Math.min(leave, edge);
    if (enter > leave) return false;
  }
  return true;
}

function lampIntersectsBus(lamp: StreetLampLocation, geometry: StreetLampGeometry,
                           buses: ReadonlyMap<string, readonly BusPose[]>): boolean {
  const angle = lamp.rotation_deg * Math.PI / 180;
  const cos = Math.cos(angle), sin = Math.sin(angle);
  const poleX = lamp.x - cos * geometry.poleOffsetM;
  const poleZ = lamp.z + sin * geometry.poleOffsetM;
  // Between sampled poses, a bus corner can move at most 0.2 m in translation
  // plus 5.89 m * 2 degrees in rotation. Half that span is under 0.21 m.
  const cellX = Math.floor(lamp.x / GRID_M), cellZ = Math.floor(lamp.z / GRID_M);
  for (let x = cellX - 1; x <= cellX + 1; x++) {
    for (let z = cellZ - 1; z <= cellZ + 1; z++) {
      for (const bus of buses.get(`${x},${z}`) ?? []) {
        if (Math.abs(bus.x - lamp.x) > 12 || Math.abs(bus.z - lamp.z) > 12) continue;
        let minX = Infinity, maxX = -Infinity, halfZ = 0;
        for (const slice of geometry.slices) {
          if (bus.topY + CLEARANCE_M < slice.minY
              || bus.bottomY - CLEARANCE_M > slice.maxY) continue;
          minX = Math.min(minX, slice.minX);
          maxX = Math.max(maxX, slice.maxX);
          halfZ = Math.max(halfZ, slice.halfZ);
        }
        if (minX === Infinity) continue;
        const startWorldX = poleX + minX * cos, startWorldZ = poleZ - minX * sin;
        const endWorldX = poleX + maxX * cos, endWorldZ = poleZ - maxX * sin;
        const startX = (startWorldX - bus.x) * bus.cos + (startWorldZ - bus.z) * bus.sin;
        const startZ = -(startWorldX - bus.x) * bus.sin + (startWorldZ - bus.z) * bus.cos;
        const endX = (endWorldX - bus.x) * bus.cos + (endWorldZ - bus.z) * bus.sin;
        const endZ = -(endWorldX - bus.x) * bus.sin + (endWorldZ - bus.z) * bus.cos;
        const padding = halfZ + CLEARANCE_M + 0.21;
        if (segmentHitsBox(startX, startZ, endX, endZ,
          BUS_HALF_WIDTH_M + padding, BUS_HALF_LENGTH_M + padding)) return true;
      }
    }
  }
  return false;
}

function pointInRing(x: number, z: number, ring: readonly Point[]): boolean {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index++) {
    const a = ring[index]!, b = ring[previous]!;
    if ((a[1] > z) !== (b[1] > z)
        && x < (b[0] - a[0]) * (z - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

function sidewalkContains(x: number, z: number, polygons: readonly IndexedPolygon[]): boolean {
  return polygons.some(polygon => x >= polygon.minX && x <= polygon.maxX
    && z >= polygon.minZ && z <= polygon.maxZ
    && pointInRing(x, z, polygon.outline)
    && !polygon.holes.some(hole => pointInRing(x, z, hole)));
}

function pavedPoleFootprint(x: number, z: number, radius: number,
                            polygons: readonly IndexedPolygon[]): boolean {
  if (!sidewalkContains(x, z, polygons)) return false;
  for (let index = 0; index < 8; index++) {
    const angle = index * Math.PI / 4;
    if (!sidewalkContains(x + radius * Math.cos(angle), z + radius * Math.sin(angle), polygons)) return false;
  }
  return true;
}

function indexedPolygons(polygons: readonly StreetPavingPolygon[]): IndexedPolygon[] {
  return polygons.map(polygon => {
    const xs = polygon.outline.map(point => point[0]);
    const zs = polygon.outline.map(point => point[1]);
    return { ...polygon, minX: Math.min(...xs), maxX: Math.max(...xs),
      minZ: Math.min(...zs), maxZ: Math.max(...zs) };
  });
}

function personGrid(traffic: StreetTrafficData): Map<string, Point[]> {
  const grid = new Map<string, Point[]>();
  for (const frame of traffic.frames) for (const person of frame.persons) {
    const key = gridKey(person[1], person[2]);
    const bucket = grid.get(key) ?? [];
    bucket.push([person[1], person[2]]);
    grid.set(key, bucket);
  }
  return grid;
}

function personNear(x: number, z: number, people: ReadonlyMap<string, readonly Point[]>): boolean {
  const cellX = Math.floor(x / GRID_M), cellZ = Math.floor(z / GRID_M);
  for (let cx = cellX - 1; cx <= cellX + 1; cx++) for (let cz = cellZ - 1; cz <= cellZ + 1; cz++) {
    for (const point of people.get(`${cx},${cz}`) ?? []) {
      if (Math.hypot(x - point[0], z - point[1]) < 0.85) return true;
    }
  }
  return false;
}

/** Validate authored curb placements; omit conflicts without moving poles off their facility strip. */
export function resolveStreetLampClearance(lamps: readonly StreetLampLocation[],
                                           walkbed: readonly StreetPavingPolygon[],
                                           traffic: StreetTrafficData,
                                           geometry: StreetLampGeometry,
                                           crossings: readonly StreetCrossing[]): StreetLampClearance {
  if (!["aero-bench.city-sumo-preview/v1", "aero-bench.city-sumo-preview/v2"].includes(traffic.schema_version)
      || traffic.frames.length < 2
      || traffic.step_seconds !== 0.25 || walkbed.length === 0
      || geometry.poleOffsetM <= 0 || geometry.poleRadiusM <= 0 || geometry.slices.length === 0) {
    throw new Error("Street lamp clearance requires matching recorded SUMO frames and measured lamp geometry");
  }
  const buses = busPoseGrid(traffic);
  const polygons = indexedPolygons(walkbed);
  const people = personGrid(traffic);
  const conflicts = lamps.flatMap((lamp, index) =>
    !pavedPoleFootprint(lamp.x, lamp.z, geometry.poleRadiusM + CLEARANCE_M, polygons)
    || lampIntersectsBus(lamp, geometry, buses)
    || poleInCrossing(lamp, geometry.poleRadiusM, crossings)
    || personNear(lamp.x, lamp.z, people)
    || traffic.signals.some(signal => Math.hypot(lamp.x - signal.x, lamp.z - signal.z) < 1.5)
      ? [index] : []);
  const conflicting = new Set(conflicts);
  return { lamps: lamps.filter((_, index) => !conflicting.has(index)),
    conflictingIndices: conflicts, relocatedIndices: [], omittedIndices: conflicts };
}

/** Static facility clearance from the displayed walkbed and this network's real signals. */
export function resolveStaticStreetLampClearance(lamps: readonly StreetLampLocation[],
                                                 walkbed: readonly StreetPavingPolygon[],
                                                 signals: readonly StaticStreetSignal[],
                                                 geometry: StreetLampGeometry,
                                                 crossings: readonly StreetCrossing[]): StreetLampClearance {
  if (walkbed.length === 0 || geometry.poleOffsetM <= 0 || geometry.poleRadiusM <= 0
      || geometry.slices.length === 0 || signals.some(signal => !Number.isFinite(signal.x) || !Number.isFinite(signal.z))) {
    throw new Error("Street lamp static clearance requires displayed pavement, measured lamp geometry and real network signals");
  }
  const polygons = indexedPolygons(walkbed);
  const conflicts = lamps.flatMap((lamp, index) =>
    !pavedPoleFootprint(lamp.x, lamp.z, geometry.poleRadiusM + CLEARANCE_M, polygons)
    || poleInCrossing(lamp, geometry.poleRadiusM, crossings)
    || signals.some(signal => Math.hypot(lamp.x - signal.x, lamp.z - signal.z) < 1.5)
      ? [index] : []);
  const conflicting = new Set(conflicts);
  return { lamps: lamps.filter((_, index) => !conflicting.has(index)),
    conflictingIndices: conflicts, relocatedIndices: [], omittedIndices: conflicts };
}
