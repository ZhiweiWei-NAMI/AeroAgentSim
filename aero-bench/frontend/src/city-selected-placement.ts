/** Strict selected-city placement and bounds adapter bridging the authoring
 * scenario to one content-verified static presentation.
 *
 * CitySelectedScenario v2 is sealed to a SelectedSceneDraft; this module re-proves
 * that exact identity against the VerifiedStaticPresentation manifest before
 * measuring a single footprint. It then converts the presentation's verified
 * building placements into collision boxes and its motor roadbed into road
 * polygons with the existing normalization helpers, validates the static road /
 * building-placement fields and digest links, and pushes every authored facility
 * and no-fly polygon through the workspace geometry validator and the selected
 * ENU bounds (local X=east, Z=-north). Nothing here executes a flight, charges a
 * battery, allocates an order, or models dynamic traffic: the module only reports
 * structured placement and bounds issues with explicit paths and codes.
 *
 * Street lamps and traffic signals have positions but no footprint dimensions in
 * the verified road/signal payloads. The renderer supplies measured GLB bounds;
 * missing measurements block placement rather than inventing a footprint.
 */
import type { StaticPresentationManifest, StaticSignalInventory, VerifiedStaticPresentation } from "./city-authoring-api";
import type { CitySelectedScenario, SelectedScenarioFacility } from "./city-selected-scenario";
import { selectionDigest } from "./city-selected-draft";
import { validateStaticCityRoadPayload } from "./city-roads";
import type { SceneOrigin } from "./city-region-selector";
import type { SourceBuildingTriangleRange } from "./city-building-shape";
import { measureRoofSupports, validateRooftopVolume, type RooftopVolumeGeometry } from "./city-selected-rooftop";
import {
  normalizeBuildingPlacements, normalizeRoadbed, validateAirspacePolygon, validateFacilityPlacement,
  type AirspacePolygon, type BuildingPlacementSource, type PlacementBox, type PointXZ, type RoadPolygon,
} from "./city-workspace-geometry";

export type SelectedPlacementIssueCode =
  | "identity_mismatch"
  | "selection_digest_mismatch"
  | "invalid_road_payload"
  | "road_link_mismatch"
  | "invalid_building_placement"
  | "building_placement_link_mismatch"
  | "invalid_signal_inventory"
  | "signal_link_mismatch"
  | "invalid_static_obstacle"
  | "invalid_facility"
  | "facility_out_of_bounds"
  | "building_overlap"
  | "static_overlap"
  | "road_overlap"
  | "facility_overlap"
  | "airspace_overlap"
  | "invalid_no_fly"
  | "no_fly_out_of_bounds"
  | "unverified_static_assets"
  | "rooftop_unsupported"
  | "rooftop_support_mismatch"
  | "rooftop_outside_building"
  | "rooftop_obstacle";

/** A rooftop site can exist only on a building whose complete source footprint is
 * verified. `topY` is the roof top measured from an actual flat upward-facing
 * source triangle; no roof topology is invented. */
export interface RoofSupport {
  readonly buildingId: string;
  readonly x: number;
  readonly z: number;
  readonly widthM: number;
  readonly depthM: number;
  readonly rotationDeg: number;
  readonly topY: number;
}

export interface SelectedPlacementIssue {
  /** JSON-pointer-style path into the scenario or the verified presentation. */
  readonly path: string;
  readonly code: SelectedPlacementIssueCode;
  readonly message: string;
  readonly obstacleId?: string;
}

/** The subset of a verified presentation this pure adapter actually consumes. */
export type SelectedPlacementInput =
  Pick<VerifiedStaticPresentation, "manifest" | "buildingPlacement" | "road" | "signals"> & {
    /** Hash from the verified ready job's presentation.manifest reference. */
    readonly presentationManifestSha256: string;
    /** Bounds measured from the assembled selected scene, including lamps, trees and signals. */
    readonly staticObstacles?: readonly PlacementBox[];
    /** Actual selected-scene building source triangles, bound to the current
     * authoring map key (the map accessor guards key/revision and is cleared when
     * the scene changes). Only complete-footprint buildings are included. */
    readonly rooftopMesh?: ReadonlyMap<string, SourceBuildingTriangleRange[]>;
  };

interface EnuBounds {
  readonly min_east_m: number;
  readonly max_east_m: number;
  readonly min_north_m: number;
  readonly max_north_m: number;
}

const EPSILON = 1e-9;
const SHA256 = /^[0-9a-f]{64}$/;
const BUILDING_PLACEMENT_SCHEMA = "aero-bench.city-building-placement/v1";
const SIGNAL_INVENTORY_SCHEMA = "aero-bench.city-static-signal-inventory/v1";

function sameSceneOrigin(left: SceneOrigin, right: SceneOrigin): boolean {
  return left.latitude_deg === right.latitude_deg && left.longitude_deg === right.longitude_deg
    && left.ellipsoid_height_m === right.ellipsoid_height_m
    && left.geoid_undulation_m === right.geoid_undulation_m && left.amsl_m === right.amsl_m;
}

/** Sync structural identity binding between the scenario and the presentation.
 * A single mismatch is already fatal: no placement is ever measured against a
 * different city, and the caller's verified loader enforced the presentation
 * document digest (draft.presentation_manifest_sha256) that this parsed manifest
 * cannot carry. */
export function verifySelectedPlacementIdentity(scenario: CitySelectedScenario,
                                                presentation: SelectedPlacementInput): SelectedPlacementIssue[] {
  const issues: SelectedPlacementIssue[] = [];
  const draft = scenario.selectedScene;
  const manifest = presentation.manifest;
  if (draft.job_id !== manifest.job_id) {
    issues.push({ path: "selectedScene.job_id", code: "identity_mismatch",
      message: `选城场景绑定的任务 ID 与呈现 manifest 不一致（${draft.job_id} ≠ ${manifest.job_id}）` });
  }
  if (draft.selection_sha256 !== manifest.selection_sha256) {
    issues.push({ path: "selectedScene.selection_sha256", code: "identity_mismatch",
      message: "选城场景的选区哈希与呈现 manifest 不一致" });
  }
  if (draft.selection.source_id !== manifest.source_id) {
    issues.push({ path: "selectedScene.selection.source_id", code: "identity_mismatch",
      message: `选城场景的来源 ID 与呈现 manifest 不一致（${draft.selection.source_id} ≠ ${manifest.source_id}）` });
  }
  if (draft.source_sha256 !== manifest.raw_source_sha256
      || draft.selection.source_sha256 !== manifest.raw_source_sha256) {
    issues.push({ path: "selectedScene.selection.source_sha256", code: "identity_mismatch",
      message: "选城场景的原始 OSM 哈希与呈现 manifest 不一致" });
  }
  if (!sameSceneOrigin(manifest.origin, draft.selection.origin)) {
    issues.push({ path: "selectedScene.selection.origin", code: "identity_mismatch",
      message: "选城场景的场景原点与呈现 manifest 不一致" });
  }
  if (draft.pack_manifest_sha256 !== manifest.pack_manifest.sha256) {
    issues.push({ path: "selectedScene.pack_manifest_sha256", code: "identity_mismatch",
      message: "选城场景的网格包 manifest 哈希与呈现 manifest 不一致" });
  }
  if (draft.presentation_manifest_sha256 !== presentation.presentationManifestSha256) {
    issues.push({ path: "selectedScene.presentation_manifest_sha256", code: "identity_mismatch",
      message: "选城场景的呈现 manifest 哈希与已验证任务不一致" });
  }
  return issues;
}

/** Proves the exact selection document still hashes to the draft's bound SHA-256. */
export async function verifySelectedPlacementDigest(scenario: CitySelectedScenario): Promise<SelectedPlacementIssue[]> {
  const digest = await selectionDigest(scenario.selectedScene.selection);
  return digest === scenario.selectedScene.selection_sha256 ? []
    : [{ path: "selectedScene.selection", code: "selection_digest_mismatch",
      message: "选城场景的选区文档无法重算为绑定的 selection_sha256，拒绝使用" }];
}

interface RoadValidation {
  readonly issues: SelectedPlacementIssue[];
  readonly polygons: RoadPolygon[] | null;
  readonly displayedSurfaceSha256: string | null;
  readonly hasStreetLamps: boolean;
}

function validateStaticRoad(value: unknown, manifest: StaticPresentationManifest): RoadValidation {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return { issues: [{ path: "road", code: "invalid_road_payload", message: "静态道路载荷格式无效" }],
      polygons: null, displayedSurfaceSha256: null, hasStreetLamps: false };
  }
  let data;
  try {
    data = validateStaticCityRoadPayload(value);
  } catch (error) {
    return { issues: [{ path: "road", code: "invalid_road_payload",
      message: `静态道路载荷无效：${error instanceof Error ? error.message : String(error)}` }],
    polygons: null, displayedSurfaceSha256: null, hasStreetLamps: false };
  }
  const linkIssues: SelectedPlacementIssue[] = [];
  const check = (actual: unknown, expected: string, path: string, label: string): void => {
    if (actual !== expected) linkIssues.push({ path, code: "road_link_mismatch",
      message: `${label}与呈现 manifest 不一致` });
  };
  check(data.source_network_sha256, manifest.network.sha256, "road.source_network_sha256", "道路源网络哈希");
  check(data.mesh_pack_source_sha256, manifest.effective_osm_sha256, "road.mesh_pack_source_sha256", "道路网格来源哈希");
  check(data.building_placement_sha256, manifest.building_placement.sha256, "road.building_placement_sha256", "道路建筑放置哈希");
  const raw = value as Record<string, unknown>;
  check(raw.source_osm_sha256, manifest.sumo_source_osm_sha256, "road.source_osm_sha256", "道路 SUMO 源 OSM 哈希");
  check(raw.mesh_pack_manifest_sha256, manifest.pack_manifest.sha256, "road.mesh_pack_manifest_sha256", "道路网格包 manifest 哈希");
  check(raw.signal_inventory_sha256, manifest.signal_inventory.sha256, "road.signal_inventory_sha256", "道路信号目录哈希");
  // A road whose digests do not all match the same manifest is not this city's
  // road; its polygons are never fed to the overlap checks.
  const trusted = linkIssues.length === 0;
  let polygons: RoadPolygon[] | null = null;
  if (trusted) {
    try {
      polygons = normalizeRoadbed(data.roadbed);
    } catch (error) {
      return { issues: [...linkIssues, { path: "road.roadbed", code: "invalid_road_payload",
        message: `道路铺设多边形无效：${error instanceof Error ? error.message : String(error)}` }],
      polygons: null, displayedSurfaceSha256: null, hasStreetLamps: false };
    }
  }
  return { issues: linkIssues, polygons,
    displayedSurfaceSha256: trusted ? data.displayed_surface_sha256 : null,
    hasStreetLamps: trusted && data.street_lamps.length > 0 };
}

interface BuildingPlacementValidation {
  readonly issues: SelectedPlacementIssue[];
  readonly boxes: PlacementBox[] | null;
  readonly roofSupports: RoofSupport[];
  readonly displayedSurfaceSha256: string | null;
}

function parseBuildingPlacementRow(value: unknown): BuildingPlacementSource | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const buildingId = row.building_id;
  const part = row.part;
  const numbers = [row.x, row.z, row.width, row.depth, row.height, row.rotation_deg, row.base_y];
  if (typeof buildingId !== "string" || buildingId.length === 0
      || typeof part !== "number" || !Number.isSafeInteger(part) || part < 0
      || !numbers.every(item => typeof item === "number" && Number.isFinite(item))) {
    return null;
  }
  const [x, z, width, depth, height, rotation_deg, base_y] = numbers as
    [number, number, number, number, number, number, number];
  if (width <= 0 || depth <= 0 || height <= 0) return null;
  return { building_id: buildingId, part, x, z, width, depth, height, rotation_deg, base_y };
}

function validateBuildingPlacements(value: unknown, manifest: StaticPresentationManifest,
                                    roadDisplayedSurfaceSha256: string | null,
                                    roofMesh: ReadonlyMap<string, SourceBuildingTriangleRange[]> | undefined):
    BuildingPlacementValidation {
  const invalid = (path: string, message: string): SelectedPlacementIssue =>
    ({ path, code: "invalid_building_placement", message });
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return { issues: [invalid("buildingPlacement", "建筑放置载荷格式无效")], boxes: null, roofSupports: [],
      displayedSurfaceSha256: null };
  }
  const document = value as Record<string, unknown>;
  const issues: SelectedPlacementIssue[] = [];
  const linkIssues: SelectedPlacementIssue[] = [];
  if (document.schema_version !== BUILDING_PLACEMENT_SCHEMA) {
    return { issues: [invalid("buildingPlacement.schema_version", "建筑放置版本无效")], boxes: null, roofSupports: [],
      displayedSurfaceSha256: null };
  }
  if (document.mesh_pack_manifest_sha256 !== manifest.pack_manifest.sha256) {
    linkIssues.push({ path: "buildingPlacement.mesh_pack_manifest_sha256", code: "building_placement_link_mismatch",
      message: "建筑放置的网格包 manifest 哈希与呈现 manifest 不一致" });
  }
  if (document.mesh_pack_source_sha256 !== manifest.effective_osm_sha256) {
    linkIssues.push({ path: "buildingPlacement.mesh_pack_source_sha256", code: "building_placement_link_mismatch",
      message: "建筑放置的网格来源哈希与呈现 manifest 不一致" });
  }
  const displayed = document.displayed_surface_sha256;
  const displayedSurface = typeof displayed === "string" && SHA256.test(displayed) ? displayed : null;
  if (displayedSurface === null) {
    linkIssues.push(invalid("buildingPlacement.displayed_surface_sha256", "建筑放置缺少有效的 displayed_surface_sha256"));
  } else if (roadDisplayedSurfaceSha256 !== null && displayedSurface !== roadDisplayedSurfaceSha256) {
    linkIssues.push({ path: "buildingPlacement.displayed_surface_sha256", code: "building_placement_link_mismatch",
      message: "建筑放置与静态道路的 displayed_surface_sha256 不一致" });
  }
  const rawPlacements = document.placements;
  const rawCompleteIds = document.complete_footprint_buildings;
  const rawEnvelopes = document.complete_footprint_envelopes;
  if (document.source_kind !== "verified-source-surfaces-and-inscribed-rectangles"
      || !Array.isArray(rawPlacements) || !Array.isArray(rawCompleteIds)
      || !Array.isArray(rawEnvelopes)) {
    issues.push(invalid("buildingPlacement", "建筑放置缺少完整建筑包络或来源类型无效"));
    return { issues: [...issues, ...linkIssues], boxes: null, roofSupports: [], displayedSurfaceSha256: null };
  }
  const completeIds = new Set<string>();
  rawCompleteIds.forEach((raw, index) => {
    if (typeof raw !== "string" || !raw || completeIds.has(raw)) {
      issues.push(invalid(`buildingPlacement.complete_footprint_buildings[${index}]`, "完整建筑 ID 无效或重复"));
    } else completeIds.add(raw);
  });
  const envelopes: BuildingPlacementSource[] = [];
  const envelopeIds = new Set<string>();
  rawEnvelopes.forEach((raw, index) => {
    const parsed = parseBuildingPlacementRow(raw);
    if (parsed === null || parsed.part !== 0 || !completeIds.has(parsed.building_id)
        || envelopeIds.has(parsed.building_id)) {
      issues.push(invalid(`buildingPlacement.complete_footprint_envelopes[${index}]`,
        "完整建筑碰撞包络无效、重复或未登记"));
    } else {
      envelopeIds.add(parsed.building_id);
      envelopes.push(parsed);
    }
  });
  if (envelopeIds.size !== completeIds.size) {
    issues.push(invalid("buildingPlacement.complete_footprint_envelopes", "完整建筑缺少碰撞包络"));
  }
  const rows: BuildingPlacementSource[] = [];
  rawPlacements.forEach((raw, index) => {
    const parsed = parseBuildingPlacementRow(raw);
    if (parsed === null) {
      issues.push(invalid(`buildingPlacement.placements[${index}]`, "建筑放置行字段无效"));
    } else {
      rows.push(parsed);
    }
  });
  // The renderer uses complete source-footprint envelopes, then fallback
  // placements for the remaining buildings. Match that exact visual set.
  const trusted = linkIssues.length === 0 && issues.length === 0;
  let boxes: PlacementBox[] | null = null;
  if (trusted) {
    try {
      boxes = normalizeBuildingPlacements([
        ...envelopes, ...rows.filter(row => !completeIds.has(row.building_id)),
      ]);
    } catch (error) {
      issues.push(invalid("buildingPlacement", `建筑放置无法归一化：${error instanceof Error ? error.message : String(error)}`));
      boxes = null;
    }
  }
  // Rooftop support is measured from the ACTUAL selected-scene building mesh
  // triangles, never from the collision envelope: an envelope is an axis-aligned
  // box that bounds the whole source mesh, so its top is the highest point
  // (possibly a spire, sloped roof or setback), never evidence of a flat upward
  // surface large enough to support a vertiport. When the current scene exposes
  // its verified source triangles (`rooftopMesh`), every complete-building
  // envelope is measured against those exact triangles and a support is emitted
  // only when one flat upward triangle provably contains a rectangle. Without
  // that mesh the list stays empty and every rooftop facility fails with
  // `rooftop_unsupported` instead of pretending a floating platform is verified.
  let roofSupports: RoofSupport[] = [];
  if (trusted && roofMesh !== undefined && envelopes.length > 0) {
    try {
      roofSupports = measureRoofSupports(envelopes, roofMesh);
    } catch (error) {
      issues.push(invalid("buildingPlacement", "来源建筑网格无法测量屋顶支撑："
        + `${error instanceof Error ? error.message : String(error)}`));
    }
  }
  return { issues: [...issues, ...linkIssues], boxes, roofSupports,
    displayedSurfaceSha256: trusted && displayedSurface !== null ? displayedSurface : null };
}

/** Static signals have network XZ but no footprint in their inventory. Require
 * one or more measured rendered GLB bounds for every signal. */
function validateSignalInventory(signals: StaticSignalInventory,
                                 manifest: StaticPresentationManifest,
                                 measured: readonly PlacementBox[] | undefined): SelectedPlacementIssue[] {
  if (signals.schema_version !== SIGNAL_INVENTORY_SCHEMA || !Array.isArray(signals.signals)) {
    return [{ path: "signals", code: "invalid_signal_inventory", message: "静态信号目录格式无效" }];
  }
  const issues: SelectedPlacementIssue[] = [];
  if (signals.source_network_sha256 !== manifest.network.sha256) {
    issues.push({ path: "signals.source_network_sha256", code: "signal_link_mismatch",
      message: "信号目录的源网络哈希与呈现 manifest 不一致" });
  }
  if (signals.mesh_pack_source_sha256 !== manifest.effective_osm_sha256) {
    issues.push({ path: "signals.mesh_pack_source_sha256", code: "signal_link_mismatch",
      message: "信号目录的网格来源哈希与呈现 manifest 不一致" });
  }
  const bad = signals.signals.find(signal => !Number.isFinite(signal.x) || !Number.isFinite(signal.z));
  if (bad !== undefined) {
    issues.push({ path: "signals.signals", code: "invalid_signal_inventory",
      message: `信号 ${bad.id} 缺少有限 XZ 坐标` });
  }
  if (signals.signals.some(signal => !measured?.some(box => box.id.startsWith(`signal:${signal.id}:`)))) {
    issues.push({ path: "signals", code: "unverified_static_assets",
      message: "静态信号净空未纳入校验：须先从当前渲染场景测量所有信号灯的 GLB 碰撞盒" });
  }
  return issues;
}

function validateMeasuredStaticObstacles(boxes: readonly PlacementBox[]): SelectedPlacementIssue[] {
  const issues: SelectedPlacementIssue[] = [];
  boxes.forEach((box, index) => {
    if (box.kind !== "street_asset" || !box.id
        || ![box.x, box.z, box.widthM, box.depthM, box.heightM, box.rotationDeg, box.baseY]
          .every(value => typeof value === "number" && Number.isFinite(value))
        || box.widthM <= 0 || box.depthM <= 0 || box.heightM <= 0) {
      issues.push({ path: `staticObstacles[${index}]`, code: "invalid_static_obstacle",
        message: "渲染场景测量的街道设施碰撞盒无效" });
    }
  });
  return issues;
}

/** Axis-aligned ENU bounds proof against the rotated rectangle. Local X is east,
 * local Z is -north, so a corner at local (x, z) has ENU (east=x, north=-z). The
 * half-extent projection onto each ENU axis is exact for a rotated rectangle. */
function footprintOutsideEnu(box: PlacementBox, bounds: EnuBounds): boolean {
  const angle = box.rotationDeg * Math.PI / 180;
  const cosine = Math.cos(angle), sine = Math.sin(angle);
  const halfEast = Math.abs(cosine) * box.widthM / 2 + Math.abs(sine) * box.depthM / 2;
  const halfNorth = Math.abs(sine) * box.widthM / 2 + Math.abs(cosine) * box.depthM / 2;
  const east = box.x, north = -box.z;
  return east - halfEast < bounds.min_east_m - EPSILON || east + halfEast > bounds.max_east_m + EPSILON
    || north - halfNorth < bounds.min_north_m - EPSILON || north + halfNorth > bounds.max_north_m + EPSILON;
}

function polygonOutsideEnu(polygon: readonly PointXZ[], bounds: EnuBounds): boolean {
  return polygon.some(point => point.x < bounds.min_east_m - EPSILON || point.x > bounds.max_east_m + EPSILON
    || -point.z < bounds.min_north_m - EPSILON || -point.z > bounds.max_north_m + EPSILON);
}

function facilityBox(facility: SelectedScenarioFacility): PlacementBox {
  return {
    id: facility.id, x: facility.position.x, z: facility.position.z,
    widthM: facility.widthM, depthM: facility.depthM, heightM: facility.heightM,
    rotationDeg: facility.rotationDeg, baseY: facility.supportHeightM ?? 0,
  };
}

/** True when every rotated corner of the facility footprint lies within the rotated
 * footprint of the verified support building. No roof topology is invented. */
function footprintInsideSupport(facility: SelectedScenarioFacility, support: RoofSupport): boolean {
  const facilityAngle = facility.rotationDeg * Math.PI / 180;
  const buildingAngle = -support.rotationDeg * Math.PI / 180;
  const cosF = Math.cos(facilityAngle), sinF = Math.sin(facilityAngle);
  const cosB = Math.cos(buildingAngle), sinB = Math.sin(buildingAngle);
  const corners: readonly (readonly [number, number])[] = [
    [-facility.widthM / 2, -facility.depthM / 2], [facility.widthM / 2, -facility.depthM / 2],
    [facility.widthM / 2, facility.depthM / 2], [-facility.widthM / 2, facility.depthM / 2],
  ];
  return corners.every(([cx, cz]) => {
    const worldX = facility.position.x + cx * cosF + cz * sinF;
    const worldZ = facility.position.z - cx * sinF + cz * cosF;
    const dx = worldX - support.x, dz = worldZ - support.z;
    const localX = dx * cosB + dz * sinB;
    const localZ = -dx * sinB + dz * cosB;
    return Math.abs(localX) <= support.widthM / 2 + EPSILON
      && Math.abs(localZ) <= support.depthM / 2 + EPSILON;
  });
}

function validateRooftopPlacement(facility: SelectedScenarioFacility, supports: readonly RoofSupport[],
                                  roofMesh: ReadonlyMap<string, SourceBuildingTriangleRange[]> | undefined,
                                  path: string): SelectedPlacementIssue[] {
  const issues: SelectedPlacementIssue[] = [];
  const buildingId = facility.buildingId;
  const support = buildingId === null ? undefined : supports.find(item => item.buildingId === buildingId);
  if (support === undefined) {
    issues.push({ path: `${path}.buildingId`, code: "rooftop_unsupported",
      message: `设施 ${facility.id} 的屋顶放置缺少可核验支撑：所选建筑 ${buildingId ?? "（未绑定）"} `
        + "不是当前呈现中具有完整来源足迹且能证明平屋面支撑的建筑，来源无法证明屋顶支撑，拒绝虚构高度或屋顶拓扑" });
    return issues;
  }
  if (facility.supportHeightM === null || Math.abs(facility.supportHeightM - support.topY) > 1e-6) {
    issues.push({ path: `${path}.supportHeightM`, code: "rooftop_support_mismatch",
      message: `设施 ${facility.id} 的支撑高度必须等于核验建筑顶面 ${support.topY} m（当前 ${facility.supportHeightM}）` });
  }
  if (!footprintInsideSupport(facility, support)) {
    issues.push({ path: `${path}.position`, code: "rooftop_outside_building",
      message: `设施 ${facility.id} 的旋转足迹超出核验建筑 ${support.buildingId} 的屋顶足迹` });
  }
  if (roofMesh !== undefined) {
    const geometry: RooftopVolumeGeometry = {
      position: facility.position, rotationDeg: facility.rotationDeg,
      widthM: facility.widthM, depthM: facility.depthM, heightM: facility.heightM,
    };
    const volumeIssues = validateRooftopVolume(geometry, support, roofMesh);
    for (const message of volumeIssues) {
      issues.push({ path: `${path}.position`, code: "rooftop_obstacle",
        message: `设施 ${facility.id}：${message}` });
    }
  }
  return issues;
}

/** Structural-only authoring validator; the no-fly regions are parsed by
 * parseCitySelectedScenario, so this pass only guards hand-built scenarios. */
function toAirspace(zone: CitySelectedScenario["noFlyZones"][number]): AirspacePolygon {
  return {
    id: zone.id, name: zone.name, polygon: zone.polygon, floorM: zone.floorM, ceilingM: zone.ceilingM,
    startsAtS: zone.startsAtS, endsAtS: zone.endsAtS,
    source: { kind: zone.source.kind, label: zone.source.label,
      uri: zone.source.uri === null ? undefined : zone.source.uri },
  };
}

export interface SelectedPlacementInspection {
  readonly issues: readonly SelectedPlacementIssue[];
  /** Current-city geometry is returned only when all digest links and document structures are valid. */
  readonly geometry: {
    readonly buildings: readonly PlacementBox[];
    readonly roads: readonly RoadPolygon[];
    /** Complete verified building footprints eligible for a rooftop site. */
    readonly roofSupportedBuildings: readonly RoofSupport[];
  } | null;
}

/** Validate the exact selected city and expose its geometry for the map editor. */
export async function inspectCitySelectedPlacement(scenario: CitySelectedScenario,
                                                   presentation: SelectedPlacementInput): Promise<SelectedPlacementInspection> {
  const issues = verifySelectedPlacementIdentity(scenario, presentation);
  issues.push(...await verifySelectedPlacementDigest(scenario));
  if (issues.length) return { issues, geometry: null };

  const manifest = presentation.manifest;
  const road = validateStaticRoad(presentation.road, manifest);
  const buildings = validateBuildingPlacements(presentation.buildingPlacement, manifest,
    road.displayedSurfaceSha256, presentation.rooftopMesh);
  issues.push(...road.issues, ...buildings.issues);
  const measured = presentation.staticObstacles;
  if (measured !== undefined) issues.push(...validateMeasuredStaticObstacles(measured));
  issues.push(...validateSignalInventory(presentation.signals, manifest, measured));
  if (road.hasStreetLamps && !measured?.some(box => box.id.startsWith("lamp:"))) {
    issues.push({ path: "road.street_lamps", code: "unverified_static_assets",
      message: "街灯净空未纳入校验：须先从当前渲染场景测量街灯的 GLB 碰撞盒" });
  }

  const bounds = scenario.selectedScene.selection.bounds_enu_m;
  const airspace: AirspacePolygon[] = [];
  scenario.noFlyZones.forEach((zone, index) => {
    const polygonIssues = validateAirspacePolygon(zone);
    if (polygonIssues.length > 0) {
      issues.push({ path: `noFlyZones[${index}]`, code: "invalid_no_fly",
        message: `禁飞区 ${zone.id}：${polygonIssues.map(item => item.message).join("；")}` });
      return;
    }
    airspace.push(toAirspace(zone));
    if (polygonOutsideEnu(zone.polygon, bounds)) {
      issues.push({ path: `noFlyZones[${index}].polygon`, code: "no_fly_out_of_bounds",
        message: `禁飞区 ${zone.id} 的多边形顶点超出选区 ENU 边界` });
    }
  });

  if (road.polygons === null || buildings.boxes === null
      || issues.some(issue => issue.code === "invalid_static_obstacle"
        || issue.code === "invalid_signal_inventory" || issue.code === "signal_link_mismatch")) {
    return { issues, geometry: null };
  }
  const obstacles = [...buildings.boxes, ...(measured ?? [])];
  const roadPolygons = road.polygons ?? [];
  scenario.facilities.forEach((facility, index) => {
    const path = `facilities[${index}]`;
    const box = facilityBox(facility);
    if (footprintOutsideEnu(box, bounds)) {
      issues.push({ path: `${path}.position`, code: "facility_out_of_bounds",
        message: `设施 ${facility.id} 的旋转足迹超出选区 ENU 边界` });
    }
    if (facility.placement === "rooftop") {
      issues.push(...validateRooftopPlacement(facility, buildings.roofSupports,
        presentation.rooftopMesh, path));
    }
    try {
      // A rooftop site is elevated above the roadbed, so the ground roadbed is not
      // a collision surface for it; every other verified obstacle still applies.
      const placementRoads = facility.placement === "rooftop" ? [] : roadPolygons;
      for (const geometryIssue of validateFacilityPlacement(facility, obstacles, placementRoads,
        scenario.facilities, airspace)) {
        issues.push({ path, code: geometryIssue.code, message: geometryIssue.message,
          obstacleId: geometryIssue.obstacleId });
      }
    } catch (error) {
      issues.push({ path, code: "invalid_facility",
        message: `设施 ${facility.id} 无法校验：${error instanceof Error ? error.message : String(error)}` });
    }
  });
  return { issues, geometry: { buildings: buildings.boxes, roads: roadPolygons,
    roofSupportedBuildings: buildings.roofSupports } };
}

export async function validateCitySelectedPlacement(scenario: CitySelectedScenario,
                                                    presentation: SelectedPlacementInput): Promise<SelectedPlacementIssue[]> {
  return [...(await inspectCitySelectedPlacement(scenario, presentation)).issues];
}
