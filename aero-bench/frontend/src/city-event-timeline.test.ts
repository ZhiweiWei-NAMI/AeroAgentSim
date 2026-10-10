// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import {
  cityEventTimelineSnapshot, renderCityEventTimeline, type CityTimelineEvent,
} from "./city-event-timeline";

function event(id: string, atS: number, type: CityTimelineEvent["type"] = "order.created"): CityTimelineEvent {
  return { id, atS, type, targetId: `target.${id}`, payload: { source: id } };
}

const events = [
  event("late", 20, "traffic.restricted"),
  event("same-b", 10, "weather.changed"),
  event("early", 5, "order.created"),
  event("same-a", 10, "airspace.activated"),
];

describe("city event timeline", () => {
  it("sorts by scheduled time and event identity without changing the draft array", () => {
    const original = [...events];
    const snapshot = cityEventTimelineSnapshot(events, 0);
    expect(snapshot.entries.map(entry => entry.event.id)).toEqual(["early", "same-a", "same-b", "late"]);
    expect(snapshot.entries.map(entry => entry.status)).toEqual([
      "scheduled", "scheduled", "scheduled", "scheduled",
    ]);
    expect(events).toEqual(original);
  });

  it("treats the latest reached equal-time group as current and older rows as past", () => {
    const snapshot = cityEventTimelineSnapshot(events, 12);
    expect(snapshot.currentAtS).toBe(10);
    expect(snapshot.entries.map(entry => [entry.event.id, entry.status])).toEqual([
      ["early", "past"], ["same-a", "current"], ["same-b", "current"], ["late", "scheduled"],
    ]);
  });

  it("is stateless across forward seek, backward seek and a repeated time", () => {
    expect(cityEventTimelineSnapshot(events, 25).entries.map(entry => entry.status))
      .toEqual(["past", "past", "past", "current"]);
    const backwards = cityEventTimelineSnapshot(events, 6);
    expect(backwards.entries.map(entry => entry.status))
      .toEqual(["current", "scheduled", "scheduled", "scheduled"]);
    expect(cityEventTimelineSnapshot(events, 6)).toEqual(backwards);
  });

  it("renders every status with an explicit preview-only disclaimer and updates without duplicates", () => {
    const root = document.createElement("div");
    const timeline = renderCityEventTimeline(root, events, 12);
    expect(root.querySelector('[data-role="event-timeline-disclaimer"]')?.textContent)
      .toContain("不表示 Provider 已接收、执行或产生正式遥测");
    expect(root.querySelectorAll('[data-timeline-status="past"]')).toHaveLength(1);
    expect(root.querySelectorAll('[data-timeline-status="current"]')).toHaveLength(2);
    expect(root.querySelectorAll('[data-timeline-status="scheduled"]')).toHaveLength(1);
    expect(root.querySelector('[data-event-id="early"]')?.textContent)
      .toContain('负载 {"source":"early"}');

    timeline.update(events, 6);
    timeline.update(events, 6);
    expect(root.querySelectorAll("[data-event-id]")).toHaveLength(4);
    expect(root.querySelectorAll('[data-timeline-status="current"]')).toHaveLength(1);
    expect(root.querySelector('[data-role="event-timeline-summary"]')?.textContent)
      .toContain("播放头 0:06");
    timeline.dispose();
    expect(root.childElementCount).toBe(0);
  });

  it("renders payload objects in deterministic key order", () => {
    const root = document.createElement("div");
    renderCityEventTimeline(root, [{ ...event("payload", 1), payload: {
      z: 1, a: { y: true, x: [2, 1] },
    } }], 0);
    expect(root.querySelector('[data-event-id="payload"]')?.textContent)
      .toContain('负载 {"a":{"x":[2,1],"y":true},"z":1}');
  });

  it("shows an explicit empty state and rejects invalid clocks or duplicate identities", () => {
    const root = document.createElement("div");
    renderCityEventTimeline(root, [], 0);
    expect(root.textContent).toContain("当前草稿没有计划事件");
    expect(() => cityEventTimelineSnapshot(events, -1)).toThrow(/finite and non-negative/);
    expect(() => cityEventTimelineSnapshot([event("same", 1), event("same", 2)], 0))
      .toThrow(/input is invalid/);
  });
});
