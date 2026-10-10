// @vitest-environment node
import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CityCalibratedEnvironment, cityVisualSolar, sampleCityLighting } from "./city-lighting-calibration";
import { cityVisualWeather } from "./city-weather-calibration";
import { createCityDaySky, disposeCityDaySky } from "./city-day-sky";
import { CityWeatherSkyEnvironment, CITY_WEATHER_SKY_PMREM_SIZE } from "./city-weather-sky-environment";

function renderer(): THREE.WebGLRenderer {
  return { getRenderTarget: () => null, getActiveCubeFace: () => 0, getActiveMipmapLevel: () => 0,
    setRenderTarget: vi.fn(), xr: { enabled: true }, shadowMap: { autoUpdate: true },
    autoClear: true, toneMapping: THREE.ACESFilmicToneMapping } as unknown as THREE.WebGLRenderer;
}

function readyHdr(render: THREE.WebGLRenderer): { hdr: CityCalibratedEnvironment; target: THREE.WebGLRenderTarget } {
  const hdr = new CityCalibratedEnvironment(render), target = new THREE.WebGLRenderTarget(8, 8);
  (Reflect.get(hdr, "targets") as Map<string, THREE.WebGLRenderTarget>).set("day", target);
  return { hdr, target };
}

afterEach(() => vi.restoreAllMocks());

describe("bounded weather sky environment", () => {
  it("uses the declared visible Sky for clear weather without requesting an unused HDR", () => {
    const render = renderer(), sky = createCityDaySky();
    const owner = new CityWeatherSkyEnvironment(render, { kind: "visible-sky" });
    const filtered = new THREE.WebGLRenderTarget(384, 512);
    const capture = vi.spyOn(THREE.PMREMGenerator.prototype, "fromScene").mockReturnValue(filtered);
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("clear") });
    expect(owner.textureFor(sky, sample)).toBe(filtered.texture);
    expect(owner.textureFor(sky, sample)).toBe(filtered.texture);
    expect(capture).toHaveBeenCalledOnce();
    expect(owner.state.policy).toBe("visible-sky"); expect(owner.state.source).toBe("procedural-sky");
    owner.dispose(); disposeCityDaySky(sky);
  });

  it("selects the verified HDR for clear weather and exposes unavailable HDR explicitly", () => {
    const render = renderer(), sky = createCityDaySky();
    const { hdr, target } = readyHdr(render), owner = new CityWeatherSkyEnvironment(render, { kind: "verified-hdr-for-clear", environment: hdr });
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("clear") });
    const capture = vi.spyOn(THREE.PMREMGenerator.prototype, "fromScene");
    expect(owner.textureFor(sky, sample)).toBe(target.texture);
    expect(capture).not.toHaveBeenCalled();
    expect(owner.state).toEqual({ source: "verified-hdr", skyCaptures: 0,
      pmremFaceSize: 128, containsCityGeometry: false, policy: "verified-hdr-for-clear" });
    owner.dispose(); hdr.dispose();
    const unavailable = new CityCalibratedEnvironment(render);
    const missingOwner = new CityWeatherSkyEnvironment(render, { kind: "verified-hdr-for-clear", environment: unavailable });
    expect(() => missingOwner.textureFor(sky, sample)).toThrow(/not ready/);
    missingOwner.dispose(); unavailable.dispose(); disposeCityDaySky(sky);
  });

  it("captures the visible calibrated Sky once, with borrowed geometry and no city meshes", () => {
    const render = renderer(), sky = createCityDaySky(), { hdr } = readyHdr(render);
    const owner = new CityWeatherSkyEnvironment(render, { kind: "verified-hdr-for-clear", environment: hdr }), filtered = new THREE.WebGLRenderTarget(384, 512);
    const capture = vi.spyOn(THREE.PMREMGenerator.prototype, "fromScene").mockImplementation((scene, sigma, near, far, options) => {
      expect(scene.children).toHaveLength(1);
      const mesh = scene.children[0] as THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial>;
      expect(mesh.geometry).toBe(sky.geometry);
      expect(mesh.material).not.toBe(sky.material);
      expect(mesh.position.toArray()).toEqual([0, 0, 0]);
      expect(mesh.material.uniforms.cityCalibratedCloudMix?.value)
        .toBe(sky.material.uniforms.cityCalibratedCloudMix?.value);
      expect(mesh.material.uniforms.cityCalibratedOvercast?.value)
        .toEqual(sky.material.uniforms.cityCalibratedOvercast?.value);
      expect(sigma).toBe(0); expect(near).toBe(0.1); expect(far).toBeGreaterThan(20_000);
      expect(options?.size).toBe(CITY_WEATHER_SKY_PMREM_SIZE);
      return filtered;
    });
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("cloudy") });
    expect(owner.textureFor(sky, sample)).toBe(filtered.texture);
    sky.position.set(50, 8, -20); // Camera-following Sky relocation does not change its panorama.
    expect(owner.textureFor(sky, sample)).toBe(filtered.texture);
    expect(capture).toHaveBeenCalledOnce();
    expect(owner.state.skyCaptures).toBe(1);
    owner.dispose(); hdr.dispose(); disposeCityDaySky(sky);
  });

  it("reuses equal appearance from a new observed sample identity and replaces only one sky target", () => {
    const render = renderer(), sky = createCityDaySky(), { hdr, target } = readyHdr(render);
    const owner = new CityWeatherSkyEnvironment(render, { kind: "verified-hdr-for-clear", environment: hdr });
    const first = new THREE.WebGLRenderTarget(384, 512), second = new THREE.WebGLRenderTarget(384, 512);
    const firstDispose = vi.spyOn(first, "dispose"), hdrDispose = vi.spyOn(target, "dispose");
    const capture = vi.spyOn(THREE.PMREMGenerator.prototype, "fromScene")
      .mockReturnValueOnce(first).mockReturnValueOnce(second);
    const observed = { kind: "observed-weather" as const, providerId: "weather-a", sampleId: "a",
      sampleTimeSeconds: 0, settings: { ...cityVisualWeather("cloudy").settings } };
    owner.textureFor(sky, sampleCityLighting({ solar: cityVisualSolar("day"), weather: observed }));
    observed.sampleId = "b"; observed.sampleTimeSeconds = 1;
    owner.textureFor(sky, sampleCityLighting({ solar: cityVisualSolar("day"), weather: observed }));
    expect(capture).toHaveBeenCalledOnce(); expect(firstDispose).not.toHaveBeenCalled();
    owner.textureFor(sky, sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("rain") }));
    expect(capture).toHaveBeenCalledTimes(2); expect(firstDispose).toHaveBeenCalledOnce();
    expect(owner.state.skyCaptures).toBe(2); expect(hdrDispose).not.toHaveBeenCalled();
    owner.dispose(); hdr.dispose(); disposeCityDaySky(sky);
  });

  it("samples haze with low cloud cover and releases only its own material and filtered target", () => {
    const render = renderer(), sky = createCityDaySky(), { hdr, target } = readyHdr(render);
    const owner = new CityWeatherSkyEnvironment(render, { kind: "verified-hdr-for-clear", environment: hdr }), filtered = new THREE.WebGLRenderTarget(384, 512);
    let clonedMaterial: THREE.Material | undefined;
    vi.spyOn(THREE.PMREMGenerator.prototype, "fromScene").mockImplementation(scene => {
      clonedMaterial = (scene.children[0] as THREE.Mesh).material as THREE.Material;
      return filtered;
    });
    const skyDispose = vi.spyOn(sky.material, "dispose"), geometryDispose = vi.spyOn(sky.geometry, "dispose");
    const hdrDispose = vi.spyOn(target, "dispose"), filteredDispose = vi.spyOn(filtered, "dispose");
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: { kind: "visual-settings",
      settings: { ...cityVisualWeather("clear").settings, visibilityM: 300 } } });
    owner.textureFor(sky, sample);
    const materialDispose = vi.spyOn(clonedMaterial!, "dispose");
    owner.dispose(); owner.dispose();
    expect(materialDispose).toHaveBeenCalledOnce(); expect(filteredDispose).toHaveBeenCalledOnce();
    expect(skyDispose).not.toHaveBeenCalled(); expect(geometryDispose).not.toHaveBeenCalled();
    expect(hdrDispose).not.toHaveBeenCalled();
    expect(() => owner.textureFor(sky, sample)).toThrow(/disposed/);
    hdr.dispose(); disposeCityDaySky(sky);
  });

  it("restores renderer state and exposes a failed pass without publishing a capture", () => {
    const render = renderer(), sky = createCityDaySky(), { hdr } = readyHdr(render);
    const owner = new CityWeatherSkyEnvironment(render, { kind: "verified-hdr-for-clear", environment: hdr });
    vi.spyOn(THREE.PMREMGenerator.prototype, "fromScene").mockImplementation(() => {
      render.xr.enabled = false; render.autoClear = false; render.toneMapping = THREE.NoToneMapping;
      throw new Error("sky pass failed");
    });
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("rain") });
    expect(() => owner.textureFor(sky, sample)).toThrow(/sky pass failed/);
    expect(render.xr.enabled).toBe(true); expect(render.autoClear).toBe(true);
    expect(render.shadowMap.autoUpdate).toBe(true); expect(render.toneMapping).toBe(THREE.ACESFilmicToneMapping);
    expect(owner.state.source).toBeNull(); expect(owner.state.skyCaptures).toBe(0);
    owner.dispose(); hdr.dispose(); disposeCityDaySky(sky);
  });
});
