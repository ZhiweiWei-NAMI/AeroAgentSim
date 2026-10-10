// @vitest-environment node
import { readFileSync } from "node:fs";
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { CITY_FACADE_PANE_PROFILE_KEY, readCityFacadePaneProfile, type CityFacadePaneProfile } from "./city-facade-pane-profile";
import {
  CITY_WINDOW_LIT_FRACTION, cityFacadeWindowGroups, installCityWindowLights, rasterCityFacadeWindowIds,
} from "./city-facade-window-lights";

const ROOT = new URL("../public/building-renders/shanghai-huangpu-east-v1/", import.meta.url);

function realProfiles(): CityFacadePaneProfile[] {
  const manifest = JSON.parse(readFileSync(new URL("manifest.json", ROOT), "utf8")) as
    { buildings: { derived_glb: { path: string } }[] };
  const profiles = new Map<string, CityFacadePaneProfile>();
  for (const entry of manifest.buildings) {
    const glb = readFileSync(new URL(entry.derived_glb.path, ROOT));
    const document = JSON.parse(glb.subarray(20, 20 + glb.readUInt32LE(12)).toString("utf8")) as
      { materials?: { extras?: Record<string, unknown> }[] };
    for (const material of document.materials ?? []) {
      const raw = material.extras?.[CITY_FACADE_PANE_PROFILE_KEY];
      if (raw === undefined) continue;
      const profile = readCityFacadePaneProfile(raw);
      profiles.set(profile.style_id, profile);
    }
  }
  return [...profiles.values()];
}

function synthetic(rectangles: [number, number, number, number][],
  opaque: [number, number][][] = []): CityFacadePaneProfile {
  return readCityFacadePaneProfile({
    schema_version: "aero-bench.authored-facade-pane-profile/v1", provenance: "source-texture-art",
    style_id: "synthetic", material_kind: "masonry-and-windows", coordinate_system: "image-top-left-pixel",
    tile_size_px: [64, 64], source_crop_px: [0, 0, 64, 64],
    glass_rectangles_px: rectangles, opaque_polygons_px: opaque,
  });
}

describe("authored facade window grouping", () => {
  it("merges a transom with its sash but keeps neighbouring windows apart", () => {
    // Two columns; each window is a 12 px sash with a 4 px-separated transom above it.
    const groups = cityFacadeWindowGroups(synthetic([
      [4, 4, 20, 10], [4, 14, 20, 26], [40, 4, 56, 10], [40, 14, 56, 26], [4, 40, 20, 52],
    ]));
    expect(groups[0]).toBe(groups[1]);
    expect(groups[2]).toBe(groups[3]);
    expect(new Set([groups[0], groups[2], groups[4]]).size).toBe(3);
  });

  it("rasters ids only on glass pixels and never inside authored opaque polygons", () => {
    const profile = synthetic([[4, 4, 28, 28]], [[[10, 10], [20, 10], [20, 20], [10, 20]]]);
    const ids = rasterCityFacadeWindowIds(profile);
    const at = (x: number, y: number): number => ids[(y * 64 + x) * 4]!;
    expect(at(5, 5)).toBe(1);
    expect(at(15, 15)).toBe(0);
    expect(at(40, 40)).toBe(0);
    for (let offset = 3; offset < ids.length; offset += 4) expect(ids[offset]).toBe(255);
  });

  it("covers every real authored style with at least one window and at most 254 per tile", () => {
    const profiles = realProfiles();
    expect(profiles.length).toBeGreaterThan(0);
    for (const profile of profiles) {
      const groups = cityFacadeWindowGroups(profile);
      expect(groups).toHaveLength(profile.glass_rectangles_px.length);
      expect(Math.max(...groups)).toBeLessThan(254);
      const ids = rasterCityFacadeWindowIds(profile);
      let lit = 0;
      for (let offset = 0; offset < ids.length; offset += 4) if (ids[offset]! > 0) lit++;
      expect(lit).toBeGreaterThan(0);
    }
  });

  it("keeps daytime windows dark and lights a minority at night", () => {
    expect(CITY_WINDOW_LIT_FRACTION.day).toBe(0);
    expect(CITY_WINDOW_LIT_FRACTION.night).toBeGreaterThan(0);
    expect(CITY_WINDOW_LIT_FRACTION.night).toBeLessThan(CITY_WINDOW_LIT_FRACTION.twilight);
  });

  it("installs once, requires the Illum map and survives a clone that copies the compile hook", () => {
    const profile = synthetic([[4, 4, 28, 28]]);
    const bare = new THREE.MeshStandardMaterial();
    expect(() => installCityWindowLights(bare, profile)).toThrow(/albedo and Illum/);
    const material = new THREE.MeshStandardMaterial({ map: new THREE.Texture(), emissiveMap: new THREE.Texture() });
    installCityWindowLights(material, profile);
    const hook = material.onBeforeCompile;
    installCityWindowLights(material, profile);
    expect(material.onBeforeCompile).toBe(hook);
    const shader = { uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: THREE.ShaderLib.standard.vertexShader, fragmentShader: THREE.ShaderLib.standard.fragmentShader };
    hook.call(material, shader as unknown as THREE.WebGLProgramParametersWithUniforms, {} as THREE.WebGLRenderer);
    expect(shader.fragmentShader).toContain("cityWindowLitFraction");
    expect(shader.vertexShader).toContain("vCityBuildingSeed = mod(floor(modelMatrix[3].xz), 251.0)");
    expect(shader.fragmentShader).toContain("floor(vEmissiveMapUv) + floor(vCityBuildingSeed + 0.5)");
    expect(shader.uniforms.cityWindowIds!.value).toBeInstanceOf(THREE.DataTexture);
  });
});
