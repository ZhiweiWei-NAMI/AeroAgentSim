import * as THREE from 'three';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import './plan-overlay.css';

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
export function overlayLabelAnchor(position: THREE.Vector3, camera: THREE.PerspectiveCamera, width: number, height: number): { x: number; y: number } | undefined {
  if (position.clone().applyMatrix4(camera.matrixWorldInverse).z >= 0) return;
  const point = position.clone().project(camera);
  if (point.z < -1 || point.z > 1 || Math.abs(point.x) > 1 || Math.abs(point.y) > 1) return;
  return { x: (point.x + 1) * width / 2, y: (1 - point.y) * height / 2 };
}

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
    const back = 1 + 3.16 * Math.pow(progress - 1, 3) + 2.16 * Math.pow(progress - 1, 2);
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

interface OverlayViewOptions {
  scene: THREE.Scene; camera: THREE.PerspectiveCamera; target: THREE.Vector3; host: HTMLElement;
  invalidate: () => void;
  frameBounds: (bounds: THREE.Box3, camera: THREE.PerspectiveCamera, target: THREE.Vector3) => void;
}
interface MarkerView {
  data: OverlayMarker; head: THREE.Sprite; halo: THREE.Line; stem: THREE.Line; ripple: THREE.Line;
  label: HTMLSpanElement; labelSize: THREE.Vector2; color: THREE.Color;
}
interface RouteView {
  data: OverlayRoute; arc: THREE.QuadraticBezierCurve3; line: Line2; dot: THREE.Sprite;
  color: THREE.Color; progress: number; dashOffset: number;
}
interface CameraTween {
  at: number; from: THREE.PerspectiveCamera; to: THREE.PerspectiveCamera;
  fromTarget: THREE.Vector3; toTarget: THREE.Vector3;
}

export class OverlayCameraMotion {
  private tween?: CameraTween;

  constructor(private readonly camera: THREE.PerspectiveCamera, private readonly target: THREE.Vector3) {}

  get active() { return this.tween !== undefined; }

  start(to: THREE.PerspectiveCamera, toTarget: THREE.Vector3, now: number) {
    this.tween = { at: now, from: this.camera.clone(), to: to.clone(), fromTarget: this.target.clone(), toTarget: toTarget.clone() };
  }

  cancel() { this.tween = undefined; }

  advance(now: number, reduced: boolean) {
    const tween = this.tween; if (!tween) return;
    const p = reduced ? 1 : overlayEase((now - tween.at) / 600), { camera, target } = this;
    camera.position.lerpVectors(tween.from.position, tween.to.position, p);
    target.lerpVectors(tween.fromTarget, tween.toTarget, p);
    camera.near = THREE.MathUtils.lerp(tween.from.near, tween.to.near, p);
    camera.far = Math.max(camera.far, tween.from.far, tween.to.far);
    const from = tween.from.view, to = tween.to.view;
    const view = to?.enabled ? to : from?.enabled ? from : undefined;
    if (view) camera.setViewOffset(view.fullWidth, view.fullHeight, THREE.MathUtils.lerp(from?.enabled ? from.offsetX : 0, to?.enabled ? to.offsetX : 0, p), THREE.MathUtils.lerp(from?.enabled ? from.offsetY : 0, to?.enabled ? to.offsetY : 0, p), view.fullWidth, view.fullHeight);
    if (p === 1) { if (!to?.enabled) camera.clearViewOffset(); this.cancel(); }
    camera.updateProjectionMatrix();
  }
}

const ROUTE_COLORS = { task: '#38c6f4', handoff: '#8a7dff', response: '#ff6b5e', notice: '#f4b740' };
function markerColor(marker: OverlayMarker, tone: PreviewOverlay['tone']): string {
  if (marker.state === 'removed') return '#7d8792';
  if (marker.kind === 'incident') return '#ff6b5e';
  if (marker.kind === 'handoff' || tone === 'proposed') return '#8a7dff';
  return marker.kind === 'vehicle' || marker.kind === 'fleet' ? '#38c6f4' : '#1f7ae0';
}

function headTexture(kind: OverlayMarker['kind'] | 'dot', color: string, removed = false, blueprint = false): THREE.CanvasTexture {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 96;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Plan overlay requires a 2D canvas / 方案叠加层需要二维画布');
  ctx.translate(48, 48);
  if (kind === 'dot') {
    const gradient = ctx.createRadialGradient(0, 0, 3, 0, 0, 43);
    gradient.addColorStop(0, '#ffffff'); gradient.addColorStop(0.2, color); gradient.addColorStop(1, `${color}00`);
    ctx.fillStyle = gradient; ctx.fillRect(-48, -48, 96, 96);
  } else {
    ctx.shadowColor = blueprint ? color : 'rgba(9,20,36,.3)'; ctx.shadowBlur = blueprint ? 12 : 7; ctx.shadowOffsetY = blueprint ? 0 : 3;
    ctx.fillStyle = blueprint ? '#0b1329' : '#ffffff'; ctx.strokeStyle = blueprint ? color : 'rgba(20,32,46,.4)'; ctx.lineWidth = 2;
    ctx.beginPath();
    if (kind === 'handoff') { ctx.moveTo(0, -35); ctx.lineTo(35, 0); ctx.lineTo(0, 35); ctx.lineTo(-35, 0); ctx.closePath(); }
    else ctx.arc(0, 0, 32, 0, Math.PI * 2);
    ctx.fill(); ctx.stroke(); ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 5; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    const line = (points: number[][]) => { ctx.beginPath(); points.forEach(([x, y], index) => index ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke(); };
    const circle = (x: number, y: number, r: number) => { ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke(); };
    switch (kind) {
      case 'incident': line([[0, -17], [0, 5]]); ctx.beginPath(); ctx.arc(0, 17, 3.5, 0, Math.PI * 2); ctx.fill(); break;
      case 'handoff': line([[-17, -6], [15, -6], [8, -13]]); line([[17, 8], [-15, 8], [-8, 15]]); break;
      case 'kitchen': line([[-16, -18], [-16, -4], [-6, -4], [-6, -18]]); line([[-11, -18], [-11, 19]]); line([[12, 19], [12, -18], [19, -6], [12, -3]]); break;
      case 'customer': line([[-19, -1], [0, -19], [19, -1]]); line([[-13, -5], [-13, 18], [13, 18], [13, -5]]); line([[0, 18], [0, 7]]); break;
      case 'vertiport': circle(0, 0, 22); line([[-9, -12], [-9, 12]]); line([[9, -12], [9, 12]]); line([[-9, 0], [9, 0]]); break;
      case 'charging': line([[4, -22], [-13, 3], [5, 3], [-3, 22], [16, -5], [0, -5], [4, -22]]); break;
      case 'vehicle': line([[-15, -15], [15, 15]]); line([[-15, 15], [15, -15]]); for (const x of [-16, 16]) for (const y of [-16, 16]) circle(x, y, 6); break;
      case 'fleet': for (const [x, y] of [[0, -14], [-14, 12], [14, 12]]) { line([[x, y - 7], [x + 7, y + 6], [x - 7, y + 6], [x, y - 7]]); } break;
      case 'place': circle(0, 0, 13); ctx.beginPath(); ctx.arc(0, 0, 4, 0, Math.PI * 2); ctx.fill(); break;
    }
    if (removed) { ctx.strokeStyle = '#65717e'; ctx.lineWidth = 6; line([[-24, 24], [24, -24]]); }
  }
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** Shares ScenePreview's invalidation loop; no renderer, simulation state or independent RAF is created here. */
export class PlanOverlayView {
  private readonly group = new THREE.Group();
  private readonly labels = document.createElement('div');
  private readonly timeline = new OverlayTimeline();
  private readonly geometries = new Set<THREE.BufferGeometry>();
  private readonly materials = new Set<THREE.Material>();
  private readonly textures = new Set<THREE.Texture>();
  private readonly media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  private reduced = this.media?.matches ?? false;
  private markers: MarkerView[] = [];
  private routes: RouteView[] = [];
  private overlay?: PreviewOverlay;
  private focus = new Set<string>();
  private readonly motion: OverlayCameraMotion;
  private lastTime?: number;
  private disposed = false;
  private wake = () => this.interact();
  private motionChange = () => { this.reduced = this.media?.matches ?? false; this.options.invalidate(); };

  constructor(private readonly options: OverlayViewOptions) {
    this.motion = new OverlayCameraMotion(options.camera, options.target);
    this.group.name = 'plan-overlay'; options.scene.add(this.group);
    this.labels.className = 'pv-labels'; options.host.append(this.labels);
    this.media?.addEventListener('change', this.motionChange);
    for (const event of ['pointermove', 'pointerdown', 'wheel']) options.host.addEventListener(event, this.wake, { passive: true });
  }

  update(overlay: PreviewOverlay, now = performance.now()) {
    this.overlay = overlay; this.focus = overlayFocus(overlay);
    const change = this.timeline.update(overlay, now);
    if (change.rebuild) {
      this.clear();
      try {
        this.markers = overlay.markers.map(marker => this.createMarker(marker, overlay.tone));
        this.routes = overlay.routes.map(route => this.createRoute(route, overlay.tone));
      } catch (error) { this.clear(); throw error; }
    }
    this.labels.style.visibility = 'hidden';
    for (const view of this.markers) {
      view.label.className = ['pv-label', `is-${view.data.state}`, `is-${view.data.kind}`, this.focus.has(view.data.id) ? 'is-focus' : ''].join(' ');
      view.label.hidden = false;
    }
    for (const view of this.markers) view.labelSize.set(view.label.offsetWidth, view.label.offsetHeight);
    for (const view of this.markers) view.label.hidden = true;
    this.labels.style.visibility = '';
    if (change.frame) {
      const bounds = this.bounds(overlay.frame!.ids);
      if (!bounds.isEmpty()) {
        const to = this.options.camera.clone(), toTarget = this.options.target.clone();
        this.options.frameBounds(bounds, to, toTarget);
        this.motion.start(to, toTarget, now);
      }
    }
    this.options.invalidate();
  }

  bounds(ids?: string[]): THREE.Box3 {
    const selected = ids && new Set(ids), bounds = new THREE.Box3();
    for (const marker of this.overlay?.markers ?? []) if (!selected || selected.has(marker.id)) {
      bounds.expandByPoint(overlayPosition(marker.position));
      bounds.expandByPoint(new THREE.Vector3(marker.position[0], 0, -marker.position[1]));
    }
    for (const route of this.overlay?.routes ?? []) if (!selected || selected.has(route.id)) for (const point of overlayArc(route).getPoints(64)) bounds.expandByPoint(point);
    if (!bounds.isEmpty()) bounds.expandByScalar(12);
    return bounds;
  }

  interact(cancelFrame = false) {
    if (cancelFrame) this.cancelFrame();
    this.timeline.interact(performance.now()); this.options.invalidate();
  }

  cancelFrame() { this.motion.cancel(); }

  advanceCamera(now: number) { this.motion.advance(now, this.reduced); }

  render(now: number): boolean {
    if (!this.overlay || this.disposed) return false;
    const { camera, host } = this.options, width = Math.max(host.clientWidth, 1), height = Math.max(host.clientHeight, 1);
    camera.updateMatrixWorld();
    const elapsed = this.lastTime === undefined ? 0 : Math.min(now - this.lastTime, 100); this.lastTime = now;
    const faded = (id: string) => this.focus.size && !this.focus.has(id) ? 0.35 : 1;
    const worldPerPixel = (point: THREE.Vector3) => 2 * Math.max(camera.near, -point.clone().applyMatrix4(camera.matrixWorldInverse).z) / (camera.projectionMatrix.elements[5] * height);
    const spriteScale = (pixels: number) => pixels * 2 / (height * camera.projectionMatrix.elements[5]);
    let active = this.motion.active;
    const candidates: OverlayLabelBox[] = [], views = new Map(this.markers.map(view => [view.data.id, view]));
    for (const view of this.markers) {
      const state = this.timeline.marker(view.data, now, this.reduced), focused = this.focus.has(view.data.id);
      const scale = focused ? 1.25 : 1, opacity = state.opacity * faded(view.data.id);
      active ||= state.active;
      view.head.visible = view.halo.visible = view.stem.visible = state.visible;
      view.head.scale.setScalar(spriteScale(25) * state.scale * scale);
      view.head.material.opacity = opacity;
      const material = view.halo.material as THREE.LineBasicMaterial;
      material.opacity = opacity * (focused ? 1 : 0.8); material.color.copy(view.color).lerp(new THREE.Color('#ffffff'), focused ? 0.2 : 0);
      const radius = worldPerPixel(view.halo.position) * 16 * scale;
      view.halo.scale.setScalar(radius * state.scale);
      (view.stem.material as THREE.LineBasicMaterial).opacity = opacity * 0.65;
      view.ripple.visible = state.visible && state.ripple !== undefined;
      if (state.ripple !== undefined) {
        view.ripple.scale.setScalar(radius * (1 + state.ripple * 2));
        (view.ripple.material as THREE.LineBasicMaterial).opacity = (1 - state.ripple) * opacity * 0.6;
      }
      view.label.style.opacity = String(opacity * state.labelOpacity);
      view.label.hidden = true;
    }
    for (const marker of overlayLabelOrder(this.overlay.markers, this.focus)) {
      const view = views.get(marker.id)!;
      const state = this.timeline.marker(marker, now, this.reduced);
      if (!state.visible || state.labelOpacity === 0) continue;
      const point = overlayLabelAnchor(view.head.position, camera, width, height);
      if (!point) continue;
      const { x: boxWidth, y: boxHeight } = view.labelSize;
      candidates.push({ id: marker.id, x: point.x - boxWidth / 2, y: point.y - boxHeight - 16 + state.labelOffset, width: boxWidth, height: boxHeight });
    }
    const placed = layoutOverlayLabels(candidates, width, height);
    for (const box of placed) {
      const label = views.get(box.id)!.label;
      label.hidden = false; label.style.left = `${box.x}px`; label.style.top = `${box.y}px`;
    }
    const labelIds = JSON.stringify(placed.map(box => box.id));
    if (host.dataset.overlayLabels !== labelIds) host.dataset.overlayLabels = labelIds;
    for (const view of this.routes) {
      const state = this.timeline.route(view.data, now, this.reduced), focused = this.focus.has(view.data.id);
      active ||= state.active;
      const material = view.line.material;
      material.resolution.set(width, height);
      material.uniforms.linewidth.value = focused ? 2.5 : 2;
      material.opacity = (this.overlay.tone === 'proposed' ? 0.88 : 0.9) * faded(view.data.id);
      material.color.copy(view.color).lerp(new THREE.Color('#ffffff'), focused ? 0.18 : 0);
      material.dashScale = 1 / worldPerPixel(view.arc.getPoint(0.5));
      if (state.flow) view.dashOffset -= elapsed * 0.04;
      material.dashOffset = view.dashOffset;
      if (view.progress !== state.progress) {
        // Replace the small geometry during reveal so discarded instanced buffers are released too.
        this.geometries.delete(view.line.geometry); view.line.geometry.dispose();
        view.line.geometry = this.routeGeometry(view.arc, state.progress); view.line.computeLineDistances(); view.progress = state.progress;
      }
      view.line.visible = state.progress > 0 && view.arc.getLength() > 0;
      view.dot.visible = state.travel !== undefined;
      if (state.travel !== undefined) {
        view.dot.position.copy(view.arc.getPointAt(state.travel)); view.dot.scale.setScalar(spriteScale(18));
        view.dot.material.opacity = faded(view.data.id) * Math.sin(state.travel * Math.PI);
      }
    }
    return active;
  }

  dispose() {
    if (this.disposed) return; this.disposed = true;
    this.cancelFrame(); this.clear(); this.group.removeFromParent(); this.labels.remove();
    this.media?.removeEventListener('change', this.motionChange);
    for (const event of ['pointermove', 'pointerdown', 'wheel']) this.options.host.removeEventListener(event, this.wake);
  }

  private clear() {
    for (const geometry of this.geometries) geometry.dispose();
    for (const material of this.materials) material.dispose();
    for (const texture of this.textures) texture.dispose();
    this.geometries.clear(); this.materials.clear(); this.textures.clear();
    this.group.clear(); this.labels.replaceChildren(); this.markers = []; this.routes = [];
    this.options.host.dataset.overlayLabels = '[]';
  }

  private sprite(kind: OverlayMarker['kind'] | 'dot', color: string, removed = false) {
    const blueprint = this.overlay?.tone === 'proposed';
    const texture = headTexture(kind, color, removed, blueprint); this.textures.add(texture);
    const material = new THREE.SpriteMaterial({ map: texture, transparent: true, sizeAttenuation: false, depthTest: false, depthWrite: false, toneMapped: false });
    if (blueprint && kind === 'dot') material.blending = THREE.AdditiveBlending;
    this.materials.add(material);
    const sprite = new THREE.Sprite(material); sprite.renderOrder = 104; sprite.frustumCulled = false;
    this.group.add(sprite); return sprite;
  }

  private thinLine(points: THREE.Vector3[], color: string, dashed = false) {
    const geometry = new THREE.BufferGeometry().setFromPoints(points); this.geometries.add(geometry);
    const options: THREE.LineBasicMaterialParameters = { color, transparent: true, depthTest: false, depthWrite: false, toneMapped: false, blending: this.overlay?.tone === 'proposed' ? THREE.AdditiveBlending : THREE.NormalBlending };
    const material = dashed ? new THREE.LineDashedMaterial({ ...options, dashSize: 0.2, gapSize: 0.16 }) : new THREE.LineBasicMaterial(options);
    this.materials.add(material);
    const line = new THREE.Line(geometry, material); line.computeLineDistances(); line.renderOrder = 102; line.frustumCulled = false;
    this.group.add(line); return line;
  }

  private createMarker(data: OverlayMarker, tone: PreviewOverlay['tone']): MarkerView {
    const color = markerColor(data, tone), head = this.sprite(data.kind, color, data.state === 'removed'); head.position.copy(overlayPosition(data.position));
    const ring = Array.from({ length: 65 }, (_, index) => new THREE.Vector3(Math.cos(index * Math.PI / 32), 0, Math.sin(index * Math.PI / 32)));
    const halo = this.thinLine(ring, color, tone === 'proposed' || data.state === 'removed'); halo.position.set(data.position[0], 0.12, -data.position[1]);
    const stem = this.thinLine([new THREE.Vector3(data.position[0], 0, -data.position[1]), head.position.clone()], color);
    const ripple = this.thinLine(ring, color); ripple.position.copy(halo.position);
    const label = document.createElement('span'), text = document.createElement('span');
    text.className = 'pv-label-text'; text.textContent = data.label; label.append(text); label.hidden = true;
    label.dataset.overlayId = data.id; label.style.setProperty('--pv-color', color); this.labels.append(label);
    return { data, head, halo, stem, ripple, label, labelSize: new THREE.Vector2(), color: new THREE.Color(color) };
  }

  private routeGeometry(arc: THREE.QuadraticBezierCurve3, progress = 1): LineGeometry {
    const geometry = new LineGeometry(); this.geometries.add(geometry);
    geometry.setPositions(Array.from({ length: 65 }, (_, index) => arc.getPoint(index / 64 * progress).toArray()).flat());
    return geometry;
  }

  private createRoute(data: OverlayRoute, tone: PreviewOverlay['tone']): RouteView {
    const color = new THREE.Color(ROUTE_COLORS[data.kind]);
    if (tone === 'proposed') color.lerp(new THREE.Color('#8a7dff'), 0.3);
    const arc = overlayArc(data), geometry = this.routeGeometry(arc);
    const material = new LineMaterial({ color, dashed: tone === 'proposed', dashSize: 10, gapSize: 7, transparent: true, depthTest: false, depthWrite: false, toneMapped: false, blending: tone === 'proposed' ? THREE.AdditiveBlending : THREE.NormalBlending });
    material.uniforms.linewidth.value = 2; this.materials.add(material);
    const line = new Line2(geometry, material); line.computeLineDistances(); line.renderOrder = 101; line.frustumCulled = false; this.group.add(line);
    const dot = this.sprite('dot', ROUTE_COLORS[data.kind]);
    return { data, arc, line, dot, color, progress: 1, dashOffset: 0 };
  }
}
