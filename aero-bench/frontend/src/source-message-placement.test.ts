import { describe, expect, it } from "vitest";
import {
  placeSourceMessage,
  rectsIntersect,
  SOURCE_MESSAGE_DEFAULT_PLACEMENT,
  SOURCE_MESSAGE_HUD_GAP_PX,
  SOURCE_MESSAGE_TOP_CHROME_PX,
  toastOverlapsHud,
  type PlaceSourceMessageInput,
  type SourceMessageRect,
} from "./source-message-placement";

const TOAST = { width: 372, height: 31 };

type PlaceArgs = Omit<PlaceSourceMessageInput, "toast"> & Partial<Pick<PlaceSourceMessageInput, "toast">>;

function hudRect(left: number, top: number, right: number, bottom: number): SourceMessageRect {
  return { left, top, right, bottom };
}

/** Default HUD corner at right:16 bottom:16 with width 360 and the given height. */
function defaultHud(height: number, viewportWidth = 1600, viewportHeight = 900): SourceMessageRect {
  return hudRect(viewportWidth - 16 - 360, viewportHeight - 16 - height, viewportWidth - 16, viewportHeight - 16);
}

function place(input: PlaceArgs): { viewport: { width: number; height: number }; toast: { width: number; height: number }; placement: { right: number; bottom: number } } {
  const toast = input.toast ?? TOAST;
  return { viewport: input.viewport, toast, placement: placeSourceMessage({ ...input, toast }) };
}

function toastRect(
  viewport: { width: number; height: number },
  toast: { width: number; height: number },
  placement: { right: number; bottom: number },
): SourceMessageRect {
  return {
    left: viewport.width - placement.right - toast.width,
    top: viewport.height - placement.bottom - toast.height,
    right: viewport.width - placement.right,
    bottom: viewport.height - placement.bottom,
  };
}

function expectClearOfHud(
  viewport: { width: number; height: number },
  toast: { width: number; height: number },
  placement: { right: number; bottom: number },
  hud: SourceMessageRect,
): void {
  expect(toastOverlapsHud(toastRect(viewport, toast, placement), hud)).toBe(false);
}

function expectInsideViewport(
  viewport: { width: number; height: number },
  toast: { width: number; height: number },
  placement: { right: number; bottom: number },
): void {
  expect(placement.right).toBeGreaterThanOrEqual(0);
  expect(placement.bottom).toBeGreaterThanOrEqual(0);
  expect(placement.right + toast.width).toBeLessThanOrEqual(viewport.width);
  expect(placement.bottom + toast.height).toBeLessThanOrEqual(viewport.height);
}

describe("placeSourceMessage", () => {
  it("keeps the default corner when the HUD is hidden", () => {
    const { placement } = place({ viewport: { width: 1600, height: 900 }, hud: null });
    expect(placement).toEqual(SOURCE_MESSAGE_DEFAULT_PLACEMENT);
  });

  it("keeps the default corner when the HUD is elsewhere", () => {
    const viewport = { width: 1600, height: 900 };
    const hud = hudRect(40, 300, 400, 700);
    const { toast, placement } = place({ viewport, hud });
    expect(placement).toEqual(SOURCE_MESSAGE_DEFAULT_PLACEMENT);
    expectClearOfHud(viewport, toast, placement, hud);
    expectInsideViewport(viewport, toast, placement);
  });

  it("keeps the default corner when the HUD is dragged to the top-right (no intersection)", () => {
    const viewport = { width: 1600, height: 900 };
    const hud = hudRect(1600 - 16 - 360, 45, 1600 - 16, 45 + 200);
    const { toast, placement } = place({ viewport, hud });
    expect(placement).toEqual(SOURCE_MESSAGE_DEFAULT_PLACEMENT);
    expectClearOfHud(viewport, toast, placement, hud);
  });

  it("moves above the HUD when the default corner intersects the expanded default HUD (1600×900)", () => {
    const viewport = { width: 1600, height: 900 };
    const hud = defaultHud(381);
    const { toast, placement } = place({ viewport, hud });
    expectClearOfHud(viewport, toast, placement, hud);
    expectInsideViewport(viewport, toast, placement);
    expect(placement.right).toBe(16);
    expect(placement.bottom).toBe(16 + 381 + SOURCE_MESSAGE_HUD_GAP_PX);
  });

  it("moves above the HUD at 1280×800 as well", () => {
    const viewport = { width: 1280, height: 800 };
    const hud = defaultHud(381, 1280, 800);
    const { toast, placement } = place({ viewport, hud });
    expectClearOfHud(viewport, toast, placement, hud);
    expectInsideViewport(viewport, toast, placement);
    expect(placement.bottom).toBe(16 + 381 + SOURCE_MESSAGE_HUD_GAP_PX);
  });

  it("moves left of the HUD when there is no room above (tall HUD from under the chrome)", () => {
    const viewport = { width: 1600, height: 900 };
    const hud = hudRect(400, 30, 1584, 710);
    const { toast, placement } = place({ viewport, hud });
    expectClearOfHud(viewport, toast, placement, hud);
    expectInsideViewport(viewport, toast, placement);
    expect(placement.right).toBe(1600 - 400 + SOURCE_MESSAGE_HUD_GAP_PX);
    expect(placement.bottom).toBe(1600 === viewport.width ? 190 : placement.bottom);
    expect(toastRect(viewport, toast, placement).bottom).toBe(hud.bottom);
  });

  it("moves left of the HUD when the HUD is dragged to the bottom-left", () => {
    const viewport = { width: 1600, height: 900 };
    const hud = hudRect(16, 900 - 16 - 381, 16 + 360, 900 - 16);
    const { toast, placement } = place({ viewport, hud });
    expectClearOfHud(viewport, toast, placement, hud);
    expectInsideViewport(viewport, toast, placement);
  });

  it("keeps the default corner for the minimized HUD (no intersection)", () => {
    const viewport = { width: 1600, height: 900 };
    const hud = defaultHud(42);
    const { toast, placement } = place({ viewport, hud });
    expect(placement).toEqual(SOURCE_MESSAGE_DEFAULT_PLACEMENT);
    expectClearOfHud(viewport, toast, placement, hud);
  });

  it("falls back to the clamped default corner when no candidate is clear", () => {
    const viewport = { width: 1600, height: 900 };
    const hud = hudRect(0, 30, 1600, 900);
    const { toast, placement } = place({ viewport, hud });
    expectInsideViewport(viewport, toast, placement);
    expect(placement.right).toBe(SOURCE_MESSAGE_DEFAULT_PLACEMENT.right);
    expect(placement.bottom).toBe(Math.min(SOURCE_MESSAGE_DEFAULT_PLACEMENT.bottom, viewport.height - toast.height));
  });

  it("stays inside a tiny viewport with a corner-docked HUD", () => {
    const viewport = { width: 320, height: 240 };
    const toast = { width: 200, height: 40 };
    const hud = hudRect(0, 100, 150, 240);
    const { placement } = place({ viewport, hud, toast });
    expectInsideViewport(viewport, toast, placement);
    expectClearOfHud(viewport, toast, placement, hud);
  });

  it("clamps into a tiny viewport even with no HUD", () => {
    const viewport = { width: 320, height: 240 };
    const toast = { width: 300, height: 100 };
    const { placement } = place({ viewport, hud: null, toast });
    expectInsideViewport(viewport, toast, placement);
    expect(placement.right).toBe(SOURCE_MESSAGE_DEFAULT_PLACEMENT.right);
    expect(placement.bottom).toBe(viewport.height - toast.height);
  });

  it("treats a degenerate HUD rect as no HUD", () => {
    const viewport = { width: 1600, height: 900 };
    expect(place({ viewport, hud: hudRect(10, 10, 10, 10) }).placement).toEqual(SOURCE_MESSAGE_DEFAULT_PLACEMENT);
    expect(place({ viewport, hud: hudRect(Number.NaN, 0, 100, 100) }).placement).toEqual(SOURCE_MESSAGE_DEFAULT_PLACEMENT);
  });

  it("replaces a hidden HUD placement when the HUD reappears over the default corner", () => {
    const viewport = { width: 1600, height: 900 };
    const hud = defaultHud(381);
    const { placement } = place({ viewport, hud });
    expect(placement).not.toEqual(SOURCE_MESSAGE_DEFAULT_PLACEMENT);
    expect(place({ viewport, hud: null }).placement).toEqual(SOURCE_MESSAGE_DEFAULT_PLACEMENT);
  });

  it("keeps placements below the top chrome band", () => {
    const viewport = { width: 1600, height: 900 };
    const hud = hudRect(400, 30, 1584, 710);
    const { toast, placement } = place({ viewport, hud });
    expect(viewport.height - placement.bottom - toast.height)
      .toBeGreaterThanOrEqual(SOURCE_MESSAGE_TOP_CHROME_PX - 1);
  });
});

describe("toastOverlapsHud", () => {
  it("applies the gap on every side", () => {
    const hud = hudRect(100, 100, 200, 200);
    expect(toastOverlapsHud(hudRect(95, 95, 150, 130), hud)).toBe(true);
    // Right edge exactly at hud.left - gap is the first clear position.
    expect(toastOverlapsHud(hudRect(82, 95, 92, 130), hud)).toBe(false);
    expect(toastOverlapsHud(hudRect(50, 200 + SOURCE_MESSAGE_HUD_GAP_PX, 150, 250), hud)).toBe(false);
    expect(toastOverlapsHud(hudRect(50, 80, 150, 92 + 1), hud)).toBe(true);
  });

  it("reports touching edges as intersecting for raw rects", () => {
    expect(rectsIntersect(hudRect(0, 0, 10, 10), hudRect(10, 0, 20, 10))).toBe(false);
    expect(rectsIntersect(hudRect(0, 0, 10, 10), hudRect(9, 9, 20, 20))).toBe(true);
  });
});
