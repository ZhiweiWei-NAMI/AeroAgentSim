import * as THREE from "three";
import { Sky } from "three/addons/objects/Sky.js";
import type { CityWeatherSettings } from "./city-weather";

/** The Sky shader uses this distance when fading Rayleigh scattering with sun height. */
export const CITY_DAY_SKY_SUN_DISTANCE_M = 450_000;
export const CITY_DAY_SKY_RADIUS_M = 10_000;
export const CITY_DAY_SKY_RADIANCE_GAIN_DEFAULT = 0.3;

const DEFAULT_SUN_DIRECTION = new THREE.Vector3(-0.68, 0.47, 0.56);
const MIN_VISIBILITY_M = 350;
const REFERENCE_VISIBILITY_M = 10_000;
const SKY_SHADER_GAIN_DECLARATION_ANCHOR = "uniform float time;";
const SKY_SHADER_OUTPUT = "gl_FragColor = vec4( texColor, 1.0 );";

function replaceSkyShaderTokenOnce(source: string, token: string, replacement: string): string {
  const count = source.split(token).length - 1;
  if (count !== 1) throw new Error(`Three.js Sky shader changed: expected one ${token.trim()} token`);
  return source.replace(token, replacement);
}

function installRadianceGain(sky: Sky, gain: number): void {
  const material = sky.material;
  const withGainUniform = replaceSkyShaderTokenOnce(material.fragmentShader,
    SKY_SHADER_GAIN_DECLARATION_ANCHOR,
    `${SKY_SHADER_GAIN_DECLARATION_ANCHOR}\n\t\tuniform float cityDaySkyRadianceGain;`);
  material.fragmentShader = replaceSkyShaderTokenOnce(withGainUniform, SKY_SHADER_OUTPUT,
    SKY_SHADER_OUTPUT.replace("texColor", "texColor * cityDaySkyRadianceGain"));
  material.uniforms.cityDaySkyRadianceGain = { value: gain };
  material.needsUpdate = true;
}

function checkedRadianceGain(gain: number): number {
  if (!Number.isFinite(gain) || gain <= 0 || gain > 1) {
    throw new RangeError("Sky radiance gain must be greater than 0 and at most 1");
  }
  return gain;
}

/**
 * Build one camera-following atmospheric sky draw for the ordinary city preview.
 * The verified source HDRI stays available separately for scene.environment and
 * local reflection capture. Procedural cloud noise is disabled here because
 * CityWeather owns the existing controllable cloud, rain and snow layer.
 */
export function createCityDaySky(radianceGain = CITY_DAY_SKY_RADIANCE_GAIN_DEFAULT): Sky {
  checkedRadianceGain(radianceGain);
  const sky = new Sky();
  sky.name = "city-day-atmosphere";
  sky.frustumCulled = false;
  sky.renderOrder = -1000;
  sky.scale.setScalar(CITY_DAY_SKY_RADIUS_M);
  installRadianceGain(sky, radianceGain);

  const uniforms = sky.material.uniforms;
  uniforms.turbidity!.value = 2.2;
  uniforms.rayleigh!.value = 1.8;
  uniforms.mieCoefficient!.value = 0.004;
  uniforms.mieDirectionalG!.value = 0.78;
  uniforms.showSunDisc!.value = 0;
  uniforms.cloudCoverage!.value = 0;
  uniforms.cloudDensity!.value = 0;
  setCityDaySkySunDirection(sky, DEFAULT_SUN_DIRECTION);
  return sky;
}

/** Scale sky radiance before tone mapping without changing building exposure or global IBL. */
export function setCityDaySkyRadianceGain(sky: Sky, gain: number): void {
  const uniform = sky.material.uniforms.cityDaySkyRadianceGain;
  if (uniform === undefined) throw new Error("Sky was not created by createCityDaySky");
  uniform.value = checkedRadianceGain(gain);
}

/** Keep the sky's analytic sun aligned with the directional city light. */
export function setCityDaySkySunDirection(sky: Sky, direction: THREE.Vector3): void {
  if (![direction.x, direction.y, direction.z].every(Number.isFinite)
      || direction.lengthSq() < 1e-12) {
    throw new RangeError("Sky sun direction must be a finite, nonzero vector");
  }
  sky.material.uniforms.sunPosition!.value.copy(direction).normalize()
    .multiplyScalar(CITY_DAY_SKY_SUN_DISTANCE_M);
}

/**
 * Tint scattering for the selected visual weather. Visibility still controls
 * city fog in CityWeather; this only keeps the distant sky from staying vivid
 * blue in low-visibility presets.
 */
export function setCityDaySkyWeather(sky: Sky, settings: CityWeatherSettings): void {
  if (!Number.isFinite(settings.cloudCover) || settings.cloudCover < 0 || settings.cloudCover > 1) {
    throw new RangeError("cloudCover must be between 0 and 1");
  }
  if (!Number.isFinite(settings.visibilityM) || settings.visibilityM <= 0) {
    throw new RangeError("visibilityM must be positive");
  }

  const visibilityHaze = THREE.MathUtils.clamp(
    (REFERENCE_VISIBILITY_M - settings.visibilityM) / (REFERENCE_VISIBILITY_M - MIN_VISIBILITY_M),
    0,
    1,
  );
  const cover = settings.cloudCover;
  const uniforms = sky.material.uniforms;
  uniforms.turbidity!.value = 2.2 + cover * 2.8 + visibilityHaze * 2.2;
  uniforms.rayleigh!.value = 1.8 - cover * 0.35 - visibilityHaze * 0.2;
  uniforms.mieCoefficient!.value = 0.004 + cover * 0.0015 + visibilityHaze * 0.001;
  // The established CityWeather cloud meshes carry the preset's cloud coverage.
  // Leave Sky's optional five-octave per-pixel cloud shader disabled.
  uniforms.cloudCoverage!.value = 0;
  uniforms.cloudDensity!.value = 0;
}

/** Recenter the sky dome so its scattering stays effectively infinitely distant. */
export function centerCityDaySky(sky: Sky, cameraPosition: THREE.Vector3): void {
  if (![cameraPosition.x, cameraPosition.y, cameraPosition.z].every(Number.isFinite)) {
    throw new RangeError("Sky camera position must be finite");
  }
  sky.position.copy(cameraPosition);
}

export function disposeCityDaySky(sky: Sky): void {
  sky.geometry.dispose();
  sky.material.dispose();
}
