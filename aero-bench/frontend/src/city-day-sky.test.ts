// @vitest-environment node
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import {
  CITY_DAY_SKY_RADIUS_M,
  CITY_DAY_SKY_RADIANCE_GAIN_DEFAULT,
  CITY_DAY_SKY_SUN_DISTANCE_M,
  centerCityDaySky,
  createCityDaySky,
  disposeCityDaySky,
  setCityDaySkySunDirection,
  setCityDaySkyRadianceGain,
  setCityDaySkyWeather,
} from "./city-day-sky";
import { CITY_WEATHER_CLEAR, CITY_WEATHER_PRESETS } from "./city-weather";

describe("city daylight atmosphere", () => {
  it("creates one bounded sky mesh without lights or procedural cloud cost", () => {
    const sky = createCityDaySky();
    expect(sky.name).toBe("city-day-atmosphere");
    expect(sky.scale.toArray()).toEqual([CITY_DAY_SKY_RADIUS_M, CITY_DAY_SKY_RADIUS_M, CITY_DAY_SKY_RADIUS_M]);
    expect(sky.material.uniforms.cloudCoverage?.value).toBe(0);
    expect(sky.material.uniforms.cloudDensity?.value).toBe(0);
    expect(sky.material.uniforms.showSunDisc?.value).toBe(0);
    expect(sky.material.uniforms.cityDaySkyRadianceGain?.value).toBe(CITY_DAY_SKY_RADIANCE_GAIN_DEFAULT);
    const gainAt = sky.material.fragmentShader.indexOf("texColor * cityDaySkyRadianceGain");
    const toneMappingAt = sky.material.fragmentShader.indexOf("#include <tonemapping_fragment>");
    expect(gainAt).toBeGreaterThan(-1);
    expect(gainAt).toBeLessThan(toneMappingAt);
    expect(sky.material.uniforms.sunPosition?.value.length()).toBeCloseTo(CITY_DAY_SKY_SUN_DISTANCE_M);
    sky.geometry.dispose();
    sky.material.dispose();
  });

  it("exposes a bounded sky-only radiance gain", () => {
    const sky = createCityDaySky();
    setCityDaySkyRadianceGain(sky, 0.28);
    expect(sky.material.uniforms.cityDaySkyRadianceGain?.value).toBe(0.28);
    expect(() => setCityDaySkyRadianceGain(sky, 0)).toThrow(RangeError);
    expect(() => setCityDaySkyRadianceGain(sky, 1.1)).toThrow(RangeError);
    expect(() => createCityDaySky(Number.NaN)).toThrow(RangeError);
    sky.geometry.dispose();
    sky.material.dispose();
  });

  it("tracks sun direction and scales atmospheric haze with weather", () => {
    const sky = createCityDaySky();
    setCityDaySkySunDirection(sky, new THREE.Vector3(3, 4, 0));
    const sun = sky.material.uniforms.sunPosition?.value as THREE.Vector3;
    expect(sun.x / CITY_DAY_SKY_SUN_DISTANCE_M).toBeCloseTo(0.6);
    expect(sun.y / CITY_DAY_SKY_SUN_DISTANCE_M).toBeCloseTo(0.8);

    setCityDaySkyWeather(sky, CITY_WEATHER_CLEAR);
    const clearTurbidity = sky.material.uniforms.turbidity?.value as number;
    setCityDaySkyWeather(sky, CITY_WEATHER_PRESETS.rain);
    expect(sky.material.uniforms.turbidity?.value as number).toBeGreaterThan(clearTurbidity);
    expect(sky.material.uniforms.cloudCoverage?.value).toBe(0);
    expect(sky.material.uniforms.cloudDensity?.value).toBe(0);
    sky.geometry.dispose();
    sky.material.dispose();
  });

  it("centers at the camera and releases the single sky draw resources", () => {
    const sky = createCityDaySky();
    const position = new THREE.Vector3(15, 42, -88);
    const geometryDispose = vi.spyOn(sky.geometry, "dispose");
    const materialDispose = vi.spyOn(sky.material, "dispose");
    centerCityDaySky(sky, position);
    expect(sky.position.toArray()).toEqual(position.toArray());
    disposeCityDaySky(sky);
    expect(geometryDispose).toHaveBeenCalledOnce();
    expect(materialDispose).toHaveBeenCalledOnce();
    expect(() => centerCityDaySky(sky, new THREE.Vector3(Number.NaN, 0, 0))).toThrow(RangeError);
    expect(() => setCityDaySkySunDirection(sky, new THREE.Vector3())).toThrow(RangeError);
    expect(() => setCityDaySkyWeather(sky, { ...CITY_WEATHER_CLEAR, cloudCover: 1.1 })).toThrow(RangeError);
    expect(() => setCityDaySkyWeather(sky, { ...CITY_WEATHER_CLEAR, visibilityM: 0 })).toThrow(RangeError);
  });
});
