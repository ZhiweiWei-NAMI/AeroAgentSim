import * as T from 'three';
import type { Conditions } from '../conditions';

/**
 * Sky/fog/exposure coupling: rain, fog and light must read as ONE weather, not overlays.
 * Extends the existing setDusk path (scene.ts) rather than replacing it. All steps are
 * idempotent functions of the current conditions, so reverting is exact.
 * Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it.
 */

const FOG_DAY = new T.Color('#c3c9cf'), FOG_DUSK = new T.Color('#6f6a73');
const FILL_CLEAR = new T.Color('#aebfd4');

/** Exponential fog density so contrast reaches 5 % at visibility V: √(−ln 0.05)/V ≈ 1.731/V. */
export const fogDensity = (visibilityM: number) => Math.sqrt(-Math.log(0.05)) / visibilityM;

export interface SkyTarget {
  scene: T.Scene;
  renderer: T.WebGLRenderer;
  sun: T.DirectionalLight;
  fill: T.HemisphereLight;
  sky: T.Mesh & { material: T.ShaderMaterial };
  daySky: T.Mesh & { material: T.ShaderMaterial };
  horizon: T.Color;
}

export function applySkyWeather(target: SkyTarget, conditions: Conditions, dusk: boolean) {
  const { scene, renderer, sun, fill, sky, daySky, horizon } = target;
  const r = Math.min(conditions.precipitationMmH / 8, 1);
  const base = scene.userData.weatherBase as { sun: number; fill: number; exposure: number } ??
    (scene.userData.weatherBase = { sun: sun.intensity, fill: fill.intensity, exposure: renderer.toneMappingExposure });
  // Sun × (1 − 0.45·r); hemisphere light shifts cooler; exposure × (1 − 0.15·r).
  sun.intensity = base.sun * (1 - 0.45 * r);
  fill.color.set(dusk ? '#91a8d5' : '#dceeff').lerp(FILL_CLEAR, r * 0.35);
  fill.intensity = base.fill;
  renderer.toneMappingExposure = base.exposure * (1 - 0.15 * r);
  // Sky turbidity rises and Rayleigh falls with rain.
  sky.material.uniforms.turbidity.value = (dusk ? 3.5 : 2.1) + r * 3;
  if (sky.material.uniforms.rayleigh) sky.material.uniforms.rayleigh.value = 1.1 * (1 - 0.5 * r);
  // Visibility fog: FogExp2 when denser than the art-direction fog, else keep the existing Fog.
  const art = scene.fog instanceof T.Fog && !(scene.fog instanceof T.FogExp2) ? scene.fog : undefined;
  const weatherFog = scene.fog instanceof T.FogExp2 ? scene.fog : undefined;
  if (conditions.visibilityM != null) {
    // City scale caps the practical density; 1.731/50000 is effectively clear.
    const density = Math.max(fogDensity(conditions.visibilityM), 0);
    if (!weatherFog) scene.fog = new T.FogExp2((art ?? { color: horizon } as T.Fog).color.getHex(), density);
    else weatherFog.density = density;
    scene.fog.color.copy(horizon).lerp(dusk ? FOG_DUSK : FOG_DAY, 0.35);
  } else if (weatherFog) {
    // Clear (null) restores today's look exactly.
    const linear = new T.Fog(weatherFog.color, dusk ? 850 : 500, dusk ? 4200 : 2000);
    weatherFog.dispose(); scene.fog = linear;
  } else if (art) art.color.copy(horizon);
  if (scene.fog) {
    if (art) art.color.copy(scene.fog.color);
    // The sky dome horizon band blends to the same colour so nothing seams.
    daySky.material.uniforms.horizon.value.copy(scene.fog.color);
    scene.background = scene.fog.color;
  }
  // Wet look is read by the viewport's applyCityDisplay via userData.weatherWet and reverted exactly.
  scene.userData.weatherWet = r;
}
