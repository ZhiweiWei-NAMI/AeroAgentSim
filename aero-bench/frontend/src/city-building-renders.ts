/**
 * Production loading path for the validated per-building render GLBs.
 *
 * Schema v2 (deduplicated runtime): every building is a geometry-only GLB
 * fetched from a content-addressed path through AssetResolver (size + SHA-256
 * verified before parsing) and placed at its recorded ENU anchor with identity
 * rotation. Style textures are stored exactly once in the shared texture store;
 * each GLB references them through `assets/<sha256>` image URIs which are
 * fetched through the same digest-verified path and injected into GLTFLoader as
 * blob URLs, so no unverified bytes ever reach the renderer. Shared images are
 * deduplicated into one THREE.Texture per unique image digest. The manifest
 * binds every building to its canonical source digest (`glb`) and the exact
 * derived bytes it loads (`derived_glb`).
 */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { AssetResolver, type DeclaredAsset } from "./asset-resolver";
import type { CityTimeOfDay } from "./city-lighting-calibration";
import { CITY_SUBSYSTEM_LIGHTING } from "./city-subsystem-lighting";
import { collisionBox } from "./city-presentation";
import { cacheStaticTransforms } from "./city-rendering";
import { BuildingRenderBatches } from "./city-building-batch";
import { runInFrameSlices, waitForGpuCommands } from "./city-asset-cache";
import type { BuildingPlacementSource } from "./city-workspace-geometry";

export const BUILDING_RENDER_SCHEMA = "aero-bench.building-render-runtime/v2";
export const BUILDING_RENDER_PLACEMENT_RULE =
  "ENU_east = anchor_east_m + X; ENU_north = anchor_north_m - Z; ENU_up = base_enu_up_m + Y";
export const BUILDING_RENDER_ART_SCHEMA = "aero-bench.building-render-art-detail/v1";
export const BUILDING_RENDER_ART_CLASS = "derived_render_art_only_not_survey";

export interface BuildingRenderOrigin {
  readonly latitude_deg: number;
  readonly longitude_deg: number;
  readonly ellipsoid_height_m: number;
  readonly amsl_m: number;
  readonly geoid_undulation_m: number;
}

export interface BuildingRenderArtProfile {
  readonly schema: typeof BUILDING_RENDER_ART_SCHEMA;
  readonly class: typeof BUILDING_RENDER_ART_CLASS;
  readonly disclaimer_en: string;
  readonly disclaimer_zh: string;
  readonly note: string;
  readonly layers: { readonly roof_detail: boolean; readonly facade_relief: boolean };
}

export interface BuildingRenderArtEntry {
  readonly bays: number;
  readonly parapet: boolean;
  readonly parapet_height_m: number;
  readonly parapet_thickness_m: number;
  readonly relief: boolean;
  readonly relief_edges: number;
  readonly roof_structures: number;
  readonly triangles: number;
  readonly vertices: number;
}

export interface BuildingRenderGlbRef {
  readonly path: string;
  readonly sha256: string;
  readonly bytes: number;
}

export interface BuildingRenderEnvelope {
  readonly min_e: number;
  readonly min_n: number;
  readonly max_e: number;
  readonly max_n: number;
  readonly base_up: number;
  readonly top_up: number;
}

export interface BuildingRenderEntry {
  readonly object_id: string;
  readonly osm: { readonly type: "way" | "relation"; readonly id: number };
  /** Canonical validated source GLB binding (digest evidence; not fetched at runtime). */
  readonly glb: BuildingRenderGlbRef;
  /** Fetched render GLB; canonical placement is preserved and render art is explicitly declared. */
  readonly derived_glb: BuildingRenderGlbRef;
  readonly anchor_east_m: number;
  readonly anchor_north_m: number;
  readonly base_enu_up_m: number;
  readonly height_m: number;
  readonly envelope: BuildingRenderEnvelope;
  readonly style_id: string;
  readonly block: number;
  readonly pack_target_ids: readonly string[];
  readonly art_detail?: BuildingRenderArtEntry;
}

export interface BuildingRenderTextureRef {
  readonly sha256: string;
  readonly bytes: number;
  readonly media_type: string;
}

export interface BuildingRenderBlock {
  readonly index: number;
  readonly min_e: number;
  readonly max_e: number;
  readonly min_n: number;
  readonly max_n: number;
  readonly object_ids: readonly string[];
}

export interface BuildingRenderManifest {
  readonly schema_version: typeof BUILDING_RENDER_SCHEMA;
  readonly scene: {
    readonly id: string;
    readonly coordinate_frame: "ENU";
    readonly origin_wgs84: BuildingRenderOrigin;
    readonly objects_json_sha256: string;
    readonly placement_rule: string;
    readonly mesh_pack_manifest_sha256: string;
    readonly mesh_pack_source_sha256: string;
  };
  readonly sources: {
    readonly scaleout_glb_combined_sha256: string;
    readonly scaleout_manifest_sha256: string;
    readonly catalog_fragment_sha256: string;
  };
  readonly art_detail?: BuildingRenderArtProfile;
  readonly counts: {
    readonly buildings: number;
    /** Canonical source bytes (provenance). */
    readonly total_bytes: number;
    readonly derived_total_bytes: number;
    readonly texture_bytes: number;
    readonly textures: number;
    readonly styles: number;
    readonly pack_linked_objects: number;
    readonly pack_missing_objects: number;
    readonly pack_targets: number;
    readonly blocks: number;
    readonly source_vertices?: number;
    readonly source_triangles?: number;
    readonly derived_vertices?: number;
    readonly derived_triangles?: number;
    readonly art_detail_buildings?: number;
    readonly art_detail_relief_buildings?: number;
    readonly art_detail_roofs?: number;
    readonly art_detail_structures?: number;
  };
  readonly pack_missing_object_ids: readonly string[];
  readonly block_size_m: number;
  readonly textures: readonly BuildingRenderTextureRef[];
  readonly blocks: readonly BuildingRenderBlock[];
  readonly buildings: readonly BuildingRenderEntry[];
}

export interface BuildingRenderProgress {
  readonly total: number;
  readonly loaded: number;
  readonly active: number;
  readonly failed: number;
  readonly errors: readonly BuildingRenderLoadError[];
}

export interface BuildingRenderLoadError {
  readonly object_id: string;
  readonly message: string;
}

export interface BuildingRenderManifestRef {
  readonly sha256: string;
  readonly size_bytes: number;
}

const SHA_PATTERN = /^[0-9a-f]{64}$/;
const OBJECT_ID_PATTERN = /^building\.(way|relation)\.\d+\.component\.\d+$/;
const IMAGE_URI_PATTERN = /^assets\/[0-9a-f]{64}$/;
const ENVELOPE_TOLERANCE_M = 1e-3;

function invalid(message: string): never {
  throw new Error(`Building render manifest is invalid: ${message}`);
}

function asObject(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid(`${label} must be an object`);
  return value as Record<string, unknown>;
}

function asArray(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) invalid(`${label} must be an array`);
  return value;
}

function asNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) invalid(`${label} must be a finite number`);
  return value;
}

function asString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) invalid(`${label} must be a non-empty string`);
  return value;
}

function asDigest(value: unknown, label: string): string {
  const digest = asString(value, label);
  if (!SHA_PATTERN.test(digest) || /^0+$/.test(digest)) invalid(`${label} must be a nonzero lowercase sha256`);
  return digest;
}

function fields(value: Record<string, unknown>, required: readonly string[], label: string,
                optional: readonly string[] = []): void {
  for (const key of required) {
    if (!Object.hasOwn(value, key)) invalid(`${label}.${key} is missing`);
  }
  for (const key of Object.keys(value)) {
    if (!required.includes(key) && !optional.includes(key)) invalid(`${label}.${key} is unknown`);
  }
}

function asInteger(value: unknown, label: string, minimum = 0): number {
  const result = asNumber(value, label);
  if (!Number.isSafeInteger(result) || result < minimum) invalid(`${label} must be a safe integer >= ${minimum}`);
  return result;
}

function asBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") invalid(`${label} must be a boolean`);
  return value;
}

function sortedUniqueIds(ids: readonly string[], label: string, pattern: RegExp): void {
  if (ids.some(id => !pattern.test(id))) invalid(`${label} contains an invalid id`);
  if (ids.some((id, index) => index > 0 && ids[index - 1]! >= id)) {
    invalid(`${label} must be sorted and unique`);
  }
}

export function parseBuildingRenderOrigin(value: unknown): BuildingRenderOrigin {
  const origin = asObject(value, "origin_wgs84");
  fields(origin, ["latitude_deg", "longitude_deg", "ellipsoid_height_m", "amsl_m", "geoid_undulation_m"], "origin_wgs84");
  const result = {
    latitude_deg: asNumber(origin.latitude_deg, "origin_wgs84.latitude_deg"),
    longitude_deg: asNumber(origin.longitude_deg, "origin_wgs84.longitude_deg"),
    ellipsoid_height_m: asNumber(origin.ellipsoid_height_m, "origin_wgs84.ellipsoid_height_m"),
    amsl_m: asNumber(origin.amsl_m, "origin_wgs84.amsl_m"),
    geoid_undulation_m: asNumber(origin.geoid_undulation_m, "origin_wgs84.geoid_undulation_m"),
  };
  if (Math.abs(result.latitude_deg) >= 90 || Math.abs(result.longitude_deg) > 180) {
    invalid("origin_wgs84 is outside geographic bounds");
  }
  if (Math.abs(result.ellipsoid_height_m - result.amsl_m - result.geoid_undulation_m) > ENVELOPE_TOLERANCE_M) {
    invalid("origin_wgs84 vertical relation is inconsistent");
  }
  return result;
}

function parseArtProfile(value: unknown): BuildingRenderArtProfile {
  const art = asObject(value, "art_detail");
  fields(art, ["schema", "class", "disclaimer_en", "disclaimer_zh", "note", "layers"], "art_detail");
  if (art.schema !== BUILDING_RENDER_ART_SCHEMA || art.class !== BUILDING_RENDER_ART_CLASS) {
    invalid("art_detail must declare the supported non-survey render art schema and class");
  }
  const disclaimerEn = asString(art.disclaimer_en, "art_detail.disclaimer_en");
  const disclaimerZh = asString(art.disclaimer_zh, "art_detail.disclaimer_zh");
  if (!disclaimerEn.includes("not survey or measurement data") || !disclaimerZh.includes("非测绘事实")) {
    invalid("art_detail lacks its non-survey disclaimers");
  }
  const layers = asObject(art.layers, "art_detail.layers");
  fields(layers, ["roof_detail", "facade_relief"], "art_detail.layers");
  return {
    schema: BUILDING_RENDER_ART_SCHEMA, class: BUILDING_RENDER_ART_CLASS,
    disclaimer_en: disclaimerEn, disclaimer_zh: disclaimerZh,
    note: asString(art.note, "art_detail.note"),
    layers: {
      roof_detail: asBoolean(layers.roof_detail, "art_detail.layers.roof_detail"),
      facade_relief: asBoolean(layers.facade_relief, "art_detail.layers.facade_relief"),
    },
  };
}

function parseArtEntry(value: unknown, profile: BuildingRenderArtProfile, height: number,
                       label: string): BuildingRenderArtEntry {
  const art = asObject(value, label);
  fields(art, ["bays", "parapet", "parapet_height_m", "parapet_thickness_m", "relief", "relief_edges",
    "roof_structures", "triangles", "vertices"], label);
  const result = {
    bays: asInteger(art.bays, `${label}.bays`),
    parapet: asBoolean(art.parapet, `${label}.parapet`),
    parapet_height_m: asNumber(art.parapet_height_m, `${label}.parapet_height_m`),
    parapet_thickness_m: asNumber(art.parapet_thickness_m, `${label}.parapet_thickness_m`),
    relief: asBoolean(art.relief, `${label}.relief`),
    relief_edges: asInteger(art.relief_edges, `${label}.relief_edges`),
    roof_structures: asInteger(art.roof_structures, `${label}.roof_structures`),
    triangles: asInteger(art.triangles, `${label}.triangles`),
    vertices: asInteger(art.vertices, `${label}.vertices`),
  };
  if (result.parapet_height_m < 0 || result.parapet_height_m > height
      || result.parapet_thickness_m < 0) invalid(`${label} has invalid metre dimensions`);
  if (result.parapet !== (result.parapet_height_m > 0 && result.parapet_thickness_m > 0)
      || (!result.parapet && (result.parapet_height_m !== 0 || result.parapet_thickness_m !== 0))) {
    invalid(`${label} parapet dimensions disagree with its presence`);
  }
  if ((!profile.layers.facade_relief && result.relief)
      || (!result.relief && (result.bays !== 0 || result.relief_edges !== 0))
      || (!profile.layers.roof_detail && (result.parapet || result.roof_structures !== 0))) {
    invalid(`${label} disagrees with the declared art layers`);
  }
  return result;
}

export function parseBuildingRenderManifest(value: unknown): BuildingRenderManifest {
  const root = asObject(value, "root");
  fields(root, ["schema_version", "scene", "sources", "counts", "pack_missing_object_ids", "block_size_m",
    "textures", "blocks", "buildings"], "root", ["art_detail"]);
  if (root.schema_version !== BUILDING_RENDER_SCHEMA) invalid("schema_version mismatch");
  const scene = asObject(root.scene, "scene");
  fields(scene, ["id", "coordinate_frame", "origin_wgs84", "objects_json_sha256", "placement_rule",
    "mesh_pack_manifest_sha256", "mesh_pack_source_sha256"], "scene");
  if (scene.coordinate_frame !== "ENU") invalid("coordinate_frame must be ENU");
  const origin = parseBuildingRenderOrigin(scene.origin_wgs84);
  const objectsDigest = asDigest(scene.objects_json_sha256, "scene.objects_json_sha256");
  if (scene.placement_rule !== BUILDING_RENDER_PLACEMENT_RULE) invalid("placement rule mismatch");
  const packDigest = asDigest(scene.mesh_pack_manifest_sha256, "scene.mesh_pack_manifest_sha256");
  asString(scene.id, "scene.id");
  asDigest(scene.mesh_pack_source_sha256, "scene.mesh_pack_source_sha256");
  const sources = asObject(root.sources, "sources");
  fields(sources, ["scaleout_glb_combined_sha256", "scaleout_manifest_sha256", "catalog_fragment_sha256"], "sources");
  const combinedDigest = asDigest(sources.scaleout_glb_combined_sha256, "sources.scaleout_glb_combined_sha256");
  const scaleoutDigest = asDigest(sources.scaleout_manifest_sha256, "sources.scaleout_manifest_sha256");
  const fragmentDigest = asDigest(sources.catalog_fragment_sha256, "sources.catalog_fragment_sha256");
  const blockSize = asNumber(root.block_size_m, "block_size_m");
  if (blockSize <= 0) invalid("block_size_m must be positive metres");
  const artProfile = Object.hasOwn(root, "art_detail") ? parseArtProfile(root.art_detail) : undefined;

  const textures = asArray(root.textures, "textures").map((raw, index) => {
    const texture = asObject(raw, `textures[${index}]`);
    fields(texture, ["sha256", "bytes", "media_type"], `textures[${index}]`);
    const sha256 = asDigest(texture.sha256, `textures[${index}].sha256`);
    const bytes = asNumber(texture.bytes, `textures[${index}].bytes`);
    if (!Number.isSafeInteger(bytes) || bytes < 64) invalid(`textures[${index}].bytes is implausible`);
    const mediaType = asString(texture.media_type, `textures[${index}].media_type`);
    if (!/^[-+.a-z0-9]+\/[-+.a-z0-9]+$/.test(mediaType)) {
      invalid(`textures[${index}].media_type is invalid`);
    }
    return { sha256, bytes, media_type: mediaType } satisfies BuildingRenderTextureRef;
  });
  if (textures.length === 0) invalid("manifest has no shared textures");
  for (let index = 1; index < textures.length; index++) {
    if (textures[index - 1]!.sha256 >= textures[index]!.sha256) {
      invalid("textures must be sorted by unique sha256");
    }
  }
  const textureRefs = new Map(textures.map(texture => [texture.sha256, texture]));

  const counts = asObject(root.counts, "counts");
  const artCountKeys = ["source_vertices", "source_triangles", "derived_vertices", "derived_triangles",
    "art_detail_buildings", "art_detail_relief_buildings", "art_detail_roofs", "art_detail_structures"] as const;
  fields(counts, ["buildings", "total_bytes", "derived_total_bytes", "texture_bytes", "textures", "styles",
    "pack_linked_objects", "pack_missing_objects", "pack_targets", "blocks",
    ...(artProfile === undefined ? [] : artCountKeys)], "counts");
  for (const [key, count] of Object.entries(counts)) asInteger(count, `counts.${key}`);
  const missingIds = asArray(root.pack_missing_object_ids, "pack_missing_object_ids").map((id, index) =>
    asString(id, `pack_missing_object_ids[${index}]`));
  sortedUniqueIds(missingIds, "pack_missing_object_ids", OBJECT_ID_PATTERN);
  const blocks = asArray(root.blocks, "blocks").map((raw, blockPosition) => {
    const block = asObject(raw, `blocks[${blockPosition}]`);
    fields(block, ["index", "min_e", "max_e", "min_n", "max_n", "object_ids"], `blocks[${blockPosition}]`);
    const index = asNumber(block.index, `blocks[${blockPosition}].index`);
    if (index !== blockPosition) invalid(`block index ${index} is out of order`);
    const envelope = {
      min_e: asNumber(block.min_e, `blocks[${blockPosition}].min_e`),
      max_e: asNumber(block.max_e, `blocks[${blockPosition}].max_e`),
      min_n: asNumber(block.min_n, `blocks[${blockPosition}].min_n`),
      max_n: asNumber(block.max_n, `blocks[${blockPosition}].max_n`),
    };
    if (!(envelope.min_e < envelope.max_e && envelope.min_n < envelope.max_n)) {
      invalid(`block ${index} has an empty footprint span`);
    }
    if (Math.abs(envelope.max_e - envelope.min_e - blockSize) > ENVELOPE_TOLERANCE_M
        || Math.abs(envelope.max_n - envelope.min_n - blockSize) > ENVELOPE_TOLERANCE_M) {
      invalid(`block ${index} spans do not match block_size_m`);
    }
    const objectIds = asArray(block.object_ids, `blocks[${blockPosition}].object_ids`).map((id, position) =>
      asString(id, `blocks[${index}].object_ids[${position}]`));
    if (objectIds.length === 0) invalid(`block ${index} is empty`);
    sortedUniqueIds(objectIds, `block ${index} object_ids`, OBJECT_ID_PATTERN);
    return { index, ...envelope, object_ids: objectIds };
  });
  if (blocks.length === 0) invalid("manifest has no blocks");

  const buildings = asArray(root.buildings, "buildings").map((raw, position) => {
    const entry = asObject(raw, `buildings[${position}]`);
    fields(entry, ["object_id", "osm", "glb", "derived_glb", "anchor_east_m", "anchor_north_m",
      "base_enu_up_m", "height_m", "envelope", "style_id", "block", "pack_target_ids"],
    `buildings[${position}]`, ["art_detail"]);
    const objectId = asString(entry.object_id, `buildings[${position}].object_id`);
    if (!OBJECT_ID_PATTERN.test(objectId)) invalid(`${objectId} does not match the scene object id contract`);
    const osm = asObject(entry.osm, `${objectId}.osm`);
    fields(osm, ["type", "id"], `${objectId}.osm`);
    if (osm.type !== "way" && osm.type !== "relation") invalid(`${objectId}.osm.type is unsupported`);
    const osmId = asInteger(osm.id, `${objectId}.osm.id`, 1);
    if (!objectId.startsWith(`building.${osm.type}.${osmId}.component.`)) invalid(`${objectId} disagrees with osm identity`);
    const glb = asObject(entry.glb, `${objectId}.glb`);
    fields(glb, ["path", "sha256", "bytes"], `${objectId}.glb`);
    const sha256 = asDigest(glb.sha256, `${objectId}.glb.sha256`);
    const path = asString(glb.path, `${objectId}.glb.path`);
    if (path !== `assets/${sha256}`) invalid(`${objectId}.glb.path is not content-addressed`);
    const bytes = asNumber(glb.bytes, `${objectId}.glb.bytes`);
    if (!Number.isSafeInteger(bytes) || bytes < 1024) invalid(`${objectId}.glb.bytes is implausible`);
    const derived = asObject(entry.derived_glb, `${objectId}.derived_glb`);
    fields(derived, ["path", "sha256", "bytes"], `${objectId}.derived_glb`);
    const derivedSha256 = asDigest(derived.sha256, `${objectId}.derived_glb.sha256`);
    const derivedPath = asString(derived.path, `${objectId}.derived_glb.path`);
    if (derivedPath !== `assets/${derivedSha256}`) {
      invalid(`${objectId}.derived_glb.path is not content-addressed`);
    }
    const derivedBytes = asNumber(derived.bytes, `${objectId}.derived_glb.bytes`);
    if (!Number.isSafeInteger(derivedBytes) || derivedBytes < 256) {
      invalid(`${objectId}.derived_glb.bytes is implausible`);
    }
    if (derivedSha256 === sha256) invalid(`${objectId}.derived_glb reuses the canonical digest`);
    if (textureRefs.has(derivedSha256)) {
      invalid(`${objectId}.derived_glb collides with the texture store`);
    }
    const anchorEast = asNumber(entry.anchor_east_m, `${objectId}.anchor_east_m`);
    const anchorNorth = asNumber(entry.anchor_north_m, `${objectId}.anchor_north_m`);
    const baseUp = asNumber(entry.base_enu_up_m, `${objectId}.base_enu_up_m`);
    const height = asNumber(entry.height_m, `${objectId}.height_m`);
    if (!(height > 0)) invalid(`${objectId}.height_m must be positive`);
    const rawEnvelope = asObject(entry.envelope, `${objectId}.envelope`);
    fields(rawEnvelope, ["min_e", "min_n", "max_e", "max_n", "base_up", "top_up"], `${objectId}.envelope`);
    const envelope: BuildingRenderEnvelope = {
      min_e: asNumber(rawEnvelope.min_e, `${objectId}.envelope.min_e`),
      min_n: asNumber(rawEnvelope.min_n, `${objectId}.envelope.min_n`),
      max_e: asNumber(rawEnvelope.max_e, `${objectId}.envelope.max_e`),
      max_n: asNumber(rawEnvelope.max_n, `${objectId}.envelope.max_n`),
      base_up: asNumber(rawEnvelope.base_up, `${objectId}.envelope.base_up`),
      top_up: asNumber(rawEnvelope.top_up, `${objectId}.envelope.top_up`),
    };
    if (!(envelope.min_e < envelope.max_e && envelope.min_n < envelope.max_n)) invalid(`${objectId} envelope has no footprint area`);
    if (!(envelope.min_e <= anchorEast && anchorEast <= envelope.max_e
      && envelope.min_n <= anchorNorth && anchorNorth <= envelope.max_n)) {
      invalid(`${objectId} anchor lies outside its envelope`);
    }
    if (Math.abs(envelope.base_up - baseUp) > ENVELOPE_TOLERANCE_M
      || Math.abs(envelope.top_up - envelope.base_up - height) > ENVELOPE_TOLERANCE_M) {
      invalid(`${objectId} envelope does not span base..base+height`);
    }
    const block = asNumber(entry.block, `${objectId}.block`);
    if (!Number.isSafeInteger(block) || block < 0 || block >= blocks.length) {
      invalid(`${objectId} references an unknown block`);
    }
    const packTargets = asArray(entry.pack_target_ids, `${objectId}.pack_target_ids`).map((target, targetPosition) =>
      asString(target, `${objectId}.pack_target_ids[${targetPosition}]`));
    sortedUniqueIds(packTargets, `${objectId}.pack_target_ids`, /^[wr]-?\d+$/);
    if (Object.hasOwn(entry, "art_detail") !== (artProfile !== undefined)) {
      invalid(`${objectId}.art_detail must match the manifest art declaration`);
    }
    return {
      object_id: objectId,
      osm: { type: osm.type, id: osmId },
      glb: { path, sha256, bytes },
      derived_glb: { path: derivedPath, sha256: derivedSha256, bytes: derivedBytes },
      anchor_east_m: anchorEast,
      anchor_north_m: anchorNorth,
      base_enu_up_m: baseUp,
      height_m: height,
      envelope,
      style_id: asString(entry.style_id, `${objectId}.style_id`),
      block,
      pack_target_ids: packTargets,
      ...(artProfile === undefined ? {} : { art_detail: parseArtEntry(entry.art_detail, artProfile, height, `${objectId}.art_detail`) }),
    } satisfies BuildingRenderEntry;
  });

  if (buildings.length === 0) invalid("manifest has no buildings");
  const uniqueIds = new Set(buildings.map(entry => entry.object_id));
  if (uniqueIds.size !== buildings.length) invalid("object ids are not unique");
  const uniqueDigests = new Set(buildings.map(entry => entry.glb.sha256));
  if (uniqueDigests.size !== buildings.length) invalid("glb digests are not unique");
  const uniqueDerivedDigests = new Set(buildings.map(entry => entry.derived_glb.sha256));
  if (uniqueDerivedDigests.size !== buildings.length) invalid("derived glb digests are not unique");
  for (const entry of buildings) {
    if (textureRefs.has(entry.glb.sha256)) invalid(`${entry.object_id} canonical digest collides with the texture store`);
    if (uniqueDigests.has(entry.derived_glb.sha256)) invalid(`${entry.object_id} derived digest collides with the canonical store`);
  }
  const totalBytes = buildings.reduce((sum, entry) => sum + entry.glb.bytes, 0);
  const derivedTotalBytes = buildings.reduce((sum, entry) => sum + entry.derived_glb.bytes, 0);
  const textureBytes = textures.reduce((sum, texture) => sum + texture.bytes, 0);
  if (asNumber(counts.buildings, "counts.buildings") !== buildings.length) invalid("counts.buildings mismatch");
  if (asNumber(counts.total_bytes, "counts.total_bytes") !== totalBytes) invalid("counts.total_bytes mismatch");
  if (asNumber(counts.derived_total_bytes, "counts.derived_total_bytes") !== derivedTotalBytes) {
    invalid("counts.derived_total_bytes mismatch");
  }
  if (asNumber(counts.texture_bytes, "counts.texture_bytes") !== textureBytes) {
    invalid("counts.texture_bytes mismatch");
  }
  if (asNumber(counts.textures, "counts.textures") !== textures.length) invalid("counts.textures mismatch");
  if (asNumber(counts.blocks, "counts.blocks") !== blocks.length) invalid("counts.blocks mismatch");
  if (asNumber(counts.pack_missing_objects, "counts.pack_missing_objects") !== missingIds.length) {
    invalid("counts.pack_missing_objects mismatch");
  }
  const linkedCount = buildings.filter(entry => entry.pack_target_ids.length > 0).length;
  if (asNumber(counts.pack_linked_objects, "counts.pack_linked_objects") !== linkedCount) {
    invalid("counts.pack_linked_objects mismatch");
  }
  const targetCount = buildings.reduce((sum, entry) => sum + entry.pack_target_ids.length, 0);
  if (asNumber(counts.pack_targets, "counts.pack_targets") !== targetCount) {
    invalid("counts.pack_targets mismatch");
  }
  const missingSet = new Set(missingIds);
  if (missingIds.some(id => !uniqueIds.has(id))) invalid("pack_missing_object_ids contains unknown objects");
  const allTargets = buildings.flatMap(entry => entry.pack_target_ids);
  if (new Set(allTargets).size !== allTargets.length) invalid("pack target ids are not unique across buildings");
  for (const entry of buildings) {
    const linked = entry.pack_target_ids.length > 0;
    if (linked === missingSet.has(entry.object_id)) {
      invalid(`${entry.object_id} pack linkage disagrees with pack_missing_object_ids`);
    }
    const block = blocks[entry.block]!;
    const inBlock = entry.anchor_east_m >= block.min_e && entry.anchor_east_m < block.max_e
      && entry.anchor_north_m >= block.min_n && entry.anchor_north_m < block.max_n;
    if (!inBlock) invalid(`${entry.object_id} anchor is outside its declared block`);
    if (!block.object_ids.includes(entry.object_id)) {
      invalid(`${entry.object_id} is missing from its block object_ids`);
    }
  }
  const declaredIds = blocks.flatMap(block => block.object_ids);
  const declared = new Set(declaredIds);
  if (declaredIds.length !== buildings.length || declared.size !== buildings.length
      || declaredIds.some(id => !uniqueIds.has(id))) invalid("block object_ids do not cover the building set exactly once");
  const styleCount = new Set(buildings.map(entry => entry.style_id)).size;
  if (asNumber(counts.styles, "counts.styles") !== styleCount) invalid("counts.styles mismatch");
  let artCounts: Pick<BuildingRenderManifest["counts"], typeof artCountKeys[number]> | undefined;
  if (artProfile !== undefined) {
    artCounts = Object.fromEntries(artCountKeys.map(key => [key, asInteger(counts[key], `counts.${key}`)]));
    const aggregate = {
      derived_vertices: buildings.reduce((sum, entry) => sum + entry.art_detail!.vertices, 0),
      derived_triangles: buildings.reduce((sum, entry) => sum + entry.art_detail!.triangles, 0),
      art_detail_buildings: buildings.filter(entry => entry.art_detail!.vertices > 0).length,
      art_detail_relief_buildings: buildings.filter(entry => entry.art_detail!.relief).length,
      art_detail_roofs: buildings.filter(entry => entry.art_detail!.parapet).length,
      art_detail_structures: buildings.filter(entry => entry.art_detail!.roof_structures > 0).length,
    };
    for (const key of Object.keys(aggregate) as (keyof typeof aggregate)[]) {
      if (artCounts[key] !== aggregate[key]) invalid(`counts.${key} mismatch`);
    }
    if (artCounts.source_vertices! < buildings.length * 3 || artCounts.source_triangles! < buildings.length
        || artCounts.source_vertices! > artCounts.derived_vertices! || artCounts.source_triangles! > artCounts.derived_triangles!) {
      invalid("source geometry counts exceed or cannot form the derived building inventory");
    }
  }

  return {
    schema_version: BUILDING_RENDER_SCHEMA,
    scene: {
      id: asString(scene.id, "scene.id"),
      coordinate_frame: "ENU",
      origin_wgs84: origin,
      objects_json_sha256: objectsDigest,
      placement_rule: BUILDING_RENDER_PLACEMENT_RULE,
      mesh_pack_manifest_sha256: packDigest,
      mesh_pack_source_sha256: asDigest(scene.mesh_pack_source_sha256, "scene.mesh_pack_source_sha256"),
    },
    sources: {
      scaleout_glb_combined_sha256: combinedDigest,
      scaleout_manifest_sha256: scaleoutDigest,
      catalog_fragment_sha256: fragmentDigest,
    },
    ...(artProfile === undefined ? {} : { art_detail: artProfile }),
    counts: {
      buildings: buildings.length,
      total_bytes: totalBytes,
      derived_total_bytes: derivedTotalBytes,
      texture_bytes: textureBytes,
      textures: textures.length,
      styles: styleCount,
      pack_linked_objects: linkedCount,
      pack_missing_objects: missingIds.length,
      pack_targets: targetCount,
      blocks: blocks.length,
      ...artCounts,
    },
    pack_missing_object_ids: missingIds,
    block_size_m: blockSize,
    textures,
    blocks,
    buildings,
  };
}

export async function fetchBuildingRenderManifest(
  resolver: AssetResolver, ref: BuildingRenderManifestRef, signal?: AbortSignal,
): Promise<BuildingRenderManifest> {
  const declared: DeclaredAsset = {
    sha256: ref.sha256, sizeBytes: ref.size_bytes, mediaType: "application/json",
  };
  const bytes = await resolver.fetchVerifiedBytes(`assets/${ref.sha256}`, declared, signal);
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new Error("Building render manifest is not valid UTF-8 JSON");
  }
  return parseBuildingRenderManifest(parsed);
}

/** World placement of the asset-local frame: east → +X, north → −Z, up → +Y. */
export function buildingWorldPosition(entry: BuildingRenderEntry): THREE.Vector3 {
  return new THREE.Vector3(entry.anchor_east_m, entry.base_enu_up_m, -entry.anchor_north_m);
}

/** Axis-aligned footprint envelope in world coordinates; rotation is identity. */
export function buildingPlacement(entry: BuildingRenderEntry): BuildingPlacementSource {
  const { min_e, max_e, min_n, max_n, base_up } = entry.envelope;
  return {
    building_id: entry.object_id,
    part: 0,
    x: (min_e + max_e) / 2,
    z: -(min_n + max_n) / 2,
    width: max_e - min_e,
    depth: max_n - min_n,
    height: entry.height_m,
    rotation_deg: 0,
    base_y: base_up,
  };
}

export interface TexturePlanEntry {
  readonly sha256: string;
  readonly colorSpace: THREE.ColorSpace;
}

export function readGlbDocument(bytes: ArrayBuffer): Record<string, unknown> {
  if (bytes.byteLength < 28) throw new Error("Building render asset is not a glTF 2.0 GLB container");
  const header = new DataView(bytes, 0, 20);
  if (header.getUint32(0, true) !== 0x46546c67 || header.getUint32(4, true) !== 2) {
    throw new Error("Building render asset is not a glTF 2.0 GLB container");
  }
  if (header.getUint32(8, true) !== bytes.byteLength) throw new Error("Building render GLB declared length mismatch");
  const jsonLength = header.getUint32(12, true);
  const jsonType = header.getUint32(16, true);
  if (jsonType !== 0x4e4f534a || jsonLength % 4 !== 0 || 28 + jsonLength > bytes.byteLength) {
    throw new Error("Building render GLB has no readable JSON chunk");
  }
  const binHeader = new DataView(bytes, 20 + jsonLength, 8);
  if (binHeader.getUint32(4, true) !== 0x004e4942 || binHeader.getUint32(0, true) % 4 !== 0
      || 28 + jsonLength + binHeader.getUint32(0, true) !== bytes.byteLength) {
    throw new Error("Building render GLB BIN chunk length or type mismatch");
  }
  const json = new Uint8Array(bytes, 20, jsonLength);
  return asObject(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(json)), "GLB document");
}

/** Validate the declared source identity and every embedded geometry byte range before GLTFLoader. */
export function validateBuildingRenderGlb(bytes: ArrayBuffer, entry: BuildingRenderEntry,
                                        manifest: BuildingRenderManifest): Record<string, unknown> {
  const document = readGlbDocument(bytes);
  const asset = asObject(document.asset, "GLB asset");
  if (asset.version !== "2.0") invalid("GLB asset.version must be 2.0");
  const extras = asObject(asset.extras, "GLB asset.extras");
  if (extras.object_id !== entry.object_id || extras.source_frame !== "asset_local") invalid("GLB object identity or source frame mismatch");
  const scene = asObject(extras.scene, "GLB asset.extras.scene");
  if (scene.objects_json_sha256 !== manifest.scene.objects_json_sha256) invalid("GLB objects.json digest mismatch");
  const origin = parseBuildingRenderOrigin(scene.origin_wgs84);
  for (const key of Object.keys(origin) as (keyof BuildingRenderOrigin)[]) {
    if (origin[key] !== manifest.scene.origin_wgs84[key]) invalid(`GLB origin ${key} mismatch`);
  }
  const frame = asObject(extras.frame, "GLB asset.extras.frame");
  if (frame.placement !== BUILDING_RENDER_PLACEMENT_RULE) invalid("GLB placement rule mismatch");
  for (const key of ["anchor_east_m", "anchor_north_m", "base_enu_up_m"] as const) {
    if (frame[key] !== entry[key]) invalid(`GLB ${key} mismatch`);
  }
  if (asObject(extras.building, "GLB asset.extras.building").height_m !== entry.height_m
      || asObject(extras.style, "GLB asset.extras.style").style_id !== entry.style_id) invalid("GLB height or style source mismatch");
  if ((extras.art_detail !== undefined) !== (entry.art_detail !== undefined)) invalid("GLB art declaration mismatch");
  if (entry.art_detail !== undefined) {
    const art = asObject(extras.art_detail, "GLB asset.extras.art_detail");
    if (art.schema !== BUILDING_RENDER_ART_SCHEMA || art.class !== BUILDING_RENDER_ART_CLASS
        || art.triangles !== entry.art_detail.triangles || art.vertices !== entry.art_detail.vertices
        || asNumber(art.max_up_m, "GLB art.max_up_m") > entry.envelope.top_up) invalid("GLB art provenance mismatch");
    const layers = asObject(art.layers, "GLB art.layers");
    if (layers.roof_detail !== manifest.art_detail?.layers.roof_detail
        || layers.facade_relief !== manifest.art_detail?.layers.facade_relief) invalid("GLB art layers mismatch");
    if (!asString(art.disclaimer_en, "GLB art.disclaimer_en").includes("not survey or measurement data")
        || !asString(art.disclaimer_zh, "GLB art.disclaimer_zh").includes("非测绘事实")) invalid("GLB art non-survey disclaimer mismatch");
    const detail = asObject(art.detail, "GLB art.detail");
    for (const key of ["bays", "parapet", "parapet_height_m", "parapet_thickness_m", "relief_edges", "roof_structures"] as const) {
      if (detail[key] !== entry.art_detail[key]) invalid(`GLB art.detail.${key} mismatch`);
    }
    if (detail.facade_relief !== entry.art_detail.relief) invalid("GLB art.detail.facade_relief mismatch");
  }
  const jsonLength = new DataView(bytes).getUint32(12, true);
  const binLength = new DataView(bytes).getUint32(20 + jsonLength, true);
  const buffers = asArray(document.buffers, "GLB buffers");
  if (buffers.length !== 1) invalid("GLB must contain one embedded buffer");
  const buffer = asObject(buffers[0], "GLB buffers[0]");
  if (buffer.uri !== undefined || asInteger(buffer.byteLength, "GLB buffer.byteLength", 1) !== binLength) {
    invalid("GLB buffer differs from its embedded BIN chunk");
  }
  const views = asArray(document.bufferViews, "GLB bufferViews").map((raw, index) => {
    const view = asObject(raw, `GLB bufferViews[${index}]`);
    if (view.buffer !== 0) invalid(`GLB bufferViews[${index}] references an external buffer`);
    const offset = view.byteOffset === undefined ? 0 : asInteger(view.byteOffset, `GLB bufferViews[${index}].byteOffset`);
    const length = asInteger(view.byteLength, `GLB bufferViews[${index}].byteLength`, 1);
    if (offset + length > binLength) invalid(`GLB bufferViews[${index}] exceeds its BIN chunk`);
    const stride = view.byteStride === undefined ? undefined : asInteger(view.byteStride, `GLB bufferViews[${index}].byteStride`, 4);
    if (stride !== undefined && (stride > 252 || stride % 4 !== 0)) invalid(`GLB bufferViews[${index}] has an invalid byteStride`);
    return { offset, length, stride };
  });
  const dimensions: Readonly<Record<string, number>> = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };
  const componentBytes: Readonly<Record<number, number>> = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };
  const accessors = asArray(document.accessors, "GLB accessors").map((raw, index) => {
    const accessor = asObject(raw, `GLB accessors[${index}]`);
    const view = views[asInteger(accessor.bufferView, `GLB accessors[${index}].bufferView`)];
    if (view === undefined || accessor.sparse !== undefined) invalid(`GLB accessors[${index}] must reference embedded geometry`);
    const size = componentBytes[asInteger(accessor.componentType, `GLB accessors[${index}].componentType`)];
    const dimension = dimensions[asString(accessor.type, `GLB accessors[${index}].type`)];
    if (size === undefined || dimension === undefined) invalid(`GLB accessors[${index}] has an unsupported geometry type`);
    const count = asInteger(accessor.count, `GLB accessors[${index}].count`, 1);
    const offset = accessor.byteOffset === undefined ? 0 : asInteger(accessor.byteOffset, `GLB accessors[${index}].byteOffset`);
    const stride = view.stride ?? size * dimension;
    if (stride < size * dimension || offset % size !== 0 || (view.offset + offset) % size !== 0
        || offset + (count - 1) * stride + size * dimension > view.length) invalid(`GLB accessors[${index}] exceeds or misaligns its bufferView`);
    if (accessor.componentType === 5126) {
      const data = new DataView(bytes, 28 + jsonLength + view.offset + offset);
      for (let item = 0; item < count; item++) for (let axis = 0; axis < dimension; axis++) {
        if (!Number.isFinite(data.getFloat32(item * stride + axis * size, true))) invalid(`GLB accessors[${index}] contains nonfinite geometry`);
      }
    }
    return { accessor, count, offset: view.offset + offset, stride, size };
  });
  const positionAccessors = new Set<number>();
  let triangleCount = 0;
  const meshes = asArray(document.meshes, "GLB meshes");
  if (meshes.length === 0) invalid("GLB has no geometry meshes");
  for (const raw of meshes) {
    for (const primitiveRaw of asArray(asObject(raw, "GLB mesh").primitives, "GLB mesh.primitives")) {
      const primitive = asObject(primitiveRaw, "GLB primitive");
      if (primitive.mode !== undefined && primitive.mode !== 4) invalid("GLB primitive must use triangles");
      const attributes = asObject(primitive.attributes, "GLB primitive.attributes");
      const position = accessors[asInteger(attributes.POSITION, "GLB primitive.POSITION")];
      const indices = accessors[asInteger(primitive.indices, "GLB primitive.indices")];
      if (position?.accessor.type !== "VEC3" || position.accessor.componentType !== 5126
          || indices?.accessor.type !== "SCALAR" || ![5121, 5123, 5125].includes(indices.accessor.componentType as number)
          || indices.count % 3 !== 0) invalid("GLB primitive geometry accessor contract mismatch");
      positionAccessors.add(attributes.POSITION as number);
      triangleCount += indices.count / 3;
      for (const [name, accessorIndex] of Object.entries(attributes)) {
        const attribute = accessors[asInteger(accessorIndex, `GLB primitive.${name}`)];
        if (attribute === undefined || attribute.count !== position.count) invalid(`GLB primitive.${name} attribute count mismatch`);
      }
      const data = new DataView(bytes, 28 + jsonLength + indices.offset);
      for (let index = 0; index < indices.count; index++) {
        const offset = index * indices.stride;
        const vertex = indices.size === 4 ? data.getUint32(offset, true)
          : indices.size === 2 ? data.getUint16(offset, true) : data.getUint8(offset);
        if (vertex >= position.count) invalid("GLB triangle references an unknown vertex");
      }
    }
  }
  const measured = { min_e: Infinity, min_n: Infinity, max_e: -Infinity, max_n: -Infinity,
    base_up: Infinity, top_up: -Infinity };
  for (const index of positionAccessors) {
    const position = accessors[index]!;
    const data = new DataView(bytes, 28 + jsonLength + position.offset);
    for (let vertex = 0; vertex < position.count; vertex++) {
      const offset = vertex * position.stride;
      const east = entry.anchor_east_m + data.getFloat32(offset, true);
      const north = entry.anchor_north_m - data.getFloat32(offset + 8, true);
      const up = entry.base_enu_up_m + data.getFloat32(offset + 4, true);
      measured.min_e = Math.min(measured.min_e, east); measured.max_e = Math.max(measured.max_e, east);
      measured.min_n = Math.min(measured.min_n, north); measured.max_n = Math.max(measured.max_n, north);
      measured.base_up = Math.min(measured.base_up, up); measured.top_up = Math.max(measured.top_up, up);
    }
  }
  for (const key of Object.keys(measured) as (keyof typeof measured)[]) {
    if (Math.abs(measured[key] - entry.envelope[key]) > ENVELOPE_TOLERANCE_M) invalid(`GLB actual envelope ${key} mismatch`);
  }
  const nodes = asArray(document.nodes, "GLB nodes");
  const identity = { translation: [0, 0, 0], rotation: [0, 0, 0, 1], scale: [1, 1, 1],
    matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] };
  const nodeMeshes = new Set<number>();
  for (const [index, raw] of nodes.entries()) {
    const node = asObject(raw, `GLB nodes[${index}]`);
    const mesh = asInteger(node.mesh, `GLB nodes[${index}].mesh`);
    if (mesh >= meshes.length || node.children !== undefined || node.skin !== undefined) invalid("GLB nodes must directly reference local geometry meshes");
    nodeMeshes.add(mesh);
    for (const key of Object.keys(identity) as (keyof typeof identity)[]) {
      if (node[key] !== undefined) {
        const value = asArray(node[key], `GLB nodes[${index}].${key}`);
        if (value.length !== identity[key].length || value.some((part, axis) => part !== identity[key][axis])) invalid("GLB node transform must be identity");
      }
    }
  }
  if (nodeMeshes.size !== meshes.length || nodes.length !== meshes.length) invalid("GLB nodes must place each declared mesh exactly once");
  const scenes = asArray(document.scenes, "GLB scenes");
  if (scenes.length !== 1 || document.scene !== 0) invalid("GLB must declare one active local scene");
  const sceneNodes = asArray(asObject(scenes[0], "GLB scenes[0]").nodes, "GLB scenes[0].nodes");
  if (sceneNodes.length !== nodes.length || new Set(sceneNodes).size !== nodes.length
      || sceneNodes.some(index => typeof index !== "number" || !Number.isSafeInteger(index) || index < 0 || index >= nodes.length)) {
    invalid("GLB active scene does not cover its nodes exactly once");
  }
  if (document.animations !== undefined || document.skins !== undefined) invalid("GLB runtime buildings must use static local geometry");
  if (entry.art_detail !== undefined
      && (entry.art_detail.vertices !== [...positionAccessors].reduce((sum, index) => sum + accessors[index]!.count, 0)
        || entry.art_detail.triangles !== triangleCount)) invalid("GLB actual geometry counts disagree with its art declaration");
  texturePlan(document);
  const textureDigests = new Set(manifest.textures.map(texture => texture.sha256));
  for (const raw of asArray(document.images, "GLB images")) {
    const uri = asString(asObject(raw, "GLB image").uri, "GLB image.uri");
    if (!textureDigests.has(uri.slice("assets/".length))) invalid("GLB references an undeclared shared texture");
  }
  return document;
}

export function texturePlan(document: Record<string, unknown>): Map<number, TexturePlanEntry> {
  const images = asArray(document.images, "images");
  const textures = asArray(document.textures, "textures");
  const imageSha: string[] = [];
  for (const [index, raw] of images.entries()) {
    const image = asObject(raw, `images[${index}]`);
    if (image.bufferView !== undefined) {
      throw new Error("Building render image embeds bytes; derived GLBs must use the shared texture store");
    }
    const uri = asString(image.uri, `images[${index}].uri`);
    if (!IMAGE_URI_PATTERN.test(uri)) {
      throw new Error("Building render image URI is not a content-addressed texture store path");
    }
    imageSha.push(uri.slice("assets/".length));
  }
  const colorSpaceByTexture = new Map<number, THREE.ColorSpace>();
  const materials = asArray(document.materials, "materials");
  const mark = (textureInfo: unknown, colorSpace: THREE.ColorSpace, label: string): void => {
    if (textureInfo === undefined || textureInfo === null) return;
    const index = asObject(textureInfo, label).index;
    if (typeof index !== "number" || !Number.isSafeInteger(index) || index < 0 || index >= textures.length) {
      throw new Error(`Building render ${label} references an unknown texture`);
    }
    colorSpaceByTexture.set(index, colorSpace);
  };
  for (const [index, raw] of materials.entries()) {
    const material = asObject(raw, `materials[${index}]`);
    const pbr = asObject(material.pbrMetallicRoughness, `materials[${index}].pbrMetallicRoughness`);
    mark(pbr.baseColorTexture, THREE.SRGBColorSpace, `materials[${index}].baseColorTexture`);
    mark(material.emissiveTexture, THREE.SRGBColorSpace, `materials[${index}].emissiveTexture`);
    mark(material.normalTexture, THREE.NoColorSpace, `materials[${index}].normalTexture`);
  }
  const plan = new Map<number, TexturePlanEntry>();
  for (const [index, raw] of textures.entries()) {
    const texture = asObject(raw, `textures[${index}]`);
    const source = texture.source;
    if (typeof source !== "number" || !Number.isSafeInteger(source) || source < 0 || source >= imageSha.length) {
      throw new Error("Building render texture references an unknown image");
    }
    plan.set(index, {
      sha256: imageSha[source]!,
      colorSpace: colorSpaceByTexture.get(index) ?? THREE.NoColorSpace,
    });
  }
  return plan;
}

export interface BuildingRenderStreamerOptions {
  readonly manifest: BuildingRenderManifest;
  readonly resolver: AssetResolver;
  readonly group: THREE.Group;
  readonly anisotropy?: number;
  readonly concurrency?: number;
  readonly onProgress?: (progress: BuildingRenderProgress) => void;
  readonly requestRender?: () => void;
  /** Called once per building right after it joins the scene group. */
  readonly onPlaced?: (visual: THREE.Object3D) => void;
  /** Test seam: parse verified GLB bytes into a placeable object. */
  readonly parseBuilding?: (bytes: ArrayBuffer, entry: BuildingRenderEntry) => Promise<THREE.Object3D>;
}

/** Source-equivalent opaque materials share uploads; per-object matrices remain separate. */
export function shareBuildingRenderMaterial(material: THREE.MeshStandardMaterial,
    document: Record<string, unknown>, materialIndex: number,
    cache: Map<string, THREE.MeshStandardMaterial>): THREE.MeshStandardMaterial {
  // Transparent draw ordering is object-dependent and is not part of this optimization.
  if (material.transparent || material.opacity !== 1 || !material.depthWrite) return material;
  const definition = asArray(document.materials, "materials")[materialIndex];
  if (definition === undefined) throw new Error("Building material has no verified source definition");
  const key = JSON.stringify({ definition, images: document.images, textures: document.textures,
    samplers: document.samplers, type: material.type, vertexColors: material.vertexColors,
    flatShading: material.flatShading, side: material.side,
    normalScale: material.normalScale.toArray(), normalMapType: material.normalMapType,
    clearcoatNormalScale: material instanceof THREE.MeshPhysicalMaterial
      ? material.clearcoatNormalScale.toArray() : undefined });
  const shared = cache.get(key);
  if (shared === undefined) { cache.set(key, material); return material; }
  if (shared !== material) material.dispose();
  return shared;
}

/**
 * Wraps parsed GLB content in the placed visual group: ENU position, picking
 * target, envelope collision box and material registration for mood changes.
 * Shared by the GLB parse path and module tests so they assert one contract.
 */
export function assembleBuildingVisual(entry: BuildingRenderEntry, content: THREE.Object3D,
                                       materials: readonly THREE.MeshStandardMaterial[]): THREE.Group {
  const placement = buildingPlacement(entry);
  const visual = new THREE.Group();
  visual.name = entry.object_id;
  visual.position.copy(buildingWorldPosition(entry));
  visual.userData.target = { kind: "building", id: entry.object_id };
  visual.userData.objectId = entry.object_id;
  visual.userData.glbSha256 = entry.glb.sha256;
  visual.userData.derivedGlbSha256 = entry.derived_glb.sha256;
  visual.userData.styleId = entry.style_id;
  visual.userData.buildingRenderAsset = true;
  visual.userData.collisionBox = placement;
  visual.userData.packTargetIds = [...entry.pack_target_ids];
  visual.userData.renderMaterials = [...materials];
  visual.add(content);
  visual.add(collisionBox(
    new THREE.Vector3(placement.width, placement.height, placement.depth), 0x2ed9df));
  // Verified render GLBs contain no animation, skins or nonidentity node transforms, and
  // batching bakes the placed world transform, so the whole visual is static. Parent
  // movement still propagates; an auto-updating root would re-multiply every subtree each frame.
  cacheStaticTransforms(visual);
  return visual;
}

export class BuildingRenderStreamer {
  readonly manifest: BuildingRenderManifest;
  readonly group: THREE.Group;
  private readonly resolver: AssetResolver;
  private readonly anisotropy: number;
  private readonly concurrency: number;
  private readonly onProgress?: (progress: BuildingRenderProgress) => void;
  private readonly requestRender?: () => void;
  private readonly onPlaced?: (visual: THREE.Object3D) => void;
  private readonly parseBuilding: (bytes: ArrayBuffer, entry: BuildingRenderEntry) => Promise<THREE.Object3D>;
  private readonly manager = new THREE.LoadingManager();
  private readonly loader: GLTFLoader;
  private readonly textureCache = new Map<string, THREE.Texture>();
  private readonly materialCache = new Map<string, THREE.MeshStandardMaterial>();
  private readonly textureRefs: Map<string, BuildingRenderTextureRef>;
  private readonly textureUrlPending = new Map<string, Promise<string>>();
  private readonly textureUrls = new Map<string, string>();
  private readonly state = new Map<string, "pending" | "active" | "placed" | "failed">();
  private readonly errors: BuildingRenderLoadError[] = [];
  private readonly abort = new AbortController();
  private readonly completions: { resolve: () => void }[] = [];
  private active = 0;
  private loaded = 0;
  private running = false;
  private disposed = false;
  private lastCamera: THREE.Camera | null = null;
  readonly batches: BuildingRenderBatches;
  private renderFrame: number | null = null;

  constructor(options: BuildingRenderStreamerOptions) {
    this.manifest = options.manifest;
    this.resolver = options.resolver;
    this.group = options.group;
    this.batches = new BuildingRenderBatches(this.group, this.manifest.blocks);
    this.anisotropy = options.anisotropy ?? 4;
    this.concurrency = Math.max(1, Math.floor(options.concurrency ?? 4));
    this.onProgress = options.onProgress;
    this.requestRender = options.requestRender;
    this.onPlaced = options.onPlaced;
    this.parseBuilding = options.parseBuilding ?? this.parseGlb.bind(this);
    this.textureRefs = new Map(options.manifest.textures.map(texture => [texture.sha256, texture]));
    // GLTFLoader resolves external image URIs through the loading manager:
    // only digest-verified blob URLs are ever published to it.
    this.manager.setURLModifier(url => {
      const exact = this.textureUrls.get(url);
      if (exact !== undefined) return exact;
      if ([...this.textureUrls.values()].includes(url)) return url;
      throw new Error(`Building render requested an undeclared resource: ${url}`);
    });
    this.loader = new GLTFLoader(this.manager);
    for (const entry of options.manifest.buildings) this.state.set(entry.object_id, "pending");
  }

  get progress(): BuildingRenderProgress {
    return {
      total: this.manifest.buildings.length,
      loaded: this.loaded,
      active: this.active,
      failed: this.errors.length,
      errors: [...this.errors],
    };
  }

  private blockCenter(block: BuildingRenderBlock): THREE.Vector3 {
    return new THREE.Vector3((block.min_e + block.max_e) / 2, 50, -(block.min_n + block.max_n) / 2);
  }

  private frustum(camera: THREE.Camera): THREE.Frustum {
    camera.updateMatrixWorld();
    const matrix = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    return new THREE.Frustum().setFromProjectionMatrix(matrix);
  }

  /** Deterministic queue order: in-frustum blocks first, then distance, then block index, then id. */
  private nextPending(camera: THREE.Camera | null): { entry: BuildingRenderEntry; distance: number; visible: boolean } | null {
    const frustum = camera === null ? null : this.frustum(camera);
    let best: { entry: BuildingRenderEntry; distance: number; visible: boolean } | null = null;
    let bestKey: readonly [number, number, number, string] | null = null;
    for (const entry of this.manifest.buildings) {
      if (this.state.get(entry.object_id) !== "pending") continue;
      const block = this.manifest.blocks[entry.block]!;
      const center = this.blockCenter(block);
      const visible = frustum === null
        || frustum.intersectsSphere(new THREE.Sphere(center, this.blockRadius(block)));
      const distance = camera === null ? 0 : camera.position.distanceTo(center);
      const key: readonly [number, number, number, string] =
        [visible ? 0 : 1, distance, entry.block, entry.object_id];
      if (best === null || bestKey === null) {
        best = { entry, distance, visible };
        bestKey = key;
        continue;
      }
      let decided = false;
      for (let axis = 0; axis < key.length; axis++) {
        const left = key[axis]!;
        const right = bestKey[axis]!;
        if (left < right) { decided = true; break; }
        if (left > right) break;
      }
      if (decided) {
        best = { entry, distance, visible };
        bestKey = key;
      }
    }
    return best;
  }

  private blockRadius(block: BuildingRenderBlock): number {
    const halfWidth = (block.max_e - block.min_e) / 2;
    const halfDepth = (block.max_n - block.min_n) / 2;
    // 100 m vertical slack covers the tallest recorded tower for visibility tests.
    return Math.hypot(halfWidth, halfDepth, 100);
  }

  private settle(objectId: string, failed: boolean, message?: string): void {
    this.state.set(objectId, failed ? "failed" : "placed");
    if (failed) this.errors.push({ object_id: objectId, message: message ?? "unknown error" });
    else this.loaded++;
    this.active--;
    const waiters = this.completions.splice(0);
    for (const waiter of waiters) waiter.resolve();
    this.onProgress?.(this.progress);
    if (this.requestRender !== undefined && this.renderFrame === null) {
      this.renderFrame = requestAnimationFrame(() => {
        this.renderFrame = null;
        if (this.disposed) return;
        this.batches.flush();
        this.requestRender!();
      });
    }
  }

  private async run(): Promise<void> {
    if (this.running || this.disposed) return;
    this.running = true;
    try {
      while (!this.disposed) {
        if (this.active >= this.concurrency) {
          if (this.active === 0) break;
          await new Promise<void>(resolve => this.completions.push({ resolve }));
          continue;
        }
        const next = this.nextPending(this.lastCamera);
        if (next === null) {
          if (this.active === 0) break;
          await new Promise<void>(resolve => this.completions.push({ resolve }));
          continue;
        }
        const entry = next.entry;
        this.state.set(entry.object_id, "active");
        this.active++;
        void this.loadOne(entry);
      }
    } finally {
      this.running = false;
    }
  }

  private async loadOne(entry: BuildingRenderEntry): Promise<void> {
    try {
      const declared: DeclaredAsset = {
        sha256: entry.derived_glb.sha256, sizeBytes: entry.derived_glb.bytes,
        mediaType: "model/gltf-binary",
      };
      const bytes = await this.resolver.fetchVerifiedBytes(entry.derived_glb.path, declared, this.abort.signal);
      if (this.disposed) throw new Error("Building render streamer was disposed");
      const visual = await this.parseBuilding(bytes, entry);
      if (this.disposed) {
        visual.traverse(node => {
          if (node instanceof THREE.Mesh) {
            node.geometry.dispose();
            for (const material of Array.isArray(node.material) ? node.material : [node.material]) material.dispose();
          }
        });
        throw new Error("Building render streamer was disposed");
      }
      this.group.add(visual);
      this.onPlaced?.(visual);
      this.batches.add(visual, entry.block);
      this.settle(entry.object_id, false);
    } catch (error) {
      if (this.disposed || this.abort.signal.aborted) {
        this.state.set(entry.object_id, "failed");
        this.active--;
        const waiters = this.completions.splice(0);
        for (const waiter of waiters) waiter.resolve();
        return;
      }
      this.settle(entry.object_id, true, error instanceof Error ? error.message : String(error));
    }
  }

  /** Fetch one shared texture through the verified resolver exactly once. */
  private ensureTexture(uri: string): Promise<string> {
    const pending = this.textureUrlPending.get(uri);
    if (pending !== undefined) return pending;
    const sha256 = uri.slice("assets/".length);
    const ref = this.textureRefs.get(sha256);
    if (ref === undefined) {
      return Promise.reject(
        new Error(`Building render texture ${sha256} is missing from the manifest texture store`));
    }
    const promise = this.resolver.fetchVerified(uri, {
      sha256, sizeBytes: ref.bytes, mediaType: ref.media_type,
    }, this.abort.signal).then(resolved => {
      this.textureUrls.set(uri, resolved.url);
      return resolved.url;
    });
    this.textureUrlPending.set(uri, promise);
    return promise;
  }

  private async parseGlb(bytes: ArrayBuffer, entry: BuildingRenderEntry): Promise<THREE.Object3D> {
    const document = validateBuildingRenderGlb(bytes, entry, this.manifest);
    const plan = texturePlan(document);
    const uris = new Set<string>();
    for (const raw of asArray(document.images, "images")) {
      const uri = asObject(raw, "image").uri;
      if (typeof uri !== "string") throw new Error("Building render image URI is missing");
      uris.add(uri);
    }
    // Every referenced texture must be verified before GLTFLoader parses,
    // so the URL modifier can only ever hand it bytes of known digests.
    await Promise.all([...uris].map(uri => this.ensureTexture(uri)));
    const gltf = await this.loader.parseAsync(bytes, "");
    const facadeMaterials = new Set<THREE.MeshStandardMaterial>();
    gltf.scene.traverse(node => {
      if (!(node instanceof THREE.Mesh)) return;
      node.castShadow = true;
      node.receiveShadow = true;
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        if (material instanceof THREE.MeshStandardMaterial) facadeMaterials.add(material);
      }
    });
    const replacements = new Map<THREE.Texture, THREE.Texture>();
    for (const [textureIndex, planEntry] of plan) {
      const original = await gltf.parser.getDependency("texture", textureIndex) as THREE.Texture;
      if (!(original instanceof THREE.Texture)) {
        throw new Error(`Building render texture ${planEntry.sha256} failed to load`);
      }
      const shared = this.textureCache.get(planEntry.sha256);
      if (shared === undefined) {
        original.colorSpace = planEntry.colorSpace;
        original.wrapS = THREE.RepeatWrapping;
        original.wrapT = THREE.RepeatWrapping;
        original.anisotropy = this.anisotropy;
        original.needsUpdate = true;
        this.textureCache.set(planEntry.sha256, original);
        continue;
      }
      if (shared.colorSpace !== planEntry.colorSpace) {
        throw new Error(`Building render texture ${planEntry.sha256} is used with two color spaces`);
      }
      if (shared !== original) {
        replacements.set(original, shared);
        original.dispose();
      }
    }
    if (replacements.size > 0) {
      for (const material of facadeMaterials) {
        for (const slot of ["map", "normalMap", "emissiveMap"] as const) {
          const texture = material[slot];
          if (texture !== null && texture !== undefined && replacements.has(texture)) {
            material[slot] = replacements.get(texture)!;
          }
        }
        material.needsUpdate = true;
      }
    }
    const sharedMaterials = new Set<THREE.MeshStandardMaterial>();
    const materialReplacements = new Map<THREE.Material, THREE.MeshStandardMaterial>();
    for (const material of facadeMaterials) {
      const index = gltf.parser.associations.get(material)?.materials;
      if (index === undefined) throw new Error("Building material lost its verified GLTF association");
      const shared = shareBuildingRenderMaterial(material, document, index, this.materialCache);
      materialReplacements.set(material, shared); sharedMaterials.add(shared);
    }
    gltf.scene.traverse(node => {
      if (!(node instanceof THREE.Mesh)) return;
      if (Array.isArray(node.material)) {
        node.material = node.material.map(material => materialReplacements.get(material) ?? material);
      } else node.material = materialReplacements.get(node.material) ?? node.material;
    });
    return assembleBuildingVisual(entry, gltf.scene, [...sharedMaterials]);
  }

  /** Load every building whose block intersects the camera frustum; resolves when that scope is terminal. */
  async prime(camera: THREE.Camera): Promise<void> {
    if (this.disposed) return;
    this.lastCamera = camera;
    const frustum = this.frustum(camera);
    const scope = new Set<string>();
    for (const block of this.manifest.blocks) {
      if (!frustum.intersectsSphere(new THREE.Sphere(this.blockCenter(block), this.blockRadius(block)))) continue;
      for (const objectId of block.object_ids) scope.add(objectId);
    }
    if (scope.size === 0) {
      for (const objectId of this.state.keys()) scope.add(objectId);
    }
    void this.run();
    await this.waitUntil(() =>
      [...scope].every(objectId => {
        const state = this.state.get(objectId);
        return state === "placed" || state === "failed" || this.disposed;
      }));
    this.batches.flush();
  }

  /** Prioritise with the current camera and stream the remainder in the background. */
  update(camera: THREE.Camera): void {
    if (this.disposed) return;
    this.lastCamera = camera;
    // The batch group flushes in its updateMatrixWorld hook before every render.
    void this.run();
  }

  /** Wait until every building reached a terminal state. */
  async loadAll(): Promise<void> {
    if (this.disposed) return;
    const run = this.run();
    await this.waitUntil(() => [...this.state.values()].every(state => state !== "pending" && state !== "active"));
    await run;
    this.batches.flush();
  }

  /** Compile the same ordinary Mesh programs used by the first streamed frame. */
  async prepareRenderer(renderer: THREE.WebGLRenderer, camera: THREE.Camera,
    scene: THREE.Scene): Promise<void> {
    this.batches.flush();
    this.batches.prepareRenderer(renderer);
    const textures = new Set<THREE.Texture>();
    this.batches.group.traverse(node => {
      if (!(node instanceof THREE.Mesh)) return;
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
      }
    });
    await runInFrameSlices(textures, texture => renderer.initTexture(texture));
    await renderer.compileAsync(this.batches.group, camera, scene);
    await waitForGpuCommands(renderer);
    // compileAsync only prepares surface materials. Render once through Three's
    // normal clipping/shadow lifecycle to initialize depth/distance variants too.
    this.batches.warmShadowPrograms(renderer, camera, scene);
  }

  private async waitUntil(predicate: () => boolean): Promise<void> {
    while (!predicate()) {
      if (this.disposed) return;
      if (this.active === 0 && !this.running && !predicate()) {
        // Queue stalled without active work: restart the pump once.
        await this.run();
        continue;
      }
      await new Promise<void>(resolve => this.completions.push({ resolve }));
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.abort.abort();
    if (this.renderFrame !== null) cancelAnimationFrame(this.renderFrame);
    this.renderFrame = null;
    this.batches.dispose();
    this.textureCache.clear();
    this.materialCache.clear();
    this.textureUrlPending.clear();
    this.textureUrls.clear();
    const waiters = this.completions.splice(0);
    for (const waiter of waiters) waiter.resolve();
  }
}

/**
 * Time-of-day material policy for the render GLBs. Day and twilight retain the
 * former CITY_LIGHTING output; night uses a separate local-source policy.
 */
export function setBuildingRenderLighting(root: THREE.Object3D, timeOfDay: CityTimeOfDay,
                                          environment: THREE.Texture): void {
  const lighting = CITY_SUBSYSTEM_LIGHTING[timeOfDay];
  for (const material of collectRenderMaterials(root)) {
    material.emissiveIntensity = lighting.buildingWindowIntensity;
    material.envMapIntensity = lighting.buildingEnvironmentIntensity;
    if (material.envMap !== environment) {
      material.envMap = environment;
      material.needsUpdate = true;
    }
  }
}

function collectRenderMaterials(root: THREE.Object3D): THREE.MeshStandardMaterial[] {
  // Lazy buildings attach after a mood switch, so collect fresh every call.
  const collected = new Set<THREE.MeshStandardMaterial>();
  const gather = (source: THREE.Object3D): void => {
    const materials = source.userData.renderMaterials as THREE.MeshStandardMaterial[] | undefined;
    if (materials !== undefined) for (const material of materials) collected.add(material);
  };
  gather(root);
  for (const child of root.children) gather(child);
  return [...collected];
}
