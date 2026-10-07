import { Observable, type Unsubscribe } from "./observable";
import type { TraceTarget } from "./target";

export interface ContextMenu {
  readonly x: number;
  readonly y: number;
  readonly target: TraceTarget | null;
}

/**
 * Translate a canvas-local pointer position into client coordinates.
 * The map reports positions relative to the local renderer canvas, but the
 * context menu is `position: fixed`, which is positioned relative to
 * the viewport — the canvas bounding rect offset must be applied
 * before opening the menu.
 */
export function clientPointFromCanvas(
  x: number,
  y: number,
  canvas: { getBoundingClientRect(): { left: number; top: number } },
): { x: number; y: number } {
  const rect = canvas.getBoundingClientRect();
  return { x: rect.left + x, y: rect.top + y };
}

/** Map context menu placement; a null value closes the menu. */
export class ContextMenuState {
  private readonly value = new Observable<ContextMenu | null>(null);

  get(): ContextMenu | null {
    return this.value.value;
  }

  open(x: number, y: number, target: TraceTarget | null): void {
    this.value.set({ x, y, target });
  }

  close(): void {
    this.value.set(null);
  }

  subscribe(listener: (menu: ContextMenu | null) => void): Unsubscribe {
    return this.value.subscribe(listener);
  }
}
