// @vitest-environment node
import { describe, expect, it } from "vitest";
import source from "../../public/osm2world/shanghai-hongqiao.osm.json";
import { parseOsmJson, sourceFromScenario } from "./source";
import { publicTrace } from "../testing/trace-v3-fixture";
import type { PublicScenario } from "../generated/aero-bench-contracts";

describe("OSM2World source contract", () => {
  it("accepts the configured offline OSM source and preserves feature counts", () => {
    const parsed = parseOsmJson(source);
    expect(parsed.version).toBe(0.6);
    expect(parsed.bounds).toEqual({ minlat: 31.2228, minlon: 121.4636, maxlat: 31.2371, maxlon: 121.4868 });
    expect(parsed.elements.filter((element) => element.type === "way" && element.tags?.building !== undefined).length).toBeGreaterThan(1000);
    expect(parsed.elements.filter((element) => element.type === "node").length).toBeGreaterThan(15000);
    expect(parsed.elements.filter((element) => element.type === "way" && element.tags?.landuse !== undefined).length).toBeGreaterThan(50);
    expect(parsed.elements.filter((element) => element.type === "way" && element.tags?.highway !== undefined).length).toBeGreaterThan(800);
  });

  it("rejects an undeclared road node instead of repairing topology", () => {
    const value = structuredClone(source) as unknown as { elements: { type: string; nodes?: number[] }[] };
    const way = value.elements.find((element) => element.type === "way" && element.nodes !== undefined)!;
    way.nodes![0] = -1;
    expect(() => parseOsmJson(value)).toThrow(/missing node/);
  });

  it("projects authoritative PublicScenario geometry to OSM2World features", () => {
    const trace = publicTrace() as { readonly scenario: PublicScenario };
    const projected = sourceFromScenario(trace.scenario);
    expect(projected.generator.name).toBe("OSM2World");
    expect(projected.provenance.source_dataset).toBe("AERO-BENCH PublicScenario");
    expect(projected.overlay.fixtures).toHaveLength(trace.scenario.entities.length);
    expect(projected.osm.elements.filter((element) => element.type === "way" && element.tags?.building !== undefined)).toHaveLength(trace.scenario.buildings.length);
  });
});
