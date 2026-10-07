import { CITY_WEATHER_PRESETS, checkCityWeatherSettings, type CityWeatherSettings } from "./city-weather";

export type CityWeatherPreset = keyof typeof CITY_WEATHER_PRESETS;

/** Provenance belongs to display input. This module never publishes a Provider observation. */
export type CityWeatherInput =
  | { readonly kind: "artistic-preset"; readonly preset: CityWeatherPreset;
      readonly settings: Readonly<CityWeatherSettings> }
  | { readonly kind: "visual-settings"; readonly settings: Readonly<CityWeatherSettings> }
  | { readonly kind: "observed-weather"; readonly providerId: string; readonly sampleId: string;
      readonly sampleTimeSeconds: number; readonly settings: Readonly<CityWeatherSettings> };

export function cityVisualWeather(preset: CityWeatherPreset): CityWeatherInput {
  if (!Object.hasOwn(CITY_WEATHER_PRESETS, preset)) throw new Error(`Unknown city weather preset: ${preset}`);
  return { kind: "artistic-preset", preset, settings: { ...CITY_WEATHER_PRESETS[preset] } };
}

export function checkCityWeatherInput(input: CityWeatherInput): void {
  if (input === null || typeof input !== "object") throw new Error("City weather input is required");
  checkCityWeatherSettings(input.settings);
  if (input.kind === "artistic-preset") {
    if (!Object.hasOwn(CITY_WEATHER_PRESETS, input.preset)) throw new Error("Unknown city weather preset");
    if (Object.entries(CITY_WEATHER_PRESETS[input.preset]).some(([key, value]) =>
      input.settings[key as keyof CityWeatherSettings] !== value)) {
      throw new Error("Artistic preset settings differ from the declared preset");
    }
  } else if (input.kind === "observed-weather") {
    if (typeof input.providerId !== "string" || !input.providerId.trim()
        || typeof input.sampleId !== "string" || !input.sampleId.trim()
        || !Number.isFinite(input.sampleTimeSeconds) || input.sampleTimeSeconds < 0) {
      throw new Error("Observed weather needs a Provider, sample identity and nonnegative sample time");
    }
  } else if (input.kind !== "visual-settings") {
    throw new Error("Unknown city weather provenance");
  }
}

/** Cloud extinction and diffuse fill share one input, so an overcast sky cannot retain full sun. */
export function cityWeatherLightFactors(settings: CityWeatherSettings): {
  readonly direct: number; readonly diffuse: number; readonly wetness: number;
} {
  checkCityWeatherSettings(settings);
  return {
    direct: Math.exp(-2.8 * settings.cloudCover),
    diffuse: 1 - 0.16 * settings.cloudCover,
    wetness: settings.precipitation === "rain" || settings.precipitation === "drizzle"
      ? 1 - Math.exp(-settings.precipitationRateMmPerH / 3) : 0,
  };
}
