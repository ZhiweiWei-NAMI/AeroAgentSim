import * as THREE from "three";
import type { Sky } from "three/addons/objects/Sky.js";
import { applyCitySkyCalibration, CityCalibratedEnvironment,
  type CityLightingSample } from "./city-lighting-calibration";

export const CITY_WEATHER_SKY_PMREM_SIZE = 128;

export type CitySkyReflectionProfile =
  | { readonly kind: "visible-sky" }
  | { readonly kind: "verified-hdr-for-clear"; readonly environment: CityCalibratedEnvironment };

export interface CityWeatherSkyEnvironmentState {
  readonly source: "verified-hdr" | "procedural-sky" | null;
  readonly skyCaptures: number;
  readonly pmremFaceSize: number;
  readonly containsCityGeometry: false;
  readonly policy: CitySkyReflectionProfile["kind"];
}

/**
 * The dataset declares visible Sky or verified HDR for clear weather. Graded
 * cloud/haze always samples the same visible calibrated Sky. One PMREM is retained,
 * and identical appearance samples do no work. This is a static sky environment;
 * the separate local city probe supplies rendered neighbours.
 */
export class CityWeatherSkyEnvironment {
  private readonly scene = new THREE.Scene();
  private capturedSky: THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial> | null = null;
  private pmrem: THREE.PMREMGenerator | null = null;
  private target: THREE.WebGLRenderTarget | null = null;
  private key: string | null = null;
  private source: CityWeatherSkyEnvironmentState["source"] = null;
  private captures = 0;
  private disposed = false;
  private readonly profile: CitySkyReflectionProfile;

  constructor(private readonly renderer: THREE.WebGLRenderer,
    profile: CitySkyReflectionProfile) {
    if (profile?.kind === "visible-sky") this.profile = { kind: profile.kind };
    else if (profile?.kind === "verified-hdr-for-clear" && profile.environment instanceof CityCalibratedEnvironment) {
      this.profile = { kind: profile.kind, environment: profile.environment };
    } else throw new Error("City sky reflection presentation profile is invalid");
  }

  get state(): CityWeatherSkyEnvironmentState {
    return { source: this.source, skyCaptures: this.captures,
      pmremFaceSize: CITY_WEATHER_SKY_PMREM_SIZE, containsCityGeometry: false, policy: this.profile.kind };
  }

  /** Call after a lighting/weather change; streamed buildings reuse the result. */
  textureFor(sky: Sky, sample: CityLightingSample): THREE.Texture {
    if (this.disposed) throw new Error("City weather sky environment is disposed");
    applyCitySkyCalibration(sky, sample);
    // The cloud threshold matches the visible grade; reduced visibility also
    // changes the Sky scattering. This is an explicit display source policy.
    if (this.profile.kind === "verified-hdr-for-clear" && sample.weather.settings.cloudCover <= 0.15
        && sample.weather.settings.visibilityM >= 10_000) {
      const texture = this.profile.environment.textureFor(sample.timeOfDay);
      this.source = "verified-hdr";
      return texture;
    }
    const key = JSON.stringify([sample.solar.elevationDeg, ...sample.sunDirection.toArray(),
      sample.weather.settings.cloudCover, sample.weather.settings.visibilityM]);
    if (key === this.key && this.target !== null) {
      this.source = "procedural-sky";
      return this.target.texture;
    }
    if (this.capturedSky === null) {
      // Only the material is owned here. The original Sky geometry is borrowed.
      this.capturedSky = new THREE.Mesh(sky.geometry, sky.material.clone());
      this.capturedSky.frustumCulled = false;
      this.scene.add(this.capturedSky);
    } else {
      this.capturedSky.geometry = sky.geometry;
      this.capturedSky.material.copy(sky.material);
      this.capturedSky.material.needsUpdate = true;
    }
    this.capturedSky.position.set(0, 0, 0);
    this.capturedSky.scale.copy(sky.scale);
    this.capturedSky.updateMatrixWorld(true);
    this.pmrem ??= new THREE.PMREMGenerator(this.renderer);
    const previous = this.target;
    const oldTarget = this.renderer.getRenderTarget();
    const oldFace = this.renderer.getActiveCubeFace();
    const oldMipmap = this.renderer.getActiveMipmapLevel();
    const oldXr = this.renderer.xr.enabled;
    const oldAutoClear = this.renderer.autoClear;
    const oldShadowAutoUpdate = this.renderer.shadowMap.autoUpdate;
    const oldToneMapping = this.renderer.toneMapping;
    try {
      this.renderer.shadowMap.autoUpdate = false;
      const next = this.pmrem.fromScene(this.scene, 0, 0.1, sky.scale.length() * 2,
        { size: CITY_WEATHER_SKY_PMREM_SIZE });
      this.target = next;
      this.key = key;
      this.captures++;
      this.source = "procedural-sky";
      previous?.dispose();
      return next.texture;
    } finally {
      // PMREM normally restores state; retain the same rule on a failed pass.
      this.renderer.setRenderTarget(oldTarget, oldFace, oldMipmap);
      this.renderer.xr.enabled = oldXr;
      this.renderer.autoClear = oldAutoClear;
      this.renderer.shadowMap.autoUpdate = oldShadowAutoUpdate;
      this.renderer.toneMapping = oldToneMapping;
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.target?.dispose(); this.pmrem?.dispose();
    this.capturedSky?.material.dispose();
    this.scene.clear();
    this.target = null; this.pmrem = null; this.capturedSky = null;
    this.key = null; this.source = null; this.disposed = true;
  }
}
