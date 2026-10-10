import * as THREE from "three";
import { checkCityWeatherSettings, type CityWeatherSettings } from "./city-weather";
import { surfaceWetnessFromWeather } from "./city-surface-wetness";

/**
 * T8b: wind- and rain-driven water surface response for the T8a water materials.
 *
 * Patches each shared `MeshPhysicalMaterial` of `createWaterMaterials`
 * (`city-water-material.ts`, left unpatched for exactly this purpose) with a
 * band-limited sinusoidal ripple normal driven by the existing visual weather
 * controls. Reflections stay the PBR response to the existing
 * `scene.environment`: no new render pass, no render target, no planar
 * reflection, no transparency change. No flow direction, depth or bathymetry is
 * implied: the ripples follow the wind only, and rain only moves the slope lost
 * to band limiting back into roughness.
 *
 * Physical model, stated inputs vs. assumptions:
 * - Resolved slope field (design choice, not a measured urban-water spectrum):
 *   twelve sinusoidal waves, so the glint pattern does not repeat like the
 *   measured four-wave fixture. Wavelengths form the geometric series
 *   `0.3 * (5 / 0.3)^(i / 11)` m (0.3 m to 5.0 m, ratio 1.2912), directions sit
 *   at wind direction + `1.2 * sin(i * 2.399963)` rad (golden-angle spread over
 *   +/-1.2 rad without repeats), phases are `2 pi * fract(i * 0.6180339887)` rad;
 *   all three tables are computed in TypeScript from those formulas.
 * - Equal slope share per wave (`m = sigma * sqrt(2 / N)` each): assumption that
 *   equal variance per logarithmic band reproduces the broadband Cox-Munk slope
 *   without a measured wave spectrum; only the total is constrained by data.
 * - Angular frequency from the capillary-gravity dispersion relation
 *   `omega = sqrt(g k + (T / rho) k^3)` with clean-water constants
 *   `g = 9.81` m/s^2, `T / rho = 7.28e-5` m^3/s^2, `k = 2 pi / wavelength`;
 *   computed in TypeScript and written into the shader as literals.
 * - Total RMS slope from the Cox-Munk clean-surface fit
 *   `sigma^2 = 0.003 + 0.00512 U`, `U` = wind speed in m/s.
 *   Assumption: an open-sea fit applied to sheltered urban water, scaled by the
 *   per-type response below (no measured urban-water slope data exists).
 * - Type response (assumption: sheltering by banks and structures; no measured
 *   data): water-river 1.0, water-lake 1.0, water-reservoir 1.0, water-dock 0.8,
 *   water-generic 0.8, water-canal 0.6, water-basin 0.6, water-pond 0.45.
 *   Effective `sigma = typeResponse * sqrt(0.003 + 0.00512 U)`.
 * - Each of the N = 12 waves gets slope amplitude `m = sigma * sqrt(2 / N)` so
 *   the summed slope field has RMS slope `sigma` exactly:
 *   `sqrt(sum_i m_i^2 / 2) = sigma`.
 * - Band limiting by pixel footprint in metres:
 *   `f = max(length(dFdx(vWaterXZ)), length(dFdy(vWaterXZ)))`; wave i keeps
 *   weight `w_i = 1 - smoothstep(0.25 lambda_i, 0.5 lambda_i, f)`. Water whose
 *   footprint passes half its wavelength loses that wave's slope entirely, so
 *   distant water does not shimmer.
 * - Lost slope variance `sigma_lost^2 = sum_i (1 - w_i^2) m_i^2 / 2` re-enters
 *   the GGX roughness: with `alpha = roughness^2`,
 *   `alpha_eff = sqrt(alpha_base^2 + 2 sigma_lost^2)`,
 *   `roughness_eff = sqrt(alpha_eff)`, where `roughness_base` is the material's
 *   roughness after `roughnessmap_fragment` (no roughness map is set on the T8a
 *   materials, so it is the measured look roughness). Rain:
 *   `roughness_eff = max(roughness_eff, mix(roughness_base, 0.32, rain))` with
 *   `rain = surfaceWetnessFromWeather(settings)`. Assumption: a presentation
 *   approximation of drop impacts flattening visible micro-ripples, not a
 *   measured rain roughness; snow and hail give rain 0 exactly as in the
 *   wetness module.
 * - Normal reconstruction assumption: the water geometry is horizontal and
 *   up-facing (built by `terrainSurfaceGeometry`), so the world-space normal can
 *   be replaced wholesale by `normalize(vec3(-grad.x, 1.0, -grad.y))` from the
 *   height gradient `grad h = sum_i w_i m_i cos(k_i dot(d_i, xz) - omega_i t +
 *   phi_i) d_i`, then rotated into view space with the built-in `viewMatrix`.
 *   No other geometry is bound by this patch.
 */

export const WATER_SURFACE_V1 = Object.freeze({
  version: "city-water-surface-v2",
  /** Geometric series 0.3 * (5 / 0.3)^(i / 11) m, i = 0..11; 0.3 m to 5.0 m. */
  wavelengthsM: Object.freeze(Array.from({ length: 12 }, (_, i) =>
    0.3 * Math.pow(5 / 0.3, i / 11))),
  /** 1.2 * sin(i * 2.399963) rad, i = 0..11; golden-angle spread over +/-1.2 rad. */
  directionOffsetsRad: Object.freeze(Array.from({ length: 12 }, (_, i) =>
    1.2 * Math.sin(i * 2.399963))),
  /** 2 pi * fract(i * 0.6180339887) rad, i = 0..11. */
  phasesRad: Object.freeze(Array.from({ length: 12 }, (_, i) =>
    2 * Math.PI * (i * 0.6180339887 % 1))),
  coxMunk: Object.freeze({ base: 0.003, perMps: 0.00512 }),
  /** Clean-water constants of the dispersion relation: g [m/s^2], T/rho [m^3/s^2]. */
  dispersion: Object.freeze({ g: 9.81, tOverRho: 7.28e-5 }),
  rainRoughness: 0.32,
  typeResponse: Object.freeze({
    "water-river": 1.0,
    "water-lake": 1.0,
    "water-reservoir": 1.0,
    "water-dock": 0.8,
    "water-generic": 0.8,
    "water-canal": 0.6,
    "water-basin": 0.6,
    "water-pond": 0.45,
  } as Readonly<Record<string, number>>),
} as const);

export interface WaterSurfaceUniforms {
  /** Seconds of visual time. */
  readonly uWaterTime: { value: number };
  /** Unit travel direction in city (x, z). */
  readonly uWaterWind: { value: THREE.Vector2 };
  /** Wind speed in m/s. */
  readonly uWaterWindMps: { value: number };
  /** Rain intensity 0..1. */
  readonly uWaterRain: { value: number };
}

export function createWaterSurfaceUniforms(): WaterSurfaceUniforms {
  return {
    // Wind (0, -1): direction of travel north, i.e. toward -Z in the city frame.
    uWaterTime: { value: 0 },
    uWaterWind: { value: new THREE.Vector2(0, -1) },
    uWaterWindMps: { value: 0 },
    uWaterRain: { value: 0 },
  };
}

/** Publishes the weather controls into the shared uniforms. Reuses the direction
 * vector: no per-call allocation. */
export function setWaterSurfaceWeather(u: WaterSurfaceUniforms,
    settings: Readonly<CityWeatherSettings>): void {
  checkCityWeatherSettings(settings);
  const theta = settings.windDirectionDeg * Math.PI / 180;
  // City frame: +X east, -Z north; windDirectionDeg is the direction of travel,
  // clockwise from north, so the unit vector is (sin theta, -cos theta).
  u.uWaterWind.value.set(Math.sin(theta), -Math.cos(theta));
  u.uWaterWindMps.value = settings.windMps;
  u.uWaterRain.value = surfaceWetnessFromWeather(settings);
}

export function setWaterSurfaceTime(u: WaterSurfaceUniforms, timeS: number): void {
  if (!Number.isFinite(timeS) || timeS < 0) {
    throw new RangeError("Water surface time must be finite and nonnegative");
  }
  u.uWaterTime.value = timeS;
}

/** Per-wave slope amplitude `m_i = sigma * sqrt(2 / N)` for the tests and the docs. */
export function waterSlopeAmplitudes(presetId: string, windMps: number): readonly number[] {
  const typeResponse = WATER_SURFACE_V1.typeResponse[presetId];
  if (typeResponse === undefined) {
    throw new Error(`Water surface has no type response for preset: ${presetId}`);
  }
  if (!Number.isFinite(windMps) || windMps < 0) {
    throw new RangeError("Water surface wind speed must be finite and nonnegative");
  }
  const sigma = typeResponse * Math.sqrt(WATER_SURFACE_V1.coxMunk.base
    + WATER_SURFACE_V1.coxMunk.perMps * windMps);
  const waves = WATER_SURFACE_V1.wavelengthsM.length;
  return WATER_SURFACE_V1.wavelengthsM.map(() => sigma * Math.sqrt(2 / waves));
}

const SURFACE_MARKER = "city-water-surface/v2";

/** Shortest round-trip GLSL float literal for a computed double. */
function glslNumber(value: number): string {
  if (!Number.isFinite(value)) throw new Error(`Water surface constant is not finite: ${value}`);
  const text = value.toString();
  return text.includes(".") || text.includes("e") || text.includes("E") ? text : `${text}.0`;
}

/** Replaces the single occurrence of `token`; any other count is a patch
 * failure, because silently stacking or skipping would corrupt the shader. */
function replaceOnce(source: string, token: string, replacement: string, label: string): string {
  const first = source.indexOf(token);
  if (first < 0 || source.indexOf(token, first + 1) >= 0) {
    throw new Error(`Water surface could not patch ${label}`);
  }
  return source.slice(0, first) + replacement + source.slice(first + token.length);
}

/** Per-wave shader constants, computed once in TypeScript from the
 * capillary-gravity dispersion relation. */
interface WaterWaveConstants {
  readonly k: number;
  readonly omega: number;
  readonly lambda: number;
  /** cos and sin of the wave's direction offset from the wind direction of travel. */
  readonly offsetCos: number;
  readonly offsetSin: number;
  readonly phase: number;
}

const WAVES: readonly WaterWaveConstants[] = WATER_SURFACE_V1.wavelengthsM.map((lambda, index) => {
  const k = 2 * Math.PI / lambda;
  const { g, tOverRho } = WATER_SURFACE_V1.dispersion;
  return {
    k,
    omega: Math.sqrt(g * k + tOverRho * k * k * k),
    lambda,
    offsetCos: Math.cos(WATER_SURFACE_V1.directionOffsetsRad[index]!),
    offsetSin: Math.sin(WATER_SURFACE_V1.directionOffsetsRad[index]!),
    phase: WATER_SURFACE_V1.phasesRad[index]!,
  };
});

const WATER_UNIFORM_TIME = "uWaterTime";
const WATER_UNIFORM_WIND = "uWaterWind";
const WATER_UNIFORM_WIND_MPS = "uWaterWindMps";
const WATER_UNIFORM_RAIN = "uWaterRain";
const WATER_VARYING = "vWaterXZ";
const WATER_NORMAL_LINE = "normal = waterSurfaceNormal( waterLostSlope2 );";

function waterVertexChunk(): string {
  return `
varying vec2 ${WATER_VARYING};
`;
}

/** The wave table as global GLSL constants; each direction is composed in the
 * function from the wind uniform and the wave's offset rotation. */
function waterWaveConstants(): string {
  return WAVES.map((wave, index) =>
    `const float k${index} = ${glslNumber(wave.k)};      // 2 pi / ${glslNumber(wave.lambda)} m
const float omega${index} = ${glslNumber(wave.omega)};   // sqrt(g k + (T/rho) k^3)
const float lambda${index} = ${glslNumber(wave.lambda)};
const float phi${index} = ${glslNumber(wave.phase)};
const vec2 waveRot${index} = vec2( ${glslNumber(wave.offsetCos)}, ${glslNumber(wave.offsetSin)} );`)
    .join("\n");
}

/** Fragment-stage uniforms, varying and the ripple normal function. The type
 * response is baked in as a literal so the program key pins it per preset. */
function waterFragmentChunk(typeResponse: number): string {
  const count = WAVES.length;
  const weights = WAVES.map((_, index) =>
    `  float w${index} = 1.0 - smoothstep( 0.25 * lambda${index}, 0.5 * lambda${index}, footprint );`)
    .join("\n");
  // Wave i travels along the wind direction rotated by its offset (unit length because
  // uWaterWind is a unit vector and waveRot is (cos, sin) of the offset).
  const directions = WAVES.map((_, index) =>
    `  vec2 waveDir${index} = vec2( waveRot${index}.x * ${WATER_UNIFORM_WIND}.x - waveRot${index}.y * ${WATER_UNIFORM_WIND}.y,
    waveRot${index}.y * ${WATER_UNIFORM_WIND}.x + waveRot${index}.x * ${WATER_UNIFORM_WIND}.y );`)
    .join("\n");
  const gradient = WAVES.map((_, index) =>
    `  grad += w${index} * amp * cos( k${index} * dot( waveDir${index}, ${WATER_VARYING} ) - omega${index} * ${WATER_UNIFORM_TIME} + phi${index} ) * waveDir${index};`)
    .join("\n");
  const lost = WAVES.map((_, index) =>
    `    ( 1.0 - w${index} * w${index} ) * amp * amp / 2.0`)
    .join(" +\n");
  return `
uniform float ${WATER_UNIFORM_TIME};
uniform vec2 ${WATER_UNIFORM_WIND};
uniform float ${WATER_UNIFORM_WIND_MPS};
uniform float ${WATER_UNIFORM_RAIN};
varying vec2 ${WATER_VARYING};

${waterWaveConstants()}

vec3 waterSurfaceNormal( out float lostSlopeVariance ) {
  // Cox-Munk clean-surface fit scaled by this preset's sheltering response.
  float sigma = sqrt( ${glslNumber(WATER_SURFACE_V1.coxMunk.base)} + ${glslNumber(WATER_SURFACE_V1.coxMunk.perMps)} * ${WATER_UNIFORM_WIND_MPS} ) * ${glslNumber(typeResponse)};
  // Each of the ${count} waves carries slope amplitude sigma * sqrt(2 / N).
  float amp = sigma * ${glslNumber(Math.sqrt(2 / count))};
  // Metres per pixel: band limiting keeps each wave only below half its wavelength.
  float footprint = max( length( dFdx( ${WATER_VARYING} ) ), length( dFdy( ${WATER_VARYING} ) ) );
${weights}
${directions}
  vec2 grad = vec2( 0.0 );
${gradient}
  lostSlopeVariance = (
${lost} );
  // World-space normal of the horizontal surface, rotated into view space like vNormal.
  vec3 worldNormal = normalize( vec3( -grad.x, 1.0, -grad.y ) );
  return normalize( ( viewMatrix * vec4( worldNormal, 0.0 ) ).xyz );
}
`;
}

/** The roughness update is placed after the normal line because the lost slope
 * variance is only known there; `roughnessFactor` itself is declared earlier in
 * `roughnessmap_fragment`, and `lights_physical_fragment` reads it later. */
function waterRoughnessChunk(): string {
  return `  float waterLostSlope2;
${WATER_NORMAL_LINE}
  {
    float waterAlphaBase = roughnessFactor * roughnessFactor;
    float waterAlphaEff = sqrt( waterAlphaBase * waterAlphaBase + 2.0 * waterLostSlope2 );
    float waterRoughnessEff = sqrt( max( waterAlphaEff, 0.0 ) );
    waterRoughnessEff = max( waterRoughnessEff, mix( roughnessFactor, ${glslNumber(WATER_SURFACE_V1.rainRoughness)}, clamp( ${WATER_UNIFORM_RAIN}, 0.0, 1.0 ) ) );
    roughnessFactor = waterRoughnessEff;
  }`;
}

/**
 * Patch a water material with the wind- and rain-driven ripple normal.
 *
 * Throws unless the material is an unpatched (no other `onBeforeCompile` or
 * `customProgramCacheKey`) smooth-shaded `MeshPhysicalMaterial` without a
 * normal map, for a preset that has a type response. Applying it twice throws
 * instead of silently stacking the patch. The uniforms are shared by reference:
 * the layer's `setWeather`/`update` reach every patched material through them.
 */
export function applyWaterSurface(material: THREE.MeshPhysicalMaterial, presetId: string,
    u: WaterSurfaceUniforms): void {
  const typeResponse = WATER_SURFACE_V1.typeResponse[presetId];
  if (material.userData.waterSurface !== undefined || Object.hasOwn(material, "onBeforeCompile")
    || Object.hasOwn(material, "customProgramCacheKey")) {
    throw new Error(`Water surface needs an unpatched water material: ${material.name || material.type}`);
  }
  if (typeResponse === undefined) {
    throw new Error(`Water surface has no type response for preset: ${presetId}`);
  }
  if (material.flatShading || material.normalMap !== null) {
    throw new Error(`Water surface needs a smooth-shaded material without a normal map: ${material.name || material.type}`);
  }
  material.onBeforeCompile = shader => {
    shader.uniforms[WATER_UNIFORM_TIME] = u.uWaterTime;
    shader.uniforms[WATER_UNIFORM_WIND] = u.uWaterWind;
    shader.uniforms[WATER_UNIFORM_WIND_MPS] = u.uWaterWindMps;
    shader.uniforms[WATER_UNIFORM_RAIN] = u.uWaterRain;
    shader.vertexShader = replaceOnce(shader.vertexShader, "#include <common>",
      `#include <common>
${waterVertexChunk()}`, "<common> in the vertex stage");
    shader.vertexShader = replaceOnce(shader.vertexShader, "#include <project_vertex>",
      `#include <project_vertex>
#ifdef USE_INSTANCING
  ${WATER_VARYING} = ( modelMatrix * instanceMatrix * vec4( transformed, 1.0 ) ).xz;
#else
  ${WATER_VARYING} = ( modelMatrix * vec4( transformed, 1.0 ) ).xz;
#endif`, "<project_vertex>");
    shader.fragmentShader = replaceOnce(shader.fragmentShader, "#include <common>",
      `#include <common>
${waterFragmentChunk(typeResponse)}`, "<common> in the fragment stage");
    shader.fragmentShader = replaceOnce(shader.fragmentShader, "#include <normal_fragment_maps>",
      `#include <normal_fragment_maps>
${waterRoughnessChunk()}`, "<normal_fragment_maps>");
  };
  // The type response is compiled into the shader, so it must be part of the
  // program key; otherwise presets with equal features would share one program.
  material.customProgramCacheKey = () => `${WATER_SURFACE_V1.version}:${presetId}`;
  material.userData.waterSurface = { version: WATER_SURFACE_V1.version, presetId, typeResponse,
    marker: SURFACE_MARKER };
  material.needsUpdate = true;
}
