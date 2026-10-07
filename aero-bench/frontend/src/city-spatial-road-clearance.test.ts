import { describe, expect, it } from "vitest";
import type { CityVegetationRoadBinding } from "./city-vegetation-layer";
import {
  evaluateSpatialRoadClearance,
  spatialRoadBlockingPolygons,
  spatialRoadClearanceFromCanonicalBinding,
} from "./city-spatial-road-clearance";
import type { FacilityPlacement } from "./city-workspace-geometry";

const provenance = {
  source: "canonical-road-v3" as const,
  roadSha256: "1".repeat(64),
  fixtureIdentity: "2".repeat(64),
  displayedSurfaceSha256: "3".repeat(64),
};

function rectangle(minX: number, minZ: number, maxX: number, maxZ: number) {
  return { outline: [[minX, minZ], [maxX, minZ], [maxX, maxZ], [minX, maxZ]] as const, holes: [] };
}

function binding(): CityVegetationRoadBinding {
  return {
    roadbed: [rectangle(0, 0, 10, 10)],
    walkbed: [rectangle(20, 0, 30, 10)],
    crossings: [{ id: "crossing-1", shape: [[40, 5], [50, 5]], width: 4 }],
    junctions: [],
    lamps: [{ id: "lamp-1", x: 60, z: 5, radiusM: 2, groundRadiusM: 0.5 }],
    signals: [{ id: "signal-1", x: 70, z: 5, radiusM: 1.5, groundRadiusM: 0.4 }],
  };
}

function facility(kind: FacilityPlacement["kind"], x: number, z = 5,
                  supportHeightM: number | null = null): FacilityPlacement {
  return { id: `facility-${kind}`, kind, position: { x, z }, widthM: 2, depthM: 2,
    heightM: 4, rotationDeg: 0, supportHeightM };
}

describe("verified spatial road clearance", () => {
  it("retains separate roadbed, walkbed, crossing and effective-fixture inventories", () => {
    const clearance = spatialRoadClearanceFromCanonicalBinding(binding(), provenance);
    expect(clearance.provenance).toEqual(provenance);
    expect(clearance.roadbed).toHaveLength(1);
    expect(clearance.walkbed).toHaveLength(1);
    expect(clearance.crossings).toHaveLength(1);
    expect(clearance.fixtures.map(item => item.id)).toEqual([
      "fixture:street_lamp:lamp-1", "fixture:signal:signal-1",
    ]);
    expect(spatialRoadBlockingPolygons(clearance)).toHaveLength(3);
  });

  it.each(["vertiport", "hub", "charger"] as const)(
    "blocks every ground %s on motor road, walkbed, crossing and fixture clearance",
    kind => {
      const clearance = spatialRoadClearanceFromCanonicalBinding(binding(), provenance);
      expect(evaluateSpatialRoadClearance(facility(kind, 5), clearance)[0]).toMatchObject({
        severity: "block", code: "roadbed_overlap", surface: "roadbed",
      });
      expect(evaluateSpatialRoadClearance(facility(kind, 25), clearance)[0]).toMatchObject({
        severity: "block", code: "walkbed_overlap", surface: "walkbed",
      });
      expect(evaluateSpatialRoadClearance(facility(kind, 45), clearance)[0]).toMatchObject({
        severity: "block", code: "crossing_overlap", surface: "crossing",
      });
      expect(evaluateSpatialRoadClearance(facility(kind, 60), clearance)[0]).toMatchObject({
        severity: "block", code: "fixture_overlap", surface: "fixture",
      });
    },
  );

  it("reports unavailable inputs instead of allowing a ground placement", () => {
    expect(evaluateSpatialRoadClearance(facility("charger", 90), null)).toEqual([
      expect.objectContaining({ severity: "block", code: "clearance_unavailable" }),
    ]);
  });

  it("labels a clear result and a rooftop exemption as visual-authoring warnings", () => {
    const clearance = spatialRoadClearanceFromCanonicalBinding(binding(), provenance);
    expect(evaluateSpatialRoadClearance(facility("hub", 90), clearance)).toEqual([
      expect.objectContaining({ severity: "warning", code: "visual_clearance_only" }),
    ]);
    expect(evaluateSpatialRoadClearance(facility("vertiport", 5, 5, 20), clearance)).toEqual([
      expect.objectContaining({ severity: "warning", code: "visual_clearance_only" }),
    ]);
  });

  it("rejects a crossing inventory without a measurable segment", () => {
    const invalid: CityVegetationRoadBinding = {
      ...binding(), crossings: [{ id: "zero", shape: [[1, 1], [1, 1]] as const, width: 2 }],
    };
    expect(() => spatialRoadClearanceFromCanonicalBinding(invalid, provenance))
      .toThrow(/no nonzero segment/);
  });
});
