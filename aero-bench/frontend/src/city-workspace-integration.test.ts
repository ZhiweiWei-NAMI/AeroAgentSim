import { describe, expect, it } from "vitest";
import { recordedDisplayIds } from "./city-presentation";

describe("SUMO workspace display limits", () => {
  const frames = [
    { second: 0, tls: {}, vehicles: [
      ["vehicle.short", 0, 0, 0, "sedan"], ["vehicle.long", 0, 0, 0, "taxi"],
      ["bicycle.short", 0, 0, 0, "bicycle"], ["bicycle.long", 0, 0, 0, "bicycle"],
    ], persons: [["person.short", 0, 0, 0], ["person.long", 0, 0, 0]] },
    { second: 0.25, tls: {}, vehicles: [
      ["vehicle.long", 1, 0, 0, "taxi"], ["bicycle.long", 1, 0, 0, "bicycle"],
    ], persons: [["person.long", 1, 0, 0]] },
  ] as const;

  it("keeps the same recorded identities while seeking and never exceeds each category cap", () => {
    const limits = { vehicles: 1, bicycles: 1, pedestrians: 1 };
    const selected = recordedDisplayIds(frames, limits);
    expect([...selected.vehicles]).toEqual(["vehicle.long"]);
    expect([...selected.bicycles]).toEqual(["bicycle.long"]);
    expect([...selected.pedestrians]).toEqual(["person.long"]);
    expect(recordedDisplayIds([...frames].reverse(), limits)).toEqual(selected);
    expect([...recordedDisplayIds(frames, { vehicles: 0, bicycles: 0, pedestrians: 0 }).vehicles]).toEqual([]);
  });

  it("rejects a display limit that is not a non-negative integer", () => {
    expect(() => recordedDisplayIds(frames, { vehicles: -1, bicycles: 0, pedestrians: 0 })).toThrow(RangeError);
    expect(() => recordedDisplayIds(frames, { vehicles: 1.5, bicycles: 0, pedestrians: 0 })).toThrow(RangeError);
    expect(() => recordedDisplayIds(frames, { vehicles: 1, bicycles: 0 } as never)).toThrow(RangeError);
  });
});
