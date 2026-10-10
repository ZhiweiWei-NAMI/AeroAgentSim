/** Deterministic legal-position proposal for selected-city facilities.
 *
 * A map click is never applied verbatim. This module proposes the nearest legal
 * placement for a clicked point: it searches a deterministic ring of candidate
 * centres and rotations, rejects every candidate whose full rotated footprint
 * crosses a verified building, street asset, motor road, existing facility, active
 * no-fly volume or the selected ENU bounds, and returns the closest candidate that
 * clears all of them with an explicit displacement and reason. A rooftop site is
 * proposed only on a verified complete-footprint building, using that building's
 * verified top height — no roof height or topology is ever invented.
 *
 * Blank land inside the selection has no verified land-use in the source data, so
 * a clear ground candidate is never called "verified land use"; it is reported as
 * "clear of verified geometry, land use unverified".
 */
import type { SelectedScenarioFacility } from "./city-selected-scenario";
import { validateFacilityPlacement, type AirspacePolygon, type PlacementBox, type PointXZ,
  type RoadPolygon } from "./city-workspace-geometry";
import type { RoofSupport } from "./city-selected-placement";
import type { SourceBuildingTriangleRange } from "./city-building-shape";
import { validateRooftopVolume, type RooftopVolumeGeometry } from "./city-selected-rooftop";

const EPSILON = 1e-9;

export interface SnapExtent {
  readonly minX: number;
  readonly maxX: number;
  readonly minZ: number;
  readonly maxZ: number;
}

export interface SnapContext {
  readonly extent: SnapExtent;
  readonly buildings: readonly PlacementBox[];
  readonly staticObstacles: readonly PlacementBox[];
  readonly roads: readonly RoadPolygon[];
  readonly facilities: readonly SelectedScenarioFacility[];
  readonly airspace: readonly AirspacePolygon[];
  readonly roofSupports: readonly RoofSupport[];
  /** Scene-bound source triangles used to verify rooftop net volume; optional so
   * callers without a loaded selected scene can still preview footprint fits. */
  readonly rooftopMesh?: ReadonlyMap<string, SourceBuildingTriangleRange[]>;
}

export interface SnapCandidate {
  readonly position: PointXZ;
  readonly rotationDeg: number;
  /** Distance in metres between the requested point and this proposal. */
  readonly displacementM: number;
  readonly reason: string;
  /** True only when the proposal clears every verified constraint. */
  readonly legal: boolean;
  /** Residual issues, present when no fully legal position was found. */
  readonly issues: readonly string[];
  /** Blank land inside a selection carries no verified land-use in the source. */
  readonly landUseVerified: false;
  /** Verified support height for a rooftop proposal, else null. */
  readonly supportHeightM: number | null;
}

export interface SnapOptions {
  readonly radiusM?: number;
  readonly stepM?: number;
  /** Candidate rotations tried per position after the requested rotation. */
  readonly rotationsDeg?: readonly number[];
}

function footprintOutsideExtent(facility: SelectedScenarioFacility, extent: SnapExtent): boolean {
  const angle = facility.rotationDeg * Math.PI / 180;
  const cosine = Math.abs(Math.cos(angle)), sine = Math.abs(Math.sin(angle));
  const halfEast = cosine * facility.widthM / 2 + sine * facility.depthM / 2;
  const halfNorth = sine * facility.widthM / 2 + cosine * facility.depthM / 2;
  const east = facility.position.x, north = -facility.position.z;
  return east - halfEast < extent.minX - EPSILON || east + halfEast > extent.maxX + EPSILON
    || north - halfNorth < -extent.maxZ - EPSILON || north + halfNorth > -extent.minZ + EPSILON;
}

function issuesFor(facility: SelectedScenarioFacility, context: SnapContext): string[] {
  const messages: string[] = [];
  if (footprintOutsideExtent(facility, context.extent)) messages.push("旋转足迹超出选区 ENU 边界");
  const obstacles = [...context.buildings, ...context.staticObstacles];
  try {
    for (const issue of validateFacilityPlacement(facility, obstacles, context.roads,
      context.facilities, context.airspace)) {
      messages.push(issue.message);
    }
  } catch (error) {
    messages.push(`设施无法校验：${error instanceof Error ? error.message : String(error)}`);
  }
  return messages;
}

/** Deterministic candidate centres ordered by distance, then angle index. */
function ringOffsets(step: number, radius: number): PointXZ[] {
  const offsets: PointXZ[] = [{ x: 0, z: 0 }];
  const rings = Math.max(0, Math.floor(radius / step));
  for (let ring = 1; ring <= rings; ring++) {
    const steps = ring * 8;
    for (let index = 0; index < steps; index++) {
      const angle = (2 * Math.PI * index) / steps;
      offsets.push({ x: ring * step * Math.cos(angle), z: ring * step * Math.sin(angle) });
    }
  }
  offsets.sort((a, b) => {
    const da = Math.hypot(a.x, a.z), db = Math.hypot(b.x, b.z);
    return Math.abs(da - db) > EPSILON ? da - db
      : Math.abs(a.z - b.z) > EPSILON ? a.z - b.z : a.x - b.x;
  });
  return offsets;
}

function candidateReason(displacementM: number): string {
  return displacementM <= EPSILON
    ? "请求位置未与已验证建筑/道路/街道设施/设施/禁飞区相交；选区空白地块的土地用途未被核验"
    : `已吸附到最近的合法位置（位移 ${displacementM.toFixed(2)} m）：避让已验证建筑/道路/街道设施/设施/禁飞区与边界；`
      + "选区空白地块的土地用途未被核验";
}

/** Propose the nearest fully legal ground placement for a clicked point. */
export function proposeGroundSnap(facility: SelectedScenarioFacility, context: SnapContext,
                                  requested: PointXZ, options: SnapOptions = {}): SnapCandidate {
  const step = options.stepM ?? 1;
  const radius = options.radiusM ?? 12;
  if (!Number.isFinite(step) || step <= 0 || !Number.isFinite(radius) || radius < 0) {
    throw new Error("吸附步长与半径必须是非负数（步长须大于 0）");
  }
  const rotations = [facility.rotationDeg, ...(options.rotationsDeg ?? [0, 90, 180, 270])];
  const others = context.facilities.filter(other => other.id !== facility.id);
  const base: SelectedScenarioFacility = { ...facility, placement: "ground", buildingId: null, supportHeightM: null };
  for (const offset of ringOffsets(step, radius)) {
    const position = { x: requested.x + offset.x, z: requested.z + offset.z };
    for (const rotationDeg of rotations) {
      const candidate = { ...base, position, rotationDeg };
      if (issuesFor(candidate, { ...context, facilities: others }).length === 0) {
        const displacementM = Math.hypot(offset.x, offset.z);
        return { position, rotationDeg, displacementM, reason: candidateReason(displacementM),
          legal: true, issues: [], landUseVerified: false, supportHeightM: null };
      }
    }
  }
  const failed: SelectedScenarioFacility = { ...base, position: requested };
  const issues = issuesFor(failed, { ...context, facilities: others });
  return { position: requested, rotationDeg: facility.rotationDeg, displacementM: 0,
    reason: `在 ${radius} m 内未找到合法位置，保留请求点并列出冲突（未自动应用）`, legal: false,
    issues: issues.length ? issues : ["请求点无效"], landUseVerified: false, supportHeightM: null };
}

/** Propose a rooftop placement on one verified complete-footprint building. When the
 * building cannot prove roof support, the candidate is not legal and reports it. */
export function proposeRoofSnap(facility: SelectedScenarioFacility, context: SnapContext,
                                buildingId: string, requested: PointXZ): SnapCandidate {
  const support = context.roofSupports.find(item => item.buildingId === buildingId);
  if (support === undefined) {
    return { position: requested, rotationDeg: facility.rotationDeg, displacementM: 0, legal: false,
      reason: `建筑 ${buildingId} 不是具有完整来源足迹的核验建筑，来源无法证明屋顶支撑，`
        + "不虚构屋顶高度或拓扑", issues: ["rooftop_unsupported"], landUseVerified: false, supportHeightM: null };
  }
  const position = { x: support.x, z: support.z };
  const candidate: SelectedScenarioFacility = { ...facility, placement: "rooftop", buildingId,
    supportHeightM: support.topY, position, rotationDeg: facility.rotationDeg };
  const inside = footprintInsideSupport(candidate, support);
  const issues: string[] = [];
  if (!inside) issues.push(`旋转足迹超出核验建筑 ${buildingId} 的屋顶足迹`);
  if (facility.widthM > support.widthM + EPSILON || facility.depthM > support.depthM + EPSILON) {
    issues.push(`设施尺寸 ${facility.widthM}×${facility.depthM}m 大于屋顶 ${support.widthM}×${support.depthM}m`);
  }
  if (context.rooftopMesh !== undefined) {
    const geometry: RooftopVolumeGeometry = { position: candidate.position, rotationDeg: facility.rotationDeg,
      widthM: facility.widthM, depthM: facility.depthM, heightM: facility.heightM };
    for (const message of validateRooftopVolume(geometry, support, context.rooftopMesh)) {
      issues.push(message);
    }
  }
  const displacementM = Math.hypot(position.x - requested.x, position.z - requested.z);
  return { position, rotationDeg: facility.rotationDeg, displacementM, legal: issues.length === 0,
    reason: issues.length === 0
      ? `已吸附到建筑 ${buildingId} 核验屋顶中心，支撑高度 ${support.topY} m`
      : `建筑 ${buildingId} 核验屋顶无法容纳该设施：${issues.join("；")}`,
    issues, landUseVerified: false, supportHeightM: support.topY };
}

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

/** Apply a legal candidate onto the facility, refusing an illegal candidate. */
export function applySnapCandidate(facility: SelectedScenarioFacility, candidate: SnapCandidate):
    SelectedScenarioFacility {
  if (!candidate.legal) throw new Error(`拒绝对非法候选位置应用：${candidate.reason}`);
  return { ...facility, position: candidate.position, rotationDeg: candidate.rotationDeg,
    placement: candidate.supportHeightM === null ? "ground" : "rooftop",
    supportHeightM: candidate.supportHeightM,
    buildingId: candidate.supportHeightM === null ? null : facility.buildingId };
}
