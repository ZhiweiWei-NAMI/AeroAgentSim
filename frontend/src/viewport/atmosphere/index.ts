import * as T from 'three';
import type { Conditions } from '../conditions';
import { IncidentFX, type AtmosphereOptions } from './incident-fx';
import { Rain } from './rain';
import { WindStreaks } from './wind';
import { observerFogOffset, visibilityFog } from '../scene';
import type { Quality } from '../pipeline';
export type { AtmosphereOptions } from './incident-fx';

const FOG_DAY = new T.Color('#c3c9cf'), FOG_DUSK = new T.Color('#6f6a73'), WARM = new T.Color('#ffba79');

/** Pin disc size in CSS px (screen-space sprite, see IncidentFX.setPinScale). */
export const PIN_CSS_PX = 26;

/**
 * One atmosphere module for the run viewport, captures and the draft preview.
 * Every effect is a closed-form function of (journaled cut, simulation time):
 * seek, replay, refresh and capture reproduce the same picture, and a paused
 * clock changes nothing, so idle rendering stays at 0 draws.
 * Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it.
 */
export class AtmosphereLayer {
  readonly root = new T.Group();
  readonly ready: Promise<void>;
  private incidents: IncidentFX;
  private rain: Rain;
  private wind?: WindStreaks;
  private emptyRing = new Map<string, number>();
  private preview?: {
    background: T.Scene['background']; fog: T.Scene['fog']; horizon: T.Color; exposure: number;
    lights: Array<{ light: T.Light; intensity: number; color: T.Color }>;
  };
  private lastPreview?: { dusk: boolean; rain: number; visibilityM: number | null; observerOffsetM: number };
  private previewFog = new T.Fog(0xffffff);
  private fill?: T.HemisphereLight;
  private smokeAmbient = new T.Color();
  constructor(private scene: T.Scene, private renderer: T.WebGLRenderer, quality: Quality, options: AtmosphereOptions = { annotations: true }) {
    this.incidents = new IncidentFX(quality, { ...options, onReady: () => options.onReady?.() });
    this.ready = this.incidents.ready;
    this.rain = new Rain(quality);
    this.root.add(this.incidents.root, this.rain.mesh);
    if (options.annotations) { this.wind = new WindStreaks(quality); this.root.add(this.wind.mesh); }
    scene.add(this.root);
    scene.traverse(node => { if (node instanceof T.HemisphereLight) this.fill = node; });
    // Full rigs are driven by the owner's setAtmosphere path; previews retain their simple lights.
    if (!scene.userData.lighting?.setAtmosphere) {
      const lights: NonNullable<typeof this.preview>['lights'] = [];
      scene.traverse(node => { if (node instanceof T.DirectionalLight || node instanceof T.HemisphereLight) lights.push({ light: node, intensity: node.intensity, color: node.color.clone() }); });
      const horizon = (scene.background instanceof T.Color ? scene.background : scene.fog?.color ?? new T.Color()).clone();
      this.preview = { background: scene.background, fog: scene.fog, horizon, exposure: renderer.toneMappingExposure, lights };
    }
  }
  setQuality(quality: Quality) {
    this.incidents.setQuality(quality);
    this.rain.setQuality(quality);
    this.wind?.setQuality(quality);
  }
  setPinHeightCss(heightCssPx: number) { this.incidents.setPinScale(heightCssPx); }
  update(conditions: Conditions, simSeconds: number, camera: T.Camera,
    roofHeight?: (east: number, north: number) => number, ringStart?: Map<string, number>, origin?: T.Vector3, orbitTarget?: T.Vector3) {
    if (this.preview) this.applyPreview(conditions, orbitTarget ? observerFogOffset(camera, orbitTarget) : 0);
    // Smoke reflects the existing rig's ambient light rather than emitting its
    // palette at full brightness. Average the sky/ground light for soft volume sprites.
    if (this.fill) {
      this.smokeAmbient.copy(this.fill.color).add(this.fill.groundColor).multiplyScalar(this.fill.intensity * 0.5);
      this.incidents.setSmokeLight(this.smokeAmbient);
    }
    this.incidents.setWind(conditions.wind?.east ?? 0, conditions.wind?.north ?? 0);
    // The pin is screen-space: size it from the container's CSS height (the drawing buffer
    // would double-count devicePixelRatio).
    const dom = this.renderer.domElement;
    this.incidents.setPinScale(dom.clientHeight || Math.round(dom.height / this.renderer.getPixelRatio()));
    const ring = ringStart ?? this.emptyRing;
    this.incidents.update(conditions.incidents, simSeconds, camera, ring, origin, roofHeight);
    this.rain.update(conditions.precipitationMmH, simSeconds, camera, conditions.wind ?? undefined);
    this.wind?.update(conditions.wind, simSeconds, camera);
  }
  private applyPreview(conditions: Conditions, observerOffsetM: number) {
    const dusk = conditions.light === 'dusk', rain = Math.min(conditions.precipitationMmH / 8, 1), visibilityM = conditions.visibilityM;
    const previous = this.lastPreview;
    if (previous && previous.dusk === dusk && previous.rain === rain && previous.visibilityM === visibilityM
      && (visibilityM === null || Math.abs(previous.observerOffsetM - observerOffsetM) <= 5)) return;
    this.lastPreview = { dusk, rain, visibilityM, observerOffsetM };
    const base = this.preview!;
    if (visibilityM !== null) {
      this.previewFog.color.copy(base.horizon);
      if (dusk) this.previewFog.color.set('#7f91aa');
      this.previewFog.color.lerp(dusk ? FOG_DUSK : FOG_DAY, 0.35);
      this.scene.fog = visibilityFog(base.fog, this.previewFog, visibilityM, observerOffsetM);
      this.scene.background = this.scene.fog === this.previewFog ? this.previewFog.color : dusk ? this.previewFog.color.set('#7f91aa') : base.background;
    } else {
      this.scene.fog = base.fog;
      this.scene.background = dusk ? this.previewFog.color.set('#7f91aa') : base.background;
    }
    for (const { light, intensity, color } of base.lights) {
      light.intensity = intensity * (dusk ? 0.5 : 1) * (light instanceof T.DirectionalLight ? 1 - 0.45 * rain : 1);
      light.color.copy(color); if (dusk) light.color.lerp(WARM, 0.5);
    }
    this.renderer.toneMappingExposure = base.exposure * (1 - 0.15 * rain);
    this.scene.userData.weatherWet = rain;
  }
  dispose() {
    this.incidents.dispose(); this.rain.dispose(); this.wind?.dispose();
    if (this.preview) {
      this.scene.background = this.preview.background; this.scene.fog = this.preview.fog;
      this.renderer.toneMappingExposure = this.preview.exposure;
      for (const { light, intensity, color } of this.preview.lights) { light.intensity = intensity; light.color.copy(color); }
    }
    this.root.removeFromParent();
  }
}
