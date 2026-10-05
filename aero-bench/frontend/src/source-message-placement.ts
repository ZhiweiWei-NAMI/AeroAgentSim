/**
 * Placement of the fixed source-message toast relative to the telemetry HUD.
 *
 * The toast and the operator PFD share the lower-right corner of the map, so
 * the toast must move out of the HUD's rectangle (plus a small gap) whenever
 * both are visible. All geometry decisions live in this pure module: the
 * caller measures the live client rects, this code returns the CSS
 * `right`/`bottom` offsets for the fixed toast, and no DOM is touched here.
 */

export interface SourceMessageRect {
  readonly left: number;
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
}

export interface SourceMessageSize {
  readonly width: number;
  readonly height: number;
}

export interface PlaceSourceMessageInput {
  /** Viewport (window.innerWidth/Height) the fixed toast lives in. */
  readonly viewport: SourceMessageSize;
  /** Measured border-box size of the toast element. */
  readonly toast: SourceMessageSize;
  /** HUD client rect, or null when the HUD is hidden or absent. */
  readonly hud: SourceMessageRect | null;
}

export interface SourceMessagePlacement {
  readonly right: number;
  readonly bottom: number;
}

/** Distance the toast keeps from the HUD rectangle. */
export const SOURCE_MESSAGE_HUD_GAP_PX = 8;

/** Today's corner offset, kept whenever the HUD is not covering it. */
export const SOURCE_MESSAGE_DEFAULT_PLACEMENT: SourceMessagePlacement = { right: 14, bottom: 190 };

/** Height of the viewer's top chrome band the toast must stay below. */
export const SOURCE_MESSAGE_TOP_CHROME_PX = 40;

function usable(value: number): boolean {
  return Number.isFinite(value) && value >= 0;
}

function grow(rect: SourceMessageRect, gap: number): SourceMessageRect {
  return {
    left: rect.left - gap,
    top: rect.top - gap,
    right: rect.right + gap,
    bottom: rect.bottom + gap,
  };
}

export function rectsIntersect(a: SourceMessageRect, b: SourceMessageRect): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

/** True when the toast rectangle would touch the HUD rectangle grown by the gap. */
export function toastOverlapsHud(
  toast: SourceMessageRect,
  hud: SourceMessageRect,
  gap: number = SOURCE_MESSAGE_HUD_GAP_PX,
): boolean {
  return rectsIntersect(toast, grow(hud, gap));
}

function rectFromPlacement(
  viewport: SourceMessageSize,
  toast: SourceMessageSize,
  placement: SourceMessagePlacement,
): SourceMessageRect {
  return {
    right: viewport.width - placement.right,
    bottom: viewport.height - placement.bottom,
    left: viewport.width - placement.right - toast.width,
    top: viewport.height - placement.bottom - toast.height,
  };
}

function fitsViewport(
  viewport: SourceMessageSize,
  toast: SourceMessageSize,
  placement: SourceMessagePlacement,
): boolean {
  return placement.right >= 0
    && placement.bottom >= 0
    && placement.right + toast.width <= viewport.width
    && placement.bottom + toast.height <= viewport.height;
}

function isClear(
  viewport: SourceMessageSize,
  toast: SourceMessageSize,
  hud: SourceMessageRect,
  placement: SourceMessagePlacement,
): boolean {
  return fitsViewport(viewport, toast, placement)
    && !toastOverlapsHud(rectFromPlacement(viewport, toast, placement), hud);
}

/**
 * Place the toast so it never covers (and is never covered by) the HUD:
 * keep the default corner when it is free, otherwise move to the nearest
 * clear position — directly above the HUD with the same right edge, then
 * directly left of the HUD with the same bottom, then the top-right of the
 * viewport below the chrome band — and clamp into the viewport as a last
 * resort so the message is always fully readable.
 */
export function placeSourceMessage(input: PlaceSourceMessageInput): SourceMessagePlacement {
  const viewport = input.viewport;
  const toast = input.toast;
  const hud = input.hud;
  if (!usable(viewport.width) || !usable(viewport.height)
    || !usable(toast.width) || !usable(toast.height)) {
    return { ...SOURCE_MESSAGE_DEFAULT_PLACEMENT };
  }
  const fallback: SourceMessagePlacement = {
    right: Math.min(SOURCE_MESSAGE_DEFAULT_PLACEMENT.right, Math.max(0, viewport.width - toast.width)),
    bottom: Math.min(SOURCE_MESSAGE_DEFAULT_PLACEMENT.bottom, Math.max(0, viewport.height - toast.height)),
  };
  if (hud === null || !hudRectIsUsable(hud) || isClear(viewport, toast, hud, SOURCE_MESSAGE_DEFAULT_PLACEMENT)) {
    return { ...fallback };
  }
  const aboveHud: SourceMessagePlacement = {
    right: Math.max(0, viewport.width - hud.right),
    bottom: viewport.height - hud.top + SOURCE_MESSAGE_HUD_GAP_PX,
  };
  if (isClear(viewport, toast, hud, aboveHud)) return aboveHud;
  const leftOfHud: SourceMessagePlacement = {
    right: viewport.width - hud.left + SOURCE_MESSAGE_HUD_GAP_PX,
    bottom: Math.max(0, viewport.height - hud.bottom),
  };
  if (isClear(viewport, toast, hud, leftOfHud)) return leftOfHud;
  const topRight: SourceMessagePlacement = {
    right: SOURCE_MESSAGE_DEFAULT_PLACEMENT.right,
    bottom: Math.max(0, viewport.height - SOURCE_MESSAGE_TOP_CHROME_PX - toast.height),
  };
  if (isClear(viewport, toast, hud, topRight)) return topRight;
  return fallback;
}

function hudRectIsUsable(hud: SourceMessageRect): boolean {
  return Number.isFinite(hud.left) && Number.isFinite(hud.top)
    && Number.isFinite(hud.right) && Number.isFinite(hud.bottom)
    && hud.right > hud.left && hud.bottom > hud.top;
}
