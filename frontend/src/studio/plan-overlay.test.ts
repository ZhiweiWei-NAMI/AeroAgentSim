import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { layoutOverlayLabels, overlayArc, overlayEase, overlayFocus, overlayLabelAnchor, overlayLabelOrder, overlayPosition, overlayRevealTimes, OverlayCameraMotion, OverlayTimeline, type OverlayMarker, type OverlayRoute, type PreviewOverlay } from './plan-overlay';
import { framePreview, visiblePreviewFrame } from './ScenePreview';

const marker = (id: string, state: OverlayMarker['state'] = 'existing', kind: OverlayMarker['kind'] = 'place'): OverlayMarker => ({ id, state, kind, label: id, position: [0, 0, 0] });
const route: OverlayRoute = { id: 'route', from: [0, 0, 0], to: [100, 0, 40], kind: 'handoff', state: 'new' };
const plan = (changes: Partial<PreviewOverlay> = {}): PreviewOverlay => ({ tone: 'proposed', markers: [marker('from'), { ...marker('to'), position: route.to }], routes: [route], ...changes });

describe('plan overlay geometry', () => {
  it('maps ENU without inventing elevation and preserves both route endpoints', () => {
    expect(overlayPosition([10, 20, 30]).toArray()).toEqual([10, 30, -20]);
    const arc = overlayArc(route);
    expect(arc.getPoint(0).toArray()).toEqual(overlayPosition(route.from).toArray());
    expect(arc.getPoint(1).toArray()).toEqual(overlayPosition(route.to).toArray());
  });

  it('raises the actual apex by a quarter of horizontal distance, capped at 120 metres', () => {
    for (const to of [[100, 0, 40], [0, 1000, 80], [60, 80, -20]] as [number, number, number][]) {
      const arc = overlayArc({ from: [0, 0, 10], to });
      const peak = (arc.v1.y - arc.v0.y) / (2 * arc.v1.y - arc.v0.y - arc.v2.y);
      expect(arc.getPoint(peak).y).toBeCloseTo(Math.max(10, to[2]) + Math.min(Math.hypot(to[0], to[1]) * 0.25, 120));
      expect(arc.getPoint(0.5).x).toBeCloseTo(to[0] / 2);
      expect(arc.getPoint(0.5).z).toBeCloseTo(-to[1] / 2);
    }
  });

  it('keeps coincident and vertical endpoints finite', () => {
    for (const to of [[0, 0, 0], [0, 0, 100]] as [number, number, number][]) {
      expect(overlayArc({ from: [0, 0, 0], to }).getPoints(12).every(point => point.toArray().every(Number.isFinite))).toBe(true);
    }
  });
});

describe('overlay ordering', () => {
  it('emphasises only routes at the exact supplied focused endpoint', () => {
    const overlay = plan({ focus: ['from'] });
    overlay.routes.push({ ...route, id: 'nearby', from: [0.1, 0, 0] });
    expect([...overlayFocus(overlay)]).toEqual(['from', 'route']);
    expect([...overlayFocus({ ...overlay, focus: ['route'] })]).toEqual(['route']);
  });

  it('waits for both endpoints even when a route was listed first, without hiding unlisted items', () => {
    const times = overlayRevealTimes(plan({ reveal: { key: 'v1', order: ['route', 'to', 'missing', 'from', 'from'] } }), 1000);
    expect(times.get('to')).toBe(1140);
    expect(times.get('from')).toBe(1280);
    expect(times.get('route')).toBe(1660);
    expect(times.has('missing')).toBe(false);
    expect(overlayRevealTimes(plan({ reveal: { key: 'v2', order: ['from'] } }), 0).has('route')).toBe(false);
  });

  it('prioritises focus, diffs, incidents and handoffs with stable ordering', () => {
    const markers = [marker('ordinary'), marker('handoff', 'existing', 'handoff'), marker('incident', 'existing', 'incident'), marker('new', 'new'), marker('changed', 'changed'), marker('focus')];
    expect(overlayLabelOrder(markers, new Set(['focus'])).map(item => item.id)).toEqual(['focus', 'new', 'changed', 'incident', 'handoff', 'ordinary']);
    expect(markers[0].id).toBe('ordinary');
  });

  it('nudges labels vertically, rejects overflow and caps the visible set', () => {
    const boxes = layoutOverlayLabels([
      { id: 'focus', x: 10, y: 100, width: 80, height: 20 },
      { id: 'new', x: 10, y: 100, width: 80, height: 20 },
      { id: 'outside', x: -1, y: 20, width: 80, height: 20 },
    ], 300, 200);
    expect(boxes.map(box => [box.id, box.y])).toEqual([['focus', 100], ['new', 76]]);
    const many = Array.from({ length: 30 }, (_, index) => ({ id: String(index), x: index * 20, y: 50, width: 10, height: 10 }));
    expect(layoutOverlayLabels(many, 1000, 200)).toHaveLength(24);
  });

  it('projects label anchors only inside the camera frustum', () => {
    const camera = new THREE.PerspectiveCamera(45, 800 / 600, 1, 1000);
    camera.position.set(0, 0, 10); camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
    expect(overlayLabelAnchor(new THREE.Vector3(), camera, 800, 600)).toEqual({ x: 400, y: 300 });
    for (const point of [new THREE.Vector3(0, 0, 20), new THREE.Vector3(1000, 0, 0), new THREE.Vector3(0, 0, 9.5), new THREE.Vector3(0, 0, -2000)]) {
      expect(overlayLabelAnchor(point, camera, 800, 600)).toBeUndefined();
    }
  });
});

describe('overlay state and motion', () => {
  it('breathes only for the first six seconds and preserves age across focus and tone changes', () => {
    const timeline = new OverlayTimeline(), item = marker('new', 'new'), overlay = plan({ markers: [item], routes: [] });
    expect(timeline.update(overlay, 0).rebuild).toBe(true);
    expect(timeline.marker(item, 900, false)).toMatchObject({ scale: 1.18, active: true });
    expect(timeline.marker(item, 900, false).opacity).toBeCloseTo(0.55);
    expect(timeline.update({ ...overlay, focus: ['new'] }, 1000).rebuild).toBe(false);
    timeline.update({ ...overlay, tone: 'configured' }, 4000);
    expect(timeline.marker(item, 6000, false)).toMatchObject({ scale: 1, opacity: 0.95, active: false });
  });

  it('replays reveal and requests framing once per key, never on hover', () => {
    const timeline = new OverlayTimeline(), overlay = plan({ reveal: { key: 'v1', order: ['to', 'from', 'route'] }, frame: { key: 'v1', ids: ['route'] } });
    expect(timeline.update(overlay, 0)).toEqual({ rebuild: true, frame: true });
    expect(timeline.marker(overlay.markers[0], 0, false).visible).toBe(false);
    expect(timeline.route(route, 500, false).progress).toBe(0);
    expect(timeline.update({ ...overlay, focus: ['from'] }, 1000)).toEqual({ rebuild: false, frame: false });
    expect(timeline.route(route, 1100, false).progress).toBe(1);
    timeline.update({ ...overlay, reveal: { key: 'v2', order: ['to', 'from'] } }, 2000);
    expect(timeline.marker(overlay.markers[0], 2000, false).visible).toBe(false);
  });

  it('makes reduced motion instant and static, including proposed incidents', () => {
    const timeline = new OverlayTimeline(), item = marker('incident', 'new', 'incident');
    timeline.update(plan({ markers: [item], reveal: { key: 'v1', order: ['route', 'incident'] } }), 0);
    expect(timeline.marker(item, 0, true)).toMatchObject({ visible: true, scale: 1, opacity: 0.95, ripple: undefined, active: false });
    expect(timeline.route(route, 0, true)).toMatchObject({ progress: 1, flow: false, travel: undefined, active: false });
  });

  it('settles changed markers, greys removed markers, and repeats proposed incident ripples', () => {
    const timeline = new OverlayTimeline(), changed = marker('changed', 'changed'), incident = marker('incident', 'existing', 'incident');
    timeline.update(plan({ markers: [changed, incident] }), 0);
    expect(timeline.marker(changed, 500, false).ripple).toBe(0.5);
    expect(timeline.marker(changed, 1000, false).active).toBe(false);
    expect(timeline.marker(incident, 4500, false).ripple).toBe(0.5);
    timeline.update(plan({ tone: 'configured', markers: [changed, incident] }), 5000);
    expect(timeline.marker(incident, 6500, false).active).toBe(false);
    expect(timeline.marker({ ...incident, state: 'removed' }, 6500, false)).toMatchObject({ opacity: 0.3, ripple: undefined, active: false });
  });

  it('idles routes after twelve seconds and resumes on interaction', () => {
    const timeline = new OverlayTimeline(); timeline.update(plan(), 0);
    expect(timeline.route(route, 100, false)).toMatchObject({ flow: true, active: true });
    expect(timeline.route(route, 12000, false)).toMatchObject({ flow: false, travel: undefined, active: false });
    timeline.interact(13000);
    expect(timeline.route(route, 13000, false).flow).toBe(true);
    timeline.update(plan({ tone: 'configured', routes: [{ ...route, state: 'existing' }] }), 14000);
    expect(timeline.route({ ...route, state: 'existing' }, 14000, false).active).toBe(false);
  });

  it('clamps the camera easing and eases symmetrically through its midpoint', () => {
    expect([-1, 0, 0.5, 1, 2].map(overlayEase)).toEqual([0, 0, 0.5, 1, 1]);
    expect(overlayEase(0.25)).toBeCloseTo(1 - overlayEase(0.75));
  });
});

describe('preview framing between panels', () => {
  const rect = (left: number, top: number, width: number, height: number) => ({ left, top, width, height, right: left + width, bottom: top + height });
  const full = rect(100, 50, 1400, 900), toolbar = rect(100, 50, 1400, 80);

  it('reserves the assistant on the right and plan board on the left', () => {
    const assistant = rect(1150, 150, 350, 700), board = rect(100, 150, 300, 700);
    const frame = visiblePreviewFrame(full, [assistant], [board], toolbar)!;
    expect(frame).toEqual({ x: 320, y: 100, width: 710, height: 736, fullWidth: 1400, fullHeight: 900 });
    const camera = new THREE.PerspectiveCamera(45), target = new THREE.Vector3();
    const bounds = new THREE.Box3(new THREE.Vector3(-50, 0, -70), new THREE.Vector3(300, 240, 150));
    framePreview(camera, bounds, target, frame); camera.lookAt(target); camera.updateMatrixWorld();
    for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
      const point = new THREE.Vector3(x, y, z).project(camera);
      const px = (point.x + 1) * full.width / 2, py = (1 - point.y) * full.height / 2;
      expect(px).toBeGreaterThan(frame.x); expect(px).toBeLessThan(frame.x + frame.width);
      expect(py).toBeGreaterThan(frame.y); expect(py).toBeLessThan(frame.y + frame.height);
    }
  });

  it('uses the innermost right panel and ignores hidden or off-stage obstructions', () => {
    const config = rect(900, 150, 600, 700), assistant = rect(1150, 150, 350, 700);
    expect(visiblePreviewFrame(full, [config, assistant], [], toolbar)?.width).toBe(760);
    expect(visiblePreviewFrame(full, [rect(0, 0, 0, 0), rect(1600, 0, 100, 200)], [], toolbar)).toBeUndefined();
    expect(visiblePreviewFrame(rect(0, 0, 0, 0), [assistant], [])).toBeUndefined();
  });

  it('can frame with only a plan board and keeps a positive frame when panels overlap', () => {
    expect(visiblePreviewFrame(full, [], [rect(100, 150, 300, 700)])?.x).toBe(320);
    expect(visiblePreviewFrame(full, [rect(700, 150, 800, 700)], [rect(100, 150, 900, 700)])?.width).toBe(1);
  });
});

describe('camera motion', () => {
  it('eases camera, target and projection together for exactly 600 milliseconds', () => {
    const camera = new THREE.PerspectiveCamera(45, 1.5), target = new THREE.Vector3(); camera.position.set(0, 100, 100);
    const destination = camera.clone(); destination.position.set(200, 300, 400); destination.setViewOffset(1200, 800, 200, -40, 1200, 800);
    const motion = new OverlayCameraMotion(camera, target);
    motion.start(destination, new THREE.Vector3(100, 20, 40), 1000);
    motion.advance(1300, false);
    expect(camera.position.toArray()).toEqual([100, 200, 250]);
    expect(target.toArray()).toEqual([50, 10, 20]);
    expect(camera.view?.offsetX).toBe(100); expect(camera.view?.offsetY).toBe(-20);
    expect(motion.active).toBe(true);
    motion.advance(1600, false);
    expect(camera.position.toArray()).toEqual(destination.position.toArray());
    expect(camera.view?.offsetX).toBe(200); expect(motion.active).toBe(false);
  });

  it('keeps the user camera position after a drag cancels framing', () => {
    const camera = new THREE.PerspectiveCamera(), target = new THREE.Vector3();
    const destination = camera.clone(); destination.position.set(100, 100, 100);
    const motion = new OverlayCameraMotion(camera, target); motion.start(destination, new THREE.Vector3(10, 10, 10), 0);
    motion.advance(150, false); motion.cancel(); camera.position.set(300, 20, 60);
    const stoppedTarget = target.clone(); motion.advance(600, false);
    expect(camera.position.toArray()).toEqual([300, 20, 60]); expect(target.equals(stoppedTarget)).toBe(true);
    expect(motion.active).toBe(false);
  });

  it('frames instantly under reduced motion and clears an obsolete view offset', () => {
    const camera = new THREE.PerspectiveCamera(), target = new THREE.Vector3(); camera.setViewOffset(1000, 800, 100, 0, 1000, 800);
    const destination = camera.clone(); destination.clearViewOffset(); destination.position.set(100, 200, 300);
    const motion = new OverlayCameraMotion(camera, target); motion.start(destination, new THREE.Vector3(10, 20, 30), 0); motion.advance(0, true);
    expect(camera.position.toArray()).toEqual([100, 200, 300]); expect(target.toArray()).toEqual([10, 20, 30]);
    expect(camera.view?.enabled).toBe(false); expect(motion.active).toBe(false);
  });
});
