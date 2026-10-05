/** Strict authoring-only DISPATCH PLANNING ENGINE for one selected OSM city.
 *
 * Stage 3: this engine plans which user-declared drone units could accept which
 * authored logistics orders inside the bound CitySelectedScenario. It is a pure,
 * deterministic AUTHORING PLANNER. It never executes a flight, dispatches an
 * order, charges a battery, touches a Provider, or emits telemetry. The returned
 * plan is always `executable:false`, carries explicit provenance labels, and never
 * fabricates order lifecycle transitions, pickup evidence or any actual provider
 * state.
 *
 * The route callback is the SOLE route authority: the planner asks it for the
 * collision-validated leg between two facilities at a departure time for a
 * profile, and the callback answers either with a verified aggregate estimate
 * (duration, distance, segments) or a structured failure. The planner never
 * invents a route, never falls back to straight-line distance, and never derives
 * performance or body dimensions from GLB display scale — those numbers are only
 * ever user-declared in the performance profiles.
 *
 * Energy is deliberately conservative: every leg burns
 *     max(cruisePowerW, hoverPowerW) * legSeconds / 3600  Wh,
 * a reserve of fleetEntry.batteryWh * fleetEntry.reserveRatio is enforced at every
 * intermediate point, and when a direct mission would breach that reserve the
 * planner may insert ONE planned charging stop at a declared positive-power
 * charger facility reachable from the unit's current site, charging at
 * chargingPowerW * chargeEfficiency. Charging never happens at a zero-power
 * facility and never overfills a declared battery capacity.
 *
 * Two deterministic assignment modes are supported:
 *   - centralized + nearest_feasible: the unit with the earliest feasible
 *     delivery (deterministic tie-break by unit id) serves each order.
 *   - distributed + sealed_bid: every unit privately computes a feasibility bid
 *     (a LOCAL planning approximation of a bid protocol — never real networked
 *     consensus), the plan records bid summaries, and the deterministic winner is
 *     the feasible bid with the earliest delivery (tie-break by unit id).
 * Unsupported combinations, and any "external" algorithm choice, are rejected
 * explicitly as requiring a formal Provider outside this authoring planner.
 */
import { parseCitySelectedLogistics, effectiveHubHandoff,
  type CitySelectedLogistics, type FleetPerformanceProfile, type LogisticsOrderRequest,
  type LogisticsAlgorithms } from "./city-selected-logistics-draft";
import { parseCitySelectedScenario,
  type CitySelectedScenario, type SelectedScenarioFacility, type SelectedScenarioFleetEntry } from "./city-selected-scenario";

export const CITY_SELECTED_DISPATCH_PLAN_SCHEMA = "aero-bench.city-selected-dispatch-plan/v1" as const;

/** Tolerance used only to avoid floating-point boundary noise in feasibility. */
const EPSILON = 1e-9;

/** End of an open-ended landing-pad occupancy for a unit parked at a facility. */
const FOREVER = 1e18;

const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/;

const PLAN_LABEL = "Authoring dispatch plan only. Declares no order dispatch, pickup, delivery, charging or any Provider state.";

/** One collision-validated route leg aggregate, as returned by the route authority.
 * `segments` are the callback's own verified sub-segments and are echoed verbatim. */
export interface DispatchRouteSegment {
  readonly kind: string;
  readonly distanceM: number;
  readonly estimatedSeconds: number;
  readonly label?: string;
}

export interface DispatchRouteRequest {
  readonly profile: FleetPerformanceProfile;
  readonly fromFacilityId: string;
  readonly toFacilityId: string;
  /** Physical pad index the unit occupies at the departure facility. */
  readonly fromPadIndex: number;
  /** Physical pad index the flight is bound to land on at the target facility. */
  readonly toPadIndex: number;
  readonly departureAtS: number;
}

export type RouteFailureCode = "no_route" | "geometry_unverified" | "collision" | "policy_gap";

/** Structured failure result: the route callback always answers success or a typed
 * failure, never an exception and never a guessed number. */
export interface RouteFailure {
  readonly ok: false;
  readonly code: RouteFailureCode;
  readonly message: string;
}

export interface RouteSuccess {
  readonly ok: true;
  readonly durationS: number;
  readonly distanceM: number;
  readonly segments: readonly DispatchRouteSegment[];
}

export type DispatchRouteResult = RouteSuccess | RouteFailure;

/** Sole route authority. It must already validate the requested leg against the
 * collision-verified geometry of the bound city for the given departure time and
 * profile; the planner treats any failure as an infeasible leg. */
export type RouteEstimator = (request: DispatchRouteRequest) => DispatchRouteResult;

/** One planned leg inside an accepted assignment, with a plan-unique reference.
 * The endpoints are bound to the deterministic physical pads the unit departs
 * from and lands on, so the route geometry always uses real pad coordinates. */
export interface DispatchRouteLeg {
  readonly reference: string;
  readonly fromFacilityId: string;
  readonly toFacilityId: string;
  readonly fromPadIndex: number;
  readonly toPadIndex: number;
  readonly departureAtS: number;
  readonly estimatedDurationS: number;
  readonly estimatedDistanceM: number;
  readonly segments: readonly DispatchRouteSegment[];
}

/** One planned charging stop. No charge is claimed to have happened — this is a
 * planner-documented future action only. */
export interface PlannedChargingStop {
  readonly facilityId: string;
  readonly chargingPowerW: number;
  readonly routeToCharger: DispatchRouteLeg;
  readonly arrivalAtS: number;
  readonly departureAtS: number;
  readonly batteryAtArrivalWh: number;
  readonly batteryAfterChargeWh: number;
  readonly chargedWh: number;
  readonly chargeSeconds: number;
}

/** Explicit declared planning assumption for one physical drone. This is authoring
 * input, never inferred telemetry. */
export interface DispatchUnitState {
  readonly unitId: string;
  readonly fleetEntryId: string;
  readonly atFacilityId: string;
  readonly availableAtS: number;
  readonly batteryWh: number;
}

/** Final state after all planned missions, including the deterministic physical
 * pad the unit is parked on at its final facility. */
export interface DispatchUnitFinalState extends DispatchUnitState {
  readonly currentPadIndex: number;
}

export type AssignmentFailureCode =
  | "missing_profile"
  | "no_cargo_capacity"
  | "pad_capacity"
  | "route_to_pickup"
  | "route_delivery"
  | "no_charging_available"
  | "deadline_passed"
  | "not_released";

/** Rough priority used only to pick a stable headline reason for an unassigned
 * order when no unit could serve it. Low index wins. */
const REASON_PRIORITY: readonly UnassignedReasonCode[] = [
  "missing_profile",
  "no_cargo_capacity",
  "hub_storage_exceeded",
  "hub_throughput_exceeded",
  "pad_capacity",
  "no_route",
  "no_charging_available",
  "deadline_passed",
  "not_released",
  "no_feasible_unit",
];

export type UnassignedReasonCode =
  | "missing_profile"
  | "no_cargo_capacity"
  | "hub_storage_exceeded"
  | "hub_throughput_exceeded"
  | "pad_capacity"
  | "no_route"
  | "no_charging_available"
  | "deadline_passed"
  | "not_released"
  | "no_feasible_unit";

export interface AcceptedAssignment {
  readonly orderId: string;
  readonly unitId: string;
  readonly fleetEntryId: string;
  readonly cargoKg: number;
  /** The hub through which the parcel is handed off; enforced, never decorative. */
  readonly hubHandoffFacilityId: string;
  readonly routeToPickup: DispatchRouteLeg;
  readonly routeFromPickupToDestination: DispatchRouteLeg;
  /** Every post-pickup leg in order; a hub midpoint appears as its own leg. */
  readonly routeLegs: readonly DispatchRouteLeg[];
  readonly chargingStops: readonly PlannedChargingStop[];
  /** Mission start: the instant the unit first departs for this order (either
   * straight to pickup or, when charging, to the planned charger). */
  readonly departureAtS: number;
  readonly pickupAtS: number;
  readonly arrivalAtS: number;
  readonly batteryAtPickupDepartureWh: number;
  readonly batteryAtArrivalWh: number;
  readonly energyConsumedWh: number;
}

export interface PerUnitFailure {
  readonly unitId: string;
  readonly fleetEntryId: string;
  readonly reason: AssignmentFailureCode;
  readonly message: string;
}

export interface UnassignedOrder {
  readonly orderId: string;
  readonly primaryReason: UnassignedReasonCode;
  readonly detail: string;
  readonly perUnitFailures: readonly PerUnitFailure[];
}

/** Recorded per-unit feasibility bid in distributed/sealed_bid mode. This is a
 * local planning approximation of a bid protocol, never a real networked result. */
export interface SealedBidSummary {
  readonly orderId: string;
  readonly unitId: string;
  readonly fleetEntryId: string;
  readonly feasible: boolean;
  readonly estimatedDeliveryS: number | null;
  readonly estimatedEnergyWh: number | null;
  readonly plannedChargingStopCount: number;
  readonly reason: AssignmentFailureCode | null;
}

export interface DispatchProvenance {
  readonly planner: "city-selected-dispatch";
  readonly planSchema: typeof CITY_SELECTED_DISPATCH_PLAN_SCHEMA;
  readonly label: string;
  readonly mode: LogisticsAlgorithms["mode"];
  readonly assignment: LogisticsAlgorithms["assignment"];
  readonly routing: LogisticsAlgorithms["routing"];
  readonly charging: LogisticsAlgorithms["charging"];
  /** Identity of the exact bound selected scene from the current scenario. */
  readonly boundSelectedScene: {
    readonly job_id: string;
    readonly selection_sha256: string;
    readonly source_id: string;
    readonly source_sha256: string;
  };
  /** Explicit planning assumptions, repeated here verbatim for auditability. */
  readonly declaredUnits: readonly DispatchUnitState[];
}

export interface DispatchPlan {
  readonly purpose: "selected-logistics-dispatch-plan";
  readonly schema_version: typeof CITY_SELECTED_DISPATCH_PLAN_SCHEMA;
  readonly executable: false;
  readonly provenance: DispatchProvenance;
  /** Deterministic planning epoch: the earliest release among considered orders
   * (0 when no orders). No wall-clock time ever enters the plan. */
  readonly planningEpochS: number;
  readonly ordersConsidered: number;
  readonly acceptedAssignments: readonly AcceptedAssignment[];
  readonly unassignedOrders: readonly UnassignedOrder[];
  /** Flat view of every planned charging stop across accepted assignments. */
  readonly plannedChargingStops: readonly PlannedChargingStop[];
  /** Every leg the route authority produced, keyed by leg.reference. */
  readonly routeReferences: Readonly<Record<string, DispatchRouteLeg>>;
  /** Present only in distributed/sealed_bid mode; otherwise empty. */
  readonly sealedBids: readonly SealedBidSummary[];
  readonly unitFinalStates: readonly DispatchUnitFinalState[];
}

export interface DispatchPlanInput {
  readonly scenario: CitySelectedScenario;
  readonly logistics: CitySelectedLogistics;
  readonly units: readonly DispatchUnitState[];
  readonly routeEstimator: RouteEstimator;
  /** Optional caller-supplied deterministic generated requests. They are merged
   * with the manual orders, de-duplicated and sorted by release time. */
  readonly generatedOrders?: readonly LogisticsOrderRequest[];
}

/** Validation pass: re-parses the bound scene and the merged logistics document
 * (rejecting a foreign scenario or duplicate ids), then checks the declared unit
 * states and fleet counts. Throws at the first blocking issue. */
function validateDispatchInput(input: DispatchPlanInput): void {
  const scenario = parseCitySelectedScenario(input.scenario);
  const mergedOrders: readonly unknown[] = input.generatedOrders === undefined
    ? input.logistics.orders
    : [...input.logistics.orders, ...input.generatedOrders];
  parseCitySelectedLogistics({ ...input.logistics, orders: mergedOrders }, scenario);

  const fleetById = new Map(scenario.fleet.map(entry => [entry.id, entry]));
  const facilityIds = new Set(scenario.facilities.map(facility => facility.id));
  const counts = new Map<string, number>();
  const seen = new Set<string>();
  for (const unit of input.units) {
    if (!ID.test(unit.unitId) || seen.has(unit.unitId)) throw new Error(`派工单位 ID 无效或重复：${unit.unitId}`);
    seen.add(unit.unitId);
    const entry = fleetById.get(unit.fleetEntryId);
    if (entry === undefined) throw new Error(`单位 ${unit.unitId} 引用未知机队条目：${unit.fleetEntryId}`);
    if (!facilityIds.has(unit.atFacilityId)) throw new Error(`单位 ${unit.unitId} 所在设施不存在：${unit.atFacilityId}`);
    if (!Number.isFinite(unit.availableAtS) || unit.availableAtS < 0) {
      throw new Error(`单位 ${unit.unitId} 的可用时间必须是有限非负数`);
    }
    if (!Number.isFinite(unit.batteryWh) || unit.batteryWh < 0 || unit.batteryWh > entry.batteryWh) {
      throw new Error(`单位 ${unit.unitId} 的电池电量必须在 0 与机队容量 ${entry.batteryWh}Wh 之间`);
    }
    counts.set(unit.fleetEntryId, (counts.get(unit.fleetEntryId) ?? 0) + 1);
  }
  for (const entry of scenario.fleet) {
    if ((counts.get(entry.id) ?? 0) > entry.count) {
      throw new Error(`机队条目 ${entry.id} 声明的单位数量超过配置数量 ${entry.count}`);
    }
  }
}

/** Deterministic order list: manual plus caller-supplied generated requests.
 * Duplicate ids were already rejected by the parse pass inside validation. */
function sortedOrders(doc: CitySelectedLogistics, generated: readonly LogisticsOrderRequest[] | undefined):
    LogisticsOrderRequest[] {
  const merged = generated === undefined ? doc.orders : [...doc.orders, ...generated];
  return [...merged].sort((a, b) =>
    a.releaseAtS !== b.releaseAtS ? a.releaseAtS - b.releaseAtS : (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

interface RefStream { index: number; }

function nextRef(stream: RefStream): string {
  const reference = `leg.${stream.index}`;
  stream.index += 1;
  return reference;
}

function energyWh(powerW: number, seconds: number): number {
  return powerW * seconds / 3600;
}

function maxLegPower(profile: FleetPerformanceProfile): number {
  return Math.max(profile.cruisePowerW, profile.hoverPowerW);
}

type LegOutcome =
  | { readonly ok: true; readonly leg: DispatchRouteLeg }
  | { readonly ok: false; readonly message: string };

/** Asks the route authority for one leg bound to the unit's current pad and the
 * target pad. A same-site leg is degenerate and trivially safe ONLY when the
 * unit stays on the very same physical pad (the pickup is already under the
 * aircraft); switching pads inside one facility is a real transfer that must be
 * planned and answered by the callback like any other leg, never a 0s/0m
 * teleport. Every real (or pad-switching) leg must be answered by the callback. */
function requestLeg(estimator: RouteEstimator, profile: FleetPerformanceProfile, reference: string,
                    from: string, to: string, fromPadIndex: number, toPadIndex: number,
                    departureAtS: number): LegOutcome {
  if (from === to) {
    if (fromPadIndex !== toPadIndex) {
      return { ok: false, message: `同设施 ${from} 内起降位 ${fromPadIndex}→${toPadIndex} `
        + `必须真实规划；只有设施且起降位完全相同才能作为零长腿` };
    }
    return { ok: true, leg: {
      reference, fromFacilityId: from, toFacilityId: to, fromPadIndex, toPadIndex, departureAtS,
      estimatedDurationS: 0, estimatedDistanceM: 0, segments: [],
    } };
  }
  const result = estimator({ profile, fromFacilityId: from, toFacilityId: to,
    fromPadIndex, toPadIndex, departureAtS });
  if (!result.ok) return { ok: false, message: result.message };
  if (!Number.isFinite(result.durationS) || result.durationS <= 0
      || !Number.isFinite(result.distanceM) || result.distanceM <= 0
      || !Array.isArray(result.segments) || result.segments.length === 0
      || result.segments.some(segment => !segment.kind
        || !Number.isFinite(segment.distanceM) || segment.distanceM < 0
        || !Number.isFinite(segment.estimatedSeconds) || segment.estimatedSeconds < 0)
      || Math.abs(result.segments.reduce((sum, segment) => sum + segment.distanceM, 0) - result.distanceM) > 1e-6
      || Math.abs(result.segments.reduce((sum, segment) => sum + segment.estimatedSeconds, 0) - result.durationS) > 1e-6) {
    return { ok: false, message: `路权回调返回了无效腿估计（${from}→${to}）` };
  }
  return { ok: true, leg: {
    reference, fromFacilityId: from, toFacilityId: to, fromPadIndex, toPadIndex, departureAtS,
    estimatedDurationS: result.durationS, estimatedDistanceM: result.distanceM,
    segments: result.segments.map(segment => ({ ...segment })),
  } };
}

interface UnitRuntime {
  state: DispatchUnitState;
  readonly entry: SelectedScenarioFleetEntry;
  readonly profile: FleetPerformanceProfile | null;
  /** Start of the unit's current parking slot occupancy at `state.atFacilityId`
   * (declared initial condition: parked at home from the planning epoch). */
  parkingStartAtS: number;
  /** Deterministic physical pad the unit occupies at `state.atFacilityId`. */
  currentPadIndex: number;
}

/** Facility sequence for a mission: start → pickup source → (hub handoff when it
 * is not already an endpoint) → destination, with consecutive duplicates removed. */
function missionSequence(startId: string, order: LogisticsOrderRequest): string[] {
  const sequence = [startId, order.sourceFacilityId];
  const handoff = order.hubHandoffFacilityId;
  if (handoff !== null && handoff !== order.sourceFacilityId && handoff !== order.destinationFacilityId) {
    sequence.push(handoff);
  }
  sequence.push(order.destinationFacilityId);
  return sequence.filter((id, index) => index === 0 || id !== sequence[index - 1]);
}

/** Sum several verified legs into one pickup→destination aggregate. Durations,
 * distances and the callback's own sub-segments are summed/concatenated; the
 * aggregate inherits the first leg's departure pad and the last leg's landing pad. */
function combineLegs(reference: string, legs: readonly DispatchRouteLeg[]): DispatchRouteLeg {
  const first = legs[0]!;
  const last = legs[legs.length - 1]!;
  return {
    reference, fromFacilityId: first.fromFacilityId, toFacilityId: last.toFacilityId,
    fromPadIndex: first.fromPadIndex, toPadIndex: last.toPadIndex,
    departureAtS: first.departureAtS,
    estimatedDurationS: legs.reduce((sum, leg) => sum + leg.estimatedDurationS, 0),
    estimatedDistanceM: legs.reduce((sum, leg) => sum + leg.estimatedDistanceM, 0),
    segments: legs.flatMap(leg => leg.segments),
  };
}

interface FeasibleAttempt {
  readonly feasible: true;
  readonly unitId: string;
  readonly fleetEntryId: string;
  readonly hubHandoffFacilityId: string;
  readonly arrivalAtS: number;
  readonly departureAtS: number;
  readonly pickupAtS: number;
  readonly energyConsumedWh: number;
  readonly batteryAtPickupDepartureWh: number;
  readonly batteryAtArrivalWh: number;
  readonly routeToPickup: DispatchRouteLeg;
  readonly routeFromPickupToDestination: DispatchRouteLeg;
  readonly routeLegs: readonly DispatchRouteLeg[];
  readonly chargingStops: readonly PlannedChargingStop[];
  /** Physical pad reservations this mission claims, already allocated to
   * deterministic pad indices. */
  readonly padReservations: readonly PadInterval[];
  /** Pad the unit parks on at the final destination. */
  readonly finalPadIndex: number;
}

interface InfeasibleAttempt {
  readonly feasible: false;
  readonly unitId: string;
  readonly fleetEntryId: string;
  readonly failure: PerUnitFailure;
}

type UnitAttempt = FeasibleAttempt | InfeasibleAttempt;

function fail(unitId: string, fleetEntryId: string, reason: AssignmentFailureCode, message: string):
    InfeasibleAttempt {
  return { feasible: false, unitId, fleetEntryId, failure: { unitId, fleetEntryId, reason, message } };
}

/** Greedy single-attempt feasibility for one unit against one order. Orders are
 * processed in release order so the unit's declared availability/battery/location
 * always reflect every earlier accepted mission. */
function attemptForUnit(unit: UnitRuntime, order: LogisticsOrderRequest, scenario: CitySelectedScenario,
                        estimator: RouteEstimator, refs: RefStream,
                        reservedCharges: readonly PlannedChargingStop[],
                        acceptedPads: readonly PadInterval[]): UnitAttempt {
  const { entry, profile } = unit;
  const unitId = unit.state.unitId;
  if (profile === null) {
    return fail(unitId, entry.id, "missing_profile", `机队条目 ${entry.id} 缺少性能档案`);
  }
  if (order.cargoKg > entry.maxPayloadKg + EPSILON) {
    return fail(unitId, entry.id, "no_cargo_capacity",
      `货物 ${order.cargoKg}kg 超过机队 ${entry.id} 最大载荷 ${entry.maxPayloadKg}kg`);
  }
  const power = maxLegPower(profile);
  const reserve = entry.batteryWh * entry.reserveRatio;
  const departureAtS = Math.max(unit.state.availableAtS, order.releaseAtS);
  if (departureAtS >= order.deliverByS - EPSILON) {
    return fail(unitId, entry.id, "deadline_passed",
      `最早可出发时间 ${departureAtS}s 已超过订单期限 ${order.deliverByS}s`);
  }

  const sequence = missionSequence(unit.state.atFacilityId, order);
  const externalReservations = acceptedPads.filter(interval => interval.unitId !== unitId);
  const padded = planPaddedSequence(estimator, profile, refs, sequence, departureAtS,
    unit.state.atFacilityId, unit.currentPadIndex, unit.parkingStartAtS, unitId, scenario,
    externalReservations);
  if (!padded.ok) {
    if (padded.code === "pad_capacity") {
      return fail(unitId, entry.id, "pad_capacity", padded.message);
    }
    return fail(unitId, entry.id, padded.index === 0 ? "route_to_pickup" : "route_delivery",
      `无法规划${padded.index === 0 ? "到取件点" : "取件到目的地"}的航路：${padded.message}`);
  }
  const route = padded;
  // When the unit already parks at the pickup source, missionSequence collapses
  // the start into the source and the first real leg is the delivery itself. Keep
  // the baseline split: a degenerate zero-duration to-pickup leg plus the full
  // post-pickup route, so `routeFromPickupToDestination` always has at least one leg.
  const startsAtSource = unit.state.atFacilityId === order.sourceFacilityId;
  const routeToPickup = startsAtSource
    ? { reference: nextRef(refs), fromFacilityId: order.sourceFacilityId,
        toFacilityId: order.sourceFacilityId, fromPadIndex: unit.currentPadIndex,
        toPadIndex: unit.currentPadIndex, departureAtS,
        estimatedDurationS: 0, estimatedDistanceM: 0, segments: [] }
    : route.legs[0]!;
  const postLegs = startsAtSource ? route.legs : route.legs.slice(1);
  const delivery = combineLegs(nextRef(refs), postLegs);
  const pickupDeparture = departureAtS + routeToPickup.estimatedDurationS;
  const legSeconds = route.legs.reduce((sum, leg) => sum + leg.estimatedDurationS, 0);
  const arrivalAtS = route.arrivalAtS;
  if (arrivalAtS > order.deliverByS + EPSILON) {
    return fail(unitId, entry.id, "deadline_passed",
      `最早到达 ${arrivalAtS}s 超过订单期限 ${order.deliverByS}s`);
  }
  const energyConsumedWh = energyWh(power, legSeconds);
  const energyToPickup = energyWh(power, routeToPickup.estimatedDurationS);
  const finalBattery = unit.state.batteryWh - energyConsumedWh;
  if (finalBattery < reserve - EPSILON) {
    // Direct flight would breach the declared reserve; try ONE reachable,
    // positive-power charging stop before pickup. Charging is never invented at
    // a zero-power facility and never overfills a declared battery capacity.
    return attemptWithCharging(unit, order, scenario, estimator, profile, power, reserve,
      departureAtS, refs, reservedCharges, acceptedPads);
  }
  return {
    feasible: true, unitId, fleetEntryId: entry.id,
    hubHandoffFacilityId: effectiveHubHandoff(order, facilitiesOf(scenario)),
    arrivalAtS, departureAtS, pickupAtS: pickupDeparture,
    energyConsumedWh,
    batteryAtPickupDepartureWh: unit.state.batteryWh - energyToPickup,
    batteryAtArrivalWh: finalBattery,
    routeToPickup, routeFromPickupToDestination: delivery,
    routeLegs: postLegs,
    chargingStops: [],
    padReservations: route.reservations,
    finalPadIndex: route.finalPadIndex,
  };
}

function facilitiesOf(scenario: CitySelectedScenario): ReadonlyMap<string, CitySelectedScenario["facilities"][number]> {
  return new Map(scenario.facilities.map(facility => [facility.id, facility]));
}

/** Declared hub storage is shared across accepted missions over their declared
 * time windows, using half-open intervals and summed kilograms. */
function hubStorageExceeded(capacityKg: number,
                            window: { readonly start: number; readonly end: number; readonly kg: number },
                            accepted: readonly { readonly start: number; readonly end: number; readonly kg: number }[]): boolean {
  const events: { time: number; change: number }[] = [
    { time: window.start, change: window.kg }, { time: window.end, change: -window.kg },
  ];
  for (const other of accepted) {
    if (other.end <= window.start || other.start >= window.end) continue;
    events.push({ time: Math.max(window.start, other.start), change: other.kg },
      { time: Math.min(window.end, other.end), change: -other.kg });
  }
  events.sort((a, b) => a.time - b.time || a.change - b.change);
  let occupied = 0;
  for (const event of events) {
    occupied += event.change;
    if (occupied > capacityKg + EPSILON) return true;
  }
  return false;
}

/** Declared charger capacity is shared across accepted missions, using half-open time intervals. */
function chargerHasCapacity(facilityId: string, capacity: number, start: number, end: number,
                            reserved: readonly PlannedChargingStop[]): boolean {
  const events = [{ time: start, change: 1 }, { time: end, change: -1 }];
  for (const stop of reserved) {
    if (stop.facilityId !== facilityId || stop.departureAtS <= start || stop.arrivalAtS >= end) continue;
    events.push({ time: Math.max(start, stop.arrivalAtS), change: 1 },
      { time: Math.min(end, stop.departureAtS), change: -1 });
  }
  events.sort((a, b) => a.time - b.time || a.change - b.change);
  let occupied = 0;
  for (const event of events) {
    occupied += event.change;
    if (occupied > capacity) return false;
  }
  return true;
}

/** Physical landing/charging pad count for one facility. A standalone charger has
 * one 2.2 m pad per charging slot; a vertiport/hub has `landing.parkingSlots`
 * laid-out pad rectangles. The scheduler reserves exactly these many pads, so the
 * declared capability and the physical pad geometry always agree. */
function facilityPadSlots(facility: SelectedScenarioFacility): number {
  return facility.kind === "charger" ? (facility.charging?.slots ?? 0) : (facility.landing?.parkingSlots ?? 0);
}

/** Declared pad-handling dwell for ONE intermediate contact at a landing-capable
 * facility, derived from the authored throughput where the capability supplies
 * one: `movementsPerHour` means the declared pad cycle time is 3600/movements.
 * Intermediate pickups/hub passages physically occupy a pad for this whole
 * handling dwell, so a passage can never hide behind a zero-length dwell. A
 * facility without a declared landing throughput (standalone charger) has no
 * landing handling time; its true dwell is already carried by its explicit
 * charging window. No hidden or invented duration is ever added. */
function facilityHandlingSeconds(facility: SelectedScenarioFacility): number {
  return facility.landing !== null && facility.landing.movementsPerHour > 0
    ? 3600 / facility.landing.movementsPerHour
    : 0;
}

/** One reserved physical pad occupancy. Half-open [start, end). `padIndex`
 * identifies the exact landing/charging rectangle the unit occupies. */
interface PadInterval {
  readonly facilityId: string;
  readonly padIndex: number;
  readonly start: number;
  readonly end: number;
  readonly unitId: string;
}

/** True when any reservation in `reservations` occupies exactly this physical pad
 * over the half-open interval [start, end). A reservation ending at `start` or
 * beginning at `end` does not conflict (adjacent reuse at a shared boundary is
 * allowed). A zero-length dwell is still an explicit physical CONTACT: it can
 * never land on a pad that another reservation actually covers at that instant,
 * even when a hidden duration would otherwise be zero. Nothing here claims
 * continuous mid-air collision avoidance — this only guards one physical pad. */
function padOccupied(reservations: readonly PadInterval[], facilityId: string, padIndex: number,
                     start: number, end: number): boolean {
  for (const reservation of reservations) {
    if (reservation.facilityId !== facilityId || reservation.padIndex !== padIndex) continue;
    if (reservation.end <= start || reservation.start >= end) continue;
    return true;
  }
  if (end <= start) {
    for (const reservation of reservations) {
      if (reservation.facilityId !== facilityId || reservation.padIndex !== padIndex) continue;
      if (reservation.start <= start && start < reservation.end) return true;
    }
  }
  return false;
}

type PaddedSequenceResult =
  | { readonly ok: true; readonly legs: readonly DispatchRouteLeg[]; readonly arrivalAtS: number;
      readonly reservations: readonly PadInterval[]; readonly finalPadIndex: number }
  | { readonly ok: false; readonly message: string; readonly index: number;
      readonly code: AssignmentFailureCode };

/** Plan one mission chain front-to-back, allocating a deterministic physical pad
 * to every dwell and binding each route leg to the unit's actual departure and
 * landing pads. The unit occupies its current pad from `parkingStartAtS` until the
 * first leg departs; each leg lands on the lowest-index target pad that is free
 * for its dwell. When the chain returns to the mission-start facility (a
 * same-site pickup/return or a hub handoff that IS the start facility), the
 * unit's own start pad is the PREFERRED landing pad — the planner preserves the
 * unit's declared physical pad for a same-site visit instead of teleporting it to
 * the lowest index. Intermediate contacts claim the declared handling dwell
 * (3600/movementsPerHour where the capability supplies one) as a real pad
 * occupancy; final parks stay open-ended. `externalReservations` are the OTHER
 * units' already accepted pad occupancies; the candidate's own competing
 * reservations accumulate during the run so it can never double-book itself. */
function planPaddedSequence(estimator: RouteEstimator, profile: FleetPerformanceProfile, refs: RefStream,
                            sequence: readonly string[], startAtS: number, startFacilityId: string,
                            startPadIndex: number, parkingStartAtS: number, unitId: string,
                            scenario: CitySelectedScenario,
                            externalReservations: readonly PadInterval[]): PaddedSequenceResult {
  const facilityById = new Map(scenario.facilities.map(facility => [facility.id, facility]));
  const legs: DispatchRouteLeg[] = [];
  const reservations: PadInterval[] = [];
  const blocking = (): readonly PadInterval[] => [...externalReservations, ...reservations];
  if (startAtS > parkingStartAtS) {
    reservations.push({ facilityId: startFacilityId, padIndex: startPadIndex,
      start: parkingStartAtS, end: startAtS, unitId });
  }
  let currentFacilityId = startFacilityId;
  let currentPadIndex = startPadIndex;
  let time = startAtS;
  for (let index = 0; index + 1 < sequence.length; index++) {
    const toFacilityId = sequence[index + 1]!;
    const isFinal = index + 2 >= sequence.length;
    const target = facilityById.get(toFacilityId);
    if (target === undefined) {
      return { ok: false, message: `目标设施不存在：${toFacilityId}`, index, code: "route_delivery" };
    }
    const slots = facilityPadSlots(target);
    if (slots <= 0) {
      return { ok: false, message: `设施 ${toFacilityId} 没有可用的物理起降位`, index, code: "pad_capacity" };
    }
    const handling = facilityHandlingSeconds(target);
    // A same-site visit keeps the unit's own physical pad whenever that pad is
    // free; only then do lower-index pads become candidates. Everything else
    // scans the lowest-index free physical pad, deterministically.
    const stacking = Array.from({ length: slots }, (_, padIndex) => padIndex);
    const padOrder = toFacilityId === startFacilityId
      ? [startPadIndex, ...stacking.filter(padIndex => padIndex !== startPadIndex)]
      : stacking;
    let firstRouteFailure: string | null = null;
    let anyRoutable = false;
    let chosenPadIndex: number | null = null;
    let chosenLeg: DispatchRouteLeg | null = null;
    let dwellStart = 0;
    for (const padIndex of padOrder) {
      const legOutcome = requestLeg(estimator, profile, nextRef(refs), currentFacilityId, toFacilityId,
        currentPadIndex, padIndex, time);
      if (!legOutcome.ok) {
        firstRouteFailure ??= legOutcome.message;
        continue;
      }
      anyRoutable = true;
      const arrival = time + legOutcome.leg.estimatedDurationS;
      const dwellEnd = isFinal ? FOREVER : arrival + handling;
      if (padOccupied(blocking(), toFacilityId, padIndex, arrival, dwellEnd)) continue;
      chosenPadIndex = padIndex;
      chosenLeg = legOutcome.leg;
      dwellStart = arrival;
      break;
    }
    if (!anyRoutable) {
      return { ok: false, message: firstRouteFailure ?? `航路不可达（${currentFacilityId}→${toFacilityId}）`,
        index, code: index === 0 ? "route_to_pickup" : "route_delivery" };
    }
    if (chosenPadIndex === null || chosenLeg === null) {
      return { ok: false, message: `设施 ${toFacilityId} 在 ${time.toFixed(1)}s 起没有空闲的物理起降位`,
        index, code: "pad_capacity" };
    }
    legs.push(chosenLeg);
    reservations.push({ facilityId: toFacilityId, padIndex: chosenPadIndex,
      start: dwellStart, end: isFinal ? FOREVER : dwellStart + handling, unitId });
    currentFacilityId = toFacilityId;
    currentPadIndex = chosenPadIndex;
    time += chosenLeg.estimatedDurationS;
    // An intermediate landing physically occupies its pad for the declared
    // handling dwell; the next leg departs only after that dwell has elapsed.
    if (!isFinal) time += handling;
  }
  return { ok: true, legs, arrivalAtS: time, reservations, finalPadIndex: currentPadIndex };
}

/** Deterministically allocate distinct physical pad indices to the declared
 * initial units at their home facilities. More units than physical pads is an
 * explicit infeasibility of the declared authoring input; instead of throwing it
 * is reported back as a structured allocation failure so the plan can carry a
 * UI-visible `pad_capacity` unassigned result for every order. */
type InitialPadAllocation =
  | { readonly ok: true; readonly pads: ReadonlyMap<string, number> }
  | { readonly ok: false; readonly message: string; readonly unitsWithoutPad: readonly string[] };

function initialPadIndexes(scenario: CitySelectedScenario,
                           units: readonly DispatchUnitState[]): InitialPadAllocation {
  const facilityById = new Map(scenario.facilities.map(facility => [facility.id, facility]));
  const byFacility = new Map<string, DispatchUnitState[]>();
  for (const unitState of units) {
    const list = byFacility.get(unitState.atFacilityId) ?? [];
    list.push(unitState);
    byFacility.set(unitState.atFacilityId, list);
  }
  const assigned = new Map<string, number>();
  for (const [facilityId, list] of byFacility) {
    const sorted = [...list].sort((a, b) => (a.unitId < b.unitId ? -1 : a.unitId > b.unitId ? 1 : 0));
    const facility = facilityById.get(facilityId);
    const slots = facility === undefined ? 0 : facilityPadSlots(facility);
    if (sorted.length > slots) {
      return { ok: false,
        message: `设施 ${facilityId} 的初始停机数量 ${sorted.length} 超过物理起降位数量 ${slots}：`
          + `初始航机必须占用不同的起降位`,
        unitsWithoutPad: sorted.slice(slots).map(unitState => unitState.unitId) };
    }
    sorted.forEach((unitState, index) => assigned.set(unitState.unitId, index));
  }
  return { ok: true, pads: assigned };
}

/** Declared hub storage window for an ACCEPTED mission, from the actual route
 * legs: a midpoint hub holds the parcel from arrival to departure, a source hub
 * from release to departure, and a destination hub frees it on arrival. */
function hubDwellWindow(hubId: string, order: LogisticsOrderRequest, attempt: FeasibleAttempt):
    { start: number; end: number; kg: number } {
  const legs = attempt.routeLegs;
  if (hubId === order.sourceFacilityId) {
    const out = legs.find(leg => leg.fromFacilityId === hubId);
    return { start: order.releaseAtS, end: out?.departureAtS ?? attempt.arrivalAtS, kg: order.cargoKg };
  }
  if (hubId === order.destinationFacilityId) {
    return { start: attempt.arrivalAtS, end: attempt.arrivalAtS, kg: order.cargoKg };
  }
  const into = legs.find(leg => leg.toFacilityId === hubId);
  const out = legs.find(leg => leg.fromFacilityId === hubId);
  const start = into !== undefined ? into.departureAtS + into.estimatedDurationS : attempt.pickupAtS;
  const end = out?.departureAtS ?? attempt.arrivalAtS;
  return { start, end: Math.max(start, end), kg: order.cargoKg };
}

function attemptWithCharging(unit: UnitRuntime, order: LogisticsOrderRequest, scenario: CitySelectedScenario,
                             estimator: RouteEstimator, profile: FleetPerformanceProfile, power: number,
                             reserve: number, departureAtS: number, refs: RefStream,
                             reservedCharges: readonly PlannedChargingStop[],
                             acceptedPads: readonly PadInterval[]): UnitAttempt {
  const { entry } = unit;
  const unitId = unit.state.unitId;
  const capacity = entry.batteryWh;
  const chargers = scenario.facilities.filter(facility =>
    facility.charging !== null && facility.charging.powerW > 0)
    .sort((left, right) => left.id.localeCompare(right.id));
  if (chargers.length === 0) {
    return fail(unitId, entry.id, "no_charging_available", "场景中没有正功率充电设施可规划充电");
  }
  const externalReservations = acceptedPads.filter(interval => interval.unitId !== unitId);

  let best: FeasibleAttempt | null = null;
  for (const charger of chargers) {
    const charging = charger.charging!;
    const slots = facilityPadSlots(charger);
    if (slots <= 0) continue;
    const atCharger = unit.state.atFacilityId === charger.id;
    // A unit already parked at the charger stays on its own pad; otherwise every
    // physical charger pad is a candidate and the lowest-index free one wins.
    const padCandidates = atCharger
      ? [unit.currentPadIndex]
      : Array.from({ length: slots }, (_, index) => index);
    for (const chargerPadIndex of padCandidates) {
      const routeToCharger = requestLeg(estimator, profile, nextRef(refs),
        unit.state.atFacilityId, charger.id, unit.currentPadIndex, chargerPadIndex, departureAtS);
      if (!routeToCharger.ok) continue;
      const energyToCharger = energyWh(power, routeToCharger.leg.estimatedDurationS);
      const batteryAtCharger = unit.state.batteryWh - energyToCharger;
      if (routeToCharger.leg.estimatedDistanceM > 0 && batteryAtCharger < reserve - EPSILON) continue;
      const chargerArrival = departureAtS + routeToCharger.leg.estimatedDurationS;
      const chargedWh = capacity - batteryAtCharger;
      if (chargedWh <= EPSILON) continue;
      // This conservative policy fills the declared battery before the mission.
      // Route planning runs at the actual post-charge time, so timed no-fly zones
      // cannot be bypassed by a route calculated before charging.
      const chargeSeconds = chargedWh * 3600 / (charging.powerW * profile.chargeEfficiency);
      const chargerDeparture = chargerArrival + chargeSeconds;
      if (!Number.isFinite(chargerDeparture) || chargerDeparture >= order.deliverByS) continue;
      if (!chargerHasCapacity(charger.id, charging.slots, chargerArrival, chargerDeparture, reservedCharges)) continue;
      const preReservations: PadInterval[] = [];
      if (departureAtS > unit.parkingStartAtS) {
        preReservations.push({ facilityId: unit.state.atFacilityId, padIndex: unit.currentPadIndex,
          start: unit.parkingStartAtS, end: departureAtS, unitId });
      }
      const chargerReservation: PadInterval = { facilityId: charger.id, padIndex: chargerPadIndex,
        start: chargerArrival, end: chargerDeparture, unitId };
      if (padOccupied([...externalReservations, ...preReservations],
        charger.id, chargerPadIndex, chargerArrival, chargerDeparture)) continue;
      const sequence = missionSequence(charger.id, order);
      const padded = planPaddedSequence(estimator, profile, refs, sequence, chargerDeparture,
        charger.id, chargerPadIndex, chargerDeparture, unitId, scenario,
        [...externalReservations, ...preReservations, chargerReservation]);
      if (!padded.ok) continue;
      // When the charger IS the pickup source, missionSequence collapses the
      // charger into the source so every padded leg is already a post-pickup
      // delivery leg. Keep the baseline split (same pattern as attemptForUnit): a
      // degenerate zero-duration to-pickup leg on the source pad plus the FULL
      // padded legs as the delivery route, so routeFromPickupToDestination always
      // carries at least one leg. A direct source→destination flight would
      // otherwise collapse to combineLegs([]) and an intermediate hub would get
      // the wrong leg attribution and pickup time. Source FACILITY identity is the
      // condition, whether the unit already parks on the source charger pad or
      // flies to it first and charges there.
      const startsAtSource = charger.id === order.sourceFacilityId;
      const routeToPickup = startsAtSource
        ? { reference: nextRef(refs), fromFacilityId: order.sourceFacilityId,
            toFacilityId: order.sourceFacilityId, fromPadIndex: chargerPadIndex,
            toPadIndex: chargerPadIndex, departureAtS: chargerDeparture,
            estimatedDurationS: 0, estimatedDistanceM: 0, segments: [] }
        : padded.legs[0]!;
      const postLegs = startsAtSource ? padded.legs : padded.legs.slice(1);
      const delivery = combineLegs(nextRef(refs), postLegs);
      const pickupDeparture = chargerDeparture + routeToPickup.estimatedDurationS;
      const legSeconds = padded.legs.reduce((sum, leg) => sum + leg.estimatedDurationS, 0);
      const batteryAtDestination = capacity - energyWh(power, legSeconds);
      const arrivalAtS = padded.arrivalAtS;
      if (!Number.isFinite(arrivalAtS) || batteryAtDestination < reserve - EPSILON
          || arrivalAtS > order.deliverByS + EPSILON) continue;

      const stop: PlannedChargingStop = {
        facilityId: charger.id,
        chargingPowerW: charging.powerW,
        routeToCharger: routeToCharger.leg,
        arrivalAtS: chargerArrival,
        departureAtS: chargerDeparture,
        batteryAtArrivalWh: batteryAtCharger,
        batteryAfterChargeWh: capacity,
        chargedWh,
        chargeSeconds,
      };
      const candidate: FeasibleAttempt = {
        feasible: true, unitId, fleetEntryId: entry.id,
        hubHandoffFacilityId: effectiveHubHandoff(order, facilitiesOf(scenario)),
        arrivalAtS,
        departureAtS,
        pickupAtS: pickupDeparture,
        energyConsumedWh: energyToCharger + energyWh(power, legSeconds),
        batteryAtPickupDepartureWh: capacity - energyWh(power, routeToPickup.estimatedDurationS),
        batteryAtArrivalWh: batteryAtDestination,
        routeToPickup, routeFromPickupToDestination: delivery,
        routeLegs: postLegs,
        chargingStops: [stop],
        padReservations: [...preReservations, chargerReservation, ...padded.reservations],
        finalPadIndex: padded.finalPadIndex,
      };
      if (best === null || candidate.arrivalAtS < best.arrivalAtS - EPSILON) best = candidate;
    }
  }
  return best ?? fail(unitId, entry.id, "no_charging_available",
    "当前出发时间下没有可达且有空位的充电站，或充电后电量/期限仍不可行；本策略不搜索延迟出发和排队");
}

/** Deterministic ordering of feasible attempts by earliest delivery, then unit id. */
function compareArrival(a: FeasibleAttempt, b: FeasibleAttempt): number {
  if (a.arrivalAtS !== b.arrivalAtS) return a.arrivalAtS - b.arrivalAtS;
  return a.unitId < b.unitId ? -1 : a.unitId > b.unitId ? 1 : 0;
}

const ASSIGNMENT_TO_HEADLINE: Record<AssignmentFailureCode, UnassignedReasonCode> = {
  missing_profile: "missing_profile",
  no_cargo_capacity: "no_cargo_capacity",
  pad_capacity: "pad_capacity",
  route_to_pickup: "no_route",
  route_delivery: "no_route",
  no_charging_available: "no_charging_available",
  deadline_passed: "deadline_passed",
  not_released: "not_released",
};

function headlineReason(attempts: readonly UnitAttempt[]): UnassignedReasonCode {
  const codes = new Set(attempts.filter(item => !item.feasible).map(item => item.failure.reason));
  if (codes.size === 0) return "no_feasible_unit";
  for (const priority of REASON_PRIORITY) {
    if ([...codes].some(code => ASSIGNMENT_TO_HEADLINE[code] === priority)) return priority;
  }
  return "no_feasible_unit";
}

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    for (const key of Object.keys(value)) deepFreeze((value as Record<string, unknown>)[key]);
    Object.freeze(value);
  }
  return value;
}

/** Build a structured, UI-visible infeasible plan for a home crowd that exceeds
 * the physical pads of a declared home facility. Every order is left unassigned
 * with an explicit `pad_capacity` headline and matching per-unit failures; the
 * plan still carries full provenance, the declared units (with their impossible
 * sorted home positions) and a deterministic epoch — the browser surface renders
 * `unassignedOrders`, so this preserves the pre-existing structured result
 * instead of surfacing an uncaught exception. */
function buildPadCrowdPlan(input: DispatchPlanInput, scenario: CitySelectedScenario,
                           logistics: CitySelectedLogistics, orders: readonly LogisticsOrderRequest[],
                           failure: Extract<InitialPadAllocation, { readonly ok: false }>): DispatchPlan {
  const declared = input.units;
  const unitsWithoutPad = new Set(failure.unitsWithoutPad);
  const perUnitFailures: PerUnitFailure[] = declared
    .filter(unitState => unitsWithoutPad.has(unitState.unitId))
    .map(unitState => ({ unitId: unitState.unitId, fleetEntryId: unitState.fleetEntryId,
      reason: "pad_capacity" as const, message: failure.message }));
  const algorithms = logistics.algorithms;
  // Report the impossible sorted home positions honestly (indices beyond the pad
  // count are visible), so the final states never invent a pad that exists.
  const sortedHomePads = new Map<string, number>();
  const byHome = new Map<string, DispatchUnitState[]>();
  for (const unitState of declared) {
    const bucket = byHome.get(unitState.atFacilityId) ?? [];
    bucket.push(unitState);
    byHome.set(unitState.atFacilityId, bucket);
  }
  for (const list of byHome.values()) {
    [...list].sort((a, b) => (a.unitId < b.unitId ? -1 : a.unitId > b.unitId ? 1 : 0))
      .forEach((unitState, index) => sortedHomePads.set(unitState.unitId, index));
  }
  const plan: DispatchPlan = {
    purpose: "selected-logistics-dispatch-plan",
    schema_version: CITY_SELECTED_DISPATCH_PLAN_SCHEMA,
    executable: false,
    provenance: {
      planner: "city-selected-dispatch",
      planSchema: CITY_SELECTED_DISPATCH_PLAN_SCHEMA,
      label: PLAN_LABEL,
      mode: algorithms.mode,
      assignment: algorithms.assignment,
      routing: algorithms.routing,
      charging: algorithms.charging,
      boundSelectedScene: {
        job_id: scenario.selectedScene.job_id,
        selection_sha256: scenario.selectedScene.selection_sha256,
        source_id: scenario.selectedScene.selection.source_id,
        source_sha256: scenario.selectedScene.source_sha256,
      },
      declaredUnits: declared.map(declaredUnit => ({ ...declaredUnit })),
    },
    planningEpochS: orders.length === 0 ? 0 : orders[0]!.releaseAtS,
    ordersConsidered: orders.length,
    acceptedAssignments: [],
    unassignedOrders: orders.map(orderItem => ({
      orderId: orderItem.id,
      primaryReason: "pad_capacity",
      detail: `${failure.message}：初始航机无法占据不同起降位，订单 ${orderItem.id} 无法规划`,
      perUnitFailures,
    })),
    plannedChargingStops: [],
    routeReferences: {},
    sealedBids: [],
    unitFinalStates: declared.map(unitState => ({
      ...unitState,
      atFacilityId: unitState.atFacilityId,
      availableAtS: unitState.availableAtS,
      batteryWh: unitState.batteryWh,
      currentPadIndex: sortedHomePads.get(unitState.unitId) ?? 0,
    })),
  };
  return deepFreeze(plan);
}

/** Main entry point. Rejects invalid input, then plans every order deterministically. */
export function planSelectedDispatch(input: DispatchPlanInput): DispatchPlan {
  validateDispatchInput(input);
  const scenario = parseCitySelectedScenario(input.scenario);
  const logistics = parseCitySelectedLogistics(input.logistics, scenario);
  const algorithms = logistics.algorithms;
  const centralized = algorithms.mode === "centralized" && algorithms.assignment === "nearest_feasible";
  const distributed = algorithms.mode === "distributed" && algorithms.assignment === "sealed_bid";
  if (!centralized && !distributed) {
    if (algorithms.assignment === "external" || algorithms.routing === "external"
        || algorithms.charging === "external") {
      throw new Error("外部算法选择需要正式 Provider 运行，本地 authoring 派工规划器不执行外部工作负载");
    }
    throw new Error(`不支持的调度组合：mode=${algorithms.mode}, assignment=${algorithms.assignment}`);
  }
  if (algorithms.routing !== "grid_astar" || algorithms.charging !== "reserve_threshold") {
    throw new Error(`不支持的算法选择（routing=${algorithms.routing}, charging=${algorithms.charging}）：`
      + "authoring 规划器只接受 grid_astar + reserve_threshold");
  }

  const orders = sortedOrders(logistics, input.generatedOrders);
  const fleetById = new Map(scenario.fleet.map(entry => [entry.id, entry]));
  const profileByEntry = new Map(logistics.performanceProfiles.map(profile => [profile.fleetEntryId, profile]));
  // Declared initial condition: every unit is parked at its home facility from the
  // planning epoch. Each home unit occupies one distinct physical pad; a home
  // crowd beyond the declared pad count is an explicit structured infeasibility
  // reported as `unassignedOrders` with a pad_capacity headline, never a throw.
  const allocation = initialPadIndexes(scenario, input.units);
  if (!allocation.ok) {
    return buildPadCrowdPlan(input, scenario, logistics, orders, allocation);
  }
  const initialPads = allocation.pads;
  const runtime: UnitRuntime[] = input.units.map(state => ({
    state,
    entry: fleetById.get(state.fleetEntryId)!,
    profile: profileByEntry.get(state.fleetEntryId) ?? null,
    parkingStartAtS: 0,
    currentPadIndex: initialPads.get(state.unitId)!,
  }));

  const refs: RefStream = { index: 0 };
  const accepted: AcceptedAssignment[] = [];
  const unassigned: UnassignedOrder[] = [];
  const sealedBids: SealedBidSummary[] = [];
  const plannedChargingStops: PlannedChargingStop[] = [];
  const routeReferences: Record<string, DispatchRouteLeg> = {};
  const facilityById = new Map(scenario.facilities.map(facility => [facility.id, facility]));
  const hubWindows = new Map<string, { start: number; end: number; kg: number }[]>();
  const hubTotals = new Map<string, number>();
  const acceptedPads: PadInterval[] = input.units.map(state => ({
    facilityId: state.atFacilityId, padIndex: initialPads.get(state.unitId)!,
    start: 0, end: FOREVER, unitId: state.unitId,
  }));
  const horizonHours = orders.length === 0 ? 0
    : Math.max(0, Math.max(...orders.map(order => order.deliverByS))
      - Math.min(...orders.map(order => order.releaseAtS))) / 3600;

  for (const order of orders) {
    // Hub storage and throughput are enforced against the parcel's hub: the
    // throughput rate is checked on the cumulative volume over the horizon, and
    // storage on the actual dwell window of the winning mission below.
    const hubId = effectiveHubHandoff(order, facilityById);
    const hub = facilityById.get(hubId);
    if (hub === undefined || hub.cargo === null) {
      throw new Error(`订单 ${order.id} 的中转站 ${hubId} 缺少货站能力`);
    }
    const cargo = hub.cargo;
    const rateHours = horizonHours > 0 ? horizonHours
      : (order.deliverByS - order.releaseAtS) / 3600;
    const prospectiveTotal = (hubTotals.get(hubId) ?? 0) + order.cargoKg;
    if (prospectiveTotal > cargo.throughputPerHourKg * rateHours + EPSILON) {
      unassigned.push({ orderId: order.id, primaryReason: "hub_throughput_exceeded",
        detail: `中转站 ${hubId} 在 ${rateHours.toFixed(3)}h 内的处理量 ${prospectiveTotal.toFixed(3)}kg `
          + `超过处理能力 ${cargo.throughputPerHourKg}kg/h`, perUnitFailures: [] });
      continue;
    }

    const attempts: UnitAttempt[] = runtime.map(unit => attemptForUnit(unit, order, scenario,
      input.routeEstimator, refs, plannedChargingStops, acceptedPads));

    if (distributed) {
      for (const attempt of attempts) {
        sealedBids.push(attempt.feasible
          ? { orderId: order.id, unitId: attempt.unitId, fleetEntryId: attempt.fleetEntryId, feasible: true,
              estimatedDeliveryS: attempt.arrivalAtS, estimatedEnergyWh: attempt.energyConsumedWh,
              plannedChargingStopCount: attempt.chargingStops.length, reason: null }
          : { orderId: order.id, unitId: attempt.unitId, fleetEntryId: attempt.fleetEntryId, feasible: false,
              estimatedDeliveryS: null, estimatedEnergyWh: null,
              plannedChargingStopCount: 0, reason: attempt.failure.reason });
      }
    }

    // Only the deterministic winner's routes and charging stops are part of the
    // plan; rival feasible attempts are discarded even though the route authority
    // was consulted for them during evaluation.
    const feasible = attempts.filter((attempt): attempt is FeasibleAttempt => attempt.feasible);
    const winner = feasible.length === 0 ? null : [...feasible].sort(compareArrival)[0]!;
    if (winner === null) {
      const reason = headlineReason(attempts);
      unassigned.push({
        orderId: order.id,
        primaryReason: reason,
        detail: `${order.sourceFacilityId}→${order.destinationFacilityId} 无单位可执行`,
        perUnitFailures: attempts
          .filter((attempt): attempt is InfeasibleAttempt => !attempt.feasible)
          .map(attemptId => attemptId.failure),
      });
      continue;
    }

    // Hub storage is enforced on the winning mission's actual dwell window: a
    // midpoint hub holds the parcel only from arrival to departure, not for the
    // whole release-to-deadline interval, so overlapping orders are compared on
    // their real shared occupancy.
    const dwell = hubDwellWindow(hubId, order, winner);
    if (hubStorageExceeded(cargo.storageCapacityKg, dwell, hubWindows.get(hubId) ?? [])) {
      unassigned.push({ orderId: order.id, primaryReason: "hub_storage_exceeded",
        detail: `中转站 ${hubId} 在任务实际停留 ${Math.max(0, dwell.end - dwell.start).toFixed(1)}s 内`
          + `并发存储超过存储容量 ${cargo.storageCapacityKg}kg`,
        perUnitFailures: [] });
      continue;
    }

    for (const leg of [winner.routeToPickup, winner.routeFromPickupToDestination, ...winner.routeLegs]) {
      routeReferences[leg.reference] = leg;
    }
    for (const stop of winner.chargingStops) {
      routeReferences[stop.routeToCharger.reference] = stop.routeToCharger;
      plannedChargingStops.push(stop);
    }
    const windows = hubWindows.get(hubId) ?? [];
    windows.push(dwell);
    hubWindows.set(hubId, windows);
    hubTotals.set(hubId, prospectiveTotal);

    const assigned = runtime.find(unit => unit.state.unitId === winner.unitId)!;
    // The winning unit leaves its CURRENT open-ended parking interval (the unit's
    // last `[.., FOREVER)` dwell) and parks on the mission's actual pad rectangles
    // (already allocated to deterministic pad indices) for its declared dwells.
    // Its PRIOR completed FINITE reservations (parking and intermediate handling
    // dwells) are retained verbatim: erasing historical finite occupancies would
    // allow later assignment of another unit to travel inside a pad time the
    // winner physically held, a historical double booking.
    acceptedPads.splice(0, acceptedPads.length,
      ...acceptedPads.filter(interval =>
        interval.unitId !== winner.unitId || interval.end < FOREVER),
      ...winner.padReservations);
    assigned.state = {
      unitId: winner.unitId,
      fleetEntryId: winner.fleetEntryId,
      atFacilityId: order.destinationFacilityId,
      availableAtS: winner.arrivalAtS,
      batteryWh: winner.batteryAtArrivalWh,
    };
    assigned.parkingStartAtS = winner.arrivalAtS;
    assigned.currentPadIndex = winner.finalPadIndex;
    accepted.push({
      orderId: order.id,
      unitId: winner.unitId,
      fleetEntryId: winner.fleetEntryId,
      cargoKg: order.cargoKg,
      hubHandoffFacilityId: winner.hubHandoffFacilityId,
      routeToPickup: winner.routeToPickup,
      routeFromPickupToDestination: winner.routeFromPickupToDestination,
      routeLegs: winner.routeLegs,
      chargingStops: winner.chargingStops,
      departureAtS: winner.departureAtS,
      pickupAtS: winner.pickupAtS,
      arrivalAtS: winner.arrivalAtS,
      batteryAtPickupDepartureWh: winner.batteryAtPickupDepartureWh,
      batteryAtArrivalWh: winner.batteryAtArrivalWh,
      energyConsumedWh: winner.energyConsumedWh,
    });
  }

  const plan: DispatchPlan = {
    purpose: "selected-logistics-dispatch-plan",
    schema_version: CITY_SELECTED_DISPATCH_PLAN_SCHEMA,
    executable: false,
    provenance: {
      planner: "city-selected-dispatch",
      planSchema: CITY_SELECTED_DISPATCH_PLAN_SCHEMA,
      label: PLAN_LABEL,
      mode: algorithms.mode,
      assignment: algorithms.assignment,
      routing: algorithms.routing,
      charging: algorithms.charging,
      boundSelectedScene: {
        job_id: scenario.selectedScene.job_id,
        selection_sha256: scenario.selectedScene.selection_sha256,
        source_id: scenario.selectedScene.selection.source_id,
        source_sha256: scenario.selectedScene.source_sha256,
      },
      declaredUnits: input.units.map(declaredUnit => ({ ...declaredUnit })),
    },
    planningEpochS: orders.length === 0 ? 0 : orders[0]!.releaseAtS,
    ordersConsidered: orders.length,
    acceptedAssignments: accepted,
    unassignedOrders: unassigned,
    plannedChargingStops,
    routeReferences,
    sealedBids,
    unitFinalStates: runtime.map(runtimeUnit => ({
      ...runtimeUnit.state,
      currentPadIndex: runtimeUnit.currentPadIndex,
    })),
  };
  return deepFreeze(plan);
}
