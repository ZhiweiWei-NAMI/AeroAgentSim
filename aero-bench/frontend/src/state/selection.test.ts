import { describe, expect, it } from "vitest";
import { SelectionState } from "./selection";
import { sameTarget, targetKey, type TraceTarget } from "./target";

const entity: TraceTarget = { kind: "entity", id: "fixture.uav" };
const event: TraceTarget = { kind: "event", id: "event.0000000000000000" };

describe("SelectionState", () => {
  it("starts empty and reports the selected target", () => {
    const selection = new SelectionState();
    expect(selection.get()).toBeNull();
    expect(selection.isSelected(entity)).toBe(false);

    selection.select(entity);
    expect(selection.get()).toEqual(entity);
    expect(selection.isSelected(entity)).toBe(true);
    expect(selection.isSelected(event)).toBe(false);
  });

  it("notifies subscribers, replaying the current value on subscribe", () => {
    const selection = new SelectionState();
    const seen: (TraceTarget | null)[] = [];
    const unsubscribe = selection.subscribe((target) => seen.push(target));
    selection.select(event);
    unsubscribe();
    selection.clear();
    expect(seen).toEqual([null, event]);
  });

  it("clears back to the empty canvas selection", () => {
    const selection = new SelectionState();
    selection.select(entity);
    selection.clear();
    expect(selection.get()).toBeNull();
  });
});

describe("target helpers", () => {
  it("compares and keys targets without string ambiguity", () => {
    expect(sameTarget(entity, { kind: "entity", id: "fixture.uav" })).toBe(true);
    expect(sameTarget(entity, event)).toBe(false);
    expect(sameTarget(entity, null)).toBe(false);
    expect(sameTarget(null, null)).toBe(true);
    expect(targetKey(entity)).toBe("entity:fixture.uav");
  });
});
