export interface LabelRect { left: number; right: number; top: number; bottom: number }
/** A paused canvas still has to react when the glass panels move or resize. */
export function workbenchLayoutKey(container: HTMLElement): string {
  const wb = container.closest('.wb');
  if (!wb) return '';
  return [...wb.querySelectorAll('.wb-scene-frame, .wb-panel, .wb-camera, .wb-graph, .wb-history, .wb-waiting')].map(node => {
    const box = node.getBoundingClientRect(); return `${box.x}/${box.y}/${box.width}/${box.height}`;
  }).join('|');
}
const overlaps = (a: LabelRect, b: LabelRect) => a.left < b.right + 6 && a.right + 6 > b.left && a.top < b.bottom + 4 && a.bottom + 4 > b.top;
/** Keep labels wholly inside the usable scene. Selected labels stay reachable beside glass panels. */
export function placeLabel(x: number, y: number, width: number, height: number, safe: LabelRect, obstacles: LabelRect[], selected: boolean) {
  if (width > safe.right - safe.left || height > safe.bottom - safe.top) return;
  const rect = (cx: number, bottom: number) => ({ left: cx - width / 2, right: cx + width / 2, top: bottom - height, bottom });
  const inside = (box: LabelRect) => box.left >= safe.left && box.right <= safe.right && box.top >= safe.top && box.bottom <= safe.bottom;
  if (!selected) { const box = rect(x, y); return inside(box) && !obstacles.some(obstacle => overlaps(box, obstacle)) ? { x, y, box } : undefined; }
  const clamp = (cx: number, bottom: number) => ({ x: Math.max(safe.left + width / 2, Math.min(safe.right - width / 2, cx)), y: Math.max(safe.top + height, Math.min(safe.bottom, bottom)) });
  const candidates = [clamp(x, y), ...obstacles.flatMap(box => [clamp(box.left - width / 2 - 8, y), clamp(box.right + width / 2 + 8, y), clamp(x, box.top - 6), clamp(x, box.bottom + height + 6)])];
  return candidates.map(point => ({ ...point, box: rect(point.x, point.y) })).filter(point => inside(point.box) && !obstacles.some(obstacle => overlaps(point.box, obstacle))).sort((a, b) => (a.x - x) ** 2 + (a.y - y) ** 2 - (b.x - x) ** 2 - (b.y - y) ** 2)[0];
}
