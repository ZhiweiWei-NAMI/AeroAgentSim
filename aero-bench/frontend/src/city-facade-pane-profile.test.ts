// @vitest-environment node
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildCityFacadeRoughness, collectCityCalibratedFacadeMaterials, setCityBuildingCalibration } from "./city-presentation";
import { cityVisualSolar, sampleCityLighting } from "./city-lighting-calibration";
import { cityVisualWeather } from "./city-weather-calibration";
import { CITY_FACADE_PANE_PROFILE_KEY, rasterCityFacadePaneRoughness,
  readCityFacadePaneProfile, type CityFacadePaneProfile } from "./city-facade-pane-profile";

function paneProfile(): CityFacadePaneProfile {
  return readCityFacadePaneProfile({ schema_version: "aero-bench.authored-facade-pane-profile/v1",
    provenance: "source-texture-art", style_id: "independent-regional-panes", material_kind: "glass-and-masonry",
    coordinate_system: "image-top-left-pixel", tile_size_px: [6, 3], source_crop_px: [10, 20, 16, 23],
    glass_rectangles_px: [[1, 0, 5, 3]], opaque_polygons_px: [[[3, 0], [4, 0], [4, 3], [3, 3]]] });
}

function fixture(profile = paneProfile()): { root: THREE.Group; material: THREE.MeshStandardMaterial } {
  const [width, height] = profile.tile_size_px;
  const material = new THREE.MeshStandardMaterial();
  material.name = "Independent regional source facade";
  material.userData[CITY_FACADE_PANE_PROFILE_KEY] = profile;
  for (const slot of ["map", "normalMap", "emissiveMap"] as const) {
    const pixels = new Uint8ClampedArray(width * height * 4).fill(slot === "emissiveMap" ? 0 : 225);
    for (let i = 3; i < pixels.length; i += 4) pixels[i] = 255;
    const texture = new THREE.Texture({ width, height, pixels });
    texture.flipY = false; texture.wrapS = THREE.RepeatWrapping; texture.wrapT = THREE.RepeatWrapping;
    material[slot] = texture;
  }
  const root = new THREE.Group();
  root.userData.renderMaterials = [material];
  root.add(new THREE.Mesh(new THREE.BufferGeometry(), material));
  return { root, material };
}

beforeEach(() => {
  vi.stubGlobal("document", { createElement: () => {
    let data: Uint8ClampedArray | null = null;
    return { width: 0, height: 0, getContext: () => ({
      drawImage: (image: { pixels: Uint8ClampedArray }) => { data = image.pixels; },
      clearRect: () => { data = null; }, getImageData: () => ({ data }),
    }) };
  } });
});
afterEach(() => vi.unstubAllGlobals());

describe("source-authored facade pane material", () => {
  it("keeps every bright unlit pane smooth and frames/masonry rough, independent of Illum intensity", () => {
    const profile = paneProfile(), albedo = new Uint8ClampedArray(6 * 3 * 4).fill(225);
    const unlit = new Uint8ClampedArray(albedo.length), lit = new Uint8ClampedArray(albedo.length).fill(255);
    const before = albedo.slice(), darkMask = buildCityFacadeRoughness(albedo, unlit, profile);
    expect(buildCityFacadeRoughness(albedo, lit, profile)).toEqual(darkMask);
    for (let y = 0; y < 3; y++) {
      for (const x of [1, 2, 4]) expect(darkMask[(y * 6 + x) * 4 + 1]).toBe(48);
      for (const x of [0, 3, 5]) expect(darkMask[(y * 6 + x) * 4 + 1]).toBe(224);
    }
    expect(albedo).toEqual(before);
  });

  it("applies profile semantics to independent source names and probe clones, sharing one texture mask", () => {
    const { root, material } = fixture(), environment = new THREE.Texture();
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("clear") });
    const clone = material.clone(); clone.name = "A different source name with local reflection";
    (root.children[0] as THREE.Mesh).material = clone;
    expect(setCityBuildingCalibration(root, sample, environment)).toEqual({ facadeMaterials: 2, roofMaterials: 0 });
    expect(material.roughnessMap).toBe(clone.roughnessMap);
    expect(material.metalness).toBe(0); expect(clone.metalness).toBe(0);
    expect(material.emissiveIntensity).toBe(0);
    const mask = material.roughnessMap as THREE.DataTexture;
    expect(mask.image.width).toBe(6); expect(mask.image.height).toBe(3);
    expect(mask.flipY).toBe(false); expect(mask.channel).toBe(0); expect(mask.wrapS).toBe(THREE.RepeatWrapping);
    expect(collectCityCalibratedFacadeMaterials(root)).toEqual([material, clone]);
    setCityBuildingCalibration(root, sample, environment);
    expect(material.roughnessMap).toBe(mask);
  });

  it("reuses matching pane semantics across different style identities, and rejects conflicting semantics", () => {
    const first = fixture(), second = fixture();
    second.material.map = first.material.map; second.material.normalMap = first.material.normalMap;
    second.material.emissiveMap = first.material.emissiveMap;
    second.material.userData[CITY_FACADE_PANE_PROFILE_KEY] = { ...paneProfile(), style_id: "another-dataset-style" };
    const environment = new THREE.Texture();
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("clear") });
    setCityBuildingCalibration(first.root, sample, environment);
    setCityBuildingCalibration(second.root, sample, environment);
    expect(first.material.roughnessMap).toBe(second.material.roughnessMap);
    second.material.userData[CITY_FACADE_PANE_PROFILE_KEY] = { ...paneProfile(), opaque_polygons_px: [] };
    expect(() => setCityBuildingCalibration(second.root, sample, environment)).toThrow(/conflicting authored pane profiles/);
  });

  it("exposes missing source profiles and unavailable PBR fields rather than guessing old material names", () => {
    const { root, material } = fixture();
    const environment = new THREE.Texture();
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("clear") });
    delete material.userData[CITY_FACADE_PANE_PROFILE_KEY];
    material.name = "Buildings Modern.mat.facade";
    expect(() => setCityBuildingCalibration(root, sample, environment)).toThrow(/missing its authored presentation profile/);
    material.userData[CITY_FACADE_PANE_PROFILE_KEY] = paneProfile(); material.normalMap = null;
    expect(() => setCityBuildingCalibration(root, sample, environment)).toThrow(/missing albedo, normal or Illum/);
    const basic = new THREE.MeshBasicMaterial(); basic.userData[CITY_FACADE_PANE_PROFILE_KEY] = paneProfile();
    root.userData.renderMaterials = [basic]; (root.children[0] as THREE.Mesh).material = basic;
    expect(() => setCityBuildingCalibration(root, sample, environment)).toThrow(/requires source PBR/);
  });

  it("rejects dimensions, channel transforms and sampler drift even after a mask has been cached", () => {
    const { root, material } = fixture(), environment = new THREE.Texture();
    const sample = sampleCityLighting({ solar: cityVisualSolar("day"), weather: cityVisualWeather("clear") });
    setCityBuildingCalibration(root, sample, environment);
    material.normalMap!.offset.x = 0.1;
    expect(() => setCityBuildingCalibration(root, sample, environment)).toThrow(/share TEXCOORD0/);
    material.normalMap!.offset.x = 0; material.normalMap!.wrapT = THREE.ClampToEdgeWrapping;
    expect(() => setCityBuildingCalibration(root, sample, environment)).toThrow(/share TEXCOORD0/);
    material.normalMap!.wrapT = THREE.RepeatWrapping;
    (material.normalMap!.image as { width: number }).width = 5;
    expect(() => setCityBuildingCalibration(root, sample, environment)).toThrow(/dimensions do not match/);
  });

  it("validates authored pane bounds/provenance and rasterizes opaque diagonal structures at pixel centres", () => {
    expect(() => readCityFacadePaneProfile({ ...paneProfile(), provenance: "surveyed-truth" })).toThrow(/identity/);
    expect(() => readCityFacadePaneProfile({ ...paneProfile(), source_crop_px: [0, 0, 7, 3] })).toThrow(/tile size/);
    expect(() => readCityFacadePaneProfile({ ...paneProfile(), glass_rectangles_px: [[0, 0, 7, 3]] })).toThrow(/outside/);
    expect(() => readCityFacadePaneProfile({ ...paneProfile(), opaque_polygons_px: [[[0, 0], [1, 1], [2, 2]]] }))
      .toThrow(/zero area/);
    const diagonal = readCityFacadePaneProfile({ ...paneProfile(), glass_rectangles_px: [[0, 0, 6, 3]],
      opaque_polygons_px: [[[0, 0], [1, 0], [6, 3], [5, 3]]] });
    const data = rasterCityFacadePaneRoughness(diagonal);
    expect(data[(1 * 6 + 2) * 4 + 1]).toBe(224);
    expect(data[(0 * 6 + 4) * 4 + 1]).toBe(48);
  });
});
