// @vitest-environment node
import { readFileSync } from "node:fs";
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { CITY_GROUND_MATERIAL_RULES_V1 } from "./city-ground-material-rules";
import {
  createWaterMaterials,
  WATER_PRESET_LOOKS,
  WATER_TRANSPARENCY_POLICY,
  waterPresetSaturationReport,
} from "./city-water-material";

/** The measured reference colours this module's literal hex numbers are copied
 * from (T8a, produced by `measure-water-references.py`). */
const REFERENCE_COLORS_PATH =
  "validation/terrain-realism-20261001/T8a/water-reference-colors.json";

interface WaterReferenceColors {
  crops: { referenceId: string }[];
  presets: Record<string, { hexSrgb: string }>;
}

const referenceColors = JSON.parse(readFileSync(REFERENCE_COLORS_PATH, "utf8")) as WaterReferenceColors;

/** The `water-*` preset IDs of the T3 rule set, in declaration order. */
const RULE_WATER_PRESET_IDS = CITY_GROUND_MATERIAL_RULES_V1.presets
  .map(preset => preset.id)
  .filter(id => id.startsWith("water-"));

const LOOK_IDS = Object.keys(WATER_PRESET_LOOKS);

describe("WATER_PRESET_LOOKS coverage and values", () => {
  it("covers exactly the water-* presets of the T3 rule set", () => {
    expect(LOOK_IDS.sort()).toEqual([...RULE_WATER_PRESET_IDS].sort());
    // Every rule-set water preset really belongs to the water family.
    for (const presetId of RULE_WATER_PRESET_IDS) {
      expect(CITY_GROUND_MATERIAL_RULES_V1.presets.find(preset => preset.id === presetId)?.family)
        .toBe("water");
    }
  });

  it("copies every colour from water-reference-colors.json", () => {
    for (const presetId of LOOK_IDS) {
      const expected = Number(referenceColors.presets[presetId]?.hexSrgb);
      expect(expected).toBeTypeOf("number");
      expect(WATER_PRESET_LOOKS[presetId]?.color).toBe(expected);
    }
  });

  it("names the JSON and the reference photographs as the colour basis", () => {
    const cropIds = referenceColors.crops.map((crop: { referenceId: string }) => crop.referenceId);
    for (const presetId of LOOK_IDS) {
      const look = WATER_PRESET_LOOKS[presetId];
      expect(look?.colorBasis).toContain("water-reference-colors.json");
      expect(look?.referenceIds.length).toBeGreaterThan(0);
      // Every named reference exists in the measurement JSON.
      for (const referenceId of look?.referenceIds ?? []) {
        expect(cropIds).toContain(referenceId);
      }
    }
  });

  it("keeps every preset off the replaced saturated-blue style", () => {
    for (const entry of waterPresetSaturationReport()) {
      expect(entry.hslSaturation, `${entry.presetId} saturation`).toBeLessThanOrEqual(0.35);
      const blue = entry.hueDeg >= 190 && entry.hueDeg <= 250;
      if (blue) {
        expect(entry.hslSaturation, `${entry.presetId} blue hue ${entry.hueDeg}`).toBeLessThanOrEqual(0.2);
      }
    }
  });
});

describe("WATER_TRANSPARENCY_POLICY", () => {
  it("states the opaque policy and freezes it in the looks", () => {
    expect(WATER_TRANSPARENCY_POLICY)
      .toBe("opaque: no bathymetry or bed geometry in any scene; transparency would expose undefined ground");
    expect(Object.isFrozen(WATER_PRESET_LOOKS)).toBe(true);
    for (const presetId of LOOK_IDS) expect(Object.isFrozen(WATER_PRESET_LOOKS[presetId])).toBe(true);
  });
});

describe("createWaterMaterials", () => {
  it("builds one opaque physical material per preset with the stated properties", () => {
    const materials = createWaterMaterials();
    try {
      const expectedRoughness: Record<string, number> = {
        "water-river": 0.16, "water-dock": 0.16,
        "water-canal": 0.06, "water-basin": 0.06,
        "water-pond": 0.05,
        "water-lake": 0.1, "water-reservoir": 0.1, "water-generic": 0.1,
      };
      for (const presetId of LOOK_IDS) {
        const look = WATER_PRESET_LOOKS[presetId]!;
        const material = materials.material(presetId);
        expect(material).toBeInstanceOf(THREE.MeshPhysicalMaterial);
        expect(material.name).toBe(`water:${presetId}`);
        expect(material.color.getHex(THREE.SRGBColorSpace)).toBe(look.color);
        expect(material.roughness).toBe(expectedRoughness[presetId]);
        expect(material.envMapIntensity).toBe(look.envMapIntensity);
        expect(material.transparent).toBe(false);
        expect(material.opacity).toBe(1);
        expect(material.transmission).toBe(0);
        expect(material.ior).toBe(1.333);
        expect(material.metalness).toBe(0);
        expect(material.specularIntensity).toBe(1);
        expect(material.clearcoat).toBe(0);
        expect(material.side).toBe(THREE.FrontSide);
        expect(material.depthWrite).toBe(true);
      }
    } finally {
      materials.dispose();
    }
  });

  it("returns one shared instance per preset across calls", () => {
    const first = createWaterMaterials();
    const second = createWaterMaterials();
    try {
      for (const presetId of LOOK_IDS) {
        expect(first.material(presetId)).toBe(first.material(presetId));
        expect(first.material(presetId)).not.toBe(second.material(presetId));
      }
    } finally {
      first.dispose();
      second.dispose();
    }
  });

  it("throws for non-water and unknown presets", () => {
    const materials = createWaterMaterials();
    try {
      expect(() => materials.material("unclassified")).toThrow(/unknown or non-water preset/);
      expect(() => materials.material("lawn-maintained")).toThrow(/unknown or non-water preset/);
      expect(() => materials.material("water-unknown-type")).toThrow(/unknown or non-water preset/);
      expect(() => materials.material("")).toThrow(/unknown or non-water preset/);
    } finally {
      materials.dispose();
    }
  });

  it("leaves every material unpatched for T8b", () => {
    const materials = createWaterMaterials();
    try {
      for (const presetId of LOOK_IDS) {
        const material = materials.material(presetId);
        expect(material.onBeforeCompile).toBe(THREE.Material.prototype.onBeforeCompile);
      }
    } finally {
      materials.dispose();
    }
  });

  it("dispose disposes each material exactly once", () => {
    const materials = createWaterMaterials();
    const listeners = new Map<string, ReturnType<typeof vi.fn>>();
    // `Material.dispose()` dispatches its `dispose` event exactly once.
    for (const presetId of LOOK_IDS) {
      const listener = vi.fn();
      materials.material(presetId).addEventListener("dispose", listener);
      listeners.set(presetId, listener);
    }
    materials.dispose();
    for (const [presetId, listener] of listeners) {
      expect(listener, `${presetId} dispose event`).toHaveBeenCalledTimes(1);
    }
  });
});

describe("waterPresetSaturationReport", () => {
  it("reports every preset with finite sRGB-space HSL values", () => {
    const report = waterPresetSaturationReport();
    expect(report.map(entry => entry.presetId).sort()).toEqual([...LOOK_IDS].sort());
    for (const entry of report) {
      expect(entry.hslSaturation).toBeGreaterThanOrEqual(0);
      expect(entry.hslSaturation).toBeLessThanOrEqual(1);
      expect(entry.hslLightness).toBeGreaterThanOrEqual(0);
      expect(entry.hslLightness).toBeLessThanOrEqual(1);
      expect(entry.hueDeg).toBeGreaterThanOrEqual(0);
      expect(entry.hueDeg).toBeLessThan(360);
      expect(Number.isFinite(entry.hslSaturation + entry.hslLightness + entry.hueDeg)).toBe(true);
    }
  });
});
