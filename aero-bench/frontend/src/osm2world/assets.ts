import type { PublicScenario } from "../generated/aero-bench-contracts";
import { AssetResolver } from "../asset-resolver";
import { parseOsmJson, type OsmJson } from "./source";
import { loadMeshPack, type LoadedMeshPack } from "./pack-loader";

export function sceneAsset(scenario: PublicScenario) {
  const layers = scenario.layers.filter(layer => layer.kind === "osm_scene");
  if (layers.length > 1) throw new Error("Only one OSM scene layer may be declared");
  const layer = layers[0];
  if (layer === undefined) return null;
  const asset = scenario.assets.find(asset => asset.asset_id === layer.asset_id);
  if (asset === undefined || asset.media_type !== "application/json") throw new Error("OSM scene must reference a declared JSON asset");
  return asset;
}

export function meshPackAsset(scenario: PublicScenario) {
  const layers = scenario.layers.filter(layer => layer.kind === "osm_mesh");
  if (layers.length > 1) throw new Error("Only one OSM mesh layer may be declared");
  const layer = layers[0];
  if (layer === undefined) return null;
  const asset = scenario.assets.find(asset => asset.asset_id === layer.asset_id);
  if (asset === undefined || asset.media_type !== "application/json") throw new Error("OSM mesh layer requires a declared JSON manifest");
  return asset;
}

export async function loadScenarioMeshPack(scenario: PublicScenario, resolver: AssetResolver, onProgress?: (completed: number, total: number) => void): Promise<LoadedMeshPack> {
  const asset = meshPackAsset(scenario), source = sceneAsset(scenario);
  if (asset === null || source === null) throw new Error("Declared OSM source and preconverted mesh assets are required");
  return loadMeshPack(resolver, { sha256: asset.sha256, size_bytes: asset.size_bytes }, undefined, manifest => {
    if (manifest.source.sha256 !== source.sha256 || manifest.source.size_bytes !== source.size_bytes) throw new Error("Mesh pack source differs from declared OSM scene");
    const refs = [...manifest.batches.map(batch => batch.file), ...Object.values(manifest.textures)];
    for (const ref of refs) {
      const declared = scenario.assets.find(candidate => candidate.sha256 === ref.sha256);
      if (declared === undefined || declared.size_bytes !== ref.size_bytes) throw new Error("Mesh pack references an asset outside the public scenario");
    }
  }, onProgress);
}

export async function loadSceneAsset(scenario: PublicScenario, resolver: AssetResolver): Promise<OsmJson> {
  const asset = sceneAsset(scenario);
  if (asset === null) throw new Error("No OSM scene asset declared");
  const verified = await resolver.fetchVerified(asset.replay_path, { sha256: asset.sha256, sizeBytes: asset.size_bytes, mediaType: asset.media_type });
  try {
    const response = await fetch(verified.url);
    return parseOsmJson(await response.json());
  } finally {
    resolver.revoke(verified.url);
  }
}
