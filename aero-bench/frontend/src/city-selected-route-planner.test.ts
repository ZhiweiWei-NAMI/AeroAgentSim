import { describe, expect, it } from "vitest";
import { validateFlightSegment, type AirspacePolygon, type PlacementBox } from "./city-workspace-geometry";
import { planSelectedCityRoute, type SelectedRouteInput } from "./city-selected-route-planner";

const base: SelectedRouteInput = {
  start: { x: 0, y: 1, z: 0 }, goal: { x: 20, y: 1, z: 0 },
  bounds: { minX: -5, maxX: 25, minZ: -10, maxZ: 10 },
  cruiseAltitudeM: 10, speedMps: 2, departureAtS: 0,
  bodySizeM: { x: 0.8, y: 0.4, z: 0.8 },
  obstacles: [], facilities: [], noFlyZones: [], landingPads: [],
  gridStepM: 2, maxVisitedNodes: 1000,
};
const building: PlacementBox = {
  id: "building-1", x: 10, z: 0, widthM: 4, depthM: 4, heightM: 15, rotationDeg: 0,
};
function zone(zMin: number, zMax: number, startsAtS = 0): AirspacePolygon {
  return { id: "nfz", name: "test", polygon: [
    { x: 8, z: zMin }, { x: 12, z: zMin }, { x: 12, z: zMax }, { x: 8, z: zMax },
  ], floorM: 0, ceilingM: 20, startsAtS, endsAtS: null,
    source: { kind: "manual", label: "test" } };
}
function expectValidRoute(input: SelectedRouteInput): ReturnType<typeof planSelectedCityRoute> {
  const route = planSelectedCityRoute(input);
  expect(route.ok).toBe(true);
  if (!route.ok) return route;
  expect(route.executable).toBe(false);
  expect(route.waypoints[0]).toEqual(input.start);
  expect(route.waypoints.at(-1)).toEqual(input.goal);
  expect(route.waypointTimesS).toHaveLength(route.waypoints.length);
  for (let i = 1; i < route.waypoints.length; i++) {
    expect(validateFlightSegment(route.waypoints[i - 1]!, route.waypoints[i]!,
      route.waypointTimesS[i - 1]!, route.waypointTimesS[i]!, input.obstacles,
      input.facilities, input.noFlyZones, input.bodySizeM, input.landingPads)).toEqual([]);
  }
  return route;
}

describe("selected-city collision-checked route planning", () => {
  it("returns an exact-endpoint direct route when clear", () => {
    const route = expectValidRoute(base);
    if (route.ok) {
      expect(route.waypoints).toHaveLength(4);
      expect(route.pathLengthM).toBeCloseTo(38);
    }
  });

  it("detours around a building at the declared cruise altitude", () => {
    const input = { ...base, obstacles: [building] };
    const route = expectValidRoute(input);
    if (route.ok) {
      expect(route.waypoints.length).toBeGreaterThan(4);
      expect(route.pathLengthM).toBeGreaterThan(38);
      expect(route.visitedNodes).toBeGreaterThan(0);
    }
  });

  it("honors no-fly activation time rather than ignoring a zone", () => {
    const active = expectValidRoute({ ...base, noFlyZones: [zone(-2, 2)] });
    const future = expectValidRoute({ ...base, noFlyZones: [zone(-2, 2, 100)] });
    if (active.ok && future.ok) {
      expect(active.waypoints.length).toBeGreaterThan(4);
      expect(future.waypoints).toHaveLength(4);
    }
  });

  it("fails explicitly for an impenetrable corridor and a bounded search", () => {
    const input = { ...base, noFlyZones: [zone(-10, 10)], gridStepM: 2 };
    const result = planSelectedCityRoute(input);
    expect(result).toMatchObject({ ok: false, code: "no_route" });
    const capped = planSelectedCityRoute({ ...base, obstacles: [building], maxVisitedNodes: 1 });
    expect(capped).toMatchObject({ ok: false, code: "search_budget_exhausted" });
  });

  it("rejects absent or malformed geometry inputs", () => {
    expect(planSelectedCityRoute({ ...base, speedMps: 0 })).toMatchObject({ ok: false, code: "invalid_input" });
    expect(planSelectedCityRoute({ ...base, bounds: undefined as never }))
      .toMatchObject({ ok: false, code: "invalid_input" });
    expect(planSelectedCityRoute({ ...base, cruiseAltitudeM: 0 }))
      .toMatchObject({ ok: false, code: "invalid_input" });
  });
});
