/** OSM2World mesh-pack loader; original source contract retained. */
interface GeographicOrigin { readonly latitude_deg: number; readonly longitude_deg: number; }
type StaticLayer = "buildings" | "roads" | "terrain";
interface TraceTarget { readonly kind: "building" | "road"; readonly id: string; }

export const MESH_PACK_SCHEMA = "aero-bench.osm2world-mesh-pack/v1";
export const MAX_PACK_CHUNK_BYTES = 64 * 1024 * 1024;
/** Opaque file reference: the name is not a content hash and is never checked. */
export interface PackFile { readonly asset_id: string; readonly size_bytes: number; }
export interface PackedMaterial {
  readonly color: readonly [number, number, number];
  readonly base_color_texture: string | null;
  readonly normal_texture: string | null;
  readonly orm_texture: string | null;
  readonly opacity_texture: string | null;
  readonly transparent: boolean;
  readonly clamp: boolean;
}
export interface PackedRange { readonly end: number; readonly target: TraceTarget | null; }
export interface PackedBatch {
  readonly file: PackFile;
  readonly vertices: number;
  readonly indices: number;
  readonly layer: StaticLayer;
  readonly material: PackedMaterial;
  readonly ranges: readonly PackedRange[];
}
export interface PackedObject { readonly id: string; readonly tags: Readonly<Record<string, string>>; }
export interface PackCoordinateContract {
  readonly schema_version: "aero-bench.osm2world-source-coordinates/v1";
  readonly recipe: "source-node-bounds-local-Mercator-then-declared-origin-translation";
  readonly converter_origin: GeographicOrigin;
  readonly stored_translation_xz_m: readonly [number, number];
  readonly earth_circumference_m: 40075016.686;
  readonly native_point_quantization_m: 0.001;
  readonly storage: "source-mesh-float32-converter-coordinates-then-declared-origin-translation";
}
export interface MeshPackManifest {
  readonly schema_version: typeof MESH_PACK_SCHEMA;
  readonly source: PackFile;
  readonly generator: { readonly revision: string };
  readonly projection: { readonly name: "MetricMapProjection"; readonly axes: "east-up-south"; readonly origin: GeographicOrigin };
  readonly coordinate_contract: PackCoordinateContract;
  readonly extent: { readonly west: number; readonly east: number; readonly south: number; readonly north: number };
  readonly original_mesh_count: number;
  readonly batches: readonly PackedBatch[];
  readonly objects: readonly PackedObject[];
  readonly textures: Readonly<Record<string, PackFile>>;
}

/** Extra keys are tolerated: older files may still carry obsolete hash annotations. */
function object(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw Error(`${label} must be an object`);
  const result = value as Record<string, unknown>;
  if (keys.some(key => !(key in result))) throw Error(`${label} fields must match the pack contract`);
  return result;
}
function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw Error(`${label} must be finite`);
  return value;
}
function integer(value: unknown, minimum: number, maximum: number, label: string): number {
  const result = finite(value, label);
  if (!Number.isSafeInteger(result) || result < minimum || result > maximum) throw Error(`${label} is outside its integer bound`);
  return result;
}
function assetId(value: unknown): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/.test(value) || typeof value==="string" && value.split("/").some(part=>part===".." || part==="." || !part)) throw Error("pack asset id is invalid");
  return value;
}
function file(value: unknown): PackFile {
  const ref = object(value, ["size_bytes"], "pack file");
  return { asset_id: assetId(ref.asset_id ?? ref.sha256), size_bytes: integer(ref.size_bytes, 1, MAX_PACK_CHUNK_BYTES, "pack file size") };
}
function texture(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !/^\/osm2world\/style\/textures\/[a-zA-Z0-9_./-]+$/.test(value) || value.slice(1).split("/").some(part => part === ".." || part === "." || part === "")) throw Error("pack texture must be a bundled style path");
  return value;
}
function target(value: unknown): TraceTarget | null {
  if (value === null) return null;
  const result = object(value, ["kind", "id"], "pack target");
  if ((result.kind !== "building" && result.kind !== "road") || typeof result.id !== "string" || !/^[a-zA-Z0-9_.:-]{1,256}$/.test(result.id)) throw Error("pack target identity is invalid");
  return { kind: result.kind, id: result.id };
}
export function parseMeshPack(value: unknown): MeshPackManifest {
  const root = object(value, ["schema_version", "source", "generator", "projection", "coordinate_contract", "extent", "original_mesh_count", "batches", "objects", "textures"], "mesh pack");
  if (root.schema_version !== MESH_PACK_SCHEMA) throw Error("unsupported mesh pack schema");
  const generator = object(root.generator, ["revision"], "pack generator");
  if (typeof generator.revision !== "string" || !generator.revision.trim()) throw Error("pack upstream revision is invalid");
  const projection = object(root.projection, ["name", "axes", "origin"], "pack projection");
  if (projection.name !== "MetricMapProjection" || projection.axes !== "east-up-south") throw Error("unsupported pack projection");
  const origin = object(projection.origin, ["latitude_deg", "longitude_deg"], "pack origin");
  const latitude = finite(origin.latitude_deg, "latitude"), longitude = finite(origin.longitude_deg, "longitude");
  if (Math.abs(latitude) >= 90 || Math.abs(longitude) > 180) throw Error("pack origin is outside geographic bounds");
  const source = file(root.source);
  const coordinates = object(root.coordinate_contract, ["schema_version", "recipe", "converter_origin",
    "stored_translation_xz_m", "earth_circumference_m", "native_point_quantization_m", "storage"], "pack source coordinates");
  if (coordinates.schema_version !== "aero-bench.osm2world-source-coordinates/v1"
      || coordinates.recipe !== "source-node-bounds-local-Mercator-then-declared-origin-translation"
      || coordinates.earth_circumference_m !== 40075016.686 || coordinates.native_point_quantization_m !== 0.001
      || coordinates.storage !== "source-mesh-float32-converter-coordinates-then-declared-origin-translation") {
    throw Error("unsupported pack source coordinate contract");
  }
  const converter = object(coordinates.converter_origin, ["latitude_deg", "longitude_deg"], "pack converter origin");
  const converterLatitude = finite(converter.latitude_deg, "converter latitude");
  const converterLongitude = finite(converter.longitude_deg, "converter longitude");
  if (Math.abs(converterLatitude) >= 90 || Math.abs(converterLongitude) > 180) throw Error("pack converter origin is outside geographic bounds");
  if (!Array.isArray(coordinates.stored_translation_xz_m) || coordinates.stored_translation_xz_m.length !== 2) {
    throw Error("pack source translation must contain x/z metres");
  }
  const sourceCoordinates: PackCoordinateContract = {
    schema_version: "aero-bench.osm2world-source-coordinates/v1",
    recipe: "source-node-bounds-local-Mercator-then-declared-origin-translation",
    converter_origin: { latitude_deg: converterLatitude, longitude_deg: converterLongitude },
    stored_translation_xz_m: [finite(coordinates.stored_translation_xz_m[0], "pack source translation x"),
      finite(coordinates.stored_translation_xz_m[1], "pack source translation z")],
    earth_circumference_m: 40075016.686, native_point_quantization_m: 0.001,
    storage: "source-mesh-float32-converter-coordinates-then-declared-origin-translation",
  };
  const rawExtent = object(root.extent, ["west", "east", "south", "north"], "pack extent");
  const extent = { west: finite(rawExtent.west, "west"), east: finite(rawExtent.east, "east"), south: finite(rawExtent.south, "south"), north: finite(rawExtent.north, "north") };
  if (extent.west >= extent.east || extent.south >= extent.north) throw Error("pack extent is inverted");
  if (!Array.isArray(root.batches) || !root.batches.length || root.batches.length > 4096) throw Error("pack batches exceed declared bound");
  const batches: PackedBatch[] = root.batches.map(raw => {
    const batch = object(raw, ["file", "vertices", "indices", "layer", "material", "ranges"], "pack batch");
    const ref = file(batch.file);
    const vertices = integer(batch.vertices, 3, MAX_PACK_CHUNK_BYTES / 32, "vertices");
    const indices = integer(batch.indices, 3, MAX_PACK_CHUNK_BYTES / 4, "indices");
    if (indices % 3 || ref.size_bytes !== vertices * 32 + indices * 4) throw Error("pack buffer layout differs from declared counts");
    if (batch.layer !== "buildings" && batch.layer !== "roads" && batch.layer !== "terrain") throw Error("unknown pack layer");
    const mat = object(batch.material, ["color", "base_color_texture", "normal_texture", "orm_texture", "opacity_texture", "transparent", "clamp"], "pack material");
    if (!Array.isArray(mat.color) || mat.color.length !== 3 || mat.color.some(channel => typeof channel !== "number" || !Number.isFinite(channel) || channel < 0 || channel > 1)) throw Error("invalid pack color");
    if (typeof mat.transparent !== "boolean" || typeof mat.clamp !== "boolean") throw Error("invalid pack material flags");
    if (!Array.isArray(batch.ranges) || !batch.ranges.length || batch.ranges.length > indices / 3) throw Error("invalid pack ranges");
    let previous = 0;
    const ranges = batch.ranges.map(rawRange => {
      const range = object(rawRange, ["end", "target"], "pack range");
      const end = integer(range.end, previous + 1, indices / 3, "range end");
      previous = end;
      return { end, target: target(range.target) };
    });
    if (previous !== indices / 3) throw Error("pack ranges must close the triangle inventory");
    return { file: ref, vertices, indices, layer: batch.layer, ranges, material: { color: mat.color as [number, number, number], base_color_texture: texture(mat.base_color_texture), normal_texture: texture(mat.normal_texture), orm_texture: texture(mat.orm_texture), opacity_texture: texture(mat.opacity_texture), transparent: mat.transparent, clamp: mat.clamp } };
  });
  if (!Array.isArray(root.objects) || root.objects.length > 100_000) throw Error("pack object inventory exceeds bound");
  const ids = new Set<string>();
  const objects = root.objects.map(raw => {
    const item = object(raw, ["id", "tags"], "pack object");
    if (typeof item.id !== "string" || !/^[nwr]-?\d+$/.test(item.id) || ids.has(item.id)) throw Error("pack OSM object identity must be unique");
    ids.add(item.id);
    if (item.tags === null || typeof item.tags !== "object" || Array.isArray(item.tags) || Object.values(item.tags).some(tag => typeof tag !== "string")) throw Error("invalid pack object tags");
    return { id: item.id, tags: item.tags as Record<string, string> };
  });
  if (root.textures === null || typeof root.textures !== "object" || Array.isArray(root.textures) || Object.keys(root.textures).length > 4096) throw Error("invalid pack texture inventory");
  const textures: Record<string, PackFile> = {};
  for (const [path, reference] of Object.entries(root.textures)) {
    texture(path);
    textures[path] = file(reference);
  }
  for (const batch of batches) for (const path of [batch.material.base_color_texture, batch.material.normal_texture, batch.material.orm_texture, batch.material.opacity_texture]) {
    if (path !== null && textures[path] === undefined) throw Error("pack material references an undeclared texture");
  }
  return {
    schema_version: MESH_PACK_SCHEMA, source, coordinate_contract: sourceCoordinates,
    generator: { revision: generator.revision },
    projection: { name: "MetricMapProjection", axes: "east-up-south", origin: { latitude_deg: latitude, longitude_deg: longitude } },
    extent, original_mesh_count: integer(root.original_mesh_count, 1, 1_000_000, "original mesh count"), batches, objects, textures,
  };
}

export function unpackMeshBatch(batch: PackedBatch, bytes: ArrayBuffer) {
  if (bytes.byteLength !== batch.file.size_bytes) throw Error("pack chunk size mismatch");
  // Each typed view must lie inside the received buffer; construction throws on
  // out-of-bounds offsets, so sizes are re-derived here for an exact message.
  if (batch.vertices * 32 + batch.indices * 4 > bytes.byteLength) throw Error("pack chunk is shorter than its declared layout");
  const positions = new Float32Array(bytes, 0, batch.vertices * 3);
  const normals = new Float32Array(bytes, batch.vertices * 12, batch.vertices * 3);
  const uvs = new Float32Array(bytes, batch.vertices * 24, batch.vertices * 2);
  const indices = new Uint32Array(bytes, batch.vertices * 32, batch.indices);
  if ([positions, normals, uvs].some(values => values.some(value => !Number.isFinite(value)))) throw Error("pack buffer contains nonfinite geometry");
  if (indices.some(index => index >= batch.vertices)) throw Error("pack triangle references a missing vertex");
  return { positions, normals, uvs, indices };
}
