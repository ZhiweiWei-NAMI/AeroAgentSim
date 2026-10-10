/** Strict authoring-only scenario contract for one selected OSM city.
 *
 * A city-selected-scenario binds exactly one SelectedSceneDraft — never the old
 * scenePath workspace — plus editable facilities, no-fly zones, fleet entries and
 * background demand counts. Every value is authoring input explicitly labelled
 * non-executable: the document carries no trajectories, orders, dynamics or
 * provider evidence, and saving it claims no collision or feasibility verification. */
import { parseSelectedSceneDraft, verifySelectedSceneDigest, type SelectedSceneDraft } from "./city-selected-draft";
import { CHARGER_SLOT_PITCH_M, FACILITY_MIN_DIMENSIONS, LANDING_PAD_LAYOUTS, maxLandingParkingSlots,
  type FacilityCargo, type FacilityCharging, type FacilityKind, type FacilityLanding, type FacilityPlacement } from "./city-facility-models";
import { validateAirspacePolygon, type PointXZ } from "./city-workspace-geometry";
import { parseStrictJson } from "./strict-json";

export const CITY_SELECTED_SCENARIO_SCHEMA = "aero-bench.city-selected-scenario/v3" as const;
export const CITY_SELECTED_SCENARIO_STORAGE_KEY = "aero-bench.city-selected-scenario.v3";

const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/;
const MODEL_ASSET_ID = /^model:[A-Za-z0-9][A-Za-z0-9_.:-]*$/;
const CURRENCY = /^[A-Z]{3}$/;

export type { FacilityCargo, FacilityCharging, FacilityKind, FacilityLanding, FacilityPlacement };

/** One authored physical facility. Capabilities are explicit and never inferred:
 * a landing pad declares its operational capacity, a hub declares cargo storage in
 * kilograms and throughput per hour, a charging capability declares slots, power
 * and a price with its own currency and unit. Charging is a capability that can be
 * attached to an eligible landing facility or supplied by a standalone ground
 * charger; it is never conflated with the physical facility itself. */
export interface SelectedScenarioFacility {
  id: string;
  name: string;
  kind: FacilityKind;
  /** Ground site or a rooftop site bound to one verified selected building. */
  placement: FacilityPlacement;
  /** Verified selected building identity for a rooftop site; null on the ground. */
  buildingId: string | null;
  /** Verified support height in metres (building roof top) for a rooftop site; null on the ground. */
  supportHeightM: number | null;
  position: PointXZ;
  rotationDeg: number;
  widthM: number;
  depthM: number;
  heightM: number;
  /** Landing-pad capability. Required for vertiport and hub; null for a standalone charger. */
  landing: FacilityLanding | null;
  /** Hub cargo storage/throughput capability. Required for hub; null otherwise. */
  cargo: FacilityCargo | null;
  /** Charging capability. Required for a standalone charger; optional attachment otherwise. */
  charging: FacilityCharging | null;
}

/** A no-fly volume in local east-south metres of the selected scene, with altitude in metres. */
export interface SelectedScenarioNoFlyZone {
  id: string;
  name: string;
  polygon: PointXZ[];
  floorM: number;
  ceilingM: number;
  startsAtS: number;
  endsAtS: number | null;
  source: { kind: "manual" | "geojson"; label: string; uri: string | null };
}

export interface SelectedScenarioFleetEntry {
  id: string;
  assetId: string;
  count: number;
  homeFacilityId: string | null;
  batteryWh: number;
  reserveRatio: number;
  maxPayloadKg: number;
}

/** Background demand counts only; counts can never create trajectories. */
export interface SelectedScenarioDemand {
  vehicles: number;
  pedestrians: number;
  bicycles: number;
}

export interface CitySelectedScenario {
  purpose: "selected-scenario-authoring";
  schema_version: typeof CITY_SELECTED_SCENARIO_SCHEMA;
  /** Explicit protocol-level non-executable label. */
  executable: false;
  /** The one selected city this scenario is authored against; bound by digest, never by scenePath. */
  selectedScene: SelectedSceneDraft;
  facilities: SelectedScenarioFacility[];
  noFlyZones: SelectedScenarioNoFlyZone[];
  fleet: SelectedScenarioFleetEntry[];
  demand: SelectedScenarioDemand;
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
function positiveInteger(value: unknown, label: string): number {
  const result = finite(value, label);
  if (!Number.isSafeInteger(result) || result < 1) throw new Error(`${label} 必须是正整数`);
  return result;
}
function ratio(value: unknown, label: string): number {
  const result = finite(value, label);
  if (result < 0 || result > 1) throw new Error(`${label} 必须在 0 与 1 之间`);
  return result;
}
function point2(value: unknown, label: string): PointXZ {
  const item = object(value, ["x", "z"], label);
  return { x: finite(item.x, `${label} x`), z: finite(item.z, `${label} z`) };
}
function list(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} 必须是数组`);
  return value;
}

function nullableObject(value: unknown, keys: readonly string[], label: string): Record<string, unknown> | null {
  return value === null ? null : object(value, keys, label);
}

function parseLandingCapability(value: unknown): FacilityLanding | null {
  const item = nullableObject(value, ["parkingSlots", "movementsPerHour"], "起降位能力");
  if (item === null) return null;
  return {
    parkingSlots: positiveInteger(item.parkingSlots, "并行起降位数量"),
    movementsPerHour: positive(item.movementsPerHour, "起降位处理能力（架次/小时）"),
  };
}

function parseCargoCapability(value: unknown): FacilityCargo | null {
  const item = nullableObject(value, ["storageCapacityKg", "throughputPerHourKg"], "货站能力");
  if (item === null) return null;
  return {
    storageCapacityKg: positive(item.storageCapacityKg, "货站存储容量（kg）"),
    throughputPerHourKg: positive(item.throughputPerHourKg, "货站处理能力（kg/小时）"),
  };
}

function parseChargingCapability(value: unknown): FacilityCharging | null {
  const item = nullableObject(value, ["slots", "powerW", "priceAmount", "priceCurrency", "priceUnit"], "充电能力");
  if (item === null) return null;
  if (item.priceUnit !== "kWh") throw new Error("充电价格单位必须为 kWh");
  const currency = string(item.priceCurrency, "充电价格货币");
  if (!CURRENCY.test(currency)) throw new Error("充电价格货币必须是三位大写字母代码");
  return {
    slots: positiveInteger(item.slots, "充电位数量"),
    powerW: positive(item.powerW, "充电功率（W）"),
    priceAmount: nonNegative(item.priceAmount, "充电价格金额"),
    priceCurrency: currency,
    priceUnit: "kWh",
  };
}

function parseFacility(value: unknown): SelectedScenarioFacility {
  const item = object(value, ["id", "name", "kind", "placement", "buildingId", "supportHeightM", "position",
    "rotationDeg", "widthM", "depthM", "heightM", "landing", "cargo", "charging"], "设施");
  if (item.kind !== "vertiport" && item.kind !== "hub" && item.kind !== "charger") throw new Error("设施类型无效");
  if (item.placement !== "ground" && item.placement !== "rooftop") throw new Error("设施放置类型无效");
  const kind: FacilityKind = item.kind;
  const placement: FacilityPlacement = item.placement;
  const facilityId = id(item.id, "设施 ID");
  const facility: SelectedScenarioFacility = {
    id: facilityId, name: string(item.name, "设施名称"), kind, placement,
    buildingId: item.buildingId === null ? null : id(item.buildingId, "设施绑定建筑 ID"),
    supportHeightM: item.supportHeightM === null ? null : positive(item.supportHeightM, "设施支撑高度（m）"),
    position: point2(item.position, "设施位置"),
    rotationDeg: finite(item.rotationDeg, "设施朝向（deg）"),
    widthM: positive(item.widthM, "设施宽度（m）"), depthM: positive(item.depthM, "设施进深（m）"),
    heightM: positive(item.heightM, "设施高度（m）"),
    landing: parseLandingCapability(item.landing),
    cargo: parseCargoCapability(item.cargo),
    charging: parseChargingCapability(item.charging),
  };
  const minimum = FACILITY_MIN_DIMENSIONS[kind];
  if (facility.widthM < minimum.widthM || facility.depthM < minimum.depthM
    || facility.heightM < minimum.heightM) {
    throw new Error(`设施 ${facility.id} 小于 ${kind} 素材的最小尺寸`);
  }
  // Placement rules: rooftop sites exist only for vertiports and must bind a verified building.
  if (placement === "rooftop") {
    if (kind !== "vertiport") throw new Error(`设施 ${facility.id}：仅起降点可放置在屋顶`);
    if (facility.buildingId === null || facility.supportHeightM === null) {
      throw new Error(`设施 ${facility.id}：屋顶放置必须绑定建筑 ID 与核验支撑高度`);
    }
  } else if (facility.buildingId !== null || facility.supportHeightM !== null) {
    throw new Error(`设施 ${facility.id}：地面放置不得绑定建筑 ID 或支撑高度`);
  }
  // Capability rules keep the physical facility distinct from charging capability.
  if (kind === "vertiport") {
    if (facility.landing === null) throw new Error(`设施 ${facility.id}：起降点必须声明起降位能力`);
    if (facility.cargo !== null) throw new Error(`设施 ${facility.id}：起降点不得声明货站能力`);
  } else if (kind === "hub") {
    if (placement !== "ground") throw new Error(`设施 ${facility.id}：物流中转站只能放置在地面`);
    if (facility.landing === null) throw new Error(`设施 ${facility.id}：物流中转站必须声明起降位能力`);
    if (facility.cargo === null) throw new Error(`设施 ${facility.id}：物流中转站必须声明货站能力`);
  } else {
    if (placement !== "ground") throw new Error(`设施 ${facility.id}：独立充电站只能放置在地面`);
    if (facility.landing !== null || facility.cargo !== null) {
      throw new Error(`设施 ${facility.id}：独立充电站不声明起降位或货站能力`);
    }
    if (facility.charging === null) throw new Error(`设施 ${facility.id}：独立充电站必须声明充电能力`);
  }
  // Carrier counts must physically fit the declared footprint. A landing
  // capability carries `parkingSlots` concurrent pad rectangles; the single-row
  // layout `(n-1)*pitch + padWidthM <= widthM` must hold so the scheduler count
  // and the physical pad geometry always agree. A standalone charger model holds
  // at most three 2.2 m-wide stations.
  if (facility.landing !== null) {
    const layout = LANDING_PAD_LAYOUTS[facility.kind === "vertiport" ? "vertiport" : "hub"];
    const limit = maxLandingParkingSlots(facility.kind === "vertiport" ? "vertiport" : "hub", facility.widthM);
    if (facility.landing.parkingSlots > limit) {
      throw new Error(`设施 ${facility.id}：并行起降位 ${facility.landing.parkingSlots} 超出 ${facility.widthM}m 宽场地`
        + `（${layout.widthM}m 起降位按 ${layout.pitchM}m 间距单行布置，最多 ${limit} 个）`);
    }
  }
  if (facility.charging !== null
      && facility.charging.slots * CHARGER_SLOT_PITCH_M > facility.widthM + 1e-9) {
    throw new Error(`设施 ${facility.id}：充电位数量 ${facility.charging.slots} 超出 ${facility.widthM}m 宽的场地`);
  }
  if (facility.kind === "charger" && facility.charging !== null && facility.charging.slots > 3) {
    throw new Error(`设施 ${facility.id}：独立充电站模型最多容纳 3 个充电位`);
  }
  return facility;
}

function parseNoFlyZone(value: unknown): SelectedScenarioNoFlyZone {
  const item = object(value, ["id", "name", "polygon", "floorM", "ceilingM", "startsAtS", "endsAtS", "source"], "禁飞区");
  const origin = object(item.source, ["kind", "label", "uri"], "禁飞区来源");
  if (origin.kind !== "manual" && origin.kind !== "geojson") throw new Error("禁飞区来源类型无效");
  const zone: SelectedScenarioNoFlyZone = {
    id: id(item.id, "禁飞区 ID"), name: string(item.name, "禁飞区名称"),
    polygon: list(item.polygon, "禁飞区多边形").map((point, index) => point2(point, `禁飞区顶点 ${index}`)),
    floorM: nonNegative(item.floorM, "禁飞区下限（m）"),
    ceilingM: positive(item.ceilingM, "禁飞区上限（m）"),
    startsAtS: nonNegative(item.startsAtS, "禁飞区开始时间（s）"),
    endsAtS: item.endsAtS === null ? null : positive(item.endsAtS, "禁飞区结束时间（s）"),
    source: { kind: origin.kind, label: string(origin.label, "禁飞区来源标识"),
      uri: origin.uri === null ? null : string(origin.uri, "禁飞区来源 URI") },
  };
  if (zone.endsAtS !== null && zone.endsAtS <= zone.startsAtS) {
    throw new Error(`禁飞区 ${zone.id} 的结束时间必须晚于开始时间`);
  }
  const geometryIssue = validateAirspacePolygon(zone)[0];
  if (geometryIssue !== undefined) throw new Error(`禁飞区 ${zone.id}：${geometryIssue.message}`);
  return zone;
}

function parseFleetEntry(value: unknown): SelectedScenarioFleetEntry {
  const item = object(value, ["id", "assetId", "count", "homeFacilityId", "batteryWh", "reserveRatio",
    "maxPayloadKg"], "机队");
  const assetId = string(item.assetId, "机队资产 ID");
  if (!MODEL_ASSET_ID.test(assetId)) throw new Error("机队资产 ID 无效");
  return {
    id: id(item.id, "机队 ID"), assetId,
    count: positiveInteger(item.count, "机队数量"),
    homeFacilityId: item.homeFacilityId === null ? null : id(item.homeFacilityId, "机队驻地设施 ID"),
    batteryWh: positive(item.batteryWh, "电池电量（Wh）"),
    reserveRatio: ratio(item.reserveRatio, "备用比例"),
    maxPayloadKg: nonNegative(item.maxPayloadKg, "最大载荷（kg）"),
  };
}

/** Validate one authoring scenario. Structural validity only: no placement, collision or feasibility claim. */
export function parseCitySelectedScenario(value: unknown): CitySelectedScenario {
  const item = object(value, ["purpose", "schema_version", "executable", "selectedScene", "facilities",
    "noFlyZones", "fleet", "demand"], "选城场景");
  if (item.purpose !== "selected-scenario-authoring") throw new Error("选城场景 purpose 无效");
  if (item.schema_version !== CITY_SELECTED_SCENARIO_SCHEMA) throw new Error("选城场景版本无效");
  if (item.executable !== false) throw new Error("选城场景必须显式标记为不可执行");
  const demand = object(item.demand, ["vehicles", "pedestrians", "bicycles"], "背景需求");
  const scenario: CitySelectedScenario = {
    purpose: "selected-scenario-authoring",
    schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
    executable: false,
    selectedScene: parseSelectedSceneDraft(item.selectedScene),
    facilities: list(item.facilities, "设施列表").map(parseFacility),
    noFlyZones: list(item.noFlyZones, "禁飞区列表").map(parseNoFlyZone),
    fleet: list(item.fleet, "机队列表").map(parseFleetEntry),
    demand: {
      vehicles: nonNegativeInteger(demand.vehicles, "背景车辆数"),
      pedestrians: nonNegativeInteger(demand.pedestrians, "背景行人数量"),
      bicycles: nonNegativeInteger(demand.bicycles, "背景自行车数量"),
    },
  };
  for (const [entries, label] of [
    [scenario.facilities, "设施"], [scenario.noFlyZones, "禁飞区"], [scenario.fleet, "机队"],
  ] as const) {
    const seen = new Set<string>();
    for (const entry of entries) {
      if (seen.has(entry.id)) throw new Error(`${label} ID 重复：${entry.id}`);
      seen.add(entry.id);
    }
  }
  const facilities = new Set(scenario.facilities.map(facility => facility.id));
  for (const entry of scenario.fleet) {
    if (entry.homeFacilityId !== null && !facilities.has(entry.homeFacilityId)) {
      throw new Error(`机队 ${entry.id} 的 homeFacilityId 引用未知设施：${entry.homeFacilityId}`);
    }
  }
  return scenario;
}

/** Empty authoring default bound to exactly one supplied selected-scene draft. */
export async function createDefaultCitySelectedScenario(draft: SelectedSceneDraft): Promise<CitySelectedScenario> {
  const scenario = parseCitySelectedScenario({
    purpose: "selected-scenario-authoring",
    schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
    executable: false,
    selectedScene: draft,
    facilities: [], noFlyZones: [], fleet: [],
    demand: { vehicles: 0, pedestrians: 0, bicycles: 0 },
  });
  const issues = await verifySelectedSceneDigest(scenario.selectedScene);
  if (issues.length) throw new Error(issues.join("；"));
  return scenario;
}

/** Import verifies the bound selected-scene digest; a tampered selection is rejected, never re-bound. */
export async function importCitySelectedScenario(json: string): Promise<CitySelectedScenario> {
  const scenario = parseCitySelectedScenario(parseStrictJson(json));
  const issues = await verifySelectedSceneDigest(scenario.selectedScene);
  if (issues.length) throw new Error(issues.join("；"));
  return scenario;
}

export function exportCitySelectedScenario(scenario: CitySelectedScenario): string {
  return JSON.stringify(parseCitySelectedScenario(scenario), null, 2);
}

export function loadCitySelectedScenario(): Promise<CitySelectedScenario | null> {
  const stored = window.localStorage.getItem(CITY_SELECTED_SCENARIO_STORAGE_KEY);
  return stored === null ? Promise.resolve(null) : importCitySelectedScenario(stored);
}

/** Authoring save only: verifies structure and the selected-scene digest, and claims no
 * collision or feasibility verification. */
export async function saveCitySelectedScenario(scenario: CitySelectedScenario): Promise<void> {
  const valid = parseCitySelectedScenario(scenario);
  const issues = await verifySelectedSceneDigest(valid.selectedScene);
  if (issues.length) throw new Error(issues.join("；"));
  window.localStorage.setItem(CITY_SELECTED_SCENARIO_STORAGE_KEY, JSON.stringify(valid));
}
