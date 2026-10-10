/**
 * T5a: runtime terrain texture library and shared ground materials.
 *
 * Turns the licensed T4 PBR assets (processed into
 * `public/textures/terrain-v1/` by
 * `validation/terrain-realism-20261001/T5a/prepare-terrain-textures.py`) into a
 * small, strictly validated runtime texture library, and builds one shared
 * physically based material per ground preset of `CITY_GROUND_MATERIAL_RULES_V1`
 * with metre-based UVs and per-polygon vertex-colour variation, so the
 * renderers can later replace flat colours (wiring is T9).
 *
 * All materials created here stay unpatched `THREE.MeshStandardMaterial`s:
 * no `onBeforeCompile`, no `customProgramCacheKey`, so
 * `applySurfaceWetness` keeps working on every one of them.
 */

import * as THREE from "three";
import { type GroundMaterialAssignment } from "./city-ground-material-rules";

export const TERRAIN_TEXTURE_LIBRARY_URL = "/textures/terrain-v1/manifest.json";

const LIBRARY_SCHEMA = "aero-bench.terrain-texture-library/v1";
const TERRAIN_TEXTURE_PATH_PREFIX = "/textures/terrain-v1/";
const HASH_PATTERN = /^[0-9a-f]{64}$/;
const WATER_PREFIX = "water-";
const UNCLASSIFIED_PRESET = "unclassified";

export interface TerrainTextureFile {
  readonly map: string;
  readonly path: string;
  readonly width: number;
  readonly height: number;
  readonly bytes: number;
  readonly sha256: string;
  readonly sourceFiles: readonly { readonly path: string; readonly sha256: string }[];
}

export interface TerrainTextureSet {
  readonly id: string;
  readonly sourceAssetId: string;
  readonly sourcePage: string;
  readonly licence: string;
  readonly licencePage: string;
  readonly author: string;
  readonly tileSizeM: number;
  readonly tileSizeBasis: string;
  readonly processing: string;
  readonly files: readonly TerrainTextureFile[];
}

export interface TerrainTextureLibrary {
  readonly schema: string;
  readonly sets: readonly TerrainTextureSet[];
}

export interface TerrainPresetLook {
  /** Texture set ID, or null for an authored flat colour look. */
  readonly textureSet: string | null;
  /** sRGB hex tint multiplied over the texture (or standing alone when flat). */
  readonly tint: number;
  readonly roughness: number;
  readonly normalScale: number;
  readonly aoIntensity: number;
  /** Remap of the ORM roughness (G) channel, r' = min + (max - min) * r, applied
   * before the `roughness` multiplier. Required exactly when `textureSet` is set
   * ([0, 1] is the identity); null for flat looks, which have no roughness map. */
  readonly roughnessRange: readonly [number, number] | null;
  /** Required exactly when `textureSet` is null. */
  readonly flatReason: string | null;
}

function isRecord(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`terrain-texture-library: ${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function positiveInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`terrain-texture-library: ${label} must be a positive integer: ${String(value)}`);
  }
  return value;
}

function nonEmptyString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`terrain-texture-library: ${label} must be a non-empty string: ${String(value)}`);
  }
  return value;
}

/**
 * Strict validation of the texture library manifest. Throws a specific `Error`
 * on schema mismatch, duplicate set IDs, missing or extra maps, paths outside
 * the set directory, malformed hashes or non-positive numbers. Returns a
 * frozen object; never partially trusted data.
 */
export function parseTerrainTextureLibrary(value: unknown): TerrainTextureLibrary {
  const root = isRecord(value, "library");
  if (root.schema !== LIBRARY_SCHEMA) {
    throw new Error(`terrain-texture-library: schema must be ${LIBRARY_SCHEMA}: ${String(root.schema)}`);
  }
  if (!Array.isArray(root.sets)) {
    throw new Error("terrain-texture-library: sets must be an array");
  }
  const seen = new Set<string>();
  const sets = root.sets.map((raw): TerrainTextureSet => {
    const entry = isRecord(raw, "set");
    const id = nonEmptyString(entry.id, "set id");
    if (seen.has(id)) throw new Error(`terrain-texture-library: duplicate set id: ${id}`);
    seen.add(id);
    const sourceFiles = (rawFiles => {
      if (!Array.isArray(rawFiles)) {
        throw new Error(`terrain-texture-library: set ${id} files must be an array`);
      }
      const maps = new Map<string, TerrainTextureFile>();
      for (const rawFile of rawFiles) {
        const file = isRecord(rawFile, `set ${id} file`);
        const map = nonEmptyString(file.map, `set ${id} file map`);
        if (maps.has(map)) {
          throw new Error(`terrain-texture-library: set ${id} duplicate map: ${map}`);
        }
        const path = nonEmptyString(file.path, `set ${id} file ${map} path`);
        if (!path.startsWith(`${TERRAIN_TEXTURE_PATH_PREFIX}${id}/`) || !path.endsWith(".webp")) {
          throw new Error(`terrain-texture-library: set ${id} file ${map} path must start with ${TERRAIN_TEXTURE_PATH_PREFIX}${id}/ and end .webp: ${path}`);
        }
        if (path.includes("..")) {
          throw new Error(`terrain-texture-library: set ${id} file ${map} path must not contain "..": ${path}`);
        }
        if (typeof file.sha256 !== "string" || !HASH_PATTERN.test(file.sha256)) {
          throw new Error(`terrain-texture-library: set ${id} file ${map} sha256 must be 64 hex characters: ${String(file.sha256)}`);
        }
        for (const rawSource of (rawSourceFiles => {
          if (!Array.isArray(rawSourceFiles)) {
            throw new Error(`terrain-texture-library: set ${id} file ${map} sourceFiles must be an array`);
          }
          return rawSourceFiles;
        })(file.sourceFiles)) {
          const source = isRecord(rawSource, `set ${id} file ${map} sourceFile`);
          nonEmptyString(source.path, `set ${id} file ${map} sourceFile path`);
          if (typeof source.sha256 !== "string" || !HASH_PATTERN.test(source.sha256)) {
            throw new Error(`terrain-texture-library: set ${id} file ${map} sourceFile sha256 must be 64 hex characters: ${String(source.sha256)}`);
          }
        }
        maps.set(map, {
          map,
          path,
          width: positiveInteger(file.width, `set ${id} file ${map} width`),
          height: positiveInteger(file.height, `set ${id} file ${map} height`),
          bytes: positiveInteger(file.bytes, `set ${id} file ${map} bytes`),
          sha256: file.sha256,
          sourceFiles: Object.freeze((file.sourceFiles as Record<string, unknown>[]).map(source =>
            Object.freeze({ path: source.path as string, sha256: source.sha256 as string }))),
        });
      }
      for (const required of ["color", "normal", "orm"]) {
        if (!maps.has(required)) {
          throw new Error(`terrain-texture-library: set ${id} is missing the ${required} map`);
        }
      }
      if (maps.size !== 3) {
        throw new Error(`terrain-texture-library: set ${id} must carry exactly the color, normal and orm maps`);
      }
      return [...maps.values()];
    })(entry.files);
    nonEmptyString(entry.sourceAssetId, `set ${id} sourceAssetId`);
    nonEmptyString(entry.sourcePage, `set ${id} sourcePage`);
    nonEmptyString(entry.licence, `set ${id} licence`);
    nonEmptyString(entry.licencePage, `set ${id} licencePage`);
    nonEmptyString(entry.author, `set ${id} author`);
    nonEmptyString(entry.tileSizeBasis, `set ${id} tileSizeBasis`);
    nonEmptyString(entry.processing, `set ${id} processing`);
    if (typeof entry.tileSizeM !== "number" || !Number.isFinite(entry.tileSizeM) || entry.tileSizeM <= 0) {
      throw new Error(`terrain-texture-library: set ${id} tileSizeM must be a positive finite number: ${String(entry.tileSizeM)}`);
    }
    return Object.freeze({
      id,
      sourceAssetId: entry.sourceAssetId as string,
      sourcePage: entry.sourcePage as string,
      licence: entry.licence as string,
      licencePage: entry.licencePage as string,
      author: entry.author as string,
      tileSizeM: entry.tileSizeM,
      tileSizeBasis: entry.tileSizeBasis as string,
      processing: entry.processing as string,
      files: Object.freeze(sourceFiles),
    });
  });
  return Object.freeze({ schema: LIBRARY_SCHEMA, sets: Object.freeze(sets) });
}

/** One look per preset of `CITY_GROUND_MATERIAL_RULES_V1` except the water-*
 * presets and `unclassified` (water is T8a). Frozen.
 *
 * `roughnessRange` basis: the only scan-derived grass roughness in the library
 * is the Poly Haven `grass_ground` scan with ORM G-channel mean 0.937, while the
 * authored ambientCG maps measure lower (Grass001 0.544, Grass008 0.710). The
 * remap r' = min + (max - min) * r lifts their means to the scan level while
 * keeping their relative variation: lawn-grass001 0.85 + 0.15 * 0.544 = 0.932,
 * sports-grass008 0.85 + 0.15 * 0.710 = 0.957. The [0, 1] identity stays for
 * every look already at or above the scan (`grass-natural`, `woodland-floor`
 * use the scan itself) and for genuinely glossier surfaces (`artificial-turf`
 * plastic fibres) and all non-grass textured looks. */
export const TERRAIN_PRESET_LOOKS: Readonly<Record<string, TerrainPresetLook>> = Object.freeze({
  "lawn-maintained": Object.freeze({ textureSet: "lawn-grass001", tint: 0xffffff,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0.85, 1] as const), flatReason: null }),
  "grass-generic": Object.freeze({ textureSet: "lawn-grass001", tint: 0xffffff,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0.85, 1] as const), flatReason: null }),
  "grass-natural": Object.freeze({ textureSet: "natural-grass-ground", tint: 0xffffff,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0, 1] as const), flatReason: null }),
  "woodland-floor": Object.freeze({ textureSet: "natural-grass-ground", tint: 0xb4b4a8,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0, 1] as const), flatReason: null }),
  "grass-sports": Object.freeze({ textureSet: "sports-grass008", tint: 0xffffff,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0.85, 1] as const), flatReason: null }),
  "pitch-turf-unknown": Object.freeze({ textureSet: "sports-grass008", tint: 0xffffff,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0.85, 1] as const), flatReason: null }),
  "artificial-turf": Object.freeze({ textureSet: "sports-grass008", tint: 0xd8e8d0,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0, 1] as const), flatReason: null }),
  "soil-brownfield": Object.freeze({ textureSet: "dry-grass-withered", tint: 0xffffff,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0, 1] as const), flatReason: null }),
  "soil-bare": Object.freeze({ textureSet: "soil-park-dirt", tint: 0xffffff,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0, 1] as const), flatReason: null }),
  "soil-construction": Object.freeze({ textureSet: "soil-park-dirt", tint: 0xd6d0c4,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0, 1] as const), flatReason: null }),
  "mulch": Object.freeze({ textureSet: "mulch-ground048", tint: 0xffffff,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0, 1] as const), flatReason: null }),
  "gravel": Object.freeze({ textureSet: "gravel-043", tint: 0xffffff,
    roughness: 1, normalScale: 1, aoIntensity: 0.6,
    roughnessRange: Object.freeze([0, 1] as const), flatReason: null }),
  "grass-paver": Object.freeze({ textureSet: null, tint: 0x708755,
    roughness: 1, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[no grass-paver asset in T4; current explicit_surface grass colour]" }),
  "tartan-track": Object.freeze({ textureSet: null, tint: 0x9a5040,
    roughness: 0.85, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[no track asset in T4]" }),
  "sand": Object.freeze({ textureSet: null, tint: 0xc2b08a,
    roughness: 0.95, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[no sand asset in T4; neutral authored colour]" }),
  "asphalt": Object.freeze({ textureSet: null, tint: 0x53575a,
    roughness: 0.88, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[current explicit_surface asphalt colour]" }),
  "concrete": Object.freeze({ textureSet: null, tint: 0x99958d,
    roughness: 0.94, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[current explicit_surface concrete colour]" }),
  "paving-unit": Object.freeze({ textureSet: null, tint: 0x99958d,
    roughness: 0.94, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[current explicit_surface concrete colour]" }),
  "paving-generic": Object.freeze({ textureSet: null, tint: 0x8a8780,
    roughness: 0.9, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[surface unknown beyond paved; neutral]" }),
  "parking-generic": Object.freeze({ textureSet: null, tint: 0x55595c,
    roughness: 0.88, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[current parking colour]" }),
  "plaza-generic": Object.freeze({ textureSet: null, tint: 0xada79c,
    roughness: 0.9, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[current square colour]" }),
  "court-hard-unknown": Object.freeze({ textureSet: null, tint: 0x8f8f88,
    roughness: 0.9, normalScale: 1, aoIntensity: 0.6, roughnessRange: null,
    flatReason: "[court surface unknown; neutral]" }),
});

/** Throw unless every look satisfies the `roughnessRange` contract: textured
 * looks carry a frozen two-element array of finite numbers with
 * `0 <= min <= max <= 1`, flat looks carry `null`. */
export function validateTerrainPresetLooks(looks: Readonly<Record<string, TerrainPresetLook>>): void {
  for (const [presetId, look] of Object.entries(looks)) {
    const range = look.roughnessRange;
    if (look.textureSet === null) {
      if (range !== null) {
        throw new Error(`terrain-materials: preset ${presetId} roughnessRange must be null for a flat look`);
      }
      continue;
    }
    if (!Array.isArray(range) || range.length !== 2 || !Object.isFrozen(range)
      || !range.every(value => typeof value === "number" && Number.isFinite(value))
      || !(range[0]! >= 0 && range[0]! <= range[1]! && range[1]! <= 1)) {
      throw new Error(`terrain-materials: preset ${presetId} roughnessRange must be a frozen [min, max] with 0 <= min <= max <= 1`);
    }
  }
}
validateTerrainPresetLooks(TERRAIN_PRESET_LOOKS);

/** Presets that must never receive a look from this module. */
function isExcludedPreset(presetId: string): boolean {
  return presetId === UNCLASSIFIED_PRESET || presetId.startsWith(WATER_PREFIX);
}

function lookFor(presetId: string): TerrainPresetLook {
  const look = TERRAIN_PRESET_LOOKS[presetId];
  if (look === undefined) {
    throw new Error(`terrain-materials: preset ${presetId} has no look in TERRAIN_PRESET_LOOKS`);
  }
  return look;
}

export interface TerrainTextureLoaders {
  /** Injected URL resolver: the verified scene path passes `visualAssets.url`. */
  readonly resolveUrl: (path: string) => Promise<string>;
  /** Injected texture loader (tests use a fake). */
  readonly loadTexture: (url: string) => Promise<THREE.Texture>;
  /** GPU anisotropy cap from the renderer capabilities. */
  readonly maxAnisotropy: number;
}

export interface TerrainTextureBundle {
  readonly map: THREE.Texture;
  readonly normalMap: THREE.Texture;
  readonly ormMap: THREE.Texture;
  readonly tileSizeM: number;
}

export interface TerrainTextureSets {
  get(setId: string): TerrainTextureBundle;
  dispose(): void;
}

function configureTexture(texture: THREE.Texture, colorSpace: string,
    maxAnisotropy: number): THREE.Texture {
  texture.colorSpace = colorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.generateMipmaps = true;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.anisotropy = Math.min(8, maxAnisotropy);
  return texture;
}

/** Loads only the requested sets, each file exactly once, with the ticket's
 * colour-space, wrapping, mipmap, filter and anisotropy settings. */
export async function loadTerrainTextureSets(library: TerrainTextureLibrary,
    setIds: readonly string[], loaders: TerrainTextureLoaders): Promise<TerrainTextureSets> {
  const loadedSets: { setId: string; bundle: TerrainTextureBundle }[] = [];
  const setById = new Map(library.sets.map(set => [set.id, set]));
  const textures: THREE.Texture[] = [];
  const requests = [...new Set(setIds)];
  // Every requested id is validated before the first fetch: a later unknown id must not
  // leave textures of earlier sets decoded but undisposed.
  for (const setId of requests) {
    if (!setById.has(setId)) throw new Error(`terrain-materials: texture set ${setId} is not in the library`);
  }
  for (const setId of requests) {
    const set = setById.get(setId)!;
    const byMap = new Map(set.files.map(file => [file.map, file]));
    const load = async (map: string): Promise<THREE.Texture> => {
      const file = byMap.get(map);
      if (file === undefined) {
        throw new Error(`terrain-materials: texture set ${setId} is missing the ${map} map`);
      }
      const url = await loaders.resolveUrl(file.path);
      const texture = await loaders.loadTexture(url);
      textures.push(texture);
      return configureTexture(texture, map === "color" ? THREE.SRGBColorSpace : THREE.NoColorSpace,
        loaders.maxAnisotropy);
    };
    // One failure fails the whole load: settle every in-flight texture of the set first,
    // then release everything decoded so far (previous sets included) and rethrow.
    const settled = await Promise.allSettled([load("color"), load("normal"), load("orm")]);
    const rejected = settled.find((result): result is PromiseRejectedResult => result.status === "rejected");
    if (rejected !== undefined) {
      textures.forEach(texture => texture.dispose());
      textures.length = 0;
      throw rejected.reason;
    }
    const bundle = settled.map(result => (result as PromiseFulfilledResult<THREE.Texture>).value);
    loadedSets.push({ setId, bundle: { map: bundle[0]!, normalMap: bundle[1]!, ormMap: bundle[2]!,
      tileSizeM: set.tileSizeM } });
  }
  const loaded = new Map(loadedSets.map(entry => [entry.setId, entry.bundle]));
  return {
    get(setId: string): TerrainTextureBundle {
      const bundle = loaded.get(setId);
      if (bundle === undefined) {
        throw new Error(`terrain-materials: texture set ${setId} was not loaded`);
      }
      return bundle;
    },
    dispose(): void {
      textures.forEach(texture => texture.dispose());
      textures.length = 0;
      loaded.clear();
    },
  };
}

export interface TerrainMaterials {
  material(presetId: string): THREE.MeshStandardMaterial;
  dispose(): void;
}

export interface TerrainMaterialsTextures {
  get(setId: string): TerrainTextureBundle;
}

/** One shared unpatched `MeshStandardMaterial` per preset. The same preset
 * always returns the same instance. Asking for `unclassified` or a `water-*`
 * preset throws. `textures` must carry every texture set the requested looks
 * reference. If a later preset's construction throws, every material already
 * created by this call is disposed before the error is rethrown. */
export function createTerrainMaterials(textures: TerrainMaterialsTextures,
    presetIds: readonly string[]): TerrainMaterials {
  const materials = new Map<string, THREE.MeshStandardMaterial>();
  try {
    for (const presetId of presetIds) {
      if (isExcludedPreset(presetId)) {
        throw new Error(`terrain-materials: preset ${presetId} must not receive a terrain material`);
      }
      if (materials.has(presetId)) continue;
      const look = lookFor(presetId);
      if (look.textureSet === null) {
        materials.set(presetId, new THREE.MeshStandardMaterial({
          name: `terrain:${presetId}`,
          color: 0xffffff,
          roughness: look.roughness,
          metalness: 0,
          vertexColors: true,
        }));
        continue;
      }
      const bundle = textures.get(look.textureSet);
      materials.set(presetId, new THREE.MeshStandardMaterial({
        name: `terrain:${presetId}`,
        color: 0xffffff,
        roughness: look.roughness,
        metalness: 0,
        vertexColors: true,
        map: bundle.map,
        normalMap: bundle.normalMap,
        normalScale: new THREE.Vector2(look.normalScale, look.normalScale),
        roughnessMap: bundle.ormMap,
        aoMap: bundle.ormMap,
        aoMapIntensity: look.aoIntensity,
      }));
    }
  } catch (error) {
    materials.forEach(material => material.dispose());
    materials.clear();
    throw error;
  }
  return {
    material(presetId: string): THREE.MeshStandardMaterial {
      const material = materials.get(presetId);
      if (material === undefined) {
        throw new Error(`terrain-materials: no material was created for preset ${presetId}`);
      }
      return material;
    },
    dispose(): void {
      materials.forEach(material => material.dispose());
      materials.clear();
    },
  };
}

export interface TerrainVertexAttributes {
  readonly uv: Float32Array;
  readonly color: Float32Array;
}

/** Linear-space RGB of the look tint after the assignment's hue and value
 * shifts; identical for every vertex of the polygon. */
function variedTint(look: TerrainPresetLook, assignment: GroundMaterialAssignment): THREE.Color {
  const color = new THREE.Color(look.tint);
  color.offsetHSL(assignment.variation.hueShiftDeg / 360, 0, 0);
  const factor = 1 + assignment.variation.valueShift;
  color.r = Math.min(1, Math.max(0, color.r * factor));
  color.g = Math.min(1, Math.max(0, color.g * factor));
  color.b = Math.min(1, Math.max(0, color.b * factor));
  return color;
}

/** Metre-based UVs and the per-polygon vertex colour for one ground polygon.
 * UVs rotate the ground-plane (x, z) by the assignment's rotation about the
 * world origin, then divide by `tileSizeM` and apply the assignment's offsets.
 * `tileSizeM` is the texture set's for textured looks, the assignment's for
 * flat looks. */
export function terrainVertexAttributes(positions: ArrayLike<number>,
    assignment: GroundMaterialAssignment, look: TerrainPresetLook,
    tileSizeM: number): TerrainVertexAttributes {
  if (!Number.isFinite(tileSizeM) || tileSizeM <= 0) {
    throw new Error(`terrain-materials: tileSizeM must be a positive finite number: ${String(tileSizeM)}`);
  }
  if (positions.length % 3 !== 0) {
    throw new Error("terrain-materials: positions must be xyz triples");
  }
  const cos = Math.cos(assignment.variation.rotationRad);
  const sin = Math.sin(assignment.variation.rotationRad);
  const vertexCount = positions.length / 3;
  const uv = new Float32Array(vertexCount * 2);
  for (let index = 0; index < vertexCount; index++) {
    const x = positions[index * 3]!;
    const z = positions[index * 3 + 2]!;
    const rotatedU = x * cos - z * sin;
    const rotatedV = x * sin + z * cos;
    uv[index * 2] = rotatedU / tileSizeM + assignment.variation.offsetU;
    uv[index * 2 + 1] = rotatedV / tileSizeM + assignment.variation.offsetV;
  }
  const tint = variedTint(look, assignment);
  const color = new Float32Array(vertexCount * 3);
  for (let index = 0; index < vertexCount; index++) {
    color[index * 3] = tint.r;
    color[index * 3 + 1] = tint.g;
    color[index * 3 + 2] = tint.b;
  }
  return { uv, color };
}

/** Writes the metre-based UVs and per-polygon vertex colour onto a
 * `BufferGeometry` built from world positions. */
export function applyTerrainAttributes(geometry: THREE.BufferGeometry,
    assignment: GroundMaterialAssignment, look: TerrainPresetLook,
    tileSizeM: number): void {
  const position = geometry.getAttribute("position");
  if (position === undefined) {
    throw new Error("terrain-materials: geometry needs a position attribute");
  }
  const { uv, color } = terrainVertexAttributes(position.array as ArrayLike<number>,
    assignment, look, tileSizeM);
  geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  geometry.setAttribute("color", new THREE.BufferAttribute(color, 3));
}
