import { AssetResolver, type DeclaredAsset } from "./asset-resolver";
import {
  BUILDING_RENDER_PLACEMENT_RULE,
  parseBuildingRenderManifest,
  parseBuildingRenderOrigin,
  type BuildingRenderEntry,
  type BuildingRenderManifest,
  type BuildingRenderManifestRef,
} from "./city-building-renders";
import type { LoadedMeshPack } from "./osm2world/pack-loader";

export const BUILDING_RENDER_SOURCE_CONTEXT_SCHEMA = "aero-bench.building-render-source-context/v1";

export type BuildingRenderSourceEntry = Pick<BuildingRenderEntry,
  "object_id" | "osm" | "glb" | "style_id" | "anchor_east_m" | "anchor_north_m"
  | "base_enu_up_m" | "height_m" | "envelope" | "pack_target_ids">
  & { readonly geometry: { readonly vertices: number; readonly triangles: number } };

/** Source summaries are derived from verified objects, canonical GLBs and pack metadata. */
export interface BuildingRenderSourceContext {
  readonly schema_version: typeof BUILDING_RENDER_SOURCE_CONTEXT_SCHEMA;
  readonly scene: BuildingRenderManifest["scene"];
  readonly sources: BuildingRenderManifest["sources"];
  readonly buildings: readonly BuildingRenderSourceEntry[];
}

export interface VerifiedBuildingRenderSourceContext {
  readonly context: BuildingRenderSourceContext;
  readonly sha256: string;
  readonly size_bytes: number;
}

export interface BuildingRenderContext {
  /** The selected scene declares this identity; do not derive it from the render manifest. */
  readonly expectedSceneId: string;
  readonly pack: Pick<LoadedMeshPack, "manifest" | "manifestSha256">;
  readonly source: VerifiedBuildingRenderSourceContext;
}

const verifiedContexts = new WeakSet<VerifiedBuildingRenderSourceContext>();
const OBJECT_ID = /^building\.(way|relation)\.\d+\.component\.\d+$/;
const PACK_ID = /^[wr]-?\d+$/;

function invalid(message: string): never {
  throw new Error(`Building render source context is invalid: ${message}`);
}

function object(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid(`${label} must be an object`);
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== keys.length || keys.some(key => !Object.hasOwn(result, key))) {
    invalid(`${label} fields must match the source context contract`);
  }
  return result;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) invalid(`${label} must be a non-empty string`);
  return value;
}

function digest(value: unknown, label: string): string {
  const result = text(value, label);
  if (!/^[0-9a-f]{64}$/.test(result) || /^0+$/.test(result)) invalid(`${label} must be a nonzero lowercase sha256`);
  return result;
}

function number(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) invalid(`${label} must be finite`);
  return value;
}

function integer(value: unknown, label: string, minimum: number): number {
  const result = number(value, label);
  if (!Number.isSafeInteger(result) || result < minimum) invalid(`${label} must be a safe integer >= ${minimum}`);
  return result;
}

function freeze<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

export function parseBuildingRenderSourceContext(value: unknown): BuildingRenderSourceContext {
  const root = object(value, ["schema_version", "scene", "sources", "buildings"], "root");
  if (root.schema_version !== BUILDING_RENDER_SOURCE_CONTEXT_SCHEMA) invalid("unsupported schema_version");
  const scene = object(root.scene, ["id", "coordinate_frame", "origin_wgs84", "objects_json_sha256",
    "placement_rule", "mesh_pack_manifest_sha256", "mesh_pack_source_sha256"], "scene");
  if (scene.coordinate_frame !== "ENU" || scene.placement_rule !== BUILDING_RENDER_PLACEMENT_RULE) {
    invalid("scene frame and placement must use the ENU metre contract");
  }
  const sources = object(root.sources, ["scaleout_glb_combined_sha256", "scaleout_manifest_sha256",
    "catalog_fragment_sha256"], "sources");
  if (!Array.isArray(root.buildings) || root.buildings.length === 0) invalid("buildings must be a non-empty array");
  const buildings = root.buildings.map((raw, index): BuildingRenderSourceEntry => {
    const label = `buildings[${index}]`;
    const entry = object(raw, ["object_id", "osm", "glb", "style_id", "anchor_east_m", "anchor_north_m",
      "base_enu_up_m", "height_m", "envelope", "pack_target_ids", "geometry"], label);
    const id = text(entry.object_id, `${label}.object_id`);
    if (!OBJECT_ID.test(id)) invalid(`${label}.object_id is unsupported`);
    const osm = object(entry.osm, ["type", "id"], `${id}.osm`);
    if (osm.type !== "way" && osm.type !== "relation") invalid(`${id}.osm.type is unsupported`);
    const osmId = integer(osm.id, `${id}.osm.id`, 1);
    if (!id.startsWith(`building.${osm.type}.${osmId}.component.`)) invalid(`${id}.osm disagrees with object_id`);
    const glb = object(entry.glb, ["path", "sha256", "bytes"], `${id}.glb`);
    const geometry = object(entry.geometry, ["vertices", "triangles"], `${id}.geometry`);
    const sha256 = digest(glb.sha256, `${id}.glb.sha256`);
    if (glb.path !== `assets/${sha256}`) invalid(`${id}.glb.path must be content-addressed`);
    const envelope = object(entry.envelope, ["min_e", "min_n", "max_e", "max_n", "base_up", "top_up"], `${id}.envelope`);
    const parsedEnvelope = {
      min_e: number(envelope.min_e, `${id}.envelope.min_e`), min_n: number(envelope.min_n, `${id}.envelope.min_n`),
      max_e: number(envelope.max_e, `${id}.envelope.max_e`), max_n: number(envelope.max_n, `${id}.envelope.max_n`),
      base_up: number(envelope.base_up, `${id}.envelope.base_up`), top_up: number(envelope.top_up, `${id}.envelope.top_up`),
    };
    const anchorEast = number(entry.anchor_east_m, `${id}.anchor_east_m`);
    const anchorNorth = number(entry.anchor_north_m, `${id}.anchor_north_m`);
    const baseUp = number(entry.base_enu_up_m, `${id}.base_enu_up_m`);
    const height = number(entry.height_m, `${id}.height_m`);
    if (height <= 0 || parsedEnvelope.min_e >= parsedEnvelope.max_e || parsedEnvelope.min_n >= parsedEnvelope.max_n
        || anchorEast < parsedEnvelope.min_e || anchorEast > parsedEnvelope.max_e
        || anchorNorth < parsedEnvelope.min_n || anchorNorth > parsedEnvelope.max_n
        || Math.abs(parsedEnvelope.base_up - baseUp) > 1e-3
        || Math.abs(parsedEnvelope.top_up - baseUp - height) > 1e-3) invalid(`${id} has inconsistent placement metres`);
    if (!Array.isArray(entry.pack_target_ids)) invalid(`${id}.pack_target_ids must be an array`);
    const targets = entry.pack_target_ids.map((target, position) => text(target, `${id}.pack_target_ids[${position}]`));
    if (targets.some((target, position) => !PACK_ID.test(target) || (position > 0 && targets[position - 1]! >= target))) {
      invalid(`${id}.pack_target_ids must be sorted and unique`);
    }
    return {
      object_id: id, osm: { type: osm.type, id: osmId },
      glb: { path: `assets/${sha256}`, sha256, bytes: integer(glb.bytes, `${id}.glb.bytes`, 1024) },
      style_id: text(entry.style_id, `${id}.style_id`), anchor_east_m: anchorEast, anchor_north_m: anchorNorth,
      base_enu_up_m: baseUp, height_m: height, envelope: parsedEnvelope, pack_target_ids: targets,
      geometry: { vertices: integer(geometry.vertices, `${id}.geometry.vertices`, 3),
        triangles: integer(geometry.triangles, `${id}.geometry.triangles`, 1) },
    };
  });
  if (buildings.some((entry, index) => index > 0 && buildings[index - 1]!.object_id >= entry.object_id)) {
    invalid("building object ids must be sorted and unique");
  }
  if (new Set(buildings.map(entry => entry.glb.sha256)).size !== buildings.length) invalid("canonical glb digests must be unique");
  const targets = buildings.flatMap(entry => entry.pack_target_ids);
  if (new Set(targets).size !== targets.length) invalid("pack target ids must be unique across buildings");
  return {
    schema_version: BUILDING_RENDER_SOURCE_CONTEXT_SCHEMA,
    scene: {
      id: text(scene.id, "scene.id"), coordinate_frame: "ENU", origin_wgs84: parseBuildingRenderOrigin(scene.origin_wgs84),
      objects_json_sha256: digest(scene.objects_json_sha256, "scene.objects_json_sha256"),
      placement_rule: BUILDING_RENDER_PLACEMENT_RULE,
      mesh_pack_manifest_sha256: digest(scene.mesh_pack_manifest_sha256, "scene.mesh_pack_manifest_sha256"),
      mesh_pack_source_sha256: digest(scene.mesh_pack_source_sha256, "scene.mesh_pack_source_sha256"),
    },
    sources: {
      scaleout_glb_combined_sha256: digest(sources.scaleout_glb_combined_sha256, "sources.scaleout_glb_combined_sha256"),
      scaleout_manifest_sha256: digest(sources.scaleout_manifest_sha256, "sources.scaleout_manifest_sha256"),
      catalog_fragment_sha256: digest(sources.catalog_fragment_sha256, "sources.catalog_fragment_sha256"),
    },
    buildings,
  };
}

/** A digest-verified summary is viewer evidence; it does not seal or authorize a formal run. */
export async function fetchBuildingRenderSourceContext(resolver: AssetResolver, ref: BuildingRenderManifestRef,
                                                      signal?: AbortSignal): Promise<VerifiedBuildingRenderSourceContext> {
  const declared: DeclaredAsset = { sha256: ref.sha256, sizeBytes: ref.size_bytes, mediaType: "application/json" };
  const bytes = await resolver.fetchVerifiedBytes(`assets/${ref.sha256}`, declared, signal);
  let value: unknown;
  try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
  catch { invalid("source context is not valid UTF-8 JSON"); }
  const result = freeze({ context: parseBuildingRenderSourceContext(value), sha256: ref.sha256, size_bytes: ref.size_bytes });
  verifiedContexts.add(result);
  return result;
}

export function validateBuildingRenderContext(render: BuildingRenderManifest, context: BuildingRenderContext): void {
  if (context === undefined || context === null || typeof context.expectedSceneId !== "string"
      || !context.expectedSceneId.trim()) invalid("selected scene expected identity is missing");
  if (!context.source || !verifiedContexts.has(context.source)) invalid("source context has not been digest verified");
  if (!context.pack || !context.pack.manifest) invalid("verified mesh pack is not ready");
  const manifest = parseBuildingRenderManifest(render);
  const source = context.source.context;
  const pack = context.pack;
  if (manifest.scene.id !== context.expectedSceneId || source.scene.id !== context.expectedSceneId) {
    invalid("scene id does not match the selected source identity");
  }
  for (const key of ["objects_json_sha256", "placement_rule", "mesh_pack_manifest_sha256", "mesh_pack_source_sha256"] as const) {
    if (manifest.scene[key] !== source.scene[key]) invalid(`scene.${key} differs from the verified source`);
  }
  if (pack.manifestSha256 !== source.scene.mesh_pack_manifest_sha256
      || pack.manifest.source.sha256 !== source.scene.mesh_pack_source_sha256) invalid("mesh pack manifest or source digest mismatch");
  for (const key of Object.keys(source.scene.origin_wgs84) as (keyof typeof source.scene.origin_wgs84)[]) {
    if (manifest.scene.origin_wgs84[key] !== source.scene.origin_wgs84[key]) invalid(`origin_wgs84.${key} mismatch`);
  }
  for (const key of ["latitude_deg", "longitude_deg"] as const) {
    if (pack.manifest.projection.origin[key] !== source.scene.origin_wgs84[key]) invalid(`mesh pack origin ${key} mismatch`);
  }
  for (const key of Object.keys(source.sources) as (keyof typeof source.sources)[]) {
    if (manifest.sources[key] !== source.sources[key]) invalid(`sources.${key} mismatch`);
  }
  if (manifest.buildings.length !== source.buildings.length) invalid("source building count mismatch");
  if (manifest.art_detail !== undefined) {
    for (const key of ["vertices", "triangles"] as const) {
      if (manifest.counts[`source_${key}`] !== source.buildings.reduce((sum, entry) => sum + entry.geometry[key], 0)) {
        invalid(`source geometry ${key} count mismatch`);
      }
    }
  }
  const rendered = new Map(manifest.buildings.map(entry => [entry.object_id, entry]));
  const packTargets = new Set(pack.manifest.batches.flatMap(batch => batch.ranges
    .filter(range => range.target?.kind === "building").map(range => range.target!.id)));
  // Object metadata uses JS Number spelling; triangle targets use the exact
  // represented integer. This is the current pack contract for synthetic IDs.
  const packObjects = pack.manifest.objects.filter(item => /^[wr]-?\d+$/.test(item.id)).map(item => {
    const numberId = Number(item.id.slice(1));
    if (!Number.isFinite(numberId) || !Number.isInteger(numberId)) invalid("pack object id has no finite integer representation");
    return { id: item.id[0]! + BigInt(numberId).toString(), tags: item.tags };
  }).filter(item => packTargets.has(item.id));
  if (new Set(packObjects.map(item => item.id)).size !== packObjects.length) invalid("pack object ids collide in the target representation");
  const linked = new Set<string>();
  for (const expected of source.buildings) {
    const actual = rendered.get(expected.object_id);
    if (actual === undefined) invalid(`${expected.object_id} is missing from the render manifest`);
    for (const key of ["style_id", "anchor_east_m", "anchor_north_m", "base_enu_up_m", "height_m"] as const) {
      if (actual[key] !== expected[key]) invalid(`${expected.object_id}.${key} source mismatch`);
    }
    for (const key of ["sha256", "bytes", "path"] as const) {
      if (actual.glb[key] !== expected.glb[key]) invalid(`${expected.object_id}.glb.${key} source mismatch`);
    }
    for (const key of Object.keys(expected.envelope) as (keyof typeof expected.envelope)[]) {
      if (actual.envelope[key] !== expected.envelope[key]) invalid(`${expected.object_id}.envelope.${key} source mismatch`);
    }
    if (actual.osm.type !== expected.osm.type || actual.osm.id !== expected.osm.id
        || actual.pack_target_ids.join("\n") !== expected.pack_target_ids.join("\n")) invalid(`${expected.object_id} source id mapping mismatch`);
    const component = expected.object_id.split(".component.")[1]!;
    const matched = packObjects.filter(item => item.tags["aero_bench:source_type"] === expected.osm.type
      && item.tags["aero_bench:source_id"] === String(expected.osm.id)
      && (item.tags["aero_bench:component_index"] ?? "0") === component).map(item => item.id).sort();
    if (matched.join("\n") !== expected.pack_target_ids.join("\n")) invalid(`${expected.object_id} does not match the verified pack object mapping`);
    for (const target of matched) linked.add(target);
  }
  if (linked.size !== packTargets.size || [...packTargets].some(target => !linked.has(target))) {
    invalid("verified pack building targets are not covered exactly");
  }
}
