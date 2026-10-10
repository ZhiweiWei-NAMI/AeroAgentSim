/** Collision-checked authoring route for a verified selected city. No flight is executed here. */
import { validateFlightSegment, type AirspacePolygon, type FacilityPlacement,
  type LandingPadClearance, type PlacementBox, type PointXYZ } from "./city-workspace-geometry";

export interface SelectedRouteInput {
  readonly start: PointXYZ;
  readonly goal: PointXYZ;
  readonly bounds: { readonly minX: number; readonly maxX: number;
    readonly minZ: number; readonly maxZ: number };
  readonly cruiseAltitudeM: number;
  readonly speedMps: number;
  readonly departureAtS: number;
  readonly bodySizeM: { readonly x: number; readonly y: number; readonly z: number };
  /** Building and measured street-asset collision boxes from the selected presentation. */
  readonly obstacles: readonly PlacementBox[];
  readonly facilities: readonly FacilityPlacement[];
  readonly noFlyZones: readonly AirspacePolygon[];
  readonly landingPads: readonly LandingPadClearance[];
  readonly gridStepM?: number;
  readonly maxVisitedNodes?: number;
}

export type SelectedRouteFailureCode = "invalid_input" | "endpoint_collision" | "no_route" | "search_budget_exhausted";
export type SelectedRouteResult =
  | { readonly ok: true; readonly purpose: "selected-route-authoring"; readonly executable: false;
      readonly waypoints: readonly PointXYZ[]; readonly waypointTimesS: readonly number[];
      readonly pathLengthM: number; readonly travelTimeS: number; readonly visitedNodes: number }
  | { readonly ok: false; readonly code: SelectedRouteFailureCode; readonly message: string;
      readonly visitedNodes: number };

const EPS = 1e-8;
const DIRECTIONS: readonly (readonly [number, number])[] = [
  [-1, 0], [0, -1], [0, 1], [1, 0], [-1, -1], [-1, 1], [1, -1], [1, 1],
];

function distance(a: PointXYZ, b: PointXYZ): number {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}
function same(a: PointXYZ, b: PointXYZ): boolean {
  return Math.abs(a.x - b.x) <= EPS && Math.abs(a.y - b.y) <= EPS && Math.abs(a.z - b.z) <= EPS;
}
function failure(code: SelectedRouteFailureCode, message: string, visitedNodes = 0): SelectedRouteResult {
  return { ok: false, code, message, visitedNodes };
}
function validateInput(input: SelectedRouteInput): { step: number; budget: number } {
  const numeric = [input.start.x, input.start.y, input.start.z, input.goal.x, input.goal.y, input.goal.z,
    input.bounds.minX, input.bounds.maxX, input.bounds.minZ, input.bounds.maxZ,
    input.cruiseAltitudeM, input.speedMps, input.departureAtS,
    input.bodySizeM.x, input.bodySizeM.y, input.bodySizeM.z];
  if (numeric.some(value => !Number.isFinite(value)) || input.speedMps <= 0 || input.departureAtS < 0
      || input.bodySizeM.x <= 0 || input.bodySizeM.y <= 0 || input.bodySizeM.z <= 0
      || input.bounds.maxX <= input.bounds.minX || input.bounds.maxZ <= input.bounds.minZ
      || input.start.y < input.bodySizeM.y / 2 || input.goal.y < input.bodySizeM.y / 2
      || input.cruiseAltitudeM < Math.max(input.start.y, input.goal.y)
      || [input.start, input.goal].some(point => point.x < input.bounds.minX || point.x > input.bounds.maxX
        || point.z < input.bounds.minZ || point.z > input.bounds.maxZ)) {
    throw new Error("航线坐标、边界、机体尺寸、速度或巡航高度无效");
  }
  if (!Array.isArray(input.obstacles) || !Array.isArray(input.facilities)
      || !Array.isArray(input.noFlyZones) || !Array.isArray(input.landingPads)) {
    throw new Error("航线缺少建筑、街道设施、设施或空域的明确输入");
  }
  const step = input.gridStepM ?? 8;
  const budget = input.maxVisitedNodes ?? 6000;
  if (!Number.isFinite(step) || step <= 0 || step > 100
      || !Number.isSafeInteger(budget) || budget <= 0 || budget > 100000) {
    throw new Error("航线网格步长或搜索预算无效");
  }
  return { step, budget };
}

/** Validate every returned segment at its actual planned time. */
function assemble(input: SelectedRouteInput, points: readonly PointXYZ[], visitedNodes: number): SelectedRouteResult {
  const waypoints: PointXYZ[] = [];
  for (const point of points) if (waypoints.length === 0 || !same(waypoints[waypoints.length - 1]!, point)) {
    waypoints.push(point);
  }
  if (waypoints.length < 2) return failure("invalid_input", "起终点必须不同", visitedNodes);
  const times = [input.departureAtS];
  let length = 0;
  for (let index = 1; index < waypoints.length; index++) {
    const from = waypoints[index - 1]!, to = waypoints[index]!;
    const segmentLength = distance(from, to);
    const end = times[index - 1]! + segmentLength / input.speedMps;
    if (!Number.isFinite(end) || end <= times[index - 1]!) {
      return failure("invalid_input", "航线时间超出可表示范围", visitedNodes);
    }
    const issues = validateFlightSegment(from, to, times[index - 1]!, end,
      input.obstacles, input.facilities, input.noFlyZones, input.bodySizeM, input.landingPads);
    if (issues.length) return failure("no_route", `航段与 ${issues[0]!.obstacleId} 相交`, visitedNodes);
    length += segmentLength;
    times.push(end);
  }
  return { ok: true, purpose: "selected-route-authoring", executable: false,
    waypoints, waypointTimesS: times, pathLengthM: length,
    travelTimeS: times[times.length - 1]! - input.departureAtS, visitedNodes };
}

/** Direct path first; otherwise deterministic bounded A* at the requested altitude. */
export function planSelectedCityRoute(input: SelectedRouteInput): SelectedRouteResult {
  let step: number, budget: number;
  try { ({ step, budget } = validateInput(input)); }
  catch (error) { return failure("invalid_input", error instanceof Error ? error.message : String(error)); }
  const startCruise: PointXYZ = { x: input.start.x, y: input.cruiseAltitudeM, z: input.start.z };
  const goalCruise: PointXYZ = { x: input.goal.x, y: input.cruiseAltitudeM, z: input.goal.z };
  const ascendS = distance(input.start, startCruise) / input.speedMps;
  const ascent = same(input.start, startCruise) ? null : assemble(input, [input.start, startCruise], 0);
  if (ascent !== null && !ascent.ok) return failure("endpoint_collision", `起点上升通道不可用：${ascent.message}`);
  const direct = assemble(input, [input.start, startCruise, goalCruise, input.goal], 0);
  if (direct.ok) return direct;

  const width = Math.floor((input.bounds.maxX - input.bounds.minX) / step) + 1;
  const depth = Math.floor((input.bounds.maxZ - input.bounds.minZ) / step) + 1;
  if (!Number.isSafeInteger(width * depth) || width * depth > 250000) {
    return failure("invalid_input", "航线网格规模超出上限");
  }
  const gridPoint = (id: number): PointXYZ => ({
    x: input.bounds.minX + (id % width) * step, y: input.cruiseAltitudeM,
    z: input.bounds.minZ + Math.floor(id / width) * step,
  });
  const startId = -1;
  const nodePoint = (id: number): PointXYZ => id === startId ? startCruise : gridPoint(id);
  const nearCells = (point: PointXYZ): number[] => {
    const centerX = Math.round((point.x - input.bounds.minX) / step);
    const centerZ = Math.round((point.z - input.bounds.minZ) / step);
    const candidates: number[] = [];
    for (let dz = -2; dz <= 2; dz++) for (let dx = -2; dx <= 2; dx++) {
      const x = centerX + dx, z = centerZ + dz;
      if (x >= 0 && x < width && z >= 0 && z < depth) candidates.push(z * width + x);
    }
    return candidates.sort((a, b) => {
      const diff = distance(gridPoint(a), point) - distance(gridPoint(b), point);
      return Math.abs(diff) > EPS ? diff : a - b;
    });
  };
  const startNeighbors = nearCells(startCruise);
  const goalNeighbors = new Set(nearCells(goalCruise));
  const startTime = input.departureAtS + ascendS;
  const g = new Map<number, number>([[startId, 0]]);
  const parent = new Map<number, number>();
  const open = new Set<number>([startId]);
  const closed = new Set<number>();
  const heuristic = (id: number): number => distance(nodePoint(id), goalCruise);
  const clear = (from: PointXYZ, to: PointXYZ, startS: number): boolean => {
    const metres = distance(from, to);
    if (metres <= EPS) return true;
    const endS = startS + metres / input.speedMps;
    if (!Number.isFinite(endS) || endS <= startS) return false;
    return validateFlightSegment(from, to, startS, endS, input.obstacles,
      input.facilities, input.noFlyZones, input.bodySizeM, input.landingPads).length === 0;
  };
  let visited = 0;
  while (open.size) {
    if (visited >= budget) return failure("search_budget_exhausted", "航线搜索达到节点上限", visited);
    const current = [...open].sort((a, b) => {
      const fA = g.get(a)! + heuristic(a), fB = g.get(b)! + heuristic(b);
      return Math.abs(fA - fB) > EPS ? fA - fB : a - b;
    })[0]!;
    open.delete(current); closed.add(current); visited++;
    const point = nodePoint(current);
    const arrival = startTime + g.get(current)! / input.speedMps;
    if (current !== startId && goalNeighbors.has(current)
        && clear(point, goalCruise, arrival)) {
      const goalArrival = arrival + distance(point, goalCruise) / input.speedMps;
      if (same(goalCruise, input.goal) || clear(goalCruise, input.goal, goalArrival)) {
        const cruisePath: PointXYZ[] = [goalCruise];
        let cursor = current;
        while (cursor !== startId) {
          cruisePath.push(nodePoint(cursor));
          const previous = parent.get(cursor);
          if (previous === undefined) return failure("no_route", "航线搜索父节点缺失", visited);
          cursor = previous;
        }
        cruisePath.push(startCruise);
        cruisePath.reverse();
        const result = assemble(input, [input.start, ...cruisePath, input.goal], visited);
        if (result.ok) return result;
      }
    }
    const neighbors: number[] = current === startId ? startNeighbors : (() => {
      const x = current % width, z = Math.floor(current / width);
      const values: number[] = [];
      for (const [dx, dz] of DIRECTIONS) {
        const nx = x + dx, nz = z + dz;
        if (nx >= 0 && nx < width && nz >= 0 && nz < depth) values.push(nz * width + nx);
      }
      return values;
    })();
    for (const neighbor of neighbors) {
      if (closed.has(neighbor)) continue;
      const nextPoint = gridPoint(neighbor);
      const metres = distance(point, nextPoint);
      if (metres <= EPS || !clear(point, nextPoint, arrival)) continue;
      const nextG = g.get(current)! + metres;
      if (nextG + EPS >= (g.get(neighbor) ?? Infinity)) continue;
      g.set(neighbor, nextG); parent.set(neighbor, current); open.add(neighbor);
    }
  }
  return failure("no_route", "当前高度与时间窗内没有通过碰撞校验的航线", visited);
}
