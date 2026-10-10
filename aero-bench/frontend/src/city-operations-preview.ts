import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { cacheStaticTransforms } from "./city-rendering";
import { createFacilityVisual, disposeFacilityVisual, facilityLandingPads,
  loadFacilityVisualAssets, type FacilityLandingPad, type FacilityVisualAssets } from "./city-facility-models";
import { CITY_FLEET_ASSETS, type CityAirspace, type CityFleetAsset,
  type CityWorkspaceConfig } from "./city-workspace-config";
import { validateFlightSegment, type LandingPadClearance, type PlacementBox,
  type PointXYZ } from "./city-workspace-geometry";

export interface PreviewValidationIssue {
  path: string;
  code: string;
  message: string;
  entityIds?: string[];
  atS?: number;
}

export interface PreviewCollisionContext { obstacles: readonly PlacementBox[] }

/** Body-centre coordinates in local east-up-south metres; time is scene seconds. */
export interface PreviewFlightSegment {
  entityId: string;
  source: "cycle" | "manual";
  phase: "ground" | "takeoff" | "hover" | "landing" | "manual";
  from: PointXYZ;
  to: PointXYZ;
  startsAtS: number;
  endsAtS: number;
  bodySizeM: { x: number; y: number; z: number };
}

type FleetEntry = CityWorkspaceConfig["fleet"][number];
type FacilityEntry = CityWorkspaceConfig["facilities"][number];
type Keyframe = CityWorkspaceConfig["stateKeyframes"][number];
type ModelLoader = (url: string) => Promise<THREE.Group>;

interface PlannedCraft {
  id: string;
  fleetId: string;
  label: string;
  asset: CityFleetAsset;
  position: THREE.Vector3;
}

interface PreviewCraft extends PlannedCraft {
  node: THREE.Group;
  lowDetail: THREE.Group;
  highDetail: THREE.Group;
  keyframes: Keyframe[];
}

interface FacilityView {
  spec: FacilityEntry;
  visual: THREE.Group;
  pads: FacilityLandingPad[];
}

interface ZoneView {
  spec: CityAirspace;
  visual: THREE.Group;
  geometry: THREE.BufferGeometry;
  edges: THREE.EdgesGeometry;
}

function centre(position: { x: number; y: number; z: number }, bodyHeight: number): PointXYZ {
  return { x: position.x, y: position.y + bodyHeight / 2, z: position.z };
}

function positionAt(segment: PreviewFlightSegment, time: number): PointXYZ {
  const fraction = (time - segment.startsAtS) / (segment.endsAtS - segment.startsAtS);
  return {
    x: THREE.MathUtils.lerp(segment.from.x, segment.to.x, fraction),
    y: THREE.MathUtils.lerp(segment.from.y, segment.to.y, fraction),
    z: THREE.MathUtils.lerp(segment.from.z, segment.to.z, fraction),
  };
}

/** Exact closest horizontal approach during the interval of vertical body overlap. */
function aircraftCollisionAt(left: PreviewFlightSegment, right: PreviewFlightSegment): number | null {
  const start = Math.max(left.startsAtS, right.startsAtS);
  const end = Math.min(left.endsAtS, right.endsAtS);
  if (end <= start) return null;
  const a = positionAt(left, start), b = positionAt(right, start);
  const duration = end - start;
  const leftDuration = left.endsAtS - left.startsAtS;
  const rightDuration = right.endsAtS - right.startsAtS;
  const vx = (left.to.x - left.from.x) / leftDuration - (right.to.x - right.from.x) / rightDuration;
  const vy = (left.to.y - left.from.y) / leftDuration - (right.to.y - right.from.y) / rightDuration;
  const vz = (left.to.z - left.from.z) / leftDuration - (right.to.z - right.from.z) / rightDuration;
  const dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
  const halfHeight = (left.bodySizeM.y + right.bodySizeM.y) / 2;
  let lower = 0, upper = duration;
  if (Math.abs(vy) < 1e-12) {
    if (Math.abs(dy) >= halfHeight) return null;
  } else {
    const first = (-halfHeight - dy) / vy, second = (halfHeight - dy) / vy;
    lower = Math.max(lower, Math.min(first, second));
    upper = Math.min(upper, Math.max(first, second));
    if (upper <= lower) return null;
  }
  const horizontalSpeedSquared = vx * vx + vz * vz;
  const optimum = horizontalSpeedSquared < 1e-18 ? lower
    : -(dx * vx + dz * vz) / horizontalSpeedSquared;
  const elapsed = Math.max(lower, Math.min(upper, optimum));
  const radius = (Math.hypot(left.bodySizeM.x, left.bodySizeM.z)
    + Math.hypot(right.bodySizeM.x, right.bodySizeM.z)) / 2;
  const horizontalDistanceSquared = (dx + vx * elapsed) ** 2 + (dz + vz * elapsed) ** 2;
  return horizontalDistanceSquared < radius * radius - 1e-9 ? start + elapsed : null;
}

function clipLinearInterval(startValue: number, endValue: number, low: number, high: number,
                            duration: number): [number, number] | null {
  const slope = (endValue - startValue) / duration;
  if (Math.abs(slope) < 1e-12) return startValue > low && startValue < high ? [0, duration] : null;
  const first = (low - startValue) / slope, second = (high - startValue) / slope;
  const begin = Math.max(0, Math.min(first, second));
  const end = Math.min(duration, Math.max(first, second));
  return end > begin ? [begin, end] : null;
}

function possibleManualCycleInterval(manual: PreviewFlightSegment,
                                     cycle: readonly PreviewFlightSegment[]): [number, number] | null {
  const fixed = cycle[0]!;
  const duration = manual.endsAtS - manual.startsAtS;
  const radius = (Math.hypot(manual.bodySizeM.x, manual.bodySizeM.z)
    + Math.hypot(fixed.bodySizeM.x, fixed.bodySizeM.z)) / 2;
  const dx = manual.from.x - fixed.from.x, dz = manual.from.z - fixed.from.z;
  const vx = (manual.to.x - manual.from.x) / duration;
  const vz = (manual.to.z - manual.from.z) / duration;
  const a = vx * vx + vz * vz;
  const b = 2 * (dx * vx + dz * vz);
  const c = dx * dx + dz * dz - radius * radius;
  let horizontal: [number, number] | null;
  if (a < 1e-18) horizontal = c < 0 ? [0, duration] : null;
  else {
    const discriminant = b * b - 4 * a * c;
    if (discriminant <= 0) return null;
    const first = (-b - Math.sqrt(discriminant)) / (2 * a);
    const second = (-b + Math.sqrt(discriminant)) / (2 * a);
    const start = Math.max(0, first), end = Math.min(duration, second);
    horizontal = end > start ? [start, end] : null;
  }
  if (horizontal === null) return null;
  const halfHeight = (manual.bodySizeM.y + fixed.bodySizeM.y) / 2;
  const vertical = clipLinearInterval(manual.from.y, manual.to.y,
    Math.min(...cycle.map(segment => Math.min(segment.from.y, segment.to.y))) - halfHeight,
    Math.max(...cycle.map(segment => Math.max(segment.from.y, segment.to.y))) + halfHeight,
    duration);
  if (vertical === null) return null;
  const start = Math.max(horizontal[0], vertical[0]);
  const end = Math.min(horizontal[1], vertical[1]);
  return end > start ? [manual.startsAtS + start, manual.startsAtS + end] : null;
}

const ASSET_BY_ID = new Map<string, CityFleetAsset>(CITY_FLEET_ASSETS.map(asset => [asset.id, asset]));
const LANDING_CLEARANCE_M = 0.5;
const CYCLE_SECONDS = 16;
const HOVER_HEIGHT_M = 6;

// GLB scene graph measurements: visible mesh primitives and indexed triangles per instance.
// One nearby/followed aircraft uses the original GLB; the others use source-derived LOD1.
// The city baseline is about 650 draws / 2.2 million triangles. This layer gets 20% of
// the draw count and 25% of the triangle count, including facilities and no-fly volumes.
export const PREVIEW_RENDER_BUDGET = { drawCalls: 130, triangles: 550_000 } as const;
export interface RenderCost { drawCalls: number; triangles: number }
const MODEL_LOW_RENDER_COST: Readonly<Record<CityFleetAsset["id"], RenderCost>> = {
  "model:holybro-x500": { drawCalls: 4, triangles: 18_475 },
  "model:quadcopter-40-preview": { drawCalls: 3, triangles: 15_997 },
};
const MODEL_HIGH_RENDER_COST: Readonly<Record<CityFleetAsset["id"], RenderCost>> = {
  "model:holybro-x500": { drawCalls: 4, triangles: 120_504 },
  "model:quadcopter-40-preview": { drawCalls: 3, triangles: 113_632 },
};

function highDetailDelta(asset: CityFleetAsset): RenderCost {
  const high = MODEL_HIGH_RENDER_COST[asset.id], low = MODEL_LOW_RENDER_COST[asset.id];
  return { drawCalls: high.drawCalls - low.drawCalls, triangles: high.triangles - low.triangles };
}

function geometryCost(object: THREE.Object3D): RenderCost {
  const result = { drawCalls: 0, triangles: 0 };
  object.traverse(node => {
    if (node instanceof THREE.Mesh) {
      result.drawCalls += Array.isArray(node.material) ? node.material.length : 1;
      result.triangles += (node.geometry.index?.count ?? node.geometry.attributes.position?.count ?? 0) / 3;
    } else if (node instanceof THREE.Line || node instanceof THREE.LineSegments) {
      result.drawCalls++;
    }
  });
  return result;
}

function addCost(left: RenderCost, right: RenderCost): RenderCost {
  return { drawCalls: left.drawCalls + right.drawCalls, triangles: left.triangles + right.triangles };
}

function exceedsBudget(cost: RenderCost): boolean {
  return cost.drawCalls > PREVIEW_RENDER_BUDGET.drawCalls
    || cost.triangles > PREVIEW_RENDER_BUDGET.triangles;
}

function craftId(fleet: FleetEntry, index: number): string {
  return fleet.count === 1 ? fleet.id : `${fleet.id}.${String(index + 1).padStart(2, "0")}`;
}

function craftDiameter(craft: readonly FleetEntry[]): number {
  return Math.max(...craft.map(entry => {
    const size = ASSET_BY_ID.get(entry.assetId)!.sizeM;
    return Math.hypot(size.x, size.z);
  }));
}

function padGrid(pad: FacilityLandingPad, diameter: number): { columns: number; rows: number } {
  const pitch = diameter + LANDING_CLEARANCE_M;
  return {
    columns: Math.max(0, Math.floor((pad.widthM - diameter + 1e-9) / pitch) + 1),
    rows: Math.max(0, Math.floor((pad.depthM - diameter + 1e-9) / pitch) + 1),
  };
}

function safeSlotCapacity(pads: readonly FacilityLandingPad[], diameter: number): number {
  return pads.reduce((sum, pad) => {
    const grid = padGrid(pad, diameter);
    return Math.min(Number.MAX_SAFE_INTEGER, sum + grid.columns * grid.rows);
  }, 0);
}

function safeSlots(pads: readonly FacilityLandingPad[], count: number, diameter: number): THREE.Vector3[] {
  const pitch = diameter + LANDING_CLEARANCE_M;
  const slots: THREE.Vector3[] = [];
  for (const pad of pads) {
    if (slots.length === count) break;
    const { columns: maxColumns, rows: maxRows } = padGrid(pad, diameter);
    if (maxColumns < 1 || maxRows < 1) continue;
    const take = Math.min(count - slots.length, maxColumns * maxRows);
    let columns = Math.min(maxColumns, Math.max(1, Math.ceil(Math.sqrt(take * pad.widthM / pad.depthM))));
    while (Math.ceil(take / columns) > maxRows) columns++;
    const rows = Math.ceil(take / columns);
    for (let row = 0, placed = 0; row < rows; row++) {
      const rowCount = Math.min(columns, take - placed);
      for (let column = 0; column < rowCount; column++, placed++) {
        slots.push(new THREE.Vector3(
          pad.x + (column - (rowCount - 1) / 2) * pitch,
          pad.y,
          pad.z + (row - (rows - 1) / 2) * pitch,
        ));
      }
    }
  }
  return slots;
}

function fitModel(source: THREE.Group, asset: CityFleetAsset): THREE.Group {
  const fitted = new THREE.Group();
  fitted.add(source.clone(true));
  fitted.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(fitted);
  const size = bounds.getSize(new THREE.Vector3());
  if (bounds.isEmpty() || size.x <= 0 || size.y <= 0 || size.z <= 0) {
    throw new Error(`Asset ${asset.id} has no valid three-dimensional geometry`);
  }
  const scales = [asset.sizeM.x / size.x, asset.sizeM.y / size.y, asset.sizeM.z / size.z];
  const scale = Math.min(...scales);
  if (Math.max(...scales) / scale > 1.01) {
    throw new Error(`Asset ${asset.id} display dimensions do not preserve source proportions`);
  }
  fitted.scale.setScalar(scale);
  fitted.updateMatrixWorld(true);
  bounds.setFromObject(fitted);
  const center = bounds.getCenter(new THREE.Vector3());
  fitted.position.set(-center.x, -bounds.min.y, -center.z);
  // The fitted model is final once scaled and grounded; the craft node moves per tick,
  // and chooseHighDetail only toggles visibility of these two chains.
  cacheStaticTransforms(fitted);
  fitted.traverse(node => {
    if (node instanceof THREE.Mesh) {
      node.castShadow = false;
      node.receiveShadow = true;
    }
  });
  return fitted;
}

function zoneVolume(zone: CityAirspace, material: THREE.Material, edgeMaterial: THREE.Material): ZoneView {
  const points = zone.polygon;
  const ring = points.length > 3 && points[0]!.x === points[points.length - 1]!.x
    && points[0]!.z === points[points.length - 1]!.z ? points.slice(0, -1) : points;
  const shape = new THREE.Shape(ring.map(point => new THREE.Vector2(point.x, -point.z)));
  const geometry = new THREE.ExtrudeGeometry(shape, {
    depth: zone.ceilingM - zone.floorM, bevelEnabled: false, curveSegments: 1, steps: 1,
  });
  geometry.rotateX(-Math.PI / 2);
  const edges = new THREE.EdgesGeometry(geometry);
  const visual = new THREE.Group();
  visual.name = `禁飞区 ${zone.name}`;
  visual.position.y = zone.floorM;
  visual.userData.target = { kind: "region", id: zone.id };
  visual.add(new THREE.Mesh(geometry, material), new THREE.LineSegments(edges, edgeMaterial));
  // The volume is baked at its floor height; zone gating only toggles visibility.
  cacheStaticTransforms(visual);
  return { spec: zone, visual, geometry, edges };
}

function disposeModelResources(root: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  root.traverse(node => {
    if (!(node instanceof THREE.Mesh)) return;
    geometries.add(node.geometry);
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      materials.add(material);
      for (const value of Object.values(material)) {
        if (value instanceof THREE.Texture) textures.add(value);
      }
    }
  });
  for (const geometry of geometries) geometry.dispose();
  for (const material of materials) material.dispose();
  for (const texture of textures) texture.dispose();
}

/** Authoring geometry only. No pose, status, energy, or mission result is inferred from a Provider. */
export class CityOperationsPreview {
  readonly group = new THREE.Group();

  private readonly modelLoader: ModelLoader;
  private readonly facilityAssetLoader: () => Promise<FacilityVisualAssets>;
  private readonly modelCache = new Map<string, Promise<THREE.Group>>();
  private readonly modelSources = new Set<THREE.Group>();
  private readonly facilities = new Map<string, FacilityView>();
  private readonly zones: ZoneView[] = [];
  private readonly craft = new Map<string, PreviewCraft>();
  private readonly segments: PreviewFlightSegment[] = [];
  private readonly zoneMaterial = new THREE.MeshBasicMaterial({
    color: 0xff685d, opacity: 0.13, transparent: true, depthWrite: false, side: THREE.DoubleSide,
  });
  private readonly zoneEdgeMaterial = new THREE.LineBasicMaterial({ color: 0xff685d, transparent: true, opacity: 0.78 });
  private generation = 0;
  private timeSeconds = 0;
  private disposed = false;
  private renderCost: RenderCost = { drawCalls: 0, triangles: 0 };
  private highDetailCost: RenderCost = { drawCalls: 0, triangles: 0 };
  private viewCamera: THREE.Camera | null = null;
  private followedEntityId: string | null = null;
  private readonly cameraPosition = new THREE.Vector3();

  constructor(scene: THREE.Scene, loadModel?: ModelLoader,
              loadFacilityAssets: () => Promise<FacilityVisualAssets> = loadFacilityVisualAssets) {
    const loader = new GLTFLoader();
    this.modelLoader = loadModel ?? (async url => (await loader.loadAsync(url)).scene);
    this.facilityAssetLoader = loadFacilityAssets;
    this.group.name = "场景编排预览";
    scene.add(this.group);
  }

  private getModel(url: string): Promise<THREE.Group> {
    const cached = this.modelCache.get(url);
    if (cached !== undefined) return cached;
    const pending = this.modelLoader(url).then(model => {
      if (this.disposed) {
        disposeModelResources(model);
        throw new Error("Operations preview was disposed while loading an asset");
      }
      this.modelSources.add(model);
      return model;
    }).catch(error => {
      this.modelCache.delete(url);
      throw error;
    });
    this.modelCache.set(url, pending);
    return pending;
  }

  private clearVisuals(): void {
    for (const craft of this.craft.values()) craft.node.parent?.remove(craft.node);
    this.craft.clear();
    for (const view of this.facilities.values()) disposeFacilityVisual(view.visual);
    this.facilities.clear();
    for (const zone of this.zones) {
      zone.visual.parent?.remove(zone.visual);
      zone.geometry.dispose();
      zone.edges.dispose();
    }
    this.zones.length = 0;
    this.segments.length = 0;
    this.renderCost = { drawCalls: 0, triangles: 0 };
    this.highDetailCost = { drawCalls: 0, triangles: 0 };
  }

  setViewCamera(camera: THREE.Camera): void {
    this.viewCamera = camera;
    this.chooseHighDetail();
  }

  setHighDetailEntity(id: string | null): void {
    this.followedEntityId = id;
    this.chooseHighDetail();
  }

  private chooseHighDetail(): void {
    let chosen: PreviewCraft | undefined;
    if (this.followedEntityId !== null) chosen = this.craft.get(this.followedEntityId);
    if (chosen === undefined && this.viewCamera !== null) {
      this.viewCamera.getWorldPosition(this.cameraPosition);
      let nearest = Infinity;
      for (const craft of this.craft.values()) {
        const distance = craft.node.position.distanceToSquared(this.cameraPosition);
        if (distance < nearest) { nearest = distance; chosen = craft; }
      }
    }
    chosen ??= this.craft.values().next().value;
    for (const craft of this.craft.values()) {
      craft.highDetail.visible = craft === chosen;
      craft.lowDetail.visible = craft !== chosen;
    }
  }

  private landingClearances(): LandingPadClearance[] {
    const result: LandingPadClearance[] = [];
    for (const view of this.facilities.values()) {
      view.visual.updateMatrixWorld(true);
      for (const pad of view.pads) {
        const centrePoint = view.visual.localToWorld(new THREE.Vector3(pad.x, pad.y, pad.z));
        result.push({ facilityId: view.spec.id, x: centrePoint.x, z: centrePoint.z,
          widthM: pad.widthM, depthM: pad.depthM, rotationDeg: view.spec.rotationDeg,
          padY: centrePoint.y });
      }
    }
    return result;
  }

  private buildFlightSegments(config: CityWorkspaceConfig): void {
    this.segments.length = 0;
    const horizon = Math.max(CYCLE_SECONDS,
      ...config.airspace.map(zone => zone.startsAtS + CYCLE_SECONDS),
      ...config.stateKeyframes.map(frame => frame.atS + CYCLE_SECONDS));
    for (const craft of this.craft.values()) {
      const size = craft.asset.sizeM;
      const bodySizeM = { x: size.x, y: size.y, z: size.z };
      const append = (source: PreviewFlightSegment["source"], phase: PreviewFlightSegment["phase"],
                      from: PointXYZ, to: PointXYZ, startsAtS: number, endsAtS: number): void => {
        if (endsAtS <= startsAtS) return;
        this.segments.push({ entityId: craft.id, source, phase, from, to,
          startsAtS, endsAtS, bodySizeM });
      };
      if (craft.keyframes.length > 0) {
        const frames = craft.keyframes;
        const first = frames[0]!;
        const firstCentre = centre(first.position, size.y);
        append("manual", "manual", firstCentre, firstCentre, 0, first.atS);
        for (let index = 1; index < frames.length; index++) {
          const left = frames[index - 1]!, right = frames[index]!;
          append("manual", "manual", centre(left.position, size.y),
            centre(right.position, size.y), left.atS, right.atS);
        }
        const last = frames[frames.length - 1]!;
        const lastCentre = centre(last.position, size.y);
        append("manual", "manual", lastCentre, lastCentre, last.atS,
          Math.max(horizon, last.atS + CYCLE_SECONDS));
      } else {
        const ground = centre(craft.position, size.y);
        const hover = { ...ground, y: ground.y + HOVER_HEIGHT_M };
        append("cycle", "ground", ground, ground, 0, 2);
        append("cycle", "takeoff", ground, hover, 2, 5);
        append("cycle", "hover", hover, hover, 5, 9);
        append("cycle", "landing", hover, ground, 9, 12);
        append("cycle", "ground", ground, ground, 12, 16);
      }
    }
  }

  private validateFlightSegments(config: CityWorkspaceConfig,
                                 context: PreviewCollisionContext | undefined): PreviewValidationIssue[] {
    const issues: PreviewValidationIssue[] = [];
    if (this.segments.length === 0) return issues;
    if (context === undefined) {
      issues.push({ path: "fleet", code: "missing_collision_context",
        message: "缺少建筑几何，无法核验编排航线" });
    }
    const pads = this.landingClearances();
    const seen = new Set<string>();
    const evaluate = (segment: PreviewFlightSegment, offset: number): void => {
      const startsAtS = segment.startsAtS + offset;
      const endsAtS = segment.endsAtS + offset;
      try {
        for (const issue of validateFlightSegment(segment.from, segment.to, startsAtS, endsAtS,
          context?.obstacles ?? [], config.facilities, config.airspace, segment.bodySizeM, pads)) {
          const obstacleParts = issue.obstacleId.split(":");
          const obstacleId = obstacleParts[0] === "street_lamp" && obstacleParts.length >= 3
            ? obstacleParts.slice(0, -1).join(":") : issue.obstacleId;
          const key = `${segment.entityId}:${issue.code}:${obstacleId}`;
          if (seen.has(key)) continue;
          seen.add(key);
          const message = issue.code === "building_collision"
            ? `航段穿过静态障碍物 ${obstacleId}` : issue.message;
          issues.push({ path: segment.source === "manual" ? "stateKeyframes" : `fleet.${segment.entityId}`,
            code: issue.code, message: `${segment.entityId}: ${message}`,
            entityIds: [segment.entityId], atS: startsAtS });
        }
      } catch (error) {
        const key = `${segment.entityId}:invalid_flight_segment`;
        if (seen.has(key)) return;
        seen.add(key);
        issues.push({ path: segment.source === "manual" ? "stateKeyframes" : `fleet.${segment.entityId}`,
          code: "invalid_flight_segment", message: error instanceof Error ? error.message : String(error),
          entityIds: [segment.entityId], atS: startsAtS });
      }
    };
    const cycleOffsets = new Set<number>([0]);
    for (const zone of config.airspace) {
      const cycle = Math.floor(zone.startsAtS / CYCLE_SECONDS);
      cycleOffsets.add(cycle * CYCLE_SECONDS);
      cycleOffsets.add((cycle + 1) * CYCLE_SECONDS);
    }
    for (const segment of this.segments) {
      if (segment.from.y < segment.bodySizeM.y / 2 || segment.to.y < segment.bodySizeM.y / 2) {
        issues.push({ path: segment.source === "manual" ? "stateKeyframes" : `fleet.${segment.entityId}`,
          code: "ground_collision", message: `${segment.entityId} 航段低于地面`,
          entityIds: [segment.entityId], atS: segment.startsAtS });
      }
      if (segment.source === "manual") evaluate(segment, 0);
      else for (const offset of cycleOffsets) evaluate(segment, offset);
    }
    return issues;
  }

  private validateAircraftSeparation(): PreviewValidationIssue[] {
    const byEntity = new Map<string, PreviewFlightSegment[]>();
    for (const segment of this.segments) {
      const items = byEntity.get(segment.entityId) ?? [];
      items.push(segment);
      byEntity.set(segment.entityId, items);
    }
    const ids = [...byEntity.keys()];
    const issues: PreviewValidationIssue[] = [];
    for (let leftIndex = 0; leftIndex < ids.length; leftIndex++) {
      for (let rightIndex = leftIndex + 1; rightIndex < ids.length; rightIndex++) {
        const leftId = ids[leftIndex]!, rightId = ids[rightIndex]!;
        const left = byEntity.get(leftId)!, right = byEntity.get(rightId)!;
        const leftCycle = left[0]!.source === "cycle", rightCycle = right[0]!.source === "cycle";
        if (leftCycle && rightCycle) {
          const radius = (Math.hypot(left[0]!.bodySizeM.x, left[0]!.bodySizeM.z)
            + Math.hypot(right[0]!.bodySizeM.x, right[0]!.bodySizeM.z)) / 2;
          if (Math.hypot(left[0]!.from.x - right[0]!.from.x,
            left[0]!.from.z - right[0]!.from.z) >= radius) continue;
        }
        let collisionAt: number | null = null;
        let incompleteAt: number | null = null;
        if (leftCycle === rightCycle) {
          for (const a of left) for (const b of right) {
            const at = aircraftCollisionAt(a, b);
            if (at !== null && (collisionAt === null || at < collisionAt)) collisionAt = at;
          }
        } else {
          const manual = leftCycle ? right : left;
          const cycle = leftCycle ? left : right;
          for (const authored of manual) {
            const possible = possibleManualCycleInterval(authored, cycle);
            if (possible === null) continue;
            const firstCycle = Math.floor(possible[0] / CYCLE_SECONDS);
            const lastCycle = Math.floor(possible[1] / CYCLE_SECONDS);
            const checkedLastCycle = Math.min(lastCycle, firstCycle + 511);
            for (let index = firstCycle; index <= checkedLastCycle; index++) {
              for (const phase of cycle) {
                const shifted = { ...phase, startsAtS: phase.startsAtS + index * CYCLE_SECONDS,
                  endsAtS: phase.endsAtS + index * CYCLE_SECONDS };
                const at = aircraftCollisionAt(authored, shifted);
                if (at !== null && (collisionAt === null || at < collisionAt)) collisionAt = at;
              }
            }
            if (lastCycle > checkedLastCycle && collisionAt === null) incompleteAt = possible[0];
          }
        }
        if (collisionAt !== null) {
          issues.push({ path: "stateKeyframes", code: "aircraft_collision",
            message: `${leftId} 与 ${rightId} 在约 ${collisionAt.toFixed(3)} 秒处间距不足`,
            entityIds: [leftId, rightId], atS: collisionAt });
        } else if (incompleteAt !== null) {
          issues.push({ path: "stateKeyframes", code: "trajectory_check_limit",
            message: `${leftId} 与 ${rightId} 在超过 512 个飞行周期内持续接近；请缩短手动航段以完成碰撞核验`,
            entityIds: [leftId, rightId], atS: incompleteAt });
        }
      }
    }
    return issues;
  }

  async setConfig(config: CityWorkspaceConfig, context?: PreviewCollisionContext): Promise<PreviewValidationIssue[]> {
    if (this.disposed) throw new Error("Operations preview is disposed");
    const generation = ++this.generation;
    this.clearVisuals();
    const issues: PreviewValidationIssue[] = [];
    let facilityAssets: FacilityVisualAssets | undefined;
    if (config.facilities.some(facility => facility.kind === "vertiport")) {
      try {
        facilityAssets = await this.facilityAssetLoader();
      } catch (error) {
        issues.push({ path: "facilities", code: "facility_asset_load_failed",
          message: error instanceof Error ? error.message : String(error) });
      }
      if (generation !== this.generation || this.disposed) return [];
    }

    for (const [index, spec] of config.facilities.entries()) {
      try {
        if (spec.kind === "vertiport" && facilityAssets === undefined) continue;
        const visual = createFacilityVisual(spec, facilityAssets);
        visual.userData.target = { kind: "entity", id: spec.id };
        visual.userData.entityKind = "static_asset";
        visual.traverse(node => { if (node instanceof THREE.Mesh) node.castShadow = false; });
        const nextCost = addCost(this.renderCost, geometryCost(visual));
        if (exceedsBudget(nextCost)) {
          disposeFacilityVisual(visual);
          issues.push({ path: `facilities[${index}]`, code: "preview_render_budget",
            message: `设施 ${spec.name} 超出编排预览几何预算` });
          continue;
        }
        this.renderCost = nextCost;
        this.group.add(visual);
        this.facilities.set(spec.id, { spec, visual, pads: facilityLandingPads(spec) });
      } catch (error) {
        issues.push({ path: `facilities[${index}]`, code: "invalid_facility",
          message: error instanceof Error ? error.message : String(error) });
      }
    }
    for (const [index, spec] of config.airspace.entries()) {
      try {
        const zone = zoneVolume(spec, this.zoneMaterial, this.zoneEdgeMaterial);
        const nextCost = addCost(this.renderCost, geometryCost(zone.visual));
        if (exceedsBudget(nextCost)) {
          zone.geometry.dispose();
          zone.edges.dispose();
          issues.push({ path: `airspace[${index}]`, code: "preview_render_budget",
            message: `禁飞区 ${spec.name} 超出编排预览几何预算` });
          continue;
        }
        this.renderCost = nextCost;
        this.zones.push(zone);
        this.group.add(zone.visual);
      } catch (error) {
        issues.push({ path: `airspace[${index}]`, code: "invalid_airspace",
          message: error instanceof Error ? error.message : String(error) });
      }
    }

    const staticCost = { ...this.renderCost };
    const fleetsByHome = new Map<string, { fleet: FleetEntry; index: number }[]>();
    const fleetAsset = new Map<FleetEntry, CityFleetAsset>();
    for (const [index, fleet] of config.fleet.entries()) {
      const path = `fleet[${index}]`;
      const asset = ASSET_BY_ID.get(fleet.assetId);
      if (asset === undefined) {
        issues.push({ path: `${path}.assetId`, code: "unknown_asset", message: `未知机型资产 ${fleet.assetId}` });
        continue;
      }
      if (fleet.homeFacilityId === null) {
        issues.push({ path: `${path}.homeFacilityId`, code: "missing_home", message: `编队 ${fleet.id} 未指定起降设施` });
        continue;
      }
      if (!this.facilities.has(fleet.homeFacilityId)) {
        issues.push({ path: `${path}.homeFacilityId`, code: "unknown_home",
          message: `编队 ${fleet.id} 引用的设施 ${fleet.homeFacilityId} 不存在` });
        continue;
      }
      fleetAsset.set(fleet, asset);
      const atHome = fleetsByHome.get(fleet.homeFacilityId) ?? [];
      atHome.push({ fleet, index });
      fleetsByHome.set(fleet.homeFacilityId, atHome);
    }

    const planned: PlannedCraft[] = [];
    const usedIds = new Set(this.facilities.keys());
    for (const [homeId, entries] of fleetsByHome) {
      const home = this.facilities.get(homeId)!;
      const total = entries.reduce((sum, entry) => sum + entry.fleet.count, 0);
      if (total > home.spec.capacity) {
        issues.push({ path: `facilities[${config.facilities.indexOf(home.spec)}].capacity`, code: "capacity_exceeded",
          message: `${home.spec.name} 声明 ${home.spec.capacity} 个机位，但需停放 ${total} 架` });
        continue;
      }
      if (home.pads.length === 0) {
        issues.push({ path: `facilities[${config.facilities.indexOf(home.spec)}]`, code: "no_landing_surface",
          message: `${home.spec.name} 没有可用起降面` });
        continue;
      }
      const diameter = craftDiameter(entries.map(entry => entry.fleet));
      const geometricCapacity = safeSlotCapacity(home.pads, diameter);
      if (geometricCapacity < total) {
        issues.push({ path: `facilities[${config.facilities.indexOf(home.spec)}]`, code: "insufficient_landing_space",
          message: `${home.spec.name} 按安全间距只能容纳 ${geometricCapacity} 架，请求 ${total} 架` });
        continue;
      }
      const fleetCost = entries.reduce((sum, entry) => {
        const model = MODEL_LOW_RENDER_COST[fleetAsset.get(entry.fleet)!.id];
        return addCost(sum, { drawCalls: model.drawCalls * entry.fleet.count,
          triangles: model.triangles * entry.fleet.count });
      }, { drawCalls: 0, triangles: 0 });
      const nextCost = addCost(this.renderCost, fleetCost);
      const largestNewDelta = entries.reduce((largest, entry) => {
        const delta = highDetailDelta(fleetAsset.get(entry.fleet)!);
        return { drawCalls: Math.max(largest.drawCalls, delta.drawCalls),
          triangles: Math.max(largest.triangles, delta.triangles) };
      }, this.highDetailCost);
      const visibleCost = addCost(nextCost, largestNewDelta);
      if (exceedsBudget(visibleCost)) {
        issues.push({ path: `facilities[${config.facilities.indexOf(home.spec)}]`, code: "preview_render_budget",
          message: `${home.spec.name} 的机群将使预览开销升至 ${visibleCost.drawCalls} 次绘制 / ${Math.ceil(visibleCost.triangles)} 个三角形；预算为 ${PREVIEW_RENDER_BUDGET.drawCalls} 次绘制 / ${PREVIEW_RENDER_BUDGET.triangles} 个三角形` });
        continue;
      }
      this.renderCost = nextCost;
      this.highDetailCost = largestNewDelta;
      const slots = safeSlots(home.pads, total, diameter);
      home.visual.updateMatrixWorld(true);
      let slotIndex = 0;
      for (const { fleet, index } of entries) {
        const ids = Array.from({ length: fleet.count }, (_, i) => craftId(fleet, i));
        if (ids.some(id => usedIds.has(id))) {
          issues.push({ path: `fleet[${index}].id`, code: "entity_id_collision",
            message: `编队 ${fleet.id} 生成的实体 ID 已被占用` });
          slotIndex += fleet.count;
          continue;
        }
        for (let i = 0; i < fleet.count; i++) {
          const id = ids[i]!;
          usedIds.add(id);
          const position = home.visual.localToWorld(slots[slotIndex++]!.clone());
          planned.push({ id, fleetId: fleet.id,
            label: fleet.count === 1 ? fleet.id : `${fleet.id} ${i + 1}`,
            asset: fleetAsset.get(fleet)!, position });
        }
      }
    }

    const loaded = await Promise.allSettled([...new Set(planned.flatMap(craft =>
      [craft.asset.url, craft.asset.previewLodUrl]))]
      .map(async url => [url, await this.getModel(url)] as const));
    if (generation !== this.generation || this.disposed) return [];
    const models = new Map<string, THREE.Group>();
    for (const result of loaded) {
      if (result.status === "fulfilled") models.set(result.value[0], result.value[1]);
    }
    for (const craft of planned) {
      const low = models.get(craft.asset.previewLodUrl);
      const high = models.get(craft.asset.url);
      if (low === undefined || high === undefined) {
        issues.push({ path: `fleet.${craft.id}.assetId`, code: "asset_load_failed",
          message: `无法加载 ${craft.asset.id} 的源模型与 LOD 预览资产` });
        continue;
      }
      try {
        const node = new THREE.Group();
        node.name = craft.label;
        node.userData.target = { kind: "entity", id: craft.id };
        node.userData.entityKind = "uav";
        node.position.copy(craft.position);
        const lowDetail = fitModel(low, craft.asset);
        const highDetail = fitModel(high, craft.asset);
        highDetail.visible = false;
        node.add(lowDetail, highDetail);
        this.group.add(node);
        this.craft.set(craft.id, { ...craft, node, lowDetail, highDetail, keyframes: [] });
      } catch (error) {
        issues.push({ path: `fleet.${craft.id}.assetId`, code: "asset_geometry_invalid",
          message: error instanceof Error ? error.message : String(error) });
      }
    }

    const framesByEntity = new Map<string, Keyframe[]>();
    for (const [index, frame] of config.stateKeyframes.entries()) {
      if (!this.craft.has(frame.entityId)) {
        issues.push({ path: `stateKeyframes[${index}].entityId`, code: "unknown_keyframe_entity",
          message: `关键帧 ${frame.id} 引用的飞行器 ${frame.entityId} 不存在` });
        continue;
      }
      const frames = framesByEntity.get(frame.entityId) ?? [];
      frames.push(frame);
      framesByEntity.set(frame.entityId, frames);
    }
    for (const [id, frames] of framesByEntity) {
      frames.sort((a, b) => a.atS - b.atS);
      if (frames.some((frame, index) => index > 0 && frame.atS === frames[index - 1]!.atS)) {
        issues.push({ path: "stateKeyframes", code: "duplicate_keyframe_time",
          message: `飞行器 ${id} 在同一时刻有多个关键帧` });
        continue;
      }
      this.craft.get(id)!.keyframes = frames;
    }
    this.buildFlightSegments(config);
    issues.push(...this.validateFlightSegments(config, context));
    issues.push(...this.validateAircraftSeparation());
    const invalidFleets = new Set<string>();
    for (const issue of issues) {
      if (issue.code !== "building_collision" && issue.code !== "facility_collision"
          && issue.code !== "airspace_incursion" && issue.code !== "ground_collision"
          && issue.code !== "invalid_flight_segment" && issue.code !== "aircraft_collision"
          && issue.code !== "trajectory_check_limit") continue;
      for (const id of issue.entityIds ?? []) {
        const craft = this.craft.get(id);
        if (craft !== undefined) invalidFleets.add(craft.fleetId);
      }
    }
    if (invalidFleets.size > 0) {
      for (const [id, craft] of this.craft) {
        if (!invalidFleets.has(craft.fleetId)) continue;
        craft.node.parent?.remove(craft.node);
        this.craft.delete(id);
      }
    }
    this.renderCost = [...this.craft.values()].reduce((sum, craft) =>
      addCost(sum, MODEL_LOW_RENDER_COST[craft.asset.id]), staticCost);
    this.highDetailCost = [...this.craft.values()].reduce((largest, craft) => {
      const delta = highDetailDelta(craft.asset);
      return { drawCalls: Math.max(largest.drawCalls, delta.drawCalls),
        triangles: Math.max(largest.triangles, delta.triangles) };
    }, { drawCalls: 0, triangles: 0 });
    this.update(this.timeSeconds);
    return issues;
  }

  update(timeSeconds: number): void {
    if (!Number.isFinite(timeSeconds)) throw new RangeError("Preview time must be finite seconds");
    this.timeSeconds = timeSeconds;
    for (const zone of this.zones) {
      zone.visual.visible = timeSeconds >= zone.spec.startsAtS
        && (zone.spec.endsAtS === null || timeSeconds < zone.spec.endsAtS);
    }
    for (const craft of this.craft.values()) {
      const frames = craft.keyframes;
      if (frames.length > 0) {
        craft.node.userData.flightPhase = "manual";
        const first = frames[0]!;
        const last = frames[frames.length - 1]!;
        if (timeSeconds <= first.atS) {
          craft.node.position.set(first.position.x, first.position.y, first.position.z);
          craft.node.userData.previewLabel = first.label;
        } else if (timeSeconds >= last.atS) {
          craft.node.position.set(last.position.x, last.position.y, last.position.z);
          craft.node.userData.previewLabel = last.label;
        } else {
          let right = 1;
          while (frames[right]!.atS < timeSeconds) right++;
          const a = frames[right - 1]!, b = frames[right]!;
          craft.node.userData.previewLabel = a.label;
          const ratio = (timeSeconds - a.atS) / (b.atS - a.atS);
          craft.node.position.set(
            THREE.MathUtils.lerp(a.position.x, b.position.x, ratio),
            THREE.MathUtils.lerp(a.position.y, b.position.y, ratio),
            THREE.MathUtils.lerp(a.position.z, b.position.z, ratio),
          );
        }
      } else {
        craft.node.userData.previewLabel = "";
        const phase = ((timeSeconds % CYCLE_SECONDS) + CYCLE_SECONDS) % CYCLE_SECONDS;
        craft.node.userData.flightPhase = phase < 2 || phase >= 12 ? "ground"
          : phase < 5 ? "takeoff" : phase < 9 ? "hover" : "landing";
        const lift = phase < 2 ? 0 : phase < 5 ? (phase - 2) / 3 * HOVER_HEIGHT_M
          : phase < 9 ? HOVER_HEIGHT_M : phase < 12 ? (12 - phase) / 3 * HOVER_HEIGHT_M : 0;
        craft.node.position.set(craft.position.x, craft.position.y + lift, craft.position.z);
      }
    }
    this.chooseHighDetail();
  }

  entityPosition(id: string): THREE.Vector3 | null {
    const craft = this.craft.get(id);
    if (craft !== undefined) return craft.node.position.clone();
    const facility = this.facilities.get(id);
    return facility === undefined ? null : facility.visual.position.clone();
  }

  entityLabel(id: string): string | null {
    const craft = this.craft.get(id);
    if (craft !== undefined) return String(craft.node.userData.previewLabel || craft.label);
    return this.facilities.get(id)?.spec.name ?? null;
  }

  entityChoices(): { id: string; label: string }[] {
    return [
      ...[...this.facilities.values()].map(view => ({ id: view.spec.id, label: view.spec.name })),
      ...[...this.craft.values()].map(view => ({ id: view.id, label: view.label })),
    ];
  }

  renderEstimate(): RenderCost {
    return addCost(this.renderCost, this.highDetailCost);
  }

  flightSegments(): readonly PreviewFlightSegment[] {
    return this.segments;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.generation++;
    this.clearVisuals();
    this.group.parent?.remove(this.group);
    this.zoneMaterial.dispose();
    this.zoneEdgeMaterial.dispose();
    for (const model of this.modelSources) disposeModelResources(model);
    this.modelSources.clear();
    this.modelCache.clear();
  }
}
