import * as THREE from "three";

/** Triangle slice retained from a hash-verified OSM2World building batch. */
export interface SourceBuildingTriangleRange {
  readonly positions: Float32Array;
  readonly normals: Float32Array;
  readonly indices: Uint32Array;
  readonly firstTriangle: number;
  readonly endTriangle: number;
  readonly roofMaterial: boolean;
}

export interface SourceBuildingShape {
  readonly ranges: readonly SourceBuildingTriangleRange[];
}

export interface SourceBuildingEnvelope {
  readonly building_id: string;
  readonly part: 0;
  readonly x: number;
  readonly z: number;
  readonly width: number;
  readonly depth: number;
  readonly rotation_deg: number;
  readonly base_y: number;
  readonly height: number;
}

function validateRange(range: SourceBuildingTriangleRange): void {
  if (!Number.isSafeInteger(range.firstTriangle) || !Number.isSafeInteger(range.endTriangle)
      || range.firstTriangle < 0 || range.endTriangle <= range.firstTriangle
      || typeof range.roofMaterial !== "boolean"
      || range.endTriangle * 3 > range.indices.length
      || range.positions.length % 3 !== 0 || range.normals.length !== range.positions.length) {
    throw new Error("Verified building triangle range is outside its source batch");
  }
}

function visitTriangles(shape: SourceBuildingShape,
                        visit: (positionOffset: number, normalOffset: number,
                                source: SourceBuildingTriangleRange) => void): number {
  let count = 0;
  for (const range of shape.ranges) {
    validateRange(range);
    for (let triangle = range.firstTriangle; triangle < range.endTriangle; triangle++) {
      const indexOffset = triangle * 3;
      for (let corner = 0; corner < 3; corner++) {
        const vertex = range.indices[indexOffset + corner]!;
        if (vertex * 3 + 2 >= range.positions.length) {
          throw new Error("Verified building triangle references a missing source vertex");
        }
        const positionOffset = vertex * 3;
        const normalOffset = vertex * 3;
        if (![range.positions[positionOffset], range.positions[positionOffset + 1],
          range.positions[positionOffset + 2], range.normals[normalOffset],
          range.normals[normalOffset + 1], range.normals[normalOffset + 2]].every(Number.isFinite)) {
          throw new Error("Verified building triangle contains a non-finite vertex");
        }
      }
      visit(indexOffset, 0, range);
      count++;
    }
  }
  return count;
}

const UPWARD_ROOF_NORMAL_Y = 0.55;

function isSourceRoof(normalY: number, range: SourceBuildingTriangleRange): boolean {
  return range.roofMaterial || Math.abs(normalY) >= UPWARD_ROOF_NORMAL_Y;
}

function projectedTriangleAreaXZ(points: readonly (readonly [number, number, number])[]): number {
  return Math.abs((points[1]![0] - points[0]![0]) * (points[2]![2] - points[0]![2])
    - (points[1]![2] - points[0]![2]) * (points[2]![0] - points[0]![0])) / 2;
}

/** Projected area of verified upward source faces classified as roof. */
export function sourceBuildingRoofProjectedAreaM2(shape: SourceBuildingShape): number {
  let area = 0;
  visitTriangles(shape, (indexOffset, _normalOffset, range) => {
    const points = [0, 1, 2].map(corner => {
      const vertex = range.indices[indexOffset + corner]! * 3;
      return [range.positions[vertex]!, range.positions[vertex + 1]!, range.positions[vertex + 2]!] as const;
    });
    const normals = [0, 1, 2].map(corner => {
      const vertex = range.indices[indexOffset + corner]! * 3;
      return range.normals[vertex + 1]!;
    });
    const averageNormalY = (normals[0]! + normals[1]! + normals[2]!) / 3;
    if (!isSourceRoof(averageNormalY, range) || averageNormalY <= 0) return;
    area += projectedTriangleAreaXZ(points);
  });
  return area;
}

/** Creates exact source faces with metre-scaled atlas UVs and no added infill. */
export function sourceBuildingGeometry(shape: SourceBuildingShape,
                                      envelope: SourceBuildingEnvelope): THREE.BufferGeometry {
  if (![envelope.x, envelope.z, envelope.width, envelope.depth, envelope.rotation_deg,
    envelope.base_y, envelope.height].every(Number.isFinite)
      || envelope.width <= 0 || envelope.depth <= 0 || envelope.height <= 0) {
    throw new Error("Source building geometry has an invalid oriented envelope");
  }
  const facade: number[] = [];
  const facadeNormals: number[] = [];
  const facadeUvs: number[] = [];
  const roof: number[] = [];
  const roofNormals: number[] = [];
  const roofUvs: number[] = [];
  const radians = THREE.MathUtils.degToRad(envelope.rotation_deg);
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  let verifiedRoofArea = 0;
  const append = (target: number[], value: number): void => {
    if (!Number.isFinite(value)) throw new Error("Source building geometry contains a non-finite coordinate");
    target.push(value);
  };
  const triangleCount = visitTriangles(shape, (indexOffset, _normalOffset, range) => {
    const cornerData = [0, 1, 2].map(corner => {
      const vertex = range.indices[indexOffset + corner]! * 3;
      return {
        x: range.positions[vertex]!, y: range.positions[vertex + 1]!, z: range.positions[vertex + 2]!,
        nx: range.normals[vertex]!, ny: range.normals[vertex + 1]!, nz: range.normals[vertex + 2]!,
      };
    });
    const averageNormalY = (cornerData[0]!.ny + cornerData[1]!.ny + cornerData[2]!.ny) / 3;
    const isRoof = isSourceRoof(averageNormalY, range);
    if (isRoof && averageNormalY > 0) {
      verifiedRoofArea += projectedTriangleAreaXZ(cornerData.map(vertex => [vertex.x, vertex.y, vertex.z] as const));
    }
    const vertices = isRoof ? roof : facade;
    const normals = isRoof ? roofNormals : facadeNormals;
    const uvs = isRoof ? roofUvs : facadeUvs;
    const normalX = cornerData.reduce((sum, vertex) => sum + vertex.nx, 0) / 3;
    const normalZ = cornerData.reduce((sum, vertex) => sum + vertex.nz, 0) / 3;
    const horizontalNormalLength = Math.hypot(normalX, normalZ);
    const tangentX = horizontalNormalLength > 1e-8 ? -normalZ / horizontalNormalLength : 1;
    const tangentZ = horizontalNormalLength > 1e-8 ? normalX / horizontalNormalLength : 0;
    for (const vertex of cornerData) {
      const deltaX = vertex.x - envelope.x;
      const deltaZ = vertex.z - envelope.z;
      append(vertices, cosine * deltaX - sine * deltaZ);
      append(vertices, vertex.y - envelope.base_y);
      append(vertices, sine * deltaX + cosine * deltaZ);
      append(normals, cosine * vertex.nx - sine * vertex.nz);
      append(normals, vertex.ny);
      append(normals, sine * vertex.nx + cosine * vertex.nz);
      if (isRoof) {
        append(uvs, vertex.x / 3); append(uvs, vertex.z / 3);
      } else {
        append(uvs, (vertex.x * tangentX + vertex.z * tangentZ) / 12);
        append(uvs, (vertex.y - envelope.base_y) / 14);
      }
    }
  });
  if (triangleCount === 0 || facade.length + roof.length === 0 || verifiedRoofArea <= 0.05) {
    throw new Error("Complete OSM building has no upward roof faces with positive projected area");
  }
  const geometry = new THREE.BufferGeometry();
  const positions = facade.concat(roof);
  const normals = facadeNormals.concat(roofNormals);
  const uvs = facadeUvs.concat(roofUvs);
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  const facadeVertexCount = facade.length / 3;
  const roofVertexCount = roof.length / 3;
  if (facadeVertexCount > 0) geometry.addGroup(0, facadeVertexCount, 0);
  if (roofVertexCount > 0) geometry.addGroup(facadeVertexCount, roofVertexCount, 1);
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  const measured = geometry.boundingBox;
  if (measured === null || measured.min.x < -envelope.width / 2 - 0.002
      || measured.max.x > envelope.width / 2 + 0.002
      || measured.min.y < -0.002 || measured.max.y > envelope.height + 0.002
      || measured.min.z < -envelope.depth / 2 - 0.002
      || measured.max.z > envelope.depth / 2 + 0.002) {
    geometry.dispose();
    throw new Error("Source building vertices exceed the verified collision envelope");
  }
  return geometry;
}
