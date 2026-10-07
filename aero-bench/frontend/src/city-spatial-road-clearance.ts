import type { CityVegetationRoadBinding } from "./city-vegetation-layer";
import { validateStaticCityRoadPayload } from "./city-roads";
import {
  validateFacilityPlacement,
  type FacilityPlacement,
  type PlacementBox,
  type PointXZ,
  type RoadPolygon,
} from "./city-workspace-geometry";

export type SpatialSurfaceKind = "roadbed" | "walkbed" | "crossing" | "fixture";
export type SpatialFacilityKind = "vertiport" | "hub" | "charger";

export interface SpatialRoadClearanceProvenance {
  readonly source: "canonical-road-v3" | "selected-static-road-v1" | "native-road-v3";
  readonly roadSha256: string;
  readonly fixtureIdentity: string;
  readonly displayedSurfaceSha256: string;
}

/** Road and street-fixture geometry accepted by its existing source verifier.
 * This is an authoring collision input. It is not a formal dynamics or land-use claim. */
export interface SpatialRoadClearance {
  readonly provenance: SpatialRoadClearanceProvenance;
  readonly roadbed: readonly RoadPolygon[];
  readonly walkbed: readonly RoadPolygon[];
  readonly crossings: readonly RoadPolygon[];
  readonly fixtures: readonly PlacementBox[];
}

export interface SpatialRoadClearanceIssue {
  readonly severity: "block" | "warning";
  readonly code: "clearance_unavailable" | "roadbed_overlap" | "walkbed_overlap"
    | "crossing_overlap" | "fixture_overlap" | "visual_clearance_only";
  readonly surface?: SpatialSurfaceKind;
  readonly obstacleId?: string;
  readonly message: string;
}

const SHA256 = /^[0-9a-f]{64}$/;
const facilityNames: Record<SpatialFacilityKind, string> = {
  vertiport: "地面起降点",
  hub: "物流中转站",
  charger: "充电站",
};

function checkedProvenance(provenance: SpatialRoadClearanceProvenance): SpatialRoadClearanceProvenance {
  if (!SHA256.test(provenance.roadSha256) || !SHA256.test(provenance.displayedSurfaceSha256)
      || !SHA256.test(provenance.fixtureIdentity)) {
    throw new Error("Spatial road clearance provenance is incomplete");
  }
  return provenance;
}

function points(source: readonly (readonly [number, number])[], label: string): PointXZ[] {
  if (!Array.isArray(source) || source.length < 3
      || source.some(point => !Array.isArray(point) || point.length !== 2 || !point.every(Number.isFinite))) {
    throw new Error(`${label} is not a finite polygon ring`);
  }
  return source.map(point => ({ x: point[0], z: point[1] }));
}

function polygons(source: readonly {
  readonly outline: readonly (readonly [number, number])[];
  readonly holes: readonly (readonly (readonly [number, number])[])[];
}[], prefix: "roadbed" | "walkbed"): RoadPolygon[] {
  if (!Array.isArray(source)) throw new Error(`Spatial ${prefix} polygons are missing`);
  return source.map((polygon, index) => {
    if (!Array.isArray(polygon.holes)) throw new Error(`Spatial ${prefix} ${index} has invalid holes`);
    return { id: `${prefix}:${index}`, outline: points(polygon.outline, `${prefix} ${index}`),
      holes: polygon.holes.map((hole: readonly (readonly [number, number])[], holeIndex: number) =>
        points(hole, `${prefix} ${index} hole ${holeIndex}`)) };
  });
}

/** Convert a crossing centreline to conservative square-ended segment footprints.
 * The source width is retained. Extending each segment by half the width closes joint
 * and endpoint gaps, so the authoring gate can over-block but cannot miss a crossing. */
function crossingPolygons(source: readonly {
  readonly id: string;
  readonly shape: readonly (readonly [number, number])[];
  readonly width: number;
}[]): RoadPolygon[] {
  if (!Array.isArray(source)) throw new Error("Spatial crossing inventory is missing");
  const result: RoadPolygon[] = [];
  for (const crossing of source) {
    if (!crossing.id || !Number.isFinite(crossing.width) || crossing.width <= 0
        || !Array.isArray(crossing.shape) || crossing.shape.length < 2
        || crossing.shape.some((point: readonly [number, number]) =>
          !Array.isArray(point) || point.length !== 2 || !point.every(Number.isFinite))) {
      throw new Error(`Spatial crossing ${crossing.id || "?"} is invalid`);
    }
    let segments = 0;
    for (let index = 1; index < crossing.shape.length; index++) {
      const start = crossing.shape[index - 1]!, end = crossing.shape[index]!;
      const dx = end[0] - start[0], dz = end[1] - start[1];
      const length = Math.hypot(dx, dz);
      if (length <= 1e-9) continue;
      const half = crossing.width / 2;
      const tx = dx / length, tz = dz / length;
      const nx = -tz * half, nz = tx * half;
      const sx = start[0] - tx * half, sz = start[1] - tz * half;
      const ex = end[0] + tx * half, ez = end[1] + tz * half;
      result.push({ id: `crossing:${crossing.id}:${index - 1}`, holes: [], outline: [
        { x: sx + nx, z: sz + nz }, { x: ex + nx, z: ez + nz },
        { x: ex - nx, z: ez - nz }, { x: sx - nx, z: sz - nz },
      ] });
      segments++;
    }
    if (segments === 0) throw new Error(`Spatial crossing ${crossing.id} has no nonzero segment`);
  }
  return result;
}

/** Conservative square-ended footprints for public-scenario road centrelines.
 * They preserve the declared width for editor context without claiming road-v3
 * provenance or complete street-fixture clearance. */
export function spatialCenterlineFootprints(source: readonly {
  readonly id: string;
  readonly points: readonly PointXZ[];
  readonly widthM: number;
}[]): RoadPolygon[] {
  return crossingPolygons(source.map(item => ({
    id: item.id,
    shape: item.points.map(point => [point.x, point.z] as const),
    width: item.widthM,
  }))).map(polygon => {
    if (polygon.id === undefined) throw new Error("Spatial centreline footprint has no identity");
    return { ...polygon, id: polygon.id.replace(/^crossing:/, "road:") };
  });
}

function stationFixtures(binding: CityVegetationRoadBinding): PlacementBox[] {
  const result: PlacementBox[] = [];
  const seen = new Set<string>();
  for (const [kind, stations] of [["street_lamp", binding.lamps], ["signal", binding.signals]] as const) {
    if (!Array.isArray(stations)) throw new Error(`Spatial ${kind} clearance inventory is missing`);
    for (const station of stations) {
      const id = `fixture:${kind}:${station.id}`;
      if (!station.id || seen.has(id) || ![station.x, station.z, station.radiusM].every(Number.isFinite)
          || station.radiusM <= 0) {
        throw new Error(`Spatial ${kind} clearance is invalid: ${station.id || "?"}`);
      }
      seen.add(id);
      // radiusM encloses the complete verified rendered assembly. The axis-aligned
      // square is conservative: it can reject extra space but cannot shrink the source envelope.
      result.push({ id, kind: "street_asset", x: station.x, z: station.z,
        widthM: station.radiusM * 2, depthM: station.radiusM * 2,
        heightM: 1000, rotationDeg: 0, baseY: 0 });
    }
  }
  return result;
}

/** Build the default-scene authoring gate only after `loadVerifiedCityRoadAssets`
 * has checked road v3, effective fixtures, traffic and flight against the same pins. */
export function spatialRoadClearanceFromCanonicalBinding(
  binding: CityVegetationRoadBinding,
  provenance: SpatialRoadClearanceProvenance,
): SpatialRoadClearance {
  if (provenance.source !== "canonical-road-v3") throw new Error("Canonical clearance has the wrong source kind");
  return {
    provenance: checkedProvenance(provenance),
    roadbed: polygons(binding.roadbed, "roadbed"),
    walkbed: polygons(binding.walkbed, "walkbed"),
    crossings: crossingPolygons(binding.crossings),
    fixtures: stationFixtures(binding),
  };
}

/** Reuse the road and fixture envelopes accepted by the native presentation loader. */
export function spatialRoadClearanceFromNativeGeometry(
  road: Pick<CityVegetationRoadBinding, "roadbed" | "walkbed" | "crossings">,
  fixtures: readonly PlacementBox[],
  provenance: SpatialRoadClearanceProvenance,
): SpatialRoadClearance {
  return {
    provenance: checkedProvenance(provenance),
    roadbed: polygons(road.roadbed, "roadbed"),
    walkbed: polygons(road.walkbed, "walkbed"),
    crossings: crossingPolygons(road.crossings),
    fixtures,
  };
}

/** The selected-city loader already digest-verifies the static presentation and
 * measures rendered street assets. Re-parse the road contract here so the panel sees
 * walkbed and crossings in addition to the motor roadbed used by the older validator. */
export function spatialRoadClearanceFromSelectedPresentation(
  road: unknown,
  measuredStaticObstacles: readonly PlacementBox[],
  provenance: Omit<SpatialRoadClearanceProvenance, "displayedSurfaceSha256">,
): SpatialRoadClearance {
  if (provenance.source !== "selected-static-road-v1") throw new Error("Selected clearance has the wrong source kind");
  const parsed = validateStaticCityRoadPayload(road);
  const fixtures = measuredStaticObstacles.filter(obstacle =>
    obstacle.id.startsWith("lamp:") || obstacle.id.startsWith("signal:"));
  return {
    provenance: checkedProvenance({ ...provenance, displayedSurfaceSha256: parsed.displayed_surface_sha256 }),
    roadbed: polygons(parsed.roadbed, "roadbed"),
    walkbed: polygons(parsed.walkbed, "walkbed"),
    crossings: crossingPolygons(parsed.crossings),
    fixtures: fixtures.map(fixture => ({ ...fixture })),
  };
}

export function spatialRoadBlockingPolygons(clearance: SpatialRoadClearance): RoadPolygon[] {
  return [...clearance.roadbed, ...clearance.walkbed, ...clearance.crossings];
}

function overlapIssues(spec: FacilityPlacement, surface: Exclude<SpatialSurfaceKind, "fixture">,
                       polygonsForSurface: readonly RoadPolygon[]): SpatialRoadClearanceIssue[] {
  const code = `${surface}_overlap` as "roadbed_overlap" | "walkbed_overlap" | "crossing_overlap";
  const names = { roadbed: "机动车道", walkbed: "人行铺装", crossing: "人行横道" } as const;
  return validateFacilityPlacement(spec, [], polygonsForSurface, [], [])
    .filter(issue => issue.code === "road_overlap")
    .map(issue => ({ severity: "block", code, surface, obstacleId: issue.obstacleId,
      message: `${facilityNames[spec.kind as SpatialFacilityKind]}足迹占用已验证${names[surface]} ${issue.obstacleId ?? "surface"}` }));
}

/** Ground facilities are structures for UAV operations, not curbside road furniture.
 * The workspace contract has no curbside or pedestrian-right-of-way exception, so every
 * facility kind blocks on roadbed, walkbed, crossings and verified fixtures. Rooftop
 * facilities keep their existing support/volume checks and do not collide with ground surfaces. */
export function evaluateSpatialRoadClearance(
  spec: FacilityPlacement,
  clearance: SpatialRoadClearance | null,
): SpatialRoadClearanceIssue[] {
  const kind = spec.kind as SpatialFacilityKind;
  const name = facilityNames[kind];
  if (name === undefined) {
    return [{ severity: "block", code: "clearance_unavailable",
      message: `未知设施类型 ${spec.kind} 无法应用道路净空规则` }];
  }
  if (spec.supportHeightM !== undefined && spec.supportHeightM !== null) {
    return [{ severity: "warning", code: "visual_clearance_only",
      message: `${name}位于核验屋顶支撑面；地面道路约束不适用。屋顶几何检查仍只是编辑预览，不代表正式结构或飞行动力学验证。` }];
  }
  if (clearance === null) {
    return [{ severity: "block", code: "clearance_unavailable",
      message: `${name}缺少已验证道路、人行铺装、人行横道与有效街道设施净空，不能放置或保存` }];
  }
  const issues: SpatialRoadClearanceIssue[] = [
    ...overlapIssues(spec, "roadbed", clearance.roadbed),
    ...overlapIssues(spec, "walkbed", clearance.walkbed),
    ...overlapIssues(spec, "crossing", clearance.crossings),
  ];
  for (const issue of validateFacilityPlacement(spec, clearance.fixtures, [], [], [])) {
    if (issue.code !== "static_overlap") continue;
    issues.push({ severity: "block", code: "fixture_overlap", surface: "fixture",
      obstacleId: issue.obstacleId,
      message: `${name}足迹与有效街道设施净空 ${issue.obstacleId ?? "fixture"} 重叠` });
  }
  if (issues.length > 0) return issues;
  return [{ severity: "warning", code: "visual_clearance_only",
    message: `${name}已避开当前摘要绑定的道路、人行铺装、人行横道与有效街道设施；此结果仅用于编辑预览，不代表土地许可或正式物理验证。` }];
}
