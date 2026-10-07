import Ajv2020 from "ajv/dist/2020.js";
import { parseCityAuthoredLandscape, type CityAuthoredLandscapeItem } from "./city-authored-landscape";
import { migrateLegacyEnvironment, parseCityDraftEnvironment } from "./city-draft-environment";
import type {
  FleetPerformanceProfile, LogisticsOrderGeneration, LogisticsOrderRequest,
} from "./city-selected-logistics-draft";
import { validateAirspacePolygon } from "./city-workspace-geometry";
import type { CityDraftEnvironment } from "./city-draft-environment";

export const CITY_WORKSPACE_SCHEMA = "aero-bench.city-workspace/v3" as const;
export const CITY_WORKSPACE_V2_SCHEMA = "aero-bench.city-workspace/v2" as const;
export const CITY_WORKSPACE_LEGACY_SCHEMA = "aero-bench.city-workspace/v1" as const;
export const CITY_WORKSPACE_STORAGE_KEY = "aero-bench.city-workspace.v3";
export const CITY_WORKSPACE_V2_STORAGE_KEY = "aero-bench.city-workspace.v2";
export const CITY_WORKSPACE_LEGACY_STORAGE_KEY = "aero-bench.city-workspace.v1";

/** Models already loaded by the city presentation. The longest horizontal edge sets each
 * display scale; sizeM preserves the source GLB proportions and is not a measured manufacturer envelope. */
export const CITY_FLEET_ASSETS = [
  {
    id: "model:holybro-x500",
    label: "Holybro X500",
    url: "/models/city-runtime/holybro-x500-textured-preview.glb",
    previewLodUrl: "/models/city-runtime/holybro-x500-lod1.glb",
    sizeM: { x: 0.65, y: 1.585831 / 3.1 * 0.65, z: 0.65 },
    kind: "multirotor",
  },
  {
    id: "model:quadcopter-40-preview",
    label: "相机四旋翼",
    url: "/models/city-runtime/quadcopter-40-preview.glb",
    previewLodUrl: "/models/city-runtime/quadcopter-40-lod1.glb",
    sizeM: { x: 2.552401 / 3.1 * 0.85, y: 1.034362 / 3.1 * 0.85, z: 0.85 },
    kind: "multirotor",
  },
] as const;

export type CityFleetAsset = (typeof CITY_FLEET_ASSETS)[number];
export interface CityPoint2 { x: number; z: number }
export interface CityPoint3 { x: number; y: number; z: number }

export interface CityFacility {
  id: string;
  name: string;
  kind: "vertiport" | "hub" | "charger";
  position: CityPoint2;
  rotationDeg: number;
  widthM: number;
  depthM: number;
  heightM: number;
  capacity: number;
  chargingPowerW: number;
}

export interface CityAirspace {
  id: string;
  name: string;
  polygon: CityPoint2[];
  floorM: number;
  ceilingM: number;
  startsAtS: number;
  endsAtS: number | null;
  source: { kind: "manual" | "geojson"; label: string; uri?: string };
}

export interface CityWorkspaceV2Config {
  /** Authoring input only. This object is never an execution or verified run state. */
  purpose: "scenario-authoring";
  schema_version: typeof CITY_WORKSPACE_V2_SCHEMA;
  name: string;
  scenePath: string;
  seed: number;
  environment: CityDraftEnvironment;
  fleet: {
    id: string;
    assetId: string;
    count: number;
    homeFacilityId: string | null;
    batteryWh: number;
    reserveRatio: number;
  }[];
  /** Authoring demand. Existing replay frames can only be filtered; these counts cannot create trajectories. */
  traffic: { vehicles: number; pedestrians: number; bicycles: number };
  facilities: CityFacility[];
  /** Coordinates are local east-up-south metres from the selected scene pack. No origin is stored here. */
  airspace: CityAirspace[];
  algorithms: {
    mode: "centralized" | "distributed";
    assignment: "greedy" | "auction" | "min_cost_flow" | "external";
    routing: "astar" | "rrt_star" | "external";
    energy: "reserve_threshold" | "external";
    parameters: Record<string, string | number | boolean>;
  };
  /** A draft image reference does not establish executor feasibility or deployment readiness. */
  deployment: { executor: "docker_reference" | "kubernetes_cluster"; imageRef: string };
  /** atS schedules each event on the authoring timeline. */
  events: {
    id: string;
    atS: number;
    type: "order.created" | "weather.changed" | "airspace.activated" | "charger.outage" | "traffic.restricted";
    targetId: string;
    payload: Record<string, unknown>;
  }[];
  actionRules: {
    id: string;
    eventType: string;
    action: "accept_order" | "plan_route" | "follow_route" | "takeoff" | "land" | "start_charging" | "stop_charging";
    executorRole: "coordinator" | "vehicle";
    arguments: Record<string, unknown>;
  }[];
  stateKeyframes: { id: string; atS: number; entityId: string; position: CityPoint3; label: string }[];
  labelRules: {
    id: string;
    field: string;
    operator: "eq" | "lt" | "gt";
    value: string | number | boolean;
    label: string;
  }[];
}

/** Current authoring shape. These fields remain inputs, not execution evidence. */
export type CityWorkspaceConfig = Omit<CityWorkspaceV2Config, "schema_version"> & {
  schema_version: typeof CITY_WORKSPACE_SCHEMA;
  orders: LogisticsOrderRequest[];
  orderGeneration: LogisticsOrderGeneration;
  performanceProfiles: FleetPerformanceProfile[];
  authoredLandscape: CityAuthoredLandscapeItem[];
};

const point2 = {
  type: "object", additionalProperties: false, required: ["x", "z"],
  properties: { x: { type: "number" }, z: { type: "number" } },
} as const;
const point3 = {
  type: "object", additionalProperties: false, required: ["x", "y", "z"],
  properties: { x: { type: "number" }, y: { type: "number" }, z: { type: "number" } },
} as const;
const nonNegative = { type: "number", minimum: 0 } as const;
const positive = { type: "number", exclusiveMinimum: 0 } as const;
const nonNegativeInteger = { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER } as const;
const identifier = { type: "string", minLength: 1 } as const;
const logisticsIdentifier = { type: "string", pattern: "^[A-Za-z0-9][A-Za-z0-9_.:-]*$" } as const;
const parameterValue = { anyOf: [{ type: "string" }, { type: "number" }, { type: "boolean" }] } as const;

const workspaceV2Schema = {
  $id: CITY_WORKSPACE_V2_SCHEMA,
  type: "object", additionalProperties: false,
  required: ["purpose", "schema_version", "name", "scenePath", "seed", "environment", "fleet", "traffic",
    "facilities", "airspace", "algorithms", "deployment", "events", "actionRules", "stateKeyframes", "labelRules"],
  properties: {
    purpose: { const: "scenario-authoring" },
    schema_version: { const: CITY_WORKSPACE_V2_SCHEMA },
    name: identifier,
    scenePath: { type: "string", pattern: "^/city-presentation/[A-Za-z0-9_-]+\\.json$" },
    seed: nonNegativeInteger,
    environment: {
      type: "object", additionalProperties: false,
      required: ["cloudCover", "precipitation", "precipitationRateMmPerH", "visibilityM", "windMps",
        "windDirectionDeg", "timeOfDay", "reflectionsEnabled"],
      properties: {
        cloudCover: { type: "number", minimum: 0, maximum: 1 },
        precipitation: { enum: ["none", "drizzle", "rain", "snow", "hail"] },
        precipitationRateMmPerH: nonNegative,
        visibilityM: { type: "number", exclusiveMinimum: 0 },
        windMps: nonNegative,
        windDirectionDeg: { type: "number", minimum: 0, exclusiveMaximum: 360 },
        timeOfDay: { enum: ["day", "twilight", "night"] },
        reflectionsEnabled: { type: "boolean" },
      },
    },
    fleet: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["id", "assetId", "count", "homeFacilityId", "batteryWh", "reserveRatio"],
        properties: {
          id: identifier,
          assetId: { enum: CITY_FLEET_ASSETS.map(asset => asset.id) },
          count: { type: "integer", minimum: 1, maximum: Number.MAX_SAFE_INTEGER },
          homeFacilityId: { anyOf: [identifier, { type: "null" }] },
          batteryWh: { type: "number", exclusiveMinimum: 0 },
          reserveRatio: { type: "number", minimum: 0, maximum: 1 },
        },
      },
    },
    traffic: {
      type: "object", additionalProperties: false, required: ["vehicles", "pedestrians", "bicycles"],
      properties: { vehicles: nonNegativeInteger, pedestrians: nonNegativeInteger, bicycles: nonNegativeInteger },
    },
    facilities: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["id", "name", "kind", "position", "rotationDeg", "widthM", "depthM", "heightM",
          "capacity", "chargingPowerW"],
        properties: {
          id: identifier, name: identifier, kind: { enum: ["vertiport", "hub", "charger"] }, position: point2,
          rotationDeg: { type: "number" }, widthM: { type: "number", exclusiveMinimum: 0 },
          depthM: { type: "number", exclusiveMinimum: 0 }, heightM: { type: "number", exclusiveMinimum: 0 },
          capacity: { type: "integer", minimum: 1, maximum: Number.MAX_SAFE_INTEGER }, chargingPowerW: nonNegative,
        },
      },
    },
    airspace: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["id", "name", "polygon", "floorM", "ceilingM", "startsAtS", "endsAtS", "source"],
        properties: {
          id: identifier, name: identifier, polygon: { type: "array", minItems: 3, items: point2 },
          floorM: nonNegative, ceilingM: nonNegative, startsAtS: nonNegative,
          endsAtS: { anyOf: [nonNegative, { type: "null" }] },
          source: {
            type: "object", additionalProperties: false, required: ["kind", "label"],
            properties: { kind: { enum: ["manual", "geojson"] }, label: identifier, uri: identifier },
          },
        },
      },
    },
    algorithms: {
      type: "object", additionalProperties: false,
      required: ["mode", "assignment", "routing", "energy", "parameters"],
      properties: {
        mode: { enum: ["centralized", "distributed"] },
        assignment: { enum: ["greedy", "auction", "min_cost_flow", "external"] },
        routing: { enum: ["astar", "rrt_star", "external"] },
        energy: { enum: ["reserve_threshold", "external"] },
        parameters: { type: "object", propertyNames: identifier, additionalProperties: parameterValue },
      },
    },
    deployment: {
      type: "object", additionalProperties: false, required: ["executor", "imageRef"],
      properties: {
        executor: { enum: ["docker_reference", "kubernetes_cluster"] },
        imageRef: { type: "string", pattern: "^(?:|\\S+@sha256:[0-9a-f]{64})$" },
      },
    },
    events: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["id", "atS", "type", "targetId", "payload"],
        properties: {
          id: identifier, atS: nonNegative,
          type: { enum: ["order.created", "weather.changed", "airspace.activated", "charger.outage", "traffic.restricted"] },
          targetId: { type: "string" }, payload: { type: "object", additionalProperties: { $ref: "#/$defs/jsonValue" } },
        },
      },
    },
    actionRules: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["id", "eventType", "action", "executorRole", "arguments"],
        properties: {
          id: identifier, eventType: identifier,
          action: { enum: ["accept_order", "plan_route", "follow_route", "takeoff", "land", "start_charging", "stop_charging"] },
          executorRole: { enum: ["coordinator", "vehicle"] },
          arguments: { type: "object", additionalProperties: { $ref: "#/$defs/jsonValue" } },
        },
      },
    },
    stateKeyframes: {
      type: "array",
      items: {
        type: "object", additionalProperties: false, required: ["id", "atS", "entityId", "position", "label"],
        properties: { id: identifier, atS: nonNegative, entityId: identifier, position: point3, label: { type: "string" } },
      },
    },
    labelRules: {
      type: "array",
      items: {
        type: "object", additionalProperties: false, required: ["id", "field", "operator", "value", "label"],
        properties: {
          id: identifier, field: identifier, operator: { enum: ["eq", "lt", "gt"] },
          value: parameterValue, label: identifier,
        },
      },
    },
  },
  $defs: {
    jsonValue: {
      anyOf: [
        { type: "null" }, { type: "boolean" }, { type: "number" }, { type: "string" },
        { type: "array", items: { $ref: "#/$defs/jsonValue" } },
        { type: "object", additionalProperties: { $ref: "#/$defs/jsonValue" } },
      ],
    },
  },
} as const;

const workspaceSchema = {
  ...workspaceV2Schema,
  $id: CITY_WORKSPACE_SCHEMA,
  required: [...workspaceV2Schema.required,
    "orders", "orderGeneration", "performanceProfiles", "authoredLandscape"],
  properties: {
    ...workspaceV2Schema.properties,
    schema_version: { const: CITY_WORKSPACE_SCHEMA },
    orders: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["id", "sourceFacilityId", "destinationFacilityId", "hubHandoffFacilityId",
          "cargoKg", "releaseAtS", "deliverByS"],
        properties: {
          id: logisticsIdentifier, sourceFacilityId: logisticsIdentifier,
          destinationFacilityId: logisticsIdentifier,
          hubHandoffFacilityId: { anyOf: [logisticsIdentifier, { type: "null" }] },
          cargoKg: positive, releaseAtS: nonNegative, deliverByS: { type: "number" },
        },
      },
    },
    orderGeneration: {
      type: "object", additionalProperties: false,
      required: ["seed", "maxOrders", "startAtS", "endAtS", "cargoMinKg", "cargoMaxKg",
        "deadlineLeadS"],
      properties: {
        seed: nonNegativeInteger,
        maxOrders: { type: "integer", minimum: 0, maximum: 10_000 },
        startAtS: nonNegative, endAtS: { type: "number" },
        cargoMinKg: positive, cargoMaxKg: { type: "number" }, deadlineLeadS: positive,
      },
    },
    performanceProfiles: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["fleetEntryId", "sourceLabel", "provenance", "aircraftBody", "cruiseSpeedMps",
          "cruisePowerW", "hoverPowerW", "chargeEfficiency"],
        properties: {
          fleetEntryId: logisticsIdentifier, sourceLabel: identifier, provenance: identifier,
          aircraftBody: {
            type: "object", additionalProperties: false, required: ["xM", "yM", "zM"],
            properties: { xM: positive, yM: positive, zM: positive },
          },
          cruiseSpeedMps: positive, cruisePowerW: positive, hoverPowerW: positive,
          chargeEfficiency: { type: "number", exclusiveMinimum: 0, maximum: 1 },
        },
      },
    },
    authoredLandscape: {
      type: "array",
      items: {
        type: "object", additionalProperties: false,
        required: ["id", "label", "provenance", "kind", "polygon"],
        properties: {
          id: identifier, label: identifier, provenance: { const: "authored" },
          kind: { enum: ["green", "plaza", "planting_strip"] },
          polygon: { type: "array", minItems: 3, items: point2 },
        },
      },
    },
  },
} as const;

const ajv = new Ajv2020({ allErrors: true, strict: true });
const validateWorkspaceV2 = ajv.compile<CityWorkspaceV2Config>(workspaceV2Schema);
const validateWorkspace = ajv.compile<CityWorkspaceConfig>(workspaceSchema);

function requireJsonData(value: unknown, ancestors = new WeakSet<object>()): void {
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (Number.isFinite(value)) return;
    throw new TypeError("City workspace contains a non-finite number");
  }
  if (typeof value !== "object") throw new TypeError("City workspace contains a non-JSON value");
  const prototype = Object.getPrototypeOf(value);
  if (!Array.isArray(value) && prototype !== Object.prototype && prototype !== null) {
    throw new TypeError("City workspace contains a non-JSON object");
  }
  if (ancestors.has(value)) throw new TypeError("City workspace contains a circular reference");
  ancestors.add(value);
  if (Array.isArray(value)) {
    if (Object.keys(value).length !== value.length) throw new TypeError("City workspace contains an invalid JSON array");
    for (let index = 0; index < value.length; index++) {
      if (!Object.hasOwn(value, index)) throw new TypeError("City workspace contains a sparse array");
      requireJsonData(value[index], ancestors);
    }
  } else {
    for (const entry of Object.values(value)) requireJsonData(entry, ancestors);
  }
  ancestors.delete(value);
}

function requireUniqueIds(items: readonly { id: string }[], collection: string): void {
  const ids = new Set<string>();
  for (const item of items) {
    if (ids.has(item.id)) throw new TypeError(`City workspace ${collection} contains duplicate id ${item.id}`);
    ids.add(item.id);
  }
}

function validateWorkspaceCore(value: CityWorkspaceV2Config | CityWorkspaceConfig): void {
  // The schema bounds every weather number, but checkCityWeatherSettings stays the single
  // authority for precipitation/rate consistency and the shared renderer contract.
  parseCityDraftEnvironment(value.environment);
  for (const [items, label] of [
    [value.fleet, "fleet"], [value.facilities, "facilities"], [value.airspace, "airspace"],
    [value.events, "events"], [value.actionRules, "actionRules"],
    [value.stateKeyframes, "stateKeyframes"], [value.labelRules, "labelRules"],
  ] as const) requireUniqueIds(items, label);
  const facilities = new Map(value.facilities.map(facility => [facility.id, facility]));
  const airspace = new Set(value.airspace.map(zone => zone.id));
  for (const craft of value.fleet) {
    if (craft.homeFacilityId !== null && !facilities.has(craft.homeFacilityId)) {
      throw new TypeError(`City workspace fleet ${craft.id} references unknown home facility ${craft.homeFacilityId}`);
    }
  }
  for (const zone of value.airspace) {
    const geometryIssue = validateAirspacePolygon(zone)[0];
    if (geometryIssue !== undefined) {
      throw new TypeError(`City workspace airspace ${zone.id}: ${geometryIssue.message}`);
    }
    if (zone.endsAtS !== null && zone.endsAtS <= zone.startsAtS) {
      throw new TypeError(`City workspace airspace ${zone.id} must end after it starts`);
    }
  }
  for (const event of value.events) {
    if (event.type === "airspace.activated" && !airspace.has(event.targetId)) {
      throw new TypeError(`City workspace event ${event.id} references unknown airspace ${event.targetId}`);
    }
    if (event.type === "charger.outage" && facilities.get(event.targetId)?.kind !== "charger") {
      throw new TypeError(`City workspace event ${event.id} references unknown charger ${event.targetId}`);
    }
  }
}

/** Validate an exact v2 source before explicit migration. */
export function parseCityWorkspaceV2Config(value: unknown): CityWorkspaceV2Config {
  requireJsonData(value);
  if (!validateWorkspaceV2(value)) {
    throw new TypeError(`City workspace v2 validation failed: ${ajv.errorsText(validateWorkspaceV2.errors, { separator: "; " })}`);
  }
  validateWorkspaceCore(value);
  return value;
}

/** Validate one current authoring draft without converting it into a formal Domain Spec or run state. */
export function parseCityWorkspaceConfig(value: unknown): CityWorkspaceConfig {
  requireJsonData(value);
  if (!validateWorkspace(value)) {
    throw new TypeError(`City workspace validation failed: ${ajv.errorsText(validateWorkspace.errors, { separator: "; " })}`);
  }
  validateWorkspaceCore(value);
  requireUniqueIds(value.orders, "orders");
  const facilities = new Map(value.facilities.map(facility => [facility.id, facility]));
  for (const order of value.orders) {
    if (order.sourceFacilityId === order.destinationFacilityId) {
      throw new TypeError(`City workspace order ${order.id} source and destination must differ`);
    }
    if (order.deliverByS <= order.releaseAtS) {
      throw new TypeError(`City workspace order ${order.id} deadline must follow release time`);
    }
    const source = facilities.get(order.sourceFacilityId);
    const destination = facilities.get(order.destinationFacilityId);
    if (source === undefined || source.kind === "charger") {
      throw new TypeError(`City workspace order ${order.id} references invalid source facility ${order.sourceFacilityId}`);
    }
    if (destination === undefined || destination.kind === "charger") {
      throw new TypeError(`City workspace order ${order.id} references invalid destination facility ${order.destinationFacilityId}`);
    }
    if (order.hubHandoffFacilityId !== null) {
      if (order.hubHandoffFacilityId === order.sourceFacilityId
          || order.hubHandoffFacilityId === order.destinationFacilityId) {
        throw new TypeError(`City workspace order ${order.id} hub handoff must differ from its endpoints`);
      }
      if (facilities.get(order.hubHandoffFacilityId)?.kind !== "hub") {
        throw new TypeError(`City workspace order ${order.id} references invalid hub handoff ${order.hubHandoffFacilityId}`);
      }
    } else if (source.kind !== "hub" && destination.kind !== "hub") {
      throw new TypeError(`City workspace order ${order.id} requires a hub handoff`);
    }
  }
  if (value.orderGeneration.endAtS <= value.orderGeneration.startAtS) {
    throw new TypeError("City workspace order generation end must follow its start");
  }
  if (value.orderGeneration.cargoMaxKg < value.orderGeneration.cargoMinKg) {
    throw new TypeError("City workspace order generation cargo maximum is below its minimum");
  }
  const fleetIds = new Set(value.fleet.map(entry => entry.id));
  const profiled = new Set<string>();
  for (const profile of value.performanceProfiles) {
    if (!fleetIds.has(profile.fleetEntryId)) {
      throw new TypeError(`City workspace performance profile references unknown fleet ${profile.fleetEntryId}`);
    }
    if (profiled.has(profile.fleetEntryId)) {
      throw new TypeError(`City workspace performanceProfiles contains duplicate fleet ${profile.fleetEntryId}`);
    }
    profiled.add(profile.fleetEntryId);
  }
  return { ...value, authoredLandscape: parseCityAuthoredLandscape(value.authoredLandscape) };
}

/** Every value is a scenario authoring input; none is measured telemetry or a deployable run. */
export function createDefaultCityWorkspaceV2Config(): CityWorkspaceV2Config {
  return {
    purpose: "scenario-authoring",
    schema_version: CITY_WORKSPACE_V2_SCHEMA,
    name: "Shanghai Huangpu draft",
    scenePath: "/city-presentation/default-scene-v1.json",
    seed: 1,
    environment: {
      cloudCover: 0, precipitation: "none", precipitationRateMmPerH: 0, visibilityM: 10_000,
      windMps: 0, windDirectionDeg: 0, timeOfDay: "day", reflectionsEnabled: true,
    },
    fleet: [
      { id: "uav.01", assetId: CITY_FLEET_ASSETS[0].id, count: 1, homeFacilityId: null, batteryWh: 500, reserveRatio: 0.2 },
      { id: "uav.02", assetId: CITY_FLEET_ASSETS[1].id, count: 1, homeFacilityId: null, batteryWh: 500, reserveRatio: 0.2 },
    ],
    traffic: { vehicles: 0, pedestrians: 0, bicycles: 0 },
    facilities: [], airspace: [],
    algorithms: { mode: "centralized", assignment: "greedy", routing: "astar", energy: "reserve_threshold", parameters: {} },
    deployment: { executor: "docker_reference", imageRef: "" },
    events: [], actionRules: [], stateKeyframes: [], labelRules: [],
  };
}

function disabledOrderGeneration(seed: number): LogisticsOrderGeneration {
  return {
    seed,
    maxOrders: 0,
    startAtS: 0,
    endAtS: 3_600,
    cargoMinKg: 0.1,
    cargoMaxKg: 1,
    deadlineLeadS: 600,
  };
}

/** Explicit v2 → v3 migration. The v2 input is validated and left
 * untouched; no orders, profiles, or landscape are inferred. */
export function migrateCityWorkspaceV2ToV3(value: unknown): CityWorkspaceConfig {
  const current = parseCityWorkspaceV2Config(value);
  return parseCityWorkspaceConfig({
    ...current,
    schema_version: CITY_WORKSPACE_SCHEMA,
    orders: [],
    orderGeneration: disabledOrderGeneration(current.seed),
    performanceProfiles: [],
    authoredLandscape: [],
  });
}

export function createDefaultCityWorkspaceConfig(): CityWorkspaceConfig {
  return migrateCityWorkspaceV2ToV3(createDefaultCityWorkspaceV2Config());
}

export function importCityWorkspaceConfig(json: string): CityWorkspaceConfig {
  return parseCityWorkspaceConfig(JSON.parse(json) as unknown);
}

export function exportCityWorkspaceConfig(config: CityWorkspaceConfig): string {
  return JSON.stringify(parseCityWorkspaceConfig(config), null, 2);
}

/** Explicit v1 → v2 migration: the only legacy branch. `mood: "dusk"` becomes the
 * calibrated "twilight", "day" stays "day", and the weather fields are revalidated.
 * Nothing else is accepted; unknown drafts fail loudly instead of guessing. */
export function migrateCityWorkspaceV1(legacy: unknown): CityWorkspaceV2Config {
  requireJsonData(legacy);
  if (legacy === null || typeof legacy !== "object" || Array.isArray(legacy)) {
    throw new TypeError("Legacy city workspace must be an object");
  }
  const draft = legacy as Record<string, unknown>;
  if (draft.schema_version !== CITY_WORKSPACE_LEGACY_SCHEMA) {
    throw new TypeError(`City workspace migration expects ${CITY_WORKSPACE_LEGACY_SCHEMA}, got ${JSON.stringify(draft.schema_version)}`);
  }
  if (draft.purpose !== "scenario-authoring") {
    throw new TypeError("City workspace migration expects a scenario-authoring draft");
  }
  const { environment, ...rest } = draft;
  const migrated = { ...rest, schema_version: CITY_WORKSPACE_V2_SCHEMA,
    environment: parseCityDraftEnvironment(
      typeof environment === "object" && environment !== null
        ? migrateLegacyEnvironment(environment as Parameters<typeof migrateLegacyEnvironment>[0])
        : environment) };
  return parseCityWorkspaceV2Config(migrated);
}

/** Read the current v2 value or perform the one existing v1 migration. */
function loadCityWorkspaceV2Config(): CityWorkspaceV2Config | null {
  const stored = window.localStorage.getItem(CITY_WORKSPACE_V2_STORAGE_KEY);
  if (stored !== null) return parseCityWorkspaceV2Config(JSON.parse(stored) as unknown);
  const legacy = window.localStorage.getItem(CITY_WORKSPACE_LEGACY_STORAGE_KEY);
  if (legacy === null) return null;
  const migrated = migrateCityWorkspaceV1(JSON.parse(legacy) as unknown);
  window.localStorage.setItem(CITY_WORKSPACE_V2_STORAGE_KEY, JSON.stringify(migrated));
  return migrated;
}

/** Read current v3 first. Otherwise migrate the exact v2 source while leaving
 * its stored bytes untouched. */
export function loadCityWorkspaceConfig(): CityWorkspaceConfig | null {
  const stored = window.localStorage.getItem(CITY_WORKSPACE_STORAGE_KEY);
  if (stored !== null) return importCityWorkspaceConfig(stored);
  const v2 = loadCityWorkspaceV2Config();
  if (v2 === null) return null;
  const migrated = migrateCityWorkspaceV2ToV3(v2);
  window.localStorage.setItem(CITY_WORKSPACE_STORAGE_KEY, JSON.stringify(migrated));
  return migrated;
}

export function saveCityWorkspaceConfig(config: CityWorkspaceConfig): void {
  const valid = parseCityWorkspaceConfig(config);
  window.localStorage.setItem(CITY_WORKSPACE_STORAGE_KEY, JSON.stringify(valid));
}
