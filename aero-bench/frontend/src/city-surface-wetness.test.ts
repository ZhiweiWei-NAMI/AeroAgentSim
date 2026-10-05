// @vitest-environment node
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { CITY_WEATHER_PRESETS, type CityWeatherSettings } from "./city-weather";
import { applySurfaceWetness, createSurfaceWetnessUniforms, SURFACE_WETNESS_DEFAULTS,
  surfaceWetnessFromWeather } from "./city-surface-wetness";

function settings(overrides: Partial<CityWeatherSettings> = {}): CityWeatherSettings {
  return { ...CITY_WEATHER_PRESETS.clear, ...overrides };
}

describe("surface wetness mapping", () => {
  it("maps clear, snow and hail to zero wetness", () => {
    expect(surfaceWetnessFromWeather(settings())).toBe(0);
    expect(surfaceWetnessFromWeather(settings({ precipitation: "snow", precipitationRateMmPerH: 8 }))).toBe(0);
    expect(surfaceWetnessFromWeather(settings({ precipitation: "hail", precipitationRateMmPerH: 8 }))).toBe(0);
  });
  it("rises monotonically with rain rate and saturates below one", () => {
    let previous = -1;
    for (let rate = 0; rate <= 60; rate += 2) {
      const wetness = surfaceWetnessFromWeather(settings({ precipitation: "rain", precipitationRateMmPerH: rate }));
      expect(wetness).toBeGreaterThan(previous);
      expect(wetness).toBeLessThan(1);
      previous = wetness;
    }
    expect(surfaceWetnessFromWeather(settings({ precipitation: "rain", precipitationRateMmPerH: 6 })))
      .toBeCloseTo(1 - Math.E ** -1, 12);
    expect(surfaceWetnessFromWeather(settings({ precipitation: "rain", precipitationRateMmPerH: 120 })))
      .toBeGreaterThan(0.999999);
  });
  it("treats drizzle as half the film exposure of rain at the same rate", () => {
    const rain = surfaceWetnessFromWeather(settings({ precipitation: "rain", precipitationRateMmPerH: 4 }));
    const drizzle = surfaceWetnessFromWeather(settings({ precipitation: "drizzle", precipitationRateMmPerH: 4 }));
    expect(drizzle).toBeGreaterThan(0);
    // rate 4 * 0.5 drizzle factor / 6 saturation rate -> exposure 1/3.
    expect(drizzle).toBeCloseTo(1 - Math.E ** -(1 / 3), 12);
    expect(rain).toBeCloseTo(1 - Math.E ** -(4 / 6), 12);
  });
  it("propagates the preset rain wetness and rejects invalid settings", () => {
    expect(surfaceWetnessFromWeather(CITY_WEATHER_PRESETS.rain)).toBeCloseTo(1 - Math.E ** -1, 12);
    expect(surfaceWetnessFromWeather(CITY_WEATHER_PRESETS.snow)).toBe(0);
    expect(() => surfaceWetnessFromWeather(settings({ windMps: -3 }))).toThrow(RangeError);
    expect(() => surfaceWetnessFromWeather(settings({ precipitationRateMmPerH: NaN }))).toThrow(RangeError);
    expect(() => surfaceWetnessFromWeather(null as unknown as CityWeatherSettings)).toThrow(/required/);
  });
});

describe("surface wetness shader patch", () => {
  it("inserts the uniform, world-normal gate, darkening and roughness wiring", () => {
    const material = new THREE.MeshStandardMaterial({ name: "road-surface" });
    const u = createSurfaceWetnessUniforms();
    applySurfaceWetness(material, u);
    const shader = { uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: "void main() {\n#include <common>\n#include <begin_vertex>\n#include <beginnormal_vertex>\n}",
      fragmentShader: "void main() {\n#include <common>\n#include <map_fragment>\n#include <roughnessmap_fragment>\n}" };
    (material.onBeforeCompile as NonNullable<THREE.Material["onBeforeCompile"]>)(shader as never, {} as never);
    expect(shader.uniforms.uSurfaceWetness).toBe(u.uWetness);
    expect(shader.vertexShader).toContain("normalize(mat3(modelMatrix) * surfaceWetnessNormal).y");
    expect(shader.vertexShader).not.toContain("vec4(transformed, 1.0)");
    expect(shader.vertexShader).toContain("varying float vSurfaceUpFacing;");
    expect(shader.vertexShader).toContain("#include <beginnormal_vertex>");
    expect(shader.fragmentShader).toContain("uniform float uSurfaceWetness;");
    expect(shader.fragmentShader).toContain("roughnessFactor = mix(roughnessFactor, 0.080000, surfaceWetness);");
    expect(shader.fragmentShader).toContain("diffuseColor.rgb *= 1.0 - 0.450000 * surfaceWetnessDarken;");
    expect(material.customProgramCacheKey()).toBe("city-surface-wetness-v2:0.450000:0.080000");
    expect(SURFACE_WETNESS_DEFAULTS.maxDarkening).toBe(0.45);
    expect(SURFACE_WETNESS_DEFAULTS.minRoughness).toBe(0.08);
  });
  it("honours custom limits in the generated shader", () => {
    const material = new THREE.MeshPhysicalMaterial();
    applySurfaceWetness(material, createSurfaceWetnessUniforms(), { maxDarkening: 0.2, minRoughness: 0.3 });
    const shader = { uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: "#include <common>\n#include <beginnormal_vertex>",
      fragmentShader: "#include <common>\n#include <map_fragment>\n#include <roughnessmap_fragment>" };
    (material.onBeforeCompile as NonNullable<THREE.Material["onBeforeCompile"]>)(shader as never, {} as never);
    expect(shader.fragmentShader).toContain("0.200000");
    expect(shader.fragmentShader).toContain("0.300000");
  });
  it("keys the shader program by its limits so different limits never share a program", () => {
    const porous = new THREE.MeshStandardMaterial(), sealed = new THREE.MeshStandardMaterial();
    const twin = new THREE.MeshStandardMaterial(), u = createSurfaceWetnessUniforms();
    applySurfaceWetness(porous, u, { maxDarkening: 0.24, minRoughness: 0.6 });
    applySurfaceWetness(sealed, u, { maxDarkening: 0.4, minRoughness: 0.22 });
    applySurfaceWetness(twin, u, { maxDarkening: 0.24, minRoughness: 0.6 });
    expect(porous.customProgramCacheKey()).not.toBe(sealed.customProgramCacheKey());
    expect(porous.customProgramCacheKey()).toBe(twin.customProgramCacheKey());
  });
  it("is idempotent-free: a second application throws instead of stacking", () => {
    const material = new THREE.MeshStandardMaterial();
    applySurfaceWetness(material, createSurfaceWetnessUniforms());
    expect(() => applySurfaceWetness(material, createSurfaceWetnessUniforms())).toThrow(/unpatched/);
  });
  it("refuses materials that already carry another compile hook and rejects bad limits", () => {
    const hooked = new THREE.MeshStandardMaterial();
    hooked.onBeforeCompile = () => undefined;
    expect(() => applySurfaceWetness(hooked, createSurfaceWetnessUniforms())).toThrow(/unpatched/);
    const clean = new THREE.MeshStandardMaterial();
    expect(() => applySurfaceWetness(clean, createSurfaceWetnessUniforms(), { maxDarkening: 1.4 }))
      .toThrow(RangeError);
    expect(() => applySurfaceWetness(clean, createSurfaceWetnessUniforms(), { minRoughness: -1 }))
      .toThrow(RangeError);
    expect(() => applySurfaceWetness(clean, createSurfaceWetnessUniforms(), { minRoughness: 1.1 }))
      .toThrow(RangeError);
  });
});
