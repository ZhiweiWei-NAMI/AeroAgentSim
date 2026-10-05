import type { PackFile } from "./osm2world/pack";

export interface CitySceneAssets {
  readonly building_placement: string;
  readonly road: string;
  readonly traffic: string;
  readonly flight: string;
}

export interface CitySceneBuildingRenderRef {
  readonly base_url: string;
  readonly source_scene_id: string;
  readonly manifest: { readonly sha256: string; readonly size_bytes: number };
  readonly source_context: { readonly sha256: string; readonly size_bytes: number };
}

export interface CitySceneJsonAssetRef {
  readonly url: string;
  readonly sha256: string;
  readonly size_bytes: number;
}

/** Canonical ground geometry, effective fixtures and replay evidence of one rendered source context. */
export interface CitySceneRoadAssets {
  readonly schema_version: "aero-bench.city-road-assets/v2";
  readonly source_scene_id: string;
  readonly mesh_pack_source_sha256: string;
  readonly mesh_pack_manifest_sha256: string;
  readonly source_osm_sha256: string;
  readonly source_network_sha256: string;
  readonly displayed_surface_sha256: string;
  readonly road: CitySceneJsonAssetRef;
  readonly effective_fixtures: CitySceneJsonAssetRef;
  readonly traffic: CitySceneJsonAssetRef;
  readonly flight: CitySceneJsonAssetRef;
}

interface CitySceneBase {
  readonly schema_version: "aero-bench.city-scene-preview/v1";
  readonly name: string;
  readonly initial_mood?: "day" | "dusk";
  readonly traffic_signal_model?: CitySceneJsonAssetRef;
  readonly mesh_pack: { readonly base_url: string; readonly road_surface_texture: string;
                        readonly manifest: PackFile };
}

/**
 * Workspace buildings and building-render assets have separate id spaces.
 * A render scene may explicitly add independently verified ground road assets.
 */
export type CitySceneConfig = CitySceneBase & (
  | { readonly building_render?: undefined; readonly assets: CitySceneAssets; readonly road_assets?: never }
  | { readonly building_render: CitySceneBuildingRenderRef; readonly assets?: never;
      readonly road_assets?: CitySceneRoadAssets;
      /** Optional verified environment source document for the studio draft environment. */
      readonly environment_source?: CitySceneJsonAssetRef }
);

function record(value: unknown, name: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value as Record<string, unknown>;
}

function publicPath(value: unknown, name: string, directory = false): string {
  if (typeof value !== "string" || !/^\/[a-zA-Z0-9_./-]+$/.test(value)
      || value.slice(1, directory && value.endsWith("/") ? -1 : undefined)
        .split("/").some(part => part === "" || part === "." || part === "..")) {
    throw new Error(`${name} must be an absolute local asset URL`);
  }
  return value;
}

function digestRef(value: unknown, name: string): { sha256: string; size_bytes: number } {
  const ref = record(value, name);
  if (typeof ref.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(ref.sha256)
      || typeof ref.size_bytes !== "number" || !Number.isSafeInteger(ref.size_bytes)
      || ref.size_bytes < 1) throw new Error(`${name} is invalid`);
  return { sha256: ref.sha256, size_bytes: ref.size_bytes };
}

function roadAssets(value: unknown): CitySceneRoadAssets {
  const assets = record(value, "road assets");
  const digests = ["mesh_pack_source_sha256", "mesh_pack_manifest_sha256", "source_osm_sha256",
    "source_network_sha256", "displayed_surface_sha256"] as const;
  const files = ["road", "effective_fixtures", "traffic", "flight"] as const;
  const keys = new Set<string>(["schema_version", "source_scene_id", ...digests, ...files]);
  if (Object.keys(assets).some(key => !keys.has(key))
      || assets.schema_version !== "aero-bench.city-road-assets/v2"
      || typeof assets.source_scene_id !== "string" || !/^[a-zA-Z0-9_-]+$/.test(assets.source_scene_id)
      || digests.some(key => typeof assets[key] !== "string" || !/^[0-9a-f]{64}$/.test(assets[key] as string))) {
    throw new Error("City road assets identity is invalid");
  }
  const jsonRef = (key: typeof files[number]): CitySceneJsonAssetRef => {
    const ref = record(assets[key], `road assets ${key}`);
    if (Object.keys(ref).some(field => !["url", "sha256", "size_bytes"].includes(field))) {
      throw new Error(`Road assets ${key} has undeclared fields`);
    }
    return { url: publicPath(ref.url, `road assets ${key}`),
      ...digestRef(ref, `road assets ${key} ref`) };
  };
  return {
    schema_version: "aero-bench.city-road-assets/v2",
    source_scene_id: assets.source_scene_id,
    mesh_pack_source_sha256: assets.mesh_pack_source_sha256 as string,
    mesh_pack_manifest_sha256: assets.mesh_pack_manifest_sha256 as string,
    source_osm_sha256: assets.source_osm_sha256 as string,
    source_network_sha256: assets.source_network_sha256 as string,
    displayed_surface_sha256: assets.displayed_surface_sha256 as string,
    road: jsonRef("road"), effective_fixtures: jsonRef("effective_fixtures"),
    traffic: jsonRef("traffic"), flight: jsonRef("flight"),
  };
}

/** A city-presentation-hosted environment source document, digest-bound like every
 * other scene asset; its url must stay inside the published presentation tree. */
function environmentSource(value: unknown): CitySceneJsonAssetRef {
  const ref = record(value, "environment source");
  if (Object.keys(ref).some(field => !["url", "sha256", "size_bytes"].includes(field))) {
    throw new Error("Environment source has undeclared fields");
  }
  const url = publicPath(ref.url, "environment source");
  if (!url.startsWith("/city-presentation/") || !url.endsWith(".json")
      || url.slice(1).split("/").some(part => part === "" || part === "." || part === "..")) {
    throw new Error("Environment source url must be a city-presentation JSON document");
  }
  return { url, ...digestRef(ref, "environment source ref") };
}

export function parseCitySceneConfig(value: unknown): CitySceneConfig {
  const scene = record(value, "city scene");
  if (scene.schema_version !== "aero-bench.city-scene-preview/v1") {
    throw new Error("Unsupported city scene manifest");
  }
  if (typeof scene.name !== "string" || !scene.name.trim()) throw new Error("City scene has no name");
  if (scene.initial_mood !== undefined && scene.initial_mood !== "day" && scene.initial_mood !== "dusk") {
    throw new Error("City initial mood must be day or dusk");
  }
  const meshPack = record(scene.mesh_pack, "mesh pack");
  const manifest = digestRef(meshPack.manifest, "mesh pack manifest ref");
  const base = publicPath(meshPack.base_url, "mesh pack base", true);
  if (!base.endsWith("/")) throw new Error("Mesh pack base URL must end in /");
  const baseConfig = {
    schema_version: "aero-bench.city-scene-preview/v1" as const,
    name: scene.name,
    initial_mood: scene.initial_mood as "day" | "dusk" | undefined,
    ...(scene.traffic_signal_model === undefined ? {} : { traffic_signal_model: {
      url: publicPath(record(scene.traffic_signal_model, "traffic signal model").url, "traffic signal model"),
      ...digestRef(scene.traffic_signal_model, "traffic signal model ref"),
    } }),
    mesh_pack: { base_url: base,
      road_surface_texture: publicPath(meshPack.road_surface_texture, "road surface texture"),
      manifest },
  };

  if (scene.building_render !== undefined) {
    if (scene.assets !== undefined) {
      throw new Error("City scene must not mix building_render with workspace assets");
    }
    const render = record(scene.building_render, "building render");
    if (Object.keys(render).some(key => !["base_url", "source_scene_id", "manifest", "source_context"].includes(key))) {
      throw new Error("Building render has undeclared fields");
    }
    if (typeof render.source_scene_id !== "string" || !/^[a-zA-Z0-9_-]+$/.test(render.source_scene_id)) {
      throw new Error("Building render source scene identity is invalid");
    }
    const baseUrl = publicPath(render.base_url, "building render base", true);
    if (!baseUrl.endsWith("/")) throw new Error("Building render base URL must end in /");
    // Vegetation is planned against the verified ground roads; it has no road-free mode.
    if (scene.environment_source !== undefined && scene.road_assets === undefined) {
      throw new Error("City environment source requires verified road_assets");
    }
    return {
      ...baseConfig,
      building_render: { base_url: baseUrl, source_scene_id: render.source_scene_id,
        manifest: digestRef(render.manifest, "building render manifest ref"),
        source_context: digestRef(render.source_context, "building render source context ref") },
      ...(scene.road_assets === undefined ? {} : { road_assets: roadAssets(scene.road_assets) }),
      ...(scene.environment_source === undefined
        ? {} : { environment_source: environmentSource(scene.environment_source) }),
    };
  }

  if (scene.road_assets !== undefined) throw new Error("City road assets require building_render");
  if (scene.environment_source !== undefined) {
    throw new Error("City environment source requires building_render");
  }
  const assets = record(scene.assets, "city scene assets");
  return {
    ...baseConfig,
    assets: {
      building_placement: publicPath(assets.building_placement, "building placement"),
      road: publicPath(assets.road, "road preview"),
      traffic: publicPath(assets.traffic, "traffic preview"),
      flight: publicPath(assets.flight, "flight preview"),
    },
  };
}

export async function loadCitySceneConfig(signal?: AbortSignal): Promise<CitySceneConfig> {
  const requested = new URLSearchParams(window.location.search).get("city");
  const path = requested ?? "/city-presentation/default-scene-v1.json";
  if (!/^\/city-presentation\/[a-zA-Z0-9_-]+\.json$/.test(path)) {
    throw new Error("City scene manifest must be a local city-presentation JSON file");
  }
  const response = await fetch(path, { signal });
  if (!response.ok) throw new Error(`City scene manifest failed: ${response.status}`);
  return parseCitySceneConfig(await response.json());
}
