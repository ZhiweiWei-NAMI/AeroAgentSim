import { checkCityWeatherSettings, type CityWeatherSettings } from "./city-weather";
import type { CityTimeOfDay } from "./city-lighting-calibration";

/** Stored draft time of day. It is exactly the calibrated renderer vocabulary
 * (src/city-lighting-calibration.ts); "night" must be persistable. */
export type CityDraftTimeOfDay = CityTimeOfDay;

/** The one versioned environment domain model shared by the workspace draft,
 * the studio and the calibrated render bar. Weather fields keep the
 * CityWeatherSettings contract; the legacy day/dusk mood is not part of it. */
export interface CityDraftEnvironment extends CityWeatherSettings {
  timeOfDay: CityDraftTimeOfDay;
  reflectionsEnabled: boolean;
}

const TIME_OF_DAY: readonly CityDraftTimeOfDay[] = ["day", "twilight", "night"];
const PRECIPITATION: readonly CityWeatherSettings["precipitation"][] =
  ["none", "drizzle", "rain", "snow", "hail"];

export function parseCityDraftEnvironment(value: unknown): CityDraftEnvironment {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("City draft environment must be an object");
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  const expected = ["cloudCover", "precipitation", "precipitationRateMmPerH", "reflectionsEnabled",
    "timeOfDay", "visibilityM", "windDirectionDeg", "windMps"].sort();
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) {
    throw new Error(`City draft environment has unknown fields: ${keys.join(", ") || "(empty)"}`);
  }
  if (typeof record.timeOfDay !== "string" || !TIME_OF_DAY.includes(record.timeOfDay as CityDraftTimeOfDay)) {
    throw new Error(`City draft environment timeOfDay must be one of ${TIME_OF_DAY.join("|")}`);
  }
  if (typeof record.precipitation !== "string"
      || !PRECIPITATION.includes(record.precipitation as CityWeatherSettings["precipitation"])) {
    throw new Error("City draft environment precipitation is unknown");
  }
  if (typeof record.reflectionsEnabled !== "boolean") {
    throw new Error("City draft environment reflectionsEnabled must be boolean");
  }
  const weather: CityWeatherSettings = {
    cloudCover: record.cloudCover as number,
    precipitation: record.precipitation as CityWeatherSettings["precipitation"],
    precipitationRateMmPerH: record.precipitationRateMmPerH as number,
    visibilityM: record.visibilityM as number,
    windMps: record.windMps as number,
    windDirectionDeg: record.windDirectionDeg as number,
  };
  checkCityWeatherSettings(weather);
  return { ...weather, timeOfDay: record.timeOfDay as CityDraftTimeOfDay, reflectionsEnabled: record.reflectionsEnabled };
}

/** Legacy drafts stored mood: "day" | "dusk" without a night option. The dusk
 * visual preset is the calibrated twilight; day stays day. No other value is
 * accepted and the weather fields are re-validated against the same contract. */
export function migrateLegacyEnvironment(legacy: {
  cloudCover: number;
  precipitation: "none" | "drizzle" | "rain" | "snow" | "hail";
  precipitationRateMmPerH: number;
  visibilityM: number;
  windMps: number;
  windDirectionDeg: number;
  mood: "day" | "dusk";
  reflectionsEnabled: boolean;
}): CityDraftEnvironment {
  if (legacy === null || typeof legacy !== "object") {
    throw new Error("Legacy city environment must be an object");
  }
  if (legacy.mood !== "day" && legacy.mood !== "dusk") {
    throw new Error(`Legacy city environment mood must be "day" or "dusk", got ${JSON.stringify(legacy.mood)}`);
  }
  const weather: CityWeatherSettings = {
    cloudCover: legacy.cloudCover,
    precipitation: legacy.precipitation,
    precipitationRateMmPerH: legacy.precipitationRateMmPerH,
    visibilityM: legacy.visibilityM,
    windMps: legacy.windMps,
    windDirectionDeg: legacy.windDirectionDeg,
  };
  checkCityWeatherSettings(weather);
  return { ...weather, timeOfDay: legacy.mood === "dusk" ? "twilight" : "day",
    reflectionsEnabled: legacy.reflectionsEnabled };
}

/** Weather-only view for render paths that must not learn about the draft envelope. */
export function weatherOf(environment: CityDraftEnvironment): CityWeatherSettings {
  return {
    cloudCover: environment.cloudCover,
    precipitation: environment.precipitation,
    precipitationRateMmPerH: environment.precipitationRateMmPerH,
    visibilityM: environment.visibilityM,
    windMps: environment.windMps,
    windDirectionDeg: environment.windDirectionDeg,
  };
}
