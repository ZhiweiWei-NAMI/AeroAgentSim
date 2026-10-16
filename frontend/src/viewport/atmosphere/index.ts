import * as T from 'three';
import type { Conditions } from '../conditions';
import { IncidentFX, type AtmosphereOptions } from './incident-fx';
import { Rain } from './rain';
import { WindStreaks } from './wind';
import { ScalarUniform } from './fx-shared';
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
  private time = new ScalarUniform();
  private pinHeight = 0;
  private viewportHeight?: number;
  private windEast = NaN;
  private windNorth = NaN;
  private emptyRing = new Map<string, number>();
  private preview?: {
    background: T.Scene['background']; fog: T.Scene['fog']; horizon: T.Color; exposure: number;
    lights: Array<{ light: T.Light; intensity: number; color: T.Color }>;
  };
  private lastPreview?: { dusk: boolean; rain: number; visibilityM: number | null; observerOffsetM: number };
  private previewFog = new T.Fog(0xffffff);
  constructor(private scene: T.Scene, private renderer: T.WebGLRenderer, quality: Quality, options: AtmosphereOptions = { annotations: true }) {
    this.root.userData.structureRevision = 0;
    this.incidents = new IncidentFX(quality, { ...options, onReady: () => options.onReady?.() });
    this.ready = this.incidents.ready;
    this.rain = new Rain(quality);
    this.root.add(this.incidents.root, this.rain.mesh);
    if (options.annotations) { this.wind = new WindStreaks(quality); this.root.add(this.wind.mesh); }
    scene.add(this.root);
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
  invalidateSurface() { this.incidents.invalidateSurface(); }
  setPinHeightCss(heightCssPx: number) { this.viewportHeight = heightCssPx; this.incidents.setPinScale(heightCssPx); }
  update(conditions: Conditions, simSeconds: number, camera: T.Camera,
    roofHeight?: (east: number, north: number) => number, ringStart?: Map<string, number>, origin?: T.Vector3, orbitTarget?: T.Vector3) {
    if (this.preview) this.applyPreview(conditions, orbitTarget ? observerFogOffset(camera, orbitTarget) : 0);
    this.time.value = simSeconds;
    const east = conditions.wind?.east ?? 0, north = conditions.wind?.north ?? 0;
    if (east !== this.windEast || north !== this.windNorth) {
      this.incidents.setWind(east, north); this.windEast = east; this.windNorth = north;
    }
    // The run viewport supplies CSS height from its resize observer. Preview
    // and offscreen capture owners may still derive it from their own canvas.
    const dom = this.renderer.domElement;
    const height = this.viewportHeight ?? (dom.clientHeight || Math.round(dom.height / this.renderer.getPixelRatio()));
    if (height !== this.pinHeight) { this.pinHeight = height; this.incidents.setPinScale(height); }
    const ring = ringStart ?? this.emptyRing;
    this.incidents.update(conditions.incidents, this.time, camera, ring, origin, roofHeight);
    this.root.userData.structureRevision = this.incidents.structureRevision;
    // Smoke lighting art direction follows the same dusk/rain inputs the display lighting rig
    // already applied (the Scene/Day/Dusk override included) — journaled light only via it —
    // and the flame ramp this frame's fires wrote, so the underlight dies with the flames.
    this.incidents.setSmokeLight(this.scene, conditions);
    this.rain.update(conditions.precipitationMmH, this.time, camera, conditions.wind ?? undefined);
    this.wind?.update(conditions.wind, this.time, camera);
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
    this.scene.userData.dusk = dusk;
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
