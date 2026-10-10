// @vitest-environment node
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import * as waterMaterials from "./city-water-material";
import { groundMaterialInputFromCover, type GroundMaterialAssignment } from "./city-ground-material-rules";
import { assignCityGroundMaterial, CITY_TERRAIN_MATERIAL_SEED, createTerrainSurfaceKit,
  loadVerifiedTerrainTextureSets, TERRAIN_TEXTURE_LIBRARY_SHA256, terrainSurfaceGeometry,
  terrainTextureSetIds } from "./city-terrain-surfaces";
import { TERRAIN_TEXTURE_LIBRARY_URL } from "./city-terrain-materials";
import { createSurfaceWetnessUniforms } from "./city-surface-wetness";
import { createWaterSurfaceUniforms } from "./city-water-surface";
import { ALL_TEXTURE_SET_IDS, fakeTerrainTextureSets } from "./testing/terrain-surface-fixture";

const publicFile = (url: string): Uint8Array => new Uint8Array(readFileSync(resolve(process.cwd(), "public", url.replace(/^\/+/, ""))));
const fakeDecode = async (): Promise<THREE.Texture> => new THREE.Texture();

function assign(id: number, tags: Record<string, string>): GroundMaterialAssignment {
  return assignCityGroundMaterial(groundMaterialInputFromCover({ id: `osm:way:${id}:0`,
    provenance: { kind: "osm", sourceSha256: "a".repeat(64), elementType: "way", elementId: String(id), tags } }));
}

describe("verified terrain texture loading", () => {
  it("pins the published library manifest", () => {
    expect(createHash("sha256").update(publicFile(TERRAIN_TEXTURE_LIBRARY_URL)).digest("hex"))
      .toBe(TERRAIN_TEXTURE_LIBRARY_SHA256);
  });
  it("verifies every requested file before decoding and decodes three maps per set", async () => {
    const requested: string[] = [], decoded: number[] = [];
    const sets = await loadVerifiedTerrainTextureSets({ setIds: ["gravel-043", "lawn-grass001"], maxAnisotropy: 4,
      fetchBytes: async url => { requested.push(url); return publicFile(url); },
      decodeTexture: async bytes => { decoded.push(bytes.byteLength); return new THREE.Texture(); } });
    expect(requested[0]).toBe(TERRAIN_TEXTURE_LIBRARY_URL);
    expect(requested.slice(1).sort()).toEqual(["gravel-043", "lawn-grass001"].flatMap(set =>
      ["color", "normal", "orm"].map(map => `/textures/terrain-v1/${set}/${map}.webp`)).sort());
    expect(decoded).toHaveLength(6);
    expect(sets.get("gravel-043").map.colorSpace).toBe(THREE.SRGBColorSpace);
    expect(sets.get("gravel-043").normalMap.colorSpace).toBe(THREE.NoColorSpace);
    sets.dispose();
  });
  it("rejects a library manifest that differs from the pinned digest", async () => {
    const manifest = publicFile(TERRAIN_TEXTURE_LIBRARY_URL);
    await expect(loadVerifiedTerrainTextureSets({ setIds: ["gravel-043"], maxAnisotropy: 4, decodeTexture: fakeDecode,
      fetchBytes: async url => url === TERRAIN_TEXTURE_LIBRARY_URL
        ? new Uint8Array([...manifest, 0x0a]) : publicFile(url) })).rejects.toThrow(/library sha256 mismatch/);
  });
  it("rejects a texture whose bytes or size differ from the manifest", async () => {
    const tamper = (target: string, change: (bytes: Uint8Array) => Uint8Array) => loadVerifiedTerrainTextureSets({
      setIds: ["gravel-043"], maxAnisotropy: 4, decodeTexture: fakeDecode,
      fetchBytes: async url => url.endsWith(target) ? change(publicFile(url)) : publicFile(url) });
    await expect(tamper("gravel-043/orm.webp", bytes => { const copy = bytes.slice(); copy[100]! ^= 1; return copy; }))
      .rejects.toThrow(/sha256 mismatch: \/textures\/terrain-v1\/gravel-043\/orm.webp/);
    await expect(tamper("gravel-043/color.webp", bytes => bytes.slice(1)))
      .rejects.toThrow(/size mismatch/);
  });
});

describe("terrain surface kit", () => {
  const assignments = [
    assign(1, { leisure: "park" }), assign(2, { landuse: "grass" }), assign(3, { surface: "asphalt" }),
    assign(4, { landuse: "construction" }), assign(5, { natural: "water", water: "river" }),
    assign(6, { surface: "metal_grid" }), assign(7, { natural: "wood" }),
    assign(9, { waterway: "dock" }),
  ];
  it("requests only the texture sets the textured looks reference", () => {
    expect(terrainTextureSetIds(assignments)).toEqual(["lawn-grass001", "natural-grass-ground", "soil-park-dirt"]);
  });
  it("shares one material per preset and patches wetness once per material by family", () => {
    const kit = createTerrainSurfaceKit(assignments, fakeTerrainTextureSets(ALL_TEXTURE_SET_IDS),
      createSurfaceWetnessUniforms(), createWaterSurfaceUniforms());
    const [park, grass, asphalt, construction, river, unknown, wood, dock] = assignments.map(item => kit.material(item));
    expect(park).toBe(grass);
    expect(new Set(kit.materials).size).toBe(kit.materials.length);
    expect(kit.materials).toHaveLength(7);
    const key = (material: THREE.Material) => material.customProgramCacheKey();
    // Textured looks carry wetness and the anti-tiling detail patch; the fake
    // sets use a 2 m tile, so the detail suffix is fixed. The lawn presets
    // remap roughness to [0.85, 1]; woodland-floor (the scan) keeps [0, 1].
    expect(key(park!)).toBe("city-surface-wetness-v2:0.300000:0.550000|city-terrain-detail-v3:2:0.85,1");
    expect(key(wood!)).toBe("city-surface-wetness-v2:0.300000:0.550000|city-terrain-detail-v3:2:0,1");
    expect(key(construction!)).toBe("city-surface-wetness-v2:0.240000:0.600000|city-terrain-detail-v3:2:0,1");
    expect(key(asphalt!)).toBe("city-surface-wetness-v2:0.400000:0.220000");
    expect(key(unknown!)).toBe("city-surface-wetness-v2:0.400000:0.220000");
    expect(river).toBeInstanceOf(THREE.MeshPhysicalMaterial);
    expect(river!.userData.surfaceWetness).toBeUndefined();
    // Water materials carry the T8b ripple patch keyed by their preset; every
    // preset compiles to its own program, and terrain materials stay unpatched.
    expect(river!.userData.waterSurface).toMatchObject({ version: "city-water-surface-v2",
      presetId: "water-river", typeResponse: 1.0 });
    expect(river!.customProgramCacheKey()).toBe("city-water-surface-v2:water-river");
    expect(dock!.userData.waterSurface).toMatchObject({ presetId: "water-dock", typeResponse: 0.8 });
    expect(dock!.customProgramCacheKey()).toBe("city-water-surface-v2:water-dock");
    expect(new Set([river!.customProgramCacheKey(), dock!.customProgramCacheKey()]).size).toBe(2);
    for (const material of [park, grass, asphalt, construction, unknown, wood]) {
      expect((material as THREE.MeshStandardMaterial).userData.waterSurface).toBeUndefined();
    }
    expect(unknown!.userData.terrainPreset).toBe("unclassified");
    expect((unknown as THREE.MeshStandardMaterial).map).toBeNull();
    expect((park as THREE.MeshStandardMaterial).map).not.toBeNull();
    expect(park!.userData.terrainDetail).toEqual({ version: "city-terrain-detail-v3", tileSizeM: 2,
      roughnessRange: [0.85, 1] });
    // woodland-floor (wood) uses the scan-derived set and keeps the identity range.
    expect(wood!.userData.terrainDetail).toEqual({ version: "city-terrain-detail-v3", tileSizeM: 2,
      roughnessRange: [0, 1] });
    expect(construction!.userData.terrainDetail).toEqual({ version: "city-terrain-detail-v3", tileSizeM: 2,
      roughnessRange: [0, 1] });
    expect(asphalt!.userData.terrainDetail).toBeUndefined();
    expect(unknown!.userData.terrainDetail).toBeUndefined();
    expect(river!.userData.terrainDetail).toBeUndefined();
    expect(() => kit.material(assign(8, { surface: "sand" }))).toThrow(/no material for preset sand/);
    kit.dispose();
  });
  it("passes the look's roughness range: lawn and woodland get [0.85, 1], other textured looks [0, 1]", () => {
    const kit = createTerrainSurfaceKit(assignments, fakeTerrainTextureSets(ALL_TEXTURE_SET_IDS),
      createSurfaceWetnessUniforms(), createWaterSurfaceUniforms());
    // assignments: park/lawn-maintained, grass, asphalt, construction/soil-construction,
    // water, metal_grid/unclassified, wood, dock.
    const materials = assignments.map(item => kit.material(item)) as THREE.MeshStandardMaterial[];
    const [park, grass, , construction, , , wood] = materials;
    expect(park!.userData.terrainDetail).toMatchObject({ roughnessRange: [0.85, 1] });
    expect(grass!.userData.terrainDetail).toMatchObject({ roughnessRange: [0.85, 1] });
    // woodland-floor uses the scan-derived natural-grass-ground set and keeps the identity.
    expect(wood!.userData.terrainDetail).toMatchObject({ roughnessRange: [0, 1] });
    expect(construction!.userData.terrainDetail).toMatchObject({ roughnessRange: [0, 1] });
    // Flat, water and unclassified materials are never detail-patched.
    expect(kit.material(assign(3, { surface: "asphalt" })).userData.terrainDetail).toBeUndefined();
    kit.dispose();
  });
  it("runs the wetness insertions and the detail samples through one compile hook", () => {
    const kit = createTerrainSurfaceKit(assignments, fakeTerrainTextureSets(ALL_TEXTURE_SET_IDS), createSurfaceWetnessUniforms(),
      createWaterSurfaceUniforms());
    const park = kit.material(assignments[0]!) as THREE.MeshStandardMaterial;
    const shader = { uniforms: {} as Record<string, THREE.IUniform>,
      vertexShader: THREE.ShaderLib.standard.vertexShader,
      fragmentShader: THREE.ShaderLib.standard.fragmentShader };
    (park.onBeforeCompile as NonNullable<THREE.Material["onBeforeCompile"]>)(shader as never, {} as never);
    expect(shader.uniforms.uTerrainTileSizeM!.value).toBe(2);
    expect(shader.uniforms.uTerrainRoughnessRange!.value).toEqual(new THREE.Vector2(0.85, 1));
    expect(shader.fragmentShader).toContain("terrainDetailColor( map, vMapUv, terrainDetail )");
    expect(shader.fragmentShader).toContain("terrainRoughnessSample( roughnessMap, terrainDetail )");
    expect(shader.fragmentShader).toContain("( texelRoughness.r - 1.0 ) * aoMapIntensity");
    expect(shader.fragmentShader).toContain("uniform float uSurfaceWetness;");
    expect(shader.fragmentShader).toContain("surfaceWetnessDarken");
    const kitNoTextures = createTerrainSurfaceKit(
      [assign(3, { surface: "asphalt" }), assign(5, { natural: "water", water: "river" })],
      fakeTerrainTextureSets(ALL_TEXTURE_SET_IDS), createSurfaceWetnessUniforms(), createWaterSurfaceUniforms());
    for (const material of kitNoTextures.materials) {
      expect(material.userData.terrainDetail).toBeUndefined();
    }
    kitNoTextures.dispose();
    kit.dispose();
  });
  it("writes metre uvs and per-polygon tint for terrain, nothing for water and unclassified", () => {
    const kit = createTerrainSurfaceKit(assignments, fakeTerrainTextureSets(ALL_TEXTURE_SET_IDS),
      createSurfaceWetnessUniforms(), createWaterSurfaceUniforms());
    const triangle = [[0, 0], [2, 0], [0, 2]] as const;
    const lawn = terrainSurfaceGeometry([{ triangles: [triangle], assignment: assignments[0]! },
      { triangles: [triangle], assignment: assignments[1]! }], 0.018, kit);
    expect(lawn.getAttribute("position").count).toBe(6);
    expect(lawn.getAttribute("normal").getY(0)).toBeGreaterThan(0.99);
    expect(lawn.getAttribute("uv").count).toBe(6);
    // Different polygons of one preset keep their own seeded offsets and tints.
    expect(lawn.getAttribute("uv").getX(0)).not.toBeCloseTo(lawn.getAttribute("uv").getX(3), 6);
    expect(assignments[0]!.seed).toBe(CITY_TERRAIN_MATERIAL_SEED);
    const water = terrainSurfaceGeometry([{ triangles: [triangle], assignment: assignments[4]! }], 0.012, kit);
    expect(water.getAttribute("uv")).toBeUndefined(); expect(water.getAttribute("color")).toBeUndefined();
    expect(() => terrainSurfaceGeometry([{ triangles: [triangle], assignment: assignments[0]! },
      { triangles: [triangle], assignment: assignments[5]! }], 0, kit)).toThrow(/disagree on vertex attributes/);
    lawn.dispose(); water.dispose(); kit.dispose();
  });
  it("disposes its materials and the loaded textures", () => {
    const textures = fakeTerrainTextureSets(ALL_TEXTURE_SET_IDS);
    const kit = createTerrainSurfaceKit(assignments, textures,
      createSurfaceWetnessUniforms(), createWaterSurfaceUniforms());
    let disposedMaterials = 0, disposedTextures = 0;
    kit.materials.forEach(material => material.addEventListener("dispose", () => { disposedMaterials++; }));
    textures.textures.forEach(texture => texture.addEventListener("dispose", () => { disposedTextures++; }));
    kit.dispose();
    expect(disposedMaterials).toBe(kit.materials.length);
    expect(disposedTextures).toBe(textures.textures.length);
  });
  it("disposes every kit material after a water lookup fails without disposing caller textures", () => {
    const textures = fakeTerrainTextureSets(["lawn-grass001"]);
    const textureDisposals = textures.textures.map(texture => vi.spyOn(texture, "dispose"));
    const created = new Set<THREE.Material>();
    const setValues = THREE.Material.prototype.setValues;
    const construction = vi.spyOn(THREE.Material.prototype, "setValues").mockImplementation(function(this: THREE.Material, values) {
      created.add(this);
      return setValues.call(this, values);
    });
    const disposed: THREE.Material[] = [];
    const dispose = THREE.Material.prototype.dispose;
    const disposal = vi.spyOn(THREE.Material.prototype, "dispose").mockImplementation(function(this: THREE.Material) {
      disposed.push(this);
      return dispose.call(this);
    });
    const failure = new Error("injected kit water lookup failure");
    const createWaterMaterials = waterMaterials.createWaterMaterials;
    const waterFactory = vi.spyOn(waterMaterials, "createWaterMaterials").mockImplementation(() => {
      const water = createWaterMaterials();
      return { ...water, material: () => { throw failure; } };
    });
    try {
      let caught: unknown;
      try {
        createTerrainSurfaceKit([assign(10, { leisure: "park" }), assign(11, { surface: "asphalt" }),
          assign(12, { natural: "water", water: "river" })], textures, createSurfaceWetnessUniforms(),
          createWaterSurfaceUniforms());
      } catch (error) { caught = error; }
      expect(caught).toBe(failure);
      expect(waterFactory).toHaveBeenCalledOnce();
      // These materials exist only after terrain construction has returned successfully.
      expect([...created].map(material => material.name).sort()).toEqual([
        "terrain:asphalt", "terrain:lawn-maintained", "terrain:unclassified",
        ...Object.keys(waterMaterials.WATER_PRESET_LOOKS).map(preset => `water:${preset}`),
      ].sort());
      for (const material of created) expect(disposed.filter(item => item === material)).toHaveLength(1);
      expect(disposed).toHaveLength(created.size);
      textureDisposals.forEach(spy => expect(spy).not.toHaveBeenCalled());
    } finally {
      construction.mockRestore(); disposal.mockRestore(); waterFactory.mockRestore();
      textureDisposals.forEach(spy => spy.mockRestore());
      textures.dispose();
    }
  });
});
