import type { CitySceneJsonAssetRef } from "./city-scene-config";
import type { BuildingRenderManifest } from "./city-building-renders";
import type { CityVegetationLayerInput, CityVegetationRoadBinding } from "./city-vegetation-layer";

/**
 * Bind a scene's environment source to the verified building-render authority and the
 * mesh pack's published extent. The pack stores east-up-south coordinates, so the scene
 * frame is x = east, z = -north. Nothing here is inferred: every value comes from a
 * digest-verified document, and a missing or inconsistent value is an error.
 */
export function cityVegetationInput(
  source: CitySceneJsonAssetRef,
  renderScene: BuildingRenderManifest["scene"],
  packManifestSha256: string,
  packExtent: { readonly west: number; readonly east: number; readonly south: number; readonly north: number },
  roadBinding: CityVegetationRoadBinding,
  signal?: AbortSignal,
): CityVegetationLayerInput {
  if (renderScene.mesh_pack_manifest_sha256 !== packManifestSha256) {
    throw new Error("City vegetation authority: the render manifest binds a different mesh pack");
  }
  const { west, east, south, north } = packExtent;
  if (![west, east, south, north].every(Number.isFinite) || west >= east || south >= north) {
    throw new Error("City vegetation extent: the mesh pack extent is not a finite rectangle");
  }
  const origin = renderScene.origin_wgs84;
  return {
    source: { url: source.url, sha256: source.sha256, sizeBytes: source.size_bytes },
    authority: {
      osmSha256: renderScene.mesh_pack_source_sha256,
      objectsSha256: renderScene.objects_json_sha256,
      origin: { latitude_deg: origin.latitude_deg, longitude_deg: origin.longitude_deg,
        ellipsoid_height_m: origin.ellipsoid_height_m },
    },
    extent: {
      outline: [[west, -north], [east, -north], [east, -south], [west, -south]],
      holes: [],
    },
    roadBinding,
    ...(signal === undefined ? {} : { signal }),
  };
}
