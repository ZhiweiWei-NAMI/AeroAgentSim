import type * as THREE from "three";
import { checkCityWeatherSettings, type CityWeatherSettings } from "./city-weather";

/**
 * Visual wet-surface response to the visual weather controls. This is a presentation
 * approximation of films of water on horizontal faces, not a road-surface measurement
 * and not a hydrology input.
 */

/**
 * Snow grains and hailstones bounce off the surface instead of forming a continuous
 * water film, so they leave no visual wetness; melting or puddling would need an
 * explicit thaw model rather than a silent mapping from frozen precipitation.
 */
const SATURATION_RATE_MM_PER_H = 6;

/** 0..1 wetness: none/frozen precipitation -> 0; drizzle and rain rise and saturate. */
export function surfaceWetnessFromWeather(settings: Readonly<CityWeatherSettings>): number {
  checkCityWeatherSettings(settings);
  if (settings.precipitation !== "drizzle" && settings.precipitation !== "rain") return 0;
  // Drizzle deposits about half the film depth of rain at the same reported rate.
  const rateFactor = settings.precipitation === "drizzle" ? 0.5 : 1;
  const exposure = settings.precipitationRateMmPerH * rateFactor / SATURATION_RATE_MM_PER_H;
  return 1 - Math.exp(-exposure);
}

export interface SurfaceWetnessUniforms { uWetness: { value: number } }

export function createSurfaceWetnessUniforms(): SurfaceWetnessUniforms {
  return { uWetness: { value: 0 } };
}

export interface SurfaceWetnessOptions {
  /** Diffuse darkening cap at full wetness; films look darker because light passes through. */
  readonly maxDarkening?: number;
  /** Roughness floor at full wetness; standing water is glossy. */
  readonly minRoughness?: number;
}

export const SURFACE_WETNESS_DEFAULTS: Readonly<Required<SurfaceWetnessOptions>> =
  Object.freeze({ maxDarkening: 0.45, minRoughness: 0.08 });

const WETNESS_MARKER = "city-surface-wetness/v2";
const WETNESS_UNIFORM = "uSurfaceWetness";
const WETNESS_VARYING = "vSurfaceUpFacing";

/**
 * Patch a standard/physical material so up-facing surfaces darken and lose roughness
 * with uWetness. Wall faces stay matte because films run off vertical surfaces.
 * Idempotent: applying twice throws instead of silently stacking the patch.
 */
export function applySurfaceWetness(material: THREE.MeshStandardMaterial | THREE.MeshPhysicalMaterial,
    u: SurfaceWetnessUniforms, options?: SurfaceWetnessOptions): void {
  if (material.userData.surfaceWetness !== undefined
    || Object.hasOwn(material, "onBeforeCompile") || Object.hasOwn(material, "customProgramCacheKey")) {
    throw new Error(`Surface wetness needs an unpatched material: ${material.name || material.type}`);
  }
  const limits = { ...SURFACE_WETNESS_DEFAULTS, ...options };
  if (![limits.maxDarkening, limits.minRoughness].every(value => Number.isFinite(value) && value >= 0)
    || limits.maxDarkening > 1 || limits.minRoughness > 1) {
    throw new RangeError("Surface wetness limits are invalid");
  }
  material.onBeforeCompile = shader => {
    shader.uniforms[WETNESS_UNIFORM] = u.uWetness;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", `#include <common>\nvarying float ${WETNESS_VARYING};`)
      // World-space normal Y from the object normal (placements use rotation and uniform scale).
      .replace("#include <beginnormal_vertex>", `#include <beginnormal_vertex>
  vec3 surfaceWetnessNormal = objectNormal;
#ifdef USE_INSTANCING
  surfaceWetnessNormal = mat3(instanceMatrix) * surfaceWetnessNormal;
#endif
  ${WETNESS_VARYING} = normalize(mat3(modelMatrix) * surfaceWetnessNormal).y;`);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>
uniform float ${WETNESS_UNIFORM};
varying float ${WETNESS_VARYING};`)
      .replace("#include <roughnessmap_fragment>", `#include <roughnessmap_fragment>
  float surfaceWetness = clamp(${WETNESS_UNIFORM}, 0.0, 1.0) * smoothstep(0.35, 0.8, ${WETNESS_VARYING});
  roughnessFactor = mix(roughnessFactor, ${limits.minRoughness.toFixed(6)}, surfaceWetness);`)
      .replace("#include <map_fragment>", `#include <map_fragment>
  float surfaceWetnessDarken = clamp(${WETNESS_UNIFORM}, 0.0, 1.0) * smoothstep(0.35, 0.8, ${WETNESS_VARYING});
  diffuseColor.rgb *= 1.0 - ${limits.maxDarkening.toFixed(6)} * surfaceWetnessDarken;`);
  };
  // The limits are compiled into the shader, so they must be part of the program key;
  // otherwise materials with equal features but different limits share one program.
  const cacheKey = `city-surface-wetness-v2:${limits.maxDarkening.toFixed(6)}:${limits.minRoughness.toFixed(6)}`;
  material.customProgramCacheKey = () => cacheKey;
  material.userData.surfaceWetness = { marker: WETNESS_MARKER };
  material.needsUpdate = true;
}
