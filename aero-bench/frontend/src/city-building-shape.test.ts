import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { sourceBuildingGeometry, sourceBuildingRoofProjectedAreaM2,
  type SourceBuildingEnvelope, type SourceBuildingShape } from "./city-building-shape";

function rotatedBox(envelope: SourceBuildingEnvelope): SourceBuildingShape {
  const radians = THREE.MathUtils.degToRad(envelope.rotation_deg);
  const rotate = (x: number, y: number, z: number): [number, number, number] => {
    const point = new THREE.Vector3(x, y, z).applyAxisAngle(new THREE.Vector3(0, 1, 0), radians);
    return [point.x + envelope.x, point.y + envelope.base_y, point.z + envelope.z];
  };
  const points: number[] = [];
  const normals: number[] = [];
  const triangles: number[] = [];
  const addTriangle = (vertices: readonly [number, number, number][], normal: readonly [number, number, number]): void => {
    for (const vertex of vertices) {
      points.push(...rotate(...vertex));
      const direction = new THREE.Vector3(...normal).applyAxisAngle(new THREE.Vector3(0, 1, 0), radians);
      normals.push(direction.x, direction.y, direction.z);
      triangles.push(triangles.length);
    }
  };
  const left = -(envelope.width - 0.02) / 2, right = (envelope.width - 0.02) / 2;
  const front = -(envelope.depth - 0.02) / 2, back = (envelope.depth - 0.02) / 2;
  const bottom = 0, top = envelope.height;
  for (const [a, b, normal] of [
    [[left, front], [right, front], [0, 0, -1]],
    [[right, front], [right, back], [1, 0, 0]],
    [[right, back], [left, back], [0, 0, 1]],
    [[left, back], [left, front], [-1, 0, 0]],
  ] as const) {
    const p0: [number, number, number] = [a[0], bottom, a[1]];
    const p1: [number, number, number] = [b[0], bottom, b[1]];
    const p2: [number, number, number] = [b[0], top, b[1]];
    const p3: [number, number, number] = [a[0], top, a[1]];
    addTriangle([p0, p1, p2], normal);
    addTriangle([p0, p2, p3], normal);
  }
  const roofFirstTriangle = triangles.length / 3;
  const roof: [number, number, number][] = [
    [left, top, front], [right, top, front], [right, top, back], [left, top, back],
  ];
  addTriangle([roof[0]!, roof[1]!, roof[2]!], [0, 1, 0]);
  addTriangle([roof[0]!, roof[2]!, roof[3]!], [0, 1, 0]);
  const sourcePositions = new Float32Array(points), sourceNormals = new Float32Array(normals);
  const sourceIndices = new Uint32Array(triangles);
  return { ranges: [
    { positions: sourcePositions, normals: sourceNormals, indices: sourceIndices,
      firstTriangle: 0, endTriangle: roofFirstTriangle, roofMaterial: false },
    { positions: sourcePositions, normals: sourceNormals, indices: sourceIndices,
      firstTriangle: roofFirstTriangle, endTriangle: triangles.length / 3, roofMaterial: false },
  ] };
}

function pitchedRoof(envelope: SourceBuildingEnvelope): SourceBuildingShape {
  const radians = THREE.MathUtils.degToRad(envelope.rotation_deg);
  const rotate = (x: number, y: number, z: number): [number, number, number] => {
    const point = new THREE.Vector3(x, y, z).applyAxisAngle(new THREE.Vector3(0, 1, 0), radians);
    return [point.x + envelope.x, point.y + envelope.base_y, point.z + envelope.z];
  };
  const positions: number[] = [], normals: number[] = [], indices: number[] = [];
  const addTriangle = (vertices: readonly [number, number, number][],
                       normal: readonly [number, number, number]): void => {
    const direction = new THREE.Vector3(...normal).applyAxisAngle(new THREE.Vector3(0, 1, 0), radians);
    for (const vertex of vertices) {
      positions.push(...rotate(...vertex));
      normals.push(direction.x, direction.y, direction.z);
      indices.push(indices.length);
    }
  };
  const halfWidth = (envelope.width - 0.02) / 2;
  const halfDepth = (envelope.depth - 0.02) / 2;
  const bottom = 0, eave = envelope.height - 6, peak = envelope.height;
  const corners: readonly [number, number][] = [
    [-halfWidth, -halfDepth], [halfWidth, -halfDepth],
    [halfWidth, halfDepth], [-halfWidth, halfDepth],
  ];
  const wallNormals = [[0, 0, -1], [1, 0, 0], [0, 0, 1], [-1, 0, 0]] as const;
  for (let side = 0; side < corners.length; side++) {
    const a = corners[side]!, b = corners[(side + 1) % corners.length]!;
    const p0: [number, number, number] = [a[0], bottom, a[1]];
    const p1: [number, number, number] = [b[0], bottom, b[1]];
    const p2: [number, number, number] = [b[0], eave, b[1]];
    const p3: [number, number, number] = [a[0], eave, a[1]];
    addTriangle([p0, p1, p2], wallNormals[side]!);
    addTriangle([p0, p2, p3], wallNormals[side]!);
  }
  const roofFirstTriangle = indices.length / 3;
  const normalY = halfDepth / Math.hypot(halfDepth, peak - eave);
  const normalZ = (peak - eave) / Math.hypot(halfDepth, peak - eave);
  const frontA: [number, number, number] = [-halfWidth, eave, -halfDepth];
  const frontB: [number, number, number] = [halfWidth, eave, -halfDepth];
  const ridgeLeft: [number, number, number] = [-halfWidth, peak, 0];
  const ridgeRight: [number, number, number] = [halfWidth, peak, 0];
  addTriangle([frontA, ridgeLeft, ridgeRight], [0, normalY, -normalZ]);
  addTriangle([frontA, ridgeRight, frontB], [0, normalY, -normalZ]);
  const backA: [number, number, number] = [-halfWidth, eave, halfDepth];
  const backB: [number, number, number] = [halfWidth, eave, halfDepth];
  addTriangle([backA, backB, ridgeRight], [0, normalY, normalZ]);
  addTriangle([backA, ridgeRight, ridgeLeft], [0, normalY, normalZ]);
  const sourcePositions = new Float32Array(positions), sourceNormals = new Float32Array(normals);
  const sourceIndices = new Uint32Array(indices);
  return { ranges: [
    { positions: sourcePositions, normals: sourceNormals, indices: sourceIndices,
      firstTriangle: 0, endTriangle: roofFirstTriangle, roofMaterial: false },
    { positions: sourcePositions, normals: sourceNormals, indices: sourceIndices,
      firstTriangle: roofFirstTriangle, endTriangle: indices.length / 3, roofMaterial: true },
  ] };
}

describe("verified OSM building surfaces", () => {
  const envelope: SourceBuildingEnvelope = { building_id: "way/42", part: 0,
    x: 124.5, z: -37.25, width: 12.02, depth: 4.02, rotation_deg: 37,
    base_y: 2.5, height: 14 };

  it("uses metre UVs and rotates source triangles into the certified OBB frame", () => {
    const shape = rotatedBox(envelope);
    const sourcePositions = shape.ranges[0]!.positions;
    const sourceNormals = shape.ranges[0]!.normals;
    const sourceVertexKeys = new Set<string>();
    for (let index = 0; index < sourcePositions.length; index += 3) {
      sourceVertexKeys.add(`${sourcePositions[index]!.toFixed(4)},${sourcePositions[index + 1]!.toFixed(4)},${sourcePositions[index + 2]!.toFixed(4)}|${sourceNormals[index]!.toFixed(4)},${sourceNormals[index + 1]!.toFixed(4)},${sourceNormals[index + 2]!.toFixed(4)}`);
    }
    expect(sourceBuildingRoofProjectedAreaM2(shape)).toBeCloseTo(48);
    const geometry = sourceBuildingGeometry(shape, envelope);
    expect(geometry.groups.map(group => group.materialIndex)).toEqual([0, 1]);
    const bounds = geometry.boundingBox!;
    expect(bounds.max.x - bounds.min.x).toBeCloseTo(12);
    expect(bounds.max.y - bounds.min.y).toBeCloseTo(14);
    expect(bounds.max.z - bounds.min.z).toBeCloseTo(4);
    const position = geometry.getAttribute("position");
    const normal = geometry.getAttribute("normal");
    const yaw = THREE.MathUtils.degToRad(envelope.rotation_deg);
    const reconstructed = new THREE.Vector3();
    const reconstructedNormal = new THREE.Vector3();
    const rebuiltVertexKeys = new Set<string>();
    for (let index = 0; index < position.count; index++) {
      reconstructed.set(position.getX(index), position.getY(index), position.getZ(index))
        .applyAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
      reconstructedNormal.set(normal.getX(index), normal.getY(index), normal.getZ(index))
        .applyAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
      rebuiltVertexKeys.add(`${(reconstructed.x + envelope.x).toFixed(4)},${(reconstructed.y + envelope.base_y).toFixed(4)},${(reconstructed.z + envelope.z).toFixed(4)}|${reconstructedNormal.x.toFixed(4)},${reconstructedNormal.y.toFixed(4)},${reconstructedNormal.z.toFixed(4)}`);
    }
    expect(rebuiltVertexKeys).toEqual(sourceVertexKeys);
    const facadeUvs = geometry.getAttribute("uv");
    expect(Math.abs(facadeUvs.getX(1) - facadeUvs.getX(0))).toBeCloseTo(1);
    expect(Math.abs(facadeUvs.getY(2) - facadeUvs.getY(1))).toBeCloseTo(1);
    const facadeVertexCount = geometry.groups[0]!.count;
    const facadeV = Array.from({ length: facadeVertexCount }, (_, index) => facadeUvs.getY(index));
    expect(Math.min(...facadeV)).toBeCloseTo(0);
    expect(Math.max(...facadeV)).toBeCloseTo(1);
    geometry.dispose();
  });

  it("rejects source vertices outside the registered oriented collision envelope", () => {
    const shape = rotatedBox(envelope);
    const positions = shape.ranges[0]!.positions;
    positions[0] = positions[0]! + 20;
    expect(() => sourceBuildingGeometry(shape, envelope)).toThrow("exceed the verified collision envelope");
  });

  it("keeps low-normal pitched Roofing faces in the roof group without planar top triangles", () => {
    const shape = pitchedRoof(envelope);
    const roofRange = shape.ranges[1]!;
    const topY = envelope.base_y + envelope.height;
    for (let triangle = roofRange.firstTriangle; triangle < roofRange.endTriangle; triangle++) {
      const normalY = [0, 1, 2].reduce((sum, corner) => {
        const vertex = roofRange.indices[triangle * 3 + corner]! * 3;
        return sum + roofRange.normals[vertex + 1]!;
      }, 0) / 3;
      expect(normalY).toBeGreaterThan(0.228);
      expect(normalY).toBeLessThan(0.405);
      const heights = [0, 1, 2].map(corner => {
        const vertex = roofRange.indices[triangle * 3 + corner]! * 3;
        return roofRange.positions[vertex + 1]!;
      });
      expect(heights.some(height => Math.abs(height - topY) < 0.02)).toBe(true);
      expect(heights.every(height => Math.abs(height - topY) < 0.02)).toBe(false);
    }
    expect(sourceBuildingRoofProjectedAreaM2(shape)).toBeCloseTo(48);
    const geometry = sourceBuildingGeometry(shape, envelope);
    expect(geometry.groups.map(group => group.materialIndex)).toEqual([0, 1]);
    expect(geometry.groups[1]!.count).toBe(12);
    geometry.dispose();
  });
});
