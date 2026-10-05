/** Bind dispatch route requests to the already verified selected-city collision geometry. */
import { facilityLandingPads, toFacilityVisualSpec } from "./city-facility-models";
import type { CitySelectedScenario } from "./city-selected-scenario";
import type { CitySelectedLogistics } from "./city-selected-logistics-draft";
import { planSelectedCityRoute } from "./city-selected-route-planner";
import type { DispatchRouteResult, RouteEstimator } from "./city-selected-dispatch";
import type { LandingPadClearance, PlacementBox, PointXYZ } from "./city-workspace-geometry";

export interface SelectedDispatchRouteContext {
  readonly scenario: CitySelectedScenario;
  readonly logistics: CitySelectedLogistics;
  /** Building boxes returned by inspectCitySelectedPlacement for this scenario. */
  readonly buildings: readonly PlacementBox[];
  /** Measured GLB street assets from the selected presentation. */
  readonly staticObstacles: readonly PlacementBox[];
}

function padFor(facility: CitySelectedScenario["facilities"][number], padIndex: number): LandingPadClearance {
  const pad = facilityLandingPads(toFacilityVisualSpec(facility))[padIndex];
  if (pad === undefined) throw new Error(`设施 ${facility.id} 没有起降面索引 ${padIndex}`);
  const angle = facility.rotationDeg * Math.PI / 180;
  const cosine = Math.cos(angle), sine = Math.sin(angle);
  return { facilityId: facility.id,
    x: facility.position.x + cosine * pad.x + sine * pad.z,
    z: facility.position.z - sine * pad.x + cosine * pad.z,
    widthM: pad.widthM, depthM: pad.depthM,
    rotationDeg: facility.rotationDeg, padY: pad.y + (facility.supportHeightM ?? 0) };
}

export function createSelectedDispatchRouteEstimator(context: SelectedDispatchRouteContext): RouteEstimator {
  const { scenario, logistics } = context;
  const parameters = logistics.algorithms.parameters;
  const altitude = parameters.cruiseAltitudeM;
  const supported = new Set(["cruiseAltitudeM", "gridStepM", "maxVisitedNodes"]);
  const unsupported = Object.keys(parameters).filter(key => !supported.has(key));
  const facilities = new Map(scenario.facilities.map(facility => [facility.id, facility]));
  const pads = new Map(scenario.facilities.map(facility =>
    [facility.id, facilityLandingPads(toFacilityVisualSpec(facility)).map((_, index) => padFor(facility, index))]));
  const bounds = scenario.selectedScene.selection.bounds_enu_m;
  const collisionBounds = { minX: bounds.min_east_m, maxX: bounds.max_east_m,
    minZ: -bounds.max_north_m, maxZ: -bounds.min_north_m };
  const obstacles = [...context.buildings, ...context.staticObstacles];
  const noFlyZones = scenario.noFlyZones.map(zone => ({ ...zone, source: {
    kind: zone.source.kind, label: zone.source.label,
    uri: zone.source.uri === null ? undefined : zone.source.uri,
  } }));
  return (request): DispatchRouteResult => {
    if (unsupported.length) return { ok: false, code: "policy_gap",
      message: `内置航路算法不支持这些参数：${unsupported.join("、")}` };
    for (const key of ["gridStepM", "maxVisitedNodes"] as const) {
      if (parameters[key] !== undefined && typeof parameters[key] !== "number") {
        return { ok: false, code: "policy_gap", message: `${key} 必须是数值` };
      }
    }
    if (request.fromFacilityId === request.toFacilityId && request.fromPadIndex !== request.toPadIndex) {
      return { ok: false, code: "geometry_unverified",
        message: `同设施 ${request.fromFacilityId} 内起降位 ${request.fromPadIndex}→`
          + `${request.toPadIndex} 不允许零距瞬移，航路必须换起降位` };
    }
    if (typeof altitude !== "number" || !Number.isFinite(altitude) || altitude <= 0) {
      return { ok: false, code: "policy_gap", message: "请在算法参数 JSON 中声明正数 cruiseAltitudeM（米）" };
    }
    const from = facilities.get(request.fromFacilityId), to = facilities.get(request.toFacilityId);
    const startPads = pads.get(request.fromFacilityId), goalPads = pads.get(request.toFacilityId);
    const startPad = startPads?.[request.fromPadIndex];
    const goalPad = goalPads?.[request.toPadIndex];
    if (from === undefined || to === undefined) {
      return { ok: false, code: "geometry_unverified", message: "航路设施缺失" };
    }
    if (startPad === undefined || goalPad === undefined) {
      return { ok: false, code: "geometry_unverified",
        message: `航路起降面缺失（${request.fromFacilityId}#${request.fromPadIndex}`
          + `→${request.toFacilityId}#${request.toPadIndex}）` };
    }
    const halfHeight = request.profile.aircraftBody.yM / 2;
    const start: PointXYZ = { x: startPad.x, y: startPad.padY + halfHeight, z: startPad.z };
    const goal: PointXYZ = { x: goalPad.x, y: goalPad.padY + halfHeight, z: goalPad.z };
    const result = planSelectedCityRoute({ start, goal, bounds: collisionBounds,
      cruiseAltitudeM: altitude, speedMps: request.profile.cruiseSpeedMps,
      departureAtS: request.departureAtS,
      bodySizeM: { x: request.profile.aircraftBody.xM, y: request.profile.aircraftBody.yM,
        z: request.profile.aircraftBody.zM },
      obstacles, facilities: scenario.facilities, noFlyZones,
      landingPads: [...pads.values()].flatMap(list => list),
      gridStepM: parameters.gridStepM as number | undefined,
      maxVisitedNodes: parameters.maxVisitedNodes as number | undefined });
    if (!result.ok) return { ok: false,
      code: result.code === "invalid_input" ? "geometry_unverified" : "no_route",
      message: `${result.message}（已检查 ${result.visitedNodes} 个节点）` };
    const segments = result.waypoints.slice(1).map((point, index) => {
      const previous = result.waypoints[index]!;
      return { kind: point.y > previous.y ? "ascent" : point.y < previous.y ? "descent" : "cruise",
        distanceM: Math.hypot(point.x - previous.x, point.y - previous.y, point.z - previous.z),
        estimatedSeconds: result.waypointTimesS[index + 1]! - result.waypointTimesS[index]! };
    });
    return { ok: true, durationS: result.travelTimeS, distanceM: result.pathLengthM, segments };
  };
}
