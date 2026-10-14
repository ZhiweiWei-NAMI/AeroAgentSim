import * as T from 'three';
import type { Conditions } from '../conditions';
import { IncidentFX } from './incident-fx';
import { Rain } from './rain';
import { WindStreaks } from './wind';
import { applySkyWeather, type SkyTarget } from './sky-weather';
import type { Quality } from '../pipeline';

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
  private wind: WindStreaks;
  private skyTarget?: SkyTarget;
  constructor(scene: T.Scene, renderer: T.WebGLRenderer, quality: Quality) {
    this.incidents = new IncidentFX(quality);
    this.ready = this.incidents.ready;
    this.rain = new Rain(quality);
    this.wind = new WindStreaks(quality);
    this.root.add(this.incidents.root, this.rain.mesh, this.wind.mesh);
    scene.add(this.root);
    // The draft preview uses its own simple lights without the full sky rig; sky coupling only applies where setupScene ran.
    const lighting = scene.userData.lighting as SkyTarget | undefined;
    this.skyTarget = lighting ? { ...lighting, renderer } : undefined;
  }
  setQuality(quality: Quality) {
    this.incidents.setQuality(quality);
    this.rain.setQuality(quality);
    this.wind.setQuality(quality);
  }
  update(conditions: Conditions, simSeconds: number, camera: T.Camera,
    roofHeight?: (east: number, north: number) => number, ringStart?: Map<string, number>, origin?: T.Vector3, light = conditions.light) {
    this.incidents.setWind(conditions.wind?.east ?? 0, conditions.wind?.north ?? 0);
    const ring = ringStart ?? new Map();
    this.incidents.update(conditions.incidents, simSeconds, camera, ring, origin, roofHeight);
    this.rain.update(conditions.precipitationMmH, simSeconds, camera, conditions.wind ?? undefined);
    this.wind.update(conditions.wind, simSeconds, camera);
    if (this.skyTarget) applySkyWeather(this.skyTarget, conditions, light === 'dusk');
  }
  dispose() {
    this.incidents.dispose(); this.rain.dispose(); this.wind.dispose();
    this.root.removeFromParent();
  }
}
