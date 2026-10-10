// @vitest-environment node
import { describe, expect, it } from "vitest";
import { converterOrigin, osmExtent, projectGeographic } from "./projection";
import { parseOsmJson, sourceFromScenario } from "./source";
import osmDocument from "../../public/osm2world/shanghai-hongqiao.osm.json";
import packDocument from "../../public/osm2world/packs/shanghai-v1/manifest.json";
const authoredOsm = parseOsmJson(osmDocument);
import { publicTrace } from "../testing/trace-v3-fixture";
import type { PublicScenario } from "../generated/aero-bench-contracts";

describe("OSM2World coordinate authority", () => {
  it("uses the converter node center but frames only the configured extract", () => {
    const origin = converterOrigin(authoredOsm);
    expect(origin.longitude_deg).toBeCloseTo((121.2924597 + 121.5616139) / 2, 7);
    const extent = osmExtent(authoredOsm, origin);
    expect(extent.east - extent.west).toBeGreaterThan(2000);
    expect(extent.east - extent.west).toBeLessThan(2300);
    expect(extent.north - extent.south).toBeLessThan(1700);
  });

  it("maps its origin to zero and preserves east and north directions", () => {
    const origin = { latitude_deg: 31.2304, longitude_deg: 121.4737 };
    expect(projectGeographic(origin.latitude_deg, origin.longitude_deg, origin)).toEqual({ east: 0, north: 0 });
    expect(projectGeographic(31.2314, 121.4747, origin).east).toBeGreaterThan(90);
    expect(projectGeographic(31.2314, 121.4747, origin).north).toBeGreaterThan(110);
  });

  it("translates a clipped extract from its converter origin into the declared city frame", () => {
    const converter = { latitude_deg: 31.22995, longitude_deg: 121.4747360545605 };
    const declared = { latitude_deg: 31.2304, longitude_deg: 121.4737 };
    const sourcePoint = { latitude_deg: 31.2280023, longitude_deg: 121.4722145 };
    const shift = projectGeographic(converter.latitude_deg, converter.longitude_deg, declared);
    const local = projectGeographic(sourcePoint.latitude_deg, sourcePoint.longitude_deg, converter);
    const expected = projectGeographic(sourcePoint.latitude_deg, sourcePoint.longitude_deg, declared);
    expect(shift.north).toBeLessThan(-49);
    expect(shift.north).toBeGreaterThan(-51);
    expect(local.north + shift.north).toBeCloseTo(expected.north, 2);
    expect(local.east + shift.east).toBeCloseTo(expected.east, 2);
  });

  it("does not attach invented operational entities to the OSM extract", () => {
    expect(packDocument).not.toHaveProperty("overlay");
    expect(packDocument).not.toHaveProperty("entities");
    expect(packDocument.projection.origin).toEqual(converterOrigin(authoredOsm));
  });

  it("uses published geodetic coordinates without random building classifications", () => {
    const scenario = (publicTrace() as { scenario: PublicScenario }).scenario;
    const source = sourceFromScenario(scenario);
    expect(sourceFromScenario(scenario)).toBe(source);
    for (const way of source.osm.elements.filter(element => element.type === "way" && element.tags?.building)) {
      expect(way.tags?.building).toBe("yes");
      expect(way.tags?.["building:levels"]).toBeUndefined();
    }
  });
});
