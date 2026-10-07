/** Unit-test textures only; these are not the terrain texture library or delivery data. */
import * as THREE from "three";
import { groundMaterialInputFromGreen, type GroundMaterialAssignment } from "../city-ground-material-rules";
import type { TerrainTextureSets } from "../city-terrain-materials";
import { assignCityGroundMaterial, createTerrainSurfaceKit,
  type TerrainSurfaceKit } from "../city-terrain-surfaces";
import { createSurfaceWetnessUniforms, type SurfaceWetnessUniforms } from "../city-surface-wetness";
import { createWaterSurfaceUniforms, type WaterSurfaceUniforms } from "../city-water-surface";
import type { CityEnvironmentPlan, CityEnvironmentSurfaces } from "../city-environment";

/** One 1x1 DataTexture triple per requested set, with a 2 m tile. Records requested IDs. */
export function fakeTerrainTextureSets(setIds: readonly string[]): TerrainTextureSets & {
  readonly textures: readonly THREE.Texture[];
} {
  const textures: THREE.Texture[] = [];
  const bundles = new Map(setIds.map(id => {
    const make = (): THREE.Texture => {
      const texture = new THREE.DataTexture(new Uint8Array([128, 128, 128, 255]), 1, 1);
      textures.push(texture); return texture;
    };
    return [id, { map: make(), normalMap: make(), ormMap: make(), tileSizeM: 2 }] as const;
  }));
  return {
    textures,
    get: setId => {
      const bundle = bundles.get(setId);
      if (bundle === undefined) throw new Error(`fake terrain texture set ${setId} was not loaded`);
      return bundle;
    },
    dispose: () => { textures.forEach(texture => texture.dispose()); bundles.clear(); },
  };
}

/** Assign every green of a plan through the city rules and build a kit over fake textures. */
export function terrainSurfacesForPlan(plan: CityEnvironmentPlan,
    wetness: SurfaceWetnessUniforms = createSurfaceWetnessUniforms(),
    waterSurface: WaterSurfaceUniforms = createWaterSurfaceUniforms()): CityEnvironmentSurfaces & { readonly kit: TerrainSurfaceKit } {
  const assignments = new Map<string, GroundMaterialAssignment>();
  for (const patch of [...plan.grass, ...plan.woodlandFloor]) {
    assignments.set(patch.id, assignCityGroundMaterial(groundMaterialInputFromGreen({ id: patch.id, provenance: patch.provenance })));
  }
  return { assignments,
    kit: createTerrainSurfaceKit([...assignments.values()], fakeTerrainTextureSets(ALL_TEXTURE_SET_IDS), wetness, waterSurface) };
}

/** Every texture set in the T5a library; fakes cost nothing, so tests load all of them. */
export const ALL_TEXTURE_SET_IDS = Object.freeze(["dry-grass-withered", "gravel-043", "lawn-grass001",
  "mulch-ground048", "natural-grass-ground", "shoreline-ground083", "soil-park-dirt", "sports-grass008"]);
