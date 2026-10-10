// @vitest-environment node
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { applyTerrainDetail, TERRAIN_DETAIL_V3 } from "./city-terrain-detail";
import { applySurfaceWetness, createSurfaceWetnessUniforms } from "./city-surface-wetness";

const RANGE: readonly [number, number] = [0.85, 1];

/** A fresh textured terrain material with four textures and identity transforms. */
function texturedMaterial(name = "terrain:test"): THREE.MeshStandardMaterial {
  const orm = new THREE.Texture({ name: "orm" });
  return new THREE.MeshStandardMaterial({
    name,
    map: new THREE.Texture({ name: "color" }),
    normalMap: new THREE.Texture({ name: "normal" }),
    roughnessMap: orm,
    aoMap: orm,
  });
}

interface FakeShader {
  readonly uniforms: Record<string, THREE.IUniform>;
  vertexShader: string;
  fragmentShader: string;
}

/** The real standard shader sources with a minimal uniforms object, as
 * `WebGLProgram` would hand them to `onBeforeCompile`. */
function fakeShader(): FakeShader {
  return {
    uniforms: {} as Record<string, THREE.IUniform>,
    vertexShader: THREE.ShaderLib.standard.vertexShader,
    fragmentShader: THREE.ShaderLib.standard.fragmentShader,
  };
}

function compile(material: THREE.MeshStandardMaterial, shader: FakeShader): void {
  (material.onBeforeCompile as NonNullable<THREE.Material["onBeforeCompile"]>)(
    shader as unknown as THREE.WebGLProgramParametersWithUniforms, {} as THREE.WebGLRenderer);
}

const TILE_M = 2;

describe("terrain detail shader patch", () => {
  it("patches all four map samples, removes the includes and binds the tile-size and range uniforms", () => {
    const material = texturedMaterial();
    applyTerrainDetail(material, TILE_M, RANGE);
    const shader = fakeShader();
    compile(material, shader);
    expect(shader.fragmentShader).toContain("terrainDetailUv( vMapUv )");
    expect(shader.fragmentShader).toContain("terrainDetailColor( map, vMapUv, terrainDetail )");
    expect(shader.fragmentShader).toContain("terrainSample( normalMap, terrainDetail )");
    expect(shader.fragmentShader).toContain("terrainRoughnessSample( roughnessMap, terrainDetail )");
    expect(shader.fragmentShader).toContain("( texelRoughness.r - 1.0 ) * aoMapIntensity");
    for (const include of ["#include <map_fragment>", "#include <roughnessmap_fragment>",
      "#include <normal_fragment_maps>", "#include <aomap_fragment>"]) {
      expect(shader.fragmentShader).not.toContain(include);
    }
    expect(shader.uniforms.uTerrainTileSizeM!.value).toBe(TILE_M);
    expect(shader.uniforms.uTerrainRoughnessRange!.value).toEqual(new THREE.Vector2(0.85, 1));
    // No prior patch: the key is the detail hook's own source plus the detail suffix.
    expect(material.customProgramCacheKey())
      .toContain(`|${TERRAIN_DETAIL_V3.version}:${String(TILE_M)}:0.85,1`);
    expect(shader.fragmentShader).toContain("mapN.xy *= normalScale * mix( 1.0, 0.350000, terrainFar );");
    expect(shader.fragmentShader).toContain("smoothstep( 35.000000, 160.000000, length( vViewPosition ) )");
  });

  it("uses the hex-cell anti-tiling sample with variance-preserving weights and no far colour", () => {
    const material = texturedMaterial();
    applyTerrainDetail(material, TILE_M, RANGE);
    const shader = fakeShader();
    compile(material, shader);
    const sample = shader.fragmentShader.slice(shader.fragmentShader.indexOf("vec4 terrainSample"));
    // Three gradient-controlled samples, the mean anchor and the variance-preserving rescale.
    expect(sample.match(/textureGrad\(/g)).toHaveLength(3);
    expect(shader.fragmentShader).toContain("textureLod( tex, vec2( 0.5 ), 16.0 )");
    expect(shader.fragmentShader).toContain("inversesqrt( dot( d.w, d.w ) )");
    expect(shader.fragmentShader).not.toContain("farSample");
    expect(shader.fragmentShader).not.toContain("terrainDetailColor( map, vMapUv, terrainDetail, terrainFar )");
    // Every plain `texture2D(` fetch is replaced (map, roughness, normal x2, ao).
    expect(shader.fragmentShader.match(/texture2D\(/g)).toBeNull();
    // `terrainFar` exists only for the normal scale now: declaration plus one use.
    expect(shader.fragmentShader.match(/terrainFar/g)).toHaveLength(2);
  });

  it("remaps only the roughness G channel through the range uniform", () => {
    const material = texturedMaterial();
    applyTerrainDetail(material, TILE_M, RANGE);
    const shader = fakeShader();
    compile(material, shader);
    const helperAt = shader.fragmentShader.indexOf("vec4 terrainRoughnessSample");
    const helper = shader.fragmentShader.slice(helperAt, shader.fragmentShader.indexOf("}", helperAt) + 1);
    expect(helper).toContain("vec4 orm = terrainSample( tex, d );");
    expect(helper).toContain("orm.g = mix( uTerrainRoughnessRange.x, uTerrainRoughnessRange.y, orm.g );");
    expect(helper).toContain("return orm;");
    // The helper is declared after terrainSample and before its use in the patched chunk.
    const sampleAt = shader.fragmentShader.indexOf("vec4 terrainSample");
    const useAt = shader.fragmentShader.indexOf("terrainRoughnessSample( roughnessMap, terrainDetail )");
    expect(sampleAt).toBeGreaterThan(-1);
    expect(helperAt).toBeGreaterThan(sampleAt);
    expect(useAt).toBeGreaterThan(helperAt);
    // The uniform declaration sits in the helper block, before both helpers.
    const declared = shader.fragmentShader.indexOf("uniform vec2 uTerrainRoughnessRange;");
    expect(declared).toBeGreaterThan(-1);
    expect(declared).toBeLessThan(sampleAt);
    // The AO reuse still reads the untouched red channel of the remapped texel.
    expect(shader.fragmentShader).toContain("float ambientOcclusion = ( texelRoughness.r - 1.0 )");
  });

  it("keeps the struct declared before its use and the helpers before it", () => {
    const material = texturedMaterial();
    applyTerrainDetail(material, TILE_M, RANGE);
    const shader = fakeShader();
    compile(material, shader);
    const structAt = shader.fragmentShader.indexOf("struct TerrainDetailUv");
    const useAt = shader.fragmentShader.indexOf("TerrainDetailUv terrainDetailUv( vec2 uv )");
    expect(structAt).toBeGreaterThan(-1);
    expect(useAt).toBeGreaterThan(structAt);
    const declared = shader.fragmentShader.indexOf("uniform float uTerrainTileSizeM;");
    expect(declared).toBeLessThan(structAt);
  });

  it("composes with wetness: both patches present, wetness darkening after the map sample", () => {
    const material = texturedMaterial();
    const uniforms = createSurfaceWetnessUniforms();
    applySurfaceWetness(material, uniforms, { maxDarkening: 0.3, minRoughness: 0.55 });
    applyTerrainDetail(material, TILE_M, RANGE);
    const shader = fakeShader();
    compile(material, shader);
    expect(shader.fragmentShader).toContain("uniform float uSurfaceWetness;");
    expect(shader.fragmentShader).toContain("surfaceWetnessDarken");
    expect(shader.fragmentShader).toContain("terrainDetailUv( vMapUv )");
    expect(shader.fragmentShader).toContain("terrainRoughnessSample( roughnessMap, terrainDetail )");
    expect(shader.uniforms.uTerrainTileSizeM!.value).toBe(TILE_M);
    const mapSample = shader.fragmentShader.indexOf("terrainDetailColor( map, vMapUv, terrainDetail )");
    const darken = shader.fragmentShader.indexOf("diffuseColor.rgb *= 1.0 - 0.300000 * surfaceWetnessDarken;");
    expect(mapSample).toBeGreaterThan(-1);
    expect(darken).toBeGreaterThan(mapSample);
    expect(material.customProgramCacheKey())
      .toBe(`city-surface-wetness-v2:0.300000:0.550000|${TERRAIN_DETAIL_V3.version}:2:0.85,1`);
    // The wetness roughness insertion survives the roughnessmap replacement and
    // still follows it, so wetness sees the remapped roughnessFactor.
    const roughnessSample = shader.fragmentShader.indexOf("terrainRoughnessSample( roughnessMap, terrainDetail )");
    const wetRoughness = shader.fragmentShader.indexOf("roughnessFactor = mix(roughnessFactor, 0.550000, surfaceWetness);");
    expect(wetRoughness).toBeGreaterThan(roughnessSample);
    // The AO reuse stays inside the USE_AOMAP branch that follows the wetness roughness line.
    const ao = shader.fragmentShader.indexOf("float ambientOcclusion = ( texelRoughness.r - 1.0 )");
    expect(ao).toBeGreaterThan(wetRoughness);
  });

  it("keys the program by version, tile size and roughness range", () => {
    const small = texturedMaterial("terrain:small");
    const large = texturedMaterial("terrain:large");
    const twin = texturedMaterial("terrain:twin");
    const sameTileOtherRange = texturedMaterial("terrain:range");
    applyTerrainDetail(small, 1.5, RANGE);
    applyTerrainDetail(large, 3, RANGE);
    applyTerrainDetail(twin, 1.5, RANGE);
    applyTerrainDetail(sameTileOtherRange, 1.5, [0, 1]);
    expect(small.customProgramCacheKey()).not.toBe(large.customProgramCacheKey());
    expect(small.customProgramCacheKey()).toBe(twin.customProgramCacheKey());
    // Same tile size but a different range must compile a different program.
    expect(small.customProgramCacheKey()).not.toBe(sameTileOtherRange.customProgramCacheKey());
    expect(small.customProgramCacheKey().endsWith("|city-terrain-detail-v3:1.5:0.85,1")).toBe(true);
  });

  it("keeps keys distinct for values that differ below six decimals", () => {
    const keyFor = (tileSizeM: number, range: readonly [number, number]): string => {
      const material = texturedMaterial();
      applyTerrainDetail(material, tileSizeM, range);
      return material.customProgramCacheKey();
    };
    expect(keyFor(1.5, [1e-7, 1])).not.toBe(keyFor(1.5, [2e-7, 1]));
    expect(keyFor(1.5, [0.85, 1 - 1e-9])).not.toBe(keyFor(1.5, [0.85, 1]));
    expect(keyFor(1.4, RANGE)).not.toBe(keyFor(1.4000001, RANGE));
  });

  it("records version, tile size and roughness range in userData", () => {
    const material = texturedMaterial();
    applyTerrainDetail(material, TILE_M, RANGE);
    expect(material.userData.terrainDetail).toEqual({
      version: "city-terrain-detail-v3", tileSizeM: TILE_M, roughnessRange: [0.85, 1] });
  });

  it("throws on missing maps, shared-map mismatch, non-identity transforms and re-application", () => {
    const missingNormal = texturedMaterial();
    missingNormal.normalMap = null;
    expect(() => applyTerrainDetail(missingNormal, TILE_M, RANGE))
      .toThrow("Terrain detail needs a textured terrain material: terrain:test");
    const mismatched = texturedMaterial();
    mismatched.roughnessMap = new THREE.Texture();
    expect(() => applyTerrainDetail(mismatched, TILE_M, RANGE))
      .toThrow("Terrain detail needs a textured terrain material: terrain:test");
    const repeated = texturedMaterial();
    repeated.map!.repeat.set(2, 1);
    expect(() => applyTerrainDetail(repeated, TILE_M, RANGE))
      .toThrow("Terrain detail needs identity texture transforms: terrain:test");
    const channelled = texturedMaterial();
    channelled.aoMap!.channel = 1;
    expect(() => applyTerrainDetail(channelled, TILE_M, RANGE))
      .toThrow("Terrain detail needs identity texture transforms: terrain:test");
    const applied = texturedMaterial();
    applyTerrainDetail(applied, TILE_M, RANGE);
    expect(() => applyTerrainDetail(applied, TILE_M, RANGE))
      .toThrow("Terrain detail is already applied: terrain:test");
  });

  it("rejects a non-identity manual matrix even when the transform properties are identity", () => {
    const manual = texturedMaterial();
    manual.normalMap!.matrixAutoUpdate = false;
    manual.normalMap!.matrix.setUvTransform(0.25, 0, 1, 1, 0, 0, 0);
    expect(() => applyTerrainDetail(manual, TILE_M, RANGE))
      .toThrow("Terrain detail needs identity texture transforms: terrain:test");
    // The verifier counterexample applies the wetness patch first; composition must not lift the check.
    const composed = texturedMaterial();
    composed.roughnessMap!.matrixAutoUpdate = false;
    composed.roughnessMap!.matrix.setUvTransform(0, 0.5, 1, 1, 0, 0, 0);
    applySurfaceWetness(composed, createSurfaceWetnessUniforms());
    expect(() => applyTerrainDetail(composed, TILE_M, RANGE))
      .toThrow("Terrain detail needs identity texture transforms: terrain:test");
    // A manual matrix that is exactly the identity stays accepted.
    const accepted = texturedMaterial();
    accepted.map!.matrixAutoUpdate = false;
    accepted.map!.matrix.set(1, 0, 0, 0, 1, 0, 0, 0, 1);
    expect(() => applyTerrainDetail(accepted, TILE_M, RANGE)).not.toThrow();
  });

  it("rejects invalid tile sizes", () => {
    expect(() => applyTerrainDetail(texturedMaterial(), 0, RANGE)).toThrow(RangeError);
    expect(() => applyTerrainDetail(texturedMaterial(), NaN, RANGE)).toThrow(RangeError);
    expect(() => applyTerrainDetail(texturedMaterial(), Infinity, RANGE)).toThrow(RangeError);
  });

  it("rejects invalid roughness ranges with a RangeError", () => {
    for (const range of [[0.9, 0.8], [-0.1, 1], [0, 1.1], [NaN, 1]] as const) {
      expect(() => applyTerrainDetail(texturedMaterial(), TILE_M, range),
        `range ${JSON.stringify(range)}`).toThrow(RangeError);
      expect(() => applyTerrainDetail(texturedMaterial(), TILE_M, range),
        `range ${JSON.stringify(range)}`).toThrow(/roughnessRange/);
    }
    expect(() => applyTerrainDetail(texturedMaterial(), TILE_M, [Infinity, 1])).toThrow(RangeError);
    // Valid boundary ranges stay accepted.
    expect(() => applyTerrainDetail(texturedMaterial(), TILE_M, [0, 1])).not.toThrow();
    expect(() => applyTerrainDetail(texturedMaterial(), TILE_M, [1, 1])).not.toThrow();
  });

  it("fails closed when the fragment source does not match the three.js chunks", () => {
    const material = texturedMaterial();
    applyTerrainDetail(material, TILE_M, RANGE);
    const shader = fakeShader();
    shader.fragmentShader = THREE.ShaderLib.standard.fragmentShader
      .replace("#include <aomap_fragment>", "");
    expect(() => compile(material, shader))
      .toThrow("Terrain detail could not patch #include <aomap_fragment>");
  });

  it("rejects a fragment shader where aomap_fragment precedes roughnessmap_fragment", () => {
    const material = texturedMaterial();
    applyTerrainDetail(material, TILE_M, RANGE);
    const shader = fakeShader();
    shader.fragmentShader = THREE.ShaderLib.standard.fragmentShader
      .replace("#include <roughnessmap_fragment>", "#include <ROUGHNESS_PLACEHOLDER>")
      .replace("#include <aomap_fragment>", "#include <roughnessmap_fragment>")
      .replace("#include <ROUGHNESS_PLACEHOLDER>", "#include <aomap_fragment>");
    expect(() => compile(material, shader))
      .toThrow("Terrain detail could not patch aomap_fragment: roughnessmap_fragment must precede it");
  });

  it("asserts the standard fragment shader facts the AO reuse relies on", () => {
    // `roughnessmap_fragment` precedes `aomap_fragment` in the compiled main()
    // so `texelRoughness` is in scope, and the ORM texture is shared.
    const source = THREE.ShaderLib.standard.fragmentShader;
    expect(source.indexOf("#include <roughnessmap_fragment>"))
      .toBeLessThan(source.indexOf("#include <aomap_fragment>"));
    const material = texturedMaterial();
    expect(material.roughnessMap).toBe(material.aoMap);
  });
});
