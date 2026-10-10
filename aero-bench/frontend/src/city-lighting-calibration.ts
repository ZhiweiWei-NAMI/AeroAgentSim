import * as THREE from "three";
import { HDRLoader } from "three/addons/loaders/HDRLoader.js";
import type { Sky } from "three/addons/objects/Sky.js";
import { setCityDaySkyRadianceGain, setCityDaySkySunDirection, setCityDaySkyWeather } from "./city-day-sky";
import { readBoundedResponse } from "./verified-bytes";
import { checkCityWeatherInput, cityWeatherLightFactors, type CityWeatherInput } from "./city-weather-calibration";

export type CityTimeOfDay = "day" | "twilight" | "night";
export type CitySolarInput =
  | { readonly kind: "artistic-preset"; readonly timeOfDay: CityTimeOfDay;
      readonly elevationDeg: number; readonly azimuthDeg: number }
  | { readonly kind: "sun-position"; readonly elevationDeg: number; readonly azimuthDeg: number;
      readonly sourceId: string; readonly sampleTimeSeconds: number };

export interface CityLightingSample {
  readonly timeOfDay: CityTimeOfDay;
  readonly daylight: number;
  readonly solar: CitySolarInput;
  readonly weather: CityWeatherInput;
  readonly sunDirection: THREE.Vector3;
  readonly sunColor: THREE.Color;
  readonly sunIntensity: number;
  readonly skyFill: THREE.Color;
  readonly groundFill: THREE.Color;
  readonly hemisphereIntensity: number;
  readonly ambientColor: THREE.Color;
  readonly ambientIntensity: number;
  readonly environmentIntensity: number;
  readonly facadeEnvironmentIntensity: number;
  readonly exposure: number;
  readonly backgroundIntensity: number;
  readonly windowIntensity: number;
  readonly windowColor: THREE.Color;
  readonly wetness: number;
}

const SOLAR_PRESETS = {
  day: { elevationDeg: 35, azimuthDeg: 230 },
  twilight: { elevationDeg: 2, azimuthDeg: 260 },
  night: { elevationDeg: -12, azimuthDeg: 290 },
} as const;

export function cityVisualSolar(timeOfDay: CityTimeOfDay): CitySolarInput {
  if (!Object.hasOwn(SOLAR_PRESETS, timeOfDay)) throw new Error(`Unknown city time of day: ${timeOfDay}`);
  return { kind: "artistic-preset", timeOfDay, ...SOLAR_PRESETS[timeOfDay] };
}

function checkSolar(input: CitySolarInput): void {
  if (input === null || typeof input !== "object"
      || !Number.isFinite(input.elevationDeg) || input.elevationDeg < -90 || input.elevationDeg > 90
      || !Number.isFinite(input.azimuthDeg)) throw new Error("Finite sun elevation and azimuth are required");
  if (input.kind === "sun-position") {
    if (typeof input.sourceId !== "string" || !input.sourceId.trim()
        || !Number.isFinite(input.sampleTimeSeconds) || input.sampleTimeSeconds < 0) {
      throw new Error("Sun position needs a source identity and nonnegative sample time");
    }
  } else if (input.kind === "artistic-preset") {
    const preset = SOLAR_PRESETS[input.timeOfDay];
    if (preset === undefined || preset.elevationDeg !== input.elevationDeg
        || preset.azimuthDeg !== input.azimuthDeg) throw new Error("Sun preset values do not match its identity");
  } else throw new Error("Unknown sun input provenance");
}

const DISPLAY_LIGHTING = {
  day: { sun: 3.2, sunColor: 0xffefd9, sky: 0xd1e3f4, ground: 0x8b8071, hemisphere: 0.76,
    ambient: 0.055, environment: 1.1, facade: 1.4, exposure: 1.15, background: 0.65, windows: 0 },
  twilight: { sun: 0.75, sunColor: 0xffbc82, sky: 0xb1c1df, ground: 0x8b7771, hemisphere: 0.62,
    ambient: 0.07, environment: 1.1, facade: 1.6, exposure: 1.3, background: 0.9, windows: 0.55 },
  night: { sun: 0.045, sunColor: 0xa3bbde, sky: 0x8da3c5, ground: 0x5d5150, hemisphere: 0.32,
    ambient: 0.075, environment: 1.1, facade: 1.65, exposure: 1.4, background: 0.55, windows: 0.85 },
} as const;

/** Values are display radiance. Neither the preset nor tone mapping estimates measured irradiance. */
export function sampleCityLighting(input: { readonly solar: CitySolarInput;
  readonly weather: CityWeatherInput }): CityLightingSample {
  checkSolar(input.solar); checkCityWeatherInput(input.weather);
  const elevation = input.solar.elevationDeg;
  const timeOfDay = input.solar.kind === "artistic-preset" ? input.solar.timeOfDay
    : elevation > 8 ? "day" : elevation > -7 ? "twilight" : "night";
  const lighting = DISPLAY_LIGHTING[timeOfDay];
  const factors = cityWeatherLightFactors(input.weather.settings);
  const daylight = THREE.MathUtils.smoothstep(elevation, -9, 12);
  const azimuth = THREE.MathUtils.degToRad(input.solar.azimuthDeg);
  const altitude = THREE.MathUtils.degToRad(elevation);
  const sunDirection = new THREE.Vector3(Math.sin(azimuth) * Math.cos(altitude),
    Math.sin(altitude), -Math.cos(azimuth) * Math.cos(altitude));
  return {
    timeOfDay, daylight, solar: { ...input.solar },
    weather: { ...input.weather, settings: { ...input.weather.settings } },
    sunDirection, sunColor: new THREE.Color(lighting.sunColor),
    sunIntensity: input.solar.kind === "sun-position" && elevation <= 0 ? 0 : lighting.sun * factors.direct,
    skyFill: new THREE.Color(lighting.sky).lerp(new THREE.Color(0xc4cad1),
      input.weather.settings.cloudCover * 0.55),
    groundFill: new THREE.Color(lighting.ground), hemisphereIntensity: lighting.hemisphere * factors.diffuse,
    ambientColor: new THREE.Color(lighting.sky), ambientIntensity: lighting.ambient,
    environmentIntensity: lighting.environment * factors.diffuse,
    facadeEnvironmentIntensity: lighting.facade * factors.diffuse,
    exposure: lighting.exposure, backgroundIntensity: lighting.background,
    windowIntensity: lighting.windows, windowColor: new THREE.Color(0xffdbc0), wetness: factors.wetness,
  };
}

export interface CityHdrAsset {
  readonly url: string; readonly sha256: string; readonly sizeBytes: number;
  readonly hemisphere: "sky-only";
}

/** The original ambientCG panorama present in the user asset set; never fetched from a CDN. */
export const CITY_CALIBRATION_HDR: Readonly<CityHdrAsset> = Object.freeze({
  url: "/osm2world/style/textures/sky/DaySkyHDRI041B.hdr",
  sha256: "dfec7972ab2bf086c0a3e5f96e648d63fbf8509025fd7cf2cd53d307042a3f0d",
  sizeBytes: 2097216, hemisphere: "sky-only",
});

export interface CityHdrCalibration {
  readonly width: number; readonly height: number;
  readonly sourceSkyMean: number; readonly sourceGroundMean: number;
  readonly sourceMaximum: number; readonly clippedPixels: number;
  readonly normalizedSkyMean: number; readonly completedGroundMean: number;
  readonly radianceGain: number; readonly assumedGroundReflectance: number;
}

const LUMA = [0.2126, 0.7152, 0.0722] as const;
const DIFFUSE_RADIANCE_CEILING = 6;
const TARGET_SKY_RADIANCE = 0.62;
const GROUND_REFLECTANCE = 0.22;

/** Remove the baked solar hotspot before the one directional sun, then complete declared sky-only data. */
export function calibrateCityHdr(data: Float32Array, width: number, height: number): CityHdrCalibration {
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 16 || height < 8
      || height % 2 !== 0 || data.length !== width * height * 4) throw new Error("HDR dimensions are invalid");
  const half = height / 2;
  let skyMean = 0, groundMean = 0, sourceMaximum = 0, clippedPixels = 0, diffuseMean = 0;
  const irradiance = [0, 0, 0];
  for (let row = 0; row < height; row++) {
    const latitude = Math.PI / 2 - (row + 0.5) * Math.PI / height;
    const weight = Math.sin(latitude) * Math.cos(latitude) * Math.PI / height * 2 * Math.PI / width;
    for (let column = 0; column < width; column++) {
      const index = (row * width + column) * 4;
      let luminance = 0;
      for (let channel = 0; channel < 3; channel++) {
        const value = data[index + channel]!;
        if (!Number.isFinite(value) || value < 0) throw new Error("HDR contains invalid radiance");
        luminance += LUMA[channel]! * value;
      }
      sourceMaximum = Math.max(sourceMaximum, luminance);
      if (row >= half) { groundMean += luminance; continue; }
      skyMean += luminance;
      const reduction = luminance > DIFFUSE_RADIANCE_CEILING ? DIFFUSE_RADIANCE_CEILING / luminance : 1;
      if (reduction < 1) clippedPixels++;
      diffuseMean += luminance * reduction;
      for (let channel = 0; channel < 3; channel++) {
        data[index + channel]! *= reduction;
        irradiance[channel]! += data[index + channel]! * weight;
      }
    }
  }
  const hemispherePixels = width * half;
  skyMean /= hemispherePixels; groundMean /= hemispherePixels; diffuseMean /= hemispherePixels;
  if (!(skyMean > 0) || groundMean > skyMean * 0.01) {
    throw new Error("Declared sky-only HDR does not contain the expected empty lower hemisphere");
  }
  const radianceGain = TARGET_SKY_RADIANCE / diffuseMean;
  const ground = irradiance.map(value => value * GROUND_REFLECTANCE / Math.PI * radianceGain);
  for (let row = 0; row < height; row++) for (let column = 0; column < width; column++) {
    const index = (row * width + column) * 4;
    for (let channel = 0; channel < 3; channel++) {
      data[index + channel] = row < half ? data[index + channel]! * radianceGain : ground[channel]!;
    }
    data[index + 3] = 1;
  }
  return { width, height, sourceSkyMean: skyMean, sourceGroundMean: groundMean, sourceMaximum,
    clippedPixels, normalizedSkyMean: TARGET_SKY_RADIANCE,
    completedGroundMean: ground.reduce((sum, value, channel) => sum + value * LUMA[channel]!, 0),
    radianceGain, assumedGroundReflectance: GROUND_REFLECTANCE };
}

/** One verified transfer and three bounded PMREM passes per map lifetime, with no load fallback. */
export class CityCalibratedEnvironment {
  private readonly targets = new Map<CityTimeOfDay, THREE.WebGLRenderTarget>();
  private readonly abort = new AbortController();
  private pending: Promise<CityHdrCalibration> | null = null;
  private requestedAsset: CityHdrAsset | null = null;
  private calibrationValue: CityHdrCalibration | null = null;
  private disposed = false;

  constructor(private readonly renderer: THREE.WebGLRenderer) {}
  get calibration(): CityHdrCalibration | null { return this.calibrationValue; }

  load(asset: CityHdrAsset = CITY_CALIBRATION_HDR): Promise<CityHdrCalibration> {
    if (this.disposed) throw new Error("City calibrated environment is disposed");
    if (asset.hemisphere !== "sky-only" || !/^\/[a-zA-Z0-9_./-]+$/.test(asset.url)
        || asset.url.split("/").some(part => part === ".." || part === ".")
        || !/^[0-9a-f]{64}$/.test(asset.sha256) || !Number.isSafeInteger(asset.sizeBytes) || asset.sizeBytes <= 0) {
      throw new Error("Declared HDR asset is invalid");
    }
    if (this.pending !== null) {
      if (this.requestedAsset!.url !== asset.url || this.requestedAsset!.sha256 !== asset.sha256
          || this.requestedAsset!.sizeBytes !== asset.sizeBytes || this.requestedAsset!.hemisphere !== asset.hemisphere) {
        throw new Error("City HDR declaration cannot change during this environment lifetime");
      }
      return this.pending;
    }
    this.requestedAsset = { ...asset };
    this.pending = this.loadOnce(this.requestedAsset);
    return this.pending;
  }

  private async loadOnce(asset: CityHdrAsset): Promise<CityHdrCalibration> {
    const response = await fetch(asset.url, { redirect: "error", signal: this.abort.signal });
    if (!response.ok) throw new Error(`City HDR request failed (${response.status})`);
    const bytes = await readBoundedResponse(response, asset.sizeBytes, "City HDR", this.abort.signal, asset.sizeBytes);
    const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
      .map(value => value.toString(16).padStart(2, "0")).join("");
    if (digest !== asset.sha256) throw new Error("City HDR digest differs from its declaration");
    const decoded = new HDRLoader().setDataType(THREE.FloatType).parse(bytes);
    if (!(decoded.data instanceof Float32Array)) throw new Error("City HDR failed to decode as linear radiance");
    const width = decoded.width, height = decoded.height;
    if (typeof width !== "number" || typeof height !== "number") throw new Error("City HDR has no decoded dimensions");
    const calibration = calibrateCityHdr(decoded.data, width, height);
    if (this.disposed) throw new Error("City calibrated environment was disposed during load");
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const grades = {
      day: [1, 1, 1], twilight: [0.64, 0.51, 0.59], night: [0.12, 0.15, 0.23],
    } as const;
    try {
      for (const timeOfDay of ["day", "twilight", "night"] as const) {
        const data = decoded.data.slice();
        const grade = grades[timeOfDay];
        for (let index = 0; index < data.length; index += 4) for (let channel = 0; channel < 3; channel++) {
          data[index + channel]! *= grade[channel]!;
        }
        const source = new THREE.DataTexture(data, width, height, THREE.RGBAFormat, THREE.FloatType);
        source.mapping = THREE.EquirectangularReflectionMapping;
        source.colorSpace = THREE.LinearSRGBColorSpace;
        source.needsUpdate = true;
        try { this.targets.set(timeOfDay, pmrem.fromEquirectangular(source)); }
        finally { source.dispose(); }
      }
      this.calibrationValue = calibration;
      return calibration;
    } catch (error) {
      for (const target of this.targets.values()) target.dispose();
      this.targets.clear();
      throw error;
    } finally { pmrem.dispose(); }
  }

  textureFor(timeOfDay: CityTimeOfDay): THREE.Texture {
    if (this.disposed) throw new Error("City calibrated environment is disposed");
    const target = this.targets.get(timeOfDay);
    if (target === undefined) throw new Error(`City HDR is not ready for ${timeOfDay}`);
    return target.texture;
  }

  dispose(): void {
    if (this.disposed) return;
    this.abort.abort();
    for (const target of this.targets.values()) target.dispose();
    this.targets.clear(); this.calibrationValue = null; this.disposed = true;
  }
}

export function applyCityLightingCalibration(target: { readonly scene: THREE.Scene;
  readonly renderer: THREE.WebGLRenderer; readonly sun: THREE.DirectionalLight;
  readonly hemisphere: THREE.HemisphereLight; readonly ambient: THREE.AmbientLight },
sample: CityLightingSample, environment: THREE.Texture): void {
  target.scene.environment = environment;
  target.scene.environmentIntensity = sample.environmentIntensity;
  target.scene.backgroundIntensity = sample.backgroundIntensity;
  target.renderer.toneMapping = THREE.ACESFilmicToneMapping;
  target.renderer.toneMappingExposure = sample.exposure;
  target.sun.color.copy(sample.sunColor); target.sun.intensity = sample.sunIntensity;
  target.hemisphere.color.copy(sample.skyFill); target.hemisphere.groundColor.copy(sample.groundFill);
  target.hemisphere.intensity = sample.hemisphereIntensity;
  target.ambient.color.copy(sample.ambientColor); target.ambient.intensity = sample.ambientIntensity;
}

/** The existing sky draw shares the sun/weather sample; dusk and cloud grading remain artistic display choices. */
export function applyCitySkyCalibration(sky: Sky, sample: CityLightingSample): void {
  const material = sky.material, uniforms = material.uniforms;
  if (uniforms.cityCalibratedCloudMix === undefined) {
    const anchor = "#include <tonemapping_fragment>";
    if (material.fragmentShader.split(anchor).length !== 2) throw new Error("City sky tone mapping shader contract changed");
    material.fragmentShader = `uniform float cityCalibratedCloudMix;
uniform float cityCalibratedTwilightMix;
uniform vec3 cityCalibratedZenith;
uniform vec3 cityCalibratedHorizon;
uniform vec3 cityCalibratedOvercast;\n${material.fragmentShader}`.replace(anchor, `
      vec3 cityGrade = mix(cityCalibratedHorizon, cityCalibratedZenith,
        smoothstep(0.0, 0.65, max(0.0, direction.y)));
      gl_FragColor.rgb = mix(gl_FragColor.rgb, cityGrade, cityCalibratedTwilightMix);
      gl_FragColor.rgb = mix(gl_FragColor.rgb, cityCalibratedOvercast, cityCalibratedCloudMix);
      ${anchor}`);
    uniforms.cityCalibratedCloudMix = { value: 0 };
    uniforms.cityCalibratedTwilightMix = { value: 0 };
    uniforms.cityCalibratedZenith = { value: new THREE.Color() };
    uniforms.cityCalibratedHorizon = { value: new THREE.Color() };
    uniforms.cityCalibratedOvercast = { value: new THREE.Color() };
    material.needsUpdate = true;
  }
  const daylight = sample.daylight;
  const twilight = THREE.MathUtils.smoothstep(sample.solar.elevationDeg, -9, 2);
  uniforms.cityCalibratedZenith!.value.setRGB(0.004, 0.008, 0.018)
    .lerp(new THREE.Color().setRGB(0.03, 0.065, 0.16), twilight);
  uniforms.cityCalibratedHorizon!.value.setRGB(0.015, 0.021, 0.035)
    .lerp(new THREE.Color().setRGB(0.32, 0.15, 0.085), twilight);
  uniforms.cityCalibratedOvercast!.value.setRGB(0.008, 0.013, 0.022)
    .lerp(new THREE.Color().setRGB(0.22, 0.235, 0.255), daylight);
  uniforms.cityCalibratedCloudMix!.value = THREE.MathUtils.smoothstep(sample.weather.settings.cloudCover, 0.15, 0.9);
  uniforms.cityCalibratedTwilightMix!.value = 1 - THREE.MathUtils.smoothstep(sample.solar.elevationDeg, 0, 12);
  setCityDaySkyRadianceGain(sky, 0.3);
  setCityDaySkySunDirection(sky, sample.sunDirection);
  setCityDaySkyWeather(sky, sample.weather.settings);
  sky.visible = true;
}

/** Spend the existing single shadow map on the view; all distances derive from the supplied map envelope. */
export function focusCityCalibratedSun(sun: THREE.DirectionalLight, sample: CityLightingSample,
  envelope: THREE.Box3, focus: THREE.Vector3, camera: THREE.Camera): void {
  if (envelope.isEmpty() || ![...envelope.min.toArray(), ...envelope.max.toArray(), ...focus.toArray()]
    .every(Number.isFinite)) throw new Error("Lighting needs a finite nonempty map envelope and view focus");
  const size = envelope.getSize(new THREE.Vector3());
  const span = Math.max(size.x, size.z);
  if (span <= 0) throw new Error("Lighting map envelope has no horizontal span");
  const radius = THREE.MathUtils.clamp(camera.position.distanceTo(focus) * 0.65,
    Math.min(35, span * 0.6), span * 0.6);
  const distance = Math.max(span * 1.5, size.y * 3, radius * 3);
  // Below-horizon solar positions provide a cool artistic night fill; never claim moon ephemeris.
  const shadowDirection = sample.sunDirection.clone();
  if (sample.solar.kind === "artistic-preset" && sample.timeOfDay === "night") {
    shadowDirection.y = Math.max(0.2, shadowDirection.y); shadowDirection.normalize();
  }
  sun.position.copy(focus).addScaledVector(shadowDirection, distance);
  sun.target.position.copy(focus);
  Object.assign(sun.shadow.camera, { left: -radius, right: radius, top: radius, bottom: -radius,
    near: 1, far: distance + Math.max(span, size.y * 3) });
  sun.shadow.camera.updateProjectionMatrix();
  sun.shadow.bias = -0.00012; sun.shadow.normalBias = 0.035;
}
