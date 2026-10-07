import type { CityTimeOfDay } from "./city-lighting-calibration";
import { CITY_LIGHTING } from "./city-lighting";

export interface CitySubsystemLighting {
  readonly buildingWindowIntensity: number;
  readonly buildingEnvironmentIntensity: number;
  readonly buildingFacadeEnvironmentIntensity: number;
  readonly roadLampEmissiveIntensity: number;
  readonly roadPointLightIntensity: number;
  readonly roadHaloOpacity: number;
  readonly roadPoolOpacity: number;
  readonly vehicleRearEmissiveIntensity: number;
  readonly vehicleFrontEmissiveIntensity: number;
  readonly vehicleHeadlightIntensity: number;
  readonly advertisingLightIntensity: number;
}

/**
 * Display-lighting policy for subsystems that are not photometrically calibrated.
 * Day and twilight retain the former day/dusk output exactly. Night reduces ambient
 * reflections and increases local-source contrast; the values are artistic renderer
 * intensities, not measured candela, luminance, or occupancy observations.
 */
export const CITY_SUBSYSTEM_LIGHTING: Readonly<Record<CityTimeOfDay,
  Readonly<CitySubsystemLighting>>> = {
  day: {
    buildingWindowIntensity: CITY_LIGHTING.day.windowIntensity,
    buildingEnvironmentIntensity: CITY_LIGHTING.day.environmentIntensity,
    buildingFacadeEnvironmentIntensity: CITY_LIGHTING.day.facadeEnvironmentIntensity,
    roadLampEmissiveIntensity: 0,
    roadPointLightIntensity: 0,
    roadHaloOpacity: 0,
    roadPoolOpacity: 0,
    vehicleRearEmissiveIntensity: 0,
    vehicleFrontEmissiveIntensity: 0,
    vehicleHeadlightIntensity: 0,
    advertisingLightIntensity: 0,
  },
  twilight: {
    buildingWindowIntensity: CITY_LIGHTING.dusk.windowIntensity,
    buildingEnvironmentIntensity: CITY_LIGHTING.dusk.environmentIntensity,
    buildingFacadeEnvironmentIntensity: CITY_LIGHTING.dusk.facadeEnvironmentIntensity,
    roadLampEmissiveIntensity: 2.8,
    roadPointLightIntensity: 350,
    roadHaloOpacity: 0.62,
    roadPoolOpacity: 0.48,
    vehicleRearEmissiveIntensity: 1.1,
    vehicleFrontEmissiveIntensity: 1.8,
    vehicleHeadlightIntensity: 110,
    advertisingLightIntensity: 4,
  },
  night: {
    buildingWindowIntensity: CITY_LIGHTING.dusk.windowIntensity * 1.5,
    buildingEnvironmentIntensity: 0.28,
    buildingFacadeEnvironmentIntensity: 0.34,
    roadLampEmissiveIntensity: 3.6,
    roadPointLightIntensity: 500,
    roadHaloOpacity: 0.76,
    roadPoolOpacity: 0.62,
    vehicleRearEmissiveIntensity: 1.5,
    vehicleFrontEmissiveIntensity: 2.4,
    vehicleHeadlightIntensity: 165,
    advertisingLightIntensity: 5.2,
  },
};
