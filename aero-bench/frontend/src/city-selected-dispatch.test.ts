// @vitest-environment node
import { describe, expect, it } from "vitest";
import { CITY_SELECTED_DRAFT_SCHEMA, type SelectedSceneDraft } from "./city-selected-draft";
import { SHANGHAI_ORIGIN, SHANGHAI_SOURCE_ID, SHANGHAI_SOURCE_SHA256,
  type SceneSelection } from "./city-region-selector";
import { CITY_SELECTED_SCENARIO_SCHEMA, parseCitySelectedScenario,
  type CitySelectedScenario, type SelectedScenarioFacility } from "./city-selected-scenario";
import { CITY_SELECTED_LOGISTICS_SCHEMA, parseCitySelectedLogistics, type CitySelectedLogistics,
  type FleetPerformanceProfile, type LogisticsOrderRequest, type LogisticsAlgorithms } from "./city-selected-logistics-draft";
import { generateSelectedOrderRequests } from "./city-selected-order-generator";
import { CITY_SELECTED_DISPATCH_PLAN_SCHEMA, planSelectedDispatch,
  type DispatchPlan, type DispatchPlanInput, type DispatchRouteResult, type DispatchUnitState,
  type RouteEstimator } from "./city-selected-dispatch";
import { createSelectedDispatchRouteEstimator } from "./city-selected-dispatch-route";
import { facilityLandingPads, toFacilityVisualSpec } from "./city-facility-models";

const selection: SceneSelection = {
  schema_version: "aero-bench.scene-selection/v1",
  source_id: SHANGHAI_SOURCE_ID,
  source_sha256: SHANGHAI_SOURCE_SHA256,
  origin: SHANGHAI_ORIGIN,
  bounds_enu_m: { min_east_m: 300, max_east_m: 780, min_north_m: 410, max_north_m: 700 },
};
// Published by the real authoring service for the selection above.
const selectionSha = "7d5904868d1f437c377574e267962a7152193fb38b8e1d80b89f6b822abcb09c";
const draft: SelectedSceneDraft = {
  purpose: "selected-scene-authoring",
  schema_version: CITY_SELECTED_DRAFT_SCHEMA,
  selection,
  selection_sha256: selectionSha,
  job_id: "1".repeat(64),
  source_sha256: SHANGHAI_SOURCE_SHA256,
  pack_manifest_sha256: "2".repeat(64),
  presentation_manifest_sha256: "3".repeat(64),
};
const foreignDraft: SelectedSceneDraft = { ...draft, pack_manifest_sha256: "9".repeat(64) };

const DEFAULT_ALGORITHMS: LogisticsAlgorithms = {
  mode: "centralized", assignment: "nearest_feasible", routing: "grid_astar",
  charging: "reserve_threshold", externalImageRef: null, parameters: {},
};
const DEFAULT_GENERATION = {
  seed: 0, maxOrders: 0, startAtS: 0, endAtS: 3600, cargoMinKg: 0.1, cargoMaxKg: 1, deadlineLeadS: 600,
};

function facility(id: string, kind: SelectedScenarioFacility["kind"], x: number, z: number,
                  chargingPowerW = 0): SelectedScenarioFacility {
  const dims = kind === "vertiport" ? { widthM: 20, depthM: 16 }
    : kind === "hub" ? { widthM: 20, depthM: 16 } : { widthM: 12, depthM: 10 };
  const base = {
    id, name: id, kind, placement: "ground" as const, buildingId: null, supportHeightM: null,
    position: { x, z }, rotationDeg: 0,
    widthM: dims.widthM, depthM: dims.depthM, heightM: 5,
    landing: null, cargo: null, charging: null,
  };
  if (kind === "vertiport") {
    return {
      ...base, landing: { parkingSlots: 3, movementsPerHour: 30 },
      charging: chargingPowerW > 0
        ? { slots: 1, powerW: chargingPowerW, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" as const }
        : null,
    };
  }
  if (kind === "hub") {
    return {
      ...base, landing: { parkingSlots: 2, movementsPerHour: 20 },
      cargo: { storageCapacityKg: 10000, throughputPerHourKg: 10000 },
    };
  }
  return { ...base, charging: { slots: 1, powerW: chargingPowerW, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" as const } };
}

const VP = facility("vp.01", "vertiport", 0, 0);
const HUB_A = facility("hub.a", "hub", 1000, 0);
const HUB_B = facility("hub.b", "hub", 2000, 0);
const CHG = facility("chg.01", "charger", 500, 0, 1000);

const FLEET_BIG = {
  id: "uav.big", assetId: "model:holybro-x500", count: 3, homeFacilityId: "vp.01",
  batteryWh: 100, reserveRatio: 0.2, maxPayloadKg: 5,
};
const FLEET_SMALL = {
  id: "uav.small", assetId: "model:holybro-x500", count: 3, homeFacilityId: "vp.01",
  batteryWh: 100, reserveRatio: 0.2, maxPayloadKg: 1,
};

function profileFor(fleetEntryId: string): FleetPerformanceProfile {
  return {
    fleetEntryId, sourceLabel: "operator", provenance: "declared",
    aircraftBody: { xM: 1.2, yM: 0.5, zM: 1.2 },
    cruiseSpeedMps: 10, cruisePowerW: 200, hoverPowerW: 300, chargeEfficiency: 0.9,
  };
}

function makeScenario(selectedDraft = draft, facilities = [VP, HUB_A, HUB_B, CHG],
                      fleet = [FLEET_BIG, FLEET_SMALL]): CitySelectedScenario {
  return parseCitySelectedScenario({
    purpose: "selected-scenario-authoring", schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
    executable: false, selectedScene: selectedDraft,
    facilities, noFlyZones: [], fleet,
    demand: { vehicles: 0, pedestrians: 0, bicycles: 0 },
  });
}

function makeLogistics(scenario: CitySelectedScenario, orders: LogisticsOrderRequest[],
                       profiles: FleetPerformanceProfile[], algorithms = DEFAULT_ALGORITHMS,
                       generation = DEFAULT_GENERATION): CitySelectedLogistics {
  return parseCitySelectedLogistics({
    purpose: "selected-logistics-authoring", schema_version: CITY_SELECTED_LOGISTICS_SCHEMA,
    executable: false, selectedScene: scenario.selectedScene, orders, performanceProfiles: profiles,
    algorithms, orderGeneration: generation,
  }, scenario);
}

function estimatorFor(facilities: readonly SelectedScenarioFacility[],
                      blockedInto?: ReadonlySet<string>): RouteEstimator {
  const byId = new Map(facilities.map(facilityItem => [facilityItem.id, facilityItem]));
  return (request): DispatchRouteResult => {
    if (blockedInto?.has(request.toFacilityId)) {
      return { ok: false, code: "no_route", message: `${request.toFacilityId} 不可达` };
    }
    const from = byId.get(request.fromFacilityId)!;
    const to = byId.get(request.toFacilityId)!;
    const distance = Math.hypot(to.position.x - from.position.x, to.position.z - from.position.z);
    const duration = distance / request.profile.cruiseSpeedMps;
    return { ok: true, durationS: duration, distanceM: distance,
      segments: [{ kind: "straight", distanceM: distance, estimatedSeconds: duration }] };
  };
}

function unit(unitId: string, fleetEntryId: string, atFacilityId: string, batteryWh: number,
              availableAtS = 0): DispatchUnitState {
  return { unitId, fleetEntryId, atFacilityId, batteryWh, availableAtS };
}

function planInput(scenario: CitySelectedScenario, unitStates: readonly DispatchUnitState[],
                   blockedInto?: ReadonlySet<string>): Omit<DispatchPlanInput, "logistics"> {
  return { scenario, units: unitStates, routeEstimator: estimatorFor(scenario.facilities, blockedInto) };
}

const order = (id: string, sourceFacilityId: string, destinationFacilityId: string,
               releaseAtS: number, deliverByS: number, cargoKg = 1): LogisticsOrderRequest =>
  ({ id, sourceFacilityId, destinationFacilityId, hubHandoffFacilityId: null, cargoKg, releaseAtS, deliverByS });

describe("city selected dispatch planning engine", () => {
  it("applies routing parameters and rejects unsupported built-in configuration", () => {
    const scenario = makeScenario(draft, [facility("vp.01", "vertiport", 400, -550),
      facility("hub.a", "hub", 600, -550)]);
    const logistics = makeLogistics(scenario, [], [profileFor("uav.big")], {
      ...DEFAULT_ALGORITHMS, parameters: { cruiseAltitudeM: 30, gridStepM: 4, maxVisitedNodes: 1 },
    });
    const obstacle = { id: "wall", x: 500, z: -550, widthM: 10, depthM: 20, heightM: 60, rotationDeg: 0 };
    const request = { profile: profileFor("uav.big"), fromFacilityId: "vp.01", toFacilityId: "hub.a",
      fromPadIndex: 0, toPadIndex: 0, departureAtS: 0 };
    const estimate = (parameters: typeof logistics.algorithms.parameters) => createSelectedDispatchRouteEstimator({
      scenario, logistics: { ...logistics, algorithms: { ...logistics.algorithms, parameters } },
      buildings: [obstacle], staticObstacles: [],
    })(request);
    expect(estimate(logistics.algorithms.parameters)).toMatchObject({ ok: false, code: "no_route" });
    expect(estimate({ ...logistics.algorithms.parameters, maxVisitedNodes: 6000 }).ok).toBe(true);
    expect(estimate({ cruiseAltitudeM: 30, gridStepM: "4" })).toMatchObject({ ok: false, code: "policy_gap" });
    expect(estimate({ cruiseAltitudeM: 30, inventedOption: true })).toMatchObject({ ok: false, code: "policy_gap" });
  });
  it("returns an immutable non-executable authoring plan with provenance and route references", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario,
      [order("o1", "hub.a", "hub.b", 0, 10000)], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({
      scenario, logistics, units: [unit("uav.01", "uav.big", "vp.01", 100)],
      routeEstimator: estimatorFor(scenario.facilities),
    });
    expect(plan.schema_version).toBe(CITY_SELECTED_DISPATCH_PLAN_SCHEMA);
    expect(plan.executable).toBe(false);
    expect(plan.purpose).toBe("selected-logistics-dispatch-plan");
    expect(plan.provenance.planner).toBe("city-selected-dispatch");
    expect(plan.provenance.mode).toBe("centralized");
    expect(plan.provenance.boundSelectedScene.job_id).toBe(draft.job_id);
    expect(plan.provenance.declaredUnits).toEqual([{ unitId: "uav.01", fleetEntryId: "uav.big",
      atFacilityId: "vp.01", availableAtS: 0, batteryWh: 100 }]);
    expect(plan.acceptedAssignments[0]!.routeToPickup.segments).toHaveLength(1);
    expect(plan.routeReferences[plan.acceptedAssignments[0]!.routeToPickup.reference]).toBeDefined();
    expect(Object.isFrozen(plan)).toBe(true);
    expect(Object.isFrozen(plan.acceptedAssignments)).toBe(true);
    expect(Object.isFrozen(plan.unitFinalStates)).toBe(true);
    const again = planSelectedDispatch({
      scenario, logistics, units: [unit("uav.01", "uav.big", "vp.01", 100)],
      routeEstimator: estimatorFor(scenario.facilities),
    });
    expect(again).toEqual(plan);
  });

  it("sequences two orders onto one unit and keeps its state current", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario, [
      order("o1", "hub.a", "hub.b", 0, 10000),
      order("o2", "hub.b", "hub.a", 400, 10000),
    ], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({ ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)]),
      logistics });

    expect(plan.ordersConsidered).toBe(2);
    expect(plan.acceptedAssignments).toHaveLength(2);
    expect(plan.unassignedOrders).toHaveLength(0);
    const [first, second] = plan.acceptedAssignments;
    expect(first!.unitId).toBe("uav.01");
    expect(second!.unitId).toBe("uav.01");
    // o1: vp -> hub.a (100s) -> hub.b (100s), plus the declared 180s handling
    // dwell (3600/20 mov/h) for the intermediate pickup at hub.a; the same drone
    // cannot double-book.
    expect(second!.departureAtS).toBeGreaterThanOrEqual(first!.arrivalAtS);
    expect(first!.arrivalAtS).toBeCloseTo(380, 6);
    expect(first!.batteryAtPickupDepartureWh).toBeCloseTo(100 - 300 * 100 / 3600, 6);
    expect(first!.batteryAtPickupDepartureWh - first!.batteryAtArrivalWh)
      .toBeCloseTo(300 * first!.routeFromPickupToDestination.estimatedDurationS / 3600, 6);
    // o2 picks up at hub.b where o1 landed, then flies hub.b -> hub.a.
    expect(plan.unitFinalStates[0]!.atFacilityId).toBe("hub.a");
    expect(plan.unitFinalStates[0]!.availableAtS).toBeCloseTo(500, 6);
    expect(plan.unitFinalStates[0]!.batteryWh).toBeCloseTo(75, 6);
  });

  it("inserts a reachable charging stop when the direct mission breaches reserve", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario,
      [order("low", "hub.a", "hub.b", 0, 100000)], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({ ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 25)]),
      logistics });

    expect(plan.acceptedAssignments).toHaveLength(1);
    const assignment = plan.acceptedAssignments[0]!;
    expect(assignment.chargingStops).toHaveLength(1);
    const stop = assignment.chargingStops[0]!;
    expect(stop.facilityId).toBe("chg.01");
    expect(stop.chargingPowerW).toBe(1000);
    expect(stop.routeToCharger.toFacilityId).toBe("chg.01");
    expect(stop.chargedWh).toBeGreaterThan(0);
    expect(stop.chargedWh).toBeCloseTo(79.1667, 3);
    expect(stop.chargeSeconds).toBeCloseTo(316.6667, 3);
    expect(stop.batteryAfterChargeWh).toBeCloseTo(100, 6);
    expect(assignment.routeToPickup.departureAtS).toBeCloseTo(stop.departureAtS, 6);
    expect(assignment.batteryAtArrivalWh).toBeCloseTo(87.5, 6);
    expect(plan.plannedChargingStops).toHaveLength(1);
    expect(plan.routeReferences[stop.routeToCharger.reference]).toBe(stop.routeToCharger);
  });

  it("plans the post-charge legs at their actual time", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario,
      [order("timed", "hub.a", "hub.b", 0, 100000)], [profileFor("uav.big")]);
    const base = estimatorFor(scenario.facilities);
    const chargerDepartures: number[] = [];
    const routeEstimator: RouteEstimator = request => {
      if (request.fromFacilityId === "chg.01" && request.toFacilityId === "hub.a") {
        chargerDepartures.push(request.departureAtS);
        if (request.departureAtS < 300) {
          return { ok: false, code: "policy_gap", message: "空域尚未开放" };
        }
      }
      return base(request);
    };
    const plan = planSelectedDispatch({ scenario, logistics,
      units: [unit("uav.01", "uav.big", "vp.01", 25)], routeEstimator });
    expect(plan.acceptedAssignments).toHaveLength(1);
    expect(chargerDepartures).toEqual([plan.acceptedAssignments[0]!.chargingStops[0]!.departureAtS]);
    expect(chargerDepartures[0]).toBeGreaterThan(300);
  });

  it("allows an already parked drone below reserve to charge without a flight", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario,
      [order("parked", "hub.a", "hub.b", 0, 100000)], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({ ...planInput(scenario, [unit("uav.01", "uav.big", "chg.01", 0)]), logistics });
    expect(plan.acceptedAssignments).toHaveLength(1);
    const stop = plan.plannedChargingStops[0]!;
    expect(stop.routeToCharger.estimatedDistanceM).toBe(0);
    expect(stop.batteryAtArrivalWh).toBe(0);
    expect(stop.chargedWh).toBe(100);
    expect(stop.chargeSeconds).toBeCloseTo(400);
    const stranded = planSelectedDispatch({ ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 0)]), logistics });
    expect(stranded.acceptedAssignments).toHaveLength(0);
  });

  it("charges on the pickup source hub and keeps the direct delivery as the only post-pickup route", () => {
    const hubA = { ...facility("hub.a", "hub", 1000, 0),
      charging: { slots: 1, powerW: 1000, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" as const } };
    const hubB = facility("hub.b", "hub", 2000, 0);
    const scenario = makeScenario(draft, [VP, hubA, hubB]);
    const logistics = makeLogistics(scenario,
      [order("src-charged", "hub.a", "hub.b", 0, 100000)], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "hub.a", 21)]), logistics });
    expect(plan.acceptedAssignments).toHaveLength(1);
    expect(plan.unassignedOrders).toHaveLength(0);
    const assignment = plan.acceptedAssignments[0]!;
    expect(assignment.chargingStops).toHaveLength(1);
    const stop = assignment.chargingStops[0]!;
    expect(stop.facilityId).toBe("hub.a");
    expect(stop.batteryAtArrivalWh).toBeCloseTo(21, 6);
    expect(stop.chargedWh).toBeCloseTo(79, 6);
    expect(stop.chargeSeconds).toBeCloseTo(316, 6);
    // The pickup collapses onto the charger pad: a degenerate same-source
    // same-pad to-pickup leg departs exactly when charging finishes.
    expect(assignment.routeToPickup.fromFacilityId).toBe("hub.a");
    expect(assignment.routeToPickup.toFacilityId).toBe("hub.a");
    expect(assignment.routeToPickup.fromPadIndex).toBe(0);
    expect(assignment.routeToPickup.toPadIndex).toBe(0);
    expect(assignment.routeToPickup.departureAtS).toBeCloseTo(stop.departureAtS, 6);
    expect(assignment.routeToPickup.estimatedDurationS).toBe(0);
    expect(assignment.routeToPickup.estimatedDistanceM).toBe(0);
    expect(assignment.pickupAtS).toBeCloseTo(stop.departureAtS, 6);
    // A single nonempty delivery leg carries the whole real flight after charging.
    expect(assignment.routeLegs).toHaveLength(1);
    expect(assignment.routeFromPickupToDestination.fromFacilityId).toBe("hub.a");
    expect(assignment.routeFromPickupToDestination.toFacilityId).toBe("hub.b");
    expect(assignment.routeFromPickupToDestination.estimatedDistanceM).toBeCloseTo(1000, 6);
    expect(assignment.routeFromPickupToDestination.estimatedDurationS).toBeCloseTo(100, 6);
    expect(assignment.arrivalAtS).toBeCloseTo(stop.departureAtS
      + assignment.routeFromPickupToDestination.estimatedDurationS, 6);
    expect(assignment.batteryAtPickupDepartureWh).toBeCloseTo(100, 6);
    expect(assignment.batteryAtArrivalWh).toBeCloseTo(100 - 300 * 100 / 3600, 6);
  });

  it("preserves full post-pickup legs when charging at a source vertiport with a hub handoff", () => {
    const source = facility("vp.01", "vertiport", 0, 0, 1000);
    const hubA = facility("hub.a", "hub", 1000, 0);
    const hubB = facility("hub.b", "hub", 2000, 0);
    const scenario = makeScenario(draft, [source, hubA, hubB]);
    const logistics = makeLogistics(scenario,
      [{ ...order("src-handoff", "vp.01", "hub.b", 0, 100000), hubHandoffFacilityId: "hub.a" }],
      [profileFor("uav.big")]);
    const plan = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 22)]), logistics });
    expect(plan.acceptedAssignments).toHaveLength(1);
    expect(plan.unassignedOrders).toHaveLength(0);
    const assignment = plan.acceptedAssignments[0]!;
    expect(assignment.chargingStops).toHaveLength(1);
    const stop = assignment.chargingStops[0]!;
    expect(stop.facilityId).toBe("vp.01");
    expect(stop.batteryAtArrivalWh).toBeCloseTo(22, 6);
    expect(stop.chargedWh).toBeCloseTo(78, 6);
    expect(stop.chargeSeconds).toBeCloseTo(312, 6);
    // Source identity is the split condition: the pickup is degenerate on the
    // source pad at the charger's departure.
    expect(assignment.routeToPickup.fromFacilityId).toBe("vp.01");
    expect(assignment.routeToPickup.toFacilityId).toBe("vp.01");
    expect(assignment.routeToPickup.fromPadIndex).toBe(0);
    expect(assignment.routeToPickup.toPadIndex).toBe(0);
    expect(assignment.routeToPickup.departureAtS).toBeCloseTo(stop.departureAtS, 6);
    expect(assignment.routeToPickup.estimatedDurationS).toBe(0);
    expect(assignment.pickupAtS).toBeCloseTo(stop.departureAtS, 6);
    // Both post-charge legs survive intact: source → hub handoff → destination.
    expect(assignment.routeLegs).toHaveLength(2);
    expect(assignment.routeLegs[0]!.fromFacilityId).toBe("vp.01");
    expect(assignment.routeLegs[0]!.toFacilityId).toBe("hub.a");
    expect(assignment.routeLegs[1]!.fromFacilityId).toBe("hub.a");
    expect(assignment.routeLegs[1]!.toFacilityId).toBe("hub.b");
    expect(assignment.routeFromPickupToDestination.fromFacilityId).toBe("vp.01");
    expect(assignment.routeFromPickupToDestination.toFacilityId).toBe("hub.b");
    expect(assignment.routeFromPickupToDestination.estimatedDistanceM).toBeCloseTo(2000, 6);
    expect(assignment.routeFromPickupToDestination.estimatedDurationS).toBeCloseTo(200, 6);
    expect(assignment.arrivalAtS).toBeCloseTo(stop.departureAtS
      + assignment.routeFromPickupToDestination.estimatedDurationS + 180, 6);
    expect(assignment.batteryAtPickupDepartureWh).toBeCloseTo(100, 6);
    expect(assignment.batteryAtArrivalWh).toBeCloseTo(100 - 300 * 200 / 3600, 6);
  });

  it("charges at the source charger even when the unit must fly there first", () => {
    const hubA = facility("hub.a", "hub", 0, 0);
    const source = facility("vp.01", "vertiport", 1000, 0, 1000);
    const hubB = facility("hub.b", "hub", 2000, 0);
    const scenario = makeScenario(draft, [hubA, source, hubB]);
    const logistics = makeLogistics(scenario,
      [order("arrive-src", "vp.01", "hub.b", 0, 100000)], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "hub.a", 30)]), logistics });
    expect(plan.acceptedAssignments).toHaveLength(1);
    expect(plan.unassignedOrders).toHaveLength(0);
    const assignment = plan.acceptedAssignments[0]!;
    expect(assignment.chargingStops).toHaveLength(1);
    const stop = assignment.chargingStops[0]!;
    expect(stop.facilityId).toBe("vp.01");
    expect(stop.routeToCharger.fromFacilityId).toBe("hub.a");
    expect(stop.routeToCharger.toFacilityId).toBe("vp.01");
    // Source identity still collapses the pickup onto the charger pad.
    expect(assignment.routeToPickup.fromFacilityId).toBe("vp.01");
    expect(assignment.routeToPickup.toFacilityId).toBe("vp.01");
    expect(assignment.routeToPickup.fromPadIndex).toBe(0);
    expect(assignment.routeToPickup.toPadIndex).toBe(0);
    expect(assignment.routeToPickup.departureAtS).toBeCloseTo(stop.departureAtS, 6);
    expect(assignment.routeToPickup.estimatedDurationS).toBe(0);
    expect(assignment.pickupAtS).toBeCloseTo(stop.departureAtS, 6);
    expect(assignment.routeLegs).toHaveLength(1);
    expect(assignment.routeLegs[0]!.fromFacilityId).toBe("vp.01");
    expect(assignment.routeLegs[0]!.toFacilityId).toBe("hub.b");
    expect(assignment.routeFromPickupToDestination.fromFacilityId).toBe("vp.01");
    expect(assignment.routeFromPickupToDestination.toFacilityId).toBe("hub.b");
    expect(assignment.arrivalAtS).toBeCloseTo(stop.departureAtS
      + assignment.routeFromPickupToDestination.estimatedDurationS, 6);
    expect(assignment.batteryAtArrivalWh).toBeCloseTo(100 - 300 * 100 / 3600, 6);
  });

  it("does not reserve a single charger for two simultaneous missions", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario,
      [order("a", "hub.a", "hub.b", 0, 100000), order("b", "hub.a", "hub.b", 0, 100000)],
      [profileFor("uav.big")]);
    const units = [unit("uav.01", "uav.big", "vp.01", 25), unit("uav.02", "uav.big", "vp.01", 25)];
    const plan = planSelectedDispatch({ ...planInput(scenario, units), logistics });
    expect(plan.acceptedAssignments).toHaveLength(2);
    expect(plan.plannedChargingStops).toHaveLength(1);
    expect(plan.acceptedAssignments.map(assignment => assignment.unitId)).toEqual(["uav.01", "uav.01"]);
    expect(plan.acceptedAssignments[1]!.departureAtS).toBeGreaterThanOrEqual(plan.acceptedAssignments[0]!.arrivalAtS);
    const larger = { ...scenario, facilities: scenario.facilities.map(site =>
      site.id === "chg.01" ? { ...site, charging: { ...site.charging!, slots: 2 } } : site) };
    const simultaneous = planSelectedDispatch({ ...planInput(larger, units), logistics });
    expect(simultaneous.plannedChargingStops).toHaveLength(2);
  });

  it("keeps route, deadline and missing-charger failures explicitly unassigned", () => {
    const scenario = makeScenario();
    const profiles = [profileFor("uav.big")];

    const noRoute = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)], new Set(["hub.b"])),
      logistics: makeLogistics(scenario, [order("r", "hub.a", "hub.b", 0, 10000)], profiles),
    });
    expect(noRoute.acceptedAssignments).toHaveLength(0);
    expect(noRoute.unassignedOrders[0]!.primaryReason).toBe("no_route");
    expect(noRoute.unassignedOrders[0]!.perUnitFailures[0]!.reason).toBe("route_delivery");

    const tooLate = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)]),
      logistics: makeLogistics(scenario, [order("d", "hub.a", "hub.b", 0, 5)], profiles),
    });
    expect(tooLate.acceptedAssignments).toHaveLength(0);
    expect(tooLate.unassignedOrders[0]!.primaryReason).toBe("deadline_passed");

    // No positive-power charger anywhere: the low-battery mission is unassigned.
    const noChargerScenario = makeScenario(draft, [VP, HUB_A, HUB_B]);
    const noCharger = planSelectedDispatch({
      ...planInput(noChargerScenario, [unit("uav.01", "uav.big", "vp.01", 25)]),
      logistics: makeLogistics(noChargerScenario, [order("c", "hub.a", "hub.b", 0, 100000)], profiles),
    });
    expect(noCharger.acceptedAssignments).toHaveLength(0);
    expect(noCharger.unassignedOrders[0]!.primaryReason).toBe("no_charging_available");
  });

  it("fails explicitly on over-payload cargo and on a unit without a profile", () => {
    const scenario = makeScenario();
    const heavyLogistics = makeLogistics(scenario,
      [order("heavy", "hub.a", "hub.b", 0, 10000, 3)],
      [profileFor("uav.big"), profileFor("uav.small")]);
    const heavy = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.s1", "uav.small", "vp.01", 100)]), logistics: heavyLogistics,
    });
    expect(heavy.acceptedAssignments).toHaveLength(0);
    expect(heavy.unassignedOrders[0]!.primaryReason).toBe("no_cargo_capacity");
    expect(heavy.unassignedOrders[0]!.perUnitFailures[0]!.reason).toBe("no_cargo_capacity");

    const withoutProfile = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)]),
      logistics: makeLogistics(scenario, [order("np", "hub.a", "hub.b", 0, 10000)], []),
    });
    expect(withoutProfile.acceptedAssignments).toHaveLength(0);
    expect(withoutProfile.unassignedOrders[0]!.primaryReason).toBe("missing_profile");
    expect(withoutProfile.unassignedOrders[0]!.perUnitFailures[0]!.reason).toBe("missing_profile");
  });

  it("centralized nearest_feasible picks earliest delivery then a deterministic unit tie-break", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario,
      [order("o1", "hub.a", "hub.b", 0, 10000)], [profileFor("uav.big")]);

    // uav.near starts at the pickup hub and therefore delivers earlier.
    const closer = planSelectedDispatch({
      ...planInput(scenario, [
        unit("uav.far", "uav.big", "vp.01", 100),
        unit("uav.near", "uav.big", "hub.a", 100),
      ]), logistics,
    });
    expect(closer.acceptedAssignments).toHaveLength(1);
    expect(closer.acceptedAssignments[0]!.unitId).toBe("uav.near");
    expect(closer.acceptedAssignments[0]!.arrivalAtS).toBeCloseTo(100, 6);

    // Identical state and identical arrival: the lower unit id wins the tie.
    const tie = planSelectedDispatch({
      ...planInput(scenario, [
        unit("uav.b", "uav.big", "vp.01", 100),
        unit("uav.a", "uav.big", "vp.01", 100),
      ]), logistics,
    });
    expect(tie.acceptedAssignments[0]!.unitId).toBe("uav.a");
  });

  it("distributed sealed_bid records per-unit bids and picks the deterministic winner", () => {
    const scenario = makeScenario();
    const algorithms: LogisticsAlgorithms = {
      mode: "distributed", assignment: "sealed_bid", routing: "grid_astar",
      charging: "reserve_threshold", externalImageRef: null, parameters: {},
    };
    const logistics = makeLogistics(scenario,
      [order("o1", "hub.a", "hub.b", 0, 10000)], [profileFor("uav.big")], algorithms);
    const plan = planSelectedDispatch({
      ...planInput(scenario, [
        unit("uav.b", "uav.big", "vp.01", 100),
        unit("uav.a", "uav.big", "vp.01", 100),
      ]), logistics,
    });

    expect(plan.provenance.assignment).toBe("sealed_bid");
    expect(plan.sealedBids).toHaveLength(2);
    expect(plan.sealedBids.every(bid => bid.orderId === "o1")).toBe(true);
    expect(plan.sealedBids.every(bid => bid.feasible)).toBe(true);
    expect(plan.sealedBids.map(bid => bid.unitId).sort()).toEqual(["uav.a", "uav.b"]);
    // Both bids share the hub.a pickup handling dwell (180s at 20 mov/h):
    // vp -> hub.a 100s + 180s + hub.a -> hub.b 100s.
    expect(plan.sealedBids[0]!.estimatedDeliveryS).toBeCloseTo(380, 6);
    // The deterministic winner is the lower unit id, matching the centralized tie-break.
    expect(plan.acceptedAssignments[0]!.unitId).toBe("uav.a");

    // A bid that cannot reach a route is recorded as infeasible and does not win.
    const infeasible = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.a", "uav.big", "vp.01", 100)], new Set(["hub.b"])),
      logistics: makeLogistics(scenario, [order("o1", "hub.a", "hub.b", 0, 10000)],
        [profileFor("uav.big")], algorithms),
    });
    expect(infeasible.acceptedAssignments).toHaveLength(0);
    expect(infeasible.sealedBids[0]!.feasible).toBe(false);
    expect(infeasible.sealedBids[0]!.reason).toBe("route_delivery");
    expect(infeasible.sealedBids[0]!.orderId).toBe("o1");
    const multiple = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.a", "uav.big", "vp.01", 100)]),
      logistics: makeLogistics(scenario, [order("o1", "hub.a", "hub.b", 0, 10000),
        order("o2", "hub.b", "hub.a", 400, 10000)], [profileFor("uav.big")], algorithms),
    });
    expect(multiple.sealedBids.map(bid => [bid.orderId, bid.unitId]))
      .toEqual([["o1", "uav.a"], ["o2", "uav.a"]]);
  });

  it("accepts caller-supplied deterministic generated requests merged and sorted by release", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario,
      [order("manual.later", "hub.a", "hub.b", 400, 10000)], [profileFor("uav.big")]);
    const generated: LogisticsOrderRequest[] = [order("gen.earlier", "hub.b", "hub.a", 10, 10000)];
    const plan = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)]),
      logistics, generatedOrders: generated,
    });
    expect(plan.ordersConsidered).toBe(2);
    expect(plan.acceptedAssignments.map(item => item.orderId)).toEqual(["gen.earlier", "manual.later"]);
  });

  it("rejects a foreign scenario, duplicate order ids and out-of-capacity battery energy", () => {
    const scenario = makeScenario();
    const foreignScenario = makeScenario(foreignDraft);
    const logistics = makeLogistics(scenario,
      [order("o1", "hub.a", "hub.b", 0, 10000)], [profileFor("uav.big")]);

    expect(() => planSelectedDispatch({
      ...planInput(foreignScenario, [unit("uav.01", "uav.big", "vp.01", 100)]), logistics,
    })).toThrow(/拒绝复用外来订单/);

    const duplicate = { ...logistics, orders: [order("o1", "hub.a", "hub.b", 0, 10000),
      { ...order("o2", "hub.b", "hub.a", 5, 10000), id: "o1" }] } as unknown as CitySelectedLogistics;
    expect(() => planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)]), logistics: duplicate,
    })).toThrow(/订单 ID 重复/);

    expect(() => planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 150)]), logistics,
    })).toThrow(/电池电量/);
  });

  it("rejects external algorithms and unsupported central/distributed combinations", () => {
    const scenario = makeScenario();
    const orders = [order("o1", "hub.a", "hub.b", 0, 10000)];
    const external = makeLogistics(scenario, orders, [profileFor("uav.big")], {
      mode: "centralized", assignment: "external", routing: "grid_astar",
      charging: "reserve_threshold", externalImageRef: `reg/aero-bench@sha256:${"a".repeat(64)}`,
      parameters: {},
    });
    expect(() => planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)]), logistics: external,
    })).toThrow(/正式 Provider/);

    const mixed = makeLogistics(scenario, orders, [profileFor("uav.big")], {
      mode: "centralized", assignment: "sealed_bid", routing: "grid_astar",
      charging: "reserve_threshold", externalImageRef: null, parameters: {},
    });
    expect(() => planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)]), logistics: mixed,
    })).toThrow(/不支持的调度组合/);
  });

  it("generates deterministic, authorable orders from orderGeneration", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario, [], [profileFor("uav.big")], DEFAULT_ALGORITHMS, {
      ...DEFAULT_GENERATION, seed: 7, maxOrders: 6,
    });
    const first = generateSelectedOrderRequests(scenario, logistics.orderGeneration);
    const second = generateSelectedOrderRequests(scenario, logistics.orderGeneration);
    expect(first).toEqual(second);
    expect(first).toHaveLength(6);
    expect(first.every(item => item.sourceFacilityId !== item.destinationFacilityId)).toBe(true);
    // Every generated order must remain authorable against the same bound scenario.
    const revalidated = makeLogistics(scenario, first, [profileFor("uav.big")]);
    expect(revalidated.orders).toHaveLength(6);
  });

  it("never claims execution for a plan even when every order is accepted", () => {
    const scenario = makeScenario();
    const logistics = makeLogistics(scenario, [
      order("o1", "hub.a", "hub.b", 0, 10000),
      order("o2", "hub.b", "hub.a", 50, 10000),
    ], [profileFor("uav.big")]);
    const plan: DispatchPlan = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)]), logistics,
    });
    expect(plan.acceptedAssignments).toHaveLength(2);
    expect(plan.unassignedOrders).toHaveLength(0);
    expect(plan.executable).toBe(false);
    expect(plan.provenance.label).toMatch(/Declares no order dispatch/);
  });

  it("binds two concurrent aircraft to two distinct physical pads through the real route estimator", () => {
    const vp = { ...facility("vp.01", "vertiport", 430, -550),
      landing: { parkingSlots: 2, movementsPerHour: 30 } };
    const hubA = { ...facility("hub.a", "hub", 500, -550),
      landing: { parkingSlots: 2, movementsPerHour: 20 } };
    const hubB = { ...facility("hub.b", "hub", 650, -550),
      landing: { parkingSlots: 2, movementsPerHour: 20 } };
    const scenario = makeScenario(draft, [vp, hubA, hubB]);
    const algorithms = { ...DEFAULT_ALGORITHMS,
      parameters: { cruiseAltitudeM: 30, gridStepM: 4, maxVisitedNodes: 6000 } };
    const logistics = makeLogistics(scenario,
      [order("o1", "hub.a", "hub.b", 0, 10000), order("o2", "hub.a", "hub.b", 0, 10000)],
      [profileFor("uav.big")], algorithms);
    const estimator = createSelectedDispatchRouteEstimator({
      scenario, logistics, buildings: [], staticObstacles: [],
    });
    const plan = planSelectedDispatch({ scenario, logistics,
      units: [unit("uav.01", "uav.big", "vp.01", 100), unit("uav.02", "uav.big", "vp.01", 100)],
      routeEstimator: estimator });

    expect(plan.acceptedAssignments).toHaveLength(2);
    expect(plan.unassignedOrders).toHaveLength(0);
    // The two home pads are distinct: one aircraft departs pad 0, the other pad 1.
    expect(plan.acceptedAssignments.map(assignment => assignment.routeToPickup.fromPadIndex).sort())
      .toEqual([0, 1]);
    // The open-ended first dwell at hub.b forces the concurrent second aircraft
    // onto the other pad: the final legs bind to distinct physical pad indices.
    const finals = plan.acceptedAssignments.map(assignment =>
      assignment.routeLegs[assignment.routeLegs.length - 1]!);
    for (const final of finals) expect(final.toFacilityId).toBe("hub.b");
    expect(finals.map(final => final.toPadIndex).sort()).toEqual([0, 1]);

    // The real estimator must bind those pads physically: a route to hub.b pad 0
    // and one to hub.b pad 1 land at different coordinates, so their lengths differ.
    const pad0Route = estimator({ profile: profileFor("uav.big"), fromFacilityId: "vp.01",
      toFacilityId: "hub.b", fromPadIndex: 0, toPadIndex: 0, departureAtS: 0 });
    const pad1Route = estimator({ profile: profileFor("uav.big"), fromFacilityId: "vp.01",
      toFacilityId: "hub.b", fromPadIndex: 0, toPadIndex: 1, departureAtS: 0 });
    expect(pad0Route.ok).toBe(true);
    expect(pad1Route.ok).toBe(true);
    if (!pad0Route.ok || !pad1Route.ok) {
      throw new Error("A pad-bound route of the real estimator must succeed");
    }
    const pad0 = facilityLandingPads(toFacilityVisualSpec(hubB))[0]!;
    const pad1 = facilityLandingPads(toFacilityVisualSpec(hubB))[1]!;
    const angle = hubB.rotationDeg * Math.PI / 180;
    const x0 = hubB.position.x + Math.cos(angle) * pad0.x + Math.sin(angle) * pad0.z;
    const x1 = hubB.position.x + Math.cos(angle) * pad1.x + Math.sin(angle) * pad1.z;
    expect(Math.abs(x0 - x1)).toBeGreaterThan(4);
    expect(Math.abs(pad0Route.distanceM - pad1Route.distanceM)).toBeGreaterThan(4);
    // The winner's already-bound legs keep the same deterministic pad identities
    // and both aircraft keep an explicit final physical pad.
    expect(plan.unitFinalStates.map(state => state.currentPadIndex).sort()).toEqual([0, 1]);
  });

  it("rejects a second aircraft when the destination has only one physical pad", () => {
    const vp = { ...facility("vp.01", "vertiport", 0, 0),
      landing: { parkingSlots: 2, movementsPerHour: 30 } };
    const hubA = { ...facility("hub.a", "hub", 1000, 0),
      landing: { parkingSlots: 2, movementsPerHour: 20 } };
    const hubB = { ...facility("hub.b", "hub", 2000, 0),
      landing: { parkingSlots: 1, movementsPerHour: 20 } };
    const scenario = makeScenario(draft, [vp, hubA, hubB]);
    const logistics = makeLogistics(scenario,
      [order("o1", "hub.a", "hub.b", 0, 10000), order("o2", "hub.a", "hub.b", 0, 300)],
      [profileFor("uav.big")]);
    const plan = planSelectedDispatch({ ...planInput(scenario,
      [unit("uav.01", "uav.big", "vp.01", 100), unit("uav.02", "uav.big", "vp.01", 100)]), logistics });
    expect(plan.acceptedAssignments).toHaveLength(1);
    expect(plan.acceptedAssignments[0]!.unitId).toBe("uav.01");
    expect(plan.unassignedOrders[0]!.orderId).toBe("o2");
    expect(plan.unassignedOrders[0]!.primaryReason).toBe("pad_capacity");
  });

  it("allows half-open adjacent reuse of a pad exactly at the departure boundary", () => {
    const vp = { ...facility("vp.01", "vertiport", 0, 0),
      landing: { parkingSlots: 1, movementsPerHour: 30 } };
    const hubA = { ...facility("hub.a", "hub", 500, 0),
      landing: { parkingSlots: 2, movementsPerHour: 20 } };
    const hubB = { ...facility("hub.b", "hub", 1000, 0),
      landing: { parkingSlots: 1, movementsPerHour: 20 } };
    const scenario = makeScenario(draft, [vp, hubA, hubB]);
    const logistics = makeLogistics(scenario, [
      order("o1", "hub.a", "hub.b", 0, 10000),
      order("o1b", "hub.b", "hub.a", 0, 10000),
      order("o2", "hub.a", "hub.b", 0, 10000),
    ], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({ ...planInput(scenario,
      [unit("uav.01", "uav.big", "vp.01", 100),
        unit("uav.02", "uav.big", "hub.a", 100, 230)]), logistics });
    expect(plan.acceptedAssignments).toHaveLength(3);
    expect(plan.unassignedOrders).toHaveLength(0);
    // With declared handling (3600/20 = 180s per intermediate contact),
    // uav.01 parks uav.02's destination hub.b pad 0 at t=280: o1 wins by tie at
    // 280 (vp -> hub.a 50s + hub.a handling 180s + hub.a -> hub.b 50s). o1b lets
    // the same unit depart hub.b at exactly t=280, freeing the pad half-open;
    // uav.02 (starting at hub.a at t=230) lands hub.b pad 0 at exactly t=280 —
    // adjacent boundary reuse between two units, no double booking.
    const o1 = plan.acceptedAssignments.find(assignment => assignment.orderId === "o1")!;
    expect(o1.unitId).toBe("uav.01");
    expect(o1.routeLegs[o1.routeLegs.length - 1]!.toFacilityId).toBe("hub.b");
    expect(o1.routeLegs[o1.routeLegs.length - 1]!.toPadIndex).toBe(0);
    const o2 = plan.acceptedAssignments.find(assignment => assignment.orderId === "o2")!;
    expect(o2.unitId).toBe("uav.02");
    expect(o2.routeLegs[o2.routeLegs.length - 1]!.toFacilityId).toBe("hub.b");
    expect(o2.routeLegs[o2.routeLegs.length - 1]!.toPadIndex).toBe(0);
  });

  it("keeps single-aircraft pad allocation positive at a one-pad facility", () => {
    const vp = { ...facility("vp.01", "vertiport", 0, 0),
      landing: { parkingSlots: 1, movementsPerHour: 30 } };
    const hubA = { ...facility("hub.a", "hub", 1000, 0),
      landing: { parkingSlots: 1, movementsPerHour: 20 } };
    const hubB = { ...facility("hub.b", "hub", 2000, 0),
      landing: { parkingSlots: 1, movementsPerHour: 20 } };
    const scenario = makeScenario(draft, [vp, hubA, hubB]);
    const logistics = makeLogistics(scenario,
      [order("o1", "hub.a", "hub.b", 0, 10000)], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({ ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100)]),
      logistics });
    expect(plan.acceptedAssignments).toHaveLength(1);
    expect(plan.acceptedAssignments[0]!.unitId).toBe("uav.01");
    const finalLeg = plan.acceptedAssignments[0]!.routeLegs[
      plan.acceptedAssignments[0]!.routeLegs.length - 1]!;
    expect(finalLeg.toFacilityId).toBe("hub.b");
    expect(finalLeg.toPadIndex).toBe(0);
    expect(plan.unitFinalStates[0]!.currentPadIndex).toBe(0);
    expect(plan.unitFinalStates[0]!.atFacilityId).toBe("hub.b");
  });

  it("preserves a unit's own pad for a same-site return and never fabricates a pad switch", () => {
    const hubA = { ...facility("hub.a", "hub", 0, 0),
      landing: { parkingSlots: 2, movementsPerHour: 20 } };
    const hubB = { ...facility("hub.b", "hub", 600, 0),
      landing: { parkingSlots: 2, movementsPerHour: 20 } };
    const hubC = { ...facility("hub.c", "hub", 900, 0),
      landing: { parkingSlots: 1, movementsPerHour: 20 } };
    const vp = { ...facility("vp.01", "vertiport", -500, 0),
      landing: { parkingSlots: 1, movementsPerHour: 30 } };
    const scenario = makeScenario(draft, [hubA, hubB, hubC, vp]);
    const logistics = makeLogistics(scenario, [
      order("A", "hub.a", "hub.c", 0, 10000),
      order("B", "hub.b", "hub.a", 1, 10000),
    ], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({ ...planInput(scenario,
      [unit("uav.01", "uav.big", "hub.a", 100),
        unit("uav.02", "uav.big", "hub.a", 100)]), logistics });
    expect(plan.acceptedAssignments).toHaveLength(2);
    expect(plan.unassignedOrders).toHaveLength(0);
    // No planned leg may switch physical pads inside one facility: a same-site
    // leg is only ever degenerate on the very same pad, never a 0s pad move.
    const allLegs = plan.acceptedAssignments.flatMap(assignment => [
      assignment.routeToPickup, assignment.routeFromPickupToDestination, ...assignment.routeLegs]);
    for (const leg of allLegs) {
      if (leg.fromFacilityId === leg.toFacilityId) {
        expect(leg.fromPadIndex).toBe(leg.toPadIndex);
      }
    }
    // uav.02 returns home on order B (hub.b -> hub.a). hub.a#0 is free after
    // uav.01 departed on order A, but uav.02 was declared on pad 1 and a
    // same-site pickup must preserve that pad instead of teleporting to pad 0.
    const b = plan.acceptedAssignments.find(assignment => assignment.orderId === "B")!;
    expect(b.unitId).toBe("uav.02");
    expect(b.routeLegs[b.routeLegs.length - 1]!.toFacilityId).toBe("hub.a");
    expect(b.routeLegs[b.routeLegs.length - 1]!.toPadIndex).toBe(1);
    const finalState = plan.unitFinalStates.find(state => state.unitId === "uav.02")!;
    expect(finalState.atFacilityId).toBe("hub.a");
    expect(finalState.currentPadIndex).toBe(1);
  });

  it("rejects an intermediate pickup on a pad another unit fully occupies", () => {
    const vp = { ...facility("vp.01", "vertiport", 0, 0),
      landing: { parkingSlots: 1, movementsPerHour: 30 } };
    const hubA = { ...facility("hub.a", "hub", 1000, 0),
      landing: { parkingSlots: 1, movementsPerHour: 20 } };
    const hubB = { ...facility("hub.b", "hub", 2000, 0),
      landing: { parkingSlots: 1, movementsPerHour: 20 } };
    const scenario = makeScenario(draft, [vp, hubA, hubB]);
    const logistics = makeLogistics(scenario, [
      order("o1", "vp.01", "hub.a", 0, 10000),
      order("o2", "hub.a", "hub.b", 1, 10000),
    ], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({ ...planInput(scenario,
      [unit("uav.01", "uav.big", "vp.01", 30),
        unit("uav.02", "uav.big", "hub.b", 100)]), logistics });
    // uav.01 parks hub.a pad 0 open-ended at t=100 after o1. uav.02's o2 pickup
    // at the SAME one-pad hub.a is a physically occupied intermediate contact:
    // it must not slip through as a zero-length dwell, so o2 stays a structured
    // pad_capacity unassigned result instead of a pad-sharing plan.
    expect(plan.acceptedAssignments).toHaveLength(1);
    expect(plan.acceptedAssignments[0]!.orderId).toBe("o1");
    expect(plan.unassignedOrders[0]!.orderId).toBe("o2");
    expect(plan.unassignedOrders[0]!.primaryReason).toBe("pad_capacity");
    expect(plan.unassignedOrders[0]!.perUnitFailures
      .some(failure => failure.unitId === "uav.02" && failure.reason === "pad_capacity")).toBe(true);
  });

  it("retains a winner's completed finite pad dwells so a later unit can never inside-book them", () => {
    const vp = { ...facility("vp.01", "vertiport", 0, 0),
      landing: { parkingSlots: 1, movementsPerHour: 30 } };
    const hubA = { ...facility("hub.a", "hub", 300, 0),
      landing: { parkingSlots: 1, movementsPerHour: 20 } };
    const hubB = { ...facility("hub.b", "hub", 600, 0),
      landing: { parkingSlots: 2, movementsPerHour: 20 } };
    const hubC = { ...facility("hub.c", "hub", 900, 0),
      landing: { parkingSlots: 2, movementsPerHour: 20 } };
    const scenario = makeScenario(draft, [vp, hubA, hubB, hubC]);
    const logistics = makeLogistics(scenario, [
      { id: "o1", sourceFacilityId: "hub.b", destinationFacilityId: "hub.c",
        hubHandoffFacilityId: "hub.a", cargoKg: 1, releaseAtS: 0, deliverByS: 10000 },
      order("o2", "hub.c", "hub.b", 100, 10000),
      order("o3", "vp.01", "hub.a", 150, 10000),
    ], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({ ...planInput(scenario,
      [unit("uav.01", "uav.big", "hub.b", 100),
        unit("uav.02", "uav.big", "vp.01", 100)]), logistics });
    expect(plan.acceptedAssignments).toHaveLength(2);
    expect(plan.acceptedAssignments.map(assignment => assignment.orderId)).toEqual(["o1", "o2"]);
    // o1 hands off through hub.a: uav.01 physically holds hub.a pad 0 during its
    // declared 180s handling dwell [30,210). o2 moves uav.01 on to hub.b but must
    // NOT erase that completed finite dwell; uav.02's o3 hub.a destination landing
    // at t=180 would then double-book the pad. The retained dwell keeps o3 a
    // structured pad_capacity unassigned result instead of a pad-sharing plan.
    expect(plan.unassignedOrders[0]!.orderId).toBe("o3");
    expect(plan.unassignedOrders[0]!.primaryReason).toBe("pad_capacity");
    expect(plan.unassignedOrders[0]!.perUnitFailures
      .some(failure => failure.unitId === "uav.02" && failure.reason === "pad_capacity")).toBe(true);
    // uav.01's own o1 landing on hub.a occurs inside that handling dwell; no
    // OTHER unit may ever land hub.a pad 0 at any planned time.
    const hubAContacts = plan.acceptedAssignments
      .flatMap(assignment => assignment.routeLegs
        .filter(leg => leg.toFacilityId === "hub.a" && leg.toPadIndex === 0)
        .map(leg => ({ unitId: assignment.unitId, atS: leg.departureAtS + leg.estimatedDurationS })));
    expect(hubAContacts).toEqual([{ unitId: "uav.01", atS: 30 }]);
  });

  it("rejects a same-facility pad-switch route request as a real transfer, never a teleport", () => {
    const vp = facility("vp.01", "vertiport", 400, -550);
    const hubA = facility("hub.a", "hub", 600, -550);
    const scenario = makeScenario(draft, [vp, hubA]);
    const logistics = makeLogistics(scenario, [], [profileFor("uav.big")], {
      ...DEFAULT_ALGORITHMS, parameters: { cruiseAltitudeM: 30 },
    });
    const estimator = createSelectedDispatchRouteEstimator({
      scenario, logistics, buildings: [], staticObstacles: [],
    });
    const request = { profile: profileFor("uav.big"), fromFacilityId: "hub.a",
      toFacilityId: "hub.a", fromPadIndex: 0, toPadIndex: 1, departureAtS: 0 };
    expect(estimator(request).ok).toBe(false);
    const crossSite = estimator({ profile: profileFor("uav.big"), fromFacilityId: "vp.01",
      toFacilityId: "hub.a", fromPadIndex: 0, toPadIndex: 0, departureAtS: 0 });
    expect(crossSite.ok).toBe(true);
  });

  it("keeps a home fleet larger than the home facility pads as a structured pad_capacity result", () => {
    const vp = { ...facility("vp.01", "vertiport", 0, 0),
      landing: { parkingSlots: 1, movementsPerHour: 30 } };
    const hubA = facility("hub.a", "hub", 1000, 0);
    const hubB = facility("hub.b", "hub", 2000, 0);
    const scenario = makeScenario(draft, [vp, hubA, hubB]);
    const logistics = makeLogistics(scenario,
      [order("o1", "hub.a", "hub.b", 0, 10000)], [profileFor("uav.big")]);
    const plan = planSelectedDispatch({
      ...planInput(scenario, [unit("uav.01", "uav.big", "vp.01", 100),
        unit("uav.02", "uav.big", "vp.01", 100)]), logistics,
    });
    // A one-pad home facility cannot host two declared initial aircraft. The plan
    // must stay a structured, UI-visible result (browser surfaces unassignedOrders,
    // never an uncaught exception): every order unassigned with pad_capacity.
    expect(plan.acceptedAssignments).toHaveLength(0);
    expect(plan.ordersConsidered).toBe(1);
    expect(plan.unassignedOrders[0]!.orderId).toBe("o1");
    expect(plan.unassignedOrders[0]!.primaryReason).toBe("pad_capacity");
    // Only the aircraft that has NO physical home pad is the failing unit; the
    // partner holds the single pad but the declared fleet still cannot exist.
    expect(plan.unassignedOrders[0]!.perUnitFailures.map(failure => failure.unitId))
      .toEqual(["uav.02"]);
    expect(plan.unitFinalStates).toHaveLength(2);
    // The declared-but-impossible sorted home positions are reported honestly.
    expect(plan.provenance.declaredUnits).toHaveLength(2);
  });
});
