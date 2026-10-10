/** Strict authoring-only logistics contract for one selected OSM city.
 *
 * A city-selected-logistics document binds exactly one SelectedSceneDraft —
 * the same identity as the current CitySelectedScenario v2 — and reuses that
 * scenario's facilities and fleet entries as the only valid referents. It
 * authors order requests, user-declared fleet performance profiles, algorithm
 * choices and the deterministic order-generation configuration. Every value is
 * authoring input explicitly labelled non-executable: the document carries no
 * order status, evidence, trajectories, dispatch, provider state or simulated
 * trajectory, and saving it claims no order creation, assignment or routing. A
 * profile's aircraft dynamics are user-declared estimates unless separately
 * verified and are never inferred from GLB display scale. */
import {
  parseSelectedSceneDraft, sameSelectedSceneDraft, verifySelectedSceneDigest,
  type SelectedSceneDraft,
} from "./city-selected-draft";
import type {
  CitySelectedScenario, SelectedScenarioFacility, SelectedScenarioFleetEntry,
} from "./city-selected-scenario";
import { parseStrictJson } from "./strict-json";

export const CITY_SELECTED_LOGISTICS_SCHEMA = "aero-bench.city-selected-logistics/v2" as const;
export const CITY_SELECTED_LOGISTICS_STORAGE_KEY = "aero-bench.city-selected-logistics.v2";

const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/;
const OCI_REFERENCE = /^\S+@sha256:[0-9a-f]{64}$/;

/** A vertiport (pickup/delivery pads) and a hub (sorting/shipping hall) can
 * exchange cargo with a drone; a charger is power equipment only. */
export function facilityPermitsCargoTransfer(facility: SelectedScenarioFacility): boolean {
  return facility.kind === "vertiport" || facility.kind === "hub";
}

/** One authoring order request. No status or evidence fields: the request only
 * declares demand; it never claims acceptance, assignment, pickup or delivery.
 *
 * Cargo must pass through a ground logistics hub. When neither endpoint is a hub,
 * `hubHandoffFacilityId` names the hub midpoint the parcel is handed to; a direct
 * vertiport-to-vertiport goods bypass is rejected. Ground logistics hubs as an
 * order origin/destination are accepted in this first version and are documented
 * as their own handoff. */
export interface LogisticsOrderRequest {
  readonly id: string;
  readonly sourceFacilityId: string;
  readonly destinationFacilityId: string;
  /** Hub cargo handoff/midpoint, or null when an endpoint is already a hub. */
  readonly hubHandoffFacilityId: string | null;
  readonly cargoKg: number;
  readonly releaseAtS: number;
  readonly deliverByS: number;
}

/** The hub through which the parcel must be handed off for this order. Throws when
 * neither endpoint is a hub and no hub midpoint is declared. */
export function effectiveHubHandoff(order: LogisticsOrderRequest,
                                    facilities: ReadonlyMap<string, SelectedScenarioFacility>): string {
  if (order.hubHandoffFacilityId !== null) return order.hubHandoffFacilityId;
  if (facilities.get(order.sourceFacilityId)?.kind === "hub") return order.sourceFacilityId;
  if (facilities.get(order.destinationFacilityId)?.kind === "hub") return order.destinationFacilityId;
  throw new Error(`订单 ${order.id} 缺少物流中转站交接：禁止起降点直达起降点的货物绕过`);
}

export interface AircraftBodyMetres {
  readonly xM: number;
  readonly yM: number;
  readonly zM: number;
}

/** User-declared estimate for one configured fleet entry, keyed by its ID.
 * Performance is never inferred from GLB display scale unless separately
 * verified. Not every fleet entry needs a profile until planning time. */
export interface FleetPerformanceProfile {
  readonly fleetEntryId: string;
  /** Who declared these numbers, e.g. "operator" or "厂商样张". */
  readonly sourceLabel: string;
  /** Where these estimates came from; a user-declared estimate unless separately verified. */
  readonly provenance: string;
  readonly aircraftBody: AircraftBodyMetres;
  readonly cruiseSpeedMps: number;
  readonly cruisePowerW: number;
  readonly hoverPowerW: number;
  readonly chargeEfficiency: number;
}

export type LogisticsMode = "centralized" | "distributed";
export type LogisticsAssignment = "nearest_feasible" | "sealed_bid" | "external";
export type LogisticsRouting = "grid_astar" | "external";
export type LogisticsCharging = "reserve_threshold" | "external";

export interface LogisticsAlgorithms {
  readonly mode: LogisticsMode;
  readonly assignment: LogisticsAssignment;
  readonly routing: LogisticsRouting;
  readonly charging: LogisticsCharging;
  /** Digest-pinned OCI reference of the external workload; required iff any
   * algorithm choice is "external". No Docker/Kubernetes executor fields live
   * in a domain document. */
  readonly externalImageRef: string | null;
  readonly parameters: Readonly<Record<string, string | number | boolean>>;
}

/** Deterministic order-generation configuration. This only describes input
 * demand; it never claims that orders were actually created or dispatched. */
export interface LogisticsOrderGeneration {
  readonly seed: number;
  readonly maxOrders: number;
  readonly startAtS: number;
  readonly endAtS: number;
  readonly cargoMinKg: number;
  readonly cargoMaxKg: number;
  readonly deadlineLeadS: number;
}

export interface CitySelectedLogistics {
  readonly purpose: "selected-logistics-authoring";
  readonly schema_version: typeof CITY_SELECTED_LOGISTICS_SCHEMA;
  /** Explicit protocol-level non-executable label. */
  readonly executable: false;
  /** The one selected city this logistics document is authored against;
   * identical to the current CitySelectedScenario's selectedScene, never by
   * scenePath. */
  readonly selectedScene: SelectedSceneDraft;
  readonly orders: LogisticsOrderRequest[];
  readonly performanceProfiles: FleetPerformanceProfile[];
  readonly algorithms: LogisticsAlgorithms;
  readonly orderGeneration: LogisticsOrderGeneration;
}

function object(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} 格式无效`);
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== keys.length || keys.some(key => !Object.hasOwn(result, key))) {
    throw new Error(`${label} 字段不符合协议`);
  }
  return result;
}
function string(value: unknown, label: string): string {
  if (typeof value !== "string" || !value) throw new Error(`${label} 无效`);
  return value;
}
function id(value: unknown, label: string): string {
  const text = string(value, label);
  if (!ID.test(text)) throw new Error(`${label} 无效`);
  return text;
}
function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${label} 必须是有限数值`);
  return value;
}
function positive(value: unknown, label: string): number {
  const result = finite(value, label);
  if (result <= 0) throw new Error(`${label} 必须大于 0`);
  return result;
}
function nonNegative(value: unknown, label: string): number {
  const result = finite(value, label);
  if (result < 0) throw new Error(`${label} 不能为负`);
  return result;
}
function nonNegativeInteger(value: unknown, label: string): number {
  const result = finite(value, label);
  if (!Number.isSafeInteger(result) || result < 0) throw new Error(`${label} 必须是非负整数`);
  return result;
}
function chargeEfficiency(value: unknown, label: string): number {
  const result = finite(value, label);
  if (result <= 0 || result > 1) throw new Error(`${label} 必须在 (0,1] 之间`);
  return result;
}
function list(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} 必须是数组`);
  return value;
}

function parseOrderRequest(value: unknown, facilities: ReadonlyMap<string, SelectedScenarioFacility>,
                          fleetEntries: readonly SelectedScenarioFleetEntry[], checkReferences: boolean): LogisticsOrderRequest {
  const item = object(value, ["id", "sourceFacilityId", "destinationFacilityId", "hubHandoffFacilityId", "cargoKg",
    "releaseAtS", "deliverByS"], "订单请求");
  const order: LogisticsOrderRequest = {
    id: id(item.id, "订单 ID"),
    sourceFacilityId: id(item.sourceFacilityId, "订单来源设施 ID"),
    destinationFacilityId: id(item.destinationFacilityId, "订单目的设施 ID"),
    hubHandoffFacilityId: item.hubHandoffFacilityId === null
      ? null : id(item.hubHandoffFacilityId, "订单中转站设施 ID"),
    cargoKg: positive(item.cargoKg, "订单货物重量（kg）"),
    releaseAtS: nonNegative(item.releaseAtS, "订单释放时间（s）"),
    deliverByS: finite(item.deliverByS, "订单交付期限（s）"),
  };
  if (order.sourceFacilityId === order.destinationFacilityId) throw new Error(`订单 ${order.id} 起终点必须不同`);
  if (order.hubHandoffFacilityId !== null
      && (order.hubHandoffFacilityId === order.sourceFacilityId
        || order.hubHandoffFacilityId === order.destinationFacilityId)) {
    throw new Error(`订单 ${order.id} 的中转站必须与起终点不同`);
  }
  if (order.deliverByS <= order.releaseAtS) {
    throw new Error(`订单 ${order.id} 的交付期限必须晚于释放时间`);
  }
  if (checkReferences) {
    const source = facilities.get(order.sourceFacilityId);
    if (source === undefined) throw new Error(`订单 ${order.id} 引用未知来源设施：${order.sourceFacilityId}`);
    const destination = facilities.get(order.destinationFacilityId);
    if (destination === undefined) {
      throw new Error(`订单 ${order.id} 引用未知目的设施：${order.destinationFacilityId}`);
    }
    if (!facilityPermitsCargoTransfer(source)) {
      throw new Error(`订单 ${order.id} 的来源设施 ${order.sourceFacilityId} 不允许货物交接`);
    }
    if (!facilityPermitsCargoTransfer(destination)) {
      throw new Error(`订单 ${order.id} 的目的设施 ${order.destinationFacilityId} 不允许货物交接`);
    }
    // Hub cargo handoff/midpoint semantics: a parcel must reach a hub, and a direct
    // vertiport-to-vertiport goods bypass is never accepted.
    let handoffId: string;
    if (order.hubHandoffFacilityId !== null) {
      const handoff = facilities.get(order.hubHandoffFacilityId);
      if (handoff === undefined) {
        throw new Error(`订单 ${order.id} 引用未知中转站设施：${order.hubHandoffFacilityId}`);
      }
      if (handoff.kind !== "hub") throw new Error(`订单 ${order.id} 的中转站必须是物流中转站`);
      handoffId = handoff.id;
    } else if (source.kind === "hub") {
      handoffId = source.id;
    } else if (destination.kind === "hub") {
      handoffId = destination.id;
    } else {
      throw new Error(`订单 ${order.id} 缺少物流中转站交接：禁止起降点直达起降点的货物绕过`);
    }
    const hub = facilities.get(handoffId)!;
    if (hub.cargo === null || order.cargoKg > hub.cargo.storageCapacityKg) {
      throw new Error(`订单 ${order.id} 的货物 ${order.cargoKg}kg 超出中转站 ${handoffId} 的存储容量`);
    }
    if (!fleetEntries.some(entry => entry.maxPayloadKg >= order.cargoKg)) {
      throw new Error(`订单 ${order.id} 的货物 ${order.cargoKg}kg 超出所有配置机队的最大载荷`);
    }
  }
  return order;
}

function parsePerformanceProfile(value: unknown, fleetById: ReadonlyMap<string, SelectedScenarioFleetEntry>,
                                 checkReferences: boolean):
    FleetPerformanceProfile {
  const item = object(value, ["fleetEntryId", "sourceLabel", "provenance", "aircraftBody",
    "cruiseSpeedMps", "cruisePowerW", "hoverPowerW", "chargeEfficiency"], "性能档案");
  const fleetEntryId = id(item.fleetEntryId, "性能档案机队 ID");
  if (checkReferences && !fleetById.has(fleetEntryId)) {
    throw new Error(`性能档案引用未知机队条目：${fleetEntryId}`);
  }
  const body = object(item.aircraftBody, ["xM", "yM", "zM"], "机体尺寸");
  return {
    fleetEntryId,
    sourceLabel: string(item.sourceLabel, "性能档案来源标签"),
    provenance: string(item.provenance, "性能档案出处"),
    aircraftBody: {
      xM: positive(body.xM, "机体长度（m）"),
      yM: positive(body.yM, "机体高度（m）"),
      zM: positive(body.zM, "机体宽度（m）"),
    },
    cruiseSpeedMps: positive(item.cruiseSpeedMps, "巡航速度（m/s）"),
    cruisePowerW: positive(item.cruisePowerW, "巡航功率（W）"),
    hoverPowerW: positive(item.hoverPowerW, "悬停功率（W）"),
    chargeEfficiency: chargeEfficiency(item.chargeEfficiency, "充电效率"),
  };
}

function scalarParameter(value: unknown, key: string): string | number | boolean {
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  throw new Error(`算法参数 ${key} 必须是有限原始值`);
}

function parameterRecord(value: unknown): Record<string, string | number | boolean> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("算法参数必须是对象");
  const record = value as Record<string, unknown>;
  const result: Record<string, string | number | boolean> = {};
  for (const [key, raw] of Object.entries(record)) {
    if (!ID.test(key)) throw new Error(`算法参数名无效：${key}`);
    result[key] = scalarParameter(raw, key);
  }
  return result;
}

function parseAlgorithms(value: unknown): LogisticsAlgorithms {
  const item = object(value, ["mode", "assignment", "routing", "charging", "externalImageRef",
    "parameters"], "算法选择");
  const mode = item.mode;
  if (mode !== "centralized" && mode !== "distributed") throw new Error("调度模式无效");
  const assignment = item.assignment;
  if (assignment !== "nearest_feasible" && assignment !== "sealed_bid" && assignment !== "external") {
    throw new Error("订单分配算法无效");
  }
  const routing = item.routing;
  if (routing !== "grid_astar" && routing !== "external") throw new Error("航路规划算法无效");
  const charging = item.charging;
  if (charging !== "reserve_threshold" && charging !== "external") throw new Error("能源策略无效");
  const externalImageRef = item.externalImageRef === null
    ? null : string(item.externalImageRef, "外部镜像引用");
  if (externalImageRef !== null && !OCI_REFERENCE.test(externalImageRef)) {
    throw new Error("外部镜像引用必须是指定摘要的 OCI 引用");
  }
  const external = assignment === "external" || routing === "external" || charging === "external";
  if (external && externalImageRef === null) {
    throw new Error("选择外部算法时必须提供指定摘要的外部镜像引用");
  }
  return {
    mode, assignment, routing, charging, externalImageRef,
    parameters: parameterRecord(item.parameters),
  };
}

function parseOrderGeneration(value: unknown): LogisticsOrderGeneration {
  const item = object(value, ["seed", "maxOrders", "startAtS", "endAtS", "cargoMinKg", "cargoMaxKg",
    "deadlineLeadS"], "订单生成配置");
  const generation: LogisticsOrderGeneration = {
    seed: nonNegativeInteger(item.seed, "随机种子"),
    maxOrders: nonNegativeInteger(item.maxOrders, "最大订单数"),
    startAtS: nonNegative(item.startAtS, "生成开始时间（s）"),
    endAtS: finite(item.endAtS, "生成结束时间（s）"),
    cargoMinKg: positive(item.cargoMinKg, "最小货物重量（kg）"),
    cargoMaxKg: finite(item.cargoMaxKg, "最大货物重量（kg）"),
    deadlineLeadS: positive(item.deadlineLeadS, "交付期限超前（s）"),
  };
  if (generation.endAtS <= generation.startAtS) throw new Error("生成结束时间必须晚于开始时间");
  if (generation.cargoMaxKg < generation.cargoMinKg) throw new Error("最大货物重量不得小于最小货物重量");
  return generation;
}

/** Validate one authoring logistics document against a bound current scenario.
 * Structural and reference validity only: no order creation, dispatch or
 * feasibility claim. The supplied scenario is the only valid referent for the
 * selected city, facilities and fleet entries, so a foreign selected job or a
 * changed city can never silently reuse stored orders. */
function parseLogistics(value: unknown, scenario: CitySelectedScenario,
                        checkReferences: boolean): CitySelectedLogistics {
  const item = object(value, ["purpose", "schema_version", "executable", "selectedScene", "orders",
    "performanceProfiles", "algorithms", "orderGeneration"], "物流草稿");
  if (item.purpose !== "selected-logistics-authoring") throw new Error("物流草稿 purpose 无效");
  if (item.schema_version !== CITY_SELECTED_LOGISTICS_SCHEMA) throw new Error("物流草稿版本无效");
  if (item.executable !== false) throw new Error("物流草稿必须显式标记为不可执行");
  const selectedScene = parseSelectedSceneDraft(item.selectedScene);
  if (!sameSelectedSceneDraft(scenario.selectedScene, selectedScene)) {
    throw new Error("物流草稿绑定的选城场景与当前城市不一致，拒绝复用外来订单");
  }
  const facilities = new Map(scenario.facilities.map(facility => [facility.id, facility]));
  const fleetById = new Map(scenario.fleet.map(entry => [entry.id, entry]));
  const orders: LogisticsOrderRequest[] = [];
  const seenOrderIds = new Set<string>();
  for (const raw of list(item.orders, "订单列表")) {
    const order = parseOrderRequest(raw, facilities, scenario.fleet, checkReferences);
    if (seenOrderIds.has(order.id)) throw new Error(`订单 ID 重复：${order.id}`);
    seenOrderIds.add(order.id);
    orders.push(order);
  }
  const performanceProfiles: FleetPerformanceProfile[] = [];
  const seenProfileEntries = new Set<string>();
  for (const raw of list(item.performanceProfiles, "性能档案列表")) {
    const profile = parsePerformanceProfile(raw, fleetById, checkReferences);
    if (seenProfileEntries.has(profile.fleetEntryId)) {
      throw new Error(`性能档案机队条目重复：${profile.fleetEntryId}`);
    }
    seenProfileEntries.add(profile.fleetEntryId);
    performanceProfiles.push(profile);
  }
  return {
    purpose: "selected-logistics-authoring",
    schema_version: CITY_SELECTED_LOGISTICS_SCHEMA,
    executable: false,
    selectedScene,
    orders,
    performanceProfiles,
    algorithms: parseAlgorithms(item.algorithms),
    orderGeneration: parseOrderGeneration(item.orderGeneration),
  };
}

export function parseCitySelectedLogistics(value: unknown, scenario: CitySelectedScenario): CitySelectedLogistics {
  return parseLogistics(value, scenario, true);
}

/** Read a structurally valid draft bound to the same city for reference repair.
 * Normal save, export, dispatch and import still require the strict parser. */
export function parseCitySelectedLogisticsForRepair(value: unknown, scenario: CitySelectedScenario): CitySelectedLogistics {
  return parseLogistics(value, scenario, false);
}

/** The bound scenario's fleet entries that still lack a performance profile.
 * The parser allows an empty profile list until planning time; this helper
 * exposes exactly which configured fleet entries have none. */
export function missingPerformanceProfiles(doc: CitySelectedLogistics,
                                           scenario: CitySelectedScenario): string[] {
  const profiled = new Set(doc.performanceProfiles.map(profile => profile.fleetEntryId));
  return scenario.fleet.map(entry => entry.id).filter(id => !profiled.has(id));
}

/** Empty authoring default bound to exactly one supplied current scenario. */
export async function createDefaultCitySelectedLogistics(scenario: CitySelectedScenario):
    Promise<CitySelectedLogistics> {
  const doc = parseCitySelectedLogistics({
    purpose: "selected-logistics-authoring",
    schema_version: CITY_SELECTED_LOGISTICS_SCHEMA,
    executable: false,
    selectedScene: scenario.selectedScene,
    orders: [],
    performanceProfiles: [],
    algorithms: {
      mode: "centralized", assignment: "nearest_feasible", routing: "grid_astar",
      charging: "reserve_threshold", externalImageRef: null, parameters: {},
    },
    orderGeneration: {
      seed: 0, maxOrders: 0, startAtS: 0, endAtS: 3600, cargoMinKg: 0.1, cargoMaxKg: 1,
      deadlineLeadS: 600,
    },
  }, scenario);
  const issues = await verifySelectedSceneDigest(doc.selectedScene);
  if (issues.length) throw new Error(issues.join("；"));
  return doc;
}

/** Import verifies the bound selected-scene digest; a tampered selection is
 * rejected, never re-bound. The current scenario must be supplied so that a
 * changed city cannot silently reuse foreign orders. */
export async function importCitySelectedLogistics(json: string, scenario: CitySelectedScenario):
    Promise<CitySelectedLogistics> {
  const doc = parseCitySelectedLogistics(parseStrictJson(json), scenario);
  const issues = await verifySelectedSceneDigest(doc.selectedScene);
  if (issues.length) throw new Error(issues.join("；"));
  return doc;
}

export function exportCitySelectedLogistics(doc: CitySelectedLogistics, scenario: CitySelectedScenario): string {
  return JSON.stringify(parseCitySelectedLogistics(doc, scenario), null, 2);
}

export function loadCitySelectedLogistics(scenario: CitySelectedScenario):
    Promise<CitySelectedLogistics | null> {
  const stored = window.localStorage.getItem(CITY_SELECTED_LOGISTICS_STORAGE_KEY);
  return stored === null ? Promise.resolve(null) : importCitySelectedLogistics(stored, scenario);
}

export async function loadCitySelectedLogisticsForRepair(scenario: CitySelectedScenario): Promise<CitySelectedLogistics | null> {
  const stored = window.localStorage.getItem(CITY_SELECTED_LOGISTICS_STORAGE_KEY);
  if (stored === null) return null;
  const doc = parseCitySelectedLogisticsForRepair(parseStrictJson(stored), scenario);
  const issues = await verifySelectedSceneDigest(doc.selectedScene);
  if (issues.length) throw new Error(issues.join("；"));
  return doc;
}

/** Authoring save only: verifies structure, the bound scenario identity and the
 * selected-scene digest, and claims no order creation or dispatch. */
export async function saveCitySelectedLogistics(doc: CitySelectedLogistics, scenario: CitySelectedScenario):
    Promise<void> {
  const valid = parseCitySelectedLogistics(doc, scenario);
  const issues = await verifySelectedSceneDigest(valid.selectedScene);
  if (issues.length) throw new Error(issues.join("；"));
  window.localStorage.setItem(CITY_SELECTED_LOGISTICS_STORAGE_KEY, JSON.stringify(valid));
}

/** Persist an intermediate reference repair without treating it as a valid plan input. */
export async function saveCitySelectedLogisticsRepair(doc: CitySelectedLogistics, scenario: CitySelectedScenario):
    Promise<void> {
  const valid = parseCitySelectedLogisticsForRepair(doc, scenario);
  const issues = await verifySelectedSceneDigest(valid.selectedScene);
  if (issues.length) throw new Error(issues.join("；"));
  window.localStorage.setItem(CITY_SELECTED_LOGISTICS_STORAGE_KEY, JSON.stringify(valid));
}
