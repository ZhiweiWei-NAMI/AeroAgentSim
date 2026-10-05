import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { extractCityWindowGeometry, type NormalizedWindowRect } from "./city-window-geometry";

type Uv = readonly [number, number];
type Triangle = readonly [Uv, Uv, Uv];

interface WallOptions {
  readonly minU?: number;
  readonly minV?: number;
  readonly maxU?: number;
  readonly maxV?: number;
  readonly indexed?: boolean;
  readonly rotated?: boolean;
  readonly triangles?: readonly Triangle[];
  readonly duplicate?: boolean;
  readonly roof?: boolean;
  readonly mirroredUv?: boolean;
}

function wallGeometry(options: WallOptions = {}): THREE.BufferGeometry {
  const { minU = 0, minV = 0, maxU = 4, maxV = 4, indexed = true,
    rotated = false, duplicate = false, roof = false, mirroredUv = false } = options;
  const triangles = options.triangles ?? [
    [[minU, minV], [maxU, minV], [maxU, maxV]],
    [[minU, minV], [maxU, maxV], [minU, maxV]],
  ];
  const geometry = new THREE.BufferGeometry();
  const angle = rotated ? THREE.MathUtils.degToRad(37) : 0;
  const uAxis = new THREE.Vector3(Math.cos(angle), 0, Math.sin(angle));
  const origin = new THREE.Vector3(7, 2, -3);
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  const vertexByUv = new Map<string, number>();
  const pushVertex = (u: number, v: number): number => {
    const key = `${u.toPrecision(12)},${v.toPrecision(12)}`;
    const existing = vertexByUv.get(key);
    if (indexed && existing !== undefined) return existing;
    const point = origin.clone().addScaledVector(uAxis, u).add(new THREE.Vector3(0, v, 0));
    const vertex = positions.length / 3;
    positions.push(point.x, point.y, point.z);
    uvs.push(mirroredUv ? minU + maxU - u : u, v);
    if (indexed) vertexByUv.set(key, vertex);
    return vertex;
  };
  for (const triangle of triangles) for (const [u, v] of triangle) indices.push(pushVertex(u, v));
  if (duplicate) for (const triangle of triangles) for (const [u, v] of triangle) indices.push(pushVertex(u, v));
  if (!indexed) indices.splice(0, indices.length, ...Array.from({ length: positions.length / 3 }, (_, index) => index));
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  if (indexed) geometry.setIndex(indices);
  const wallIndexCount = indices.length;
  geometry.addGroup(0, wallIndexCount, 0);
  if (roof) {
    const first = geometry.getAttribute("position").count;
    const roofPositions = [0, 0, 0, 4, 0, 0, 4, 0, 4, 0, 0, 4];
    const roofUvs = [0, 0, 1, 0, 1, 1, 0, 1];
    const mergedPositions = [...positions, ...roofPositions];
    const mergedUvs = [...uvs, ...roofUvs];
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(mergedPositions, 3));
    geometry.setAttribute("uv", new THREE.Float32BufferAttribute(mergedUvs, 2));
    if (indexed) {
      const roofOffset = first;
      geometry.setIndex([...indices, roofOffset, roofOffset + 1, roofOffset + 2,
        roofOffset, roofOffset + 2, roofOffset + 3]);
      geometry.clearGroups();
      geometry.addGroup(0, wallIndexCount, 0);
      geometry.addGroup(wallIndexCount, 6, 1);
    } else {
      const roofIndices = Array.from({ length: 6 }, (_, index) => first + index);
      geometry.setAttribute("position", new THREE.Float32BufferAttribute([...mergedPositions, ...roofPositions], 3));
      geometry.setAttribute("uv", new THREE.Float32BufferAttribute([...mergedUvs, ...roofUvs], 2));
      geometry.clearGroups();
      geometry.addGroup(0, wallIndexCount, 0);
      geometry.addGroup(wallIndexCount, roofIndices.length, 1);
    }
  }
  return geometry;
}

const centeredWindow: NormalizedWindowRect = { u0: 0.2, v0: 0.2, u1: 0.8, v1: 0.8 };

function expectPaneTrianglesFaceNormals(geometry: THREE.BufferGeometry): void {
  const positions = geometry.getAttribute("position");
  const normals = geometry.getAttribute("normal");
  const indices = geometry.getIndex();
  expect(indices).not.toBeNull();
  for (let first = 0; first < indices!.count; first += 3) {
    const aIndex = indices!.getX(first), bIndex = indices!.getX(first + 1), cIndex = indices!.getX(first + 2);
    const a = new THREE.Vector3().fromBufferAttribute(positions, aIndex);
    const b = new THREE.Vector3().fromBufferAttribute(positions, bIndex);
    const c = new THREE.Vector3().fromBufferAttribute(positions, cIndex);
    const expected = new THREE.Vector3().fromBufferAttribute(normals, aIndex);
    expect(b.sub(a).cross(c.sub(a)).dot(expected)).toBeGreaterThan(0);
  }
}

describe("UV-mapped city windows", () => {
  it("extracts one complete pane across a triangle diagonal on a rotated indexed wall", () => {
    const geometry = wallGeometry({ rotated: true, roof: true });
    const result = extractCityWindowGeometry(geometry, [0], [centeredWindow], false);
    expect(result.windowCount).toBe(1);
    expect(result.panes.getAttribute("position").count).toBe(4);
    expect(result.panes.getIndex()?.count).toBe(6);
    expect(result.panes.getAttribute("uv").count).toBe(4);
    expect(result.frames.getAttribute("position").count).toBe(96);
    expect(result.frames.getIndex()).toBeNull();
    const normal = new THREE.Vector3(result.panes.getAttribute("normal").getX(0),
      result.panes.getAttribute("normal").getY(0), result.panes.getAttribute("normal").getZ(0));
    expect(Math.abs(normal.y)).toBeLessThan(1e-6);
    const panePosition = result.panes.getAttribute("position");
    expect(panePosition.getY(0)).toBeCloseTo(2.2, 5);
    expect(result.panes.getAttribute("uv").getX(0)).toBeCloseTo(0.2);
    expect(result.panes.getAttribute("uv").getY(0)).toBeCloseTo(0.2);
  });

  it("repeats a normalized 4-by-4 pattern across positive and negative UV tiles", () => {
    const pattern = { u0: 0.15, v0: 0.15, u1: 0.35, v1: 0.35 };
    const positive = extractCityWindowGeometry(wallGeometry(), [0], [pattern], true);
    expect(positive.windowCount).toBe(16);
    const oneTile = extractCityWindowGeometry(wallGeometry(), [0], [pattern], false);
    expect(oneTile.windowCount).toBe(1);

    const negative = extractCityWindowGeometry(wallGeometry({ minU: -2, minV: -2, maxU: 2, maxV: 2 }),
      [0], [pattern], true);
    expect(negative.windowCount).toBe(16);
    const paneUvs = negative.panes.getAttribute("uv");
    expect(Math.min(...Array.from({ length: paneUvs.count }, (_, index) => paneUvs.getX(index)))).toBeLessThan(0);
    expect(Math.max(...Array.from({ length: paneUvs.count }, (_, index) => paneUvs.getY(index)))).toBeGreaterThan(0);
  });

  it("accepts nonindexed wall triangles and deduplicates coincident triangle pairs", () => {
    const nonindexed = extractCityWindowGeometry(wallGeometry({ indexed: false }), [0], [centeredWindow], false);
    expect(nonindexed.windowCount).toBe(1);
    expect(nonindexed.panes.getAttribute("position").count).toBe(4);
    expect(nonindexed.panes.getIndex()?.count).toBe(6);
    const duplicated = extractCityWindowGeometry(wallGeometry({ duplicate: true }), [0], [centeredWindow], true);
    expect(duplicated.windowCount).toBe(16);
    expect(duplicated.panes.getAttribute("position").count).toBe(16 * 4);
    expect(duplicated.panes.getIndex()?.count).toBe(16 * 6);
  });

  it("does not bridge an open wall boundary or place a pane on a roof", () => {
    const halfWall = wallGeometry({ maxU: 0.5, maxV: 1,
      triangles: [[[0, 0], [0.5, 0], [0.5, 1]], [[0, 0], [0.5, 1], [0, 1]]] });
    const crossing = extractCityWindowGeometry(halfWall, [0], [{ u0: 0.4, v0: 0.2, u1: 0.6, v1: 0.8 }], true);
    expect(crossing.windowCount).toBe(0);

    const roofOnly = new THREE.BufferGeometry();
    roofOnly.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 4, 0, 0, 4, 0, 4], 3));
    roofOnly.setAttribute("uv", new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1], 2));
    roofOnly.addGroup(0, 3, 0);
    expect(extractCityWindowGeometry(roofOnly, [0], [centeredWindow], true).windowCount).toBe(0);
  });

  it("rejects a pane that fits when its expanded metal frame would cross the wall edge", () => {
    const wall = wallGeometry({ maxU: 0.82, maxV: 4 });
    expect(extractCityWindowGeometry(wall, [0], [centeredWindow], true,
      { frameWidthM: 0.01 }).windowCount).toBe(4);
    expect(extractCityWindowGeometry(wall, [0], [centeredWindow], true).windowCount).toBe(0);
  });

  it("omits panes smaller than the normalized window and frame pattern", () => {
    const narrow = wallGeometry({ maxU: 0.3, maxV: 1,
      triangles: [[[0, 0], [0.3, 0], [0.3, 1]], [[0, 0], [0.3, 1], [0, 1]]] });
    expect(extractCityWindowGeometry(narrow, [0], [centeredWindow], true).windowCount).toBe(0);
  });

  it("keeps glass offset, frame depth, and outward rim inside their physical limits", () => {
    const result = extractCityWindowGeometry(wallGeometry({ rotated: true }), [0], [centeredWindow], true);
    const normal = new THREE.Vector3(result.panes.getAttribute("normal").getX(0),
      result.panes.getAttribute("normal").getY(0), result.panes.getAttribute("normal").getZ(0));
    const planePoint = new THREE.Vector3(7, 2, -3);
    for (const [geometry, minOffset, maxOffset] of [
      [result.panes, 0.0019, 0.0021], [result.frames, -0.0341, 0.0061],
    ] as const) {
      const attribute = geometry.getAttribute("position");
      const projections = Array.from({ length: attribute.count }, (_, index) => {
        const point = new THREE.Vector3(attribute.getX(index), attribute.getY(index), attribute.getZ(index));
        return point.sub(planePoint).dot(normal);
      });
      expect(Math.min(...projections)).toBeGreaterThanOrEqual(minOffset);
      expect(Math.max(...projections)).toBeLessThanOrEqual(maxOffset);
    }
  });

  it("builds an indexed planar front ring when frame depth is zero", () => {
    const result = extractCityWindowGeometry(wallGeometry({ rotated: true }), [0], [centeredWindow], false,
      { paneOffsetM: 0, frameDepthM: 0, frameOutwardOffsetM: 0 });
    expect(result.windowCount).toBe(1);
    expect(result.panes.getAttribute("position").count).toBe(4);
    expect(result.panes.getIndex()?.count).toBe(6);
    expect(result.frames.getAttribute("position").count).toBe(8);
    const frameIndices = result.frames.getIndex()!;
    expect(frameIndices.count).toBe(24);
    expect(frameIndices.count / 3).toBe(8);
    expect(result.frames.boundingBox).not.toBeNull();
    expect(result.frames.boundingSphere).not.toBeNull();

    const normalAttribute = result.frames.getAttribute("normal");
    const normal = new THREE.Vector3(normalAttribute.getX(0), normalAttribute.getY(0), normalAttribute.getZ(0));
    const planePoint = new THREE.Vector3(7, 2, -3);
    const positions = result.frames.getAttribute("position");
    const planeOffsets = Array.from({ length: positions.count }, (_, vertex) => {
      const point = new THREE.Vector3(positions.getX(vertex), positions.getY(vertex), positions.getZ(vertex));
      return point.sub(planePoint).dot(normal);
    });
    expect(Math.min(...planeOffsets)).toBeCloseTo(0, 6);
    expect(Math.max(...planeOffsets)).toBeCloseTo(0, 6);
    for (let vertex = 0; vertex < normalAttribute.count; vertex++) {
      const vertexNormal = new THREE.Vector3(normalAttribute.getX(vertex), normalAttribute.getY(vertex), normalAttribute.getZ(vertex));
      expect(vertexNormal.dot(normal)).toBeCloseTo(1, 6);
    }

    for (let first = 0; first < frameIndices.count; first += 3) {
      const a = new THREE.Vector3().fromBufferAttribute(positions, frameIndices.getX(first));
      const b = new THREE.Vector3().fromBufferAttribute(positions, frameIndices.getX(first + 1));
      const c = new THREE.Vector3().fromBufferAttribute(positions, frameIndices.getX(first + 2));
      expect(b.sub(a).cross(c.sub(a)).dot(normal)).toBeGreaterThan(0);
    }
  });

  it("orients indexed panes toward the source facade normal for direct and mirrored UV mappings", () => {
    for (const mirroredUv of [false, true]) {
      for (const frameDepthM of [0, 0.04]) {
        const result = extractCityWindowGeometry(wallGeometry({ rotated: true, mirroredUv }), [0],
          [centeredWindow], false, { frameDepthM });
        expect(result.windowCount).toBe(1);
        expect(result.panes.getAttribute("position").count).toBe(4);
        expect(result.panes.getIndex()?.count).toBe(6);
        expectPaneTrianglesFaceNormals(result.panes);
      }
    }
  });
});
