import * as THREE from "three";

/**
 * T5b round 2: anti-tiling, macro variation and distance normal flattening for
 * textured terrain materials. A compile-time `onBeforeCompile` patch for the
 * shared terrain `MeshStandardMaterial`s of `createTerrainMaterials` (they ship
 * `map`, `normalMap`, `roughnessMap` and `aoMap` from one ORM set with metre
 * UVs):
 *
 * 1. Every per-map sample mixes three world-offset copies of the tile with
 *    variance-preserving weights from a skewed hex-cell grid at one cell per
 *    tile, so adjacent tiles stop repeating visibly. The blend is anchored on
 *    the texture mean (its 1x1 mip) so uncorrelated samples do not shrink
 *    contrast (offline model
 *    `validation/terrain-realism-20261001/T5c/prototype-antitiling.py`,
 *    `hex_tiling(..., scale=1.0, vp=True, sharpness=3.0)`).
 * 2. A low-frequency brightness variation in world metres breaks up large
 *    uniform areas without touching the measured texture means.
 * 3. With view distance the tangent-space normal strength fades, which reduces
 *    mip shimmer at range. The round-1 far colour sample is removed: it halved
 *    contrast at distance and is redundant once the hex offsets break
 *    repetition.
 *
 * The patch replaces only the `map_fragment`, `roughnessmap_fragment`,
 * `normal_fragment_maps` and `aomap_fragment` include tokens of the standard
 * fragment shader, so it composes with `applySurfaceWetness`, which inserts its
 * code after those same tokens (wetness must be applied before detail). The AO
 * sample reuses the roughness texel instead of fetching again: the
 * `roughnessMap === aoMap` precondition holds and `roughnessmap_fragment`
 * precedes `aomap_fragment` in the standard fragment shader. Every substitution
 * fails closed: if a three.js chunk no longer contains the expected sampling
 * expression exactly, patching throws instead of silently rendering unpatched
 * terrain. No render pass, render target or per-frame allocation is added.
 */

/** Tuned constants of this patch revision. Changing any value requires a new
 * `version`, because the constants are compiled into the shader as literals. */
export const TERRAIN_DETAIL_V3 = Object.freeze({
  version: "city-terrain-detail-v3",
  hexCellsPerTile: 1.0, // skewed hex (triangle) cells per texture tile
  hexSharpness: 3.0, // weight sharpness towards the nearest cell vertex
  macroScaleM: 37, // metres; deliberately not a multiple of any tileSizeM
  macroStrength: 0.14, // brightness multiplier range 1 +/- 0.14
  farStartM: 35,
  farEndM: 160,
  farNormalScale: 0.35, // normal strength at and beyond farEndM
});

/** The exact slice of `WebGLProgramParametersWithUniforms` this patch edits;
 * tests feed plain objects shaped like it. */
interface TerrainDetailShader {
  readonly uniforms: Record<string, THREE.IUniform>;
  vertexShader: string;
  fragmentShader: string;
}

/** Number of occurrences of `needle` in `haystack`. */
function countOccurrences(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}

/** Replace the single occurrence of `token` or throw; never a silent no-op. */
function replaceOnce(source: string, token: string, replacement: string, label: string): string {
  if (countOccurrences(source, token) !== 1) {
    throw new Error(`Terrain detail could not patch ${label}`);
  }
  return source.replace(token, replacement);
}

/** Throw unless `haystack` contains `needle` exactly `expected` times. */
function expectCount(haystack: string, needle: string, expected: number, label: string): void {
  if (countOccurrences(haystack, needle) !== expected) {
    throw new Error(`Terrain detail could not patch ${label}`);
  }
}

function floatLiteral(value: number): string {
  return value.toFixed(6);
}

/** The GLSL helper block inserted after `#include <common>`. `uv` values are in
 * texture tiles. Constants are baked in as literals so the program cache key
 * covers them through the version string. */
function terrainDetailHelperBlock(): string {
  const d = TERRAIN_DETAIL_V3;
  return `uniform float uTerrainTileSizeM;
uniform vec2 uTerrainRoughnessRange;
float terrainHash12( vec2 p ) { vec3 p3 = fract( vec3( p.xyx ) * 0.1031 ); p3 += dot( p3, p3.yzx + 33.33 ); return fract( ( p3.x + p3.y ) * p3.z ); }
vec2 terrainHash21( float n ) { vec3 p3 = fract( vec3( n ) * vec3( 0.1031, 0.1030, 0.0973 ) ); p3 += dot( p3, p3.yzx + 33.33 ); return fract( ( p3.xx + p3.yz ) * p3.zy ); }
float terrainValueNoise( vec2 p ) {
  vec2 i = floor( p ); vec2 f = fract( p ); vec2 u = f * f * ( 3.0 - 2.0 * f );
  return mix( mix( terrainHash12( i ), terrainHash12( i + vec2( 1.0, 0.0 ) ), u.x ),
              mix( terrainHash12( i + vec2( 0.0, 1.0 ) ), terrainHash12( i + vec2( 1.0, 1.0 ) ), u.x ), u.y );
}
struct TerrainDetailUv { vec2 uv1; vec2 uv2; vec2 uv3; vec3 w; vec2 dx; vec2 dy; };
TerrainDetailUv terrainDetailUv( vec2 uv ) {
  // Skewed triangle grid, ${floatLiteral(d.hexCellsPerTile)} cells per tile; each vertex owns one random offset.
  vec2 s = uv * ${floatLiteral(d.hexCellsPerTile)};
  vec2 skew = vec2( s.x + s.y * 0.5773502692, s.y * 1.1547005384 );
  vec2 base = floor( skew ); vec2 f = skew - base;
  bool upper = f.x + f.y > 1.0;
  vec2 v1 = upper ? base + 1.0 : base;
  vec3 w = upper ? vec3( f.x + f.y - 1.0, 1.0 - f.y, 1.0 - f.x ) : vec3( 1.0 - f.x - f.y, f.x, f.y );
  w = pow( max( w, 0.0 ), vec3( ${floatLiteral(d.hexSharpness)} ) ); w /= dot( w, vec3( 1.0 ) );
  TerrainDetailUv d;
  d.uv1 = uv + terrainHash21( terrainHash12( v1 ) * 97.0 );
  d.uv2 = uv + terrainHash21( terrainHash12( base + vec2( 1.0, 0.0 ) ) * 97.0 );
  d.uv3 = uv + terrainHash21( terrainHash12( base + vec2( 0.0, 1.0 ) ) * 97.0 );
  d.w = w;
  // Gradients of the continuous uv: offsets jump between cells, implicit derivatives would pick the wrong mip.
  d.dx = dFdx( uv ); d.dy = dFdy( uv );
  return d;
}
// Variance-preserving blend around the texture mean (its 1x1 mip): blending uncorrelated
// samples would otherwise shrink contrast by sqrt(sum w^2).
vec4 terrainSample( sampler2D tex, TerrainDetailUv d ) {
  vec4 mixed = textureGrad( tex, d.uv1, d.dx, d.dy ) * d.w.x + textureGrad( tex, d.uv2, d.dx, d.dy ) * d.w.y
    + textureGrad( tex, d.uv3, d.dx, d.dy ) * d.w.z;
  vec4 mean = textureLod( tex, vec2( 0.5 ), 16.0 );
  return clamp( mean + ( mixed - mean ) * inversesqrt( dot( d.w, d.w ) ), 0.0, 1.0 );
}
// Remap of the ORM roughness (G) channel, r' = min + (max - min) * r, moving the
// authored map's mean onto the scan-derived reference. AO still reads the
// untouched .r of the same sample.
vec4 terrainRoughnessSample( sampler2D tex, TerrainDetailUv d ) {
  vec4 orm = terrainSample( tex, d );
  orm.g = mix( uTerrainRoughnessRange.x, uTerrainRoughnessRange.y, orm.g );
  return orm;
}
vec4 terrainDetailColor( sampler2D tex, vec2 uv, TerrainDetailUv d ) {
  vec4 colour = terrainSample( tex, d );
  float macro = 1.0 + ${floatLiteral(d.macroStrength)} * ( terrainValueNoise( uv * uTerrainTileSizeM / ${floatLiteral(d.macroScaleM)} ) * 2.0 - 1.0 );
  return vec4( colour.rgb * macro, colour.a );
}`;
}

/** True when the texture keeps the default identity transform, which is what
 * makes `vMapUv`, `vNormalMapUv`, `vRoughnessMapUv` and `vAoMapUv` equal. With
 * `matrixAutoUpdate === false` three.js uses `texture.matrix` verbatim, so the
 * matrix itself must be the identity; the property checks alone would not
 * guarantee equal UV varyings. */
function hasIdentityTransform(texture: THREE.Texture): boolean {
  if (texture.channel !== 0
    || texture.offset.x !== 0 || texture.offset.y !== 0
    || texture.repeat.x !== 1 || texture.repeat.y !== 1
    || texture.rotation !== 0
    || texture.center.x !== 0 || texture.center.y !== 0) {
    return false;
  }
  if (texture.matrixAutoUpdate) return true; // updateMatrix() from these properties is the identity.
  const m = texture.matrix.elements;
  return m[0] === 1 && m[1] === 0 && m[2] === 0
    && m[3] === 0 && m[4] === 1 && m[5] === 0
    && m[6] === 0 && m[7] === 0 && m[8] === 1;
}

/**
 * Patch a textured terrain material in place. Throws on any precondition it
 * cannot guarantee; a material that carries the patch is rejected instead of
 * being patched twice. Wetness (`applySurfaceWetness`) must already be applied:
 * the prior compile hook runs first and its insertions sit after the include
 * tokens this patch replaces, so both stay intact.
 */
export function applyTerrainDetail(material: THREE.MeshStandardMaterial, tileSizeM: number,
    roughnessRange: readonly [number, number]): void {
  const name = material.name || material.type;
  if (!Number.isFinite(tileSizeM) || tileSizeM <= 0) {
    throw new RangeError(`Terrain detail needs a positive finite tileSizeM: ${String(tileSizeM)} (${name})`);
  }
  const [rangeMin, rangeMax] = roughnessRange;
  if (typeof rangeMin !== "number" || typeof rangeMax !== "number"
    || !Number.isFinite(rangeMin) || !Number.isFinite(rangeMax)
    || !(0 <= rangeMin && rangeMin <= rangeMax && rangeMax <= 1)) {
    throw new RangeError(`Terrain detail needs a finite roughnessRange with 0 <= min <= max <= 1: [${String(rangeMin)}, ${String(rangeMax)}] (${name})`);
  }
  const { map, normalMap, roughnessMap, aoMap } = material;
  if (map === null || normalMap === null || roughnessMap === null || aoMap === null
    || roughnessMap !== aoMap) {
    throw new Error(`Terrain detail needs a textured terrain material: ${name}`);
  }
  const textures = [map, normalMap, roughnessMap, aoMap];
  if (!textures.every(hasIdentityTransform)) {
    throw new Error(`Terrain detail needs identity texture transforms: ${name}`);
  }
  if (material.userData.terrainDetail !== undefined) {
    throw new Error(`Terrain detail is already applied: ${name}`);
  }
  const detail = TERRAIN_DETAIL_V3;
  const prior = material.onBeforeCompile.bind(material);
  const priorKey = material.customProgramCacheKey.bind(material);
  material.onBeforeCompile = (shader: THREE.WebGLProgramParametersWithUniforms,
      renderer: THREE.WebGLRenderer): void => {
    prior.call(material, shader, renderer);
    const terrainShader = shader as unknown as TerrainDetailShader;
    terrainShader.uniforms.uTerrainTileSizeM = { value: tileSizeM };
    terrainShader.uniforms.uTerrainRoughnessRange = { value: new THREE.Vector2(rangeMin, rangeMax) };
    let fragment = terrainShader.fragmentShader;
    fragment = replaceOnce(fragment, "#include <common>",
      `#include <common>\n${terrainDetailHelperBlock()}`, "#include <common>");
    const farStart = floatLiteral(detail.farStartM);
    const farEnd = floatLiteral(detail.farEndM);
    const mapChunk = THREE.ShaderChunk.map_fragment;
    expectCount(mapChunk, "texture2D( map, vMapUv )", 1, "map_fragment");
    const patchedMap = mapChunk.replace("texture2D( map, vMapUv )",
      "terrainDetailColor( map, vMapUv, terrainDetail )");
    fragment = replaceOnce(fragment, "#include <map_fragment>",
      `TerrainDetailUv terrainDetail = terrainDetailUv( vMapUv );\nfloat terrainFar = smoothstep( ${farStart}, ${farEnd}, length( vViewPosition ) );\n${patchedMap}`,
      "#include <map_fragment>");
    const roughnessChunk = THREE.ShaderChunk.roughnessmap_fragment;
    expectCount(roughnessChunk, "texture2D( roughnessMap, vRoughnessMapUv )", 1,
      "roughnessmap_fragment");
    fragment = replaceOnce(fragment, "#include <roughnessmap_fragment>",
      roughnessChunk.replace("texture2D( roughnessMap, vRoughnessMapUv )",
        "terrainRoughnessSample( roughnessMap, terrainDetail )"),
      "#include <roughnessmap_fragment>");
    const normalChunk = THREE.ShaderChunk.normal_fragment_maps;
    // The material is tangent space; the object-space branch is compiled out but
    // carries the same sampling expression, so both occurrences must exist.
    expectCount(normalChunk, "texture2D( normalMap, vNormalMapUv )", 2, "normal_fragment_maps");
    expectCount(normalChunk, "mapN.xy *= normalScale;", 1, "normal_fragment_maps");
    fragment = replaceOnce(fragment, "#include <normal_fragment_maps>",
      normalChunk
        .replace("texture2D( normalMap, vNormalMapUv )", "terrainSample( normalMap, terrainDetail )")
        .replace("texture2D( normalMap, vNormalMapUv )", "terrainSample( normalMap, terrainDetail )")
        .replace("mapN.xy *= normalScale;",
          `mapN.xy *= normalScale * mix( 1.0, ${floatLiteral(detail.farNormalScale)}, terrainFar );`),
      "#include <normal_fragment_maps>");
    const aoChunk = THREE.ShaderChunk.aomap_fragment;
    expectCount(aoChunk, "texture2D( aoMap, vAoMapUv )", 1, "aomap_fragment");
    // `texelRoughness` is declared by the (already replaced) roughnessmap block;
    // the shared-ORM precondition and this chunk order make the reuse exact.
    // Missing or duplicated tokens are rejected by `replaceOnce`; this rejects a reordering.
    const roughnessAt = terrainShader.fragmentShader.indexOf("#include <roughnessmap_fragment>");
    const aoAt = terrainShader.fragmentShader.indexOf("#include <aomap_fragment>");
    if (roughnessAt !== -1 && aoAt !== -1 && roughnessAt > aoAt) {
      throw new Error("Terrain detail could not patch aomap_fragment: roughnessmap_fragment must precede it");
    }
    fragment = replaceOnce(fragment, "#include <aomap_fragment>",
      aoChunk.replace("texture2D( aoMap, vAoMapUv )", "texelRoughness"),
      "#include <aomap_fragment>");
    terrainShader.fragmentShader = fragment;
  };
  // Tile size and range are uniforms; String() is round-trip exact, so distinct values never share a key.
  material.customProgramCacheKey = () =>
    `${priorKey()}|${detail.version}:${String(tileSizeM)}:${String(rangeMin)},${String(rangeMax)}`;
  material.userData.terrainDetail = { version: detail.version, tileSizeM, roughnessRange: [rangeMin, rangeMax] };
  material.needsUpdate = true;
}
