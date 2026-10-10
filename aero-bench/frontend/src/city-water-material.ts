/**
 * T8a: shared physically based water materials per T3 `water-*` preset.
 *
 * One `THREE.MeshPhysicalMaterial` per preset, shared by every water polygon of
 * that type. The colours are not invented: they are measured from the Shanghai
 * reference photographs of T4 with the recorded derivation of
 * `validation/terrain-realism-20261001/T8a/water-reference-colors.json`
 * (script: `measure-water-references.py` in the same folder). The hex numbers
 * below are copied from that JSON; the test reads the JSON and compares.
 *
 * Every preset is opaque: no scene has bathymetry or bed geometry, so any
 * transparency would expose undefined ground (see `WATER_TRANSPARENCY_POLICY`).
 * Normals, reflections, weather response and shorelines are T8b; renderer
 * wiring is T9. The materials stay unpatched (no `onBeforeCompile`) so T8b can
 * add normals and ripples without unpicking anything here.
 */

import * as THREE from "three";
import { CITY_GROUND_MATERIAL_RULES_V1 } from "./city-ground-material-rules";

/** The `water-*` preset IDs of `CITY_GROUND_MATERIAL_RULES_V1` (fixed by T3). */
const WATER_PRESET_IDS = CITY_GROUND_MATERIAL_RULES_V1.presets
  .map(preset => preset.id)
  .filter(id => id.startsWith("water-"));

/** No scene carries bathymetry or bed geometry: transparency would show
 * undefined ground under every water polygon, so every preset is opaque. */
export const WATER_TRANSPARENCY_POLICY =
  "opaque: no bathymetry or bed geometry in any scene; transparency would expose undefined ground" as const;

export interface WaterPresetLook {
  /** sRGB hex from `water-reference-colors.json` (T8a measured derivation). */
  readonly color: number;
  /** Reference surface state: wind chop is rougher than a calm pond. */
  readonly roughness: number;
  readonly envMapIntensity: number;
  /** The reference photograph IDs the colour was measured from. */
  readonly referenceIds: readonly string[];
  /** Where the colour number comes from. */
  readonly colorBasis: string;
}

/** Reference-measured look per `water-*` preset. Colours are literal hex numbers
 * copied from `water-reference-colors.json`
 * (validation/terrain-realism-20261001/T8a/measure-water-references.py). */
export const WATER_PRESET_LOOKS: Readonly<Record<string, WaterPresetLook>> = Object.freeze({
  "water-river": Object.freeze({
    // water-reference-colors.json: ref06 Huangpu River (overcast, choppy).
    color: 0x3d3c40, roughness: 0.16, envMapIntensity: 1,
    referenceIds: ["ref06_shanghai_huangpu_river_embankment"],
    colorBasis: "water-reference-colors.json: measured ref06 median, saturation x0.9, value x0.55",
  }),
  "water-dock": Object.freeze({
    // water-reference-colors.json: ref06 Huangpu River (overcast, choppy).
    color: 0x3d3c40, roughness: 0.16, envMapIntensity: 1,
    referenceIds: ["ref06_shanghai_huangpu_river_embankment"],
    colorBasis: "water-reference-colors.json: measured ref06 median, saturation x0.9, value x0.55",
  }),
  "water-canal": Object.freeze({
    // water-reference-colors.json: ref07 Suzhou Creek (sunny, calm, reflective).
    color: 0x3b4243, roughness: 0.06, envMapIntensity: 1,
    referenceIds: ["ref07_shanghai_suzhou_creek_embankment"],
    colorBasis: "water-reference-colors.json: measured ref07 median, saturation x0.9, value x0.55",
  }),
  "water-basin": Object.freeze({
    // water-reference-colors.json: ref07 Suzhou Creek (sunny, calm, reflective).
    color: 0x3b4243, roughness: 0.06, envMapIntensity: 1,
    referenceIds: ["ref07_shanghai_suzhou_creek_embankment"],
    colorBasis: "water-reference-colors.json: measured ref07 median, saturation x0.9, value x0.55",
  }),
  "water-pond": Object.freeze({
    // water-reference-colors.json: mean of the ref08 and ref09 Yuyuan pond medians.
    color: 0x262713, roughness: 0.05, envMapIntensity: 1,
    referenceIds: ["ref08_shanghai_yuyuan_garden_pond", "ref09_shanghai_yuyuan_park_pond_cc0"],
    colorBasis: "water-reference-colors.json: mean of ref08/ref09 medians, saturation x0.9, value x0.55",
  }),
  "water-lake": Object.freeze({
    // water-reference-colors.json: mean of all four medians; no lake reference exists.
    color: 0x31332a, roughness: 0.1, envMapIntensity: 1,
    referenceIds: ["ref06_shanghai_huangpu_river_embankment", "ref07_shanghai_suzhou_creek_embankment",
      "ref08_shanghai_yuyuan_garden_pond", "ref09_shanghai_yuyuan_park_pond_cc0"],
    colorBasis: "water-reference-colors.json: mean of all four medians (no type-specific reference), saturation x0.9, value x0.55",
  }),
  "water-reservoir": Object.freeze({
    // water-reference-colors.json: mean of all four medians; no reservoir reference exists.
    color: 0x31332a, roughness: 0.1, envMapIntensity: 1,
    referenceIds: ["ref06_shanghai_huangpu_river_embankment", "ref07_shanghai_suzhou_creek_embankment",
      "ref08_shanghai_yuyuan_garden_pond", "ref09_shanghai_yuyuan_park_pond_cc0"],
    colorBasis: "water-reference-colors.json: mean of all four medians (no type-specific reference), saturation x0.9, value x0.55",
  }),
  "water-generic": Object.freeze({
    // water-reference-colors.json: mean of all four medians; no generic reference exists.
    color: 0x31332a, roughness: 0.1, envMapIntensity: 1,
    referenceIds: ["ref06_shanghai_huangpu_river_embankment", "ref07_shanghai_suzhou_creek_embankment",
      "ref08_shanghai_yuyuan_garden_pond", "ref09_shanghai_yuyuan_park_pond_cc0"],
    colorBasis: "water-reference-colors.json: mean of all four medians (no type-specific reference), saturation x0.9, value x0.55",
  }),
});

export interface WaterMaterials {
  /** The shared material of one preset; the same instance on every call.
   * Throws for non-water or unknown presets. */
  material(presetId: string): THREE.MeshPhysicalMaterial;
  dispose(): void;
}

function assertWaterPreset(presetId: string): void {
  const look = WATER_PRESET_LOOKS[presetId];
  if (look === undefined) {
    throw new Error(`water-material: unknown or non-water preset: ${presetId}`);
  }
}

/** Builds the shared material set. One shared `MeshPhysicalMaterial` per
 * `water-*` preset of `CITY_GROUND_MATERIAL_RULES_V1`, opaque, unpatched
 * (no `onBeforeCompile`) so T8b can add normals and ripples. Throws when a
 * rule-set water preset is missing a look: the look table and the rule set
 * must not drift apart. */
export function createWaterMaterials(): WaterMaterials {
  for (const presetId of WATER_PRESET_IDS) {
    assertWaterPreset(presetId);
  }
  const materials = new Map<string, THREE.MeshPhysicalMaterial>();
  for (const presetId of WATER_PRESET_IDS) {
    const look = WATER_PRESET_LOOKS[presetId]!;
    const material = new THREE.MeshPhysicalMaterial({
      color: look.color,
      roughness: look.roughness,
      metalness: 0,
      ior: 1.333,
      specularIntensity: 1,
      clearcoat: 0,
      envMapIntensity: look.envMapIntensity,
      side: THREE.FrontSide,
      transparent: false,
      opacity: 1,
      transmission: 0,
      depthWrite: true,
    });
    material.name = `water:${presetId}`;
    materials.set(presetId, material);
  }
  return {
    material(presetId: string): THREE.MeshPhysicalMaterial {
      assertWaterPreset(presetId);
      const material = materials.get(presetId);
      if (material === undefined) {
        throw new Error(`water-material: preset has no material: ${presetId}`);
      }
      return material;
    },
    dispose: () => {
      for (const material of materials.values()) material.dispose();
      materials.clear();
    },
  };
}

export interface WaterPresetSaturationEntry {
  readonly presetId: string;
  /** HSL saturation in 0-1, in sRGB space (the space the hex was measured in). */
  readonly hslSaturation: number;
  /** HSL lightness in 0-1, in sRGB space. */
  readonly hslLightness: number;
  readonly hueDeg: number;
}

/** HSL saturation and lightness per preset, in sRGB space, for the T8a tests
 * that keep the presets off the saturated-blue look of the replaced style. */
export function waterPresetSaturationReport(): readonly WaterPresetSaturationEntry[] {
  const target = { h: 0, s: 0, l: 0 };
  return WATER_PRESET_IDS.map(presetId => {
    const look = WATER_PRESET_LOOKS[presetId];
    if (look === undefined) {
      throw new Error(`water-material: unknown or non-water preset: ${presetId}`);
    }
    new THREE.Color(look.color).getHSL(target, THREE.SRGBColorSpace);
    return { presetId, hslSaturation: target.s, hslLightness: target.l, hueDeg: target.h * 360 };
  });
}
