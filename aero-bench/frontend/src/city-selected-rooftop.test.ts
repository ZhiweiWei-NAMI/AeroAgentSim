import { describe, expect, it } from "vitest";
import * as THREE from "three";
import type { SourceBuildingShape, SourceBuildingTriangleRange } from "./city-building-shape";
import type { RoofSupport } from "./city-selected-placement";
import {
  ROOF_FLAT_Y_SPAN_EPSILON_M,
  SUPPORT_PLANE_EPSILON_M,
  flatUpwardRooftopTriangle,
  forEachRooftopTriangle,
  largestSupportRectangleInTriangle,
  measureRoofSupport,
  measureRoofSupports,
  upwardFlatRooftopTriangles,
  validateRooftopVolume,
  type RooftopVolumeGeometry,
} from "./city-selected-rooftop";

type Triangle = readonly [number, number, number, number, number, number, number, number, number];

/** Build one source batch slice holding the given triangles (world X east, Y up, Z south). */
function batchRange(triangles: readonly Triangle[]): SourceBuildingTriangleRange {
  const positions = new Float32Array(triangles.length * 9);
  const normals = new Float32Array(triangles.length * 9);
  const indices = new Uint32Array(triangles.length * 3);
  triangles.forEach((tri, triangleIndex) => {
    for (let corner = 0; corner < 3; corner++) {
      positions[triangleIndex * 9 + corner * 3] = tri[corner * 3]!;
      positions[triangleIndex * 9 + corner * 3 + 1] = tri[corner * 3 + 1]!;
      positions[triangleIndex * 9 + corner * 3 + 2] = tri[corner * 3 + 2]!;
    }
    normals.fill(1, triangleIndex * 9 + 1, triangleIndex * 9 + 3);
    indices[triangleIndex * 3] = triangleIndex * 3;
    indices[triangleIndex * 3 + 1] = triangleIndex * 3 + 1;
    indices[triangleIndex * 3 + 2] = triangleIndex * 3 + 2;
  });
  return { positions, normals, indices, firstTriangle: 0, endTriangle: triangles.length, roofMaterial: true };
}

const shape = (...ranges: SourceBuildingTriangleRange[]): SourceBuildingShape => ({ ranges });

const envelope = (overrides: Partial<{ x: number; z: number; rotation_deg: number; base_y: number; height: number }> = {}) => ({
  building_id: "b-1", x: 50, z: -20, rotation_deg: 0, base_y: 0, height: 10, ...overrides,
});

/** Flat roof triangle spanning a 30×20 footprint plus a wall that bounds the
 * envelope in Y, exactly as a verified complete building would provide. The
 * triangles are encoded in WORLD metres around the envelope centre (50,-20):
 * building-local (-15,-10)..(15,10) maps to world (35,-30)..(65,-10). */
function supportedBuilding(): { mesh: ReadonlyMap<string, SourceBuildingTriangleRange[]>; roofTri: Triangle; wallTri: Triangle } {
  const roofTri: Triangle = [35, 10, -30, 65, 10, -10, 65, 10, -30];
  const wallTri: Triangle = [35, 0, -30, 65, 0, -30, 35, 10, -30];
  return {
    mesh: new Map([["b-1", [batchRange([roofTri, wallTri])]]]),
    roofTri, wallTri,
  };
}

/** The 14×10 vertiport placed at the measured support centre of supportedBuilding. */
function padGeometry(position: { x: number; z: number } = { x: 57.5, z: -25 }): RooftopVolumeGeometry {
  return { position, rotationDeg: 0, widthM: 14, depthM: 10, heightM: 4.5 };
}

const supportOf = (): RoofSupport => ({ buildingId: "b-1", x: 57.5, z: -25, widthM: 15, depthM: 10,
  rotationDeg: 0, topY: 10 });

/** Twice the signed area of triangle (a,b,c); same sign convention as the
 * implementation's point-in-triangle test. */
function triSign(ax: number, az: number, bx: number, bz: number, cx: number, cz: number): number {
  return (bx - ax) * (cz - az) - (bz - az) * (cx - ax);
}

/** True when point (px,pz) is inside or on triangle ABC (X east, Z south plane). */
function pointInTriangle(px: number, pz: number,
                         a: readonly [number, number], b: readonly [number, number],
                         c: readonly [number, number]): boolean {
  const d1 = triSign(px, pz, a[0], a[1], b[0], b[1]);
  const d2 = triSign(px, pz, b[0], b[1], c[0], c[1]);
  const d3 = triSign(px, pz, c[0], c[1], a[0], a[1]);
  const hasNegative = d1 < 0 || d2 < 0 || d3 < 0;
  const hasPositive = d1 > 0 || d2 > 0 || d3 > 0;
  return !(hasNegative && hasPositive);
}

/** World corners of an axis-aligned rectangle at (x,z) with rotationDeg (forward
 * THREE.Matrix4.makeRotationY, the same convention footprintCorners uses). */
function rectangleWorldCorners(x: number, z: number, rotationDeg: number,
                               widthM: number, depthM: number): readonly (readonly [number, number])[] {
  const radians = rotationDeg * Math.PI / 180;
  const cosine = Math.cos(radians), sine = Math.sin(radians);
  const halfWidth = widthM / 2, halfDepth = depthM / 2;
  return ([[-halfWidth, -halfDepth], [halfWidth, -halfDepth],
    [halfWidth, halfDepth], [-halfWidth, halfDepth]] as const).map(([lx, lz]) => [
    x + lx * cosine + lz * sine,
    z - lx * sine + lz * cosine,
  ] as const);
}

describe("city-selected-rooftop flat triangle measurement", () => {
  it("iterates only the triangles of the verified source range", () => {
    const tri: Triangle = [0, 10, 0, 10, 10, 0, 10, 10, 10];
    const range = batchRange([tri]);
    const seen: string[] = [];
    forEachRooftopTriangle({ ranges: [range] }, triangle => {
      seen.push(`${triangle.ax},${triangle.ay},${triangle.az}`);
    });
    expect(seen).toEqual(["0,10,0"]);
  });

  it("classifies an exactly horizontal triangle as a flat roof", () => {
    const tri: Triangle = [0, 10, 0, 20, 10, 20, 20, 10, 0];
    const flats = upwardFlatRooftopTriangles(shape(batchRange([tri])));
    expect(flats).toHaveLength(1);
    expect(flats[0]!.by).toBe(10);
    expect(flatUpwardRooftopTriangle(flats[0]!)).toBe(true);
    expect(ROOF_FLAT_Y_SPAN_EPSILON_M).toBeLessThanOrEqual(5e-4);
  });

  it("accepts a roof horizontal to float32 round-off (Y span within the noise band)", () => {
    // The source buffers are Float32 metres: a genuinely level roof edge can have
    // vertex Y differences up to ~1e-4 m. A 5e-5 m span is that noise, not a slope.
    const tri: Triangle = [0, 10, 0, 20, 10.00005, 20, 20, 10, 0];
    expect(upwardFlatRooftopTriangles(shape(batchRange([tri])))).toHaveLength(1);
  });

  it("rejects a genuinely sloped roof even at only 0.3 degrees", () => {
    // 0.3° pitch over a 20 m edge drops ~0.105 m, far above the 1e-4 m noise band.
    const drop = Math.tan(0.3 * Math.PI / 180) * 20;
    expect(drop).toBeGreaterThan(0.01);
    const sloped: Triangle = [0, 10, 0, 20, 10, 0, 20, 10 + drop, 20];
    const flats = upwardFlatRooftopTriangles(shape(batchRange([sloped])));
    expect(flats).toHaveLength(0);
    const wall: Triangle = [0, 0, 0, 20, 0, 0, 0, 12, 0];
    // The envelope top is even the roof top, but the sloped plane is never a support.
    const support = measureRoofSupport("b-1", envelope(),
      shape(batchRange([sloped, wall])));
    expect(support).toBeNull();
  });

  it("rejects sloped and vertical faces as support geometry", () => {
    const sloped: Triangle = [0, 10, 0, 20, 12, 0, 20, 10, 20]; // ~5% pitch
    const vertical: Triangle = [0, 0, 0, 0, 10, 0, 10, 10, 0];
    const flats = upwardFlatRooftopTriangles(shape(batchRange([sloped, vertical])));
    expect(flats).toHaveLength(0);
  });

  it("measures a one-triangle flat roof support at the real plane height", () => {
    const { mesh } = supportedBuilding();
    const support = measureRoofSupport("b-1", envelope(), { ranges: mesh.get("b-1")! });
    expect(support).not.toBeNull();
    expect(support!.buildingId).toBe("b-1");
    expect(support!.x).toBeCloseTo(57.5, 5);
    expect(support!.z).toBeCloseTo(-25, 5);
    expect(support!.widthM).toBeGreaterThanOrEqual(14.9);
    expect(support!.depthM).toBeGreaterThanOrEqual(9.9);
    expect(support!.rotationDeg).toBe(0);
    expect(support!.topY).toBeCloseTo(10, 5);
  });

  it("respects the building rotation when measuring support axes", () => {
    const roofLocal: readonly (readonly [number, number])[] = [[-15, -10], [15, 10], [15, -10]];
    const wallLocal: readonly (readonly [number, number])[] = [[-15, -10], [15, -10], [-15, 10]];
    const radians = (30 * Math.PI) / 180;
    // THREE.Matrix4.makeRotationY(+30°): world = R(+θ)·local, X east / Z south.
    const rotate = (lx: number, lz: number): [number, number] => [
      50 + lx * Math.cos(radians) + lz * Math.sin(radians),
      -20 - lx * Math.sin(radians) + lz * Math.cos(radians),
    ];
    const [ax, az] = rotate(roofLocal[0]![0], roofLocal[0]![1]);
    const [bx, bz] = rotate(roofLocal[1]![0], roofLocal[1]![1]);
    const [cx, cz] = rotate(roofLocal[2]![0], roofLocal[2]![1]);
    const [wax, waz] = rotate(wallLocal[0]![0], wallLocal[0]![1]);
    const [wbx, wbz] = rotate(wallLocal[1]![0], wallLocal[1]![1]);
    const [wcx, wcz] = rotate(wallLocal[2]![0], wallLocal[2]![1]);
    const mesh = new Map([["b-1", [batchRange([
      [ax, 10, az, bx, 10, bz, cx, 10, cz],
      [wax, 0, waz, wbx, 0, wbz, wcx, 10, wcz],
    ])]]]);
    const support = measureRoofSupport("b-1", envelope({ rotation_deg: 30 }), { ranges: mesh.get("b-1")! });
    expect(support).not.toBeNull();
    expect(support!.rotationDeg).toBe(30);
    expect(support!.widthM).toBeGreaterThanOrEqual(14.9);
    expect(support!.depthM).toBeGreaterThanOrEqual(9.9);
    expect(support!.topY).toBeCloseTo(10, 5);
  });

  it("proves every advertised support corner and every snapped facility corner is inside the ORIGINAL rotated world triangle", () => {
    // Genuinely asymmetric building-local triangle placed with THREE.Matrix4.makeRotationY.
    const localRoof: readonly (readonly [number, number])[] = [[-12, -10], [14, 10], [14, -10]];
    const wallLocal: readonly (readonly [number, number])[] = [[-13, -11], [15, -11], [-13, 11]];
    const radians = (30 * Math.PI) / 180;
    const rotation = new THREE.Matrix4().makeRotationY(radians);
    const worldOf = (lx: number, ly: number, lz: number): [number, number, number] => {
      const world = new THREE.Vector3(lx, ly, lz).applyMatrix4(rotation);
      return [50 + world.x, world.y, -20 + world.z];
    };
    const [ax, _, az] = worldOf(localRoof[0]![0], 10, localRoof[0]![1]);
    const [bx, __, bz] = worldOf(localRoof[1]![0], 10, localRoof[1]![1]);
    const [cx, ___, cz] = worldOf(localRoof[2]![0], 10, localRoof[2]![1]);
    const [wax, waz] = (() => { const [x, , z] = worldOf(wallLocal[0]![0], 0, wallLocal[0]![1]); return [x, z]; })();
    const [wbx, wbz] = (() => { const [x, , z] = worldOf(wallLocal[1]![0], 0, wallLocal[1]![1]); return [x, z]; })();
    const [wcx, wcz] = (() => { const [x, , z] = worldOf(wallLocal[2]![0], 10, wallLocal[2]![1]); return [x, z]; })();
    const wallY: Triangle = [wax, 0, waz, wbx, 0, wbz, wcx, 10, wcz];
    const roofTri: Triangle = [ax, 10, az, bx, 10, bz, cx, 10, cz];
    const mesh = new Map([["b-1", [batchRange([roofTri, wallY])]]]);

    const support = measureRoofSupport("b-1", envelope({ rotation_deg: 30 }), { ranges: mesh.get("b-1")! });
    expect(support).not.toBeNull();
    expect(support!.rotationDeg).toBe(30);
    expect(support!.topY).toBeCloseTo(10, 4);
    // The advertised support is large enough for a 8×5 pad.
    expect(support!.widthM).toBeGreaterThanOrEqual(8);
    expect(support!.depthM).toBeGreaterThanOrEqual(5);

    const originalWorld: readonly (readonly [number, number])[] = [[ax, az], [bx, bz], [cx, cz]];
    for (const [x, z] of rectangleWorldCorners(support!.x, support!.z, support!.rotationDeg,
      support!.widthM, support!.depthM)) {
      expect(pointInTriangle(x, z, originalWorld[0]!, originalWorld[1]!, originalWorld[2]!),
        `support corner (${x},${z}) escaped the original world triangle`).toBe(true);
    }
    // A facility snapped to the verified support and coaxial with it is strictly
    // inside the support rectangle, so its corners are inside the original triangle.
    const snapped = { x: support!.x, z: support!.z, rotationDeg: 30, widthM: 8, depthM: 5 };
    for (const [x, z] of rectangleWorldCorners(snapped.x, snapped.z, snapped.rotationDeg,
      snapped.widthM, snapped.depthM)) {
      expect(pointInTriangle(x, z, originalWorld[0]!, originalWorld[1]!, originalWorld[2]!),
        `snapped facility corner (${x},${z}) escaped the original world triangle`).toBe(true);
    }
  });

  it("never expands support beyond the original triangle through axis snapping", () => {
    // Float32-like jitter pulls the two left vertices 0.6 mm apart on the X axis;
    // the snap fuses them to a common mean, but the proof then re-checks the
    // rectangle corners against the ORIGINAL pre-snap half-planes.
    const tri: Triangle = [0.0004, 10, 0, 10, 10, 0.0006, 10, 10, 10];
    const rect = largestSupportRectangleInTriangle(
      { ax: tri[0]!, ay: tri[1]!, az: tri[2]!, bx: tri[3]!, by: tri[4]!, bz: tri[5]!,
        cx: tri[6]!, cy: tri[7]!, cz: tri[8]! }, 0, 0, 0);
    expect(rect).not.toBeNull();
    const original: readonly (readonly [number, number])[] = [[tri[0]!, tri[2]!], [tri[3]!, tri[5]!], [tri[6]!, tri[8]!]];
    const corners: readonly (readonly [number, number])[] = [
      [rect!.x0, rect!.z0], [rect!.x1, rect!.z0], [rect!.x1, rect!.z1], [rect!.x0, rect!.z1],
    ];
    for (const [x, z] of corners) {
      expect(pointInTriangle(x, z, original[0]!, original[1]!, original[2]!),
        `returned corner (${x},${z}) is outside the original jittered triangle`).toBe(true);
    }
  });

  it("refuses a mesh that does not bound the verified envelope in Y", () => {
    const tri: Triangle = [0, 20, 0, 20, 20, 0, 20, 20, 20]; // roof at 20, envelope says height 10
    const support = measureRoofSupport("b-1", envelope(), shape(batchRange([tri])));
    expect(support).toBeNull();
  });

  it("returns null for a building with no flat upward roof triangle", () => {
    const sloped: Triangle = [0, 10, 0, 20, 12, 0, 20, 10, 20];
    const wall: Triangle = [0, 0, 0, 20, 0, 0, 0, 12, 0];
    const support = measureRoofSupport("b-1", envelope(), shape(batchRange([sloped, wall])));
    expect(support).toBeNull();
  });

  it("finds the largest axis-aligned square in a right-isoceles triangle", () => {
    // triangle (0,0),(10,0),(0,10): true max inscribed square is 5×5 at the corner.
    const rect = largestSupportRectangleInTriangle(
      { ax: 0, ay: 0, az: 0, bx: 10, by: 0, bz: 0, cx: 0, cy: 0, cz: 10 }, 0, 0, 0);
    expect(rect).not.toBeNull();
    expect(rect!.x1 - rect!.x0).toBeGreaterThanOrEqual(4.9);
    expect(rect!.z1 - rect!.z0).toBeGreaterThanOrEqual(4.9);
    expect(rect!.area).toBeGreaterThanOrEqual(24);
  });

  it("captures the full 10×10 inset of a 20-tall symmetric roof triangle", () => {
    // triangle (0,0),(20,0),(10,20): the max-axis rectangle is width 10, height 10.
    const rect = largestSupportRectangleInTriangle(
      { ax: 0, ay: 0, az: 0, bx: 20, by: 0, bz: 0, cx: 10, cy: 0, cz: 20 }, 0, 0, 0);
    expect(rect).not.toBeNull();
    expect(rect!.x1 - rect!.x0).toBeGreaterThanOrEqual(9.5);
    expect(rect!.z1 - rect!.z0).toBeGreaterThanOrEqual(9.5);
  });

  it("never measures support from a non-complete or hidden building in the mesh", () => {
    // `measureRoofSupports` only trusts the verified complete envelopes it is
    // given: a hidden/non-complete building with a flat roof in the mesh map is
    // never turned into a support.
    const complete = supportedBuilding();
    const hiddenFlat: Triangle = [35, 10, -30, 65, 10, -10, 65, 10, -30];
    const mesh = new Map([
      ["b-1", [batchRange([complete.roofTri, complete.wallTri])]],
      ["b-hidden", [batchRange([hiddenFlat])]],
    ]);
    const supports = measureRoofSupports(
      [{ building_id: "b-1", rotation_deg: 0, x: 50, z: -20, base_y: 0, height: 10 }], mesh);
    expect(supports.map(support => support.buildingId)).toEqual(["b-1"]);
  });
});

describe("city-selected-rooftop volume proof", () => {
  it("accepts a pad ON the coplanar support plane", () => {
    const { mesh } = supportedBuilding();
    expect(validateRooftopVolume(padGeometry(), supportOf(), mesh)).toEqual([]);
  });

  it("rejects a parapet rising above the support plane inside the pad volume", () => {
    const base = supportedBuilding();
    const parapet: Triangle = [54, 11, -28, 62, 11, -22, 62, 11, -28];
    const mesh = new Map([["b-1", [batchRange([base.roofTri, base.wallTri, parapet])]]]);
    const issues = validateRooftopVolume(padGeometry(), supportOf(), mesh);
    expect(issues.length).toBeGreaterThan(0);
    expect(issues.join("；")).toContain("高于支撑面");
  });

  it("rejects a real one-centimetre-thick obstacle above the support plane", () => {
    // A 1 cm deck is 100× the float32 noise band and 10× the plane epsilon; it is
    // a genuine obstacle and must be rejected (a former 2 cm tolerance hid it).
    expect(SUPPORT_PLANE_EPSILON_M).toBeLessThan(0.005);
    const base = supportedBuilding();
    const thin: Triangle = [54, 10.01, -28, 62, 10.01, -22, 62, 10.01, -28];
    const mesh = new Map([["b-1", [batchRange([base.roofTri, base.wallTri, thin])]]]);
    const issues = validateRooftopVolume(padGeometry(), supportOf(), mesh);
    expect(issues.length).toBeGreaterThan(0);
    expect(issues.join("；")).toContain("高于支撑面");
  });

  it("accepts a genuinely coplanar split roof triangle at the exact plane", () => {
    // Same flat plane, same y=10: the split roof triangle is surface, not obstacle.
    const base = supportedBuilding();
    const coplanar: Triangle = [52, 10, -28, 62, 10, -22, 62, 10, -28];
    const mesh = new Map([["b-1", [batchRange([base.roofTri, base.wallTri, coplanar])]]]);
    expect(validateRooftopVolume(padGeometry(), supportOf(), mesh)).toEqual([]);
  });

  it("ignores wall geometry below the support plane", () => {
    const base = supportedBuilding();
    const low: Triangle = [54, 0, -28, 62, 0, -28, 54, 5, -22];
    const mesh = new Map([["b-1", [batchRange([base.roofTri, base.wallTri, low])]]]);
    expect(validateRooftopVolume(padGeometry(), supportOf(), mesh)).toEqual([]);
  });

  it("rejects a neighbouring taller building whose wall enters the pad volume", () => {
    const base = supportedBuilding();
    const neighbourWall: Triangle = [56, 11, -26, 60, 12, -26, 56, 11, -22];
    const mesh = new Map([
      ["b-1", [batchRange([base.roofTri, base.wallTri])]],
      ["b-2", [batchRange([neighbourWall])]],
    ]);
    const issues = validateRooftopVolume(padGeometry(), supportOf(), mesh);
    expect(issues.length).toBeGreaterThan(0);
  });
});
