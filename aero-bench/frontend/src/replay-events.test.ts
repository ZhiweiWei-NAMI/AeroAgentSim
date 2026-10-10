import { describe, expect, it } from "vitest";
import { ReplayEvents } from "./replay-events";
import { publicRunEvent } from "./testing/trace-v3-fixture";
import type { PublicRunEvent } from "./trace";

describe("recorded replay event windows", () => {
  it("excludes future events and restores earlier history on seek", () => {
    const events = [1, 2, 3].map(tick => publicRunEvent(tick, { at: { tick, sim_time_ns: tick * 200000000 } }) as unknown as PublicRunEvent);
    const index = new ReplayEvents(events);
    expect(index.at(0)).toEqual([]);
    expect(index.at(3)).toEqual(events);
    expect(index.at(1)).toEqual([events[0]]);
  });
  it("retains a quiet channel independently from a busy channel", () => {
    const events = [publicRunEvent(0) as unknown as PublicRunEvent];
    for (let tick = 1; tick <= 500; tick++) events.push(publicRunEvent(tick, { interaction_type: "ns3.link_state.v1", at: { tick, sim_time_ns: tick * 200000000 } }) as unknown as PublicRunEvent);
    const window = new ReplayEvents(events).at(500);
    expect(window).toHaveLength(401);
    expect(window[0]).toBe(events[0]);
    expect(window.at(-1)).toBe(events.at(-1));
  });
});
