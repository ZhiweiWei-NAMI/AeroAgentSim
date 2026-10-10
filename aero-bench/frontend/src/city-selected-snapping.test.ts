// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { SelectedScenarioFacility } from "./city-selected-scenario";
import type { AirspacePolygon, PlacementBox, PointXZ, RoadPolygon } from "./city-workspace-geometry";
import type { RoofSupport } from "./city-selected-placement";
import type { SourceBuildingTriangleRange } from "./city-building-shape";
import {
  applySnapCandidate, proposeGroundSnap, proposeRoofSnap,
  type SnapContext, type SnapExtent,
} from "./city-selected-snapping";

/** Scene-bound source triangle slice for one building, as loadPackedScene builds it. */
function meshRange(triangles: readonly (readonly [number, number, number, number,
  number, number, number, number, number])[]): SourceBuildingTriangleRange[] {
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
  return [{ positions, normals, indices, firstTriangle: 0, endTriangle: triangles.length,
    roofMaterial: true }];
}

const extent: SnapExtent = { minX: -500, maxX: 500, minZ: -400, maxZ: 400 };

function facility(position: PointXZ, rotationDeg = 0, overrides: Partial<SelectedScenarioFacility> = {}):
    SelectedScenarioFacility {
  return {
    id: "fac", name: "充电站", kind: "charger",
    placement: "ground", buildingId: null, supportHeightM: null,
    position, rotationDeg, widthM: 10, depthM: 8, heightM: 3.2,
    landing: null, cargo: null,
    charging: { slots: 1, powerW: 5000, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" },
    ...overrides,
  };
}

function box(id: string, x: number, z: number, widthM: number, depthM: number,
             heightM = 20, rotationDeg = 0): PlacementBox {
  return { id, x, z, widthM, depthM, heightM, rotationDeg, baseY: 0 };
}

function context(overrides: Partial<SnapContext> = {}): SnapContext {
  return {
    extent, buildings: [], staticObstacles: [], roads: [], facilities: [], airspace: [],
    roofSupports: [],
    ...overrides,
  };
}

function road(bounds: readonly (readonly [number, number])[], id = "road-a"): RoadPolygon {
  return { id, outline: bounds.map(([x, z]) => ({ x, z })), holes: [] };
}

describe("selected-city snapping", () => {
  it("returns a clear ground proposal at the requested point without claiming verified land use", () => {
    const candidate = proposeGroundSnap(facility({ x: 20, z: -30 }), context(), { x: 20, z: -30 });
    expect(candidate.legal).toBe(true);
    expect(candidate.position).toEqual({ x: 20, z: -30 });
    expect(candidate.displacementM).toBe(0);
    expect(candidate.rotationDeg).toBe(0);
    expect(candidate.landUseVerified).toBe(false);
    expect(candidate.supportHeightM).toBeNull();
    expect(candidate.reason).toContain("土地用途未被核验");
  });

  it("displaces a click inside a verified building to the nearest clear position", () => {
    // Building spans x [35,55]; the nearest position that fully clears its 20×20
    // footprint is x=60 (10 m east), so the naive 1 m nudges still collide.
    const candidate = proposeGroundSnap(facility({ x: 50, z: 50 }), context({
      buildings: [box("b-1", 45, 50, 20, 20)],
    }), { x: 50, z: 50 });
    expect(candidate.legal).toBe(true);
    expect(candidate.displacementM).toBeGreaterThan(0);
    // The proposal must fully clear the building footprint, not just nudge the centre.
    expect(candidate.position.x).not.toBe(50);
  });

  it("reports an illegal proposal that cannot clear a fully enclosing obstacle ring", () => {
    const candidate = proposeGroundSnap(facility({ x: 0, z: 0 }), context({
      // Four 10 m-thick slabs at ±6 overlap at every corner, so the courtyard is
      // fully sealed and any escape must pass >12 m outside the click.
      buildings: [
        box("n", 0, -6, 100, 10), box("s", 0, 6, 100, 10),
        box("e", 6, 0, 10, 100), box("w", -6, 0, 10, 100),
      ],
    }), { x: 0, z: 0 });
    expect(candidate.legal).toBe(false);
    expect(candidate.issues.length).toBeGreaterThan(0);
    expect(candidate.reason).toContain("未找到合法位置");
  });

  it("validates the full rotated footprint and falls back to a clear rotation", () => {
    // At the same centre the 45° footprint crosses the 12×12 building (its near
    // corner reaches x=5.64/z=-6.36) while the requested position is clearly
    // separated on the east axis at 0°.
    const building = box("b-1", 0, 0, 12, 12);
    const rotated = proposeGroundSnap(facility({ x: 12, z: 0 }, 45), context({
      buildings: [building],
    }), { x: 12, z: 0 });
    expect(rotated.legal).toBe(true);
    expect(rotated.rotationDeg).toBe(0);
    expect(rotated.displacementM).toBe(0);
    // Sanity: 45° at the same centre really does collide there (radius 0 keeps the
    // probe at the requested point instead of snapping to a nearby clear one).
    const stuck = proposeGroundSnap(facility({ x: 12, z: 0 }, 45), context({
      buildings: [building],
    }), { x: 12, z: 0 }, { rotationsDeg: [45], radiusM: 0 });
    expect(stuck.legal).toBe(false);
  });

  it("avoids a motor road pad and reports the empty-land reason without calling it verified", () => {
    const roadPoly = road([[-10, -5], [10, -5], [10, 5], [-10, 5]], "road.mid");
    const candidate = proposeGroundSnap(facility({ x: 0, z: 0 }), context({
      roads: [roadPoly],
    }), { x: 0, z: 0 });
    expect(candidate.legal).toBe(true);
    expect(candidate.position.z).not.toBe(0);
    expect(candidate.reason).toContain("土地用途未被核验");
  });

  it("keeps the full footprint inside the selected ENU bounds", () => {
    // A 40×40 m facility centred at east 490 crosses the 500 m bound in every rotation.
    const candidate = proposeGroundSnap(facility({ x: 490, z: 0 }, 0, { widthM: 40, depthM: 40 }),
      context(), { x: 490, z: 0 });
    expect(candidate.legal).toBe(true);
    expect(candidate.position.x + 20).toBeLessThanOrEqual(500);
    expect(candidate.position.x).toBe(480);
  });

  it("flags a click so far outside the bounds as unresolvable", () => {
    const candidate = proposeGroundSnap(facility({ x: 600, z: 0 }), context(), { x: 600, z: 0 });
    expect(candidate.legal).toBe(false);
    expect(candidate.issues.some(issue => issue.includes("超出选区"))).toBe(true);
  });

  it("moves a second facility off an existing facility", () => {
    const existing = facility({ x: 0, z: 0 }, 0, { id: "fac-a" });
    const candidate = proposeGroundSnap(facility({ x: 0, z: 0 }), context({
      facilities: [existing],
    }), { x: 0, z: 0 });
    expect(candidate.legal).toBe(true);
    expect(candidate.displacementM).toBeGreaterThan(0);
  });

  it("rejects rooftop when the source cannot prove a flat roof surface", () => {
    const candidate = proposeRoofSnap(facility({ x: 0, z: 0 }), context({
      roofSupports: [],
    }), "b-1", { x: 50, z: 50 });
    expect(candidate.legal).toBe(false);
    expect(candidate.issues).toEqual(["rooftop_unsupported"]);
    expect(candidate.reason).toContain("不虚构屋顶高度");
    expect(candidate.supportHeightM).toBeNull();
  });

  it("places a rooftop site on a verified roof support at its measured top height", () => {
    const support: RoofSupport = { buildingId: "b-1", x: 50, z: 50, widthM: 30, depthM: 30,
      rotationDeg: 0, topY: 24 };
    const candidate = proposeRoofSnap(facility({ x: 0, z: 0 }), context({
      roofSupports: [support],
    }), "b-1", { x: 48, z: 52 });
    expect(candidate.legal).toBe(true);
    expect(candidate.position).toEqual({ x: 50, z: 50 });
    expect(candidate.supportHeightM).toBe(24);
    expect(candidate.landUseVerified).toBe(false);
  });

  it("rejects a rooftop support whose verified footprint cannot contain the rotated facility", () => {
    const support: RoofSupport = { buildingId: "b-1", x: 50, z: 50, widthM: 8, depthM: 8,
      rotationDeg: 0, topY: 18 };
    const candidate = proposeRoofSnap(facility({ x: 0, z: 0 }), context({
      roofSupports: [support],
    }), "b-1", { x: 50, z: 50 });
    expect(candidate.legal).toBe(false);
    expect(candidate.issues.some(issue => issue.includes("大于屋顶"))).toBe(true);
  });

  it("rejects a rooftop snap whose pad volume hits an above-roof triangle", () => {
    // Support ceiling at 24 with a parapet wall poking up to 25 directly under the
    // 10×8 pad footprint; the shared building box never sees it, only the mesh does.
    const support: RoofSupport = { buildingId: "b-1", x: 50, z: 50, widthM: 30, depthM: 30,
      rotationDeg: 0, topY: 24 };
    const rooftopMesh = new Map([["b-1", meshRange([
      [40, 24, 40, 60, 24, 60, 60, 24, 40],
      [46, 24, 50, 54, 24, 50, 54, 25, 50],
      [46, 24, 50, 54, 25, 50, 46, 25, 50],
    ])]]);
    const candidate = proposeRoofSnap(facility({ x: 0, z: 0 }), context({
      roofSupports: [support],
      rooftopMesh,
    }), "b-1", { x: 48, z: 52 });
    expect(candidate.legal).toBe(false);
    expect(candidate.issues.some(issue => issue.includes("高于支撑面"))).toBe(true);
    expect(candidate.supportHeightM).toBe(24);
  });

  it("accepts a rooftop snap when the mesh volume proof is entirely below the pad", () => {
    const support: RoofSupport = { buildingId: "b-1", x: 50, z: 50, widthM: 30, depthM: 30,
      rotationDeg: 0, topY: 24 };
    const rooftopMesh = new Map([["b-1", meshRange([
      [40, 24, 40, 60, 24, 60, 60, 24, 40],
    ])]]);
    const candidate = proposeRoofSnap(facility({ x: 0, z: 0 }), context({
      roofSupports: [support],
      rooftopMesh,
    }), "b-1", { x: 48, z: 52 });
    expect(candidate.legal).toBe(true);
    expect(candidate.position).toEqual({ x: 50, z: 50 });
  });

  it("hides nothing behind an airspace zone and moves the vertiport off the no-fly volume", () => {
    // A no-fly volume only binds aircraft operations, so a vertiport is the probe:
    // the zone covers the northern half only, leaving a legal site to the south.
    const zone: AirspacePolygon = {
      id: "nfz.1", name: "hospital",
      polygon: [{ x: -50, z: -50 }, { x: 50, z: -50 }, { x: 50, z: 0 }, { x: -50, z: 0 }],
      floorM: 0, ceilingM: 120, startsAtS: 0, endsAtS: null,
      source: { kind: "manual", label: "test", uri: undefined },
    };
    const vertiportFacility = facility({ x: 0, z: 0 }, 0, {
      kind: "vertiport", landing: { parkingSlots: 2, movementsPerHour: 30 },
      cargo: null, charging: null,
    });
    const candidate = proposeGroundSnap(vertiportFacility, context({ airspace: [zone] }),
      { x: 0, z: 0 });
    expect(candidate.legal).toBe(true);
    expect(candidate.position.z).not.toBe(0);
  });

  it("refuses to apply an illegal candidate and applies a legal one verbatim", () => {
    const base = facility({ x: 600, z: 0 });
    const stuck = proposeGroundSnap(base, context(), { x: 600, z: 0 });
    expect(stuck.legal).toBe(false);
    expect(() => applySnapCandidate(base, stuck)).toThrow(/非法候选位置/);
    const clear = proposeGroundSnap(base, context(), { x: 20, z: -30 });
    expect(applySnapCandidate(base, clear)).toMatchObject({ position: { x: 20, z: -30 }, placement: "ground" });
  });
});
