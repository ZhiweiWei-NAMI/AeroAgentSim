import * as THREE from "three";
import { rasterCityFacadePaneRoughness, readCityFacadePaneProfile, type CityFacadePaneProfile } from "./city-facade-pane-profile";
import type { CityTimeOfDay } from "./city-lighting-calibration";

/**
 * Per-window occupancy lighting for authored facades. Which windows are lit is display art
 * derived from a stable hash, not an observation of building occupancy.
 */
export const CITY_WINDOW_LIT_FRACTION: Readonly<Record<CityTimeOfDay, number>> = {
  day: 0, twilight: 0.5, night: 0.38,
};

/** Panes closer than this fraction of their median size belong to one window (e.g. transom + sash). */
const PANE_MERGE_RATIO = 0.4;

function median(values: readonly number[]): number {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)]!;
}

/** Group authored glass rectangles into windows; returns one window index per rectangle. */
export function cityFacadeWindowGroups(profile: CityFacadePaneProfile): number[] {
  const rectangles = profile.glass_rectangles_px;
  if (rectangles.length === 0) throw new Error(`Authored facade ${profile.style_id} declares no glass panes`);
  const gapX = PANE_MERGE_RATIO * median(rectangles.map(([left, , right]) => right - left));
  const gapY = PANE_MERGE_RATIO * median(rectangles.map(([, top, , bottom]) => bottom - top));
  const parent = rectangles.map((_, index) => index);
  const root = (index: number): number => {
    while (parent[index] !== index) index = parent[index] = parent[parent[index]!]!;
    return index;
  };
  for (let a = 0; a < rectangles.length; a++) for (let b = a + 1; b < rectangles.length; b++) {
    const [al, at, ar, ab] = rectangles[a]!, [bl, bt, br, bb] = rectangles[b]!;
    const horizontalGap = Math.max(bl - ar, al - br), verticalGap = Math.max(bt - ab, at - bb);
    if (horizontalGap <= gapX && verticalGap <= gapY) parent[root(a)] = root(b);
  }
  const ids = new Map<number, number>();
  return rectangles.map((_, index) => {
    const group = root(index);
    if (!ids.has(group)) ids.set(group, ids.size);
    return ids.get(group)!;
  });
}

/** RGBA8 raster in the profile's top-left pixel convention; R is 0 off-glass, else a nonzero window id. */
export function rasterCityFacadeWindowIds(profile: CityFacadePaneProfile): Uint8Array {
  const checked = readCityFacadePaneProfile(profile);
  const [width, height] = checked.tile_size_px;
  const roughness = rasterCityFacadePaneRoughness(checked);
  const glass = Math.round(255 * (checked.material_kind === "glass-and-masonry" ? 0.19 : 0.28));
  const groups = cityFacadeWindowGroups(checked);
  if (Math.max(...groups) >= 255) throw new Error(`Authored facade ${checked.style_id} has more than 254 windows per tile`);
  const data = new Uint8Array(width * height * 4);
  for (let offset = 3; offset < data.length; offset += 4) data[offset] = 255;
  checked.glass_rectangles_px.forEach(([left, top, right, bottom], index) => {
    for (let y = Math.max(0, Math.ceil(top - 0.5)); y < Math.min(height, Math.ceil(bottom - 0.5)); y++) {
      for (let x = Math.max(0, Math.ceil(left - 0.5)); x < Math.min(width, Math.ceil(right - 0.5)); x++) {
        const offset = (y * width + x) * 4;
        if (roughness[offset + 1] === glass) data[offset] = groups[index]! + 1;
      }
    }
  });
  return data;
}

const windowIdTextures = new Map<string, THREE.DataTexture>();
const litFraction = { value: 0 };

export function setCityWindowLitFraction(timeOfDay: CityTimeOfDay): void {
  litFraction.value = CITY_WINDOW_LIT_FRACTION[timeOfDay];
}

function windowIdTexture(profile: CityFacadePaneProfile, albedo: THREE.Texture): THREE.DataTexture {
  const key = JSON.stringify([profile.tile_size_px, profile.material_kind, profile.glass_rectangles_px,
    profile.opaque_polygons_px, albedo.flipY]);
  let texture = windowIdTextures.get(key);
  if (texture === undefined) {
    const [width, height] = profile.tile_size_px;
    texture = new THREE.DataTexture(rasterCityFacadeWindowIds(profile), width, height, THREE.RGBAFormat);
    texture.name = `${profile.style_id}.display-window-ids`;
    texture.colorSpace = THREE.NoColorSpace;
    texture.flipY = albedo.flipY;
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    // Ids must never blend between windows.
    texture.magFilter = texture.minFilter = THREE.NearestFilter;
    texture.generateMipmaps = false;
    texture.needsUpdate = true;
    windowIdTextures.set(key, texture);
  }
  return texture;
}

/**
 * Gate the facade Illum map per authored window. Idempotent; the compile hook closes over its
 * texture so probe clones that copy `onBeforeCompile` keep the same windows.
 */
export function installCityWindowLights(material: THREE.MeshStandardMaterial, profile: CityFacadePaneProfile): void {
  if (material.userData.cityWindowLights === true) return;
  if (material.map === null || material.emissiveMap === null) {
    throw new Error(`Authored facade window lights need albedo and Illum maps: ${material.name}`);
  }
  // Sampled with vEmissiveMapUv, which already carries the Illum map transform.
  const ids = windowIdTexture(profile, material.map);
  material.onBeforeCompile = shader => {
    shader.uniforms.cityWindowIds = { value: ids };
    shader.uniforms.cityWindowLitFraction = litFraction;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec2 vCityBuildingSeed;")
      .replace("#include <project_vertex>",
        "#include <project_vertex>\n  vCityBuildingSeed = mod(floor(modelMatrix[3].xz), 251.0);");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform sampler2D cityWindowIds;\nuniform float cityWindowLitFraction;\nvarying vec2 vCityBuildingSeed;")
      .replace("#include <emissivemap_fragment>", `#include <emissivemap_fragment>
#ifdef USE_EMISSIVEMAP
  {
    float cityWindowId = floor(texture2D(cityWindowIds, vEmissiveMapUv).r * 255.0 + 0.5);
    // The interpolated seed carries rounding error, and the sin hash amplifies it into
    // per-pixel noise. Round it so every pixel of one window hashes the same integers.
    vec3 cityKey = vec3(floor(vEmissiveMapUv) + floor(vCityBuildingSeed + 0.5), cityWindowId);
    float cityHash = fract(sin(dot(cityKey, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
    float cityLit = cityWindowId > 0.0 ? step(cityHash, cityWindowLitFraction) : 0.0;
    totalEmissiveRadiance *= cityLit * (0.6 + 0.8 * fract(cityHash * 13.7));
  }
#endif`);
  };
  material.customProgramCacheKey = () => "city-window-lights-v1";
  material.userData.cityWindowLights = true;
  material.needsUpdate = true;
}
