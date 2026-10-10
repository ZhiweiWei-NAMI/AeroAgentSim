import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { CITY_WEATHER_CLEAR, CITY_WEATHER_PRESETS, CityWeather } from "./city-weather";
import { cityVisualWeather } from "./city-weather-calibration";
import { cityVisualSolar, sampleCityLighting } from "./city-lighting-calibration";

function makeWeather(): { scene: THREE.Scene; camera: THREE.PerspectiveCamera; weather: CityWeather;
  clouds: THREE.Mesh<THREE.InstancedBufferGeometry, THREE.ShaderMaterial>;
  particles: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial> } {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera();
  const weather = new CityWeather(scene, camera);
  const clouds = scene.getObjectByName("city-weather-clouds");
  const particles = scene.getObjectByName("city-weather-precipitation");
  if (!(clouds instanceof THREE.Mesh) || !(particles instanceof THREE.Points)) {
    throw new Error("Weather draw objects missing");
  }
  return { scene, camera, weather, clouds, particles };
}

describe("city weather visuals", () => {
  it("requests animation only for falling precipitation or wind-driven clouds", () => {
    const { weather } = makeWeather();
    expect(weather.requiresAnimation).toBe(false);
    weather.setWeather(CITY_WEATHER_PRESETS.rain);
    expect(weather.requiresAnimation).toBe(true);
    weather.setWeather({ ...CITY_WEATHER_PRESETS.rain, windMps: 0 });
    expect(weather.requiresAnimation).toBe(true);
    weather.setWeather({ ...CITY_WEATHER_PRESETS.cloudy, windMps: 0 });
    expect(weather.requiresAnimation).toBe(false);
    weather.setWeather(CITY_WEATHER_PRESETS.cloudy);
    expect(weather.requiresAnimation).toBe(true);
    weather.setWeather({ ...CITY_WEATHER_CLEAR, windMps: 10 });
    expect(weather.requiresAnimation).toBe(false);
    weather.dispose();
  });
  it("uses bounded shared geometry and adds no lights", () => {
    const { scene, weather, clouds, particles } = makeWeather();
    expect(scene.children).toHaveLength(2);
    expect(scene.children.some(child => child instanceof THREE.Light)).toBe(false);
    expect(clouds.geometry.instanceCount).toBe(245);
    expect(particles.geometry.getAttribute("position").count).toBe(1536);
    expect(clouds.material.depthWrite).toBe(false);
    expect(particles.material.depthWrite).toBe(false);
    expect(clouds.visible).toBe(false);
    expect(particles.visible).toBe(false);
    weather.dispose();
  });

  it("samples any target second from fixed geometry and the current camera", () => {
    const { camera, weather, clouds, particles } = makeWeather();
    weather.setWeather(CITY_WEATHER_PRESETS.rain);
    const cloudCenters = clouds.geometry.getAttribute("aCenter");
    const particlePositions = particles.geometry.getAttribute("position");
    if (!(cloudCenters instanceof THREE.BufferAttribute)
        || !(particlePositions instanceof THREE.BufferAttribute)) {
      throw new Error("Weather attributes must be fixed buffers");
    }
    const cloudVersion = cloudCenters.version;
    const particleVersion = particlePositions.version;
    const cloudSnapshot = [...(cloudCenters.array as Float32Array)];
    const particleSnapshot = [...(particlePositions.array as Float32Array)];
    camera.position.set(12, 7, -45);
    weather.update(42.5);
    expect(clouds.material.uniforms.uTime?.value).toBe(42.5);
    expect(particles.material.uniforms.uTime?.value).toBe(42.5);
    expect(clouds.material.uniforms.uCameraPosition?.value.toArray()).toEqual([12, 7, -45]);
    weather.update(2);
    weather.update(42.5);
    expect(clouds.material.uniforms.uTime?.value).toBe(42.5);
    expect(particles.material.uniforms.uTime?.value).toBe(42.5);
    expect(cloudCenters.version).toBe(cloudVersion);
    expect(particlePositions.version).toBe(particleVersion);
    expect([...(cloudCenters.array as Float32Array)]).toEqual(cloudSnapshot);
    expect([...(particlePositions.array as Float32Array)]).toEqual(particleSnapshot);
    weather.dispose();
  });

  it("maps visibility, clouds, wind and precipitation to visual state", () => {
    const { scene, weather, clouds, particles } = makeWeather();
    weather.setWeather({ ...CITY_WEATHER_PRESETS.fog, windMps: 10, windDirectionDeg: 90 });
    expect(clouds.visible).toBe(true);
    expect(particles.visible).toBe(false);
    expect(weather.sunlightFactor).toBeCloseTo(Math.exp(-2.8 * CITY_WEATHER_PRESETS.fog.cloudCover));
    expect(clouds.material.uniforms.uWind?.value.x).toBeCloseTo(10);
    expect(clouds.material.uniforms.uWind?.value.y).toBeCloseTo(0);
    expect(scene.fog).toBeInstanceOf(THREE.Fog);
    expect((scene.fog as THREE.Fog).near).toBeCloseTo(52.5);
    expect((scene.fog as THREE.Fog).far).toBe(350);
    weather.setMood("dusk");
    expect((scene.fog as THREE.Fog).color.getHex()).toBe(0x172337);
    weather.setWeather(CITY_WEATHER_PRESETS.snow);
    expect(particles.visible).toBe(true);
    expect(particles.material.uniforms.uSnow?.value).toBe(1);
    weather.setWeather(CITY_WEATHER_PRESETS.rain);
    expect(particles.material.uniforms.uSnow?.value).toBe(0);
    weather.setWeather(CITY_WEATHER_CLEAR);
    expect(clouds.visible).toBe(false);
    expect(particles.visible).toBe(false);
    weather.dispose();
  });

  it("keeps clear daylight legible across the district and preserves visibility presets", () => {
    const { scene, weather } = makeWeather();
    const fog = scene.fog;
    expect(fog).toBeInstanceOf(THREE.Fog);
    expect((fog as THREE.Fog).near).toBe(380);
    expect((fog as THREE.Fog).far).toBe(3200);

    weather.setWeather(CITY_WEATHER_PRESETS.rain);
    expect((scene.fog as THREE.Fog).near).toBe(270);
    expect((scene.fog as THREE.Fog).far).toBe(1800);
    weather.setWeather(CITY_WEATHER_CLEAR);
    weather.setMood("dusk");
    expect((scene.fog as THREE.Fog).near).toBe(400);
    expect((scene.fog as THREE.Fog).far).toBe(1800);
    weather.dispose();
  });

  it("rejects invalid settings and releases its scene resources", () => {
    const { scene, weather, clouds, particles } = makeWeather();
    const cloudDispose = vi.spyOn(clouds.geometry, "dispose");
    const particleDispose = vi.spyOn(particles.geometry, "dispose");
    const cloudMaterialDispose = vi.spyOn(clouds.material, "dispose");
    const particleMaterialDispose = vi.spyOn(particles.material, "dispose");
    expect(() => weather.setWeather({ ...CITY_WEATHER_CLEAR, cloudCover: 1.2 })).toThrow(RangeError);
    expect(() => weather.setWeather({ ...CITY_WEATHER_CLEAR, visibilityM: 0 })).toThrow(RangeError);
    expect(() => weather.setWeather({ ...CITY_WEATHER_CLEAR, windMps: Number.NaN })).toThrow(RangeError);
    expect(() => weather.update(Number.POSITIVE_INFINITY)).toThrow(RangeError);
    weather.dispose();
    expect(scene.children).toHaveLength(0);
    expect(scene.fog).toBeNull();
    expect(cloudDispose).toHaveBeenCalledOnce();
    expect(particleDispose).toHaveBeenCalledOnce();
    expect(cloudMaterialDispose).toHaveBeenCalledOnce();
    expect(particleMaterialDispose).toHaveBeenCalledOnce();
    expect(() => weather.update(0)).toThrow(/disposed/);
  });

  it("fits clouds and ground clipping to each map while keeping measured visibility intact", () => {
    const { scene, weather, clouds, particles } = makeWeather();
    const envelope = new THREE.Box3(new THREE.Vector3(-2000, -5, -1000), new THREE.Vector3(2000, 300, 1000));
    weather.configureEnvelope(envelope);
    expect(clouds.material.uniforms.uCloudRadius?.value).toBe(5600);
    expect(clouds.material.uniforms.uCloudBase?.value).toBe(1100);
    expect(particles.material.uniforms.uGroundHeight?.value).toBe(-5);
    const observed = { kind: "observed-weather" as const, providerId: "source-weather", sampleId: "sample-12",
      sampleTimeSeconds: 12, settings: { ...CITY_WEATHER_PRESETS.cloudy, visibilityM: 9000 } };
    weather.setWeatherInput(observed);
    expect((scene.fog as THREE.Fog).far).toBe(9000);
    expect(weather.provenance).toEqual(observed);
    weather.setWeatherInput(cityVisualWeather("clear"));
    expect((scene.fog as THREE.Fog).far).toBe(10000);
    expect(weather.provenance.kind).toBe("artistic-preset");
    weather.setLighting(sampleCityLighting({ solar: cityVisualSolar("night"), weather: cityVisualWeather("clear") }));
    expect((scene.fog as THREE.Fog).near).toBe(1500);
    expect((scene.fog as THREE.Fog).far).toBe(7200);
    expect(() => weather.configureEnvelope(new THREE.Box3())).toThrow(/envelope/);
    weather.dispose();
  });
});
