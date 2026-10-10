import { projectGeographic, type GeographicOrigin } from "./osm2world/projection";

/** The city uses metres, with X east and Z south. */
export interface PointXZ { readonly x: number; readonly z: number; }
export interface PointXYZ extends PointXZ { readonly y: number; }

export interface PlacementBox {
  readonly id: string;
  readonly kind?: "street_asset";
  readonly x: number;
  readonly z: number;
  readonly widthM: number;
  readonly depthM: number;
  readonly heightM: number;
  readonly rotationDeg: number;
  readonly baseY?: number;
}

export interface BuildingPlacementSource {
  readonly building_id: string;
  readonly part: number;
  readonly x: number;
  readonly z: number;
  readonly width: number;
  readonly depth: number;
  readonly height: number;
  readonly rotation_deg: number;
  readonly base_y: number;
}

export interface RoadbedSource {
  readonly outline: readonly (readonly [number, number])[];
  readonly holes: readonly (readonly (readonly [number, number])[])[];
}

export interface RoadPolygon {
  readonly id?: string;
  readonly outline: readonly PointXZ[];
  readonly holes: readonly (readonly PointXZ[])[];
}

/** Structural subset of CityFacility; dimensions and position use the same fields.
 * A rooftop facility carries its verified support plane in `supportHeightM`, so its
 * collision volume is lifted above the ground and never collides with the roof
 * plane it sits on. */
export interface FacilityPlacement {
  readonly id: string;
  readonly kind: string;
  readonly position: PointXZ;
  readonly widthM: number;
  readonly depthM: number;
  readonly heightM: number;
  readonly rotationDeg: number;
  /** Verified roof support height for a rooftop placement; null/absent for a ground site. */
  readonly supportHeightM?: number | null;
}

/** Structural equivalent of the editable CityAirspace contract. */
export interface AirspacePolygon {
  readonly id: string;
  readonly name: string;
  readonly polygon: PointXZ[];
  readonly floorM: number;
  readonly ceilingM: number;
  readonly startsAtS: number;
  readonly endsAtS: number | null;
  readonly source: { readonly kind: "manual" | "geojson"; readonly label: string; readonly uri?: string };
}

export type PlacementIssueCode = "invalid_facility" | "building_overlap" | "static_overlap" | "road_overlap"
  | "facility_overlap" | "airspace_overlap";
export interface PlacementIssue {
  readonly code: PlacementIssueCode;
  readonly message: string;
  readonly obstacleId?: string;
}

export type AirspaceIssueCode = "invalid_bounds" | "invalid_polygon"
  | "self_intersection" | "degenerate_polygon";
export interface AirspaceIssue { readonly code: AirspaceIssueCode; readonly message: string; }

export interface GeoJSONBounds {
  readonly floorM: number;
  readonly ceilingM: number;
  readonly startsAtS?: number;
  readonly endsAtS?: number | null;
}

export type FlightPathIssueCode = "building_collision" | "static_collision" | "facility_collision" | "airspace_incursion";
export interface FlightPathIssue {
  readonly code: FlightPathIssueCode;
  readonly message: string;
  readonly obstacleId: string;
}

/** A measured clear vertical channel above one facility's landing surface. */
export interface LandingPadClearance {
  readonly facilityId: string;
  readonly x: number;
  readonly z: number;
  readonly widthM: number;
  readonly depthM: number;
  readonly rotationDeg: number;
  readonly padY: number;
}

const EARTH_CIRCUMFERENCE = 40075016.686;
const RAD = Math.PI / 180;
const GEOMETRY_EPSILON = 1e-9;

function validOrigin(origin: GeographicOrigin): void {
  if (!Number.isFinite(origin.latitude_deg) || Math.abs(origin.latitude_deg) >= 90
      || !Number.isFinite(origin.longitude_deg) || Math.abs(origin.longitude_deg) > 180) {
    throw new Error("MetricMapProjection origin is outside WGS84 bounds");
  }
}

/** Uses the exact forward formula of OSM2World's MetricMapProjection. */
export function wgs84ToLocal(latitudeDeg: number, longitudeDeg: number,
                             origin: GeographicOrigin): PointXZ {
  validOrigin(origin);
  if (!Number.isFinite(latitudeDeg) || Math.abs(latitudeDeg) >= 90
      || !Number.isFinite(longitudeDeg) || Math.abs(longitudeDeg) > 180) {
    throw new Error("WGS84 coordinate is outside the supported latitude or longitude bounds");
  }
  const projected = projectGeographic(latitudeDeg, longitudeDeg, origin);
  return { x: projected.east, z: -projected.north };
}

/** Inverse of MetricMapProjection, including the north-to-south Z sign. */
export function localToWgs84(point: PointXZ, origin: GeographicOrigin): GeographicOrigin {
  validOrigin(origin);
  if (!Number.isFinite(point.x) || !Number.isFinite(point.z)) {
    throw new Error("Local XZ coordinate must be finite");
  }
  const scale = EARTH_CIRCUMFERENCE * Math.cos(origin.latitude_deg * RAD);
  const originSine = Math.sin(origin.latitude_deg * RAD);
  const originMercatorY = Math.log((1 + originSine) / (1 - originSine)) / (4 * Math.PI) + 0.5;
  const mercatorY = originMercatorY - point.z / scale;
  const latitude_deg = Math.atan(Math.sinh((mercatorY - 0.5) * 2 * Math.PI)) / RAD;
  const longitude_deg = origin.longitude_deg + point.x * 360 / scale;
  if (!Number.isFinite(latitude_deg) || Math.abs(latitude_deg) >= 90
      || !Number.isFinite(longitude_deg) || Math.abs(longitude_deg) > 180) {
    throw new Error("Local XZ coordinate projects outside WGS84 bounds");
  }
  return { latitude_deg, longitude_deg };
}

function assertBox(box: PlacementBox): void {
  if (typeof box.id !== "string" || box.id.length === 0
      || ![box.x, box.z, box.widthM, box.depthM, box.heightM, box.rotationDeg,
        box.baseY ?? 0].every(Number.isFinite)
      || box.widthM <= 0 || box.depthM <= 0 || box.heightM <= 0) {
    throw new Error(`Invalid placement box: ${String(box.id)}`);
  }
}

export function normalizeBuildingPlacements(
  placements: readonly BuildingPlacementSource[],
): PlacementBox[] {
  return placements.map(placement => {
    if (typeof placement.building_id !== "string" || !placement.building_id
        || !Number.isSafeInteger(placement.part) || placement.part < 0) {
      throw new Error("City building placement has an invalid identity");
    }
    const result: PlacementBox = {
      id: `${placement.building_id}:${placement.part}`,
      x: placement.x, z: placement.z,
      widthM: placement.width, depthM: placement.depth, heightM: placement.height,
      rotationDeg: placement.rotation_deg, baseY: placement.base_y,
    };
    assertBox(result);
    return result;
  });
}

export function normalizeRoadbed(roadbed: readonly RoadbedSource[]): RoadPolygon[] {
  return roadbed.map((source, index) => {
    const ring = (coordinates: readonly (readonly [number, number])[]): PointXZ[] => {
      if (!Array.isArray(coordinates) || coordinates.length < 3) {
        throw new Error(`Roadbed ${index} has fewer than three points`);
      }
      return coordinates.map(coordinate => {
        if (!Array.isArray(coordinate) || coordinate.length !== 2
            || !coordinate.every(Number.isFinite)) {
          throw new Error(`Roadbed ${index} has an invalid XZ coordinate`);
        }
        return { x: coordinate[0], z: coordinate[1] };
      });
    };
    if (!Array.isArray(source.holes)) throw new Error(`Roadbed ${index} has invalid holes`);
    return { id: `roadbed:${index}`, outline: ring(source.outline), holes: source.holes.map(ring) };
  });
}

function boxCorners(box: PlacementBox): readonly [PointXZ, PointXZ, PointXZ, PointXZ] {
  const angle = box.rotationDeg * RAD;
  const cosine = Math.cos(angle), sine = Math.sin(angle);
  const halfWidth = box.widthM / 2, halfDepth = box.depthM / 2;
  const transform = (localX: number, localZ: number): PointXZ => ({
    x: box.x + cosine * localX + sine * localZ,
    z: box.z - sine * localX + cosine * localZ,
  });
  return [transform(-halfWidth, -halfDepth), transform(halfWidth, -halfDepth),
    transform(halfWidth, halfDepth), transform(-halfWidth, halfDepth)];
}

function boxAxes(box: PlacementBox): readonly [PointXZ, PointXZ] {
  const angle = box.rotationDeg * RAD;
  return [{ x: Math.cos(angle), z: -Math.sin(angle) },
    { x: Math.sin(angle), z: Math.cos(angle) }];
}

function boxesOverlap(left: PlacementBox, right: PlacementBox): boolean {
  const leftCorners = boxCorners(left), rightCorners = boxCorners(right);
  for (const axis of [...boxAxes(left), ...boxAxes(right)]) {
    const leftValues = leftCorners.map(point => point.x * axis.x + point.z * axis.z);
    const rightValues = rightCorners.map(point => point.x * axis.x + point.z * axis.z);
    if (Math.min(...leftValues) >= Math.max(...rightValues) - GEOMETRY_EPSILON
        || Math.min(...rightValues) >= Math.max(...leftValues) - GEOMETRY_EPSILON) return false;
  }
  return true;
}

/** Strict area intersection: boxes that only touch an edge or corner are separate. */
export function orientedBoxesIntersect(left: PlacementBox, right: PlacementBox): boolean {
  assertBox(left);
  assertBox(right);
  return boxesOverlap(left, right);
}

function cross(a: PointXZ, b: PointXZ, c: PointXZ): number {
  return (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x);
}

function onSegment(point: PointXZ, start: PointXZ, end: PointXZ): boolean {
  return Math.abs(cross(start, end, point)) <= GEOMETRY_EPSILON
    && point.x >= Math.min(start.x, end.x) - GEOMETRY_EPSILON
    && point.x <= Math.max(start.x, end.x) + GEOMETRY_EPSILON
    && point.z >= Math.min(start.z, end.z) - GEOMETRY_EPSILON
    && point.z <= Math.max(start.z, end.z) + GEOMETRY_EPSILON;
}

function segmentsMeet(a: PointXZ, b: PointXZ, c: PointXZ, d: PointXZ): boolean {
  const abC = cross(a, b, c), abD = cross(a, b, d);
  const cdA = cross(c, d, a), cdB = cross(c, d, b);
  if ((abC > GEOMETRY_EPSILON && abD < -GEOMETRY_EPSILON
       || abC < -GEOMETRY_EPSILON && abD > GEOMETRY_EPSILON)
      && (cdA > GEOMETRY_EPSILON && cdB < -GEOMETRY_EPSILON
          || cdA < -GEOMETRY_EPSILON && cdB > GEOMETRY_EPSILON)) return true;
  return onSegment(c, a, b) || onSegment(d, a, b)
    || onSegment(a, c, d) || onSegment(b, c, d);
}

function segmentsCrossProperly(a: PointXZ, b: PointXZ, c: PointXZ, d: PointXZ): boolean {
  const first = cross(a, b, c), second = cross(a, b, d);
  const third = cross(c, d, a), fourth = cross(c, d, b);
  return (first > GEOMETRY_EPSILON && second < -GEOMETRY_EPSILON
      || first < -GEOMETRY_EPSILON && second > GEOMETRY_EPSILON)
    && (third > GEOMETRY_EPSILON && fourth < -GEOMETRY_EPSILON
      || third < -GEOMETRY_EPSILON && fourth > GEOMETRY_EPSILON);
}

function samePoint(a: PointXZ, b: PointXZ): boolean { return a.x === b.x && a.z === b.z; }

function openRing(points: readonly PointXZ[]): readonly PointXZ[] {
  return points.length > 1 && samePoint(points[0]!, points[points.length - 1]!)
    ? points.slice(0, -1) : points;
}

function signedArea(points: readonly PointXZ[]): number {
  let doubleArea = 0;
  for (let index = 0; index < points.length; index++) {
    const current = points[index]!, next = points[(index + 1) % points.length]!;
    doubleArea += current.x * next.z - next.x * current.z;
  }
  return doubleArea / 2;
}

export function validateAirspacePolygon(
  region: Pick<AirspacePolygon, "polygon" | "floorM" | "ceilingM">,
): AirspaceIssue[] {
  const issues: AirspaceIssue[] = [];
  if (!Number.isFinite(region.floorM) || region.floorM < 0
      || !Number.isFinite(region.ceilingM) || region.ceilingM <= region.floorM) {
    issues.push({ code: "invalid_bounds", message: "Airspace floor and ceiling must define a positive altitude interval" });
  }
  if (!Array.isArray(region.polygon)) {
    issues.push({ code: "invalid_polygon", message: "Airspace polygon must be a point array" });
    return issues;
  }
  const points = openRing(region.polygon);
  if (points.length < 3 || points.some(point => point === null || typeof point !== "object"
      || !Number.isFinite(point.x) || !Number.isFinite(point.z))
      || new Set(points.map(point => `${point.x},${point.z}`)).size < 3
      || points.some((point, index) => samePoint(point, points[(index + 1) % points.length]!))) {
    issues.push({ code: "invalid_polygon", message: "Airspace polygon needs three distinct, finite vertices and no repeated edges" });
    return issues;
  }
  if (Math.abs(signedArea(points)) <= 1e-8) {
    issues.push({ code: "degenerate_polygon", message: "Airspace polygon has zero area" });
  }
  for (let first = 0; first < points.length; first++) {
    for (let second = first + 1; second < points.length; second++) {
      if (second === first + 1 || (first === 0 && second === points.length - 1)) continue;
      if (segmentsMeet(points[first]!, points[(first + 1) % points.length]!,
                       points[second]!, points[(second + 1) % points.length]!)) {
        issues.push({ code: "self_intersection", message: "Airspace polygon edges intersect or touch" });
        return issues;
      }
    }
  }
  return issues;
}

function pointInRing(point: PointXZ, ringSource: readonly PointXZ[]): -1 | 0 | 1 {
  const ring = openRing(ringSource);
  let inside = false;
  for (let index = 0; index < ring.length; index++) {
    const start = ring[index]!, end = ring[(index + 1) % ring.length]!;
    if (onSegment(point, start, end)) return -1;
    if ((start.z > point.z) !== (end.z > point.z)
        && point.x < (end.x - start.x) * (point.z - start.z) / (end.z - start.z) + start.x) {
      inside = !inside;
    }
  }
  return inside ? 1 : 0;
}

function pointInFilledPolygon(point: PointXZ, polygon: RoadPolygon): boolean {
  return pointInRing(point, polygon.outline) === 1
    && polygon.holes.every(hole => pointInRing(point, hole) === 0);
}

function pointInBox(point: PointXZ, box: PlacementBox): boolean {
  const [widthAxis, depthAxis] = boxAxes(box);
  const dx = point.x - box.x, dz = point.z - box.z;
  return Math.abs(dx * widthAxis.x + dz * widthAxis.z) < box.widthM / 2 - GEOMETRY_EPSILON
    && Math.abs(dx * depthAxis.x + dz * depthAxis.z) < box.depthM / 2 - GEOMETRY_EPSILON;
}

function boundsOverlap(left: readonly PointXZ[], right: readonly PointXZ[]): boolean {
  const bounds = (points: readonly PointXZ[]) => {
    let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
    for (const point of points) {
      minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x);
      minZ = Math.min(minZ, point.z); maxZ = Math.max(maxZ, point.z);
    }
    return { minX, maxX, minZ, maxZ };
  };
  const a = bounds(left), b = bounds(right);
  return a.minX < b.maxX - GEOMETRY_EPSILON && b.minX < a.maxX - GEOMETRY_EPSILON
    && a.minZ < b.maxZ - GEOMETRY_EPSILON && b.minZ < a.maxZ - GEOMETRY_EPSILON;
}

function polygonIntersectsBox(polygon: RoadPolygon, box: PlacementBox): boolean {
  const outline = openRing(polygon.outline);
  if (outline.length < 3) throw new Error("Collision polygon has fewer than three points");
  const corners = boxCorners(box);
  if (!boundsOverlap(outline, corners)) return false;
  const samples = [box, ...corners,
    ...corners.map((point, index) => ({
      x: (point.x + corners[(index + 1) % corners.length]!.x) / 2,
      z: (point.z + corners[(index + 1) % corners.length]!.z) / 2,
    }))];
  if (samples.some(point => pointInFilledPolygon(point, polygon))) return true;
  if (outline.some(point => pointInBox(point, box))) return true;
  for (let index = 1; index < outline.length - 1; index++) {
    const triangleCentroid = {
      x: (outline[0]!.x + outline[index]!.x + outline[index + 1]!.x) / 3,
      z: (outline[0]!.z + outline[index]!.z + outline[index + 1]!.z) / 3,
    };
    if (pointInBox(triangleCentroid, box) && pointInFilledPolygon(triangleCentroid, polygon)) return true;
  }
  for (const ringSource of [polygon.outline, ...polygon.holes]) {
    const ring = openRing(ringSource);
    for (let index = 0; index < ring.length; index++) {
      const start = ring[index]!, end = ring[(index + 1) % ring.length]!;
      for (let edge = 0; edge < corners.length; edge++) {
        if (segmentsCrossProperly(start, end, corners[edge]!, corners[(edge + 1) % corners.length]!)) {
          return true;
        }
      }
    }
  }
  return false;
}

function facilityBox(facility: FacilityPlacement): PlacementBox {
  return {
    id: facility.id, x: facility.position.x, z: facility.position.z,
    widthM: facility.widthM, depthM: facility.depthM, heightM: facility.heightM,
    rotationDeg: facility.rotationDeg, baseY: facility.supportHeightM ?? 0,
  };
}

function intervalsOverlap(aLow: number, aHigh: number, bLow: number, bHigh: number): boolean {
  return aLow < bHigh - GEOMETRY_EPSILON && bLow < aHigh - GEOMETRY_EPSILON;
}

export function validateFacilityPlacement(
  spec: FacilityPlacement,
  obstacles: readonly PlacementBox[],
  roadPolygons: readonly RoadPolygon[],
  otherFacilities: readonly FacilityPlacement[],
  airspace: readonly AirspacePolygon[],
): PlacementIssue[] {
  if (spec === null || typeof spec !== "object" || spec.position === null
      || typeof spec.position !== "object") {
    return [{ code: "invalid_facility", message: "Facility has no local XZ position" }];
  }
  const box = facilityBox(spec);
  try { assertBox(box); }
  catch {
    return [{ code: "invalid_facility", message: "Facility needs a valid ID, finite position, rotation and positive dimensions" }];
  }
  const issues: PlacementIssue[] = [];
  for (const obstacle of obstacles) {
    assertBox(obstacle);
    if (intervalsOverlap(box.baseY ?? 0, (box.baseY ?? 0) + box.heightM,
                         obstacle.baseY ?? 0, (obstacle.baseY ?? 0) + obstacle.heightM)
        && boxesOverlap(box, obstacle)) {
      issues.push({ code: obstacle.kind === "street_asset" ? "static_overlap" : "building_overlap",
        message: `Facility overlaps ${obstacle.kind === "street_asset" ? "street asset" : "building"} ${obstacle.id}`,
        obstacleId: obstacle.id });
    }
  }
  for (const road of roadPolygons) {
    if (polygonIntersectsBox(road, box)) {
      issues.push({ code: "road_overlap", message: `Facility occupies motor road ${road.id ?? "surface"}`,
        obstacleId: road.id });
    }
  }
  for (const other of otherFacilities) {
    if (other.id === spec.id) continue;
    const otherBox = facilityBox(other);
    assertBox(otherBox);
    if (boxesOverlap(box, otherBox)) {
      issues.push({ code: "facility_overlap", message: `Facility overlaps ${other.id}`,
        obstacleId: other.id });
    }
  }
  if (spec.kind === "vertiport") {
    for (const region of airspace) {
      const airspaceIssues = validateAirspacePolygon(region);
      if (airspaceIssues.length) throw new Error(`Invalid airspace ${region.id}: ${airspaceIssues[0]!.message}`);
      if (intervalsOverlap(box.baseY ?? 0, (box.baseY ?? 0) + box.heightM,
                           region.floorM, region.ceilingM)
          && polygonIntersectsBox({ outline: region.polygon, holes: [] }, box)) {
        issues.push({ code: "airspace_overlap", message: `Vertiport overlaps no-fly area ${region.name}`,
          obstacleId: region.id });
      }
    }
  }
  return issues;
}

function pointInsideBox3(point: PointXYZ, box: PlacementBox): boolean {
  const [widthAxis, depthAxis] = boxAxes(box);
  const dx = point.x - box.x, dz = point.z - box.z;
  return Math.abs(dx * widthAxis.x + dz * widthAxis.z) < box.widthM / 2 - GEOMETRY_EPSILON
    && Math.abs(dx * depthAxis.x + dz * depthAxis.z) < box.depthM / 2 - GEOMETRY_EPSILON
    && point.y > (box.baseY ?? 0) + GEOMETRY_EPSILON
    && point.y < (box.baseY ?? 0) + box.heightM - GEOMETRY_EPSILON;
}

function lerp3(from: PointXYZ, to: PointXYZ, fraction: number): PointXYZ {
  return {
    x: from.x + (to.x - from.x) * fraction,
    y: from.y + (to.y - from.y) * fraction,
    z: from.z + (to.z - from.z) * fraction,
  };
}

function segmentIntersectsBox3(from: PointXYZ, to: PointXYZ, box: PlacementBox): boolean {
  const [widthAxis, depthAxis] = boxAxes(box);
  const horizontal = (point: PointXYZ, axis: PointXZ) =>
    (point.x - box.x) * axis.x + (point.z - box.z) * axis.z;
  const intervals: readonly (readonly [number, number, number, number])[] = [
    [horizontal(from, widthAxis), horizontal(to, widthAxis), -box.widthM / 2, box.widthM / 2],
    [horizontal(from, depthAxis), horizontal(to, depthAxis), -box.depthM / 2, box.depthM / 2],
    [from.y, to.y, box.baseY ?? 0, (box.baseY ?? 0) + box.heightM],
  ];
  let entering = 0, leaving = 1;
  for (const [start, end, low, high] of intervals) {
    const change = end - start;
    if (Math.abs(change) <= GEOMETRY_EPSILON) {
      if (start < low || start > high) return false;
      continue;
    }
    const first = (low - start) / change, second = (high - start) / change;
    entering = Math.max(entering, Math.min(first, second));
    leaving = Math.min(leaving, Math.max(first, second));
    if (entering >= leaving) return false;
  }
  return pointInsideBox3(lerp3(from, to, (entering + leaving) / 2), box);
}

function pointInsidePadChannel(point: PointXYZ, pad: LandingPadClearance,
                               radius: number, halfHeight: number): boolean {
  const angle = pad.rotationDeg * RAD;
  const cosine = Math.cos(angle), sine = Math.sin(angle);
  const dx = point.x - pad.x, dz = point.z - pad.z;
  return Math.abs(dx * cosine - dz * sine) <= pad.widthM / 2 - radius + GEOMETRY_EPSILON
    && Math.abs(dx * sine + dz * cosine) <= pad.depthM / 2 - radius + GEOMETRY_EPSILON
    && point.y >= pad.padY + halfHeight - GEOMETRY_EPSILON;
}

function segmentIntersectsBoxOutsidePads(from: PointXYZ, to: PointXYZ, box: PlacementBox,
                                         pads: readonly LandingPadClearance[], radius: number,
                                         halfHeight: number): boolean {
  if (pads.length === 0) return segmentIntersectsBox3(from, to, box);
  const fractions = [0, 1];
  const addBoundary = (start: number, end: number, boundary: number): void => {
    if (start === end) return;
    const fraction = (boundary - start) / (end - start);
    if (fraction > 0 && fraction < 1) fractions.push(fraction);
  };
  const boxAxesLocal = boxAxes(box);
  for (const axis of boxAxesLocal) {
    const start = (from.x - box.x) * axis.x + (from.z - box.z) * axis.z;
    const end = (to.x - box.x) * axis.x + (to.z - box.z) * axis.z;
    const halfExtent = axis === boxAxesLocal[0] ? box.widthM / 2 : box.depthM / 2;
    addBoundary(start, end, -halfExtent);
    addBoundary(start, end, halfExtent);
  }
  addBoundary(from.y, to.y, box.baseY ?? 0);
  addBoundary(from.y, to.y, (box.baseY ?? 0) + box.heightM);
  for (const pad of pads) {
    const angle = pad.rotationDeg * RAD;
    const axes: readonly [PointXZ, PointXZ] = [
      { x: Math.cos(angle), z: -Math.sin(angle) },
      { x: Math.sin(angle), z: Math.cos(angle) },
    ];
    for (let index = 0; index < axes.length; index++) {
      const axis = axes[index]!;
      const start = (from.x - pad.x) * axis.x + (from.z - pad.z) * axis.z;
      const end = (to.x - pad.x) * axis.x + (to.z - pad.z) * axis.z;
      const halfExtent = (index === 0 ? pad.widthM : pad.depthM) / 2 - radius;
      if (halfExtent < 0) continue;
      addBoundary(start, end, -halfExtent);
      addBoundary(start, end, halfExtent);
    }
    addBoundary(from.y, to.y, pad.padY + halfHeight);
  }
  fractions.sort((left, right) => left - right);
  for (let index = 1; index < fractions.length; index++) {
    const first = fractions[index - 1]!, second = fractions[index]!;
    if (second - first <= GEOMETRY_EPSILON) continue;
    const point = lerp3(from, to, (first + second) / 2);
    if (pointInsideBox3(point, box)
        && !pads.some(pad => pointInsidePadChannel(point, pad, radius, halfHeight))) return true;
  }
  return false;
}

function edgeCrossingFraction(from: PointXZ, to: PointXZ,
                              start: PointXZ, end: PointXZ): number | null {
  const ray = { x: to.x - from.x, z: to.z - from.z };
  const edge = { x: end.x - start.x, z: end.z - start.z };
  const denominator = ray.x * edge.z - ray.z * edge.x;
  if (Math.abs(denominator) <= GEOMETRY_EPSILON) return null;
  const offset = { x: start.x - from.x, z: start.z - from.z };
  const fraction = (offset.x * edge.z - offset.z * edge.x) / denominator;
  const edgeFraction = (offset.x * ray.z - offset.z * ray.x) / denominator;
  return fraction >= 0 && fraction <= 1 && edgeFraction >= 0 && edgeFraction <= 1
    ? fraction : null;
}

function distancePointToSegment(point: PointXZ, start: PointXZ, end: PointXZ): number {
  const dx = end.x - start.x, dz = end.z - start.z;
  const lengthSquared = dx * dx + dz * dz;
  const fraction = lengthSquared === 0 ? 0
    : Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.z - start.z) * dz) / lengthSquared));
  return Math.hypot(point.x - (start.x + fraction * dx), point.z - (start.z + fraction * dz));
}

function segmentIntersectsBufferedPolygon(from: PointXZ, to: PointXZ,
                                          polygon: readonly PointXZ[], radius: number): boolean {
  const fractions = [0, 1];
  for (let index = 0; index < polygon.length; index++) {
    const start = polygon[index]!, end = polygon[(index + 1) % polygon.length]!;
    const crossing = edgeCrossingFraction(from, to, start, end);
    if (crossing !== null) fractions.push(crossing);
    if (radius > 0) {
      const distance = segmentsMeet(from, to, start, end) ? 0 : Math.min(
        distancePointToSegment(from, start, end), distancePointToSegment(to, start, end),
        distancePointToSegment(start, from, to), distancePointToSegment(end, from, to),
      );
      if (distance < radius - GEOMETRY_EPSILON) return true;
    }
  }
  fractions.sort((left, right) => left - right);
  for (let index = 1; index < fractions.length; index++) {
    const first = fractions[index - 1]!, second = fractions[index]!;
    if (second - first <= GEOMETRY_EPSILON) continue;
    const fraction = (first + second) / 2;
    const point = { x: from.x + (to.x - from.x) * fraction,
      z: from.z + (to.z - from.z) * fraction };
    if (pointInRing(point, polygon) === 1) return true;
  }
  return false;
}

function segmentIntersectsAirspace(from: PointXYZ, to: PointXYZ, startsAtS: number,
                                   endsAtS: number, region: AirspacePolygon,
                                   horizontalRadius: number, halfHeight: number): boolean {
  const fractions = [0, 1];
  const addBoundary = (start: number, end: number, boundary: number): void => {
    if (start !== end && Number.isFinite(boundary)) {
      const fraction = (boundary - start) / (end - start);
      if (fraction > 0 && fraction < 1) fractions.push(fraction);
    }
  };
  addBoundary(from.y, to.y, region.floorM - halfHeight);
  addBoundary(from.y, to.y, region.ceilingM + halfHeight);
  addBoundary(startsAtS, endsAtS, region.startsAtS);
  if (region.endsAtS !== null) addBoundary(startsAtS, endsAtS, region.endsAtS);
  const polygon = openRing(region.polygon);
  fractions.sort((left, right) => left - right);
  for (let index = 1; index < fractions.length; index++) {
    const first = fractions[index - 1]!, second = fractions[index]!;
    if (second - first <= GEOMETRY_EPSILON) continue;
    const fraction = (first + second) / 2;
    const point = lerp3(from, to, fraction);
    const time = startsAtS + (endsAtS - startsAtS) * fraction;
    if (point.y > region.floorM - halfHeight + GEOMETRY_EPSILON
        && point.y < region.ceilingM + halfHeight - GEOMETRY_EPSILON
        && time >= region.startsAtS && (region.endsAtS === null || time < region.endsAtS)
        && segmentIntersectsBufferedPolygon(lerp3(from, to, first),
          lerp3(from, to, second), polygon, horizontalRadius)) return true;
  }
  return false;
}

/** Checks the whole interpolated 3D segment, including airspace activation time. */
export function validateFlightSegment(
  from: PointXYZ,
  to: PointXYZ,
  startsAtS: number,
  endsAtS: number,
  obstacles: readonly PlacementBox[],
  facilities: readonly FacilityPlacement[],
  airspace: readonly AirspacePolygon[],
  bodySizeM: { readonly x: number; readonly y: number; readonly z: number },
  landingPads: readonly LandingPadClearance[] = [],
): FlightPathIssue[] {
  if (![from.x, from.y, from.z, to.x, to.y, to.z, startsAtS, endsAtS].every(Number.isFinite)
      || startsAtS < 0 || endsAtS <= startsAtS) {
    throw new Error("Flight segment needs finite coordinates and increasing nonnegative times");
  }
  if (![bodySizeM.x, bodySizeM.y, bodySizeM.z].every(Number.isFinite)
      || bodySizeM.x <= 0 || bodySizeM.y <= 0 || bodySizeM.z <= 0) {
    throw new Error("Flight body size must have positive finite dimensions");
  }
  // The preview does not prescribe yaw, so its horizontal half diagonal is the safe sweep radius.
  const radius = Math.hypot(bodySizeM.x, bodySizeM.z) / 2;
  const halfHeight = bodySizeM.y / 2;
  const inflated = (box: PlacementBox): PlacementBox => ({
    ...box, widthM: box.widthM + 2 * radius, depthM: box.depthM + 2 * radius,
    baseY: (box.baseY ?? 0) - halfHeight, heightM: box.heightM + 2 * halfHeight,
  });
  for (const pad of landingPads) {
    if (!pad.facilityId || ![pad.x, pad.z, pad.widthM, pad.depthM, pad.rotationDeg,
      pad.padY].every(Number.isFinite) || pad.widthM <= 0 || pad.depthM <= 0) {
      throw new Error("Landing pad clearance has invalid geometry");
    }
  }
  const issues: FlightPathIssue[] = [];
  for (const obstacle of obstacles) {
    assertBox(obstacle);
    if (segmentIntersectsBox3(from, to, inflated(obstacle))) {
      issues.push({ code: obstacle.kind === "street_asset" ? "static_collision" : "building_collision",
        message: `Flight segment crosses ${obstacle.kind === "street_asset" ? "street asset" : "building"} ${obstacle.id}`,
        obstacleId: obstacle.id });
    }
  }
  for (const facility of facilities) {
    const box = facilityBox(facility);
    assertBox(box);
    const pads = landingPads.filter(pad => pad.facilityId === facility.id);
    if (segmentIntersectsBoxOutsidePads(from, to, inflated(box), pads, radius, halfHeight)) {
      issues.push({ code: "facility_collision", message: `Flight segment crosses facility ${facility.id}`,
        obstacleId: facility.id });
    }
  }
  for (const region of airspace) {
    const airspaceIssues = validateAirspacePolygon(region);
    if (airspaceIssues.length) throw new Error(`Invalid airspace ${region.id}: ${airspaceIssues[0]!.message}`);
    if (segmentIntersectsAirspace(from, to, startsAtS, endsAtS, region, radius, halfHeight)) {
      issues.push({ code: "airspace_incursion", message: `Flight segment crosses no-fly area ${region.name}`,
        obstacleId: region.id });
    }
  }
  return issues;
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  const result = value as Record<string, unknown>;
  if ("crs" in result) throw new Error(`${label} declares a CRS; import requires RFC 7946 WGS84 longitude/latitude`);
  return result;
}

function numericProperty(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${label} must be a finite number`);
  return value;
}

function propertyOrDefault(properties: Record<string, unknown>, key: string, fallback: unknown): unknown {
  return Object.hasOwn(properties, key) ? properties[key] : fallback;
}

function hashString(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index++) {
    hash = Math.imul(hash ^ value.charCodeAt(index), 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function importAirspaceGeoJSON(
  value: unknown,
  origin: GeographicOrigin,
  sourceLabel: string,
  bounds?: GeoJSONBounds,
): AirspacePolygon[] {
  validOrigin(origin);
  if (typeof sourceLabel !== "string" || !sourceLabel.trim()) {
    throw new Error("GeoJSON import needs a source label");
  }
  const source = sourceLabel.trim();
  const result: AirspacePolygon[] = [];

  const parsePolygon = (coordinates: unknown, properties: Record<string, unknown>,
                        featureIndex: number, polygonIndex: number): void => {
    if (!Array.isArray(coordinates) || coordinates.length !== 1) {
      throw new Error("GeoJSON polygon holes are not supported; remove or split holes explicitly");
    }
    const exterior = coordinates[0];
    if (!Array.isArray(exterior) || exterior.length < 4) {
      throw new Error("GeoJSON polygon exterior needs a closed ring of at least four coordinates");
    }
    const longitudeLatitude = exterior.map((raw, index): readonly [number, number] => {
      if (!Array.isArray(raw) || raw.length !== 2
          || typeof raw[0] !== "number" || typeof raw[1] !== "number"
          || !Number.isFinite(raw[0]) || !Number.isFinite(raw[1])
          || Math.abs(raw[0]) > 180 || Math.abs(raw[1]) >= 90) {
        throw new Error(`GeoJSON coordinate ${index} must be WGS84 [longitude, latitude]`);
      }
      return [raw[0], raw[1]];
    });
    const first = longitudeLatitude[0]!, last = longitudeLatitude[longitudeLatitude.length - 1]!;
    if (first[0] !== last[0] || first[1] !== last[1]) {
      throw new Error("GeoJSON polygon ring must close with its first longitude/latitude coordinate");
    }
    for (let index = 1; index < longitudeLatitude.length; index++) {
      if (Math.abs(longitudeLatitude[index]![0] - longitudeLatitude[index - 1]![0]) > 180) {
        throw new Error("GeoJSON polygon crosses the antimeridian; split it before local projection");
      }
    }
    const polygon = longitudeLatitude.slice(0, -1)
      .map(([longitude, latitude]) => wgs84ToLocal(latitude, longitude, origin));
    const floorM = numericProperty(propertyOrDefault(properties, "floorM", bounds?.floorM), "GeoJSON floorM");
    const ceilingM = numericProperty(propertyOrDefault(properties, "ceilingM", bounds?.ceilingM), "GeoJSON ceilingM");
    const startsAtS = numericProperty(propertyOrDefault(properties, "startsAtS", bounds?.startsAtS ?? 0),
      "GeoJSON startsAtS");
    const endsAtS = propertyOrDefault(properties, "endsAtS", bounds?.endsAtS ?? null);
    if (endsAtS !== null) numericProperty(endsAtS, "GeoJSON endsAtS");
    if (startsAtS < 0 || (endsAtS !== null && (endsAtS as number) <= startsAtS)) {
      throw new Error("GeoJSON schedule must start at or after zero and end after its start");
    }
    const issues = validateAirspacePolygon({ polygon, floorM, ceilingM });
    if (issues.length) throw new Error(`Invalid GeoJSON airspace polygon: ${issues.map(issue => issue.message).join("; ")}`);
    const sequence = result.length + 1;
    const rawName = properties.name;
    if (rawName !== undefined && (typeof rawName !== "string" || !rawName.trim())) {
      throw new Error("GeoJSON feature name must be a nonempty string");
    }
    const name = typeof rawName === "string"
      ? (polygonIndex === 0 ? rawName.trim() : `${rawName.trim()} ${polygonIndex + 1}`)
      : `${source} ${sequence}`;
    result.push({
      id: `geojson-${hashString(`${source}:${featureIndex}:${polygonIndex}:${JSON.stringify(longitudeLatitude)}`)}-${sequence}`,
      name, polygon, floorM, ceilingM, startsAtS, endsAtS: endsAtS as number | null,
      source: { kind: "geojson", label: source },
    });
  };

  const parseGeometry = (raw: unknown, properties: Record<string, unknown>, featureIndex: number): void => {
    const geometry = record(raw, "GeoJSON geometry");
    if (geometry.type === "Polygon") parsePolygon(geometry.coordinates, properties, featureIndex, 0);
    else if (geometry.type === "MultiPolygon") {
      if (!Array.isArray(geometry.coordinates) || geometry.coordinates.length === 0) {
        throw new Error("GeoJSON MultiPolygon has no polygons");
      }
      geometry.coordinates.forEach((coordinates, index) =>
        parsePolygon(coordinates, properties, featureIndex, index));
    } else throw new Error(`Unsupported GeoJSON geometry: ${String(geometry.type)}`);
  };

  const root = record(value, "GeoJSON");
  if (root.type === "FeatureCollection") {
    if (!Array.isArray(root.features) || root.features.length === 0) {
      throw new Error("GeoJSON FeatureCollection has no features");
    }
    root.features.forEach((rawFeature, index) => {
      const feature = record(rawFeature, `GeoJSON feature ${index}`);
      if (feature.type !== "Feature") throw new Error(`GeoJSON feature ${index} is not a Feature`);
      const properties = feature.properties === null ? {} : record(feature.properties, "GeoJSON properties");
      parseGeometry(feature.geometry, properties, index);
    });
  } else if (root.type === "Feature") {
    const properties = root.properties === null ? {} : record(root.properties, "GeoJSON properties");
    parseGeometry(root.geometry, properties, 0);
  } else parseGeometry(root, {}, 0);
  return result;
}
