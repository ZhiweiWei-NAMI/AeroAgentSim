import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { AssetResolver, type AssetResolverOptions } from "./asset-resolver";
import { readGlbDocument } from "./city-building-renders";
import { parseCityEnvironmentSource, type EnvironmentPoint, type EnvironmentPolygon } from "./city-environment";
import { parseCityGroundCovers } from "./city-ground-cover";
import { groundMaterialInputFromCover, groundMaterialInputFromGreen } from "./city-ground-material-rules";
import { assignCityGroundMaterial, createTerrainSurfaceKit, loadVerifiedTerrainTextureSets,
  terrainTextureSetIds, TERRAIN_TEXTURE_LIBRARY_SHA256, type TerrainSurfaceKit } from "./city-terrain-surfaces";
import { createSurfaceWetnessUniforms } from "./city-surface-wetness";
import { createWaterSurfaceUniforms } from "./city-water-surface";
import { crossingStripeSegments, laneOffsets, sameJson, triangulateUpward,
  validateCanonicalCityRoadPayload } from "./city-roads";
import type { PublicBuilding, PublicEntityDefinition, PublicScenario,
  PublicScenarioAsset } from "./generated/aero-bench-contracts";
import { spatialRoadClearanceFromNativeGeometry, type SpatialRoadClearance } from "./city-spatial-road-clearance";
import { parseStrictJson } from "./strict-json";
import { assertNotAborted } from "./verified-bytes";

const NATIVE_LAYER_KINDS = ["osm_scene", "roads", "entities", "regions"] as const;
type NativeLayerKind = typeof NATIVE_LAYER_KINDS[number];

const BUILDING_PLACEMENT_RULE =
  "ENU_east = anchor_east_m + X; ENU_north = anchor_north_m - Z; ENU_up = base_enu_up_m + Y";
const BUILDING_ENVELOPE_TOLERANCE_M = 0.001;
const DEFAULT_BUILDING_CONCURRENCY = 4;

export interface NativeCityBuildingPlan {
  readonly building: PublicBuilding;
  readonly entity: PublicEntityDefinition;
  readonly asset: PublicScenarioAsset;
}

export interface NativeCityPresentationPlan {
  readonly kind: "native-city-assets";
  readonly scenarioDigest: string;
  readonly worldId: string;
  readonly worldDigest: string;
  readonly originWgs84: {
    readonly latitude_deg: number;
    readonly longitude_deg: number;
    readonly ellipsoid_height_m: number;
  };
  readonly layers: Readonly<Record<NativeLayerKind, PublicScenarioAsset>>;
  readonly buildings: readonly NativeCityBuildingPlan[];
  readonly totalBytes: number;
}

export type NativeCityPresentationInspection =
  | { readonly kind: "mesh-pack" }
  | { readonly kind: "native-city"; readonly plan: NativeCityPresentationPlan }
  | { readonly kind: "unavailable"; readonly reason: string };

export interface NativeCityPresentationProgress {
  readonly phase: "layers" | "buildings";
  readonly completed: number;
  readonly total: number;
  readonly verifiedBytes: number;
  readonly totalBytes: number;
  readonly assetId: string;
}

export interface NativeCityLayerVisibility {
  readonly buildings: boolean;
  readonly roads: boolean;
  readonly regions: boolean;
  readonly static_assets: boolean;
}

export interface NativeCityPresentationStats {
  readonly buildingCount: number;
  readonly roadbedPolygonCount: number;
  readonly walkbedPolygonCount: number;
  readonly fixtureCount: number;
  readonly omittedFixtureInteriorRingCount: number;
  readonly greenCount: number;
  readonly groundCoverCount: number;
  readonly osmElementCount: number;
  readonly verifiedBytes: number;
}

export interface LoadedNativeCityPresentation {
  readonly group: THREE.Group;
  readonly layers: {
    readonly buildings: THREE.Group;
    readonly roads: THREE.Group;
    readonly fixtures: THREE.Group;
    readonly regions: THREE.Group;
  };
  readonly bounds: THREE.Box3;
  readonly stats: NativeCityPresentationStats;
  readonly roadClearance: SpatialRoadClearance;
  setLayerVisibility(visibility: NativeCityLayerVisibility): void;
  dispose(): void;
}

export interface NativeCityPresentationLoadOptions {
  readonly baseHref: string;
  readonly signal?: AbortSignal;
  readonly concurrency?: number;
  readonly onProgress?: (progress: NativeCityPresentationProgress) => void;
  /** Test seam. Production parses the already verified, self-contained GLB bytes. */
  readonly parseGlb?: (bytes: ArrayBuffer, binding: NativeCityBuildingPlan) => Promise<THREE.Object3D>;
  /** Resolver seams are for focused tests; production uses Web Crypto and global fetch. */
  readonly fetch?: AssetResolverOptions["fetch"];
  readonly digest?: AssetResolverOptions["digest"];
}

export interface NativeBuildingGlbFrame {
  readonly anchorEastM: number;
  readonly anchorNorthM: number;
  readonly baseUpM: number;
  readonly measuredBounds: THREE.Box3;
}

interface NativeFixture {
  readonly id: string;
  readonly kind: "signal" | "street_lamp";
  readonly baseUpM: number;
  readonly topUpM: number;
  readonly footprints: readonly EnvironmentPolygon[];
  readonly omittedInteriorRingCount: number;
}

interface DraftGeometry {
  readonly positions: number[];
  readonly indices: number[];
}

interface PinnedOsmSceneInventory { readonly elementCount: number; }

const WIDE_JSON_INTEGER = "aero-bench:wide-json-integer:";

function preserveWideJsonIntegers(source: string): string {
  let result = "", index = 0, inString = false, escaped = false;
  while (index < source.length) {
    const character = source[index]!;
    if (inString) {
      result += character;
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === "\"") inString = false;
      index++;
      continue;
    }
    if (character === "\"") {
      inString = true;
      result += character;
      index++;
      continue;
    }
    if (character === "-" || character >= "0" && character <= "9") {
      const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(source.slice(index));
      if (match !== null) {
        const token = match[0];
        if (!/[.eE]/.test(token)) {
          const exact = BigInt(token);
          if (exact > BigInt(Number.MAX_SAFE_INTEGER) || exact < BigInt(Number.MIN_SAFE_INTEGER)) {
            result += JSON.stringify(`${WIDE_JSON_INTEGER}${token}`);
            index += token.length;
            continue;
          }
        }
        result += token;
        index += token.length;
        continue;
      }
    }
    result += character;
    index++;
  }
  return result;
}

function osmIdentity(value: unknown, label: string): string {
  if (typeof value === "number" && Number.isSafeInteger(value)) return String(value);
  if (typeof value === "string" && value.startsWith(WIDE_JSON_INTEGER)
      && /^-?(?:0|[1-9]\d*)$/.test(value.slice(WIDE_JSON_INTEGER.length))) {
    return value.slice(WIDE_JSON_INTEGER.length);
  }
  throw new Error(`Native city ${label} must be an exact OSM integer identity`);
}

/** Keep synthetic 64-bit OSM identities exact; only the inventory count leaves this boundary. */
function parsePinnedOsmScene(source: string): PinnedOsmSceneInventory {
  const root = record(parseStrictJson(preserveWideJsonIntegers(source)), "OSM scene");
  const elements = array(root.elements, "OSM scene elements");
  if (root.version !== 0.6) throw new Error("Native city OSM scene must use OSM JSON 0.6");
  if (root.generator !== undefined && typeof root.generator !== "string") {
    throw new Error("Native city OSM scene generator is invalid");
  }
  if (root.bounds !== undefined) {
    const bounds = record(root.bounds, "OSM scene bounds");
    const minlat = finite(bounds.minlat, "OSM scene bounds.minlat");
    const minlon = finite(bounds.minlon, "OSM scene bounds.minlon");
    const maxlat = finite(bounds.maxlat, "OSM scene bounds.maxlat");
    const maxlon = finite(bounds.maxlon, "OSM scene bounds.maxlon");
    if (minlat > maxlat || minlon > maxlon) throw new Error("Native city OSM scene bounds are inverted");
  }
  const identities = new Set<string>();
  const references: { readonly identity: string; readonly label: string }[] = [];
  for (let index = 0; index < elements.length; index++) {
    const element = record(elements[index], `OSM scene element[${index}]`);
    const kind = element.type;
    if (kind !== "node" && kind !== "way" && kind !== "relation") {
      throw new Error(`Native city OSM scene element[${index}] type is invalid`);
    }
    const identity = `${kind}:${osmIdentity(element.id, `OSM scene element[${index}].id`)}`;
    if (identities.has(identity)) throw new Error("Native city OSM scene identities are duplicated");
    identities.add(identity);
    if (element.tags !== undefined) {
      const tags = record(element.tags, `OSM scene element[${index}].tags`);
      if (Object.values(tags).some(tag => typeof tag !== "string" || tag.length === 0)) {
        throw new Error(`Native city OSM scene element[${index}] tags are invalid`);
      }
    }
    if (kind === "node") {
      finite(element.lat, `OSM scene node[${index}].lat`);
      finite(element.lon, `OSM scene node[${index}].lon`);
    } else if (kind === "way") {
      const nodes = array(element.nodes, `OSM scene way[${index}].nodes`);
      if (nodes.length < 2) throw new Error(`Native city OSM scene way[${index}] has too few nodes`);
      for (let node = 0; node < nodes.length; node++) references.push({
        identity: `node:${osmIdentity(nodes[node], `OSM scene way[${index}].nodes[${node}]`)}`,
        label: `way ${identity}`,
      });
    } else {
      const members = array(element.members, `OSM scene relation[${index}].members`);
      for (let memberIndex = 0; memberIndex < members.length; memberIndex++) {
        const member = record(members[memberIndex], `OSM scene relation[${index}].members[${memberIndex}]`);
        const memberKind = member.type;
        if (memberKind !== "node" && memberKind !== "way" && memberKind !== "relation"
            || typeof member.role !== "string") {
          throw new Error(`Native city OSM scene relation[${index}] member is invalid`);
        }
        references.push({ identity: `${memberKind}:${osmIdentity(member.ref,
          `OSM scene relation[${index}].members[${memberIndex}].ref`)}`, label: `relation ${identity}` });
      }
    }
  }
  for (const reference of references) if (!identities.has(reference.identity)) {
    throw new Error(`Native city OSM scene ${reference.label} references missing ${reference.identity}`);
  }
  return Object.freeze({ elementCount: elements.length });
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Native city ${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`Native city ${label} must be an array`);
  return value;
}

function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Native city ${label} must be finite`);
  }
  return value;
}

function integer(value: unknown, label: string, minimum = 0): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw new Error(`Native city ${label} must be a safe integer >= ${minimum}`);
  }
  return value;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Native city ${label} must be a non-empty string`);
  }
  return value;
}

function assetMap(scenario: PublicScenario): ReadonlyMap<string, PublicScenarioAsset> {
  const map = new Map<string, PublicScenarioAsset>();
  for (const asset of scenario.assets) {
    if (map.has(asset.asset_id)) throw new Error(`Native city asset identity is duplicated: ${asset.asset_id}`);
    map.set(asset.asset_id, asset);
  }
  return map;
}

function layerAsset(scenario: PublicScenario, assets: ReadonlyMap<string, PublicScenarioAsset>,
    kind: NativeLayerKind): PublicScenarioAsset {
  const bindings = scenario.layers.filter(layer => layer.kind === kind);
  if (bindings.length !== 1 || bindings[0]!.visibility !== "public" || !bindings[0]!.default_visible) {
    throw new Error(`Native city requires exactly one public default-visible ${kind} layer`);
  }
  const asset = assets.get(bindings[0]!.asset_id);
  if (asset === undefined || asset.media_type !== "application/json") {
    throw new Error(`Native city ${kind} layer is not a declared JSON asset`);
  }
  return asset;
}

function assertBuildingCoordinates(building: PublicBuilding): void {
  if (building.base_vertices.length !== building.top_vertices.length || building.base_vertices.length < 3
      || ![building.anchor_east_m, building.anchor_north_m].every(Number.isFinite)) {
    throw new Error(`Native city building geometry is incomplete: ${building.building_id}`);
  }
  for (let index = 0; index < building.base_vertices.length; index++) {
    const base = building.base_vertices[index]!.enu;
    const top = building.top_vertices[index]!.enu;
    if (![base.east_m, base.north_m, base.up_m, top.east_m, top.north_m, top.up_m].every(Number.isFinite)
        || base.east_m !== top.east_m || base.north_m !== top.north_m || top.up_m <= base.up_m) {
      throw new Error(`Native city building vertices are inconsistent: ${building.building_id}`);
    }
  }
  const baseHeights = building.base_vertices.map(vertex => vertex.enu.up_m);
  const topHeights = building.top_vertices.map(vertex => vertex.enu.up_m);
  if (Math.max(...baseHeights) - Math.min(...baseHeights) > BUILDING_ENVELOPE_TOLERANCE_M
      || Math.max(...topHeights) - Math.min(...topHeights) > BUILDING_ENVELOPE_TOLERANCE_M) {
    throw new Error(`Native city building is not compatible with its flat asset-local GLB: ${building.building_id}`);
  }
}

/** Select the explicit native-city-assets route. A declared osm_mesh keeps the existing pack route. */
export function planNativeCityPresentation(scenario: PublicScenario): NativeCityPresentationPlan | null {
  if (scenario.layers.some(layer => layer.kind === "osm_mesh")) return null;
  const unsupported = scenario.layers.filter(layer => !NATIVE_LAYER_KINDS.includes(layer.kind as NativeLayerKind));
  if (unsupported.length > 0 || scenario.layers.length !== NATIVE_LAYER_KINDS.length) {
    throw new Error("Native city assets route supports exactly osm_scene, roads, entities, and regions layers");
  }
  const assets = assetMap(scenario);
  const layers = Object.freeze(Object.fromEntries(NATIVE_LAYER_KINDS.map(kind =>
    [kind, layerAsset(scenario, assets, kind)])) as unknown as Record<NativeLayerKind, PublicScenarioAsset>);
  if (new Set(Object.values(layers).map(asset => asset.asset_id)).size !== NATIVE_LAYER_KINDS.length) {
    throw new Error("Native city layer bindings must reference four distinct assets");
  }

  const seenBuildings = new Set<string>();
  const seenEntities = new Set<string>();
  const seenRenders = new Set<string>();
  if (scenario.buildings.length === 0) {
    throw new Error("Native city assets route requires declared per-building GLBs");
  }
  const buildings = scenario.buildings.map(building => {
    assertBuildingCoordinates(building);
    if (seenBuildings.has(building.building_id) || seenEntities.has(building.entity_id)
        || seenRenders.has(building.render_asset_id)) {
      throw new Error("Native city building, entity, and render identities must be one-to-one");
    }
    const matchingEntities = scenario.entities.filter(entity => entity.entity_id === building.entity_id);
    const entity = matchingEntities[0];
    if (matchingEntities.length !== 1 || entity === undefined || entity.kind !== "static_asset"
        || entity.state !== "static" || entity.owner_kind !== "scenario"
        || entity.authority_kind !== "scenario_static" || entity.model_asset_id !== building.render_asset_id) {
      throw new Error(`Native city building has no matching scenario-static entity: ${building.building_id}`);
    }
    const position = entity.initial_pose.position.enu;
    const orientation = entity.initial_pose.orientation_enu;
    if (![position.east_m, position.north_m, position.up_m, orientation.qx, orientation.qy,
      orientation.qz, orientation.qw].every(Number.isFinite)) {
      throw new Error(`Native city building entity pose is invalid: ${building.building_id}`);
    }
    const asset = assets.get(building.render_asset_id);
    if (asset === undefined || asset.media_type !== "model/gltf-binary") {
      throw new Error(`Native city building render asset is missing or not GLB: ${building.building_id}`);
    }
    seenBuildings.add(building.building_id);
    seenEntities.add(building.entity_id);
    seenRenders.add(building.render_asset_id);
    return Object.freeze({ building, entity, asset });
  });
  const declaredGlbs = scenario.assets.filter(asset => asset.media_type === "model/gltf-binary");
  if (declaredGlbs.length !== buildings.length
      || declaredGlbs.some(asset => !seenRenders.has(asset.asset_id))) {
    throw new Error("Native city contains a foreign or unbound GLB geometry asset");
  }
  const totalBytes = [...Object.values(layers), ...buildings.map(binding => binding.asset)]
    .reduce((total, asset) => total + asset.size_bytes, 0);
  if (!Number.isSafeInteger(totalBytes) || totalBytes <= 0) {
    throw new Error("Native city declared presentation byte total is invalid");
  }
  const sourceOrigin = scenario.frame_authority.origin.wgs84;
  const originWgs84 = {
    latitude_deg: sourceOrigin.latitude_deg,
    longitude_deg: sourceOrigin.longitude_deg,
    ellipsoid_height_m: sourceOrigin.ellipsoid_height_m,
  };
  if (!Object.values(originWgs84).every(Number.isFinite)) {
    throw new Error("Native city frame authority origin is invalid");
  }
  return Object.freeze({
    kind: "native-city-assets", scenarioDigest: scenario.scenario_digest,
    worldId: scenario.world_id, worldDigest: scenario.world_digest,
    originWgs84: Object.freeze(originWgs84), layers,
    buildings: Object.freeze(buildings), totalBytes,
  });
}

/** Inspect route readiness for UI state without weakening the strict planner or loader. */
export function inspectNativeCityPresentation(scenario: PublicScenario): NativeCityPresentationInspection {
  try {
    const plan = planNativeCityPresentation(scenario);
    return plan === null ? { kind: "mesh-pack" } : { kind: "native-city", plan };
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    return { kind: "unavailable", reason: error.message };
  }
}

function expectedBuildingBounds(building: PublicBuilding): THREE.Box3 {
  const box = new THREE.Box3();
  for (const vertex of [...building.base_vertices, ...building.top_vertices]) {
    box.expandByPoint(new THREE.Vector3(vertex.enu.east_m, vertex.enu.up_m, -vertex.enu.north_m));
  }
  return box;
}

function distanceToSegment(point: THREE.Vector2, start: THREE.Vector2, end: THREE.Vector2): number {
  const segment = end.clone().sub(start);
  const lengthSquared = segment.lengthSq();
  if (lengthSquared === 0) return point.distanceTo(start);
  const t = THREE.MathUtils.clamp(point.clone().sub(start).dot(segment) / lengthSquared, 0, 1);
  return point.distanceTo(start.clone().addScaledVector(segment, t));
}

function pointOnFootprint(point: THREE.Vector2, footprint: readonly THREE.Vector2[]): boolean {
  return footprint.some((start, index) => distanceToSegment(point, start,
    footprint[(index + 1) % footprint.length]!) <= BUILDING_ENVELOPE_TOLERANCE_M);
}

function pointInsideFootprint(point: THREE.Vector2, footprint: readonly THREE.Vector2[]): boolean {
  if (pointOnFootprint(point, footprint)) return true;
  let inside = false;
  for (let index = 0, prior = footprint.length - 1; index < footprint.length; prior = index++) {
    const a = footprint[index]!, b = footprint[prior]!;
    if ((a.y > point.y) !== (b.y > point.y)
        && point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}

function compareBounds(actual: THREE.Box3, expected: THREE.Box3, label: string): void {
  for (const axis of ["x", "y", "z"] as const) {
    if (Math.abs(actual.min[axis] - expected.min[axis]) > BUILDING_ENVELOPE_TOLERANCE_M
        || Math.abs(actual.max[axis] - expected.max[axis]) > BUILDING_ENVELOPE_TOLERANCE_M) {
      throw new Error(`Native city ${label} differs from its PublicBuilding envelope`);
    }
  }
}

interface GlbView { readonly offset: number; readonly length: number; readonly stride?: number; }
interface GlbAccessor {
  readonly componentType: number;
  readonly count: number;
  readonly dimensions: number;
  readonly offset: number;
  readonly stride: number;
  readonly componentBytes: number;
  readonly type: string;
}

/** Validate the native building's complete embedded byte ranges and public geometry binding. */
export function validateNativeBuildingGlb(bytes: ArrayBuffer,
    binding: NativeCityBuildingPlan): NativeBuildingGlbFrame {
  const document = readGlbDocument(bytes);
  const asset = record(document.asset, "building GLB asset");
  const extras = record(asset.extras, "building GLB asset.extras");
  const frame = record(extras.frame, "building GLB frame");
  const buildingInfo = record(extras.building, "building GLB building metadata");
  if (asset.version !== "2.0" || extras.schema !== "aero-bench.building-render-scaleout/v0"
      || extras.object_id !== binding.building.building_id || extras.source_frame !== "asset_local"
      || frame.placement !== BUILDING_PLACEMENT_RULE) {
    throw new Error(`Native city building GLB identity or frame differs: ${binding.building.building_id}`);
  }
  const anchorEastM = finite(frame.anchor_east_m, "building GLB anchor_east_m");
  const anchorNorthM = finite(frame.anchor_north_m, "building GLB anchor_north_m");
  const baseUpM = finite(frame.base_enu_up_m, "building GLB base_enu_up_m");
  if (integer(buildingInfo.footprint_vertex_count, "building GLB footprint_vertex_count", 3)
        !== binding.building.base_vertices.length) {
    throw new Error(`Native city building GLB footprint inventory differs: ${binding.building.building_id}`);
  }
  const expected = expectedBuildingBounds(binding.building);
  if (Math.abs(finite(buildingInfo.height_m, "building GLB height_m")
      - (expected.max.y - expected.min.y)) > BUILDING_ENVELOPE_TOLERANCE_M) {
    throw new Error(`Native city building GLB height differs: ${binding.building.building_id}`);
  }

  const header = new DataView(bytes);
  const jsonLength = header.getUint32(12, true);
  const binLength = header.getUint32(20 + jsonLength, true);
  const buffers = array(document.buffers, "building GLB buffers");
  const buffer = buffers.length === 1 ? record(buffers[0], "building GLB buffer") : null;
  if (buffer === null || buffer.uri !== undefined
      || integer(buffer.byteLength, "building GLB buffer.byteLength", 1) !== binLength) {
    throw new Error("Native city building GLB must use one embedded BIN chunk");
  }
  const views: GlbView[] = array(document.bufferViews, "building GLB bufferViews").map((raw, index) => {
    const view = record(raw, `building GLB bufferViews[${index}]`);
    if (view.buffer !== 0) throw new Error("Native city building GLB references a foreign buffer");
    const offset = view.byteOffset === undefined ? 0 : integer(view.byteOffset, "building GLB byteOffset");
    const length = integer(view.byteLength, "building GLB byteLength", 1);
    const stride = view.byteStride === undefined ? undefined : integer(view.byteStride, "building GLB byteStride", 4);
    if (offset + length > binLength || stride !== undefined && (stride > 252 || stride % 4 !== 0)) {
      throw new Error("Native city building GLB bufferView exceeds its embedded BIN chunk");
    }
    return { offset, length, stride };
  });
  for (const raw of array(document.images ?? [], "building GLB images")) {
    const image = record(raw, "building GLB image");
    const view = integer(image.bufferView, "building GLB image.bufferView");
    if (views[view] === undefined || image.uri !== undefined
        || image.mimeType !== "image/png" && image.mimeType !== "image/jpeg") {
      throw new Error("Native city building GLB image is not embedded");
    }
  }
  if (document.extensionsRequired !== undefined
      && array(document.extensionsRequired, "building GLB extensionsRequired").length > 0) {
    throw new Error("Native city building GLB requires an unsupported extension");
  }
  const componentSizes: Readonly<Record<number, number>> = { 5120: 1, 5121: 1, 5122: 2,
    5123: 2, 5125: 4, 5126: 4 };
  const dimensions: Readonly<Record<string, number>> = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };
  const accessors: GlbAccessor[] = array(document.accessors, "building GLB accessors").map((raw, index) => {
    const input = record(raw, `building GLB accessors[${index}]`);
    const view = views[integer(input.bufferView, `building GLB accessors[${index}].bufferView`)];
    const componentType = integer(input.componentType, `building GLB accessors[${index}].componentType`);
    const componentBytes = componentSizes[componentType];
    const type = text(input.type, `building GLB accessors[${index}].type`);
    const dimension = dimensions[type];
    if (view === undefined || componentBytes === undefined || dimension === undefined || input.sparse !== undefined) {
      throw new Error(`Native city building GLB accessor ${index} is unsupported`);
    }
    const count = integer(input.count, `building GLB accessors[${index}].count`, 1);
    const localOffset = input.byteOffset === undefined ? 0
      : integer(input.byteOffset, `building GLB accessors[${index}].byteOffset`);
    const stride = view.stride ?? componentBytes * dimension;
    if (stride < componentBytes * dimension || localOffset % componentBytes !== 0
        || (view.offset + localOffset) % componentBytes !== 0
        || localOffset + (count - 1) * stride + componentBytes * dimension > view.length) {
      throw new Error(`Native city building GLB accessor ${index} exceeds its bufferView`);
    }
    return { componentType, count, dimensions: dimension, offset: view.offset + localOffset,
      stride, componentBytes, type };
  });

  const positionAccessors = new Set<number>();
  const meshes = array(document.meshes, "building GLB meshes");
  if (meshes.length === 0) throw new Error("Native city building GLB has no mesh");
  for (const raw of meshes) {
    for (const primitiveRaw of array(record(raw, "building GLB mesh").primitives, "building GLB primitives")) {
      const primitive = record(primitiveRaw, "building GLB primitive");
      if (primitive.mode !== undefined && primitive.mode !== 4) {
        throw new Error("Native city building GLB primitive is not triangles");
      }
      const attributes = record(primitive.attributes, "building GLB attributes");
      const positionIndex = integer(attributes.POSITION, "building GLB POSITION");
      const indexIndex = integer(primitive.indices, "building GLB indices");
      const position = accessors[positionIndex], indices = accessors[indexIndex];
      if (position?.type !== "VEC3" || position.componentType !== 5126 || indices?.type !== "SCALAR"
          || ![5121, 5123, 5125].includes(indices.componentType) || indices.count % 3 !== 0) {
        throw new Error("Native city building GLB primitive accessor contract differs");
      }
      positionAccessors.add(positionIndex);
      const data = new DataView(bytes, 28 + jsonLength + indices.offset);
      for (let index = 0; index < indices.count; index++) {
        const offset = index * indices.stride;
        const vertex = indices.componentBytes === 4 ? data.getUint32(offset, true)
          : indices.componentBytes === 2 ? data.getUint16(offset, true) : data.getUint8(offset);
        if (vertex >= position.count) throw new Error("Native city building GLB index is out of range");
      }
      for (const [name, rawIndex] of Object.entries(attributes)) {
        const accessor = accessors[integer(rawIndex, `building GLB ${name}`)];
        if (accessor === undefined || accessor.count !== position.count) {
          throw new Error(`Native city building GLB ${name} count differs from POSITION`);
        }
      }
    }
  }
  const nodes = array(document.nodes, "building GLB nodes");
  const scene = array(document.scenes, "building GLB scenes");
  if (nodes.length !== meshes.length || scene.length !== 1 || document.scene !== 0
      || document.animations !== undefined || document.skins !== undefined || document.cameras !== undefined) {
    throw new Error("Native city building GLB scene is not one static local scene");
  }
  const placedMeshes = new Set<number>();
  for (const raw of nodes) {
    const node = record(raw, "building GLB node");
    const mesh = integer(node.mesh, "building GLB node.mesh");
    const identityFields: Readonly<Record<string, readonly number[]>> = {
      translation: [0, 0, 0], rotation: [0, 0, 0, 1], scale: [1, 1, 1],
      matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    };
    if (mesh >= meshes.length || node.children !== undefined || node.skin !== undefined) {
      throw new Error("Native city building GLB node references foreign geometry");
    }
    for (const [key, expectedIdentity] of Object.entries(identityFields)) if (node[key] !== undefined) {
      const actual = array(node[key], `building GLB node.${key}`);
      if (actual.length !== expectedIdentity.length
          || actual.some((value, index) => value !== expectedIdentity[index])) {
        throw new Error("Native city building GLB node transform is not identity");
      }
    }
    placedMeshes.add(mesh);
  }
  const sceneNodes = array(record(scene[0], "building GLB scene").nodes, "building GLB scene.nodes");
  if (placedMeshes.size !== meshes.length || sceneNodes.length !== nodes.length
      || new Set(sceneNodes).size !== nodes.length
      || sceneNodes.some(index => typeof index !== "number" || !Number.isSafeInteger(index)
        || index < 0 || index >= nodes.length)) {
    throw new Error("Native city building GLB active scene does not cover each mesh once");
  }

  const footprint = binding.building.base_vertices.map(vertex =>
    new THREE.Vector2(vertex.enu.east_m, vertex.enu.north_m));
  const measured = new THREE.Box3();
  const basePoints: THREE.Vector2[] = [];
  const worldPoints: THREE.Vector2[] = [];
  for (const accessorIndex of positionAccessors) {
    const accessor = accessors[accessorIndex]!;
    const data = new DataView(bytes, 28 + jsonLength + accessor.offset);
    for (let vertex = 0; vertex < accessor.count; vertex++) {
      const offset = vertex * accessor.stride;
      const x = data.getFloat32(offset, true), y = data.getFloat32(offset + 4, true);
      const z = data.getFloat32(offset + 8, true);
      if (![x, y, z].every(Number.isFinite)) {
        throw new Error("Native city building GLB contains nonfinite geometry");
      }
      const east = anchorEastM + x, north = anchorNorthM - z, up = baseUpM + y;
      measured.expandByPoint(new THREE.Vector3(east, up, -north));
      const horizontal = new THREE.Vector2(east, north);
      worldPoints.push(horizontal);
      if (Math.abs(up - expected.min.y) <= BUILDING_ENVELOPE_TOLERANCE_M) basePoints.push(horizontal);
    }
  }
  compareBounds(measured, expected, `building GLB ${binding.building.building_id}`);
  if (basePoints.length === 0
      || footprint.some(expectedPoint => !basePoints.some(actual =>
        actual.distanceTo(expectedPoint) <= BUILDING_ENVELOPE_TOLERANCE_M))
      || basePoints.some(point => !pointOnFootprint(point, footprint))
      || worldPoints.some(point => !pointInsideFootprint(point, footprint))) {
    throw new Error(`Native city building GLB footprint differs: ${binding.building.building_id}`);
  }
  return { anchorEastM, anchorNorthM, baseUpM, measuredBounds: measured };
}

function openRing(value: unknown, label: string): EnvironmentPoint[] {
  const points = array(value, label).map((raw, index): EnvironmentPoint => {
    if (!Array.isArray(raw) || raw.length !== 2 || !raw.every(Number.isFinite)) {
      throw new Error(`Native city ${label}[${index}] is invalid`);
    }
    return [raw[0] as number, raw[1] as number];
  });
  if (points.length > 1 && points[0]![0] === points.at(-1)![0] && points[0]![1] === points.at(-1)![1]) {
    points.pop();
  }
  if (points.length < 3) throw new Error(`Native city ${label} is degenerate`);
  return points;
}

function fixturePolygon(value: unknown, label: string, renderOuterEnvelope: boolean): {
  readonly polygon: EnvironmentPolygon;
  readonly omittedInteriorRingCount: number;
} {
  const item = record(value, label);
  const outline = openRing(item.outline, `${label}.outline`);
  const sourceHoles = array(item.holes, `${label}.holes`).map((hole, index) =>
    openRing(hole, `${label}.holes[${index}]`));
  // Fixtures are rendered as conservative measured outer envelopes, not as source-model
  // mesh projections. Keep every declared interior ring in the audited count but do not
  // turn model cavities and sub-grid slivers into collision or rendering holes.
  const polygon = { outline, holes: [] };
  if (renderOuterEnvelope) {
    const triangles = triangulateUpward(outline);
    const expectedArea = Math.abs(outline.reduce((sum, point, index) => {
      const next = outline[(index + 1) % outline.length]!;
      return sum + point[0] * next[1] - next[0] * point[1];
    }, 0) / 2);
    let triangulatedArea = 0;
    for (let index = 0; index < triangles.length; index += 3) {
      const a = outline[triangles[index]!]!, b = outline[triangles[index + 1]!]!;
      const c = outline[triangles[index + 2]!]!;
      triangulatedArea += Math.abs((b[0] - a[0]) * (c[1] - a[1])
        - (b[1] - a[1]) * (c[0] - a[0])) / 2;
    }
    if (triangles.length === 0 || expectedArea <= 1e-8
        || Math.abs(triangulatedArea - expectedArea) > Math.max(1e-5, expectedArea * 1e-6)) {
      throw new Error(`Native city ${label} outer envelope triangulation changed its measured area`);
    }
  }
  return { polygon, omittedInteriorRingCount: sourceHoles.length };
}

function parseNativeFixtures(value: unknown, roadContext: Readonly<Record<string, unknown>>,
    displayedSurfaceSha256: string): NativeFixture[] {
  const root = record(value, "effective fixtures");
  if (root.schema_version !== "aero-bench.city-effective-fixture-geometry/v1"
      || root.displayed_surface_sha256 !== displayedSurfaceSha256
      || !sameJson(root.source_context, roadContext)) {
    throw new Error("Native city effective fixtures differ from the accepted road source");
  }
  const models = record(root.models, "effective fixture models");
  const modelSha: Record<NativeFixture["kind"], string> = {
    signal: text(record(models.signal, "signal model").sha256, "signal model sha256"),
    street_lamp: text(record(models.street_lamp, "street-lamp model").sha256, "street-lamp model sha256"),
  };
  if (Object.values(modelSha).some(digest => !/^[0-9a-f]{64}$/.test(digest))) {
    throw new Error("Native city effective fixture model digest is invalid");
  }
  const inventories = record(root.source_inventories, "effective fixture source inventories");
  const source = { signal: array(inventories.signals, "signal inventory"),
    street_lamp: array(inventories.street_lamps, "street-lamp inventory") } as const;
  const seen = { signal: new Set<number>(), street_lamp: new Set<number>() };
  const claim = (kind: NativeFixture["kind"], indexValue: unknown, location: unknown): number => {
    const index = integer(indexValue, `${kind} source_index`);
    if (index >= source[kind].length || seen[kind].has(index) || !sameJson(location, source[kind][index])) {
      throw new Error("Native city effective fixture source inventory is duplicated or inconsistent");
    }
    seen[kind].add(index);
    return index;
  };
  const ids = new Set<string>();
  const fixtures = array(root.effective_fixtures, "effective fixture list").map((raw, fixtureIndex): NativeFixture => {
    const item = record(raw, `effective fixture[${fixtureIndex}]`);
    const kind = item.kind;
    if (kind !== "signal" && kind !== "street_lamp") throw new Error("Native city fixture kind is invalid");
    const sourceIndex = claim(kind, item.source_index, item.source_location);
    const location = record(item.source_location, `${kind} source_location`);
    finite(location.x, `${kind} source_location.x`);
    finite(location.z, `${kind} source_location.z`);
    const id = text(item.id, `${kind} id`);
    const expectedId = kind === "signal" ? `signal:${text(location.id, "signal source id")}`
      : `street_lamp:${sourceIndex}`;
    if (id !== expectedId || ids.has(id) || item.model_sha256 !== modelSha[kind]) {
      throw new Error("Native city effective fixture identity or model binding is invalid");
    }
    const baseUpM = finite(item.base_up_m, `${kind} base_up_m`);
    const topUpM = finite(item.top_up_m, `${kind} top_up_m`);
    if (topUpM <= baseUpM) throw new Error("Native city effective fixture height is invalid");
    const footprintResults = array(item.footprints, `${kind} footprints`).map((polygon, index) =>
      fixturePolygon(polygon, `${kind} footprints[${index}]`, true));
    const motionResults = array(item.motion_footprints, `${kind} motion_footprints`).map((polygon, index) =>
      fixturePolygon(polygon, `${kind} motion_footprints[${index}]`, false));
    const footprints = footprintResults.map(result => result.polygon);
    const motion = motionResults.map(result => result.polygon);
    if (footprints.length === 0 || motion.length === 0) {
      throw new Error("Native city effective fixture has no measured footprint");
    }
    ids.add(id);
    return { id, kind, baseUpM, topUpM, footprints,
      omittedInteriorRingCount: [...footprintResults, ...motionResults]
        .reduce((total, result) => total + result.omittedInteriorRingCount, 0) };
  });
  for (const [kind, key] of [["signal", "omitted_signals"],
    ["street_lamp", "omitted_street_lamps"]] as const) {
    for (const raw of array(root[key], key)) {
      const item = record(raw, key);
      if (typeof item.reason !== "string" || item.reason.length === 0) {
        throw new Error(`Native city ${key} entry lacks a reason`);
      }
      claim(kind, item.source_index, item.source_location);
    }
    if (seen[kind].size !== source[kind].length) {
      throw new Error(`Native city ${kind} effective and omitted inventories are incomplete`);
    }
  }
  const counts = record(root.counts, "effective fixture counts");
  const signalCount = fixtures.filter(fixture => fixture.kind === "signal").length;
  const lampCount = fixtures.length - signalCount;
  if (counts.effective_signals !== signalCount || counts.effective_street_lamps !== lampCount
      || counts.source_signals !== source.signal.length || counts.source_street_lamps !== source.street_lamp.length
      || counts.omitted_signals !== source.signal.length - signalCount
      || counts.omitted_street_lamps !== source.street_lamp.length - lampCount) {
    throw new Error("Native city effective fixture counts differ from their inventories");
  }
  return fixtures;
}

function addPolygon(target: DraftGeometry, polygon: EnvironmentPolygon, y: number): void {
  const first = target.positions.length / 3;
  const points = [...polygon.outline, ...polygon.holes.flatMap(hole => hole)];
  for (const point of points) target.positions.push(point[0], y, point[1]);
  for (const index of triangulateUpward(polygon.outline, polygon.holes)) target.indices.push(first + index);
}

function addRibbon(target: DraftGeometry, shape: readonly (readonly [number, number])[],
    width: number, y: number): void {
  if (shape.length < 2 || !Number.isFinite(width) || width <= 0) return;
  const sides = laneOffsets(shape, width);
  const first = target.positions.length / 3;
  for (let index = 0; index < shape.length; index++) {
    target.positions.push(sides.left[index]![0], y, sides.left[index]![1]);
    target.positions.push(sides.right[index]![0], y, sides.right[index]![1]);
  }
  for (let index = 0; index < shape.length - 1; index++) {
    const offset = first + index * 2;
    target.indices.push(offset, offset + 1, offset + 2, offset + 1, offset + 3, offset + 2);
  }
}

function draftMesh(target: DraftGeometry, material: THREE.Material, name: string,
    computeNormals = false): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(target.positions, 3));
  geometry.setIndex(target.indices);
  if (computeNormals) geometry.computeVertexNormals();
  else {
    const normals = new Float32Array(target.positions.length);
    for (let index = 1; index < normals.length; index += 3) normals[index] = 1;
    geometry.setAttribute("normal", new THREE.BufferAttribute(normals, 3));
  }
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = name;
  mesh.receiveShadow = true;
  return mesh;
}

function createRoadGroup(road: ReturnType<typeof validateCanonicalCityRoadPayload>): THREE.Group {
  const group = new THREE.Group();
  group.name = "Declared native road-v3 surfaces";
  const roadbed: DraftGeometry = { positions: [], indices: [] };
  const concrete: DraftGeometry = { positions: [], indices: [] };
  const walkbed: DraftGeometry = { positions: [], indices: [] };
  const medians: DraftGeometry = { positions: [], indices: [] };
  const white: DraftGeometry = { positions: [], indices: [] };
  const yellow: DraftGeometry = { positions: [], indices: [] };
  const crossings: DraftGeometry = { positions: [], indices: [] };
  const curbs: DraftGeometry = { positions: [], indices: [] };
  for (const polygon of road.roadbed) addPolygon(roadbed, polygon, 0.075);
  for (const polygon of road.concrete_roadbed) addPolygon(concrete, polygon, 0.083);
  for (const polygon of road.walkbed) addPolygon(walkbed, polygon, road.street_layout.sidewalk_height_m);
  for (const polygon of road.street_layout.median_beds) addPolygon(medians, polygon,
    road.street_layout.sidewalk_height_m);
  for (const marking of road.street_layout.markings) {
    addRibbon(marking.color === "yellow" ? yellow : white, marking.shape, marking.width_m, 0.096);
  }
  for (const guide of road.direction_guides) addRibbon(white, guide.shape, guide.width_m, 0.097);
  for (const arrow of road.street_layout.arrows) addPolygon(white,
    { outline: arrow.outline, holes: [] }, 0.098);
  for (const crossing of road.crossings) for (const segment of crossingStripeSegments(crossing.shape)) {
    addRibbon(crossings, segment, Math.max(0.5, crossing.width - 0.35), 0.13);
  }
  for (const edge of road.curb_edges) addRibbon(curbs, edge, 0.18, 0.235);
  const surfaceMaterial = new THREE.MeshStandardMaterial({ color: 0x343b42, roughness: 0.9, metalness: 0.04 });
  const concreteMaterial = new THREE.MeshStandardMaterial({ color: 0x777b7c, roughness: 0.94 });
  const walkMaterial = new THREE.MeshStandardMaterial({ color: 0x9b9d98, roughness: 0.92 });
  const whiteMaterial = new THREE.MeshStandardMaterial({ color: 0xe8e9e4, roughness: 0.72,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 });
  const yellowMaterial = whiteMaterial.clone(); yellowMaterial.color.setHex(0xe1b83b);
  const curbMaterial = new THREE.MeshStandardMaterial({ color: 0xb4b5b1, roughness: 0.95 });
  group.add(draftMesh(roadbed, surfaceMaterial, "Accepted asphalt roadbed"));
  if (concrete.positions.length > 0) group.add(draftMesh(concrete, concreteMaterial, "Accepted concrete roadbed"));
  else concreteMaterial.dispose();
  group.add(draftMesh(walkbed, walkMaterial, "Accepted pedestrian surfaces"));
  if (medians.positions.length > 0) group.add(draftMesh(medians, walkMaterial, "Declared median surfaces"));
  let whiteMaterialAttached = false;
  if (white.positions.length > 0) {
    group.add(draftMesh(white, whiteMaterial, "Declared white road markings"));
    whiteMaterialAttached = true;
  }
  if (yellow.positions.length > 0) group.add(draftMesh(yellow, yellowMaterial, "Declared yellow road markings"));
  else yellowMaterial.dispose();
  if (crossings.positions.length > 0) {
    group.add(draftMesh(crossings, whiteMaterial, "Declared zebra crossings"));
    whiteMaterialAttached = true;
  }
  if (!whiteMaterialAttached) whiteMaterial.dispose();
  if (curbs.positions.length > 0) group.add(draftMesh(curbs, curbMaterial, "Declared curb bands"));
  else curbMaterial.dispose();
  group.userData.nativeSource = "aero-bench.city-road-preview/v3";
  group.userData.roadbedPolygonCount = road.roadbed.length;
  group.userData.walkbedPolygonCount = road.walkbed.length;
  return group;
}

function addPrism(target: DraftGeometry, polygon: EnvironmentPolygon, bottom: number, top: number): void {
  const points = [...polygon.outline, ...polygon.holes.flatMap(hole => hole)];
  const triangles = triangulateUpward(polygon.outline, polygon.holes);
  const first = target.positions.length / 3;
  for (const point of points) target.positions.push(point[0], bottom, point[1]);
  for (const point of points) target.positions.push(point[0], top, point[1]);
  for (let index = 0; index < triangles.length; index += 3) {
    const a = triangles[index]!, b = triangles[index + 1]!, c = triangles[index + 2]!;
    target.indices.push(first + a, first + c, first + b);
    target.indices.push(first + points.length + a, first + points.length + b, first + points.length + c);
  }
  let ringStart = 0;
  for (const ring of [polygon.outline, ...polygon.holes]) {
    for (let index = 0; index < ring.length; index++) {
      const next = (index + 1) % ring.length;
      const a = first + ringStart + index, b = first + ringStart + next;
      const c = first + points.length + ringStart + next, d = first + points.length + ringStart + index;
      target.indices.push(a, b, c, a, c, d);
    }
    ringStart += ring.length;
  }
}

function createFixtureGroup(fixtures: readonly NativeFixture[]): THREE.Group {
  const group = new THREE.Group();
  group.name = "Declared effective fixture geometry envelopes";
  for (const kind of ["signal", "street_lamp"] as const) {
    const draft: DraftGeometry = { positions: [], indices: [] };
    const selected = fixtures.filter(fixture => fixture.kind === kind);
    for (const fixture of selected) for (const polygon of fixture.footprints) {
      addPrism(draft, polygon, fixture.baseUpM, fixture.topUpM);
    }
    if (draft.positions.length === 0) continue;
    const material = new THREE.MeshStandardMaterial({
      color: kind === "signal" ? 0x2b7f78 : 0x596773,
      emissive: kind === "signal" ? 0x123d3a : 0x252b31,
      emissiveIntensity: 0.35,
      roughness: 0.48,
      metalness: 0.34,
      side: THREE.DoubleSide,
    });
    const mesh = draftMesh(draft, material,
      kind === "signal" ? "Measured signal envelopes" : "Measured street-lamp envelopes", true);
    mesh.castShadow = true;
    mesh.userData.fixtureKind = kind;
    mesh.userData.fixtureIds = selected.map(fixture => fixture.id);
    group.add(mesh);
  }
  group.userData.nativeSource = "aero-bench.city-effective-fixture-geometry/v1";
  group.userData.fixtureCount = fixtures.length;
  group.userData.omittedFixtureInteriorRingCount = fixtures.reduce((total, fixture) =>
    total + fixture.omittedInteriorRingCount, 0);
  group.userData.geometryMeaning = "measured-plan-outer-envelope-extrusion-not-source-model-mesh";
  return group;
}

/** Native geometry and classification use the viewer's existing pinned terrain materials. */
function createRegionGroup(environment: ReturnType<typeof parseCityEnvironmentSource>,
    covers: ReturnType<typeof parseCityGroundCovers>, kit: TerrainSurfaceKit): THREE.Group {
  const group = new THREE.Group();
  group.name = "Declared native green and ground-cover regions";
  const layers = [
    ...environment.greens.map(green => ({ polygon: green, y: 0.018,
      assignment: assignCityGroundMaterial(groundMaterialInputFromGreen(green)) })),
    ...covers.map(cover => ({ polygon: cover, y: 0.012,
      assignment: assignCityGroundMaterial(groundMaterialInputFromCover(cover)) })),
  ];
  const byPreset = new Map<string, typeof layers>();
  for (const layer of layers) {
    const key = `${layer.y}:${layer.assignment.preset}`;
    if (!byPreset.has(key)) byPreset.set(key, []);
    byPreset.get(key)!.push(layer);
  }
  for (const key of [...byPreset.keys()].sort()) {
    const members = byPreset.get(key)!, { assignment, y } = members[0]!;
    const draft: DraftGeometry = { positions: [], indices: [] };
    const uv: number[] = [], color: number[] = [];
    for (const member of members) {
      const start = draft.positions.length;
      addPolygon(draft, member.polygon, y);
      const attributes = kit.attributes(new Float32Array(draft.positions.slice(start)), member.assignment);
      if (attributes !== null) {
        for (const value of attributes.uv) uv.push(value);
        for (const value of attributes.color) color.push(value);
      }
    }
    const material = kit.material(assignment);
    material.userData.nativeTerrainKitOwned = true;
    const mesh = draftMesh(draft, material,
    `${y === 0.018 ? "OSM green area" : "OSM ground cover"}: ${assignment.preset}`);
    if (uv.length > 0) {
      mesh.geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
      mesh.geometry.setAttribute("color", new THREE.Float32BufferAttribute(color, 3));
    }
    mesh.userData.terrainPreset = assignment.preset;
    mesh.userData.groundMaterials = members.map(member => member.assignment);
    group.add(mesh);
  }
  group.userData.nativeSource = "aero-bench.city-environment-source/v1";
  group.userData.greenCount = environment.greens.length;
  group.userData.groundCoverCount = covers.length;
  group.userData.materialSource = "viewer-pinned-terrain-library";
  group.userData.terrainTextureLibrarySha256 = TERRAIN_TEXTURE_LIBRARY_SHA256;
  return group;
}

function pointSetsMatch(left: readonly EnvironmentPoint[], right: readonly EnvironmentPoint[]): boolean {
  const open = (points: readonly EnvironmentPoint[]): readonly EnvironmentPoint[] =>
    points.length > 1 && Math.hypot(points[0]![0] - points.at(-1)![0],
      points[0]![1] - points.at(-1)![1]) <= BUILDING_ENVELOPE_TOLERANCE_M
      ? points.slice(0, -1) : points;
  const leftOpen = open(left), rightOpen = open(right);
  if (leftOpen.length !== rightOpen.length) return false;
  const unmatched = [...rightOpen];
  for (const point of leftOpen) {
    const match = unmatched.findIndex(candidate =>
      Math.hypot(point[0] - candidate[0], point[1] - candidate[1]) <= BUILDING_ENVELOPE_TOLERANCE_M);
    if (match < 0) return false;
    unmatched.splice(match, 1);
  }
  return unmatched.length === 0;
}

function validateEnvironmentBuildings(environment: ReturnType<typeof parseCityEnvironmentSource>,
    plans: readonly NativeCityBuildingPlan[]): void {
  const source = new Map(environment.buildings.map(building => [building.id, building]));
  if (source.size !== plans.length || environment.buildings.length !== plans.length) {
    throw new Error("Native city ground-cover building inventory differs from PublicScenario");
  }
  for (const plan of plans) {
    const footprint = source.get(plan.building.building_id);
    const expected = plan.building.base_vertices.map(vertex =>
      [vertex.enu.east_m, -vertex.enu.north_m] as const);
    if (footprint === undefined || !pointSetsMatch(footprint.outline, expected) || footprint.holes.length !== 0) {
      throw new Error(`Native city ground-cover footprint differs: ${plan.building.building_id}`);
    }
  }
}

function disposeObject(root: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  root.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    geometries.add(node.geometry);
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      if (material.userData.nativeTerrainKitOwned === true) continue;
      materials.add(material);
      for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
    }
  });
  for (const geometry of geometries) geometry.dispose();
  for (const material of materials) material.dispose();
  for (const texture of textures) {
    texture.dispose();
    const data = texture.source.data as { close?: () => void } | null | undefined;
    data?.close?.();
  }
  root.clear();
}

async function defaultParseGlb(bytes: ArrayBuffer): Promise<THREE.Object3D> {
  const result = await new GLTFLoader().parseAsync(bytes, "");
  return result.scene;
}

function entityQuaternion(entity: PublicEntityDefinition): THREE.Quaternion {
  const source = entity.initial_pose.orientation_enu;
  return new THREE.Quaternion(source.qx, source.qz, -source.qy, source.qw).normalize();
}

function assembleNativeBuilding(content: THREE.Object3D, frame: NativeBuildingGlbFrame,
    binding: NativeCityBuildingPlan): THREE.Group {
  let meshCount = 0;
  content.traverse(node => {
    if (node instanceof THREE.Light || node instanceof THREE.Camera) {
      throw new Error(`Native city building parser returned foreign scene content: ${binding.building.building_id}`);
    }
    if (node instanceof THREE.Mesh) {
      meshCount++;
      node.castShadow = true;
      node.receiveShadow = true;
    }
  });
  if (meshCount === 0) throw new Error(`Native city building parser returned no mesh: ${binding.building.building_id}`);
  const visual = new THREE.Group();
  visual.name = binding.building.building_id;
  const entityPosition = binding.entity.initial_pose.position.enu;
  visual.position.set(entityPosition.east_m, entityPosition.up_m, -entityPosition.north_m);
  visual.quaternion.copy(entityQuaternion(binding.entity));
  const inverse = visual.quaternion.clone().invert();
  const anchor = new THREE.Vector3(frame.anchorEastM, frame.baseUpM, -frame.anchorNorthM);
  content.position.copy(anchor.sub(visual.position).applyQuaternion(inverse));
  // GLB geometry declares world-aligned east/up/south axes. Cancel the logical entity rotation
  // on the render child while retaining the entity as its identity/state parent.
  content.quaternion.copy(inverse);
  visual.userData.target = { kind: "building", id: binding.building.building_id };
  visual.userData.entityId = binding.entity.entity_id;
  visual.userData.renderAssetId = binding.asset.asset_id;
  visual.userData.renderSha256 = binding.asset.sha256;
  visual.userData.nativeBuilding = true;
  visual.userData.logicalEntityPose = true;
  visual.add(content);
  visual.updateMatrixWorld(true);
  const actual = new THREE.Box3().setFromObject(content);
  compareBounds(actual, expectedBuildingBounds(binding.building),
    `parsed building ${binding.building.building_id}`);
  return visual;
}

function sourceAuthority(roadValue: unknown, plan: NativeCityPresentationPlan): {
  readonly context: Readonly<Record<string, unknown>>;
  readonly objectsSha256: string;
  readonly origin: { readonly latitude_deg: number; readonly longitude_deg: number;
    readonly ellipsoid_height_m: number };
} {
  const road = record(roadValue, "road");
  const context = record(road.source_context, "road source_context");
  const origin = record(context.origin_wgs84, "road source origin");
  const buildingGeometry = record(context.building_geometry, "road building geometry");
  const actualAudit = record(buildingGeometry.actual_glb_footprint_audit, "road building GLB audit");
  const latitude_deg = finite(origin.latitude_deg, "road origin latitude");
  const longitude_deg = finite(origin.longitude_deg, "road origin longitude");
  const ellipsoid_height_m = finite(origin.ellipsoid_height_m, "road origin height");
  if (context.schema_version !== "aero-bench.city-rendered-source-context/v1"
      || context.mesh_pack_source_sha256 !== plan.layers.osm_scene.sha256
      || !/^[0-9a-f]{64}$/.test(String(context.objects_json_sha256))
      || buildingGeometry.count !== plan.buildings.length || actualAudit.count !== plan.buildings.length
      || typeof actualAudit.boundary_tolerance_m !== "number"
      || actualAudit.boundary_tolerance_m > BUILDING_ENVELOPE_TOLERANCE_M
      || latitude_deg !== plan.originWgs84.latitude_deg
      || longitude_deg !== plan.originWgs84.longitude_deg
      || ellipsoid_height_m !== plan.originWgs84.ellipsoid_height_m) {
    throw new Error("Native city road source context differs from its declared scene and buildings");
  }
  return { context, objectsSha256: String(context.objects_json_sha256),
    origin: { latitude_deg, longitude_deg, ellipsoid_height_m } };
}

/** Load and independently own the native-city-assets presentation. */
export async function loadNativeCityPresentation(plan: NativeCityPresentationPlan,
    options: NativeCityPresentationLoadOptions): Promise<LoadedNativeCityPresentation> {
  if (plan.kind !== "native-city-assets" || options.baseHref.length === 0) {
    throw new Error("Native city presentation plan or base endpoint is invalid");
  }
  const concurrency = options.concurrency ?? DEFAULT_BUILDING_CONCURRENCY;
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 16) {
    throw new Error("Native city building concurrency must be an integer from 1 to 16");
  }
  const controller = new AbortController();
  const abort = (): void => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  const resolver = new AssetResolver({ baseHref: options.baseHref, fetch: options.fetch,
    digest: options.digest });
  const root = new THREE.Group();
  root.name = `Native city assets: ${plan.worldId}`;
  const buildings = new THREE.Group(); buildings.name = "Native per-building GLBs";
  let roads: THREE.Group | null = null, fixtures: THREE.Group | null = null, regions: THREE.Group | null = null;
  let terrainKit: TerrainSurfaceKit | null = null;
  root.add(buildings);
  let completed = 0, verifiedBytes = 0, disposed = false;
  const total = NATIVE_LAYER_KINDS.length + plan.buildings.length;
  const report = (phase: NativeCityPresentationProgress["phase"], asset: PublicScenarioAsset): void => {
    completed++;
    verifiedBytes += asset.size_bytes;
    options.onProgress?.({ phase, completed, total, verifiedBytes,
      totalBytes: plan.totalBytes, assetId: asset.asset_id });
  };
  const declared = (asset: PublicScenarioAsset): { sha256: string; sizeBytes: number; mediaType: string | null } =>
    ({ sha256: asset.sha256, sizeBytes: asset.size_bytes, mediaType: asset.media_type });
  try {
    assertNotAborted(options.signal);
    const layerEntries = await Promise.all(NATIVE_LAYER_KINDS.map(async kind => {
      const asset = plan.layers[kind];
      const bytes = await resolver.fetchVerifiedBytes(asset.replay_path, declared(asset), controller.signal);
      const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      const value = kind === "osm_scene" ? parsePinnedOsmScene(text) : parseStrictJson(text);
      report("layers", asset);
      return [kind, value] as const;
    }));
    const layerValues = Object.fromEntries(layerEntries) as Record<NativeLayerKind, unknown>;
    const osm = layerValues.osm_scene as PinnedOsmSceneInventory;
    const road = validateCanonicalCityRoadPayload(layerValues.roads);
    const rawRoad = record(layerValues.roads, "road");
    if (rawRoad.source_kind !== "sumo-network-visual-geometry" || rawRoad.road_scope !== "ground-only"
        || rawRoad.mesh_pack_source_sha256 !== plan.layers.osm_scene.sha256) {
      throw new Error("Native city road is not the declared ground-only geometry for its OSM scene");
    }
    const authority = sourceAuthority(layerValues.roads, plan);
    const nativeFixtures = parseNativeFixtures(layerValues.entities, authority.context,
      text(rawRoad.displayed_surface_sha256, "road displayed_surface_sha256"));
    const environment = parseCityEnvironmentSource(layerValues.regions, {
      osmSha256: plan.layers.osm_scene.sha256, objectsSha256: authority.objectsSha256,
      origin: authority.origin,
    });
    const groundCovers = parseCityGroundCovers(layerValues.regions, {
      osmSha256: plan.layers.osm_scene.sha256, objectsSha256: authority.objectsSha256,
      origin: authority.origin,
    });
    validateEnvironmentBuildings(environment, plan.buildings);
    roads = createRoadGroup(road);
    root.add(roads);
    fixtures = createFixtureGroup(nativeFixtures);
    root.add(fixtures);
    const terrainAssignments = [
      ...environment.greens.map(green => assignCityGroundMaterial(groundMaterialInputFromGreen(green))),
      ...groundCovers.map(cover => assignCityGroundMaterial(groundMaterialInputFromCover(cover))),
    ];
    const textures = await loadVerifiedTerrainTextureSets({ setIds: terrainTextureSetIds(terrainAssignments),
      maxAnisotropy: 8, signal: controller.signal });
    try {
      terrainKit = createTerrainSurfaceKit(terrainAssignments, textures,
        createSurfaceWetnessUniforms(), createWaterSurfaceUniforms());
    } catch (error) { textures.dispose(); throw error; }
    regions = createRegionGroup(environment, groundCovers, terrainKit);
    root.add(regions);

    let nextBuilding = 0;
    const parseGlb = options.parseGlb ?? defaultParseGlb;
    let firstWorkerFailure: unknown;
    const workers = Array.from({ length: Math.min(concurrency, plan.buildings.length) }, async () => {
      while (!controller.signal.aborted) {
        const index = nextBuilding++;
        const binding = plan.buildings[index];
        if (binding === undefined) return;
        let content: THREE.Object3D | undefined;
        try {
          const bytes = await resolver.fetchVerifiedBytes(binding.asset.replay_path,
            declared(binding.asset), controller.signal);
          const frame = validateNativeBuildingGlb(bytes, binding);
          content = await parseGlb(bytes, binding);
          assertNotAborted(controller.signal);
          const visual = assembleNativeBuilding(content, frame, binding);
          buildings.add(visual);
          content = undefined;
          report("buildings", binding.asset);
        } catch (error) {
          if (content !== undefined) disposeObject(content);
          if (firstWorkerFailure === undefined) firstWorkerFailure = error;
          controller.abort();
          throw error;
        }
      }
    });
    await Promise.allSettled(workers);
    if (firstWorkerFailure !== undefined) throw firstWorkerFailure;
    assertNotAborted(options.signal);
    if (buildings.children.length !== plan.buildings.length || completed !== total
        || verifiedBytes !== plan.totalBytes) {
      throw new Error("Native city presentation did not load its complete declared inventory");
    }
    root.userData.nativeCityPresentation = true;
    root.userData.scenarioDigest = plan.scenarioDigest;
    root.userData.worldDigest = plan.worldDigest;
    const bounds = new THREE.Box3().setFromObject(root);
    if (bounds.isEmpty()) throw new Error("Native city presentation contains no renderable geometry");
    const stats: NativeCityPresentationStats = Object.freeze({
      buildingCount: plan.buildings.length,
      roadbedPolygonCount: road.roadbed.length,
      walkbedPolygonCount: road.walkbed.length,
      fixtureCount: nativeFixtures.length,
      omittedFixtureInteriorRingCount: nativeFixtures.reduce((total, fixture) =>
        total + fixture.omittedInteriorRingCount, 0),
      greenCount: environment.greens.length,
      groundCoverCount: groundCovers.length,
      osmElementCount: osm.elementCount,
      verifiedBytes,
    });
    root.userData.nativeCityStats = stats;
    const loaded: LoadedNativeCityPresentation = {
      group: root,
      layers: { buildings, roads, fixtures, regions },
      bounds: bounds.clone(),
      stats,
      roadClearance: spatialRoadClearanceFromNativeGeometry(road, nativeFixtures.flatMap(fixture =>
        fixture.footprints.map((footprint, index) => {
          const xs = footprint.outline.map(point => point[0]);
          const zs = footprint.outline.map(point => point[1]);
          const minX = Math.min(...xs), maxX = Math.max(...xs);
          const minZ = Math.min(...zs), maxZ = Math.max(...zs);
          return { id: `${fixture.id}:${index}`, kind: "street_asset", x: (minX + maxX) / 2,
            z: (minZ + maxZ) / 2, widthM: maxX - minX, depthM: maxZ - minZ,
            heightM: fixture.topUpM - fixture.baseUpM, baseY: fixture.baseUpM, rotationDeg: 0 };
        })), { source: "native-road-v3", roadSha256: plan.layers.roads.sha256,
        fixtureIdentity: plan.layers.entities.sha256,
        displayedSurfaceSha256: text(rawRoad.displayed_surface_sha256, "road displayed_surface_sha256") }),
      setLayerVisibility: visibility => {
        buildings.visible = visibility.buildings;
        roads!.visible = visibility.roads;
        regions!.visible = visibility.regions;
        fixtures!.visible = visibility.static_assets;
      },
      dispose: () => {
        if (disposed) return;
        disposed = true;
        controller.abort();
        disposeObject(root);
        terrainKit?.dispose();
        resolver.dispose();
      },
    };
    options.signal?.removeEventListener("abort", abort);
    return loaded;
  } catch (error) {
    controller.abort();
    disposeObject(root);
    terrainKit?.dispose();
    resolver.dispose();
    options.signal?.removeEventListener("abort", abort);
    throw error;
  }
}
