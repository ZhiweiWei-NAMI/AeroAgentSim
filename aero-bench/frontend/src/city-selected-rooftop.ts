/** Measured rooftop support and rooftop volume proof for one selected city.
 *
 * A rooftop site is never inferred from the building envelope: the envelope top is
 * the highest point of the whole source mesh (possibly a spire, sloped roof or
 * setback) and is not evidence of a flat upward-facing surface. This module reads
 * the actual hash-verified selected-scene building triangles (the same buffers
 * `loadPackedScene` turns into `SourceBuildingTriangleRange`s, world metres, X
 * east / Y up / Z south) and measures, per complete-footprint building, the
 * largest axis-aligned (building-local) rectangle fully contained inside ONE
 * flat upward-facing triangle. The measured plane height at the rectangle centre
 * is the support height; no roof topology is invented.
 *
 * The volume proof then walks the real roof/above-roof triangles of the support
 * building (plus any complete building whose axis-aligned bounds overlap the pad)
 * and rejects a 3D facility volume that intersects any triangle strictly above
 * the support plane — parapets, HVAC boxes, spires, gable peaks — while excluding
 * the coplanar support surface at the exact base within a tight epsilon.
 */
import type { SourceBuildingShape, SourceBuildingTriangleRange } from "./city-building-shape";
import type { PointXZ } from "./city-workspace-geometry";
import type { RoofSupport } from "./city-selected-placement";

/** A triangle in world local metres (X east, Y up, Z south). */
export interface RooftopTriangle {
  readonly ax: number; readonly ay: number; readonly az: number;
  readonly bx: number; readonly by: number; readonly bz: number;
  readonly cx: number; readonly cy: number; readonly cz: number;
}

/** A flat roof must be genuinely horizontal to Float32 numeric precision. The
 * source buffers store world metres; the three vertex Y coordinates of a real
 * horizontal roof can differ only by float32 round-off (roughly one ULP band,
 * ~1e-4 m for city-scale coordinates). Any real slope, even 0.3° (≈1.6 mm drop
 * per 0.3 m), exceeds that band and is rejected, so a rendered pad at one
 * measured plane height can never ride a sloped surface. */
export const ROOF_FLAT_Y_SPAN_EPSILON_M = 1e-4;

/** A flat roof plane may be split into many coplanar triangles; support requires
 * ONE triangle whose interior contains the whole footprint. */
const MIN_SUPPORT_SPAN_M = 0.5;
const RECT_SEARCH_STEP_M = 0.5;
const GEOMETRIC_EPSILON = 1e-6;
/** Axis snapping tolerance for recovered building-local coordinates. Real source
 * buffers are Float32 world metres: two vertices that are exactly on the same
 * horizontal line can differ by up to ~1e-4 m because of float32 round-off, which
 * would otherwise turn a flat box-roof edge into a nearly-sloping constraint and
 * wrongly reject an anchored rectangle. Values within 1 mm are fused to a common
 * mean. The fusion moves each fused vertex by at most 0.5 mm, still far below
 * any real roof pitch, and the returned support rectangle is inset by this same
 * constant and re-proven inside the ORIGINAL un-snapped triangle, so the snap
 * never expands support beyond the actual source mesh triangle. */
const ROOF_EDGE_AXIS_EPSILON_M = 1e-3;
/** Coplanar support surface tolerance: only triangles whose top is within 1 mm of
 * the measured plane are treated as part of the exact roof surface (i.e. the
 * float32 noise band around the coplanar support triangle itself). A genuine
 * 1 cm-thick obstacle above the plane is therefore rejected as a real obstruction. */
export const SUPPORT_PLANE_EPSILON_M = 1e-3;
/** Mesh-vs-envelope proof tolerance, mirroring the renderer's verifiedEnvelopeFor. */
const ENVELOPE_BOUNDS_EPSILON_M = 0.01;

function validateRange(range: SourceBuildingTriangleRange): void {
  if (!Number.isSafeInteger(range.firstTriangle) || !Number.isSafeInteger(range.endTriangle)
      || range.firstTriangle < 0 || range.endTriangle <= range.firstTriangle
      || range.endTriangle * 3 > range.indices.length
      || range.positions.length % 3 !== 0
      || range.normals.length !== range.positions.length) {
    throw new Error("Verified building triangle range is outside its source batch");
  }
}

/** Iterate every triangle of a verified building shape in world metres. */
export function forEachRooftopTriangle(shape: SourceBuildingShape,
                                       visit: (triangle: RooftopTriangle,
                                               range: SourceBuildingTriangleRange) => void): number {
  let count = 0;
  for (const range of shape.ranges) {
    validateRange(range);
    for (let triangle = range.firstTriangle; triangle < range.endTriangle; triangle++) {
      const indexOffset = triangle * 3;
      const corners: number[] = [];
      for (let corner = 0; corner < 3; corner++) {
        const vertex = range.indices[indexOffset + corner]!;
        if (vertex * 3 + 2 >= range.positions.length) {
          throw new Error("Verified building triangle references a missing source vertex");
        }
        const positionOffset = vertex * 3;
        if (![range.positions[positionOffset], range.positions[positionOffset + 1],
          range.positions[positionOffset + 2], range.normals[positionOffset],
          range.normals[positionOffset + 1], range.normals[positionOffset + 2]].every(Number.isFinite)) {
          throw new Error("Verified building triangle contains a non-finite vertex");
        }
        corners.push(positionOffset);
      }
      visit({
        ax: range.positions[corners[0]!]!, ay: range.positions[corners[0]! + 1]!,
        az: range.positions[corners[0]! + 2]!,
        bx: range.positions[corners[1]!]!, by: range.positions[corners[1]! + 1]!,
        bz: range.positions[corners[1]! + 2]!,
        cx: range.positions[corners[2]!]!, cy: range.positions[corners[2]! + 1]!,
        cz: range.positions[corners[2]! + 2]!,
      }, range);
      count++;
    }
  }
  return count;
}

/** Signed Y component of the geometric face normal, derived from the positions
 * themselves (never the loaded normals, so the verdict is self-consistent). */
function geometricNormalY(triangle: RooftopTriangle): number {
  const ux = triangle.bx - triangle.ax, uy = triangle.by - triangle.ay, uz = triangle.bz - triangle.az;
  const vx = triangle.cx - triangle.ax, vy = triangle.cy - triangle.ay, vz = triangle.cz - triangle.az;
  const nx = uy * vz - uz * vy;
  const ny = uz * vx - ux * vz;
  const nz = ux * vy - uy * vx;
  const length = Math.hypot(nx, ny, nz);
  return length === 0 ? 0 : ny / length;
}

/** Flat upward-facing (horizontal) source triangles: the only geometry that can
 * conservatively support a level pad without inventing a roof surface. A triangle
 * must be genuinely horizontal to Float32 precision (all three vertex Y values
 * within one float32 noise band) and wound so its geometric normal points up. */
export function flatUpwardRooftopTriangle(triangle: RooftopTriangle): boolean {
  const minY = Math.min(triangle.ay, triangle.by, triangle.cy);
  const maxY = Math.max(triangle.ay, triangle.by, triangle.cy);
  if (maxY - minY > ROOF_FLAT_Y_SPAN_EPSILON_M) return false;
  return geometricNormalY(triangle) > 0;
}

export function upwardFlatRooftopTriangles(shape: SourceBuildingShape): RooftopTriangle[] {
  const triangles: RooftopTriangle[] = [];
  forEachRooftopTriangle(shape, triangle => {
    if (flatUpwardRooftopTriangle(triangle)) triangles.push(triangle);
  });
  return triangles;
}

interface Edge { readonly a: number; readonly b: number; readonly c: number; }
interface LocalPoint { readonly x: number; readonly z: number; }

/** Height of the triangle's plane at a world XZ point (used for near-horizontal
 * flat triangles whose plane is measured at the support rectangle centre). */
function planeHeightAt(triangle: RooftopTriangle, x: number, z: number): number {
  const ux = triangle.bx - triangle.ax, uy = triangle.by - triangle.ay, uz = triangle.bz - triangle.az;
  const vx = triangle.cx - triangle.ax, vy = triangle.cy - triangle.ay, vz = triangle.cz - triangle.az;
  const nx = uy * vz - uz * vy;
  const ny = uz * vx - ux * vz;
  const nz = ux * vy - uy * vx;
  if (Math.abs(ny) < 1e-9) return (triangle.ay + triangle.by + triangle.cy) / 3;
  return triangle.ay - (nx * (x - triangle.ax) + nz * (z - triangle.az)) / ny;
}

/** World -> building-local, the exact inverse of THREE.Matrix4.makeRotationY(+θ)
 * (and of the renderer's `sourceBuildingGeometry` envelope recovery and the
 * module's own `footprintCorners`). With X east / Y up / Z south:
 *   localX = dx·cosθ − dz·sinθ, localZ = dx·sinθ + dz·cosθ. */
function toLocalPoint(worldX: number, worldZ: number, rotationDeg: number,
                      originX: number, originZ: number): LocalPoint {
  const radians = rotationDeg * Math.PI / 180;
  const cosine = Math.cos(radians), sine = Math.sin(radians);
  const dx = worldX - originX, dz = worldZ - originZ;
  return { x: dx * cosine - dz * sine, z: dx * sine + dz * cosine };
}

/** Building-local -> world, the forward THREE.Matrix4.makeRotationY(+θ):
 *   worldX = originX + localX·cosθ + localZ·sinθ,
 *   worldZ = originZ − localX·sinθ + localZ·cosθ. */
function fromLocalPoint(localX: number, localZ: number, rotationDeg: number,
                        originX: number, originZ: number): LocalPoint {
  const radians = rotationDeg * Math.PI / 180;
  const cosine = Math.cos(radians), sine = Math.sin(radians);
  return { x: originX + localX * cosine + localZ * sine, z: originZ - localX * sine + localZ * cosine };
}

/** Fuse the two closest of three coordinates when float32 round-off pulled two
 * vertices of one axis off the same line. Returns the coordinates in the order
 * the callers passed, with the closest pair replaced by their mean. */
function snapAxisTriple(a: number, b: number, c: number): readonly [number, number, number] {
  const values = [a, b, c];
  const pairs: readonly (readonly [number, number, number])[] = [
    [Math.abs(a - b), 0, 1], [Math.abs(a - c), 0, 2], [Math.abs(b - c), 1, 2],
  ];
  const [delta, first, second] = [...pairs].sort((x, y) => x[0] - y[0])[0]!;
  if (delta > ROOF_EDGE_AXIS_EPSILON_M) return [a, b, c];
  const mean = (values[first]! + values[second]!) / 2;
  const out = [...values];
  out[first] = mean;
  out[second] = mean;
  return [out[0]!, out[1]!, out[2]!];
}

/** Axes: building-local X is the envelope width axis, Z the depth axis. */
function triangleEdges(triangle: { readonly x: readonly [number, number, number];
                                   readonly z: readonly [number, number, number] }): Edge[] {
  let p0: LocalPoint = { x: triangle.x[0]!, z: triangle.z[0]! };
  let p1: LocalPoint = { x: triangle.x[1]!, z: triangle.z[1]! };
  let p2: LocalPoint = { x: triangle.x[2]!, z: triangle.z[2]! };
  const area2 = (p1.x - p0.x) * (p2.z - p0.z) - (p1.z - p0.z) * (p2.x - p0.x);
  if (area2 < 0) { const swap = p1; p1 = p2; p2 = swap; }
  const edges: readonly (readonly [LocalPoint, LocalPoint])[] = [[p0, p1], [p1, p2], [p2, p0]];
  return edges.map(([from, to]) => ({
    a: from.z - to.z,
    b: to.x - from.x,
    c: from.x * to.z - from.z * to.x,
  }));
}

interface RectangleSpan { readonly x0: number; readonly z0: number; readonly x1: number; readonly z1: number; readonly area: number; }

/** Largest feasible axis-aligned rectangle whose bottom-left corner is the anchor.
 * Every returned rectangle is provably inside the triangle: each edge contributes
 * a half-plane whose worst corner by coefficient sign bounds the far corner or the
 * anchor; the three candidates (full X span, full Z span, largest square) are each
 * feasible and the widest practical one is chosen. */
function maxRectangleAtAnchor(x0: number, z0: number, edges: readonly Edge[],
                              xSpanCap: number, zSpanCap: number): RectangleSpan | null {
  let xCap = xSpanCap;
  let zCap = zSpanCap;
  const couplings: { readonly wa: number; readonly wb: number; readonly cc: number }[] = [];
  for (const edge of edges) {
    const { a, b, c } = edge;
    if (a > 0 && b > 0) { if (a * x0 + b * z0 + c < -GEOMETRIC_EPSILON) return null; }
    else if (a > 0 && b < 0) { zCap = Math.min(zCap, (-a * x0 - c) / b); }
    else if (a > 0 && b === 0) { if (a * x0 + c < -GEOMETRIC_EPSILON) return null; }
    else if (a < 0 && b > 0) { xCap = Math.min(xCap, (-b * z0 - c) / a); }
    else if (a < 0 && b === 0) { xCap = Math.min(xCap, -c / a); }
    else if (a < 0 && b < 0) { couplings.push({ wa: -a, wb: -b, cc: c }); }
    else if (a === 0 && b > 0) { if (b * z0 + c < -GEOMETRIC_EPSILON) return null; }
    else if (a === 0 && b < 0) { zCap = Math.min(zCap, -c / b); }
    else if (c < -GEOMETRIC_EPSILON) return null;
  }
  const xSpan = Math.min(xSpanCap, xCap - x0);
  const zSpan = Math.min(zSpanCap, zCap - z0);
  if (xSpan < MIN_SUPPORT_SPAN_M || zSpan < MIN_SUPPORT_SPAN_M) return null;

  let zAtX = zSpan;
  for (const coupling of couplings) {
    const candidate = (coupling.cc - coupling.wa * x0 - coupling.wb * z0 - coupling.wa * xSpan) / coupling.wb;
    zAtX = Math.min(zAtX, candidate);
  }
  const areaX = zAtX >= MIN_SUPPORT_SPAN_M ? xSpan * zAtX : 0;

  let xAtZ = xSpan;
  for (const coupling of couplings) {
    const candidate = (coupling.cc - coupling.wa * x0 - coupling.wb * z0 - coupling.wb * zSpan) / coupling.wa;
    xAtZ = Math.min(xAtZ, candidate);
  }
  const areaZ = xAtZ >= MIN_SUPPORT_SPAN_M ? xAtZ * zSpan : 0;

  let side = Math.min(xSpan, zSpan);
  for (const coupling of couplings) {
    const candidate = (coupling.cc - coupling.wa * x0 - coupling.wb * z0) / (coupling.wa + coupling.wb);
    side = Math.min(side, candidate);
  }
  const areaS = side >= MIN_SUPPORT_SPAN_M ? side * side : 0;

  const area = Math.max(areaX, areaZ, areaS);
  if (area <= 0) return null;
  if (area === areaX) return { x0, z0, x1: x0 + xSpan, z1: z0 + zAtX, area };
  if (area === areaZ) return { x0, z0, x1: x0 + xAtZ, z1: z0 + zSpan, area };
  return { x0, z0, x1: x0 + side, z1: z0 + side, area };
}

/** Prove the snapped-frame rectangle lies inside the ORIGINAL un-snapped triangle.
 *
 * When the axis snap did not move any vertex (genuinely axis-aligned box-roof
 * edges), every rectangle corner already satisfies the original half-planes and
 * the rectangle is returned unchanged with its full measured area. When float32
 * jitter was fused, corners can sit up to ROOF_EDGE_AXIS_EPSILON_M outside the
 * original triangle; those corners are then moved inward by exactly that bound and
 * re-proven. Either way every returned corner passes containment in the ORIGINAL
 * triangle after all numerical approximations, so support never expands beyond the
 * actual source mesh triangle. */
function proveRectangleInsideOriginalTriangle(rect: RectangleSpan,
                                              original: readonly LocalPoint[]): RectangleSpan | null {
  const edges = triangleEdges({
    x: [original[0]!.x, original[1]!.x, original[2]!.x] as [number, number, number],
    z: [original[0]!.z, original[1]!.z, original[2]!.z] as [number, number, number],
  });
  const corners: readonly (readonly [number, number])[] = [
    [rect.x0, rect.z0], [rect.x1, rect.z0], [rect.x1, rect.z1], [rect.x0, rect.z1],
  ];
  const inside = (cx: number, cz: number): boolean =>
    edges.every(({ a, b, c }) => a * cx + b * cz + c >= -GEOMETRIC_EPSILON);
  if (corners.every(([cx, cz]) => inside(cx, cz))) return rect;
  const inset: RectangleSpan = {
    x0: rect.x0 + ROOF_EDGE_AXIS_EPSILON_M,
    z0: rect.z0 + ROOF_EDGE_AXIS_EPSILON_M,
    x1: rect.x1 - ROOF_EDGE_AXIS_EPSILON_M,
    z1: rect.z1 - ROOF_EDGE_AXIS_EPSILON_M,
    area: (rect.x1 - rect.x0 - 2 * ROOF_EDGE_AXIS_EPSILON_M) * (rect.z1 - rect.z0 - 2 * ROOF_EDGE_AXIS_EPSILON_M),
  };
  if (inset.x1 - inset.x0 < MIN_SUPPORT_SPAN_M || inset.z1 - inset.z0 < MIN_SUPPORT_SPAN_M) return null;
  const insetCorners: readonly (readonly [number, number])[] = [
    [inset.x0, inset.z0], [inset.x1, inset.z0], [inset.x1, inset.z1], [inset.x0, inset.z1],
  ];
  if (!insetCorners.every(([cx, cz]) => inside(cx, cz))) return null;
  return inset;
}

/** Largest axis-aligned rectangle fully contained inside ONE triangle, in the
 * building-local frame, from a deterministic lattice of bottom-left anchors. The
 * search classifies edges in a float32-snapped frame so genuinely axis-aligned
 * box-roof edges are recognized, then the result is re-proven inside the ORIGINAL
 * un-snapped triangle (see proveRectangleInsideOriginalTriangle). */
export function largestSupportRectangleInTriangle(
  triangle: RooftopTriangle, rotationDeg: number, originX: number, originZ: number,
): RectangleSpan | null {
  const locals = [0, 1, 2].map(index => {
    const world = index === 0
      ? { x: triangle.ax, z: triangle.az } : index === 1
      ? { x: triangle.bx, z: triangle.bz } : { x: triangle.cx, z: triangle.cz };
    return toLocalPoint(world.x, world.z, rotationDeg, originX, originZ);
  });
  const snappedX = snapAxisTriple(locals[0]!.x, locals[1]!.x, locals[2]!.x);
  const snappedZ = snapAxisTriple(locals[0]!.z, locals[1]!.z, locals[2]!.z);
  const triangleLocal = {
    x: [snappedX[0], snappedX[1], snappedX[2]] as [number, number, number],
    z: [snappedZ[0], snappedZ[1], snappedZ[2]] as [number, number, number],
  };
  const minX = Math.min(...triangleLocal.x), maxX = Math.max(...triangleLocal.x);
  const minZ = Math.min(...triangleLocal.z), maxZ = Math.max(...triangleLocal.z);
  const spanX = maxX - minX, spanZ = maxZ - minZ;
  if (spanX < MIN_SUPPORT_SPAN_M || spanZ < MIN_SUPPORT_SPAN_M) return null;
  const edges = triangleEdges(triangleLocal);
  const stepsX = Math.max(1, Math.ceil(spanX / RECT_SEARCH_STEP_M));
  const stepsZ = Math.max(1, Math.ceil(spanZ / RECT_SEARCH_STEP_M));
  let best: RectangleSpan | null = null;
  for (let stepX = 0; stepX <= stepsX; stepX++) {
    const x0 = minX + (stepX / stepsX) * spanX;
    for (let stepZ = 0; stepZ <= stepsZ; stepZ++) {
      const z0 = minZ + (stepZ / stepsZ) * spanZ;
      const candidate = maxRectangleAtAnchor(x0, z0, edges, spanX, spanZ);
      if (candidate !== null && (best === null || candidate.area > best.area)) best = candidate;
    }
  }
  if (best === null) return null;
  return proveRectangleInsideOriginalTriangle(best, locals);
}

/** Structural subset of the envelope needed to bind a measured roof to a building. */
export interface RooftopEnvelope {
  readonly building_id: string;
  readonly rotation_deg: number;
  readonly x: number;
  readonly z: number;
  readonly base_y: number;
  readonly height: number;
}

/** Measure the largest one-triangle rooftop support for a verified
 * complete-footprint building. Returns null when the source mesh does not bound
 * the envelope in Y (the mesh cannot be this building's roof) or when no single
 * flat upward triangle can contain a support rectangle. */
export function measureRoofSupport(buildingId: string, envelope: RooftopEnvelope,
                                   shape: SourceBuildingShape): RoofSupport | null {
  let minY = Infinity, maxY = -Infinity;
  const flats: RooftopTriangle[] = [];
  forEachRooftopTriangle(shape, triangle => {
    minY = Math.min(minY, triangle.ay, triangle.by, triangle.cy);
    maxY = Math.max(maxY, triangle.ay, triangle.by, triangle.cy);
    if (flatUpwardRooftopTriangle(triangle)) flats.push(triangle);
  });
  if (flats.length === 0 || minY === Infinity) return null;
  if (Math.abs(minY - envelope.base_y) > ENVELOPE_BOUNDS_EPSILON_M
      || Math.abs(maxY - (envelope.base_y + envelope.height)) > ENVELOPE_BOUNDS_EPSILON_M) {
    return null;
  }
  let best: { readonly rect: RectangleSpan; readonly topY: number } | null = null;
  for (const triangle of flats) {
    const rect = largestSupportRectangleInTriangle(triangle, envelope.rotation_deg, envelope.x, envelope.z);
    if (rect === null) continue;
    const centre = fromLocalPoint((rect.x0 + rect.x1) / 2, (rect.z0 + rect.z1) / 2,
      envelope.rotation_deg, envelope.x, envelope.z);
    const topY = planeHeightAt(triangle, centre.x, centre.z);
    if (best === null || rect.area > best.rect.area) best = { rect, topY };
  }
  if (best === null) return null;
  const centre = fromLocalPoint((best.rect.x0 + best.rect.x1) / 2, (best.rect.z0 + best.rect.z1) / 2,
    envelope.rotation_deg, envelope.x, envelope.z);
  return {
    buildingId, x: centre.x, z: centre.z,
    widthM: best.rect.x1 - best.rect.x0, depthM: best.rect.z1 - best.rect.z0,
    rotationDeg: envelope.rotation_deg, topY: best.topY,
  };
}

/** Measure every complete building that actually proves a flat roof support. */
export function measureRoofSupports(
  envelopes: readonly RooftopEnvelope[],
  mesh: ReadonlyMap<string, SourceBuildingTriangleRange[]>,
): RoofSupport[] {
  const supports: RoofSupport[] = [];
  for (const envelope of envelopes) {
    const ranges = mesh.get(envelope.building_id);
    if (ranges === undefined || ranges.length === 0) continue;
    const support = measureRoofSupport(envelope.building_id, envelope, { ranges });
    if (support !== null) supports.push(support);
  }
  return supports;
}

/** The chunk of a facility the volume proof validates, using the same footprint
 * convention (local X east, Z south; rotation is the facility's own rotation). */
export interface RooftopVolumeGeometry {
  readonly position: PointXZ;
  readonly rotationDeg: number;
  readonly widthM: number;
  readonly depthM: number;
  readonly heightM: number;
}

interface Aabb { readonly minX: number; readonly minY: number; readonly minZ: number;
                  readonly maxX: number; readonly maxY: number; readonly maxZ: number; }

function triangleAabb(triangle: RooftopTriangle): Aabb {
  return {
    minX: Math.min(triangle.ax, triangle.bx, triangle.cx),
    maxX: Math.max(triangle.ax, triangle.bx, triangle.cx),
    minY: Math.min(triangle.ay, triangle.by, triangle.cy),
    maxY: Math.max(triangle.ay, triangle.by, triangle.cy),
    minZ: Math.min(triangle.az, triangle.bz, triangle.cz),
    maxZ: Math.max(triangle.az, triangle.bz, triangle.cz),
  };
}

function rangeBounds(ranges: readonly SourceBuildingTriangleRange[]): Aabb {
  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (const range of ranges) {
    for (let triangle = range.firstTriangle; triangle < range.endTriangle; triangle++) {
      const indexOffset = triangle * 3;
      for (let corner = 0; corner < 3; corner++) {
        const vertex = range.indices[indexOffset + corner]!;
        const offset = vertex * 3;
        minX = Math.min(minX, range.positions[offset]!);
        maxX = Math.max(maxX, range.positions[offset]!);
        minY = Math.min(minY, range.positions[offset + 1]!);
        maxY = Math.max(maxY, range.positions[offset + 1]!);
        minZ = Math.min(minZ, range.positions[offset + 2]!);
        maxZ = Math.max(maxZ, range.positions[offset + 2]!);
      }
    }
  }
  return { minX: minX === Infinity ? NaN : minX, minY, minZ, maxX, maxY, maxZ };
}

function footprintCorners(geometry: RooftopVolumeGeometry): readonly (readonly [number, number])[] {
  const radians = geometry.rotationDeg * Math.PI / 180;
  const cosine = Math.cos(radians), sine = Math.sin(radians);
  const halfWidth = geometry.widthM / 2, halfDepth = geometry.depthM / 2;
  const corners: readonly (readonly [number, number])[] = [
    [-halfWidth, -halfDepth], [halfWidth, -halfDepth],
    [halfWidth, halfDepth], [-halfWidth, halfDepth],
  ];
  return corners.map(([lx, lz]) => [
    geometry.position.x + lx * cosine + lz * sine,
    geometry.position.z - lx * sine + lz * cosine,
  ] as const);
}

function sign(x1: number, z1: number, x2: number, z2: number, x3: number, z3: number): number {
  return (x1 - x3) * (z2 - z3) - (x2 - x3) * (z1 - z3);
}

function pointInTriangle(px: number, pz: number, a: readonly [number, number],
                         b: readonly [number, number], c: readonly [number, number]): boolean {
  const d1 = sign(px, pz, a[0], a[1], b[0], b[1]);
  const d2 = sign(px, pz, b[0], b[1], c[0], c[1]);
  const d3 = sign(px, pz, c[0], c[1], a[0], a[1]);
  const hasNegative = d1 < 0 || d2 < 0 || d3 < 0;
  const hasPositive = d1 > 0 || d2 > 0 || d3 > 0;
  return !(hasNegative && hasPositive);
}

function pointInRotatedRect(px: number, pz: number, geometry: RooftopVolumeGeometry): boolean {
  const radians = geometry.rotationDeg * Math.PI / 180;
  const cosine = Math.cos(radians), sine = -Math.sin(radians);
  const dx = px - geometry.position.x, dz = pz - geometry.position.z;
  const localX = dx * cosine + dz * sine;
  const localZ = -dx * sine + dz * cosine;
  return Math.abs(localX) <= geometry.widthM / 2 + GEOMETRIC_EPSILON
    && Math.abs(localZ) <= geometry.depthM / 2 + GEOMETRIC_EPSILON;
}

function segmentsCross(p1x: number, p1z: number, p2x: number, p2z: number,
                       q1x: number, q1z: number, q2x: number, q2z: number): boolean {
  const o1 = sign(p1x, p1z, p2x, p2z, q1x, q1z);
  const o2 = sign(p1x, p1z, p2x, p2z, q2x, q2z);
  const o3 = sign(q1x, q1z, q2x, q2z, p1x, p1z);
  const o4 = sign(q1x, q1z, q2x, q2z, p2x, p2z);
  return o1 * o2 < 0 && o3 * o4 < 0;
}

function triangleXzIntersectsRect(triangle: RooftopTriangle, geometry: RooftopVolumeGeometry): boolean {
  const a: readonly [number, number] = [triangle.ax, triangle.az];
  const b: readonly [number, number] = [triangle.bx, triangle.bz];
  const c: readonly [number, number] = [triangle.cx, triangle.cz];
  const rect = footprintCorners(geometry);
  for (const [rx, rz] of rect) if (pointInTriangle(rx, rz, a, b, c)) return true;
  for (const [tx, tz] of [a, b, c]) if (pointInRotatedRect(tx, tz, geometry)) return true;
  for (const triEdge of [[a, b], [b, c], [c, a]] as const) {
    for (let edge = 0; edge < 4; edge++) {
      const rectA = rect[edge]!, rectB = rect[(edge + 1) % 4]!;
      if (segmentsCross(triEdge[0][0], triEdge[0][1], triEdge[1][0], triEdge[1][1],
        rectA[0], rectA[1], rectB[0], rectB[1])) return true;
    }
  }
  return false;
}

/** True when any roof/above-roof triangle of one verified building intersects the
 * facility's 3D volume, ignoring only the coplanar support plane at the base. */
function buildingVolumeIssues(geometry: RooftopVolumeGeometry, support: RoofSupport,
                              shape: SourceBuildingShape): readonly string[] {
  const baseY = support.topY;
  const padCorners = footprintCorners(geometry);
  const padAabb = {
    minX: Math.min(...padCorners.map(corner => corner[0])),
    maxX: Math.max(...padCorners.map(corner => corner[0])),
    minZ: Math.min(...padCorners.map(corner => corner[1])),
    maxZ: Math.max(...padCorners.map(corner => corner[1])),
  };
  const issues: string[] = [];
  forEachRooftopTriangle(shape, triangle => {
    const bounds = triangleAabb(triangle);
    if (bounds.maxY <= baseY + SUPPORT_PLANE_EPSILON_M) return;
    if (bounds.minY >= baseY + geometry.heightM - GEOMETRIC_EPSILON) return;
    if (bounds.maxX < padAabb.minX - GEOMETRIC_EPSILON || bounds.minX > padAabb.maxX + GEOMETRIC_EPSILON
        || bounds.maxZ < padAabb.minZ - GEOMETRIC_EPSILON || bounds.minZ > padAabb.maxZ + GEOMETRIC_EPSILON) {
      return;
    }
    if (triangleXzIntersectsRect(triangle, geometry)) {
      issues.push(`屋顶正上方存在高于支撑面的来源几何（女儿墙/设备/尖顶），`
        + `${support.buildingId} 的净空不足以容纳设施体积`);
    }
  });
  return issues;
}

/** Reject a proposed rooftop facility volume that intersects any real roof or
 * above-roof triangle of the support building, or any complete building whose
 * axis-aligned bounds overlap the pad — parapets and HVAC never slip through.
 * Triangles at or below the exact coplanar support plane are the only exclusions. */
export function validateRooftopVolume(geometry: RooftopVolumeGeometry, support: RoofSupport,
                                      mesh: ReadonlyMap<string, SourceBuildingTriangleRange[]>): readonly string[] {
  const issues = new Set<string>();
  const padCorners = footprintCorners(geometry);
  const padAabb = {
    minX: Math.min(...padCorners.map(corner => corner[0])),
    maxX: Math.max(...padCorners.map(corner => corner[0])),
    minZ: Math.min(...padCorners.map(corner => corner[1])),
    maxZ: Math.max(...padCorners.map(corner => corner[1])),
  };
  const supportRange = mesh.get(support.buildingId);
  const supportBounds = supportRange === undefined ? null : rangeBounds(supportRange);
  const candidates = new Set<string>([support.buildingId]);
  for (const [id, ranges] of mesh) {
    if (id === support.buildingId || ranges.length === 0) continue;
    const bounds = rangeBounds(ranges);
    if (!Number.isFinite(bounds.minX)) continue;
    if (bounds.maxX < padAabb.minX - GEOMETRIC_EPSILON || bounds.minX > padAabb.maxX + GEOMETRIC_EPSILON
        || bounds.maxZ < padAabb.minZ - GEOMETRIC_EPSILON || bounds.minZ > padAabb.maxZ + GEOMETRIC_EPSILON) {
      continue;
    }
    if (supportBounds !== null && Number.isFinite(supportBounds.minX)
        && bounds.minY > support.topY + geometry.heightM + GEOMETRIC_EPSILON) continue;
    candidates.add(id);
  }
  for (const id of candidates) {
    const ranges = mesh.get(id);
    if (ranges === undefined || ranges.length === 0) continue;
    for (const message of buildingVolumeIssues(geometry, support, { ranges })) issues.add(message);
  }
  return [...issues];
}