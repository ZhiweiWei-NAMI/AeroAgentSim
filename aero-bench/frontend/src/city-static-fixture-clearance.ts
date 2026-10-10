import * as THREE from "three";
import type { StreetLampLocation } from "./city-street-clearance";
import { assertNotAborted, readBoundedResponse } from "./verified-bytes";

export interface StaticFixtureClearanceSources {
  readonly objects_json_sha256: string;
  readonly building_render_manifest_sha256: string;
  readonly road_preview_sha256: string;
  readonly traffic_preview_sha256: string;
  readonly signal_model_sha256: string;
  readonly street_lamp_model_sha256: string;
  readonly source_network_sha256: string;
}

interface SignalLocation {
  readonly id: string; readonly tls: string; readonly link: number;
  readonly x: number; readonly z: number; readonly heading: number;
}

interface FixtureOmission<T> {
  readonly source_index: number;
  readonly source_location: T;
  readonly reason: "fixture_intersects_source_building";
  readonly hits: readonly { readonly building_id: string; readonly overlap_m2: number }[];
}

export type StaticSignalFixtureOmission = FixtureOmission<SignalLocation>;

export interface StaticFixtureClearance {
  readonly schema_version: "aero-bench.city-static-fixture-clearance/v1";
  readonly source_kind: "canonical-building-footprints-and-transformed-fixture-triangles";
  readonly policy: "omit-conflicting-render-fixtures-preserve-source-traffic";
  readonly sources: StaticFixtureClearanceSources;
  readonly source_inventories: {
    readonly signals: readonly SignalLocation[];
    readonly street_lamps: readonly StreetLampLocation[];
  };
  readonly omitted_signals: readonly StaticSignalFixtureOmission[];
  readonly omitted_street_lamps: readonly FixtureOmission<StreetLampLocation>[];
  readonly statistics: {
    readonly source_buildings: number;
    readonly source_signals: number;
    readonly source_street_lamps: number;
    readonly omitted_source_signals: number;
    readonly omitted_source_street_lamps: number;
  };
}

export interface StaticFixtureClearanceRef {
  readonly url: string;
  readonly sha256: string;
  readonly size_bytes: number;
}

export interface AppliedStaticFixtureClearance {
  readonly omittedSignalIds: readonly string[];
  readonly sourceSignalCount: number;
  readonly signalFixtureCount: number;
  readonly sourceLampCount: number;
  readonly previousLampCount: number;
  readonly lampFixtureCount: number;
  readonly omittedDisplayedLampSourceIndices: readonly number[];
  readonly omittedSourceLampIndices: readonly number[];
}

function stableJson(value: unknown): string {
  return JSON.stringify(value, (_key, member: unknown) => {
    if (member !== null && typeof member === "object" && !Array.isArray(member)) {
      const object = member as Record<string, unknown>;
      return Object.fromEntries(Object.keys(object).sort().map(key => [key, object[key]]));
    }
    return member;
  });
}

/** A declared source signal keeps its TLS samples even when its fixture is omitted. */
export function signalFixtureIsOmitted(object: THREE.Object3D): boolean {
  return object.userData.staticFixtureOmission !== undefined;
}

export function validateStaticFixtureClearance(value: unknown,
  expected: StaticFixtureClearanceSources): StaticFixtureClearance {
  const data = value as StaticFixtureClearance | null;
  if (data?.schema_version !== "aero-bench.city-static-fixture-clearance/v1"
      || data.source_kind !== "canonical-building-footprints-and-transformed-fixture-triangles"
      || data.policy !== "omit-conflicting-render-fixtures-preserve-source-traffic") {
    throw new Error("Static fixture clearance has an invalid source or policy");
  }
  const keys: readonly (keyof StaticFixtureClearanceSources)[] = ["objects_json_sha256",
    "building_render_manifest_sha256", "road_preview_sha256", "traffic_preview_sha256",
    "signal_model_sha256", "street_lamp_model_sha256", "source_network_sha256"];
  if (keys.some(key => !/^[a-f0-9]{64}$/.test(expected[key]) || data.sources?.[key] !== expected[key])) {
    throw new Error("Static fixture clearance source digest differs from the loaded scene");
  }
  if (!Array.isArray(data.source_inventories?.signals) || !Array.isArray(data.source_inventories.street_lamps)
      || !Array.isArray(data.omitted_signals) || !Array.isArray(data.omitted_street_lamps)) {
    throw new Error("Static fixture clearance lacks complete source inventories");
  }
  const signals = data.source_inventories.signals, lamps = data.source_inventories.street_lamps;
  if (signals.some(signal => !signal.id || !signal.tls || !Number.isSafeInteger(signal.link) || signal.link < 0
        || ![signal.x, signal.z, signal.heading].every(Number.isFinite))
      || new Set(signals.map(signal => signal.id)).size !== signals.length
      || lamps.some(lamp => ![lamp.x, lamp.z, lamp.rotation_deg].every(Number.isFinite))) {
    throw new Error("Static fixture clearance source locations are invalid");
  }
  const check = <T>(omissions: readonly FixtureOmission<T>[], inventory: readonly T[]): void => {
    if (new Set(omissions.map(item => item.source_index)).size !== omissions.length
        || omissions.some(item => !Number.isSafeInteger(item.source_index) || item.source_index < 0
          || item.source_index >= inventory.length || item.reason !== "fixture_intersects_source_building"
          || stableJson(item.source_location) !== stableJson(inventory[item.source_index])
          || !Array.isArray(item.hits) || item.hits.length === 0
          || item.hits.some(hit => !hit.building_id || !Number.isFinite(hit.overlap_m2) || hit.overlap_m2 <= 0))) {
      throw new Error("Static fixture omission differs from its source inventory or measured contact");
    }
  };
  check(data.omitted_signals, signals); check(data.omitted_street_lamps, lamps);
  if (!Number.isSafeInteger(data.statistics?.source_buildings) || data.statistics.source_buildings <= 0
      || data.statistics.source_signals !== signals.length || data.statistics.source_street_lamps !== lamps.length
      || data.statistics.omitted_source_signals !== data.omitted_signals.length
      || data.statistics.omitted_source_street_lamps !== data.omitted_street_lamps.length) {
    throw new Error("Static fixture clearance statistics differ from its source inventory");
  }
  return data;
}

export async function loadStaticFixtureClearance(ref: StaticFixtureClearanceRef,
  expected: StaticFixtureClearanceSources, signal?: AbortSignal): Promise<StaticFixtureClearance> {
  assertNotAborted(signal);
  const response = await fetch(ref.url, { redirect: "error", signal });
  if (!response.ok) throw new Error(`Static fixture clearance request failed: ${response.status}`);
  const bytes = await readBoundedResponse(response, ref.size_bytes, "Static fixture clearance",
    signal, ref.size_bytes);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  assertNotAborted(signal);
  const actual = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
  if (actual !== ref.sha256) throw new Error("Static fixture clearance bytes differ from the declared digest");
  return validateStaticFixtureClearance(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)), expected);
}

/** Compact only rendered fixtures; never change authored locations or the SUMO data. */
export function applyStaticFixtureClearance(data: StaticFixtureClearance, roads: THREE.Group,
  signalGroup: THREE.Group, sourceSignals: readonly SignalLocation[],
  sourceLamps: readonly StreetLampLocation[]): AppliedStaticFixtureClearance {
  validateStaticFixtureClearance(data, data.sources);
  if (stableJson(sourceSignals) !== stableJson(data.source_inventories.signals)
      || stableJson(sourceLamps) !== stableJson(data.source_inventories.street_lamps)) {
    throw new Error("Static fixture clearance source inventory differs from the loaded road or traffic");
  }
  const renderedLamps = roads.userData.streetLampLocations as readonly StreetLampLocation[] | undefined;
  if (!Array.isArray(renderedLamps)) throw new Error("Static fixture clearance requires displayed street lamp locations");
  const sourceByLocation = new Map(sourceLamps.map((lamp, index) => [stableJson(lamp), index]));
  if (sourceByLocation.size !== sourceLamps.length) throw new Error("Static fixture source lamp locations are ambiguous");
  const renderedSources = renderedLamps.map(lamp => {
    const index = sourceByLocation.get(stableJson(lamp));
    if (index === undefined) throw new Error("Displayed street lamp has no declared source location");
    return index;
  });
  const omittedLampIndices = new Set(data.omitted_street_lamps.map(item => item.source_index));
  const kept = renderedSources.flatMap((sourceIndex, index) => omittedLampIndices.has(sourceIndex) ? [] : [index]);
  const instances: THREE.InstancedMesh[] = [];
  roads.traverse(node => {
    if (node instanceof THREE.InstancedMesh && (node.name.startsWith("street_light_8 ")
        || node.name === "Warm street lamp ground illumination")) instances.push(node);
  });
  if (renderedLamps.length > 0 && instances.length === 0
      || instances.some(node => node.count !== renderedLamps.length)) {
    throw new Error("Rendered street lamp instances differ from their declared locations");
  }
  const signalObjects = data.omitted_signals.map(item => {
    const matches = signalGroup.children.filter(node => node.userData.target?.kind === "traffic_signal"
      && node.userData.target.id === item.source_location.id);
    const object = matches[0];
    if (matches.length !== 1 || object === undefined || object.position.x !== item.source_location.x
        || object.position.z !== item.source_location.z
        || Math.abs(object.rotation.y + item.source_location.heading * Math.PI / 180) > 1e-12) {
      throw new Error(`Static signal fixture differs from its declared source pose: ${item.source_location.id}`);
    }
    return { object, item };
  });
  const matrix = new THREE.Matrix4(), color = new THREE.Color();
  for (const node of instances) {
    kept.forEach((sourceIndex, index) => {
      node.getMatrixAt(sourceIndex, matrix); node.setMatrixAt(index, matrix);
      if (node.instanceColor !== null) { node.getColorAt(sourceIndex, color); node.setColorAt(index, color); }
    });
    node.count = kept.length;
    node.instanceMatrix.needsUpdate = true;
    if (node.instanceColor !== null) node.instanceColor.needsUpdate = true;
    node.computeBoundingBox(); node.computeBoundingSphere();
  }
  for (const { object, item } of signalObjects) {
    object.userData.staticFixtureOmission = item satisfies StaticSignalFixtureOmission;
    object.visible = false;
  }
  const result: AppliedStaticFixtureClearance = {
    omittedSignalIds: data.omitted_signals.map(item => item.source_location.id),
    sourceSignalCount: sourceSignals.length, signalFixtureCount: sourceSignals.length - signalObjects.length,
    sourceLampCount: sourceLamps.length, previousLampCount: renderedLamps.length, lampFixtureCount: kept.length,
    omittedDisplayedLampSourceIndices: renderedSources.filter(index => omittedLampIndices.has(index)),
    omittedSourceLampIndices: [...omittedLampIndices],
  };
  roads.userData.streetLampLocations = kept.map(index => renderedLamps[index]!);
  roads.userData.streetLampCount = kept.length;
  roads.userData.staticFixtureClearance = result;
  signalGroup.userData.staticFixtureClearance = result;
  return result;
}
