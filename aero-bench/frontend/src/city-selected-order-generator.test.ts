import { describe, expect, it } from "vitest";
import type { CitySelectedScenario } from "./city-selected-scenario";
import { generateSelectedOrderRequests, type SelectedOrderGeneration } from "./city-selected-order-generator";

const config: SelectedOrderGeneration = {
  seed: 417, maxOrders: 12, startAtS: 10, endAtS: 70,
  cargoMinKg: 0.2, cargoMaxKg: 1.5, deadlineLeadS: 120,
};
const scenario = {
  facilities: [
    { id: "hub", kind: "hub" }, { id: "charger", kind: "charger" },
    { id: "vertiport", kind: "vertiport" },
  ],
  fleet: [{ count: 2, maxPayloadKg: 2 }],
} as CitySelectedScenario;

describe("selected city order demand generation", () => {
  it("is deterministic and yields valid timed requests only between cargo facilities", () => {
    const first = generateSelectedOrderRequests(scenario, config);
    expect(first).toEqual(generateSelectedOrderRequests(scenario, config));
    expect(first).toHaveLength(12);
    expect(new Set(first.map(item => item.id)).size).toBe(12);
    for (const order of first) {
      expect(["hub", "vertiport"]).toContain(order.sourceFacilityId);
      expect(["hub", "vertiport"]).toContain(order.destinationFacilityId);
      expect(order.destinationFacilityId).not.toBe(order.sourceFacilityId);
      expect(order.releaseAtS).toBeGreaterThanOrEqual(10);
      expect(order.releaseAtS).toBeLessThan(70);
      expect(order.deliverByS - order.releaseAtS).toBe(120);
      expect(order.cargoKg).toBeGreaterThanOrEqual(0.2);
      expect(order.cargoKg).toBeLessThanOrEqual(1.5);
    }
    expect(first.map(item => item.releaseAtS)).toEqual(
      [...first].map(item => item.releaseAtS).sort((a, b) => a - b));
  });

  it("rejects impossible cargo demand and an absent destination", () => {
    expect(() => generateSelectedOrderRequests(scenario, { ...config, cargoMaxKg: 2.5 }))
      .toThrow(/载重能力/);
    expect(() => generateSelectedOrderRequests({ ...scenario,
      facilities: scenario.facilities.filter(item => item.id !== "hub") }, config))
      .toThrow(/两处可装卸设施/);
  });

  it("rejects invalid configuration, but zero demand needs no facilities", () => {
    expect(() => generateSelectedOrderRequests(scenario, { ...config, maxOrders: 10_001 }))
      .toThrow(/maxOrders/);
    expect(() => generateSelectedOrderRequests(scenario, { ...config, seed: Number.NaN }))
      .toThrow(/有限数值/);
    expect(() => generateSelectedOrderRequests(scenario, { ...config, endAtS: 10 }))
      .toThrow(/时间范围/);
    expect(generateSelectedOrderRequests({ ...scenario, facilities: [] }, { ...config, maxOrders: 0 }))
      .toEqual([]);
  });
});
