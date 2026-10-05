import * as THREE from "three";
import { checkCityWeatherInput, cityWeatherLightFactors, type CityWeatherInput } from "./city-weather-calibration";
import type { CityLightingSample } from "./city-lighting-calibration";

/** Visual controls only. Formal Weather Provider samples remain authoritative elsewhere. */
export interface CityWeatherSettings {
  cloudCover: number;
  precipitation: "none" | "drizzle" | "rain" | "snow" | "hail";
  precipitationRateMmPerH: number;
  visibilityM: number;
  windMps: number;
  /** Direction of travel, clockwise from north. City coordinates use +X east and -Z north. */
  windDirectionDeg: number;
}

export const CITY_WEATHER_CLEAR: Readonly<CityWeatherSettings> = Object.freeze({
  cloudCover: 0,
  precipitation: "none",
  precipitationRateMmPerH: 0,
  visibilityM: 10000,
  windMps: 0,
  windDirectionDeg: 0,
});

export const CITY_WEATHER_PRESETS: Readonly<Record<"clear" | "cloudy" | "rain" | "fog" | "snow",
  Readonly<CityWeatherSettings>>> = Object.freeze({
  clear: CITY_WEATHER_CLEAR,
  cloudy: Object.freeze({ cloudCover: 0.72, precipitation: "none", precipitationRateMmPerH: 0,
    visibilityM: 7500, windMps: 4, windDirectionDeg: 70 }),
  rain: Object.freeze({ cloudCover: 0.88, precipitation: "rain", precipitationRateMmPerH: 6,
    visibilityM: 1800, windMps: 7, windDirectionDeg: 70 }),
  fog: Object.freeze({ cloudCover: 0.54, precipitation: "none", precipitationRateMmPerH: 0,
    visibilityM: 350, windMps: 1, windDirectionDeg: 70 }),
  snow: Object.freeze({ cloudCover: 0.8, precipitation: "snow", precipitationRateMmPerH: 2,
    visibilityM: 1400, windMps: 3, windDirectionDeg: 70 }),
});

const CLOUD_CLUSTERS = 49;
const PUFFS_PER_CLUSTER = 5;
const PARTICLE_COUNT = 1536;
const CLOUD_RADIUS_M = 1500;

/** Reproducible placements without a texture download or a per-frame object walk. */
function randomStream(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 4294967296;
  };
}

function cloudGeometry(): THREE.InstancedBufferGeometry {
  const random = randomStream(0xaced2026);
  const geometry = new THREE.InstancedBufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute([
    -0.5, -0.5, 0, 0.5, -0.5, 0, -0.5, 0.5, 0, 0.5, 0.5, 0,
  ], 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 1, 1], 2));
  geometry.setIndex([0, 1, 2, 2, 1, 3]);
  const centers = new Float32Array(CLOUD_CLUSTERS * PUFFS_PER_CLUSTER * 3);
  const sizes = new Float32Array(CLOUD_CLUSTERS * PUFFS_PER_CLUSTER * 2);
  const coverage = new Float32Array(CLOUD_CLUSTERS * PUFFS_PER_CLUSTER);
  const shades = new Float32Array(CLOUD_CLUSTERS * PUFFS_PER_CLUSTER);
  for (let cluster = 0; cluster < CLOUD_CLUSTERS; cluster++) {
    const x = ((cluster % 7 + 0.5) / 7 * 2 - 1) * CLOUD_RADIUS_M
      + (random() - 0.5) * 200;
    const z = ((Math.floor(cluster / 7) + 0.5) / 7 * 2 - 1) * CLOUD_RADIUS_M
      + (random() - 0.5) * 200;
    const high = cluster % 3 === 0;
    const y = (high ? 780 : 400) + random() * (high ? 230 : 180);
    const threshold = random();
    for (let puff = 0; puff < PUFFS_PER_CLUSTER; puff++) {
      const index = cluster * PUFFS_PER_CLUSTER + puff;
      centers.set([x + (random() - 0.5) * 180,
        y + (random() - 0.5) * 80,
        z + (random() - 0.5) * 120], index * 3);
      sizes.set([(high ? 300 : 350) + random() * 180,
        (high ? 120 : 180) + random() * 100], index * 2);
      coverage[index] = threshold;
      shades[index] = 0.78 + random() * 0.22;
    }
  }
  geometry.setAttribute("aCenter", new THREE.InstancedBufferAttribute(centers, 3));
  geometry.setAttribute("aSize", new THREE.InstancedBufferAttribute(sizes, 2));
  geometry.setAttribute("aCoverage", new THREE.InstancedBufferAttribute(coverage, 1));
  geometry.setAttribute("aShade", new THREE.InstancedBufferAttribute(shades, 1));
  geometry.instanceCount = CLOUD_CLUSTERS * PUFFS_PER_CLUSTER;
  return geometry;
}

function particleGeometry(): THREE.BufferGeometry {
  const random = randomStream(0x5eed2026);
  const positions = new Float32Array(PARTICLE_COUNT * 3);
  const variations = new Float32Array(PARTICLE_COUNT);
  for (let index = 0; index < PARTICLE_COUNT; index++) {
    positions.set([random(), random(), random()], index * 3);
    variations[index] = random();
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("aVariation", new THREE.BufferAttribute(variations, 1));
  return geometry;
}

const CLOUD_VERTEX = /* glsl */ `
  attribute vec3 aCenter;
  attribute vec2 aSize;
  attribute float aCoverage;
  attribute float aShade;
  uniform vec3 uCameraPosition;
  uniform vec2 uWind;
  uniform float uTime;
  uniform float uCloudCover;
  uniform float uVisibility;
  uniform float uCloudRadius;
  uniform float uCloudBase;
  uniform float uCloudDepth;
  varying vec2 vUv;
  varying float vAlpha;
  varying float vShade;
  void main() {
    vec2 drift = uWind * uTime * 0.32;
    float domainScale = uCloudRadius / ${CLOUD_RADIUS_M.toFixed(1)};
    vec2 local = mod(aCenter.xz * domainScale + drift + uCloudRadius, uCloudRadius * 2.0) - uCloudRadius;
    float heightPhase = (aCenter.y - 350.0) / 710.0;
    vec3 center = vec3(uCameraPosition.x + local.x, uCloudBase + heightPhase * uCloudDepth,
      uCameraPosition.z + local.y);
    vec4 viewCenter = viewMatrix * vec4(center, 1.0);
    viewCenter.xy += position.xy * aSize * domainScale;
    gl_Position = projectionMatrix * viewCenter;
    vUv = uv;
    float edgeFade = 1.0 - smoothstep(0.72, 0.99,
      max(abs(local.x), abs(local.y)) / uCloudRadius);
    float fogFade = 1.0 - smoothstep(uVisibility * 0.6, uVisibility, -viewCenter.z);
    vAlpha = smoothstep(aCoverage - 0.05, aCoverage + 0.05, uCloudCover)
      * edgeFade * fogFade;
    vShade = aShade;
  }
`;

const CLOUD_FRAGMENT = /* glsl */ `
  uniform vec3 uCloudBottom;
  uniform vec3 uCloudTop;
  varying vec2 vUv;
  varying float vAlpha;
  varying float vShade;
  void main() {
    vec2 p = (vUv - 0.5) * 2.0;
    float scallop = 0.045 * sin(p.x * 9.0 + vShade * 23.0)
      + 0.025 * sin(p.x * 19.0 - p.y * 4.0);
    float radius = length(vec2(p.x * 0.88, p.y * 1.28));
    float silhouette = 1.0 - smoothstep(0.63 + scallop, 1.00 + scallop, radius);
    float body = exp(-2.2 * dot(p, p));
    float alpha = vAlpha * silhouette * (0.17 + 0.34 * body);
    if (alpha < 0.005) discard;
    float upperLight = smoothstep(-0.72, 0.82, p.y);
    vec3 color = mix(uCloudBottom, uCloudTop, upperLight) * vShade;
    gl_FragColor = vec4(color, alpha);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

const PARTICLE_VERTEX = /* glsl */ `
  attribute float aVariation;
  uniform vec3 uCameraPosition;
  uniform vec2 uWind;
  uniform float uTime;
  uniform float uFallSpeed;
  uniform float uDensity;
  uniform float uPointSize;
  uniform float uGroundHeight;
  varying float vAlpha;
  varying float vVariation;
  void main() {
    vec3 phase = position;
    vec3 p = vec3(
      fract(phase.x + uTime * uWind.x / 120.0 + aVariation * 0.13),
      fract(phase.y - uTime * uFallSpeed * (0.75 + aVariation * 0.5) / 78.0),
      fract(phase.z + uTime * uWind.y / 120.0 + aVariation * 0.19)
    );
    vec3 world = uCameraPosition + (p - 0.5) * vec3(120.0, 78.0, 120.0);
    vec4 eye = viewMatrix * vec4(world, 1.0);
    gl_Position = projectionMatrix * eye;
    gl_PointSize = uPointSize * clamp(25.0 / max(eye.z * -1.0, 1.0), 0.28, 1.35);
    vAlpha = step(aVariation, uDensity) * smoothstep(0.0, 0.08, p.y)
      * (1.0 - smoothstep(0.88, 1.0, p.y)) * step(uGroundHeight, world.y);
    vVariation = aVariation;
  }
`;

const PARTICLE_FRAGMENT = /* glsl */ `
  uniform vec3 uParticleColor;
  uniform float uSnow;
  varying float vAlpha;
  varying float vVariation;
  void main() {
    vec2 p = gl_PointCoord - 0.5;
    float streak = (1.0 - smoothstep(0.04, 0.16, abs(p.x + p.y * 0.13)))
      * (1.0 - smoothstep(0.38, 0.5, abs(p.y)));
    float snow = 1.0 - smoothstep(0.27, 0.49, length(p));
    float alpha = vAlpha * mix(streak * 0.42, snow * (0.65 + 0.25 * vVariation), uSnow);
    if (alpha < 0.01) discard;
    gl_FragColor = vec4(uParticleColor, alpha);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

export function checkCityWeatherSettings(settings: Readonly<CityWeatherSettings>): void {
  if (settings === null || typeof settings !== "object") throw new Error("City weather settings are required");
  if (!Number.isFinite(settings.cloudCover) || settings.cloudCover < 0 || settings.cloudCover > 1) {
    throw new RangeError("cloudCover must be between 0 and 1");
  }
  if (!Number.isFinite(settings.precipitationRateMmPerH) || settings.precipitationRateMmPerH < 0) {
    throw new RangeError("precipitationRateMmPerH must be nonnegative");
  }
  if (!Number.isFinite(settings.visibilityM) || settings.visibilityM <= 0) {
    throw new RangeError("visibilityM must be positive");
  }
  if (!Number.isFinite(settings.windMps) || settings.windMps < 0
      || !Number.isFinite(settings.windDirectionDeg)) {
    throw new RangeError("windMps and windDirectionDeg must be finite; windMps must be nonnegative");
  }
  if (!["none", "drizzle", "rain", "snow", "hail"].includes(settings.precipitation)) {
    throw new RangeError("Unknown precipitation kind");
  }
  if (settings.precipitation === "none" && settings.precipitationRateMmPerH !== 0) {
    throw new RangeError("No precipitation requires a zero precipitation rate");
  }
}

/** Camera-local weather visuals. update(t) samples time t directly, including reverse seeking. */
export class CityWeather {
  private readonly scene: THREE.Scene;
  private readonly camera: THREE.Camera;
  private readonly clouds: THREE.Mesh<THREE.InstancedBufferGeometry, THREE.ShaderMaterial>;
  private readonly particles: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>;
  private readonly cloudMaterial: THREE.ShaderMaterial;
  private readonly particleMaterial: THREE.ShaderMaterial;
  private readonly fog = new THREE.Fog(0x829ba8, 1100, 3700);
  private readonly cameraPosition = new THREE.Vector3();
  private readonly wind = new THREE.Vector2();
  private settings: CityWeatherSettings = { ...CITY_WEATHER_CLEAR };
  private daylight = 1;
  private horizontalSpanM = 1000;
  private input: CityWeatherInput = { kind: "visual-settings", settings: { ...CITY_WEATHER_CLEAR } };
  private disposed = false;

  constructor(scene: THREE.Scene, camera: THREE.Camera) {
    this.scene = scene;
    this.camera = camera;
    this.cloudMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uCameraPosition: { value: this.cameraPosition },
        uWind: { value: this.wind },
        uTime: { value: 0 },
        uCloudCover: { value: 0 },
        uVisibility: { value: CITY_WEATHER_CLEAR.visibilityM },
        uCloudRadius: { value: CLOUD_RADIUS_M },
        uCloudBase: { value: 400 },
        uCloudDepth: { value: 610 },
        uCloudBottom: { value: new THREE.Color(0x94a3ac) },
        uCloudTop: { value: new THREE.Color(0xf8f9f8) },
      },
      vertexShader: CLOUD_VERTEX,
      fragmentShader: CLOUD_FRAGMENT,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    this.particleMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uCameraPosition: { value: this.cameraPosition },
        uWind: { value: this.wind },
        uTime: { value: 0 },
        uFallSpeed: { value: 0 },
        uDensity: { value: 0 },
        uPointSize: { value: 0 },
        uSnow: { value: 0 },
        uParticleColor: { value: new THREE.Color(0xc1d6e5) },
        uGroundHeight: { value: 0 },
      },
      vertexShader: PARTICLE_VERTEX,
      fragmentShader: PARTICLE_FRAGMENT,
      transparent: true,
      depthWrite: false,
    });
    this.clouds = new THREE.Mesh(cloudGeometry(), this.cloudMaterial);
    this.clouds.name = "city-weather-clouds";
    this.clouds.frustumCulled = false;
    this.clouds.renderOrder = 80;
    this.particles = new THREE.Points(particleGeometry(), this.particleMaterial);
    this.particles.name = "city-weather-precipitation";
    this.particles.frustumCulled = false;
    this.particles.renderOrder = 90;
    scene.add(this.clouds, this.particles);
    this.setWeather(CITY_WEATHER_CLEAR);
  }

  setWeather(settings: CityWeatherSettings): void {
    this.assertAlive();
    checkCityWeatherSettings(settings);
    this.input = { kind: "visual-settings", settings: { ...settings } };
    this.settings = { ...settings };
    const heading = THREE.MathUtils.degToRad(settings.windDirectionDeg);
    this.wind.set(Math.sin(heading) * settings.windMps,
      -Math.cos(heading) * settings.windMps);
    this.cloudMaterial.uniforms.uCloudCover!.value = settings.cloudCover;
    this.cloudMaterial.uniforms.uVisibility!.value = settings.visibilityM;
    this.clouds.visible = settings.cloudCover > 0;
    const wet = settings.precipitation !== "none" && settings.precipitationRateMmPerH > 0;
    this.particles.visible = wet;
    if (wet) {
      const snow = settings.precipitation === "snow" || settings.precipitation === "hail";
      this.particleMaterial.uniforms.uSnow!.value = snow ? 1 : 0;
      this.particleMaterial.uniforms.uFallSpeed!.value = settings.precipitation === "drizzle" ? 9
        : settings.precipitation === "rain" ? 22 : settings.precipitation === "hail" ? 15 : 2.8;
      this.particleMaterial.uniforms.uPointSize!.value = settings.precipitation === "drizzle" ? 8
        : settings.precipitation === "rain" ? 12 : settings.precipitation === "hail" ? 4 : 7;
      this.particleMaterial.uniforms.uDensity!.value = Math.min(1,
        0.14 + 0.86 * (1 - Math.exp(-settings.precipitationRateMmPerH / 4)));
    }
    this.applyAtmosphere();
  }

  /** Observed inputs keep their identity; an artistic preset cannot become a formal weather sample. */
  setWeatherInput(input: CityWeatherInput): void {
    checkCityWeatherInput(input);
    this.setWeather(input.settings);
    this.input = { ...input, settings: { ...input.settings } };
    this.applyAtmosphere();
  }

  get provenance(): CityWeatherInput { return { ...this.input, settings: { ...this.input.settings } }; }

  /** Cloud-free dry views do not need a render loop; precipitation and drifting clouds do. */
  get requiresAnimation(): boolean {
    return (this.settings.precipitation !== "none" && this.settings.precipitationRateMmPerH > 0)
      || (this.settings.cloudCover > 0 && this.settings.windMps > 0);
  }

  /** Map dimensions determine cloud altitude, cloud coverage area and optional presentation haze. */
  configureEnvelope(envelope: THREE.Box3): void {
    this.assertAlive();
    if (envelope.isEmpty() || ![...envelope.min.toArray(), ...envelope.max.toArray()].every(Number.isFinite)) {
      throw new Error("City weather needs a finite, nonempty map envelope");
    }
    const size = envelope.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.z);
    if (span <= 0) throw new Error("City weather envelope needs horizontal extent");
    this.horizontalSpanM = span;
    this.cloudMaterial.uniforms.uCloudRadius!.value = Math.max(800, span * 1.4);
    this.cloudMaterial.uniforms.uCloudBase!.value = envelope.max.y + Math.max(120, span * 0.2);
    this.cloudMaterial.uniforms.uCloudDepth!.value = Math.max(80, span * 0.45);
    this.particleMaterial.uniforms.uGroundHeight!.value = envelope.min.y;
    this.applyAtmosphere();
  }

  setLighting(sample: CityLightingSample): void {
    this.assertAlive();
    if (!Number.isFinite(sample.daylight) || sample.daylight < 0 || sample.daylight > 1) {
      throw new Error("Weather daylight must be between zero and one");
    }
    this.daylight = sample.daylight;
    const radiance = THREE.MathUtils.lerp(0.08, 1, sample.daylight);
    this.cloudMaterial.uniforms.uCloudBottom!.value.copy(sample.skyFill).multiplyScalar(0.55 * radiance);
    this.cloudMaterial.uniforms.uCloudTop!.value.copy(sample.skyFill).multiplyScalar(1.25 * radiance);
    this.particleMaterial.uniforms.uParticleColor!.value.copy(sample.skyFill);
    this.applyAtmosphere();
  }

  setMood(mood: "day" | "dusk"): void {
    this.assertAlive();
    if (mood !== "day" && mood !== "dusk") throw new Error("Unknown city weather mood");
    this.daylight = mood === "day" ? 1 : 0;
    this.cloudMaterial.uniforms.uCloudBottom!.value.setHex(mood === "day" ? 0x94a3ac : 0x66768e);
    this.cloudMaterial.uniforms.uCloudTop!.value.setHex(mood === "day" ? 0xf8f9f8 : 0xb8bdca);
    this.particleMaterial.uniforms.uParticleColor!.value.setHex(mood === "day" ? 0xc1d6e5 : 0xadc5df);
    this.applyAtmosphere();
  }

  /** One camera position and two scalar uniforms change per frame; all particles stay on the GPU. */
  update(timeSeconds: number): void {
    this.assertAlive();
    if (!Number.isFinite(timeSeconds)) throw new RangeError("Weather time must be finite");
    this.camera.getWorldPosition(this.cameraPosition);
    this.cloudMaterial.uniforms.uTime!.value = timeSeconds;
    this.particleMaterial.uniforms.uTime!.value = timeSeconds;
  }

  /** Apply this factor to an existing directional light when cloud shading is desired. */
  get sunlightFactor(): number { return cityWeatherLightFactors(this.settings).direct; }

  dispose(): void {
    if (this.disposed) return;
    this.scene.remove(this.clouds, this.particles);
    if (this.scene.fog === this.fog) this.scene.fog = null;
    this.clouds.geometry.dispose();
    this.particles.geometry.dispose();
    this.cloudMaterial.dispose();
    this.particleMaterial.dispose();
    this.disposed = true;
  }

  private applyAtmosphere(): void {
    // Presentation haze adds depth within this kilometre-scale district. This is
    // an art-direction limit, not a simulated meteorological visibility sample.
    const near = THREE.MathUtils.lerp(0.4, 0.38, this.daylight) * this.horizontalSpanM;
    const far = THREE.MathUtils.lerp(1.8, 3.2, this.daylight) * this.horizontalSpanM;
    // Measured visibility bypasses the artistic haze limit; preserve exactly the supplied range.
    this.fog.near = this.input.kind === "observed-weather" ? this.settings.visibilityM * 0.08
      : Math.min(near, this.settings.visibilityM * 0.15);
    this.fog.far = this.input.kind === "observed-weather" ? this.settings.visibilityM
      : Math.min(far, this.settings.visibilityM);
    this.fog.color.setHex(0x172337).lerp(new THREE.Color(0xb7cbd9), this.daylight);
    this.fog.color.lerp(new THREE.Color(0xaebcc4), this.settings.cloudCover * this.daylight * 0.6);
    this.scene.fog = this.fog;
  }

  private assertAlive(): void {
    if (this.disposed) throw new Error("CityWeather has been disposed");
  }
}
