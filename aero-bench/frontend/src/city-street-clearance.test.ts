// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveStaticStreetLampClearance, resolveStreetLampClearance, type StreetLampGeometry, type StreetPavingPolygon,
  type StreetTrafficData } from "./city-street-clearance";

const geometry: StreetLampGeometry = { poleOffsetM: 0.173, poleRadiusM: 0.179, slices: [
  { minY: 0, maxY: 2.5, minX: -0.179, maxX: 0.179, halfZ: 0.179 },
  { minY: 2.5, maxY: 4, minX: -0.42, maxX: 0.42, halfZ: 0.179 },
  { minY: 4, maxY: 6, minX: -0.5, maxX: 0.42, halfZ: 0.179 },
  { minY: 6, maxY: 7.2, minX: -3.634, maxX: 0.42, halfZ: 0.179 },
] };

function traffic(firstZ: number, lastZ: number, groundY = 0.1): StreetTrafficData {
  return {
    schema_version: "aero-bench.city-sumo-preview/v1",
    source_network_sha256: "network",
    mesh_pack_source_sha256: "mesh",
    step_seconds: 0.25,
    signals: [],
    frames: [firstZ, lastZ].map(z => ({ vehicles: [["bus.1", 6, z, 0, "bus", groundY]], persons: [] })),
  };
}

function sidewalk(minX: number, maxX: number): StreetPavingPolygon[] {
  return [{ outline: [[minX, -20], [maxX, -20], [maxX, 20], [minX, 20]], holes: [] }];
}

describe("street lamp clearance from recorded SUMO buses", () => {
  it("does not relocate a bus-conflicting pole into a crossing corridor", () => {
    const source = [{ x: 3, z: 0, rotation_deg: 180 }];
    const result = resolveStreetLampClearance(source, sidewalk(-2, 4), traffic(3, -3, 5), geometry,
      [{ shape: [[-20, 0], [20, 0]], width: 30 }]);
    expect(result.lamps).toEqual([]);
    expect(result.omittedIndices).toEqual([0]);
  });

  it("keeps a lamp whose arm passes above a ground-level bus", () => {
    const source = [{ x: 3, z: 0, rotation_deg: 180 }];
    const result = resolveStreetLampClearance(source, sidewalk(2.5, 3.5), traffic(3, -3), geometry, []);
    expect(result.conflictingIndices).toEqual([]);
    expect(result.lamps).toEqual(source);
  });

  it("omits a conflicting arm without moving its pole away from the authored curb", () => {
    const source = [{ x: 3, z: 0, rotation_deg: 180 }];
    const recorded = traffic(3, -3, 5);
    const result = resolveStreetLampClearance(source, sidewalk(-2, 4), recorded, geometry, []);
    expect(result.conflictingIndices).toEqual([0]);
    expect(result.relocatedIndices).toEqual([]);
    expect(result.omittedIndices).toEqual([0]);
    expect(result.lamps).toEqual([]);
  });

  it("detects a pole crossing the swept bus body between frames", () => {
    const source = [{ x: 4.8, z: 0, rotation_deg: 180 }, { x: 3, z: 0, rotation_deg: 0 }];
    const result = resolveStreetLampClearance(source, sidewalk(2.5, 3.5), traffic(9, -9), geometry, []);
    expect(result.conflictingIndices).toEqual([0]);
    expect(result.relocatedIndices).toEqual([]);
    expect(result.omittedIndices).toEqual([0]);
    expect(result.lamps).toEqual([source[1]]);
  });

  it.each(["shanghai", "huangpu-night", "huangpu-walkable"])("clears the displayed %s bus paths", scene => {
    const directory = resolve("public/city-presentation");
    const road = JSON.parse(readFileSync(resolve(directory, `${scene}-road-preview-v1.json`), "utf8")) as {
      street_lamps: { x: number; z: number; rotation_deg: number }[];
      walkbed: StreetPavingPolygon[];
    };
    const recorded = JSON.parse(readFileSync(resolve(directory, `${scene}-sumo-preview-v1.json`),
      "utf8")) as StreetTrafficData;
    const result = resolveStreetLampClearance(road.street_lamps, road.walkbed, recorded, geometry, []);
    expect(result.relocatedIndices).toEqual([]);
    expect(result.lamps.length).toBeGreaterThan(0);
    expect(result.lamps.every(lamp => road.street_lamps.includes(lamp))).toBe(true);
    expect(resolveStreetLampClearance(result.lamps, road.walkbed, recorded, geometry, []).conflictingIndices).toEqual([]);
  });
});

describe("static street lamp clearance from verified road and network signals", () => {
  it("omits poles conflicting with crossings or real signals while retaining clear poles", () => {
    const lamps = [
      { x: 3, z: 0, rotation_deg: 0 },
      { x: 3, z: 8, rotation_deg: 0 },
      { x: 3, z: 15, rotation_deg: 0 },
    ];
    const result = resolveStaticStreetLampClearance(lamps, sidewalk(2.5, 3.5),
      [{ x: 3, z: 8 }], geometry, [{ shape: [[0, 0], [10, 0]], width: 3 }]);
    expect(result.omittedIndices).toEqual([0, 1]);
    expect(result.lamps).toEqual([lamps[2]]);
    expect(() => resolveStaticStreetLampClearance(lamps, [], [], geometry, [])).toThrow(/displayed pavement/);
  });
});
