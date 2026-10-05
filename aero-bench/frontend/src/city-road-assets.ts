import type { CitySceneJsonAssetRef, CitySceneRoadAssets } from "./city-scene-config";
import type { BuildingRenderManifest } from "./city-building-renders";
import type { LoadedMeshPack } from "./osm2world/pack-loader";
import { sameJson, validateCanonicalCityRoadPayload } from "./city-roads";
import { cityVegetationRoadBinding, type CityVegetationEffectiveFixture, type CityVegetationRoadBinding } from "./city-vegetation-layer";
import { displaySurfaceSha256 } from "./city-surface-identity";
import { assertNotAborted, nextTask, readBoundedResponse } from "./verified-bytes";

type RoadAssetKind = "road" | "effective_fixtures" | "traffic" | "flight";
export interface CityRoadAssetPayloads {
  readonly road: unknown;
  readonly effective_fixtures: unknown;
  readonly traffic: unknown;
  readonly flight: unknown;
}
type PackIdentity = Pick<LoadedMeshPack, "manifest" | "manifestSha256">;

export interface CityRoadAssetsProgress {
  readonly completedBytes: number;
  readonly totalBytes: number;
  readonly verifiedFiles: number;
  readonly totalFiles: number;
}

/** Source fixtures that passed the effective-fixture gate; omitted ones are not drawn. */
export interface EffectiveFixtureSelection {
  readonly signalIds: ReadonlySet<string>;
  readonly streetLampIndices: ReadonlySet<number>;
  /** Model bytes the fixture geometry was measured from; the displayed models must match. */
  readonly signalModelSha256: string;
}

export interface VerifiedCityRoadAssets {
  readonly roadUrl: string;
  readonly trafficUrl: string;
  readonly flightUrl: string;
  readonly sourceContext: Readonly<Record<string, unknown>>;
  readonly displayedSurfaceSha256: string;
  readonly fixtures: EffectiveFixtureSelection;
  /** Planner inputs from the same verified road and effective-fixture bytes. */
  readonly vegetationRoadBinding: CityVegetationRoadBinding;
  dispose(): void;
}

interface RecordedTrafficCounts {
  readonly demand: { readonly observed: Readonly<Record<string, number>> };
  readonly frames: readonly {
    readonly vehicles: readonly (readonly [string, number, number, number, string, number?])[];
    readonly persons: readonly (readonly [string, number, number, number, number?])[];
  }[];
}

/** SUMO observed demand includes bicycles; replay counts come from retained identities. */
export function cityTrafficProvenance(data: RecordedTrafficCounts): {
  observedVehicles: number; observedBicycles: number; observedPersons: number;
  displayedVehicles: number; displayedBicycles: number; displayedPersons: number;
  omittedVehicles: number; note: string;
} {
  const observed = data.demand.observed;
  for (const key of ["vehicles", "bicycle", "persons"] as const) {
    if (!Number.isSafeInteger(observed[key]) || observed[key]! < 0) {
      throw new Error(`SUMO observed ${key} count is missing or invalid`);
    }
  }
  const vehicles = new Set<string>(), bicycles = new Set<string>(), persons = new Set<string>();
  for (const frame of data.frames) {
    for (const sample of frame.vehicles) {
      vehicles.add(sample[0]);
      if (sample[4] === "bicycle") bicycles.add(sample[0]);
    }
    for (const sample of frame.persons) persons.add(sample[0]);
  }
  if (observed.bicycle! > observed.vehicles! || vehicles.size > observed.vehicles!
      || bicycles.size > observed.bicycle! || persons.size > observed.persons!) {
    throw new Error("SUMO replay identities exceed observed demand");
  }
  const omittedVehicles = observed.vehicles! - vehicles.size;
  return {
    observedVehicles: observed.vehicles!, observedBicycles: observed.bicycle!, observedPersons: observed.persons!,
    displayedVehicles: vehicles.size, displayedBicycles: bicycles.size, displayedPersons: persons.size,
    omittedVehicles,
    note: `SUMO 实录 ${observed.vehicles} 辆（含自行车 ${observed.bicycle}）`
      + ` · 回放保留 ${vehicles.size} 辆（含自行车 ${bicycles.size}）、${persons.size} 名行人`
      + (omittedVehicles > 0 ? ` · ${omittedVehicles} 辆未显示` : ""),
  };
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`City road assets ${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

const FIXTURE_MODELS = {
  signal: "/models/incoming/furniture/glb/traffic_light_4.glb",
  street_lamp: "/models/incoming/furniture/glb/street_light_8.glb",
} as const;

/** Select the drawn fixtures from the effective inventory after checking it covers every source fixture once. */
function effectiveFixtureSelection(fixtures: Record<string, unknown>, road: Record<string, unknown>,
  traffic: Record<string, unknown>): EffectiveFixtureSelection {
  const inventories = record(fixtures.source_inventories, "effective fixture inventories");
  const signals = traffic.signals, lamps = road.street_lamps;
  if (!Array.isArray(signals) || !Array.isArray(lamps)
      || !sameJson(inventories.signals, signals) || !sameJson(inventories.street_lamps, lamps)) {
    throw new Error("City road assets effective fixtures differ from the recorded signals or road lamps");
  }
  const models = record(fixtures.models, "effective fixture models");
  for (const [kind, url] of Object.entries(FIXTURE_MODELS)) {
    const model = record(models[kind], `effective fixture ${kind} model`);
    if (model.url !== url || typeof model.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(model.sha256)) {
      throw new Error(`City road assets effective fixture ${kind} model differs from the displayed model`);
    }
  }
  const sources = { signal: signals, street_lamp: lamps } as const;
  const seen = { signal: new Set<number>(), street_lamp: new Set<number>() };
  const claim = (kind: unknown, index: unknown, location: unknown): void => {
    if ((kind !== "signal" && kind !== "street_lamp") || !Number.isSafeInteger(index)
        || (index as number) < 0 || (index as number) >= sources[kind].length
        || seen[kind].has(index as number) || !sameJson(location, sources[kind][index as number])) {
      throw new Error("City road assets effective fixture inventory is invalid or duplicated");
    }
    seen[kind].add(index as number);
  };
  const effective = fixtures.effective_fixtures;
  if (!Array.isArray(effective)) throw new Error("City road assets effective fixture list is missing");
  const signalIds = new Set<string>(), streetLampIndices = new Set<number>();
  for (const value of effective) {
    const item = record(value, "effective fixture");
    claim(item.kind, item.source_index, item.source_location);
    if (item.kind === "signal") signalIds.add(String(record(item.source_location, "signal").id));
    else streetLampIndices.add(item.source_index as number);
  }
  for (const [kind, key] of [["signal", "omitted_signals"], ["street_lamp", "omitted_street_lamps"]] as const) {
    const omitted = fixtures[key];
    if (!Array.isArray(omitted)) throw new Error(`City road assets ${key} are missing`);
    for (const value of omitted) {
      const item = record(value, key);
      if (typeof item.reason !== "string" || item.reason.length === 0) {
        throw new Error(`City road assets ${key} lack an explicit reason`);
      }
      claim(kind, item.source_index, item.source_location);
    }
    if (seen[kind].size !== sources[kind].length) {
      throw new Error("City road assets effective and omitted fixtures do not cover every source fixture");
    }
  }
  return { signalIds, streetLampIndices, signalModelSha256: String(record(models.signal, "signal model").sha256) };
}

/** Validate the canonical ground evidence against the verified render source context. */
export async function verifyCityRoadAssetBindings(ref: CitySceneRoadAssets, pack: PackIdentity,
  render: BuildingRenderManifest, payloads: CityRoadAssetPayloads,
  renderManifestSha256: string): Promise<EffectiveFixtureSelection> {
  if (ref.source_scene_id !== render.scene.id
      || ref.mesh_pack_manifest_sha256 !== pack.manifestSha256
      || ref.mesh_pack_source_sha256 !== pack.manifest.source.sha256
      || render.scene.mesh_pack_manifest_sha256 !== pack.manifestSha256
      || render.scene.mesh_pack_source_sha256 !== pack.manifest.source.sha256) {
    throw new Error("City road assets and building render do not share the verified mesh pack source");
  }
  const road = record(payloads.road, "road");
  const fixtures = record(payloads.effective_fixtures, "effective fixtures");
  const traffic = record(payloads.traffic, "traffic");
  const flight = record(payloads.flight, "flight");
  const context = record(road.source_context, "road source context");
  if (!/^[0-9a-f]{64}$/.test(renderManifestSha256)
      || context.schema_version !== "aero-bench.city-rendered-source-context/v1"
      || context.scene_id !== ref.source_scene_id
      || context.objects_json_sha256 !== render.scene.objects_json_sha256
      || context.building_render_manifest_sha256 !== renderManifestSha256
      || context.mesh_pack_manifest_sha256 !== ref.mesh_pack_manifest_sha256
      || context.mesh_pack_source_sha256 !== ref.mesh_pack_source_sha256
      || context.source_network_sha256 !== ref.source_network_sha256
      || context.source_osm_sha256 !== ref.source_osm_sha256
      || record(context.building_geometry, "source context building geometry").count !== render.counts.buildings) {
    throw new Error("City road assets source context differs from the verified rendered buildings");
  }
  if (road.schema_version !== "aero-bench.city-road-preview/v3"
      || road.source_kind !== "sumo-network-visual-geometry" || road.road_scope !== "ground-only"
      || record(road.physical_clearance, "road physical clearance").status !== "PASS"
      || road.source_network_sha256 !== ref.source_network_sha256
      || road.source_osm_sha256 !== ref.source_osm_sha256
      || road.mesh_pack_source_sha256 !== ref.mesh_pack_source_sha256
      || road.displayed_surface_sha256 !== ref.displayed_surface_sha256) {
    throw new Error("City road assets road is not the canonical ground road with physical clearance PASS");
  }
  if (fixtures.schema_version !== "aero-bench.city-effective-fixture-geometry/v1"
      || !sameJson(fixtures.source_context, context)
      || fixtures.displayed_surface_sha256 !== ref.displayed_surface_sha256) {
    throw new Error("City road assets effective fixtures differ from the canonical road source context");
  }
  const basis = record(traffic.visual_obstacle_basis, "traffic visual obstacle basis");
  if (traffic.schema_version !== "aero-bench.city-sumo-preview/v2"
      || traffic.source_kind !== "offline-sumo-engineering-preview"
      || traffic.vehicle_position_reference !== "center-derived-from-native-TraCI-front-bumper-and-length"
      || !sameJson(traffic.source_context, context)
      || traffic.source_network_sha256 !== ref.source_network_sha256
      || traffic.source_osm_sha256 !== ref.source_osm_sha256
      || traffic.mesh_pack_source_sha256 !== ref.mesh_pack_source_sha256
      || basis.policy !== "canonical-rendered-building-footprints-and-effective-fixtures"
      || basis.route_obstacle_basis !== basis.policy
      || basis.rendered_footprint_count !== render.counts.buildings
      || basis.effective_fixture_geometry_sha256 !== ref.effective_fixtures.sha256) {
    throw new Error("City road assets traffic is not the canonical recording of this road and fixture geometry");
  }
  const geometry = validateCanonicalCityRoadPayload(road);
  if (await displaySurfaceSha256(geometry.roadbed, geometry.walkbed) !== ref.displayed_surface_sha256) {
    throw new Error("City road assets polygons differ from the declared displayed surface");
  }
  if (flight.schema_version !== "aero-bench.city-planned-flight-preview/v2"
      || flight.source_kind !== "planned-visual-flight" || flight.physical_simulation !== false
      || flight.independent_from_sumo_preview !== true
      || flight.mesh_pack_source_sha256 !== ref.mesh_pack_source_sha256
      || !sameJson(flight.source_context, context)) {
    throw new Error("City road assets flight does not match its declared city source and provenance");
  }
  return effectiveFixtureSelection(fixtures, road, traffic);
}

/** Freeze the digest-verified JSON bytes for the existing road and replay loaders. */
export async function loadVerifiedCityRoadAssets(ref: CitySceneRoadAssets, pack: PackIdentity,
  render: BuildingRenderManifest, renderManifestSha256: string,
  onProgress?: (progress: CityRoadAssetsProgress) => void, signal?: AbortSignal): Promise<VerifiedCityRoadAssets> {
  assertNotAborted(signal);
  const controller = new AbortController();
  const abort = (): void => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const kinds: readonly RoadAssetKind[] = ["road", "effective_fixtures", "traffic", "flight"];
  const totalBytes = kinds.reduce((total, kind) => total + ref[kind].size_bytes, 0);
  const completed = new Map<RoadAssetKind, number>();
  let verifiedFiles = 0;
  const report = (): void => onProgress?.({
    completedBytes: [...completed.values()].reduce((total, bytes) => total + bytes, 0),
    totalBytes, verifiedFiles, totalFiles: kinds.length,
  });
  const read = async (kind: RoadAssetKind): Promise<{ kind: RoadAssetKind; bytes: ArrayBuffer; value: unknown }> => {
    const asset: CitySceneJsonAssetRef = ref[kind];
    const response = await fetch(asset.url, { redirect: "error", signal: controller.signal });
    assertNotAborted(controller.signal);
    if (!response.ok) throw new Error(`City road assets ${kind} request failed: ${response.status}`);
    const bytes = await readBoundedResponse(response, asset.size_bytes, `City road assets ${kind}`,
      controller.signal, asset.size_bytes, count => { completed.set(kind, count); report(); });
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    assertNotAborted(controller.signal);
    const actual = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
    if (actual !== asset.sha256) throw new Error(`City road assets ${kind} digest differs from its declared SHA-256`);
    // The fixture file is tens of MB: decode, parse and the binding checks each get their own task.
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    await nextTask(controller.signal);
    const value: unknown = JSON.parse(text);
    await nextTask(controller.signal);
    verifiedFiles++; report();
    return { kind, bytes, value };
  };
  let files: Awaited<ReturnType<typeof read>>[];
  let fixtures: EffectiveFixtureSelection;
  let sourceContext: Readonly<Record<string, unknown>>;
  let vegetationRoadBinding: CityVegetationRoadBinding;
  try {
    files = await Promise.all(kinds.map(read));
    const payloads = Object.fromEntries(files.map(file => [file.kind, file.value])) as unknown as CityRoadAssetPayloads;
    fixtures = await verifyCityRoadAssetBindings(ref, pack, render, payloads, renderManifestSha256);
    sourceContext = record(record(payloads.road, "road").source_context, "road source context");
    // The binding check above already validated every effective fixture entry against its inventory.
    vegetationRoadBinding = cityVegetationRoadBinding(validateCanonicalCityRoadPayload(payloads.road),
      record(payloads.effective_fixtures, "effective fixtures").effective_fixtures as CityVegetationEffectiveFixture[]);
    assertNotAborted(controller.signal);
  } catch (error) {
    controller.abort();
    throw error;
  } finally {
    signal?.removeEventListener("abort", abort);
  }
  const urls = new Map<RoadAssetKind, string>();
  try {
    for (const file of files) if (file.kind !== "effective_fixtures") {
      urls.set(file.kind, URL.createObjectURL(new Blob([file.bytes], { type: "application/json" })));
    }
    return {
      roadUrl: urls.get("road")!, trafficUrl: urls.get("traffic")!, flightUrl: urls.get("flight")!,
      sourceContext, displayedSurfaceSha256: ref.displayed_surface_sha256, fixtures, vegetationRoadBinding,
      dispose: () => { for (const url of urls.values()) URL.revokeObjectURL(url); urls.clear(); },
    };
  } catch (error) {
    for (const url of urls.values()) URL.revokeObjectURL(url);
    throw error;
  }
}
