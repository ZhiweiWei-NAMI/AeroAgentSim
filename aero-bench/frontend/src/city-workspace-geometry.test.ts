// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { projectGeographic } from "./osm2world/projection";
import type { CityAirspace } from "./city-workspace-config";
import {
  importAirspaceGeoJSON, localToWgs84, normalizeBuildingPlacements, normalizeRoadbed,
  orientedBoxesIntersect, validateAirspacePolygon, validateFacilityPlacement,
  validateFlightSegment, wgs84ToLocal,
  type AirspacePolygon, type BuildingPlacementSource, type FacilityPlacement,
  type PlacementBox, type RoadbedSource, type RoadPolygon,
} from "./city-workspace-geometry";

const origin = { latitude_deg: 31.2288, longitude_deg: 121.481 };
const square = (minX: number, minZ: number, maxX: number, maxZ: number) => [
  { x: minX, z: minZ }, { x: maxX, z: minZ },
  { x: maxX, z: maxZ }, { x: minX, z: maxZ },
];
const facility: FacilityPlacement = {
  id: "facility-1", kind: "vertiport", position: { x: 0, z: 0 },
  widthM: 2, depthM: 2, heightM: 4, rotationDeg: 0,
};
const box: PlacementBox = {
  id: "box", x: 0, z: 0, widthM: 2, depthM: 2, heightM: 5, rotationDeg: 0,
};
const airspace: AirspacePolygon = {
  id: "nfz-1", name: "User area", polygon: square(-2, -2, 2, 2),
  floorM: 1, ceilingM: 20, startsAtS: 0, endsAtS: null,
  source: { kind: "manual", label: "User drawing" },
};

describe("city workspace metric geometry", () => {
  it("uses the pack's east-up-south frame and round-trips WGS84", () => {
    const latitude = 31.2301, longitude = 121.4824;
    const projected = projectGeographic(latitude, longitude, origin);
    const local = wgs84ToLocal(latitude, longitude, origin);
    expect(local.x).toBeCloseTo(projected.east, 9);
    expect(local.z).toBeCloseTo(-projected.north, 9);
    expect(local.x).toBeGreaterThan(0);
    expect(local.z).toBeLessThan(0);
    const restored = localToWgs84(local, origin);
    expect(restored.latitude_deg).toBeCloseTo(latitude, 10);
    expect(restored.longitude_deg).toBeCloseTo(longitude, 10);
    expect(wgs84ToLocal(origin.latitude_deg, origin.longitude_deg, origin)).toEqual({ x: 0, z: -0 });
    expect(() => wgs84ToLocal(91, longitude, origin)).toThrow(/WGS84/);
  });

  it("normalizes the actual default scene's placement and motor road records", () => {
    const directory = fileURLToPath(new URL("../public/city-presentation/", import.meta.url));
    const buildings = JSON.parse(readFileSync(`${directory}huangpu-night-building-placement-v1.json`, "utf8")) as
      { placements: BuildingPlacementSource[] };
    const roads = JSON.parse(readFileSync(`${directory}huangpu-night-road-preview-v1.json`, "utf8")) as
      { roadbed: RoadbedSource[] };
    const boxes = normalizeBuildingPlacements(buildings.placements);
    const polygons = normalizeRoadbed(roads.roadbed);
    expect(boxes).toHaveLength(508);
    expect(polygons).toHaveLength(519);
    expect(boxes[0]).toMatchObject({
      id: `${buildings.placements[0]!.building_id}:${buildings.placements[0]!.part}`,
      x: buildings.placements[0]!.x, z: buildings.placements[0]!.z,
      widthM: buildings.placements[0]!.width, depthM: buildings.placements[0]!.depth,
      rotationDeg: buildings.placements[0]!.rotation_deg,
    });
    expect(polygons[0]!.outline[0]).toEqual({
      x: roads.roadbed[0]!.outline[0]![0], z: roads.roadbed[0]!.outline[0]![1],
    });
  });

  it("uses the full rotated rectangles, with edge contact outside the collision", () => {
    expect(orientedBoxesIntersect(box, { ...box, id: "touch", x: 2 })).toBe(false);
    expect(orientedBoxesIntersect(box, { ...box, id: "overlap", x: 1.999 })).toBe(true);
    const rotated: PlacementBox = { ...box, widthM: 10, depthM: 2, rotationDeg: 45 };
    expect(orientedBoxesIntersect(rotated, { ...box, id: "corner", x: 4, z: -4,
      widthM: 1, depthM: 1 })).toBe(true);
    expect(orientedBoxesIntersect(rotated, { ...box, id: "separate", x: 0, z: 4,
      widthM: 1, depthM: 1 })).toBe(false);
  });

  it("finds building, motor road and other facility footprint overlaps", () => {
    const road: RoadPolygon = { id: "roadbed:0", outline: square(-5, 0.5, 5, 1.5), holes: [] };
    const other = { ...facility, id: "facility-2", position: { x: 1.5, z: 0 } };
    const issues = validateFacilityPlacement(facility, [box], [road], [other], []);
    expect(issues.map(issue => [issue.code, issue.obstacleId])).toEqual([
      ["building_overlap", "box"], ["road_overlap", "roadbed:0"],
      ["facility_overlap", "facility-2"],
    ]);
    expect(validateFacilityPlacement({ ...facility, position: { x: 20, z: 20 } },
      [box], [road], [other], [])).toEqual([]);
  });

  it("honors roadbed holes and detects overlaps away from the facility center", () => {
    const road: RoadPolygon = {
      id: "roadbed:hole", outline: square(-10, -10, 10, 10),
      holes: [square(-2, -2, 2, 2)],
    };
    expect(validateFacilityPlacement(facility, [], [road], [], [])).toEqual([]);
    const edge = { ...facility, position: { x: 1.8, z: 0 } };
    expect(validateFacilityPlacement(edge, [], [road], [], []).map(issue => issue.code))
      .toEqual(["road_overlap"]);
    const triangle: RoadPolygon = {
      outline: [{ x: -1, z: -1 }, { x: 1, z: -1 }, { x: -1, z: 1 }], holes: [],
    };
    expect(validateFacilityPlacement(facility, [], [triangle], [], []).map(issue => issue.code))
      .toEqual(["road_overlap"]);
  });

  it("checks a vertiport footprint against the no-fly vertical interval", () => {
    expect(validateFacilityPlacement(facility, [], [], [], [airspace]).map(issue => issue.code))
      .toEqual(["airspace_overlap"]);
    expect(validateFacilityPlacement(facility, [], [], [], [{ ...airspace, floorM: 5 }]))
      .toEqual([]);
    expect(validateFacilityPlacement({ ...facility, kind: "hub" }, [], [], [], [airspace]))
      .toEqual([]);
    expect(validateFacilityPlacement({ ...facility, widthM: 0 }, [], [], [], []))
      .toEqual([{ code: "invalid_facility",
        message: "Facility needs a valid ID, finite position, rotation and positive dimensions" }]);
  });

  it("checks the swept body against real-height buildings and buffered no-fly space", () => {
    const body = { x: 1, y: 0.4, z: 1 };
    const elevated = { ...box, baseY: 10, heightM: 5 };
    expect(validateFlightSegment({ x: 1.5, y: 12, z: -5 }, { x: 1.5, y: 12, z: 5 },
      0, 10, [elevated], [], [], body).map(issue => issue.code)).toEqual(["building_collision"]);
    expect(validateFlightSegment({ x: 0, y: 5, z: -5 }, { x: 0, y: 5, z: 5 },
      0, 10, [elevated], [], [], body)).toEqual([]);
    expect(validateFlightSegment({ x: 2.5, y: 5, z: -5 }, { x: 2.5, y: 5, z: 5 },
      0, 10, [], [], [{ ...airspace, floorM: 4, ceilingM: 6 }], body)
      .map(issue => issue.code)).toEqual(["airspace_incursion"]);
  });

  it("checks the default vertical climb and stationary hover during active no-fly time", () => {
    const body = { x: 1, y: 0.4, z: 1 };
    const suspended = { ...airspace, floorM: 10, ceilingM: 20 };
    expect(validateFlightSegment({ x: 0, y: 4.2, z: 0 }, { x: 0, y: 30, z: 0 },
      0, 10, [], [], [suspended], body).map(issue => issue.code)).toEqual(["airspace_incursion"]);
    expect(validateFlightSegment({ x: 0, y: 15, z: 0 }, { x: 0, y: 15, z: 0 },
      10, 20, [], [], [{ ...suspended, startsAtS: 12, endsAtS: 15 }], body)
      .map(issue => issue.code)).toEqual(["airspace_incursion"]);
    expect(validateFlightSegment({ x: 0, y: 4.2, z: 0 }, { x: 0, y: 30, z: 0 },
      0, 10, [], [], [{ ...suspended, startsAtS: 8 }], body)).toEqual([]);
  });

  it("allows exact pad roof contact but catches a body below or outside its clear channel", () => {
    const body = { x: 1, y: 0.4, z: 1 };
    const roof = { ...facility, widthM: 10, depthM: 10, heightM: 8 };
    const pad = {
      facilityId: roof.id, x: 0, z: 0, widthM: 4, depthM: 4,
      rotationDeg: 0, padY: 2,
    };
    expect(validateFlightSegment({ x: 0, y: 2.2, z: 0 }, { x: 0, y: 20, z: 0 },
      0, 10, [], [roof], [], body, [pad])).toEqual([]);
    expect(validateFlightSegment({ x: 0, y: 2.19, z: 0 }, { x: 0, y: 20, z: 0 },
      0, 10, [], [roof], [], body, [pad]).map(issue => issue.code))
      .toEqual(["facility_collision"]);
    expect(validateFlightSegment({ x: 0, y: 2.2, z: 0 }, { x: 4, y: 2.2, z: 0 },
      0, 10, [], [roof], [], body, [pad]).map(issue => issue.code))
      .toEqual(["facility_collision"]);
    expect(validateFlightSegment({ x: 0, y: 8.2, z: 0 }, { x: 4, y: 8.2, z: 0 },
      0, 10, [], [roof], [], body)).toEqual([]);
  });

  it("accepts an explicit closing point and rejects degenerate or crossing polygons", () => {
    const good = { polygon: [...square(0, 0, 3, 3), { x: 0, z: 0 }], floorM: 0, ceilingM: 40 };
    expect(validateAirspacePolygon(good)).toEqual([]);
    expect(validateAirspacePolygon({ ...good, ceilingM: 0 }).map(issue => issue.code))
      .toEqual(["invalid_bounds"]);
    expect(validateAirspacePolygon({ ...good, polygon: [
      { x: 0, z: 0 }, { x: 3, z: 3 }, { x: 0, z: 3 }, { x: 3, z: 0 },
    ] }).map(issue => issue.code)).toContain("self_intersection");
    expect(validateAirspacePolygon({ ...good, polygon: [
      { x: 0, z: 0 }, { x: 1, z: 0 }, { x: 2, z: 0 },
    ] }).map(issue => issue.code)).toContain("degenerate_polygon");
  });

  it("imports WGS84 MultiPolygon features with explicit height and user provenance", () => {
    const first = [[121.481, 31.2288], [121.4811, 31.2288],
      [121.4811, 31.2289], [121.481, 31.2289], [121.481, 31.2288]];
    const second = [[121.4812, 31.2288], [121.4813, 31.2288],
      [121.4813, 31.2289], [121.4812, 31.2289], [121.4812, 31.2288]];
    const imported = importAirspaceGeoJSON({
      type: "Feature", properties: { name: "Filed area", floorM: 12, ceilingM: 80 },
      geometry: { type: "MultiPolygon", coordinates: [[first], [second]] },
    }, origin, "operator upload.geojson");
    const stored: CityAirspace[] = imported;
    expect(stored).toHaveLength(2);
    expect(imported).toHaveLength(2);
    expect(imported.map(item => item.name)).toEqual(["Filed area", "Filed area 2"]);
    expect(imported[0]).toMatchObject({
      floorM: 12, ceilingM: 80, startsAtS: 0, endsAtS: null,
      source: { kind: "geojson", label: "operator upload.geojson" },
    });
    expect(imported[0]!.polygon[0]).toEqual({ x: 0, z: -0 });
    expect(imported[0]!.polygon[2]!.z).toBeLessThan(0);
    expect(importAirspaceGeoJSON({ type: "Polygon", coordinates: [first] }, origin,
      "operator upload.geojson", { floorM: 0, ceilingM: 100 })).toHaveLength(1);
    expect(() => importAirspaceGeoJSON({ type: "Polygon", coordinates: [first] }, origin,
      "operator upload.geojson")).toThrow(/floorM/);
  });

  it("rejects holes, open rings, bad WGS84 points and unsupported coordinate systems", () => {
    const ring = [[121.481, 31.2288], [121.4811, 31.2288],
      [121.4811, 31.2289], [121.481, 31.2288]];
    const options = { floorM: 0, ceilingM: 100 };
    const geometry = (coordinates: unknown) => ({ type: "Polygon", coordinates });
    expect(() => importAirspaceGeoJSON(geometry([ring, ring]), origin, "upload", options))
      .toThrow(/holes/);
    expect(() => importAirspaceGeoJSON(geometry([[...ring.slice(0, -1), [121.481, 31.2289]]]),
      origin, "upload", options)).toThrow(/close/);
    expect(() => importAirspaceGeoJSON(geometry([[[31.2, 121.4], ...ring.slice(1)]]),
      origin, "upload", options)).toThrow(/WGS84/);
    expect(() => importAirspaceGeoJSON({ ...geometry([ring]), crs: "EPSG:3857" },
      origin, "upload", options)).toThrow(/CRS/);
    expect(() => importAirspaceGeoJSON(geometry([ring.map(point => [...point, 10])]),
      origin, "upload", options)).toThrow(/WGS84/);
  });
});
