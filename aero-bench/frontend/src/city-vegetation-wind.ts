import * as THREE from "three";

/**
 * Shared wind animation for vegetation shader patches. All values are presentation
 * inputs driven by the visual weather controls, not meteorological samples.
 */
export interface VegetationWindUniforms {
  uTime: { value: number };
  /** Normalized travel direction in city XZ: (sin deg, -cos deg) for clockwise-from-north. */
  uWindDir: { value: THREE.Vector2 };
  /** Saturating bend strength in [0, 1]; see windStrengthFor. */
  uWindStrength: { value: number };
}

export function createVegetationWindUniforms(): VegetationWindUniforms {
  return { uTime: { value: 0 }, uWindDir: { value: new THREE.Vector2(0, -1) },
    uWindStrength: { value: 0 } };
}

/**
 * Monotone saturating strength: 0 at 0 m/s, 0.63 at 6 m/s, 0.86 at 12 m/s, bounded by 1.
 * A display response curve, not a biomechanical model.
 */
export const VEGETATION_WIND_SATURATION_MPS = 6;
export function windStrengthFor(windMps: number): number {
  return 1 - Math.exp(-windMps / VEGETATION_WIND_SATURATION_MPS);
}

/**
 * Per-profile bend at full strength. `referenceHeightM` is the height whose tip reaches
 * `tipDisplacementM`; bend grows with the square of height (a cantilever-like curve), so
 * the base stays anchored. Values are authored presentation limits.
 */
export const VEGETATION_WIND_PROFILES = Object.freeze({
  tree: Object.freeze({ referenceHeightM: 7, tipDisplacementM: 0.42, flutter: 0.12 }),
  grass: Object.freeze({ referenceHeightM: 0.45, tipDisplacementM: 0.13, flutter: 0.3 }),
});
export type VegetationWindProfile = keyof typeof VEGETATION_WIND_PROFILES;

/** Validate inputs and publish direction, strength and time into the shared uniforms. */
export function setVegetationWind(u: VegetationWindUniforms, windMps: number,
    windDirectionDeg: number, timeS: number): void {
  if (![windMps, windDirectionDeg, timeS].every(Number.isFinite)) {
    throw new RangeError("Vegetation wind inputs must be finite");
  }
  if (windMps < 0) throw new RangeError("Vegetation wind speed must be nonnegative");
  const heading = windDirectionDeg * Math.PI / 180;
  // City frame: +X east, -Z north. Direction of travel, clockwise from north.
  u.uWindDir.value.set(Math.sin(heading), -Math.cos(heading));
  u.uWindStrength.value = windStrengthFor(windMps);
  u.uTime.value = timeS;
}

const WIND_MARKER = "city-vegetation-wind/v2";

function windChunk(profile: VegetationWindProfile): string {
  const { referenceHeightM, tipDisplacementM, flutter } = VEGETATION_WIND_PROFILES[profile];
  return /* glsl */ `
uniform float uTime;
uniform vec2 uWindDir;
uniform float uWindStrength;
// Returns an object-space offset. Bend is computed in world metres along the travel
// direction, then mapped back through the rotation and uniform scale of the instance.
vec3 vegetationWindOffset(vec3 objectPosition, mat4 placement) {
  mat3 linear = mat3(placement);
  float scaleSq = max(dot(linear[0], linear[0]), 1e-8);
  float heightM = max(objectPosition.y, 0.0) * sqrt(scaleSq);
  if (uWindStrength <= 0.0 || heightM <= 0.0) return vec3(0.0);
  vec2 base = placement[3].xz;
  float relative = min(heightM / ${referenceHeightM.toFixed(4)}, 1.25);
  // Gust fronts travel with the wind (continuous in space and time; no per-frame noise).
  float along = dot(base, uWindDir);
  float front = 0.5 + 0.5 * sin(along * 0.045 - uTime * 0.9);
  float slow = 0.5 + 0.5 * sin(along * 0.013 + dot(base, vec2(-uWindDir.y, uWindDir.x)) * 0.02 - uTime * 0.31);
  float gust = 0.55 + 0.45 * front * slow;
  float phase = fract(sin(dot(floor(base * 2.0), vec2(12.9898, 78.233))) * 43758.5453) * 6.2831853;
  float flutter = ${flutter.toFixed(4)} * sin(uTime * (2.1 + 0.4 * fract(phase)) + phase + objectPosition.y * 1.7);
  float bend = uWindStrength * ${tipDisplacementM.toFixed(4)} * relative * relative * (gust + flutter);
  vec3 worldOffset = vec3(uWindDir.x, 0.0, uWindDir.y) * bend;
  // Keep bent tips on an arc instead of stretching upward.
  worldOffset.y = -0.5 * bend * bend / max(heightM, 0.05);
  return transpose(linear) * worldOffset / scaleSq;
}
`;
}

/**
 * Patch a material's vertex stage so foliage bends with the shared wind uniforms while the
 * base stays anchored. Applying the same profile twice is a no-op; any other existing
 * shader hook is an error, because silently replacing it would drop another patch.
 */
export function applyVegetationWind(material: THREE.Material, u: VegetationWindUniforms,
    profile: VegetationWindProfile): void {
  if (!Object.hasOwn(VEGETATION_WIND_PROFILES, profile)) {
    throw new Error(`Unknown vegetation wind profile: ${String(profile)}`);
  }
  const existing = material.userData.vegetationWind as { marker?: string; profile?: string } | undefined;
  if (existing?.marker === WIND_MARKER) {
    if (existing.profile === profile) return;
    throw new Error(`Vegetation wind profile conflict on ${material.name || material.type}: ` +
      `${String(existing.profile)} then ${profile}`);
  }
  if (existing !== undefined || Object.hasOwn(material, "onBeforeCompile")
    || Object.hasOwn(material, "customProgramCacheKey")) {
    throw new Error(`Vegetation wind needs an unpatched material: ${material.name || material.type}`);
  }
  const chunk = windChunk(profile);
  material.onBeforeCompile = shader => {
    shader.uniforms.uTime = u.uTime;
    shader.uniforms.uWindDir = u.uWindDir;
    shader.uniforms.uWindStrength = u.uWindStrength;
    if (!shader.vertexShader.includes("#include <begin_vertex>")) {
      throw new Error("Vegetation wind requires a begin_vertex shader chunk");
    }
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", `#include <common>${chunk}`)
      .replace("#include <begin_vertex>", `#include <begin_vertex>
#ifdef USE_INSTANCING
  transformed += vegetationWindOffset(transformed, modelMatrix * instanceMatrix);
#else
  transformed += vegetationWindOffset(transformed, modelMatrix);
#endif`);
  };
  material.customProgramCacheKey = () => `city-vegetation-wind-v2:${profile}`;
  material.userData.vegetationWind = { marker: WIND_MARKER, profile };
  material.needsUpdate = true;
}

/**
 * Depth material for shadow casters, patched with the same wind so shadows move with the
 * foliage. Alpha-tested atlas maps keep their cut-out silhouettes.
 */
export function createVegetationWindDepthMaterial(u: VegetationWindUniforms, profile: VegetationWindProfile,
    source?: { readonly map: THREE.Texture | null; readonly alphaTest: number }): THREE.MeshDepthMaterial {
  const depth = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking,
    map: source?.map ?? null, alphaTest: source?.alphaTest ?? 0 });
  depth.name = `vegetation-wind-depth:${profile}`;
  applyVegetationWind(depth, u, profile);
  return depth;
}
