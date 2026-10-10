import * as THREE from "three";

/** FBX GlobalSettings creates AmbientLight nodes; cloning a vehicle must not brighten the city. */
export function removeAssetAmbientLights(asset: THREE.Object3D): void {
  const imported: THREE.AmbientLight[] = [];
  asset.traverse(node => { if (node instanceof THREE.AmbientLight) imported.push(node); });
  // Remove after traversing so sibling light/mesh nodes cannot be skipped.
  for (const light of imported) light.removeFromParent();
}

/** Display lighting, not a measurement of a Weather Provider's solar irradiance. */
export const CITY_LIGHTING = {
  day: {
    sunColor: 0xffecd6, sunIntensity: 3.6,
    skyFill: 0xc5dcf3, groundFill: 0x736958, hemisphereIntensity: 1,
    ambientColor: 0xe8edf0, ambientIntensity: 0.12,
    environmentIntensity: 0.6, facadeEnvironmentIntensity: 0.65,
    exposure: 1.25, backgroundIntensity: 1,
    groundColor: 0xc1bbb0, windowIntensity: 0,
  },
  dusk: {
    sunColor: 0x9bc3ed, sunIntensity: 0.18,
    skyFill: 0x769aca, groundFill: 0x364455, hemisphereIntensity: 1,
    ambientColor: 0x879ab5, ambientIntensity: 0.16,
    environmentIntensity: 0.4, facadeEnvironmentIntensity: 0.4,
    exposure: 1.2, backgroundIntensity: 0.28,
    groundColor: 0x89939d, windowIntensity: 1.2,
  },
} as const;
