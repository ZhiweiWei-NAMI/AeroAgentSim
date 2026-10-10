// @vitest-environment node
import { readFileSync } from "node:fs";
import * as THREE from "three";
import { HDRLoader } from "three/addons/loaders/HDRLoader.js";
import { describe, expect, it, vi } from "vitest";
import { applyCityLightingCalibration, applyCitySkyCalibration, calibrateCityHdr, CITY_CALIBRATION_HDR, CityCalibratedEnvironment,
  cityVisualSolar, focusCityCalibratedSun, sampleCityLighting } from "./city-lighting-calibration";
import { cityVisualWeather, checkCityWeatherInput } from "./city-weather-calibration";
import { buildCityFacadeRoughness, setCityBuildingCalibration, disposePresentation } from "./city-presentation";
import { createCityDaySky, disposeCityDaySky } from "./city-day-sky";
import { CITY_BUILDING_MATERIAL_ROLE_KEY, readCityFacadePaneProfile } from "./city-facade-pane-profile";

describe("city presentation light calibration", () => {
  it("releases each scene's materials while the environment owner keeps shared HDR targets alive", () => {
    const environment = new CityCalibratedEnvironment({} as THREE.WebGLRenderer);
    const target = new THREE.WebGLRenderTarget(8, 8);
    const targets = Reflect.get(environment, "targets") as Map<string, THREE.WebGLRenderTarget>;
    targets.set("day", target);
    const texture = environment.textureFor("day");
    const textureDispose = vi.spyOn(texture, "dispose"), targetDispose = vi.spyOn(target, "dispose");
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("clear") });
    const first = new THREE.Group(), second = new THREE.Group();
    const firstMaterial = new THREE.MeshStandardMaterial(), secondMaterial = new THREE.MeshStandardMaterial();
    firstMaterial.name = secondMaterial.name = "roof.plain";
    firstMaterial.userData[CITY_BUILDING_MATERIAL_ROLE_KEY] = "opaque-roof";
    secondMaterial.userData[CITY_BUILDING_MATERIAL_ROLE_KEY] = "opaque-roof";
    first.add(new THREE.Mesh(new THREE.BoxGeometry(), firstMaterial));
    second.add(new THREE.Mesh(new THREE.BoxGeometry(), secondMaterial));
    const firstDispose = vi.spyOn(firstMaterial, "dispose");
    setCityBuildingCalibration(first, sample, texture);
    setCityBuildingCalibration(second, sample, texture);
    disposePresentation(first);
    expect(firstDispose).toHaveBeenCalledOnce();
    expect(textureDispose).not.toHaveBeenCalled();
    expect(targetDispose).not.toHaveBeenCalled();
    expect(secondMaterial.envMap).toBe(texture);
    expect(environment.textureFor("day")).toBe(texture);
    disposePresentation(second);
    expect(textureDispose).not.toHaveBeenCalled();
    environment.dispose();
    expect(targetDispose).toHaveBeenCalledOnce();
    expect(() => environment.textureFor("day")).toThrow(/disposed/);
  });

  it("aborts a pending verified HDR request on dispose and never publishes a target", async () => {
    let requestedSignal: AbortSignal | undefined;
    vi.stubGlobal("fetch", (_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      requestedSignal = init.signal ?? undefined;
      requestedSignal!.addEventListener("abort", () => reject(new DOMException("HDR aborted", "AbortError")), { once: true });
    }));
    try {
      const environment = new CityCalibratedEnvironment({} as THREE.WebGLRenderer);
      const pending = environment.load();
      const rejection = expect(pending).rejects.toThrow(/HDR aborted/);
      environment.dispose();
      await rejection;
      expect(requestedSignal?.aborted).toBe(true);
      expect(environment.calibration).toBeNull();
      expect(() => environment.textureFor("day")).toThrow(/disposed/);
    } finally { vi.unstubAllGlobals(); }
  });

  it("binds one declared HDR and rejects wrong bytes before touching GPU state", async () => {
    const file = readFileSync(`public${CITY_CALIBRATION_HDR.url}`);
    vi.stubGlobal("fetch", async () => new Response(file));
    const environment = new CityCalibratedEnvironment({} as THREE.WebGLRenderer);
    const declaration = { ...CITY_CALIBRATION_HDR, sha256: "0".repeat(64) };
    try {
      const pending = environment.load(declaration);
      const rejection = expect(pending).rejects.toThrow(/digest/);
      expect(environment.load({ ...declaration })).toBe(pending);
      expect(() => environment.load(CITY_CALIBRATION_HDR)).toThrow(/declaration cannot change/);
      await rejection;
      expect(environment.calibration).toBeNull();
      environment.dispose();
      expect(() => environment.textureFor("day")).toThrow(/disposed/);
      expect(() => environment.load()).toThrow(/disposed/);
    } finally { vi.unstubAllGlobals(); }
  });

  it("uses the supplied HDR's empty ground and solar hotspot as calibration evidence", () => {
    const file = readFileSync(`public${CITY_CALIBRATION_HDR.url}`);
    expect(file.byteLength).toBe(CITY_CALIBRATION_HDR.sizeBytes);
    const decoded = new HDRLoader().setDataType(THREE.FloatType)
      .parse(file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength));
    const calibration = calibrateCityHdr(decoded.data as Float32Array, decoded.width!, decoded.height!);
    expect(calibration.sourceSkyMean).toBeGreaterThan(0);
    expect(calibration.sourceGroundMean).toBeLessThan(calibration.sourceSkyMean * 0.01);
    expect(calibration.sourceMaximum).toBeGreaterThan(6);
    expect(calibration.clippedPixels).toBeGreaterThan(0);
    expect(calibration.normalizedSkyMean).toBeCloseTo(0.62);
    expect(calibration.completedGroundMean).toBeGreaterThan(0);
    expect(calibration.completedGroundMean).toBeLessThan(calibration.normalizedSkyMean);
  });

  it("rejects a different panorama rather than assuming its lower hemisphere is empty", () => {
    const full = new Float32Array(16 * 8 * 4).fill(1);
    expect(() => calibrateCityHdr(full, 16, 8)).toThrow(/sky-only/);
    full[0] = Number.NaN;
    expect(() => calibrateCityHdr(full, 16, 8)).toThrow(/invalid radiance/);
  });

  it("keeps rain extinction, windows and below-horizon observed sunlight distinct", () => {
    const sunny = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("clear") });
    const rain = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("rain") });
    const night = sampleCityLighting({ solar: cityVisualSolar("night"), weather: cityVisualWeather("clear") });
    const twilight = sampleCityLighting({ solar: cityVisualSolar("twilight"), weather: cityVisualWeather("cloudy") });
    expect(rain.sunIntensity).toBeLessThan(sunny.sunIntensity * 0.1);
    expect(rain.environmentIntensity).toBeGreaterThan(sunny.environmentIntensity * 0.8);
    expect(rain.wetness).toBeGreaterThan(0.8);
    expect(sunny.windowIntensity).toBe(0);
    expect(twilight.windowIntensity).toBeGreaterThan(0);
    expect(night.windowIntensity).toBeGreaterThan(twilight.windowIntensity);
    expect(night.solar.kind).toBe("artistic-preset");
    const observed = sampleCityLighting({ solar: { kind: "sun-position", elevationDeg: -12,
      azimuthDeg: 290, sourceId: "known-position", sampleTimeSeconds: 34 }, weather: cityVisualWeather("clear") });
    expect(observed.sunIntensity).toBe(0);
    expect(observed.solar.kind).toBe("sun-position");
  });

  it("preserves weather sample identity and isolates the sampled settings from mutation", () => {
    const weather = { kind: "observed-weather" as const, providerId: "weather-source", sampleId: "frame-34",
      sampleTimeSeconds: 34, settings: { ...cityVisualWeather("rain").settings } };
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather });
    weather.settings.visibilityM = 20;
    expect(sample.weather.settings.visibilityM).toBe(1800);
    expect(sample.weather.kind).toBe("observed-weather");
    expect(() => checkCityWeatherInput({ ...weather, providerId: "" })).toThrow(/Provider/);
    expect(() => checkCityWeatherInput({ ...cityVisualWeather("clear"),
      settings: { ...cityVisualWeather("clear").settings, cloudCover: 0.5 } })).toThrow(/preset/);
  });

  it("targets the existing light and view shadow without changing geometry, map or camera height", () => {
    const scene = new THREE.Scene(), sun = new THREE.DirectionalLight();
    const hemisphere = new THREE.HemisphereLight(), ambient = new THREE.AmbientLight();
    scene.add(sun, hemisphere, ambient);
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("clear") });
    const renderer = { toneMapping: THREE.NoToneMapping, toneMappingExposure: 1 } as THREE.WebGLRenderer;
    const envelope = new THREE.Box3(new THREE.Vector3(-500, -2, -500), new THREE.Vector3(500, 120, 500));
    const camera = new THREE.PerspectiveCamera(); camera.position.set(10, 7, 20);
    const focus = new THREE.Vector3(0, 7, 0), environment = new THREE.Texture();
    applyCityLightingCalibration({ scene, renderer, sun, hemisphere, ambient }, sample, environment);
    focusCityCalibratedSun(sun, sample, envelope, focus, camera);
    expect(scene.children).toHaveLength(3);
    expect(scene.environment).toBe(environment);
    expect(renderer.toneMapping).toBe(THREE.ACESFilmicToneMapping);
    expect(camera.position.y).toBe(7);
    expect(envelope.max.y).toBe(120);
    expect(sun.shadow.camera.right).toBe(35);
    expect(sun.target.position.toArray()).toEqual(focus.toArray());
    const wide = new THREE.Box3(new THREE.Vector3(-2000, 0, -2000), new THREE.Vector3(2000, 300, 2000));
    camera.position.set(1000, 500, 1000);
    focusCityCalibratedSun(sun, sample, wide, focus, camera);
    expect(sun.shadow.camera.right).toBeGreaterThan(900);
    const lowSun = sampleCityLighting({ solar: { kind: "sun-position", sourceId: "position",
      sampleTimeSeconds: 0, elevationDeg: 3, azimuthDeg: 45 }, weather: cityVisualWeather("clear") });
    focusCityCalibratedSun(sun, lowSun, envelope, focus, camera);
    expect(sun.position.clone().sub(focus).normalize().dot(lowSun.sunDirection)).toBeCloseTo(1);
  });

  it("grades dusk and overcast in the existing sky draw and installs the shader once", () => {
    const sky = createCityDaySky(), geometry = sky.geometry;
    const dusk = sampleCityLighting({ solar: cityVisualSolar("twilight"), weather: cityVisualWeather("clear") });
    applyCitySkyCalibration(sky, dusk);
    expect(sky.geometry).toBe(geometry);
    expect(sky.material.uniforms.cityCalibratedTwilightMix?.value).toBeGreaterThan(0.9);
    expect(sky.material.uniforms.cityCalibratedCloudMix?.value).toBe(0);
    const shader = sky.material.fragmentShader, version = sky.material.version;
    applyCitySkyCalibration(sky, sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("rain") }));
    expect(sky.material.uniforms.cityCalibratedCloudMix?.value).toBeGreaterThan(0.99);
    expect(sky.material.uniforms.cityCalibratedTwilightMix?.value).toBe(0);
    expect(sky.material.fragmentShader).toBe(shader);
    expect(sky.material.version).toBe(version);
    disposeCityDaySky(sky);
  });
});

describe("source facade roughness estimate", () => {
  it("keeps masonry diffuse and windows dielectric without changing supplied source pixels", () => {
    const albedo = new Uint8ClampedArray([190, 160, 140, 255, 24, 40, 70, 255, 140, 120, 90, 255]);
    const illumination = new Uint8ClampedArray([0, 0, 0, 255, 200, 180, 160, 255, 0, 0, 0, 255]);
    const before = albedo.slice();
    const profile = readCityFacadePaneProfile({
      schema_version: "aero-bench.authored-facade-pane-profile/v1", provenance: "source-texture-art",
      coordinate_system: "image-top-left-pixel", style_id: "independent-source-pane", material_kind: "glass-and-masonry",
      tile_size_px: [3, 1], source_crop_px: [0, 0, 3, 1], glass_rectangles_px: [[1, 0, 2, 1]], opaque_polygons_px: [],
    });
    const result = buildCityFacadeRoughness(albedo, illumination, profile);
    expect(result[1]).toBeGreaterThan(200);
    expect(result[5]).toBeLessThan(60);
    expect([result[2], result[6], result[10]]).toEqual([0, 0, 0]);
    expect(albedo).toEqual(before);
    expect(() => buildCityFacadeRoughness(albedo, illumination.subarray(0, 4), profile))
      .toThrow(/matching RGBA/);
  });
});
