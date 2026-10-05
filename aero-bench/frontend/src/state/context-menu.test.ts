import { describe, expect, it } from "vitest";
import { ContextMenuState, clientPointFromCanvas } from "./context-menu";

describe("ContextMenuState", () => {
  it("opens at a position with a target and closes back to null", () => {
    const menu = new ContextMenuState();
    const seen: (number | null)[] = [];
    menu.subscribe((value) => seen.push(value === null ? null : value.x));

    menu.open(120, 84, { kind: "entity", id: "uav.alpha" });
    expect(menu.get()).toEqual({ x: 120, y: 84, target: { kind: "entity", id: "uav.alpha" } });

    menu.open(10, 12, null);
    expect(menu.get()?.target).toBeNull();

    menu.close();
    expect(menu.get()).toBeNull();
    expect(seen).toEqual([null, 120, 10, null]);
  });
});

describe("canvas-to-client context menu coordinates", () => {
  it("translates canvas-local positions into client coordinates using the canvas rect", () => {
    const canvas = {
      getBoundingClientRect: () => ({ left: 120, top: 240 }),
    };
    expect(clientPointFromCanvas(30, 40, canvas)).toEqual({ x: 150, y: 280 });
    expect(clientPointFromCanvas(0, 0, canvas)).toEqual({ x: 120, y: 240 });
    expect(clientPointFromCanvas(800, 600, { getBoundingClientRect: () => ({ left: 0, top: 0 }) })).toEqual({
      x: 800,
      y: 600,
    });
  });
});
