import { describe, expect, it } from "vitest";
import { CITY_WEATHER_PRESETS, type CityWeatherSettings } from "./city-weather";
import { migrateLegacyEnvironment, parseCityDraftEnvironment, weatherOf } from "./city-draft-environment";

const valid = {
  cloudCover: 0.88, precipitation: "rain", precipitationRateMmPerH: 6, visibilityM: 1800,
  windMps: 7, windDirectionDeg: 70, timeOfDay: "night", reflectionsEnabled: true,
} as const;

describe("city draft environment", () => {
  it("parses every calibrated time of day and returns a fresh object", () => {
    const parsed = parseCityDraftEnvironment(valid);
    expect(parsed).toEqual({ ...valid, timeOfDay: "night" });
    expect(parsed).not.toBe(valid);
    for (const timeOfDay of ["day", "twilight", "night"] as const) {
      expect(parseCityDraftEnvironment({ ...valid, timeOfDay }).timeOfDay).toBe(timeOfDay);
    }
  });

  it("rejects unknown keys, missing keys, and non-objects", () => {
    expect(() => parseCityDraftEnvironment({ ...valid, mood: "dusk" })).toThrow(/unknown fields/);
    expect(() => parseCityDraftEnvironment({ ...valid, extra: 1 })).toThrow(/unknown fields/);
    const { timeOfDay: _dropped, ...withoutTime } = valid;
    expect(() => parseCityDraftEnvironment(withoutTime)).toThrow(/unknown fields/);
    expect(() => parseCityDraftEnvironment(null)).toThrow(/must be an object/);
    expect(() => parseCityDraftEnvironment([valid])).toThrow(/must be an object/);
    expect(() => parseCityDraftEnvironment("day")).toThrow(/must be an object/);
  });

  it("rejects unknown time-of-day, precipitation and reflection values", () => {
    expect(() => parseCityDraftEnvironment({ ...valid, timeOfDay: "dusk" })).toThrow(/timeOfDay/);
    expect(() => parseCityDraftEnvironment({ ...valid, timeOfDay: "morning" })).toThrow(/timeOfDay/);
    expect(() => parseCityDraftEnvironment({ ...valid, timeOfDay: 1 })).toThrow(/timeOfDay/);
    expect(() => parseCityDraftEnvironment({ ...valid, precipitation: "monsoon" })).toThrow(/precipitation/);
    expect(() => parseCityDraftEnvironment({ ...valid, reflectionsEnabled: "yes" })).toThrow(/reflectionsEnabled/);
  });

  it("keeps the CityWeatherSettings finite-range contract", () => {
    expect(() => parseCityDraftEnvironment({ ...valid, cloudCover: 1.2 })).toThrow(RangeError);
    expect(() => parseCityDraftEnvironment({ ...valid, visibilityM: 0 })).toThrow(RangeError);
    expect(() => parseCityDraftEnvironment({ ...valid, windMps: -1 })).toThrow(RangeError);
    expect(() => parseCityDraftEnvironment({ ...valid, windDirectionDeg: "east" })).toThrow(RangeError);
    expect(() => parseCityDraftEnvironment({ ...valid, precipitationRateMmPerH: -2 })).toThrow(RangeError);
    expect(() => parseCityDraftEnvironment({ ...valid, precipitation: "none", precipitationRateMmPerH: 3 }))
      .toThrow(RangeError);
    expect(parseCityDraftEnvironment({ ...valid, precipitation: "hail" }).precipitation).toBe("hail");
  });

  it("migrates legacy day/dusk moods and keeps night persistable", () => {
    const legacy = {
      cloudCover: 0.72, precipitation: "none", precipitationRateMmPerH: 0, visibilityM: 7500,
      windMps: 4, windDirectionDeg: 70, mood: "day", reflectionsEnabled: false,
    } as const;
    expect(migrateLegacyEnvironment(legacy)).toEqual({
      cloudCover: 0.72, precipitation: "none", precipitationRateMmPerH: 0, visibilityM: 7500,
      windMps: 4, windDirectionDeg: 70, timeOfDay: "day", reflectionsEnabled: false,
    });
    expect(migrateLegacyEnvironment({ ...legacy, mood: "dusk" }).timeOfDay).toBe("twilight");
    expect(migrateLegacyEnvironment(legacy).reflectionsEnabled).toBe(false);
  });

  it("accepts only the documented legacy mood values and revalidates weather", () => {
    const legacy = { ...CITY_WEATHER_PRESETS.rain, mood: "dusk" as const, reflectionsEnabled: true };
    expect(migrateLegacyEnvironment(legacy).timeOfDay).toBe("twilight");
    expect(() => migrateLegacyEnvironment({ ...legacy, mood: "night" as "day" | "dusk" })).toThrow(/mood/);
    expect(() => migrateLegacyEnvironment({ ...legacy, mood: undefined as unknown as "day" | "dusk" })).toThrow(/mood/);
    expect(() => migrateLegacyEnvironment({ ...legacy, visibilityM: 0 })).toThrow(RangeError);
  });

  it("exposes the weather-only view without the draft envelope", () => {
    const environment = parseCityDraftEnvironment(valid);
    const weather: CityWeatherSettings = weatherOf(environment);
    expect(weather).toEqual({
      cloudCover: 0.88, precipitation: "rain", precipitationRateMmPerH: 6,
      visibilityM: 1800, windMps: 7, windDirectionDeg: 70,
    });
    expect("timeOfDay" in weather).toBe(false);
    expect(weatherOf({ ...environment, timeOfDay: "twilight" })).toEqual(weather);
  });
});
