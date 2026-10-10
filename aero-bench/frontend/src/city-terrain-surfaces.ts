import * as THREE from "three";
import { assignGroundMaterial, compileGroundMaterialRules, CITY_GROUND_MATERIAL_RULES_V1,
  type GroundMaterialAssignment, type GroundMaterialFamily,
  type GroundMaterialInput } from "./city-ground-material-rules";
import { createTerrainMaterials, loadTerrainTextureSets, parseTerrainTextureLibrary,
  TERRAIN_PRESET_LOOKS, TERRAIN_TEXTURE_LIBRARY_URL, terrainVertexAttributes,
  type TerrainTextureSets } from "./city-terrain-materials";
import { createWaterMaterials, WATER_PRESET_LOOKS } from "./city-water-material";
import { applySurfaceWetness, type SurfaceWetnessOptions,
  type SurfaceWetnessUniforms } from "./city-surface-wetness";
import { applyTerrainDetail } from "./city-terrain-detail";
import { applyWaterSurface, type WaterSurfaceUniforms } from "./city-water-surface";
import { assertNotAborted, readBoundedResponse } from "./verified-bytes";

/**
 * Terrain surface kit: one shared material per material preset for every ground
 * polygon (source ground covers, lawn and woodland floor), chosen by the versioned
 * ground material rules; textured looks get wetness first, then the T5b
 * hex-cell anti-tiling detail patch (flat looks, water and unclassified stay plain).
 * Textures come only from the pinned terrain texture
 * library, each file verified against the manifest's byte count and sha256.
 */

/** sha256 of `/textures/terrain-v1/manifest.json` written by
 * `validation/terrain-realism-20261001/T5a/prepare-terrain-textures.py`. */
export const TERRAIN_TEXTURE_LIBRARY_SHA256 = "a98a663c9b5d01a61da78bf02391ddc1f83ca7addc1e6cd32d13e892003ae45d";
/** Authored seed for per-polygon texture offset, rotation and tint variation. */
export const CITY_TERRAIN_MATERIAL_SEED = 20261001;
const LIBRARY_MAX_BYTES = 1_000_000;
const COMPILED_RULES = compileGroundMaterialRules(CITY_GROUND_MATERIAL_RULES_V1);

/** Unclassified ground stays visibly neutral and untextured; it is never painted as a guess. */
const UNCLASSIFIED_COLOR = 0x7d7b74;

/** Wet-surface response by physical family; water is excluded (it is already a wet surface). */
const WETNESS_BY_FAMILY: Readonly<Record<Exclude<GroundMaterialFamily, "water">, SurfaceWetnessOptions>> = {
  grass: { maxDarkening: 0.3, minRoughness: 0.55 },
  woodland_floor: { maxDarkening: 0.3, minRoughness: 0.55 },
  artificial_turf: { maxDarkening: 0.24, minRoughness: 0.6 },
  soil: { maxDarkening: 0.24, minRoughness: 0.6 },
  sand: { maxDarkening: 0.24, minRoughness: 0.6 },
  gravel: { maxDarkening: 0.24, minRoughness: 0.6 },
  mulch: { maxDarkening: 0.24, minRoughness: 0.6 },
  synthetic_track: { maxDarkening: 0.4, minRoughness: 0.22 },
  paving_asphalt: { maxDarkening: 0.4, minRoughness: 0.22 },
  paving_concrete: { maxDarkening: 0.4, minRoughness: 0.22 },
  paving_unit: { maxDarkening: 0.4, minRoughness: 0.22 },
  paving_generic: { maxDarkening: 0.4, minRoughness: 0.22 },
  unclassified: { maxDarkening: 0.4, minRoughness: 0.22 },
};

/** Mean albedo per texture set of the pinned library, from
 * `validation/terrain-realism-20261001/T9a/measure-texture-means.py` (texture-means.json). */
const TEXTURE_SET_MEAN_SRGB: Readonly<Record<string, number>> = Object.freeze({
  "dry-grass-withered": 0xad957b, "gravel-043": 0x6d6e69, "lawn-grass001": 0x455d29,
  "mulch-ground048": 0x564035, "natural-grass-ground": 0x6f613f, "shoreline-ground083": 0x948144,
  "soil-park-dirt": 0x7a663e, "sports-grass008": 0x6c843e,
});

/** Untextured colour of an assignment for paths that cannot load the texture library:
 * the measured texture mean times the look tint, the flat look colour, the water preset
 * colour, or the neutral unclassified colour. */
export function terrainFlatColor(assignment: GroundMaterialAssignment): THREE.Color {
  if (assignment.family === "unclassified") return new THREE.Color(UNCLASSIFIED_COLOR);
  if (assignment.family === "water") {
    const look = WATER_PRESET_LOOKS[assignment.preset];
    if (look === undefined) throw new Error(`Water preset has no look: ${assignment.preset}`);
    return new THREE.Color(look.color);
  }
  const look = TERRAIN_PRESET_LOOKS[assignment.preset];
  if (look === undefined) throw new Error(`Terrain preset has no look: ${assignment.preset}`);
  if (look.textureSet === null) return new THREE.Color(look.tint);
  const mean = TEXTURE_SET_MEAN_SRGB[look.textureSet];
  if (mean === undefined) throw new Error(`Terrain texture set has no measured mean: ${look.textureSet}`);
  return new THREE.Color(mean).multiply(new THREE.Color(look.tint));
}

export function assignCityGroundMaterial(input: GroundMaterialInput): GroundMaterialAssignment {
  return assignGroundMaterial(input, COMPILED_RULES, CITY_TERRAIN_MATERIAL_SEED);
}

/** Texture sets the given assignments need, sorted. */
export function terrainTextureSetIds(assignments: readonly GroundMaterialAssignment[]): string[] {
  const ids = new Set<string>();
  for (const assignment of assignments) {
    if (assignment.family === "water" || assignment.family === "unclassified") continue;
    const look = TERRAIN_PRESET_LOOKS[assignment.preset];
    if (look === undefined) throw new Error(`Terrain preset has no look: ${assignment.preset}`);
    if (look.textureSet !== null) ids.add(look.textureSet);
  }
  return [...ids].sort();
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes.buffer.slice(bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

async function fetchPublicBytes(url: string, maxBytes: number, signal?: AbortSignal): Promise<Uint8Array> {
  assertNotAborted(signal);
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Terrain texture request failed: ${url} status ${response.status}`);
  return new Uint8Array(await readBoundedResponse(response, maxBytes, "Terrain texture", signal));
}

async function decodeWebpTexture(bytes: Uint8Array): Promise<THREE.Texture> {
  const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type: "image/webp" }));
  try { return await new THREE.TextureLoader().loadAsync(url); } finally { URL.revokeObjectURL(url); }
}

export interface TerrainTextureLoadOptions {
  readonly setIds: readonly string[];
  readonly maxAnisotropy: number;
  readonly signal?: AbortSignal;
  /** Injectable transport and decoder for tests. */
  readonly fetchBytes?: (url: string, maxBytes: number, signal?: AbortSignal) => Promise<Uint8Array>;
  readonly decodeTexture?: (bytes: Uint8Array) => Promise<THREE.Texture>;
}

/** Load the requested sets from the pinned library; any byte or digest mismatch rejects the load. */
export async function loadVerifiedTerrainTextureSets(options: TerrainTextureLoadOptions): Promise<TerrainTextureSets> {
  const fetchBytes = options.fetchBytes ?? fetchPublicBytes;
  const decode = options.decodeTexture ?? decodeWebpTexture;
  const manifestBytes = await fetchBytes(TERRAIN_TEXTURE_LIBRARY_URL, LIBRARY_MAX_BYTES, options.signal);
  const manifestDigest = await sha256Hex(manifestBytes);
  if (manifestDigest !== TERRAIN_TEXTURE_LIBRARY_SHA256) {
    throw new Error(`Terrain texture library sha256 mismatch: expected ${TERRAIN_TEXTURE_LIBRARY_SHA256}, received ${manifestDigest}`);
  }
  const library = parseTerrainTextureLibrary(JSON.parse(new TextDecoder().decode(manifestBytes)));
  const files = new Map(library.sets.flatMap(set => set.files.map(file => [file.path, file] as const)));
  const verified = new Map<string, Uint8Array>();
  return loadTerrainTextureSets(library, options.setIds, {
    maxAnisotropy: options.maxAnisotropy,
    // The path is the key; the bytes are fetched, sized and hashed before any decode.
    resolveUrl: async path => {
      const file = files.get(path);
      if (file === undefined) throw new Error(`Terrain texture is not in the library: ${path}`);
      const bytes = await fetchBytes(path, file.bytes, options.signal);
      if (bytes.byteLength !== file.bytes) {
        throw new Error(`Terrain texture size mismatch: ${path} expected ${file.bytes}, received ${bytes.byteLength}`);
      }
      const digest = await sha256Hex(bytes);
      if (digest !== file.sha256) throw new Error(`Terrain texture sha256 mismatch: ${path}`);
      verified.set(path, bytes);
      return path;
    },
    loadTexture: async path => {
      const bytes = verified.get(path);
      if (bytes === undefined) throw new Error(`Terrain texture was not verified: ${path}`);
      verified.delete(path);
      return decode(bytes);
    },
  });
}

export type TerrainSurfaceMaterial = THREE.MeshStandardMaterial | THREE.MeshPhysicalMaterial;

export interface TerrainSurfaceKit {
  material(assignment: GroundMaterialAssignment): TerrainSurfaceMaterial;
  /** Per-vertex uv and colour for one polygon's world positions; null when the material takes none. */
  attributes(positions: Float32Array, assignment: GroundMaterialAssignment):
    { readonly uv: Float32Array; readonly color: Float32Array } | null;
  readonly materials: readonly TerrainSurfaceMaterial[];
  /** Releases materials and textures. Meshes using them must be disposed first. */
  dispose(): void;
}

/** Build the shared materials for the given assignments, with wetness applied once per
 * non-water material and the T8b water surface applied once per water material. If building a
 * later preset's material throws, everything this call created (terrain preset materials, water
 * materials, the unclassified material) is disposed before the error is rethrown; the texture
 * sets stay owned by the caller. */
export function createTerrainSurfaceKit(assignments: readonly GroundMaterialAssignment[],
    textures: TerrainTextureSets, wetness: SurfaceWetnessUniforms,
    waterSurface: WaterSurfaceUniforms): TerrainSurfaceKit {
  const presets = [...new Set(assignments.map(assignment => assignment.preset))].sort();
  const familyOf = new Map(assignments.map(assignment => [assignment.preset, assignment.family]));
  let terrain: ReturnType<typeof createTerrainMaterials> | null = null;
  let water: ReturnType<typeof createWaterMaterials> | null = null;
  let unclassified: THREE.MeshStandardMaterial | null = null;
  try {
    const terrainPresets = presets.filter(preset => familyOf.get(preset) !== "water" && familyOf.get(preset) !== "unclassified");
    terrain = createTerrainMaterials(textures, terrainPresets);
    water = createWaterMaterials();
    unclassified = new THREE.MeshStandardMaterial({ name: "terrain:unclassified",
      color: UNCLASSIFIED_COLOR, roughness: 0.95, metalness: 0 });
    unclassified.userData.terrainPreset = "unclassified";
    const byPreset = new Map<string, TerrainSurfaceMaterial>();
    for (const preset of presets) {
      const family = familyOf.get(preset)!;
      const material = family === "water" ? water.material(preset)
        : family === "unclassified" ? unclassified : terrain.material(preset);
      if (family !== "water") applySurfaceWetness(material, wetness, WETNESS_BY_FAMILY[family]);
      else applyWaterSurface(material as THREE.MeshPhysicalMaterial, preset, waterSurface);
      // Textured looks only: wetness composes with the detail patch, which needs all four maps.
      const look = family === "water" || family === "unclassified" ? undefined : TERRAIN_PRESET_LOOKS[preset];
      if (look !== undefined && look.textureSet !== null) {
        // The look table validation guarantees a textured look carries a range.
        applyTerrainDetail(material as THREE.MeshStandardMaterial,
          textures.get(look.textureSet).tileSizeM, look.roughnessRange!);
      }
      material.userData.terrainPreset = preset;
      byPreset.set(preset, material);
    }
    const used = new Set(byPreset.values());
    return {
      materials: [...used],
      material: assignment => {
        const material = byPreset.get(assignment.preset);
        if (material === undefined) throw new Error(`Terrain surface kit has no material for preset ${assignment.preset}`);
        return material;
      },
      attributes: (positions, assignment) => {
        if (assignment.family === "water" || assignment.family === "unclassified") return null;
        const look = TERRAIN_PRESET_LOOKS[assignment.preset]!;
        const tileSizeM = look.textureSet === null ? assignment.tileSizeM : textures.get(look.textureSet).tileSizeM;
        return terrainVertexAttributes(positions, assignment, look, tileSizeM);
      },
      dispose: () => {
        terrain!.dispose(); water!.dispose(); unclassified!.dispose(); textures.dispose();
      },
    };
  } catch (error) {
    if (terrain !== null) terrain.dispose();
    if (water !== null) water.dispose();
    if (unclassified !== null) unclassified.dispose();
    throw error;
  }
}

/** Upward-facing ground geometry for one material: triangles are CCW in XZ, which faces down in XYZ. */
export function terrainSurfaceGeometry(polygons: readonly {
  readonly triangles: readonly (readonly (readonly [number, number])[])[];
  readonly assignment: GroundMaterialAssignment;
}[], y: number, kit: TerrainSurfaceKit): THREE.BufferGeometry {
  const positions: number[] = [], uvs: number[] = [], colors: number[] = [];
  let attributed: boolean | null = null;
  for (const polygon of polygons) {
    const local: number[] = [];
    for (const triangle of polygon.triangles) for (const point of [...triangle].reverse()) local.push(point[0], y, point[1]);
    const values = kit.attributes(new Float32Array(local), polygon.assignment);
    if (attributed !== null && attributed !== (values !== null)) {
      throw new Error("Terrain surface polygons sharing a material disagree on vertex attributes");
    }
    attributed = values !== null;
    // Element-wise appends: spreading a large patch into push() can exceed the argument limit.
    for (const value of local) positions.push(value);
    if (values !== null) {
      for (const value of values.uv) uvs.push(value);
      for (const value of values.color) colors.push(value);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  if (attributed === true) {
    geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
    geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  }
  geometry.computeVertexNormals();
  return geometry;
}
