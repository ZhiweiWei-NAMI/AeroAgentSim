import { describe, expect, it } from "vitest";
import { HoverState } from "./hover";

describe("HoverState", () => {
  it("does not request another map render while the pointer stays on the same target", () => {
    const hover = new HoverState();
    let notifications = 0;
    hover.subscribe(() => notifications++);
    hover.clear();
    hover.hover({ kind: "building", id: "w1" });
    hover.hover({ kind: "building", id: "w1" });
    hover.clear(); hover.clear();
    expect(notifications).toBe(3);
  });

  it("tracks a transient hover target without touching selection", () => {
    const hover = new HoverState();
    const seen: string[] = [];
    hover.subscribe((target) => seen.push(target === null ? "null" : target.kind));

    hover.hover({ kind: "entity", id: "uav.alpha" });
    expect(hover.get()).toEqual({ kind: "entity", id: "uav.alpha" });

    hover.hover({ kind: "region", id: "zone.restricted" });
    expect(hover.get()).toEqual({ kind: "region", id: "zone.restricted" });

    hover.clear();
    expect(hover.get()).toBeNull();
    expect(seen).toEqual(["null", "entity", "region", "null"]);
  });
});
