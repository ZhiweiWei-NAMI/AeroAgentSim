import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { createSidewalkHeightSampler, type SidewalkHeightPolygon } from "./city-sidewalk-height";

const SIDEWALK_HEIGHT_M = 0.225;
const ROAD_HEIGHT_M = 0.075;

function flatFaceGeometry(outline: readonly (readonly [number, number])[], heightM: number): THREE.BufferGeometry {
  const contour = outline.map(([x, z]) => new THREE.Vector2(x, z));
  const triangles = THREE.ShapeUtils.triangulateShape(contour, []);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(
    outline.flatMap(([x, z]) => [x, heightM, z]), 3));
  geometry.setIndex(triangles.flat());
  return geometry;
}

function expectSamplerMatchesFlatFace(sample: (x: number, z: number, sourceGroundY: number) => number,
                                      geometry: THREE.BufferGeometry): void {
  const positions = geometry.getAttribute("position");
  const indices = geometry.getIndex()!;
  for (let first = 0; first < indices.count; first += 3) {
    const a = new THREE.Vector3().fromBufferAttribute(positions, indices.getX(first));
    const b = new THREE.Vector3().fromBufferAttribute(positions, indices.getX(first + 1));
    const c = new THREE.Vector3().fromBufferAttribute(positions, indices.getX(first + 2));
    const x = (a.x + b.x + c.x) / 3;
    const z = (a.z + b.z + c.z) / 3;
    const faceHeight = (a.y + b.y + c.y) / 3;
    expect(sample(x, z, 0)).toBeCloseTo(faceHeight, 6);
  }
}

describe("city sidewalk height sampler", () => {
  it("raises points inside outlines while respecting polygon holes and their boundaries", () => {
    const polygons: SidewalkHeightPolygon[] = [{
      outline: [[-10, -10], [10, -10], [10, 10], [-10, 10]],
      holes: [[[-2, -2], [2, -2], [2, 2], [-2, 2]]],
    }];
    const sample = createSidewalkHeightSampler(polygons, SIDEWALK_HEIGHT_M, ROAD_HEIGHT_M);

    expect(sample(0, 0, 0)).toBe(ROAD_HEIGHT_M);
    expect(sample(2, 0, 0)).toBe(ROAD_HEIGHT_M);
    expect(sample(3, 0, 0)).toBe(SIDEWALK_HEIGHT_M);
    expect(sample(-10, 0, 0)).toBe(SIDEWALK_HEIGHT_M);
  });

  it("indexes polygons across positive and negative grid-cell boundaries in metre coordinates", () => {
    const polygons: SidewalkHeightPolygon[] = [
      { outline: [[15, -2], [17, -2], [17, 2], [15, 2]], holes: [] },
      { outline: [[-17, -2], [-15, -2], [-15, 2], [-17, 2]], holes: [] },
    ];
    const sample = createSidewalkHeightSampler(polygons, SIDEWALK_HEIGHT_M, ROAD_HEIGHT_M);

    expect(sample(16.5, 0, 0)).toBe(SIDEWALK_HEIGHT_M);
    expect(sample(-16.5, 0, 0)).toBe(SIDEWALK_HEIGHT_M);
    expect(sample(14.9, 0, 0)).toBe(ROAD_HEIGHT_M);
  });

  it("keeps road height outside walkbed and preserves higher source ground", () => {
    const walkbed: SidewalkHeightPolygon[] = [{
      outline: [[-2, -2], [2, -2], [2, 2], [-2, 2]], holes: [],
    }];
    const sample = createSidewalkHeightSampler(walkbed, SIDEWALK_HEIGHT_M, ROAD_HEIGHT_M);

    expect(sample(5, 0, 0)).toBe(ROAD_HEIGHT_M);
    expect(sample(0, 0, 0.4)).toBe(0.4);
    expect(sample(5, 0, 0.4)).toBe(0.4);
  });

  it("matches the generated flat walkbed and road face heights at every triangle centroid", () => {
    const walkbed: SidewalkHeightPolygon[] = [{
      outline: [[-10, -10], [10, -10], [10, 10], [-10, 10]], holes: [],
    }];
    const sample = createSidewalkHeightSampler(walkbed, SIDEWALK_HEIGHT_M, ROAD_HEIGHT_M);
    const walkbedGeometry = flatFaceGeometry(walkbed[0]!.outline, SIDEWALK_HEIGHT_M);
    const roadGeometry = flatFaceGeometry([[12, -2], [16, -2], [16, 2], [12, 2]], ROAD_HEIGHT_M);

    expectSamplerMatchesFlatFace(sample, walkbedGeometry);
    expectSamplerMatchesFlatFace(sample, roadGeometry);
  });
});
