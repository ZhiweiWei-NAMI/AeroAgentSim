import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { loadOsmBuildings } from '../scene/source-geometry';
import { Assets, disposeObject } from '../viewport/assets';
import { loadTrafficCity, type CityProgress } from '../scene/traffic-city';
import type { Workspace } from './api';
import { PlanOverlayView, type PreviewOverlay } from './plan-overlay';

type LayerName = 'ground' | 'buildings' | 'roads' | 'entities' | 'airspace';
const LAYER_NAMES: LayerName[] = ['ground', 'buildings', 'roads', 'entities', 'airspace'];

interface Context {
  scene: THREE.Scene; abort: AbortController; groups: Record<LayerName, THREE.Group>;
  buildingsRequested: boolean; loadBuildings: () => void;
  fitView: () => void;
  refreshBounds: (scenario: Workspace['scenario']) => void;
  invalidate: () => void;
  setOverlay: (overlay?: PreviewOverlay) => void;
}

/** Remove and dispose every child of a layer group while keeping the group itself in the scene. */
function disposePreview(root: THREE.Object3D) { root.traverse(node => { if (node instanceof THREE.Sprite && !node.material.userData.sharedAsset) node.material.dispose(); }); disposeObject(root); }
function clearGroup(group: THREE.Group) { for (const child of [...group.children]) disposePreview(child); }

/**
 * The renderer, camera and downloaded city live for one scene identity. Layer checkboxes only
 * flip `group.visible`; cheap groups (roads, entities, airspace) rebuild when their content changes.
 */
/** Positions authored in the draft: bound entity facts and generated fleet starts (ENU). */
export function draftBinding(scenario: Workspace['scenario'], typeId: string) {
  const parents = new Map((scenario.registry?.types ?? []).map((type: any) => [type.id, type.parents ?? []]));
  const queue = [typeId], seen = new Set<string>();
  while (queue.length) {
    const current = queue.shift()!; if (seen.has(current)) continue; seen.add(current);
    const binding = Array.isArray(scenario.presentation) && scenario.presentation.find((row: any) => row.typeId === current);
    if (binding) return binding;
    queue.push(...(parents.get(current) as string[] ?? []));
  }
}

export function authoredPositions(scenario: Workspace['scenario']): number[][] {
  const points: number[][] = [];
  for (const entity of scenario.entities ?? []) {
    const binding = draftBinding(scenario, entity.type);
    const pos = binding ? entity.facts?.[binding.positionField] : undefined;
    if (Array.isArray(pos) && pos.length === 3 && pos.every(Number.isFinite)) points.push(pos);
  }
  for (const generator of Object.values(scenario.generators ?? {}) as any[]) for (const start of generator?.starts ?? []) if (Array.isArray(start) && start.length === 3 && start.every(Number.isFinite)) points.push(start);
  return points;
}

/** Fit the complete authored/loaded volume, including its height, at any window aspect. */
interface PreviewFrame { x: number; y: number; width: number; height: number; fullWidth: number; fullHeight: number }
type PreviewRect = Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom' | 'width' | 'height'>;
export function visiblePreviewFrame(full: PreviewRect, rightPanels: PreviewRect[], leftPanels: PreviewRect[], toolbar?: PreviewRect): PreviewFrame | undefined {
  if (full.width <= 0 || full.height <= 0) return;
  const intersects = (rect: PreviewRect) => rect.width > 0 && rect.height > 0 && rect.right > full.left && rect.left < full.right && rect.bottom > full.top && rect.top < full.bottom;
  const right = rightPanels.filter(intersects), left = leftPanels.filter(intersects);
  if (!right.length && !left.length) return;
  const x = Math.min(full.width - 1, Math.max(20, ...left.map(panel => panel.right - full.left + 20)));
  const edge = Math.min(full.width - 20, ...right.map(panel => panel.left - full.left - 20));
  const y = toolbar ? toolbar.bottom - full.top + 20 : 20;
  return { x, y, width: Math.max(1, edge - x), height: Math.max(1, full.height - y - 64), fullWidth: full.width, fullHeight: full.height };
}

export function framePreview(camera: THREE.PerspectiveCamera, bounds: THREE.Box3, target: THREE.Vector3, frame?: PreviewFrame) {
  const sphere = bounds.getBoundingSphere(new THREE.Sphere());
  const angle = THREE.MathUtils.degToRad(camera.fov / 2);
  const vertical = frame ? Math.atan(Math.tan(angle) * frame.height / frame.fullHeight) : angle;
  const horizontal = Math.atan(Math.tan(angle) * (frame ? frame.width / frame.fullHeight : camera.aspect));
  const distance = Math.max(sphere.radius, 1) / Math.sin(Math.min(vertical, horizontal)) * 1.08;
  target.copy(sphere.center);
  camera.position.copy(sphere.center).add(new THREE.Vector3(0.7, 1, 0.9).normalize().multiplyScalar(distance));
  if (frame) { camera.aspect = frame.fullWidth / frame.fullHeight; camera.setViewOffset(frame.fullWidth, frame.fullHeight, frame.fullWidth / 2 - frame.x - frame.width / 2, frame.fullHeight / 2 - frame.y - frame.height / 2, frame.fullWidth, frame.fullHeight); }
  else camera.clearViewOffset();
  camera.near = Math.max(0.1, distance / 10000); camera.far = Math.max(distance * 4, 1000); camera.updateProjectionMatrix();
}

/** `bare`: fill the owner's stage with no hint text (the one-scene workbench). */
export function ScenePreview({ workspace, api, layers, bare = false, overlay }: { workspace: Workspace; api: string; layers: string[]; bare?: boolean; overlay?: PreviewOverlay }) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const hintId = useId();
  const root = useRef<HTMLDivElement>(null);
  const ctx = useRef<Context>();
  const latest = useRef({ workspace, layers, overlay }); latest.current = { workspace, layers, overlay };
  const [error, setError] = useState<{ kind: 'descriptor' | 'runtime'; detail?: string }>(), [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<CityProgress>(), [rendered, setRendered] = useState(false);
  // The shared city follows the draft's declared city (choosing none hides it).
  const cityScene = (ws: Workspace) => ws.scenario?.city === 'huangpu' ? ws.demo_console?.scene : undefined;
  const city = cityScene(workspace);
  const sceneKey = JSON.stringify([workspace.id, api, city?.city?.kind ?? null, city?.city?.url ?? null, city?.camera ?? null,
    workspace.scene?.ground ?? null, workspace.scene?.origin ?? null]);
  const roadsKey = JSON.stringify(workspace.scene?.roads ?? null);
  const entitiesKey = JSON.stringify([workspace.scenario.entities, workspace.scenario.presentation ?? null, workspace.scenario.generators ?? null, workspace.scenario.registry?.types ?? null]);
  const layerKey = useMemo(() => LAYER_NAMES.map(name => layers.includes(name) ? 1 : 0).join(''), [layers]);
  const overlayKey = JSON.stringify(overlay);

  useEffect(() => {
    if (!root.current) return;
    const host = root.current, abort = new AbortController(), { workspace: ws } = latest.current;
    const scene = new THREE.Scene(); scene.background = new THREE.Color(bare ? '#cdd9e4' : '#edf2f6'); setError(undefined); setLoading(false); setProgress(undefined); setRendered(false);
    let pendingBuildings = 0;
    const beginLoad = () => { pendingBuildings++; setLoading(true); };
    const finishLoad = () => { pendingBuildings--; if (!abort.signal.aborted) setLoading(pendingBuildings > 0); };
    let assets: Assets | undefined, renderer: THREE.WebGLRenderer | undefined, controls: OrbitControls | undefined, observer: ResizeObserver | undefined, layoutObserver: MutationObserver | undefined, frame = 0;
    let cleaned = false, overlayView: PlanOverlayView | undefined;
    const cleanup = () => { if (cleaned) return; cleaned = true; abort.abort(); cancelAnimationFrame(frame); observer?.disconnect(); layoutObserver?.disconnect(); overlayView?.dispose(); controls?.dispose(); disposePreview(scene); assets?.dispose(); renderer?.dispose(); renderer?.domElement.remove(); ctx.current = undefined; };
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.domElement.setAttribute('aria-hidden', 'true'); host.append(renderer.domElement);
      // Ground covers what is actually authored (region, entities, fleets) and later the loaded city — never a fixed square.
      const authored = authoredPositions(ws.scenario);
      const bounds = new THREE.Box3();
      if (ws.scene) bounds.union(new THREE.Box3(new THREE.Vector3(-ws.scene.ground.width_m / 2, 0, -ws.scene.ground.depth_m / 2), new THREE.Vector3(ws.scene.ground.width_m / 2, 0, ws.scene.ground.depth_m / 2)));
      for (const [e, n, u] of authored) bounds.expandByPoint(new THREE.Vector3(e, u, -n));
      if (bounds.isEmpty()) bounds.set(new THREE.Vector3(-100, 0, -100), new THREE.Vector3(100, 0, 100));
      const size = bounds.getSize(new THREE.Vector3()), center = bounds.getCenter(new THREE.Vector3());
      const extent = Math.max(size.x, size.z, 200) * 1.25;
      const camera = new THREE.PerspectiveCamera(45, 1, 0.1, extent * 40);
      camera.position.set(center.x + extent * 0.55, extent * 0.6, center.z + extent * 0.7);
      controls = new OrbitControls(camera, renderer.domElement); controls.target.set(center.x, 0, center.z); controls.update();
      let firstFrame = true;
      const draw = () => {
        frame = 0; if (abort.signal.aborted) return;
        const now = performance.now();
        overlayView?.advanceCamera(now);
        controls!.update();
        const animating = overlayView?.render(now);
        renderer!.render(scene, camera);
        if (firstFrame) { setRendered(true); firstFrame = false; }
        if (animating) invalidate();
      };
      const invalidate = () => { if (!frame && !abort.signal.aborted) frame = requestAnimationFrame(draw); };
      controls.addEventListener('change', invalidate);
      const shownCity = cityScene(ws);
      let userMoved = false, framedCity = false;
      controls.addEventListener('start', () => { userMoved = true; overlayView?.interact(true); });
      const visibleFrame = (): PreviewFrame | undefined => {
        if (!bare) return;
        const shell = host.closest('[data-testid=workbench]');
        const full = host.getBoundingClientRect(), toolbar = shell?.querySelector('.wb-top')?.getBoundingClientRect();
        if (!shell) return;
        const rightPanels = [...shell.querySelectorAll('[data-testid=wb-config], [data-testid=wb-assistant]')].map(panel => panel.getBoundingClientRect());
        const leftPanels = [...shell.querySelectorAll('[data-testid=as-plan-board]')].map(panel => panel.getBoundingClientRect());
        return visiblePreviewFrame(full, rightPanels, leftPanels, toolbar);
      };
      const fitView = () => { overlayView?.cancelFrame(); const box = bounds.clone(); if (overlayView) box.union(overlayView.bounds()); framePreview(camera, box, controls!.target, visibleFrame()); controls!.update(); invalidate(); };
      scene.add(new THREE.HemisphereLight(0xffffff, 0x65705b, 2));
      const sun = new THREE.DirectionalLight(0xffffff, 2.5); sun.position.set(100, 200, 80); scene.add(sun);
      const groups = Object.fromEntries(LAYER_NAMES.map(name => { const group = new THREE.Group(); group.name = `layer-${name}`; scene.add(group); return [name, group]; })) as Record<LayerName, THREE.Group>;
      const fitGround = (box: THREE.Box3) => {
        clearGroup(groups.ground);
        const span = box.getSize(new THREE.Vector3()), mid = box.getCenter(new THREE.Vector3());
        const width = Math.max(span.x, 200) * 1.15, depth = Math.max(span.z, 200) * 1.15;
        const side = Math.max(width, depth), divisions = Math.min(80, Math.max(10, Math.round(side / 50)));
        const ground = new THREE.Mesh(new THREE.PlaneGeometry(width, depth), new THREE.MeshStandardMaterial({ color: '#dbe2d3', side: THREE.DoubleSide }));
        ground.rotation.x = -Math.PI / 2; ground.position.set(mid.x, -0.05, mid.z);
        const grid = new THREE.GridHelper(side, divisions, '#9da9ab', '#c6d0d1'); grid.position.set(mid.x, 0, mid.z);
        grid.scale.set(width / side, 1, depth / side);
        groups.ground.add(ground, grid);
      };
      fitGround(bounds);
      const acceptCity = (group: THREE.Group) => {
        if (abort.signal.aborted) return;
        if (group.parent !== groups.buildings) groups.buildings.add(group);
        const cityBox = new THREE.Box3().setFromObject(group);
        if (!cityBox.isEmpty()) {
          bounds.union(cityBox); fitGround(bounds);
          // Roads establish the extent first. Later building chunks never steal an orbit.
          if (!framedCity && !userMoved && !latest.current.overlay) { fitView(); framedCity = true; }
          camera.far = Math.max(camera.far, bounds.getSize(new THREE.Vector3()).length() * 6); camera.updateProjectionMatrix();
        }
        invalidate();
      };
      const context: Context = { scene, abort, groups, fitView, invalidate, setOverlay: value => {
        if (!value) { overlayView?.dispose(); overlayView = undefined; host.dataset.overlayLabels = '[]'; invalidate(); return; }
        try {
          overlayView ??= new PlanOverlayView({ scene, camera, target: controls!.target, host, invalidate, frameBounds: (box, destination, target) => {
            userMoved = true;
            framePreview(destination, box, target, visibleFrame());
          } });
          overlayView.update(value);
        } catch (problem) {
          overlayView?.dispose(); overlayView = undefined;
          setError({ kind: 'runtime', detail: String(problem) }); invalidate();
        }
      }, refreshBounds: scenario => {
        bounds.makeEmpty();
        if (ws.scene) bounds.union(new THREE.Box3(new THREE.Vector3(-ws.scene.ground.width_m / 2, 0, -ws.scene.ground.depth_m / 2), new THREE.Vector3(ws.scene.ground.width_m / 2, 0, ws.scene.ground.depth_m / 2)));
        for (const [e, n, u] of authoredPositions(scenario)) bounds.expandByPoint(new THREE.Vector3(e, u, -n));
        bounds.union(new THREE.Box3().setFromObject(groups.buildings));
        if (bounds.isEmpty()) bounds.set(new THREE.Vector3(-100, 0, -100), new THREE.Vector3(100, 0, 100));
        fitGround(bounds);
        if (!userMoved && !latest.current.overlay) fitView();
      }, buildingsRequested: false, loadBuildings: () => {
        if (context.buildingsRequested) return; context.buildingsRequested = true;
        if (shownCity?.city) {
          if (shownCity.city.kind !== 'traffic-city') { setError({ kind: 'descriptor' }); }
          else {
            assets = new Assets(renderer!); beginLoad();
            void loadTrafficCity(shownCity.city.url, assets, abort.signal, { onUpdate: (group, value) => { acceptCity(group); setProgress({ ...value }); } }).then(group => {
              if (abort.signal.aborted) { disposeObject(group); return; }
              acceptCity(group);
            })
              .catch(problem => { if (!abort.signal.aborted) setError({ kind: 'runtime', detail: String(problem) }); }).finally(finishLoad);
          }
        }
        if (ws.scene) {
          beginLoad();
          void loadOsmBuildings(`${api}/v1/studio/workspaces/${ws.id}/buildings.geojson`, ws.scene.origin, abort.signal).then(group => {
            if (abort.signal.aborted) disposeObject(group); else acceptCity(group);
          }).catch(e => { if (!abort.signal.aborted) setError({ kind: 'runtime', detail: String(e) }); }).finally(finishLoad);
        }
      } };
      ctx.current = context;
      const resize = () => {
        const w = Math.max(1, host.clientWidth), h = Math.max(1, host.clientHeight); renderer!.setSize(w, h, false); camera.aspect = w / h;
        if (!userMoved && !latest.current.overlay) fitView();
        else {
          if (latest.current.overlay && camera.view?.enabled) {
            const available = visibleFrame();
            if (available) camera.setViewOffset(w, h, w / 2 - available.x - available.width / 2, h / 2 - available.y - available.height / 2, w, h);
            else camera.clearViewOffset();
          }
          camera.updateProjectionMatrix();
        }
        invalidate();
      };
      observer = new ResizeObserver(resize); observer.observe(host); resize();
      // The configuration panel can appear after the city has already loaded. Its mount
      // does not resize the full-screen canvas, so observe the available viewing area too.
      const shell = bare && host.closest('[data-testid=workbench]');
      if (shell) {
        let previousFrame = JSON.stringify(visibleFrame());
        layoutObserver = new MutationObserver(records => {
          if (records.every(record => host.contains(record.target))) return;
          const nextFrame = JSON.stringify(visibleFrame());
          if (nextFrame === previousFrame) return;
          previousFrame = nextFrame;
          if (!userMoved && !latest.current.overlay) fitView();
        });
        layoutObserver.observe(shell, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style', 'hidden'] });
      }
      cancelAnimationFrame(frame); draw();
    } catch (e) { setError({ kind: 'runtime', detail: String(e) }); cleanup(); }
    return cleanup;
  }, [sceneKey]);

  // Visibility only: no disposal, no network, camera untouched. The first time buildings are shown they load once.
  useEffect(() => {
    const current = ctx.current; if (!current) return;
    LAYER_NAMES.forEach((name, index) => { current.groups[name].visible = layerKey[index] === '1'; });
    if (layerKey[1] === '1') current.loadBuildings();
    current.invalidate();
  }, [sceneKey, layerKey]);

  useEffect(() => {
    const current = ctx.current; if (!current) return;
    clearGroup(current.groups.roads);
    latest.current.workspace.scene?.roads?.forEach(road => {
      const geometry = new THREE.BufferGeometry().setFromPoints(road.points.map(([e, n, u]) => new THREE.Vector3(e, u + 0.08, -n)));
      current.groups.roads.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: '#6b737c' })));
    });
    current.invalidate();
  }, [sceneKey, roadsKey]);

  useEffect(() => {
    const current = ctx.current; if (!current) return;
    clearGroup(current.groups.entities); clearGroup(current.groups.airspace);
    const scenario = latest.current.workspace.scenario;
    current.refreshBounds(scenario);
    (scenario.entities ?? []).forEach((entity: any) => {
      // Airspace has its own polygon; it does not require an entity position binding.
      const airspace = entity.facts?.['aas.studio.airspace'];
      if (airspace && Array.isArray(airspace.polygon) && airspace.polygon.length >= 3 && airspace.polygon.every((p: unknown) => Array.isArray(p) && p.length >= 2 && p.slice(0, 2).every(Number.isFinite))) {
        for (const altitude of [airspace.floor_m, airspace.ceiling_m]) {
          if (!Number.isFinite(altitude)) continue;
          const geo = new THREE.BufferGeometry().setFromPoints(airspace.polygon.map(([e, n]: number[]) => new THREE.Vector3(e, altitude, -n)));
          current.groups.airspace.add(new THREE.LineLoop(geo, new THREE.LineBasicMaterial({ color: '#e99c36' })));
        }
      }
      const binding = draftBinding(scenario, entity.type);
      const pos = binding && entity.facts?.[binding.positionField];
      if (!Array.isArray(pos) || pos.length !== 3 || !pos.every(Number.isFinite)) return;
      const marker = new THREE.Sprite(new THREE.SpriteMaterial({ color: binding.visual?.color ?? '#1677ff', sizeAttenuation: false, depthTest: false }));
      marker.name = entity.id; marker.scale.set(0.01, 0.01, 1);
      marker.position.set(pos[0], pos[2], -pos[1]); current.groups.entities.add(marker);
    });
    // Generated fleets start at authored points; one marker per start, labelled by count in the panel.
    for (const generator of Object.values(scenario.generators ?? {}) as any[]) for (const start of generator?.starts ?? []) {
      if (!Array.isArray(start) || start.length !== 3 || !start.every(Number.isFinite)) continue;
      const marker = new THREE.Sprite(new THREE.SpriteMaterial({ color: draftBinding(scenario, generator.type)?.visual?.color ?? '#50c9ff', sizeAttenuation: false, depthTest: false }));
      marker.scale.set(0.01, 0.01, 1); marker.position.set(start[0], start[2], -start[1]); current.groups.entities.add(marker);
    }
    if (root.current) root.current.dataset.markerPositions = JSON.stringify(current.groups.entities.children.map(marker => ({ id: marker.name, position: marker.position.toArray() })));
    current.invalidate();
  }, [sceneKey, entitiesKey]);

  useEffect(() => { ctx.current?.setOverlay(latest.current.overlay); }, [sceneKey, overlayKey]);

  const status = loading && <div className="studio-preview-status" role="status">{t('Loading city geometry…')}{progress && progress.total > 0 && <span> {zh ? '建筑' : 'Buildings'} {progress.loaded} / {progress.total}</span>}</div>;
  if (bare) return <div className="scene-preview-canvas" ref={root} aria-label={t('Scene preview')} aria-busy={loading} data-rendered={rendered} data-testid="draft-preview" data-overlay-state={overlay?.tone ?? 'none'} data-overlay-labels="[]">
    {status}
    <div className="studio-preview-fit"><button className="wb-btn" onClick={() => ctx.current?.fitView()}>{zh ? '查看完整场景' : 'Fit scene'}</button><span>{overlay ? overlay.tone === 'proposed' ? (zh ? '提议方案 · 尚未应用' : 'Proposed plan · not applied') : (zh ? '场景已配置 · 未运行' : 'Configured scene · not running') : (zh ? '标记显示配置位置与机队起点' : 'Markers show configured positions and fleet starts')}</span></div>
    {error && <div className="studio-preview-error" role="alert">{error.kind === 'descriptor' ? t('Studio city preview requires a traffic-city descriptor') : t('Scene preview could not load: {error}', { error: error.detail })}</div>}
  </div>;
  return <>
    <p id={hintId}>{t('Draft scene preview, not a live run. Drag to orbit, scroll to zoom, and use the layer controls to show or hide geometry. Entities need a position binding; airspace uses its own polygon.')}</p>
    <div className="studio-preview" ref={root} role="img" aria-label={t('Scene preview')} aria-describedby={hintId} aria-busy={loading} data-overlay-state={overlay?.tone ?? 'none'} data-overlay-labels="[]">
      {status}
      {error && <div className="studio-preview-error" role="alert">{error.kind === 'descriptor' ? t('Studio city preview requires a traffic-city descriptor') : t('Scene preview could not load: {error}', { error: error.detail })}</div>}
    </div>
  </>;
}
