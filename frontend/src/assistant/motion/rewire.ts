export type RewirePhase = 'charge' | 'dissolve' | 'rewire' | 'crystallize' | 'done';

export interface Point { x: number; y: number }

export interface RewireOptions {
  from: HTMLElement;
  to: HTMLElement;
  origin?: Point;
  palette: { from: string; to: string };
  durationScale?: number;
  reducedMotion?: boolean;
  signal?: AbortSignal;
  onPhase?: (phase: RewirePhase) => void;
}

export interface PhaseInfo { phase: RewirePhase; local: number }

const CHARGE_END = 180;
const DISSOLVE_END = 620;
const REWIRE_END = 1000;
const CRYSTAL_END = 1350;

/** Deterministic PRNG (mulberry32). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function easeInOutCubic(t: number): number {
  const c = Math.min(1, Math.max(0, t));
  return c < 0.5 ? 4 * c * c * c : 1 - Math.pow(-2 * c + 2, 3) / 2;
}

export function easeOutCubic(t: number): number {
  const c = Math.min(1, Math.max(0, t));
  return 1 - Math.pow(1 - c, 3);
}

function clamp01(t: number): number { return Math.min(1, Math.max(0, t)); }

export function phaseAt(elapsedMs: number, scale: number = 1): PhaseInfo {
  const e = elapsedMs / scale;
  if (e < CHARGE_END) return { phase: 'charge', local: e / CHARGE_END };
  if (e < DISSOLVE_END) return { phase: 'dissolve', local: (e - CHARGE_END) / (DISSOLVE_END - CHARGE_END) };
  if (e < REWIRE_END) return { phase: 'rewire', local: (e - DISSOLVE_END) / (REWIRE_END - DISSOLVE_END) };
  if (e < CRYSTAL_END) return { phase: 'crystallize', local: (e - REWIRE_END) / (CRYSTAL_END - REWIRE_END) };
  return { phase: 'done', local: 0 };
}

/** Mix two #rrggbb colours; channels are rounded to nearest. */
export function mixColor(a: string, b: string, t: number): string {
  const pa = parseHex(a);
  const pb = parseHex(b);
  const c = clamp01(t);
  const r = Math.round(pa[0] + (pb[0] - pa[0]) * c);
  const g = Math.round(pa[1] + (pb[1] - pa[1]) * c);
  const bl = Math.round(pa[2] + (pb[2] - pa[2]) * c);
  return `rgb(${r},${g},${bl})`;
}

function parseHex(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.replace(/(.)/g, '$1$1') : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function quadPoint(a: Point, control: Point, b: Point, t: number): Point {
  const u = 1 - t;
  return {
    x: u * u * a.x + 2 * u * t * control.x + t * t * b.x,
    y: u * u * a.y + 2 * u * t * control.y + t * t * b.y,
  };
}

export function sampleAnchors(root: HTMLElement, max: number = 84): Point[] {
  const rootRect = root.getBoundingClientRect();
  if (rootRect.width < 1 || rootRect.height < 1) return [];
  const selector = 'input, select, textarea, button, label, h1, h2, h3, h4, p, li, .wb-tag, .wb-card, .as-bubble, .as-card, [data-anchor]';
  const els = Array.from(root.querySelectorAll<HTMLElement>(selector)).slice(0, 320);
  const cell = 14;
  const seen = new Set<string>();
  const points: Point[] = [];
  const push = (x: number, y: number): void => {
    const cx = Math.floor(x / cell);
    const cy = Math.floor(y / cell);
    const key = cx + ':' + cy;
    if (seen.has(key)) return;
    seen.add(key);
    points.push({ x, y });
  };
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    if (cx < rootRect.left || cx > rootRect.right || cy < rootRect.top || cy > rootRect.bottom) continue;
    push(cx, cy);
    if (r.width > 120) {
      push(r.left + 10, cy);
      push(r.right - 10, cy);
    }
  }
  // Sort by y then x, keep evenly spaced sample.
  points.sort((a, b) => (a.y - b.y) || (a.x - b.x));
  let picked = points;
  if (points.length > max) {
    picked = [];
    const step = (points.length - 1) / (max - 1);
    for (let i = 0; i < max; i++) picked.push(points[Math.round(i * step)]);
  }
  if (picked.length < 24) {
    picked = picked.concat(fillGrid(rootRect, 24 - picked.length, 7, picked));
    picked.sort((a, b) => (a.y - b.y) || (a.x - b.x));
  }
  return picked;
}

export function fillGrid(
  rect: { x: number; y: number; width: number; height: number },
  count: number,
  seed: number,
  existing?: Point[],
): Point[] {
  const out: Point[] = [];
  if (count <= 0 || rect.width <= 32 || rect.height <= 32) return out;
  const inset = 16;
  const x0 = rect.x + inset;
  const y0 = rect.y + inset;
  const w = rect.width - inset * 2;
  const h = rect.height - inset * 2;
  const rand = mulberry32(seed);
  const all = (existing ?? []).concat(out);
  let guard = count * 60 + 600;
  while (out.length < count && guard-- > 0) {
    const px = x0 + rand() * w;
    const py = y0 + rand() * h;
    let ok = true;
    for (let i = 0; i < all.length; i++) {
      const dx = all[i].x - px;
      const dy = all[i].y - py;
      if (dx * dx + dy * dy < 18 * 18) { ok = false; break; }
    }
    if (!ok) continue;
    out.push({ x: px, y: py });
    all.push(out[out.length - 1]);
  }
  return out;
}

export function pairAnchors(from: Point[], to: Point[], max: number = 96): Array<{ a: Point; b: Point; delay: number }> {
  const n = Math.min(max, Math.max(from.length, to.length));
  if (n === 0) return [];
  const rand = mulberry32(1337);
  const resample = (list: Point[]): Point[] => {
    if (list.length === 0) return [];
    const sorted = list.slice().sort((p, q) => (p.y - q.y) || (p.x - q.x));
    const out: Point[] = [];
    for (let i = 0; i < n; i++) {
      const p = sorted[i % sorted.length];
      const rep = Math.floor(i / sorted.length);
      if (rep === 0) { out.push({ x: p.x, y: p.y }); continue; }
      out.push({ x: p.x + (rand() * 12 - 6), y: p.y + (rand() * 12 - 6) });
    }
    out.sort((p, q) => (p.y - q.y) || (p.x - q.x));
    return out;
  };
  const a = resample(from);
  const b = resample(to);
  const pairs: Array<{ a: Point; b: Point; delay: number }> = [];
  let minY = Infinity;
  let maxY = -Infinity;
  for (const p of b) { if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y; }
  const span = maxY - minY || 1;
  for (let i = 0; i < n; i++) {
    const bb = b[i];
    const norm = (bb.y - minY) / span;
    pairs.push({ a: a[i], b: bb, delay: norm * 0.7 + rand() * 0.3 });
  }
  return pairs;
}

export function knnEdges(points: Point[], k: number = 2, maxLength: number = 150): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  const seen = new Set<string>();
  const cap = 220;
  for (let i = 0; i < points.length && out.length < cap; i++) {
    const dists: Array<{ j: number; d: number }> = [];
    for (let j = 0; j < points.length; j++) {
      if (j === i) continue;
      const dx = points[j].x - points[i].x;
      const dy = points[j].y - points[i].y;
      dists.push({ j, d: dx * dx + dy * dy });
    }
    dists.sort((p, q) => p.d - q.d);
    const limit = Math.min(k, dists.length);
    for (let n = 0; n < limit && out.length < cap; n++) {
      if (dists[n].d > maxLength * maxLength) break;
      const j = dists[n].j;
      const key = i < j ? i + ':' + j : j + ':' + i;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(i < j ? [i, j] : [j, i]);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Canvas engine
// ---------------------------------------------------------------------------

interface Node { ax: number; ay: number; bx: number; by: number; cx: number; cy: number; delay: number; born: number; flash: number }

const MAX_NODES = 96;
const NIGHT = 'rgba(9,14,26,0.9)';

function sprite(rgb: [number, number, number], size = 32): HTMLCanvasElement {
  const c = document.createElement('canvas');
  c.width = size; c.height = size;
  const g = c.getContext('2d');
  if (g) {
    const half = size / 2, grad = g.createRadialGradient(half, half, 0, half, half, half);
    grad.addColorStop(0, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.95)`);
    grad.addColorStop(0.3, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.35)`);
    grad.addColorStop(1, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0)`);
    g.fillStyle = grad; g.fillRect(0, 0, size, size);
  }
  return c;
}

function dotGrid(): HTMLCanvasElement {
  const c = document.createElement('canvas');
  c.width = 16; c.height = 16;
  const g = c.getContext('2d');
  if (g) { g.fillStyle = 'rgba(160,190,255,0.10)'; g.fillRect(8, 8, 1, 1); }
  return c;
}

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Point at distance `d` clockwise along a rounded rectangle's border, starting at the top-left straight. */
function perimeterPoint(d: number, r: DOMRect, rad: number): Point {
  const w = r.width - rad * 2, h = r.height - rad * 2, corner = Math.PI * rad / 2, total = 2 * (w + h) + 4 * corner;
  d = ((d % total) + total) % total;
  const arc = (cx: number, cy: number, from: number, t: number): Point => ({ x: cx + Math.cos(from + t * Math.PI / 2) * rad, y: cy + Math.sin(from + t * Math.PI / 2) * rad });
  if (d < w) return { x: r.left + rad + d, y: r.top }; d -= w;
  if (d < corner) return arc(r.right - rad, r.top + rad, -Math.PI / 2, d / corner); d -= corner;
  if (d < h) return { x: r.right, y: r.top + rad + d }; d -= h;
  if (d < corner) return arc(r.right - rad, r.bottom - rad, 0, d / corner); d -= corner;
  if (d < w) return { x: r.right - rad - d, y: r.bottom }; d -= w;
  if (d < corner) return arc(r.left + rad, r.bottom - rad, Math.PI / 2, d / corner); d -= corner;
  if (d < h) return { x: r.left, y: r.bottom - rad - d }; d -= h;
  return arc(r.left + rad, r.top + rad, Math.PI, d / corner);
}

/** Border distance of the point nearest to `p` (sampled; 1 px is plenty for a light sweep). */
function nearestPerimeter(p: Point, r: DOMRect, rad: number): number {
  const total = 2 * (r.width + r.height);
  let best = 0, bestD = Infinity;
  for (let d = 0; d < total; d += 6) { const q = perimeterPoint(d, r, rad), dd = (q.x - p.x) ** 2 + (q.y - p.y) ** 2; if (dd < bestD) { bestD = dd; best = d; } }
  return best;
}

export function rewire(options: RewireOptions): Promise<void> {
  const { from, to, palette, onPhase } = options;
  return new Promise<void>(resolve => {
    const scale = options.durationScale ?? 1;
    const reduced = options.reducedMotion ?? (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
    let raf = 0, timer = 0, finished = false, phase: RewirePhase | undefined;
    let surface: HTMLCanvasElement | undefined;

    const setPhase = (next: RewirePhase): void => {
      if (phase === next) return;
      phase = next;
      from.setAttribute('data-phase', next); to.setAttribute('data-phase', next);
      onPhase?.(next);
    };
    const setScan = (value: number): void => { to.style.setProperty('--rewire-scan', value.toFixed(4)); };
    const finish = (): void => {
      if (finished) return;
      finished = true;
      if (raf) cancelAnimationFrame(raf);
      if (timer) clearTimeout(timer);
      for (const el of [from, to]) { el.removeAttribute('data-phase'); el.style.removeProperty('--rewire-scan'); }
      surface?.remove();
      window.removeEventListener('resize', finish);
      options.signal?.removeEventListener('abort', finish);
      resolve();
    };
    if (options.signal?.aborted) { finish(); return; }
    options.signal?.addEventListener('abort', finish, { once: true });
    window.addEventListener('resize', finish);

    const fromRect = from.getBoundingClientRect(), toRect = to.getBoundingClientRect();
    if (reduced || !fromRect.width || !fromRect.height || !toRect.width || !toRect.height) {
      from.setAttribute('data-phase', 'dissolve'); to.setAttribute('data-phase', 'crystallize'); setScan(1);
      timer = window.setTimeout(() => { setPhase('done'); timer = window.setTimeout(finish, 0); }, reduced ? 160 : 16);
      return;
    }

    const canvas = document.createElement('canvas');
    surface = canvas;
    canvas.className = 'as-rewire';
    canvas.setAttribute('aria-hidden', 'true');
    const dpr = Math.min(2, window.devicePixelRatio || 1), vw = window.innerWidth, vh = window.innerHeight;
    canvas.width = Math.round(vw * dpr); canvas.height = Math.round(vh * dpr);
    canvas.style.cssText = `position:fixed;left:0;top:0;width:${vw}px;height:${vh}px;pointer-events:none;z-index:60;`;
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    if (!ctx) { finish(); return; }
    ctx.scale(dpr, dpr);

    // The slot both panels share; the night glass and the scan live inside it.
    const slot = { left: Math.min(fromRect.left, toRect.left), top: Math.min(fromRect.top, toRect.top), right: Math.max(fromRect.right, toRect.right), bottom: Math.max(fromRect.bottom, toRect.bottom) };
    const slotW = slot.right - slot.left, slotH = slot.bottom - slot.top;
    const origin = options.origin ?? { x: fromRect.right - 40, y: fromRect.top + 40 };
    const corners = [[slot.left, slot.top], [slot.right, slot.top], [slot.left, slot.bottom], [slot.right, slot.bottom]];
    const reach = Math.max(...corners.map(([x, y]) => Math.hypot(x - origin.x, y - origin.y)));
    const viewportReach = Math.hypot(Math.max(origin.x, vw - origin.x), Math.max(origin.y, vh - origin.y));

    const pairs = pairAnchors(sampleAnchors(from, MAX_NODES), sampleAnchors(to, MAX_NODES), MAX_NODES);
    const rand = mulberry32(4242);
    const nodes: Node[] = pairs.map((pair, index) => {
      const dx = pair.b.x - pair.a.x, dy = pair.b.y - pair.a.y, length = Math.hypot(dx, dy) || 1;
      const offset = (40 + 50 * rand()) * (index % 2 ? -1 : 1);
      return { ax: pair.a.x, ay: pair.a.y, bx: pair.b.x, by: pair.b.y, cx: (pair.a.x + pair.b.x) / 2 - dy / length * offset, cy: (pair.a.y + pair.b.y) / 2 + dx / length * offset, delay: pair.delay, born: -1, flash: -1 };
    });
    // Edges index the nodes themselves, in each layout.
    const oldEdges = knnEdges(nodes.map(node => ({ x: node.ax, y: node.ay })), 2, 150);
    const newEdges = knnEdges(nodes.map(node => ({ x: node.bx, y: node.by })), 2, 150);

    const rgbFrom = parseHex(palette.from), rgbTo = parseHex(palette.to);
    const sprites = Array.from({ length: 12 }, (_, i) => sprite([0, 1, 2].map(c => Math.round(lerp(rgbFrom[c], rgbTo[c], i / 11))) as [number, number, number]));
    const colors = Array.from({ length: 12 }, (_, i) => mixColor(palette.from, palette.to, i / 11));
    const white = sprite([234, 246, 255]);
    const grid = ctx.createPattern(dotGrid(), 'repeat');

    const cur = new Float64Array(nodes.length * 2), trail = new Float64Array(nodes.length * 10);
    for (let i = 0; i < nodes.length; i++) { cur[i * 2] = nodes[i].ax; cur[i * 2 + 1] = nodes[i].ay; for (let t = 0; t < 10; t += 2) { trail[i * 10 + t] = nodes[i].ax; trail[i * 10 + t + 1] = nodes[i].ay; } }
    const pulses: Array<{ edge: number; at: number; fresh: boolean }> = [];
    let nextPulse = 0, start = -1, fadeFrom = -1;
    const borderStart = nearestPerimeter(origin, fromRect, 14);
    const borderTotal = 2 * (fromRect.width + fromRect.height - 56) + 4 * Math.PI * 7;

    const glow = (x: number, y: number, radius: number, image: HTMLCanvasElement, alpha: number): void => {
      ctx.globalAlpha = alpha; ctx.drawImage(image, x - radius, y - radius, radius * 2, radius * 2);
    };
    const dot = (x: number, y: number, radius: number, color: string, alpha: number): void => {
      ctx.globalAlpha = alpha; ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fill();
    };
    const night = (clip: () => void, tint: string, tintAlpha: number): void => {
      ctx.save();
      roundedRect(ctx, slot.left, slot.top, slotW, slotH, 14); ctx.clip();
      clip();
      ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1; ctx.fillStyle = NIGHT; ctx.fill();
      if (grid) { ctx.fillStyle = grid; ctx.fill(); }
      const halo = ctx.createRadialGradient(origin.x, origin.y, 0, origin.x, origin.y, reach);
      halo.addColorStop(0, tint); halo.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.globalAlpha = tintAlpha; ctx.fillStyle = halo; ctx.fill();
      ctx.restore();
    };
    const edgeLines = (edges: Array<[number, number]>, alpha: number, color: string, grow?: (i: number, j: number) => number, keep?: (i: number) => boolean): void => {
      if (alpha <= 0.002) return;
      ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.lineWidth = 0.8; ctx.beginPath();
      for (const [i, j] of edges) {
        if (keep && (!keep(i) || !keep(j))) continue;
        const g = grow ? grow(i, j) : 1;
        if (g <= 0) continue;
        const x1 = cur[i * 2], y1 = cur[i * 2 + 1], x2 = cur[j * 2], y2 = cur[j * 2 + 1];
        // Each link grows from both ends and meets in the middle.
        ctx.moveTo(x1, y1); ctx.lineTo(lerp(x1, x2, g / 2), lerp(y1, y2, g / 2));
        ctx.moveTo(x2, y2); ctx.lineTo(lerp(x2, x1, g / 2), lerp(y2, y1, g / 2));
      }
      ctx.stroke();
    };

    const frame = (now: number): void => {
      if (finished) return;
      if (!from.isConnected || !to.isConnected) { finish(); return; }
      if (start < 0) start = now;
      const elapsed = (now - start) / scale, info = phaseAt(elapsed, 1);
      setPhase(info.phase);
      if (info.phase === 'done') {
        // Hand over to the real panel: fade the canvas out, then clean up.
        setScan(1);
        if (fadeFrom < 0) fadeFrom = now;
        const k = Math.min(1, (now - fadeFrom) / 140);
        canvas.style.opacity = String(1 - k);
        if (k >= 1) { finish(); return; }
        raf = requestAnimationFrame(frame);
        return;
      }
      const { local } = info, charging = info.phase === 'charge', dissolving = info.phase === 'dissolve', rewiring = info.phase === 'rewire', crystal = info.phase === 'crystallize';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1;
      ctx.clearRect(0, 0, vw, vh);

      // 1. Charge: rings at the switch and one light sweep around the panel border.
      if (charging) {
        for (const [delay, end] of [[0, 46], [50, 28]]) {
          const t = clamp01((elapsed - delay) / (CHARGE_END - delay));
          if (t <= 0) continue;
          ctx.globalAlpha = 0.55 * (1 - t); ctx.strokeStyle = palette.from; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(origin.x, origin.y, 4 + (end - 4) * easeOutCubic(t), 0, Math.PI * 2); ctx.stroke();
        }
        const head = borderStart + easeInOutCubic(local) * borderTotal, tail = head - 60;
        for (const [width, alpha] of [[6, 0.16], [1.5, 0.95]]) {
          ctx.globalAlpha = alpha; ctx.strokeStyle = palette.from; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.beginPath();
          for (let s = 0; s <= 16; s++) { const p = perimeterPoint(lerp(tail, head, s / 16), fromRect, 14); if (s) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y); }
          ctx.stroke();
        }
        const tip = perimeterPoint(head, fromRect, 14);
        glow(tip.x, tip.y, 10, sprites[0], 0.9);
      }

      // 2. Night glass: spreads with the wave, holds while rewiring, is developed away by the scan.
      const wave = dissolving ? easeOutCubic(local) * reach * 1.04 : 0;
      const scanY = crystal ? slot.top + easeInOutCubic(local) * slotH : slot.top;
      const mid = colors[rewiring ? Math.round(local * 11) : crystal ? 11 : 0];
      if (dissolving) night(() => { ctx.beginPath(); ctx.arc(origin.x, origin.y, wave, 0, Math.PI * 2); }, palette.from, 0.16);
      else if (rewiring) night(() => undefined, mid, 0.16);
      else if (crystal) night(() => { ctx.beginPath(); ctx.rect(slot.left, scanY, slotW, slot.bottom - scanY); }, palette.to, 0.16 * (1 - local));
      if (crystal) setScan(easeInOutCubic(local));

      // Faint workbench-wide ripple and the bright wavefront.
      if (dissolving) {
        ctx.globalAlpha = 0.1 * (1 - local); ctx.strokeStyle = palette.from; ctx.lineWidth = 40;
        ctx.beginPath(); ctx.arc(origin.x, origin.y, easeOutCubic(local) * viewportReach, 0, Math.PI * 2); ctx.stroke();
        ctx.save(); roundedRect(ctx, slot.left, slot.top, slotW, slotH, 14); ctx.clip();
        ctx.globalAlpha = 0.7 * (1 - local * 0.6); ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(origin.x, origin.y, wave, 0, Math.PI * 2); ctx.stroke();
        ctx.globalAlpha = 0.14; ctx.lineWidth = 16; ctx.stroke();
        ctx.restore();
      }

      // 3. Nodes.
      ctx.globalCompositeOperation = 'lighter';
      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i];
        if (node.born < 0 && (dissolving && Math.hypot(node.ax - origin.x, node.ay - origin.y) <= wave || rewiring || crystal)) node.born = elapsed;
        if (node.born < 0) continue;
        const progress = rewiring ? easeInOutCubic(clamp01((local - node.delay * 0.35) / 0.65)) : crystal ? 1 : 0;
        const p = quadPoint({ x: node.ax, y: node.ay }, { x: node.cx, y: node.cy }, { x: node.bx, y: node.by }, progress);
        const base = i * 10;
        for (let t = 8; t >= 2; t -= 2) { trail[base + t] = trail[base + t - 2]; trail[base + t + 1] = trail[base + t - 1]; }
        trail[base] = cur[i * 2]; trail[base + 1] = cur[i * 2 + 1];
        cur[i * 2] = p.x; cur[i * 2 + 1] = p.y;
        const bucket = Math.round(progress * 11), color = colors[bucket];
        if (crystal) {
          if (node.flash < 0 && p.y <= scanY) node.flash = elapsed;
          if (node.flash >= 0) {
            const t = (elapsed - node.flash) / 180;
            if (t < 1) { const radius = t < 0.5 ? lerp(2.2, 4.5, t / 0.5) : 4.5 * (1 - (t - 0.5) / 0.5), alpha = t < 0.5 ? 1 : 1 - (t - 0.5) / 0.5; glow(p.x, p.y, radius * 4, white, alpha * 0.8); dot(p.x, p.y, radius, '#eaf6ff', alpha); }
          } else { glow(p.x, p.y, 10, sprites[11], 0.8); dot(p.x, p.y, 2.2, color, 0.9); }
          continue;
        }
        const age = elapsed - node.born;
        const radius = age < 120 ? (age < 84 ? 2.2 * age / 84 : 2.2 + 0.6 * Math.sin((age - 84) / 36 * Math.PI)) : 2.2;
        if (rewiring && progress > 0 && progress < 1) {
          ctx.lineCap = 'round';
          for (let t = 0; t < 8; t += 2) {
            ctx.globalAlpha = 0.4 * (1 - t / 8); ctx.strokeStyle = color; ctx.lineWidth = 1.6 * (1 - t / 8);
            ctx.beginPath(); ctx.moveTo(trail[base + t], trail[base + t + 1]); ctx.lineTo(t ? trail[base + t - 2] : p.x, t ? trail[base + t - 1] : p.y); ctx.stroke();
          }
        }
        glow(p.x, p.y, 10, sprites[bucket], 0.9);
        dot(p.x, p.y, Math.max(0, radius), '#eaf6ff', 0.95);
      }

      // 4. Links: grow in, stretch while rewiring, re-form in the new layout.
      if (dissolving) edgeLines(oldEdges, 0.45, palette.from, (i, j) => nodes[i].born < 0 || nodes[j].born < 0 ? 0 : clamp01((elapsed - Math.max(nodes[i].born, nodes[j].born)) / 180));
      else if (rewiring) { edgeLines(oldEdges, 0.45 * (1 - clamp01(local / 0.5)), palette.from); edgeLines(newEdges, 0.45 * clamp01((local - 0.5) / 0.5), palette.to); }
      else if (crystal) edgeLines(newEdges, 0.45, palette.to, undefined, i => nodes[i].flash < 0);

      // 5. Synapse pulses.
      const firing = dissolving || rewiring && local >= 0.5;
      const edges = dissolving ? oldEdges : newEdges;
      if (firing && edges.length && elapsed >= nextPulse && pulses.length < 6) { pulses.push({ edge: Math.floor(rand() * edges.length), at: elapsed, fresh: rewiring }); nextPulse = elapsed + 40; }
      for (let k = pulses.length - 1; k >= 0; k--) {
        const pulse = pulses[k], list = pulse.fresh ? newEdges : oldEdges, edge = list[pulse.edge], t = (elapsed - pulse.at) / 260;
        if (!edge || t >= 1 || crystal || nodes[edge[0]].born < 0 || nodes[edge[1]].born < 0) { pulses.splice(k, 1); continue; }
        const x = lerp(cur[edge[0] * 2], cur[edge[1] * 2], t), y = lerp(cur[edge[0] * 2 + 1], cur[edge[1] * 2 + 1], t);
        glow(x, y, 8, white, 0.75); dot(x, y, 1.6, '#ffffff', 1);
      }

      // 6. Scan line that develops the new panel.
      if (crystal) {
        ctx.save(); roundedRect(ctx, slot.left, slot.top, slotW, slotH, 14); ctx.clip();
        ctx.globalCompositeOperation = 'source-over';
        const band = ctx.createLinearGradient(0, scanY - 28, 0, scanY);
        band.addColorStop(0, 'rgba(0,0,0,0)'); band.addColorStop(1, palette.to);
        ctx.globalAlpha = 0.18; ctx.fillStyle = band; ctx.fillRect(slot.left, scanY - 28, slotW, 28);
        ctx.globalCompositeOperation = 'lighter';
        glow(slot.left + slotW / 2, scanY, slotW * 0.6, sprites[11], 0.18);
        ctx.globalAlpha = 1; ctx.strokeStyle = '#eaf6ff'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(slot.left, scanY); ctx.lineTo(slot.right, scanY); ctx.stroke();
        ctx.lineWidth = 1.5; ctx.beginPath();
        ctx.moveTo(slot.left + 2, scanY - 3); ctx.lineTo(slot.left + 2, scanY + 3); ctx.moveTo(slot.right - 2, scanY - 3); ctx.lineTo(slot.right - 2, scanY + 3);
        ctx.moveTo(slot.left + 2, scanY); ctx.lineTo(slot.left + 8, scanY); ctx.moveTo(slot.right - 8, scanY); ctx.lineTo(slot.right - 2, scanY);
        ctx.stroke(); ctx.restore();
      }
      raf = requestAnimationFrame(frame);
    };
    setPhase('charge');
    raf = requestAnimationFrame(frame);
  });
}
