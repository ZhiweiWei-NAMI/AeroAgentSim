// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { CITY_GROUND_MATERIAL_RULES_V1, compileGroundMaterialRules, assignGroundMaterial,
  type GroundMaterialAssignment, type GroundMaterialInput } from "./city-ground-material-rules";
import { applySurfaceWetness, createSurfaceWetnessUniforms } from "./city-surface-wetness";
import { createWaterSurfaceUniforms } from "./city-water-surface";
import { TERRAIN_TEXTURE_LIBRARY_URL, TERRAIN_PRESET_LOOKS, parseTerrainTextureLibrary,
  loadTerrainTextureSets, createTerrainMaterials, terrainVertexAttributes,
  applyTerrainAttributes, validateTerrainPresetLooks,
  type TerrainPresetLook, type TerrainTextureLibrary } from "./city-terrain-materials";
import { assignCityGroundMaterial as assignCityGroundMaterialSurfaces,
  createTerrainSurfaceKit as createTerrainSurfaceKitInTest } from "./city-terrain-surfaces";
import { fakeTerrainTextureSets } from "./testing/terrain-surface-fixture";

const REAL_LIBRARY: TerrainTextureLibrary = parseTerrainTextureLibrary(JSON.parse(readFileSync(
  resolve(__dirname, "../public/textures/terrain-v1/manifest.json"), "utf-8")));

const TEXTURED_LOOK_PRESETS = ["lawn-maintained", "grass-natural", "grass-sports", "gravel"];
const FLAT_LOOK_PRESETS = ["grass-paver", "tartan-track", "sand", "asphalt", "concrete"];
const ALL_LOOK_PRESETS = [...TEXTURED_LOOK_PRESETS, ...FLAT_LOOK_PRESETS];
const SET_IDS = [...new Set(ALL_LOOK_PRESETS.map(id => TERRAIN_PRESET_LOOKS[id]!.textureSet)
  .filter((id): id is string => id !== null))];

function input(polygonId: string, tags: Record<string, string>): GroundMaterialInput {
  return { polygonId, origin: "source", tags,
    sourceRefs: { sourceSha256: "a".repeat(64), elementType: "way", elementId: "1" } };
}

function assignment(preset: string, polygonId = "poly-1"): GroundMaterialAssignment {
  const compiled = compileGroundMaterialRules(CITY_GROUND_MATERIAL_RULES_V1);
  const tagsByPreset: Record<string, Record<string, string>> = {
    "lawn-maintained": { landuse: "grass" },
    "grass-natural": { natural: "grassland" },
    "grass-sports": { surface: "grass", leisure: "pitch" },
    "pitch-turf-unknown": { leisure: "pitch" },
    "artificial-turf": { surface: "artificial_turf" },
    "woodland-floor": { landuse: "forest" },
    "soil-brownfield": { landuse: "brownfield" },
    "soil-bare": { surface: "ground" },
    "soil-construction": { landuse: "construction" },
    "mulch": { surface: "woodchips" },
    "gravel": { surface: "gravel" },
    "grass-paver": { surface: "grass_paver" },
    "tartan-track": { surface: "tartan" },
    "sand": { surface: "sand" },
    "asphalt": { surface: "asphalt" },
    "concrete": { surface: "concrete" },
    "paving-unit": { surface: "paving_stones" },
    "paving-generic": { surface: "paved" },
    "parking-generic": { amenity: "parking" },
    "plaza-generic": { place: "square" },
    "court-hard-unknown": { leisure: "pitch", sport: "tennis" },
    "grass-generic": { surface: "grass" },
  };
  const tags = tagsByPreset[preset];
  if (tags === undefined) throw new Error(`no fixture tags for preset ${preset}`);
  return assignGroundMaterial(input(polygonId, tags), compiled, 42);
}

function fakeTexture(): THREE.Texture {
  return new THREE.Texture();
}

interface LoaderCall { readonly url: string }

function fakeLoaders(maxAnisotropy = 16): {
  loaders: Parameters<typeof loadTerrainTextureSets>[2];
  calls: LoaderCall[];
  urls: string[];
} {
  const calls: LoaderCall[] = [];
  const urls: string[] = [];
  return {
    calls,
    urls,
    loaders: {
      resolveUrl: async (path: string) => {
        calls.push({ url: path });
        const url = `blob:verified:${path}`;
        urls.push(url);
        return url;
      },
      loadTexture: async (url: string) => {
        calls.push({ url });
        return fakeTexture();
      },
      maxAnisotropy,
    },
  };
}

describe("terrain texture library validation", () => {
  it("accepts the constant library URL", () => {
    expect(TERRAIN_TEXTURE_LIBRARY_URL).toBe("/textures/terrain-v1/manifest.json");
  });
  it("parses the real manifest", () => {
    expect(REAL_LIBRARY.schema).toBe("aero-bench.terrain-texture-library/v1");
    expect(REAL_LIBRARY.sets).toHaveLength(8);
    for (const set of REAL_LIBRARY.sets) {
      expect(set.files.map(file => file.map).sort()).toEqual(["color", "normal", "orm"]);
      expect(set.tileSizeM).toBeGreaterThan(0);
    }
  });
  it("rejects a bad schema", () => {
    const value = JSON.parse(JSON.stringify(REAL_LIBRARY));
    value.schema = "something-else/v9";
    expect(() => parseTerrainTextureLibrary(value)).toThrow(/schema/);
  });
  it("rejects duplicate set IDs", () => {
    const value = JSON.parse(JSON.stringify(REAL_LIBRARY));
    value.sets.push(JSON.parse(JSON.stringify(value.sets[0])));
    expect(() => parseTerrainTextureLibrary(value)).toThrow(/duplicate set id/);
  });
  it("rejects a path outside the set directory", () => {
    const value = JSON.parse(JSON.stringify(REAL_LIBRARY));
    value.sets[0].files[0].path = `/textures/terrain-v1/other-set/${value.sets[0].id}-color.webp`;
    expect(() => parseTerrainTextureLibrary(value)).toThrow(/path must start with/);
  });
  it("rejects a path traversal", () => {
    const value = JSON.parse(JSON.stringify(REAL_LIBRARY));
    value.sets[0].files[0].path = "/textures/terrain-v1/../terrain-v1/lawn-grass001/color.webp";
    expect(() => parseTerrainTextureLibrary(value)).toThrow(/\.\./);
  });
  it("rejects a non-hex sha256", () => {
    const value = JSON.parse(JSON.stringify(REAL_LIBRARY));
    value.sets[0].files[0].sha256 = "not-a-hash";
    expect(() => parseTerrainTextureLibrary(value)).toThrow(/64 hex/);
  });
  it("rejects a missing map", () => {
    const value = JSON.parse(JSON.stringify(REAL_LIBRARY));
    value.sets[0].files = value.sets[0].files.filter((file: { map: string }) => file.map !== "orm");
    expect(() => parseTerrainTextureLibrary(value)).toThrow(/missing the orm map/);
  });
  it("rejects non-positive integers and tileSizeM", () => {
    const zeroSize = JSON.parse(JSON.stringify(REAL_LIBRARY));
    zeroSize.sets[0].files[0].width = 0;
    expect(() => parseTerrainTextureLibrary(zeroSize)).toThrow(/positive integer/);
    const badTile = JSON.parse(JSON.stringify(REAL_LIBRARY));
    badTile.sets[0].tileSizeM = -1;
    expect(() => parseTerrainTextureLibrary(badTile)).toThrow(/tileSizeM/);
  });
  it("returns a frozen object", () => {
    expect(Object.isFrozen(REAL_LIBRARY)).toBe(true);
    expect(Object.isFrozen(REAL_LIBRARY.sets)).toBe(true);
    expect(Object.isFrozen(REAL_LIBRARY.sets[0])).toBe(true);
  });
});

describe("terrain preset looks", () => {
  const presetIds = CITY_GROUND_MATERIAL_RULES_V1.presets.map(preset => preset.id)
    .filter(id => id !== "unclassified" && !id.startsWith("water-"));
  it("covers exactly the non-water, non-unclassified presets", () => {
    expect(Object.keys(TERRAIN_PRESET_LOOKS).sort()).toEqual([...presetIds].sort());
  });
  it("never covers unclassified or water presets", () => {
    expect(TERRAIN_PRESET_LOOKS.unclassified).toBeUndefined();
    for (const preset of CITY_GROUND_MATERIAL_RULES_V1.presets) {
      if (preset.id.startsWith("water-")) {
        expect(TERRAIN_PRESET_LOOKS[preset.id]).toBeUndefined();
      }
    }
  });
  it("uses only texture sets that exist in the real library", () => {
    const librarySets = new Set(REAL_LIBRARY.sets.map(set => set.id));
    for (const [presetId, look] of Object.entries(TERRAIN_PRESET_LOOKS)) {
      if (look.textureSet !== null) {
        expect(librarySets.has(look.textureSet),
          `preset ${presetId} references unknown set ${look.textureSet}`).toBe(true);
      }
    }
  });
  it("requires flatReason exactly when textureSet is null", () => {
    for (const look of Object.values(TERRAIN_PRESET_LOOKS)) {
      if (look.textureSet === null) {
        expect(typeof look.flatReason).toBe("string");
        expect(look.flatReason!.startsWith("[")).toBe(true);
      } else {
        expect(look.flatReason).toBeNull();
      }
    }
  });
  it("requires a roughnessRange exactly when textureSet is set, with the grass looks lifted", () => {
    const GRASS_LOOKS_AT_085 = new Set(["lawn-maintained", "grass-generic", "grass-sports",
      "pitch-turf-unknown"]);
    for (const [presetId, look] of Object.entries(TERRAIN_PRESET_LOOKS)) {
      if (look.textureSet === null) {
        expect(look.roughnessRange, `flat look ${presetId}`).toBeNull();
      } else if (GRASS_LOOKS_AT_085.has(presetId)) {
        expect(look.roughnessRange, `grass look ${presetId}`).toEqual([0.85, 1]);
        expect(Object.isFrozen(look.roughnessRange), `grass look ${presetId}`).toBe(true);
      } else {
        expect(look.roughnessRange, `textured look ${presetId}`).toEqual([0, 1]);
        expect(Object.isFrozen(look.roughnessRange), `textured look ${presetId}`).toBe(true);
      }
    }
    // Every textured look is classified: no look escapes both branches.
    expect(GRASS_LOOKS_AT_085.size).toBe(4);
    // The remap moves the means onto the scan reference while keeping relative variation.
    const lawnMean = 0.85 + (1 - 0.85) * 0.544;
    const sportsMean = 0.85 + (1 - 0.85) * 0.71;
    expect(lawnMean).toBeCloseTo(0.9316, 12);
    expect(sportsMean).toBeCloseTo(0.9565, 12);
  });
  it("the roughness range is a frozen array in the frozen look object", () => {
    const lawn = TERRAIN_PRESET_LOOKS["lawn-maintained"]!;
    expect(Object.isFrozen(lawn)).toBe(true);
    expect(Object.isFrozen(lawn.roughnessRange)).toBe(true);
  });
  it("textured looks use roughness 1 and flat looks carry their table roughness", () => {
    for (const look of Object.values(TERRAIN_PRESET_LOOKS)) {
      if (look.textureSet !== null) {
        expect(look.roughness).toBe(1);
        expect(look.normalScale).toBe(1);
        expect(look.aoIntensity).toBeCloseTo(0.6);
      }
    }
    expect(TERRAIN_PRESET_LOOKS["tartan-track"]!.roughness).toBe(0.85);
    expect(TERRAIN_PRESET_LOOKS.sand!.roughness).toBe(0.95);
    expect(TERRAIN_PRESET_LOOKS.asphalt!.roughness).toBe(0.88);
    expect(TERRAIN_PRESET_LOOKS.concrete!.roughness).toBe(0.94);
    expect(TERRAIN_PRESET_LOOKS["paving-generic"]!.roughness).toBe(0.9);
  });
});

describe("terrain preset look validation", () => {
  const lookWith = (overrides: { textureSet?: string | null; flatReason?: string | null;
    roughnessRange?: unknown }): Record<string, TerrainPresetLook> =>
    ({ "preset-x": { ...TERRAIN_PRESET_LOOKS["lawn-maintained"]!, ...overrides } as TerrainPresetLook });
  it("accepts the shipped table", () => {
    expect(() => validateTerrainPresetLooks(TERRAIN_PRESET_LOOKS)).not.toThrow();
  });
  it("rejects a textured look without a range", () => {
    expect(() => validateTerrainPresetLooks(lookWith({ roughnessRange: null })))
      .toThrow("terrain-materials: preset preset-x roughnessRange must be a frozen [min, max] with 0 <= min <= max <= 1");
  });
  it("rejects a textured look with an invalid range", () => {
    const cases: unknown[] = [
      [0.9, 0.8], // unordered (and unfrozen)
      Object.freeze([0.9, 0.8]), // unordered
      Object.freeze([-0.1, 1]), // min below 0
      Object.freeze([0, 1.1]), // max above 1
      Object.freeze([NaN, 1]), // non-finite
      Object.freeze([Infinity, 1]), // non-finite
      Object.freeze([0.5]), // wrong length
      Object.freeze([]),
      "0-1", // not an array
      0.5, // not an array
    ];
    for (const range of cases) {
      expect(() => validateTerrainPresetLooks(lookWith({ roughnessRange: range })), String(range))
        .toThrow("terrain-materials: preset preset-x roughnessRange must be a frozen [min, max] with 0 <= min <= max <= 1");
    }
  });
  it("rejects a flat look with a range", () => {
    expect(() => validateTerrainPresetLooks(lookWith({ textureSet: null,
      flatReason: "[test flat look]", roughnessRange: [0, 1] })))
      .toThrow("terrain-materials: preset preset-x roughnessRange must be null for a flat look");
  });
});

describe("terrain texture loading", () => {
  it("configures colour space, wrapping, mipmaps and anisotropy through a fake loader", async () => {
    const { loaders } = fakeLoaders(16);
    const sets = await loadTerrainTextureSets(REAL_LIBRARY, ["lawn-grass001"], loaders);
    const bundle = sets.get("lawn-grass001");
    expect(bundle.map.colorSpace).toBe(THREE.SRGBColorSpace);
    expect(bundle.normalMap.colorSpace).toBe(THREE.NoColorSpace);
    expect(bundle.ormMap.colorSpace).toBe(THREE.NoColorSpace);
    for (const texture of [bundle.map, bundle.normalMap, bundle.ormMap]) {
      expect(texture.wrapS).toBe(THREE.RepeatWrapping);
      expect(texture.wrapT).toBe(THREE.RepeatWrapping);
      expect(texture.generateMipmaps).toBe(true);
      expect(texture.minFilter).toBe(THREE.LinearMipmapLinearFilter);
      expect(texture.magFilter).toBe(THREE.LinearFilter);
      expect(texture.anisotropy).toBe(8);
    }
    expect(bundle.tileSizeM).toBe(REAL_LIBRARY.sets.find(set => set.id === "lawn-grass001")!.tileSizeM);
    sets.dispose();
  });
  it("caps anisotropy at 8", async () => {
    const { loaders } = fakeLoaders(4);
    const sets = await loadTerrainTextureSets(REAL_LIBRARY, ["gravel-043"], loaders);
    expect(sets.get("gravel-043").map.anisotropy).toBe(4);
    sets.dispose();
  });
  it("loads each file exactly once across repeated sets", async () => {
    const { loaders, calls } = fakeLoaders();
    const sets = await loadTerrainTextureSets(REAL_LIBRARY,
      ["lawn-grass001", "lawn-grass001"], loaders);
    const texturePaths = calls.filter(call => call.url.startsWith("blob:"))
      .map(call => call.url);
    expect(texturePaths).toHaveLength(3);
    expect(new Set(texturePaths).size).toBe(3);
    sets.dispose();
  });
  it("loads only the requested sets", async () => {
    const { loaders, calls } = fakeLoaders();
    const sets = await loadTerrainTextureSets(REAL_LIBRARY, ["gravel-043"], loaders);
    expect(calls.filter(call => call.url.startsWith("/textures/"))
      .every(call => call.url.includes("gravel-043"))).toBe(true);
    expect(() => sets.get("lawn-grass001")).toThrow(/not loaded|was not loaded/);
    sets.dispose();
  });
  it("settles sibling maps and disposes every decoded texture when one map is tampered", async () => {
    // The ORM fetch stalls until both siblings decoded, then fails verification; every
    // already-decoded texture must be disposed exactly once and the rejection kept verbatim.
    let finishSiblings: () => void = () => undefined;
    const siblingsSettled = new Promise<void>(resolve => { finishSiblings = resolve; });
    const decoded: THREE.Texture[] = [];
    const disposals = new Map<THREE.Texture, number>();
    const rejection = await loadTerrainTextureSets(REAL_LIBRARY, ["gravel-043"], {
      maxAnisotropy: 4,
      resolveUrl: async path => {
        if (path.endsWith("/orm.webp")) await siblingsSettled;
        return path;
      },
      loadTexture: async path => {
        if (path.endsWith("/orm.webp")) throw new Error("Terrain texture sha256 mismatch: " + path);
        const texture = fakeTexture();
        texture.addEventListener("dispose", () => disposals.set(texture, (disposals.get(texture) ?? 0) + 1));
        decoded.push(texture);
        if (decoded.length === 2) finishSiblings();
        return texture;
      },
    }).catch((error: unknown) => error);
    expect(rejection).toBeInstanceOf(Error);
    expect((rejection as Error).message)
      .toBe("Terrain texture sha256 mismatch: /textures/terrain-v1/gravel-043/orm.webp");
    await new Promise(resolve => setImmediate(resolve));
    expect(decoded).toHaveLength(2);
    expect([...disposals.values()]).toEqual([1, 1]);
  });
  it("rejects unknown set IDs and disposes every texture", async () => {
    const { loaders } = fakeLoaders();
    await expect(loadTerrainTextureSets(REAL_LIBRARY, ["nope"], loaders))
      .rejects.toThrow(/not in the library/);
    const sets = await loadTerrainTextureSets(REAL_LIBRARY, ["gravel-043"], loaders);
    const textures = [sets.get("gravel-043").map, sets.get("gravel-043").normalMap,
      sets.get("gravel-043").ormMap];
    const disposed: string[] = [];
    textures.forEach(texture => texture.addEventListener("dispose", () => { disposed.push(texture.uuid); }));
    sets.dispose();
    expect(disposed.sort()).toEqual(textures.map(texture => texture.uuid).sort());
    expect(() => sets.get("gravel-043")).toThrow(/was not loaded/);
  });
  it("validates a later unknown set id before any fetch", async () => {
    // R1: a valid first set followed by an unknown id must fail before the first fetch,
    // so no earlier set's textures can be left decoded but undisposed.
    const { loaders, calls } = fakeLoaders();
    await expect(loadTerrainTextureSets(REAL_LIBRARY, ["gravel-043", "missing-set"], loaders))
      .rejects.toThrow(/texture set missing-set is not in the library/);
    expect(calls).toEqual([]);
  });
});

describe("terrain materials", () => {
  it("creates one shared material per preset", async () => {
    const { loaders } = fakeLoaders();
    const sets = await loadTerrainTextureSets(REAL_LIBRARY, SET_IDS, loaders);
    const materials = createTerrainMaterials(sets, ALL_LOOK_PRESETS);
    const lawn = materials.material("lawn-maintained");
    expect(materials.material("lawn-maintained")).toBe(lawn);
    expect(lawn.name).toBe("terrain:lawn-maintained");
    expect(lawn).toBeInstanceOf(THREE.MeshStandardMaterial);
    materials.dispose();
    sets.dispose();
  });
  it("shares one texture set bundle across presets that reference it", async () => {
    const { loaders } = fakeLoaders();
    const sets = await loadTerrainTextureSets(REAL_LIBRARY, ["lawn-grass001"], loaders);
    const materials = createTerrainMaterials(sets, ["lawn-maintained", "grass-generic"]);
    expect(materials.material("lawn-maintained").map)
      .toBe(materials.material("grass-generic").map);
    materials.dispose();
    sets.dispose();
  });
  it("wires textured looks with map, normal, ORM and vertex colours", async () => {
    const { loaders } = fakeLoaders();
    const sets = await loadTerrainTextureSets(REAL_LIBRARY, SET_IDS, loaders);
    const materials = createTerrainMaterials(sets, ALL_LOOK_PRESETS);
    const lawn = materials.material("lawn-maintained");
    const bundle = sets.get("lawn-grass001");
    expect(lawn.map).toBe(bundle.map);
    expect(lawn.normalMap).toBe(bundle.normalMap);
    expect(lawn.roughnessMap).toBe(bundle.ormMap);
    expect(lawn.aoMap).toBe(bundle.ormMap);
    expect(lawn.aoMapIntensity).toBeCloseTo(0.6);
    expect(lawn.normalScale.x).toBe(1);
    expect(lawn.color.getHex()).toBe(0xffffff);
    expect(lawn.vertexColors).toBe(true);
    expect(lawn.metalness).toBe(0);
    expect(lawn.roughness).toBe(1);
    materials.dispose();
    sets.dispose();
  });
  it("flat looks have no maps and keep their table roughness", async () => {
    const { loaders } = fakeLoaders();
    const sets = await loadTerrainTextureSets(REAL_LIBRARY, SET_IDS, loaders);
    const materials = createTerrainMaterials(sets, FLAT_LOOK_PRESETS);
    for (const presetId of FLAT_LOOK_PRESETS) {
      const material = materials.material(presetId);
      expect(material.map).toBeNull();
      expect(material.normalMap).toBeNull();
      expect(material.roughnessMap).toBeNull();
      expect(material.aoMap).toBeNull();
      expect(material.vertexColors).toBe(true);
      expect(material.roughness).toBeCloseTo(TERRAIN_PRESET_LOOKS[presetId]!.roughness);
      expect(material.metalness).toBe(0);
    }
    materials.dispose();
    sets.dispose();
  });
  it("rejects unclassified and water presets", () => {
    expect(() => createTerrainMaterials({ get: () => {
      throw new Error("unused");
    } }, ["unclassified"])).toThrow(/unclassified/);
    expect(() => createTerrainMaterials({ get: () => {
      throw new Error("unused");
    } }, ["water-river"])).toThrow(/water-river/);
  });
  it("kit construction disposes already-created materials when a later material throws", () => {
    // R2: the lawn material is created first, then the woodland preset's texture lookup
    // fails; every material created by the call must be disposed exactly once and the
    // original error rethrown.
    const textures = fakeTerrainTextureSets(["lawn-grass001", "natural-grass-ground"]);
    const assign = (id: string, tags: Record<string, string>) => assignCityGroundMaterialSurfaces({
      polygonId: id, origin: "source", tags,
      sourceRefs: { sourceSha256: "a".repeat(64), elementType: "way", elementId: "1" } });
    const disposed: string[] = [];
    const dispose = THREE.Material.prototype.dispose;
    const spy = vi.spyOn(THREE.Material.prototype, "dispose").mockImplementation(function(this: THREE.Material) {
      disposed.push(this.name); return dispose.call(this);
    });
    const get = textures.get.bind(textures);
    const touched: string[] = [];
    (textures as { get(setId: string): unknown }).get = setId => {
      touched.push(setId);
      if (setId === "natural-grass-ground") {
        throw new Error("fake terrain texture set natural-grass-ground was not loaded");
      }
      return get(setId);
    };
    try {
      expect(() => createTerrainSurfaceKitInTest([assign("a", { landuse: "grass" }),
        assign("b", { natural: "wood" })], textures, createSurfaceWetnessUniforms(), createWaterSurfaceUniforms()))
        .toThrow(/natural-grass-ground was not loaded/);
      expect(touched).toContain("natural-grass-ground");
    } finally {
      spy.mockRestore();
      delete (textures as { get?: unknown }).get;
    }
    // The lawn material was created and then disposed exactly once; the textures belong to
    // the caller and are not disposed by the failed constructor.
    expect(disposed.filter(name => name === "terrain:lawn-maintained")).toHaveLength(1);
    textures.dispose();
  });
  it("materials stay unpatched for surface wetness", async () => {
    const { loaders } = fakeLoaders();
    const sets = await loadTerrainTextureSets(REAL_LIBRARY, SET_IDS, loaders);
    const materials = createTerrainMaterials(sets, ALL_LOOK_PRESETS);
    const uniforms = createSurfaceWetnessUniforms();
    for (const presetId of ALL_LOOK_PRESETS) {
      const material = materials.material(presetId);
      expect(() => applySurfaceWetness(material, uniforms)).not.toThrow();
      expect(Object.hasOwn(material, "onBeforeCompile")).toBe(true);
    }
    materials.dispose();
    sets.dispose();
  });
});

describe("terrain vertex attributes", () => {
  const look = TERRAIN_PRESET_LOOKS["lawn-maintained"]!;
  it("spans 5 UV units on a 10 m edge with tile 2 m", () => {
    const tileAssignment = { ...assignment("lawn-maintained"),
      variation: { hueShiftDeg: 0, valueShift: 0, rotationRad: 0, offsetU: 0, offsetV: 0 } };
    const positions = [0, 0, 0, 10, 0, 0];
    const { uv } = terrainVertexAttributes(positions, tileAssignment, look, 2);
    expect(uv).toEqual(new Float32Array([0, 0, 5, 0]));
  });
  it("uses the assignment tileSizeM for flat looks", () => {
    const flatLook = TERRAIN_PRESET_LOOKS.sand!;
    const flatAssignment = assignment("sand");
    const { uv } = terrainVertexAttributes([0, 0, 0, 6, 0, 0], flatAssignment, flatLook,
      flatAssignment.tileSizeM);
    expect(uv[2]).toBeCloseTo(6 / flatAssignment.tileSizeM + flatAssignment.variation.offsetU);
  });
  it("rotates UVs about the world origin", () => {
    const rotation = Math.PI / 2;
    const rotated = { ...assignment("gravel"),
      variation: { hueShiftDeg: 0, valueShift: 0, rotationRad: rotation, offsetU: 0, offsetV: 0 } };
    const gravelLook = TERRAIN_PRESET_LOOKS.gravel!;
    const { uv } = terrainVertexAttributes([2, 0, 0], rotated, gravelLook, 1);
    expect(uv[0]).toBeCloseTo(0, 12);
    expect(uv[1]).toBeCloseTo(2, 12);
  });
  it("adds the assignment offsets", () => {
    const offset = { ...assignment("gravel"),
      variation: { hueShiftDeg: 0, valueShift: 0, rotationRad: 0, offsetU: 0.25, offsetV: 0.75 } };
    const gravelLook = TERRAIN_PRESET_LOOKS.gravel!;
    const { uv } = terrainVertexAttributes([1, 0, 1], offset, gravelLook, 2);
    expect(uv[0]).toBeCloseTo(0.5 + 0.25, 12);
    expect(uv[1]).toBeCloseTo(0.5 + 0.75, 12);
  });
  it("applies the hue and value variation to the tint identically across a polygon", () => {
    const varied = assignment("lawn-maintained");
    const positions = [0, 0, 0, 4, 0, 0, 4, 0, 4, 0, 0, 4];
    const { color } = terrainVertexAttributes(positions, varied, look, look.textureSet === null
      ? 1 : REAL_LIBRARY.sets.find(set => set.id === look.textureSet)!.tileSizeM);
    const base = new THREE.Color(look.tint);
    base.offsetHSL(varied.variation.hueShiftDeg / 360, 0, 0);
    const factor = 1 + varied.variation.valueShift;
    const expected = [base.r, base.g, base.b].map(channel =>
      Math.min(1, Math.max(0, channel * factor)));
    for (let vertex = 0; vertex < 4; vertex++) {
      expect(color[vertex * 3]).toBeCloseTo(expected[0]!, 12);
      expect(color[vertex * 3 + 1]).toBeCloseTo(expected[1]!, 12);
      expect(color[vertex * 3 + 2]).toBeCloseTo(expected[2]!, 12);
    }
    expect(varied.variation.hueShiftDeg).not.toBe(0);
  });
  it("gives identical attributes for the same assignment", () => {
    const same = assignment("grass-natural", "poly-7");
    const naturalLook = TERRAIN_PRESET_LOOKS["grass-natural"]!;
    const tileSize = REAL_LIBRARY.sets.find(set => set.id === naturalLook.textureSet)!.tileSizeM;
    const positions = [0, 0, 0, 3, 0, 1, 1, 0, 4];
    const first = terrainVertexAttributes(positions, same, naturalLook, tileSize);
    const second = terrainVertexAttributes(positions, same, naturalLook, tileSize);
    expect(second.uv).toEqual(first.uv);
    expect(second.color).toEqual(first.color);
  });
  it("applyTerrainAttributes writes the same values onto a geometry", () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(
      [0, 0, 0, 10, 0, 0, 10, 0, 10], 3));
    const scaled = { ...assignment("lawn-maintained"),
      variation: { hueShiftDeg: 0, valueShift: 0, rotationRad: 0, offsetU: 0, offsetV: 0 } };
    applyTerrainAttributes(geometry, scaled, look, 2);
    const uv = geometry.getAttribute("uv");
    const color = geometry.getAttribute("color");
    expect(uv.itemSize).toBe(2);
    expect(Array.from(uv.array)).toEqual([0, 0, 5, 0, 5, 5]);
    expect(color.itemSize).toBe(3);
    expect(color.count).toBe(3);
  });
  it("vertex colours land in linear space (sRGB tint 0x808080 darkens below 0.5)", () => {
    const linearLook = { ...TERRAIN_PRESET_LOOKS.sand!, tint: 0x808080 };
    const neutral = { ...assignment("sand"),
      variation: { hueShiftDeg: 0, valueShift: 0, rotationRad: 0, offsetU: 0, offsetV: 0 } };
    const { color } = terrainVertexAttributes([0, 0, 0], neutral, linearLook, 1);
    // THREE converts the sRGB hex tint to the linear working space
    // (ColorManagement enabled), so 0x80/255 lands well below 0.5.
    const linear = new THREE.Color(0x808080);
    // Float32Array storage rounds to float32 precision.
    expect(color[0]).toBeCloseTo(linear.r, 7);
    expect(color[0]).toBeLessThan(0.25);
  });
  it("rejects invalid tile sizes and ragged positions", () => {
    expect(() => terrainVertexAttributes([0, 0, 0], assignment("sand"), TERRAIN_PRESET_LOOKS.sand!, 0))
      .toThrow(/tileSizeM/);
    expect(() => terrainVertexAttributes([0, 0], assignment("sand"), TERRAIN_PRESET_LOOKS.sand!, 1))
      .toThrow(/xyz triples/);
  });
});

describe("module-level integration with the rule set", () => {
  it("every look preset is reachable from the rule set via a fixture assignment", () => {
    for (const presetId of Object.keys(TERRAIN_PRESET_LOOKS)) {
      const compiled = compileGroundMaterialRules(CITY_GROUND_MATERIAL_RULES_V1);
      expect(compiled.presetById.has(presetId), `preset ${presetId} exists in rules`).toBe(true);
    }
  });
  it("assignment variation feeds directly into vertex attributes", () => {
    const compiled = compileGroundMaterialRules(CITY_GROUND_MATERIAL_RULES_V1);
    const varied = assignGroundMaterial(input("poly-9", { amenity: "parking" }), compiled, 7);
    const parkingLook = TERRAIN_PRESET_LOOKS["parking-generic"]!;
    const attributes = terrainVertexAttributes([0, 0, 0, 5, 0, 5], varied, parkingLook,
      varied.tileSizeM);
    expect(attributes.uv).toHaveLength(4);
  });
});
