import * as THREE from 'three';

export type Vec3 = [number, number, number];
export interface OverlayMarker {
  id: string; position: Vec3; label: string;
  kind: 'vehicle' | 'fleet' | 'kitchen' | 'customer' | 'vertiport' | 'charging' | 'handoff' | 'incident' | 'place';
  state: 'new' | 'changed' | 'existing' | 'removed';
}
export interface OverlayRoute {
  id: string; from: Vec3; to: Vec3; kind: 'task' | 'handoff' | 'response' | 'notice';
  state: 'new' | 'changed' | 'existing';
}
export interface PreviewOverlay {
  tone: 'proposed' | 'configured';
  markers: OverlayMarker[]; routes: OverlayRoute[];
  focus?: string[];
  reveal?: { key: string; order: string[] };
  frame?: { key: string; ids: string[] };
}

const clamp = (value: number) => Math.max(0, Math.min(1, value));
const positionKey = (position: Vec3) => position.join(',');
export const overlayPosition = ([e, n, u]: Vec3) => new THREE.Vector3(e, u, -n);

/** Choose the control height so the curve's apex, not its control point, reaches the requested height. */
export function overlayArc(route: Pick<OverlayRoute, 'from' | 'to'>): THREE.QuadraticBezierCurve3 {
  const [e, n, u] = route.from, [ee, nn, uu] = route.to;
  const apex = Math.max(u, uu) + Math.min(Math.hypot(ee - e, nn - n) * 0.25, 120);
  const control = new THREE.Vector3((e + ee) / 2, apex + Math.sqrt((apex - u) * (apex - uu)), -(n + nn) / 2);
  return new THREE.QuadraticBezierCurve3(overlayPosition(route.from), control, overlayPosition(route.to));
}

export function overlayFocus(overlay: PreviewOverlay): Set<string> {
  const focus = new Set(overlay.focus);
  const positions = new Set(overlay.markers.filter(marker => focus.has(marker.id)).map(marker => positionKey(marker.position)));
  for (const route of overlay.routes) if (positions.has(positionKey(route.from)) || positions.has(positionKey(route.to))) focus.add(route.id);
  return focus;
}

/** Route endpoints have positions, not identities; only exact authored positions establish a connection. */
export function overlayRevealTimes(overlay: PreviewOverlay, startedAt: number): Map<string, number> {
  const known = new Set([...overlay.markers, ...overlay.routes].map(item => item.id));
  const order = [...new Set(overlay.reveal?.order)].filter(id => known.has(id));
  const times = new Map(order.map((id, index) => [id, startedAt + index * 140]));
  const endpoints = new Map<string, number>();
  for (const marker of overlay.markers) {
    const at = times.get(marker.id);
    if (at !== undefined) endpoints.set(positionKey(marker.position), Math.max(endpoints.get(positionKey(marker.position)) ?? startedAt, at + 380));
  }
  for (const route of overlay.routes) {
    const at = times.get(route.id);
    if (at !== undefined) times.set(route.id, Math.max(at, endpoints.get(positionKey(route.from)) ?? startedAt, endpoints.get(positionKey(route.to)) ?? startedAt));
  }
  return times;
}

export function overlayLabelOrder(markers: OverlayMarker[], focus: Set<string>): OverlayMarker[] {
  const priority = (marker: OverlayMarker) => focus.has(marker.id) ? 0 : marker.state === 'new' || marker.state === 'changed' ? 1 : marker.kind === 'incident' ? 2 : marker.kind === 'handoff' ? 3 : 4;
  return [...markers].sort((a, b) => priority(a) - priority(b));
}

export interface OverlayLabelBox { id: string; x: number; y: number; width: number; height: number }
export function layoutOverlayLabels(candidates: OverlayLabelBox[], width: number, height: number, limit = 24): OverlayLabelBox[] {
  const placed: OverlayLabelBox[] = [];
  for (const candidate of candidates) {
    if (placed.length >= limit) break;
    for (let step = 0; step < 7; step++) {
      const box = { ...candidate, y: candidate.y - step * (candidate.height + 4) };
      if (box.x < 0 || box.y < 0 || box.x + box.width > width || box.y + box.height > height) continue;
      if (placed.some(other => box.x < other.x + other.width + 4 && box.x + box.width + 4 > other.x && box.y < other.y + other.height + 4 && box.y + box.height + 4 > other.y)) continue;
      placed.push(box); break;
    }
  }
  return placed;
}

export function overlayEase(progress: number): number {
  const p = clamp(progress);
  return p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
}

export class OverlayTimeline {
  private contentKey?: string;
  private revealKey?: string;
  private frameKey?: string;
  private appeared = new Map<string, number>();
  private changedAt = 0;
  private reveal = new Map<string, number>();
  private interactionAt = 0;
  private overlay?: PreviewOverlay;

  update(overlay: PreviewOverlay, now: number): { rebuild: boolean; frame: boolean } {
    const contentKey = JSON.stringify([overlay.tone, overlay.markers, overlay.routes]);
    const rebuild = contentKey !== this.contentKey;
    if (rebuild) { this.changedAt = now; this.contentKey = contentKey; }
    this.appeared = new Map(overlay.markers.map(marker => [marker.id, this.appeared.get(marker.id) ?? now]));
    if (overlay.reveal && overlay.reveal.key !== this.revealKey) {
      this.revealKey = overlay.reveal.key;
      this.reveal = overlayRevealTimes(overlay, now);
    } else if (!overlay.reveal) this.reveal.clear();
    const frame = overlay.frame !== undefined && overlay.frame.key !== this.frameKey;
    if (frame) this.frameKey = overlay.frame!.key;
    this.overlay = overlay;
    this.interact(now);
    return { rebuild, frame };
  }

  interact(now: number) { this.interactionAt = now; }

  marker(marker: OverlayMarker, now: number, reduced: boolean) {
    const at = this.reveal.get(marker.id), age = at === undefined ? Infinity : now - at;
    const progress = reduced ? 1 : clamp(age / 380);
    const back = 1 + 2.9 * Math.pow(progress - 1, 3) + 1.9 * Math.pow(progress - 1, 2);
    const breathAge = now - Math.max(this.appeared.get(marker.id) ?? now, at ?? -Infinity);
    const breathing = !reduced && marker.state === 'new' && breathAge >= 0 && breathAge < 6000;
    const breath = breathing ? (1 - Math.cos(breathAge * Math.PI * 2 / 1800)) / 2 * clamp((6000 - breathAge) / 600) : 0;
    const rippleAge = Math.min(age, marker.state === 'changed' ? now - this.changedAt : Infinity);
    let ripple: number | undefined;
    if (!reduced && marker.state !== 'removed') {
      if (rippleAge >= 0 && rippleAge < 1000) ripple = rippleAge / 1000;
      else if (marker.kind === 'incident' && this.overlay?.tone === 'proposed' && age >= 0) ripple = ((now - this.changedAt) % 3000) / 3000;
    }
    return {
      visible: reduced || age >= 0,
      scale: back * (1 + 0.18 * breath), opacity: marker.state === 'removed' ? 0.3 : 0.95 - 0.4 * breath,
      labelOpacity: reduced ? 1 : clamp(age / 200), labelOffset: reduced ? 0 : 4 * (1 - clamp(age / 200)), ripple,
      active: !reduced && (age < 380 || breathing || ripple !== undefined),
    };
  }

  route(route: OverlayRoute, now: number, reduced: boolean) {
    const at = this.reveal.get(route.id), age = at === undefined ? Infinity : now - at;
    const progress = reduced ? 1 : clamp(age / 500);
    const moving = !reduced && now - this.interactionAt < 12000;
    const flow = moving && this.overlay?.tone === 'proposed';
    const travel = moving && route.state === 'new' && progress === 1 ? ((now - this.changedAt) % 3600) / 2400 : undefined;
    return { progress, flow, travel: travel !== undefined && travel < 1 ? travel : undefined, active: !reduced && (progress < 1 || flow || moving && route.state === 'new') };
  }
}
