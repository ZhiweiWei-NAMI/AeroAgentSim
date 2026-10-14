import * as T from 'three';
import type { Conditions } from '../conditions';

/**
 * Sky/fog/exposure coupling: rain, fog and light must read as ONE weather, not overlays.
 * Extends the existing setDusk path (scene.ts) rather than replacing it. All steps are
 * idempotent functions of the current conditions, so reverting is exact.
 * Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it.
 */

const FOG_DAY = new T.Color('#c3c9cf'), FOG_DUSK = new T.Color('#6f6a73');
const HORIZON_DUSK = new T.Color('#7f91aa');
const SUN_DUSK = new T.Color('#ffba79');
const FILL_CLEAR = new T.Color('#aebfd4');

/** Exponential fog density so contrast reaches 5 % at visibility V: √(−ln 0.05)/V ≈ 1.731/V. */
export const fogDensity = (visibilityM: number) => Math.sqrt(-Math.log(0.05)) / visibilityM;

export interface SkyTarget {
  scene: T.Scene;
  renderer: T.WebGLRenderer;
  sun: T.DirectionalLight;
  fill: T.HemisphereLight;
  sky?: T.Mesh & { material: T.ShaderMaterial };
  daySky?: T.Mesh & { material: T.ShaderMaterial };
  horizon: T.Color;
}

export function applySkyWeather(target: SkyTarget, conditions: Conditions, dusk: boolean) {
  const { scene, renderer, sun, fill, sky, daySky, horizon } = target;
  const r = Math.min(conditions.precipitationMmH / 8, 1);
  const base = scene.userData.weatherBase as { sun: number; fill: number; exposure: number } ??
    (scene.userData.weatherBase = { sun: sun.intensity, fill: fill.intensity, exposure: renderer.toneMappingExposure });
  if (!sky) {
    // The draft keeps its existing simple rig; apply the same dusk ratios and fog.
    const clear = scene.userData.weatherHorizon as T.Color ?? (scene.userData.weatherHorizon = horizon.clone());
    horizon.copy(dusk ? HORIZON_DUSK : clear);
    const sunColor = scene.userData.weatherSunColor as T.Color ?? (scene.userData.weatherSunColor = sun.color.clone());
    sun.color.copy(dusk ? SUN_DUSK : sunColor);
  }
  // Sun × (1 − 0.45·r); hemisphere light shifts cooler; exposure × (1 − 0.15·r).
  sun.intensity = base.sun * (!sky && dusk ? 1.5 / 3.1 : 1) * (1 - 0.45 * r);
  const fillColor = sky ? undefined : scene.userData.weatherFillColor as T.Color ?? (scene.userData.weatherFillColor = fill.color.clone());
  if (!dusk && fillColor) fill.color.copy(fillColor); else fill.color.set(dusk ? '#91a8d5' : '#dceeff');
  fill.color.lerp(FILL_CLEAR, r * 0.35);
  fill.intensity = base.fill * (!sky && dusk ? 0.35 / 1.45 : 1);
  renderer.toneMappingExposure = base.exposure * (!sky && dusk ? 1 / 1.12 : 1) * (1 - 0.15 * r);
  // Sky turbidity rises and Rayleigh falls with rain.
  if (sky) {
    sky.material.uniforms.turbidity.value = (dusk ? 3.5 : 2.1) + r * 3;
    if (sky.material.uniforms.rayleigh) sky.material.uniforms.rayleigh.value = 1.1 * (1 - 0.5 * r);
  }
  // Visibility fog: FogExp2 when denser than the art-direction fog, else keep the existing Fog.
  const art = scene.userData.weatherArtFog as T.Fog | undefined ?? (scene.fog instanceof T.Fog ? scene.fog : undefined);
  if (art) {
    scene.userData.weatherArtFog = art;
    art.near = dusk ? 850 : 500; art.far = dusk ? 4200 : 2000; art.color.copy(horizon);
  }
  const weatherFog = scene.fog instanceof T.FogExp2 ? scene.fog : undefined;
  const density = conditions.visibilityM === null ? 0 : fogDensity(conditions.visibilityM);
  const artDensity = art ? fogDensity(art.near + 0.95 * (art.far - art.near)) : 0;
  if (density > artDensity) {
    const fog = weatherFog ?? new T.FogExp2(horizon, density);
    fog.density = density;
    fog.color.copy(horizon).lerp(dusk ? FOG_DUSK : FOG_DAY, 0.35);
    scene.fog = fog;
  } else scene.fog = art ?? null;
  if (scene.fog) {
    // The sky dome horizon band blends to the same colour so nothing seams.
    daySky?.material.uniforms.horizon.value.copy(scene.fog.color);
    scene.background = scene.fog.color;
  } else scene.background = horizon;
  // Wet look is read by the viewport's applyCityDisplay via userData.weatherWet and reverted exactly.
  scene.userData.weatherWet = r;
}

/** Existing city display hooks share one reversible wet-material adjustment. */
export function applyWeatherMaterials(object: T.Object3D, wet: number, dusk: boolean) {
  object.traverse(node => { if (node instanceof T.Mesh) for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
    const uniform = material.userData.displayDusk as { value: number } | undefined;
    if (uniform) uniform.value = dusk ? 1 : 0;
    const standard = material as T.MeshStandardMaterial;
    if (!(standard.color instanceof T.Color)) continue;
    const base = standard.userData.weatherBase as { color: T.Color; roughness?: number } | undefined;
    if (wet <= 0) {
      if (base) { standard.color.copy(base.color); if (base.roughness !== undefined) standard.roughness = base.roughness; delete standard.userData.weatherBase; }
      continue;
    }
    const source = base ?? (standard.userData.weatherBase = { color: standard.color.clone(), roughness: standard.roughness });
    standard.color.copy(source.color).multiplyScalar(1 - 0.08 * wet);
    if (source.roughness !== undefined) standard.roughness = Math.max(0, source.roughness - 0.1 * wet);
  } });
}
