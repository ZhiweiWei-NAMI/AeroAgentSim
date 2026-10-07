import * as THREE from "three";

export interface NormalizedWindowRect {
  readonly u0: number;
  readonly v0: number;
  readonly u1: number;
  readonly v1: number;
}

export interface CityWindowGeometryOptions {
  readonly paneOffsetM?: number;
  readonly frameWidthM?: number;
  readonly frameDepthM?: number;
  readonly frameOutwardOffsetM?: number;
}

export interface CityWindowGeometryResult {
  /** Flat pane quads with the source UV coordinates retained for emissive maps. */
  readonly panes: THREE.BufferGeometry;
  /** Separate untextured metal frame and inset reveal geometry. */
  readonly frames: THREE.BufferGeometry;
  readonly windowCount: number;
}

interface WindowTriangle {
  readonly positions: readonly [THREE.Vector3, THREE.Vector3, THREE.Vector3];
  readonly uvs: readonly [THREE.Vector2, THREE.Vector2, THREE.Vector2];
  readonly minU: number;
  readonly minV: number;
  readonly maxU: number;
  readonly maxV: number;
  readonly areaUv: number;
  readonly affine: Omit<UvAffineGroup, "triangles">;
}

interface UvAffineGroup {
  readonly origin: THREE.Vector3;
  readonly uAxis: THREE.Vector3;
  readonly vAxis: THREE.Vector3;
  readonly normal: THREE.Vector3;
  readonly triangles: WindowTriangle[];
}

interface GeometryBuilder {
  readonly positions: number[];
  readonly normals: number[];
  readonly uvs?: number[];
  readonly indices?: number[];
}

const MAX_WALL_NORMAL_Y = 0.18;
const MAP_AXIS_TOLERANCE_M_PER_UV = 0.003;
const MAP_ORIGIN_TOLERANCE_M = 0.003;
const UV_AREA_EPSILON = 1e-11;
const MIN_WINDOW_EDGE_M = 0.05;

function finiteVector(vector: THREE.Vector3): boolean {
  return Number.isFinite(vector.x) && Number.isFinite(vector.y) && Number.isFinite(vector.z);
}

function assertRectangles(rectangles: readonly NormalizedWindowRect[]): void {
  if (rectangles.length === 0) throw new Error("At least one normalized window rectangle is required");
  for (const rect of rectangles) {
    if (![rect.u0, rect.v0, rect.u1, rect.v1].every(Number.isFinite)
        || rect.u0 < 0 || rect.v0 < 0 || rect.u1 > 1 || rect.v1 > 1
        || rect.u1 - rect.u0 <= 0 || rect.v1 - rect.v0 <= 0) {
      throw new Error("Window rectangles must be finite, nonempty, and normalized to [0, 1]");
    }
  }
}

function makeEmptyGeometry(withUv: boolean): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute([], 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute([], 3));
  if (withUv) geometry.setAttribute("uv", new THREE.Float32BufferAttribute([], 2));
  return geometry;
}

function readTriangle(geometry: THREE.BufferGeometry, first: number, indexed: boolean): WindowTriangle | null {
  const position = geometry.getAttribute("position");
  const uv = geometry.getAttribute("uv");
  const index = geometry.getIndex();
  const positions: THREE.Vector3[] = [];
  const uvs: THREE.Vector2[] = [];
  for (let corner = 0; corner < 3; corner++) {
    const vertex = indexed ? index!.getX(first + corner) : first + corner;
    if (!Number.isSafeInteger(vertex) || vertex < 0 || vertex >= position.count || vertex >= uv.count) {
      throw new Error("Window wall group references a missing geometry vertex");
    }
    const point = new THREE.Vector3(position.getX(vertex), position.getY(vertex), position.getZ(vertex));
    const texcoord = new THREE.Vector2(uv.getX(vertex), uv.getY(vertex));
    if (!finiteVector(point) || !Number.isFinite(texcoord.x) || !Number.isFinite(texcoord.y)) {
      throw new Error("Window wall geometry contains a non-finite position or UV");
    }
    positions.push(point);
    uvs.push(texcoord);
  }

  const edgeA = positions[1]!.clone().sub(positions[0]!);
  const edgeB = positions[2]!.clone().sub(positions[0]!);
  const geometricNormal = edgeA.clone().cross(edgeB);
  if (geometricNormal.lengthSq() <= 1e-16) return null;
  geometricNormal.normalize();
  if (Math.abs(geometricNormal.y) > MAX_WALL_NORMAL_Y) return null;

  const uvA = uvs[1]!.clone().sub(uvs[0]!);
  const uvB = uvs[2]!.clone().sub(uvs[0]!);
  const determinant = uvA.x * uvB.y - uvA.y * uvB.x;
  if (Math.abs(determinant) <= UV_AREA_EPSILON) return null;

  const uAxis = edgeA.clone().multiplyScalar(uvB.y)
    .addScaledVector(edgeB, -uvA.y).divideScalar(determinant);
  const vAxis = edgeB.clone().multiplyScalar(uvA.x)
    .addScaledVector(edgeA, -uvB.x).divideScalar(determinant);
  const origin = positions[0]!.clone().addScaledVector(uAxis, -uvs[0]!.x)
    .addScaledVector(vAxis, -uvs[0]!.y);
  if (!finiteVector(uAxis) || !finiteVector(vAxis) || !finiteVector(origin)) return null;

  const uvNormal = uAxis.clone().cross(vAxis);
  if (uvNormal.lengthSq() <= 1e-16) return null;
  uvNormal.normalize();
  if (uvNormal.dot(geometricNormal) < 0) uvNormal.negate();

  const us = uvs.map(value => value.x);
  const vs = uvs.map(value => value.y);
  const areaUv = Math.abs(determinant) / 2;
  return {
    positions: positions as [THREE.Vector3, THREE.Vector3, THREE.Vector3],
    uvs: uvs as [THREE.Vector2, THREE.Vector2, THREE.Vector2],
    minU: Math.min(...us), minV: Math.min(...vs), maxU: Math.max(...us), maxV: Math.max(...vs), areaUv,
    affine: { origin, uAxis, vAxis, normal: uvNormal },
  };
}

function affineOf(triangle: WindowTriangle): Omit<UvAffineGroup, "triangles"> {
  return triangle.affine;
}

function sameAffine(group: UvAffineGroup, affine: Omit<UvAffineGroup, "triangles">): boolean {
  return group.uAxis.distanceTo(affine.uAxis) <= MAP_AXIS_TOLERANCE_M_PER_UV
    && group.vAxis.distanceTo(affine.vAxis) <= MAP_AXIS_TOLERANCE_M_PER_UV
    && group.origin.distanceTo(affine.origin) <= MAP_ORIGIN_TOLERANCE_M
    && group.normal.dot(affine.normal) >= 0.9995;
}

function positionAt(group: UvAffineGroup, u: number, v: number, normalOffset = 0): THREE.Vector3 {
  return group.origin.clone().addScaledVector(group.uAxis, u).addScaledVector(group.vAxis, v)
    .addScaledVector(group.normal, normalOffset);
}

function uvTriangleKey(triangle: WindowTriangle): string {
  return triangle.uvs.map(uv => `${Math.round(uv.x * 1e6)},${Math.round(uv.y * 1e6)}`)
    .sort().join(";");
}

function tileKey(u: number, v: number): string {
  return `${u},${v}`;
}

function indexByUvTile(group: UvAffineGroup): Map<string, WindowTriangle[]> {
  const tiles = new Map<string, WindowTriangle[]>();
  const unique = new Set<string>();
  for (const triangle of group.triangles) {
    const identity = uvTriangleKey(triangle);
    if (unique.has(identity)) continue;
    unique.add(identity);
    const minTileU = Math.floor(triangle.minU), maxTileU = Math.floor(triangle.maxU);
    const minTileV = Math.floor(triangle.minV), maxTileV = Math.floor(triangle.maxV);
    for (let tileU = minTileU; tileU <= maxTileU; tileU++) {
      for (let tileV = minTileV; tileV <= maxTileV; tileV++) {
        const bucket = tiles.get(tileKey(tileU, tileV)) ?? [];
        bucket.push(triangle);
        tiles.set(tileKey(tileU, tileV), bucket);
      }
    }
  }
  return tiles;
}

function trianglesForUvRect(tiles: ReadonlyMap<string, WindowTriangle[]>,
                            minU: number, minV: number, maxU: number, maxV: number): WindowTriangle[] {
  const found = new Set<WindowTriangle>();
  for (let tileU = Math.floor(minU); tileU <= Math.floor(maxU); tileU++) {
    for (let tileV = Math.floor(minV); tileV <= Math.floor(maxV); tileV++) {
      for (const triangle of tiles.get(tileKey(tileU, tileV)) ?? []) {
        if (triangle.maxU >= minU - 1e-9 && triangle.minU <= maxU + 1e-9
            && triangle.maxV >= minV - 1e-9 && triangle.minV <= maxV + 1e-9) found.add(triangle);
      }
    }
  }
  return [...found];
}

function clipAgainstTriangle(rect: readonly (readonly [number, number])[], triangle: WindowTriangle): [number, number][] {
  let polygon = rect.map(point => [point[0], point[1]] as [number, number]);
  const vertices = triangle.uvs.map(uv => [uv.x, uv.y] as [number, number]);
  const signedArea = (vertices[1]![0] - vertices[0]![0]) * (vertices[2]![1] - vertices[0]![1])
    - (vertices[1]![1] - vertices[0]![1]) * (vertices[2]![0] - vertices[0]![0]);
  const orientation = signedArea >= 0 ? 1 : -1;
  const inside = (point: readonly number[], a: readonly number[], b: readonly number[]): boolean =>
    orientation * ((b[0]! - a[0]!) * (point[1]! - a[1]!)
      - (b[1]! - a[1]!) * (point[0]! - a[0]!)) >= -1e-10;
  for (let edge = 0; edge < 3 && polygon.length > 0; edge++) {
    const a = vertices[edge]!, b = vertices[(edge + 1) % 3]!;
    const input = polygon;
    polygon = [];
    let previous = input[input.length - 1]!;
    let previousInside = inside(previous, a, b);
    for (const current of input) {
      const currentInside = inside(current, a, b);
      if (currentInside !== previousInside) {
        const edgeX = b[0] - a[0], edgeY = b[1] - a[1];
        const prevSide = edgeX * (previous[1] - a[1]) - edgeY * (previous[0] - a[0]);
        const currSide = edgeX * (current[1] - a[1]) - edgeY * (current[0] - a[0]);
        const ratio = prevSide / (prevSide - currSide);
        polygon.push([previous[0] + (current[0] - previous[0]) * ratio,
          previous[1] + (current[1] - previous[1]) * ratio]);
      }
      if (currentInside) polygon.push([current[0], current[1]]);
      previous = current;
      previousInside = currentInside;
    }
  }
  return polygon;
}

function polygonArea(polygon: readonly (readonly number[])[]): number {
  let doubleArea = 0;
  for (let index = 0; index < polygon.length; index++) {
    const current = polygon[index]!, next = polygon[(index + 1) % polygon.length]!;
    doubleArea += current[0]! * next[1]! - current[1]! * next[0]!;
  }
  return Math.abs(doubleArea) / 2;
}

function pointInTriangle(u: number, v: number, triangle: WindowTriangle): boolean {
  const points = triangle.uvs;
  const edgeSide = (a: THREE.Vector2, b: THREE.Vector2): number =>
    (b.x - a.x) * (v - a.y) - (b.y - a.y) * (u - a.x);
  const first = edgeSide(points[0]!, points[1]!);
  const second = edgeSide(points[1]!, points[2]!);
  const third = edgeSide(points[2]!, points[0]!);
  return (first >= -1e-9 && second >= -1e-9 && third >= -1e-9)
    || (first <= 1e-9 && second <= 1e-9 && third <= 1e-9);
}

function fullyCovered(rect: NormalizedWindowRect, tileU: number, tileV: number,
                      triangles: readonly WindowTriangle[], areaScale: number): boolean {
  const u0 = tileU + rect.u0, v0 = tileV + rect.v0;
  const u1 = tileU + rect.u1, v1 = tileV + rect.v1;
  const rectArea = (u1 - u0) * (v1 - v0);
  const polygon = [[u0, v0], [u1, v0], [u1, v1], [u0, v1]] as const;
  let coveredArea = 0;
  const overlapping: WindowTriangle[] = [];
  for (const triangle of triangles) {
    if (triangle.maxU < u0 - 1e-9 || triangle.minU > u1 + 1e-9
        || triangle.maxV < v0 - 1e-9 || triangle.minV > v1 + 1e-9) continue;
    const clipped = clipAgainstTriangle(polygon, triangle);
    const area = polygonArea(clipped);
    if (area > 1e-12) {
      coveredArea += area;
      overlapping.push(triangle);
    }
  }
  const uvTolerance = Math.max(2e-7, rectArea * 0.001);
  if (coveredArea < rectArea - uvTolerance || coveredArea > rectArea + uvTolerance) return false;
  // Area coverage catches small holes; point checks also guard against duplicated or overlapping faces.
  const samples: readonly [number, number][] = [
    [u0, v0], [u1, v0], [u1, v1], [u0, v1],
    [(u0 + u1) / 2, v0], [u1, (v0 + v1) / 2], [(u0 + u1) / 2, v1], [u0, (v0 + v1) / 2],
    [(u0 + u1) / 2, (v0 + v1) / 2],
  ];
  if (samples.some(([u, v]) => !overlapping.some(triangle => pointInTriangle(u, v, triangle)))) return false;
  // Transforming a repeated UV tile must preserve its area in square metres.
  return Number.isFinite(areaScale) && areaScale > 1e-8;
}

function appendTriangle(builder: GeometryBuilder, a: THREE.Vector3, b: THREE.Vector3, c: THREE.Vector3,
                        expectedNormal: THREE.Vector3, uv?: readonly [THREE.Vector2, THREE.Vector2, THREE.Vector2]): void {
  const normal = expectedNormal.clone().normalize();
  const winding = b.clone().sub(a).cross(c.clone().sub(a));
  const points = winding.dot(normal) >= 0 ? [a, b, c] : [a, c, b];
  const texcoords = uv === undefined ? undefined : winding.dot(normal) >= 0 ? uv : [uv[0], uv[2], uv[1]];
  for (let corner = 0; corner < 3; corner++) {
    const point = points[corner]!;
    builder.positions.push(point.x, point.y, point.z);
    builder.normals.push(normal.x, normal.y, normal.z);
    if (builder.uvs !== undefined) {
      const texcoord = texcoords?.[corner];
      if (texcoord === undefined) throw new Error("Window pane triangle is missing its source UV");
      builder.uvs.push(texcoord.x, texcoord.y);
    }
  }
}

function appendQuad(builder: GeometryBuilder, points: readonly [THREE.Vector3, THREE.Vector3, THREE.Vector3, THREE.Vector3],
                    expectedNormal: THREE.Vector3): void {
  appendTriangle(builder, points[0], points[1], points[2], expectedNormal);
  appendTriangle(builder, points[0], points[2], points[3], expectedNormal);
}

function appendWindowPane(builder: GeometryBuilder, group: UvAffineGroup, rect: NormalizedWindowRect,
                          tileU: number, tileV: number, offset: number): void {
  const u0 = tileU + rect.u0, u1 = tileU + rect.u1;
  const v0 = tileV + rect.v0, v1 = tileV + rect.v1;
  const positions = [positionAt(group, u0, v0, offset), positionAt(group, u1, v0, offset),
    positionAt(group, u1, v1, offset), positionAt(group, u0, v1, offset)] as const;
  const uvs = [new THREE.Vector2(u0, v0), new THREE.Vector2(u1, v0),
    new THREE.Vector2(u1, v1), new THREE.Vector2(u0, v1)] as const;
  if (builder.indices === undefined) throw new Error("Window pane builder must use indexed geometry");
  const firstVertex = builder.positions.length / 3;
  for (let corner = 0; corner < 4; corner++) {
    const point = positions[corner]!;
    const texcoord = uvs[corner]!;
    builder.positions.push(point.x, point.y, point.z);
    builder.normals.push(group.normal.x, group.normal.y, group.normal.z);
    builder.uvs!.push(texcoord.x, texcoord.y);
  }
  const winding = positions[1]!.clone().sub(positions[0]!)
    .cross(positions[2]!.clone().sub(positions[0]!)).dot(group.normal);
  if (winding >= 0) {
    builder.indices.push(firstVertex, firstVertex + 1, firstVertex + 2,
      firstVertex, firstVertex + 2, firstVertex + 3);
  } else {
    builder.indices.push(firstVertex, firstVertex + 2, firstVertex + 1,
      firstVertex, firstVertex + 3, firstVertex + 2);
  }
}

function appendIndexedFrontRing(builder: GeometryBuilder, group: UvAffineGroup,
                                outer: readonly THREE.Vector3[], inner: readonly THREE.Vector3[]): void {
  if (builder.indices === undefined) throw new Error("Zero-depth window frame builder must use indexed geometry");
  const firstVertex = builder.positions.length / 3;
  for (let corner = 0; corner < 4; corner++) {
    const point = outer[corner]!;
    builder.positions.push(point.x, point.y, point.z);
    builder.normals.push(group.normal.x, group.normal.y, group.normal.z);
  }
  for (let corner = 0; corner < 4; corner++) {
    const point = inner[corner]!;
    builder.positions.push(point.x, point.y, point.z);
    builder.normals.push(group.normal.x, group.normal.y, group.normal.z);
  }
  for (let edge = 0; edge < 4; edge++) {
    const next = (edge + 1) % 4;
    const outerEdge = firstVertex + edge;
    const outerNext = firstVertex + next;
    const innerNext = firstVertex + 4 + next;
    const innerEdge = firstVertex + 4 + edge;
    const a = outer[edge]!, b = outer[next]!, c = inner[next]!;
    const winding = b.clone().sub(a).cross(c.clone().sub(a)).dot(group.normal);
    if (winding >= 0) {
      builder.indices.push(outerEdge, outerNext, innerNext, outerEdge, innerNext, innerEdge);
    } else {
      builder.indices.push(outerEdge, innerEdge, innerNext, outerEdge, innerNext, outerNext);
    }
  }
}

function appendWindowFrame(builder: GeometryBuilder, group: UvAffineGroup, rect: NormalizedWindowRect,
                           tileU: number, tileV: number, frameWidthM: number,
                           frameDepthM: number, frontOffsetM: number): void {
  const uLengthSq = group.uAxis.lengthSq(), vLengthSq = group.vAxis.lengthSq();
  const uPerpendicular = group.uAxis.clone().addScaledVector(group.vAxis,
    -group.uAxis.dot(group.vAxis) / vLengthSq).length();
  const vPerpendicular = group.vAxis.clone().addScaledVector(group.uAxis,
    -group.vAxis.dot(group.uAxis) / uLengthSq).length();
  if (uPerpendicular <= 1e-8 || vPerpendicular <= 1e-8) return;
  const du = frameWidthM / uPerpendicular, dv = frameWidthM / vPerpendicular;
  const u0 = tileU + rect.u0, u1 = tileU + rect.u1;
  const v0 = tileV + rect.v0, v1 = tileV + rect.v1;
  const outerUv = [[u0 - du, v0 - dv], [u1 + du, v0 - dv],
    [u1 + du, v1 + dv], [u0 - du, v1 + dv]] as const;
  const innerUv = [[u0, v0], [u1, v0], [u1, v1], [u0, v1]] as const;
  const backOffsetM = frontOffsetM - frameDepthM;
  const outerFront = outerUv.map(([u, v]) => positionAt(group, u, v, frontOffsetM)) as unknown as
    [THREE.Vector3, THREE.Vector3, THREE.Vector3, THREE.Vector3];
  const innerFront = innerUv.map(([u, v]) => positionAt(group, u, v, frontOffsetM)) as unknown as
    [THREE.Vector3, THREE.Vector3, THREE.Vector3, THREE.Vector3];
  if (frameDepthM === 0) {
    appendIndexedFrontRing(builder, group, outerFront, innerFront);
    return;
  }
  const outerBack = outerUv.map(([u, v]) => positionAt(group, u, v, backOffsetM)) as unknown as
    [THREE.Vector3, THREE.Vector3, THREE.Vector3, THREE.Vector3];
  const innerBack = innerUv.map(([u, v]) => positionAt(group, u, v, backOffsetM)) as unknown as
    [THREE.Vector3, THREE.Vector3, THREE.Vector3, THREE.Vector3];

  const center = positionAt(group, (u0 + u1) / 2, (v0 + v1) / 2);
  for (let edge = 0; edge < 4; edge++) {
    const next = (edge + 1) % 4;
    appendQuad(builder, [outerFront[edge]!, outerFront[next]!, innerFront[next]!, innerFront[edge]!], group.normal);
    appendQuad(builder, [outerBack[edge]!, innerBack[edge]!, innerBack[next]!, outerBack[next]!], group.normal.clone().negate());
    const edgeMidpoint = outerFront[edge]!.clone().add(outerFront[next]!).multiplyScalar(0.5);
    const radial = edgeMidpoint.sub(center);
    radial.addScaledVector(group.normal, -radial.dot(group.normal)).normalize();
    appendQuad(builder, [outerFront[edge]!, outerBack[edge]!, outerBack[next]!, outerFront[next]!], radial);
    appendQuad(builder, [innerFront[edge]!, innerFront[next]!, innerBack[next]!, innerBack[edge]!], radial.clone().negate());
  }
}

function makeGeometry(builder: GeometryBuilder, withUv: boolean): THREE.BufferGeometry {
  const geometry = makeEmptyGeometry(withUv);
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(builder.positions, 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(builder.normals, 3));
  if (withUv) geometry.setAttribute("uv", new THREE.Float32BufferAttribute(builder.uvs ?? [], 2));
  if (builder.indices !== undefined) geometry.setIndex(builder.indices);
  if (builder.positions.length > 0) {
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
  }
  return geometry;
}

function validateOptions(options: CityWindowGeometryOptions): Required<CityWindowGeometryOptions> {
  const values = {
    paneOffsetM: options.paneOffsetM ?? 0.002,
    frameWidthM: options.frameWidthM ?? 0.04,
    frameDepthM: options.frameDepthM ?? 0.04,
    frameOutwardOffsetM: options.frameOutwardOffsetM ?? 0.006,
  };
  if (Object.values(values).some(value => !Number.isFinite(value))
      || values.paneOffsetM < 0 || values.paneOffsetM > 0.01
      || values.frameWidthM <= 0 || values.frameWidthM > 0.08
      || values.frameDepthM < 0 || values.frameDepthM > 0.08
      || values.frameOutwardOffsetM < 0 || values.frameOutwardOffsetM > 0.008) {
    throw new Error("Window pane and frame dimensions exceed the building collision envelope limits");
  }
  return values;
}

/**
 * Extract complete repeated window rectangles only from selected vertical wall groups.
 * UV-space area coverage ensures a pane cannot cross a wall edge, roof, or mesh opening.
 */
export function extractCityWindowGeometry(
  source: THREE.BufferGeometry,
  /** Three.js `geometry.groups[].materialIndex` values that identify wall surfaces. */
  wallMaterialIndices: readonly number[],
  windowRects: readonly NormalizedWindowRect[],
  repeat: boolean,
  options: CityWindowGeometryOptions = {},
): CityWindowGeometryResult {
  assertRectangles(windowRects);
  if (typeof repeat !== "boolean") throw new Error("Window UV repeat flag must be boolean");
  if (wallMaterialIndices.length === 0 || wallMaterialIndices.some(index => !Number.isSafeInteger(index) || index < 0)) {
    throw new Error("At least one valid wall material index is required");
  }
  const dimensions = validateOptions(options);
  const position = source.getAttribute("position"), uv = source.getAttribute("uv"), index = source.getIndex();
  if (position === undefined || position.itemSize < 3 || uv === undefined || uv.itemSize < 2) {
    throw new Error("Window extraction requires position and UV attributes");
  }
  const groups = source.groups;
  if (groups.length === 0) throw new Error("Window extraction requires explicit material groups");
  const selected = new Set(wallMaterialIndices);
  const available = new Set(groups.map(group => group.materialIndex));
  if ([...selected].some(materialIndex => !available.has(materialIndex))) {
    throw new Error("A requested wall material index is absent from the source geometry");
  }
  const elementCount = index?.count ?? position.count;
  const affineGroups: UvAffineGroup[] = [];
  for (const materialGroup of groups) {
    if (materialGroup.materialIndex === undefined) throw new Error("Window material group has no material index");
    if (!selected.has(materialGroup.materialIndex)) continue;
    if (!Number.isSafeInteger(materialGroup.start) || !Number.isSafeInteger(materialGroup.count)
        || materialGroup.start < 0 || materialGroup.count < 0 || materialGroup.start + materialGroup.count > elementCount
        || materialGroup.count % 3 !== 0) {
      throw new Error("Window wall material group has an invalid triangle range");
    }
    for (let first = materialGroup.start; first < materialGroup.start + materialGroup.count; first += 3) {
      const triangle = readTriangle(source, first, index !== null);
      if (triangle === null) continue;
      const affine = affineOf(triangle);
      let group = affineGroups.find(candidate => sameAffine(candidate, affine));
      if (group === undefined) {
        group = { ...affine, triangles: [] };
        affineGroups.push(group);
      }
      const transformed = triangle.positions.map((point, corner) => positionAt(group!, triangle.uvs[corner]!.x,
        triangle.uvs[corner]!.y).distanceTo(point));
      if (Math.max(...transformed) <= MAP_ORIGIN_TOLERANCE_M + MAP_AXIS_TOLERANCE_M_PER_UV * 2) {
        group.triangles.push(triangle);
      }
    }
  }

  const paneBuilder: GeometryBuilder = { positions: [], normals: [], uvs: [], indices: [] };
  const frameBuilder: GeometryBuilder = dimensions.frameDepthM === 0
    ? { positions: [], normals: [], indices: [] }
    : { positions: [], normals: [] };
  const emittedWindows = new Set<string>();
  let windowCount = 0;
  for (const group of affineGroups) {
    if (group.triangles.length === 0) continue;
    const tiles = indexByUvTile(group);
    const minU = Math.min(...group.triangles.map(triangle => triangle.minU));
    const maxU = Math.max(...group.triangles.map(triangle => triangle.maxU));
    const minV = Math.min(...group.triangles.map(triangle => triangle.minV));
    const maxV = Math.max(...group.triangles.map(triangle => triangle.maxV));
    const firstTileU = repeat ? Math.floor(minU) : 0;
    const lastTileU = repeat ? Math.floor(maxU) : 0;
    const firstTileV = repeat ? Math.floor(minV) : 0;
    const lastTileV = repeat ? Math.floor(maxV) : 0;
    if (lastTileU - firstTileU > 4096 || lastTileV - firstTileV > 4096) {
      throw new Error("Window wall UV range exceeds the supported repeated tile bounds");
    }
    const uPerpendicular = group.uAxis.clone().addScaledVector(group.vAxis,
      -group.uAxis.dot(group.vAxis) / group.vAxis.lengthSq()).length();
    const vPerpendicular = group.vAxis.clone().addScaledVector(group.uAxis,
      -group.vAxis.dot(group.uAxis) / group.uAxis.lengthSq()).length();
    const du = dimensions.frameWidthM / uPerpendicular, dv = dimensions.frameWidthM / vPerpendicular;
    const areaScale = group.uAxis.clone().cross(group.vAxis).length();
    for (let tileU = firstTileU; tileU <= lastTileU; tileU++) {
      for (let tileV = firstTileV; tileV <= lastTileV; tileV++) {
        const tileTriangles = tiles.get(tileKey(tileU, tileV)) ?? [];
        if (tileTriangles.length === 0) continue;
        for (const rect of windowRects) {
          const windowWidthM = group.uAxis.length() * (rect.u1 - rect.u0);
          const windowHeightM = group.vAxis.length() * (rect.v1 - rect.v0);
          if (windowWidthM < MIN_WINDOW_EDGE_M || windowHeightM < MIN_WINDOW_EDGE_M) continue;
          if (!fullyCovered(rect, tileU, tileV, tileTriangles, areaScale)) continue;
          const frameU0 = tileU + rect.u0 - du, frameV0 = tileV + rect.v0 - dv;
          const frameU1 = tileU + rect.u1 + du, frameV1 = tileV + rect.v1 + dv;
          const frameTriangles = trianglesForUvRect(tiles, frameU0, frameV0, frameU1, frameV1);
          const frameOuter: NormalizedWindowRect = {
            u0: frameU0 - tileU, v0: frameV0 - tileV, u1: frameU1 - tileU, v1: frameV1 - tileV,
          };
          if (!fullyCovered(frameOuter, tileU, tileV, frameTriangles, areaScale)) continue;
          const panePoints = [
            positionAt(group, tileU + rect.u0, tileV + rect.v0),
            positionAt(group, tileU + rect.u1, tileV + rect.v0),
            positionAt(group, tileU + rect.u1, tileV + rect.v1),
            positionAt(group, tileU + rect.u0, tileV + rect.v1),
          ];
          const normalKey = [group.normal.x, group.normal.y, group.normal.z].map(value => Math.round(value * 1000)).join(",");
          const pointKey = panePoints.map(point => [point.x, point.y, point.z]
            .map(value => Math.round(value / 0.002)).join(",")).sort().join(";");
          const identity = `${normalKey}|${pointKey}`;
          if (emittedWindows.has(identity)) continue;
          emittedWindows.add(identity);
          appendWindowPane(paneBuilder, group, rect, tileU, tileV, dimensions.paneOffsetM);
          appendWindowFrame(frameBuilder, group, rect, tileU, tileV, dimensions.frameWidthM,
            dimensions.frameDepthM, dimensions.frameOutwardOffsetM);
          windowCount++;
        }
      }
    }
  }
  return { panes: makeGeometry(paneBuilder, true), frames: makeGeometry(frameBuilder, false), windowCount };
}
