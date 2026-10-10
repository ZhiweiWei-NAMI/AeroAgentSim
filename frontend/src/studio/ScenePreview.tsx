import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { loadOsmBuildings } from '../scene/source-geometry';
import { Assets, disposeObject } from '../viewport/assets';
import { loadTrafficCity } from '../scene/traffic-city';
import type { Workspace } from './api';

type LayerName = 'ground' | 'buildings' | 'roads' | 'entities' | 'airspace';
const LAYER_NAMES: LayerName[] = ['ground', 'buildings', 'roads', 'entities', 'airspace'];

interface Context {
  scene: THREE.Scene; abort: AbortController; groups: Record<LayerName, THREE.Group>;
  buildingsRequested: boolean; loadBuildings: () => void;
}

/** Remove and dispose every child of a layer group while keeping the group itself in the scene. */
function clearGroup(group: THREE.Group) { for (const child of [...group.children]) disposeObject(child); }

/**
 * The renderer, camera and downloaded city live for one scene identity. Layer checkboxes only
 * flip `group.visible`; cheap groups (roads, entities, airspace) rebuild when their content changes.
 */
export function ScenePreview({ workspace, api, layers }: { workspace: Workspace; api: string; layers: string[] }) {
  const { t } = useI18n();
  const hintId = useId();
  const root = useRef<HTMLDivElement>(null);
  const ctx = useRef<Context>();
  const latest = useRef({ workspace, layers }); latest.current = { workspace, layers };
  const [error, setError] = useState<{ kind: 'descriptor' | 'runtime'; detail?: string }>(), [loading, setLoading] = useState(false);
  const city = workspace.demo_console?.scene;
  const sceneKey = JSON.stringify([workspace.id, api, city?.city?.kind ?? null, city?.city?.url ?? null, city?.camera ?? null,
    workspace.scene?.ground ?? null, workspace.scene?.origin ?? null]);
  const roadsKey = JSON.stringify(workspace.scene?.roads ?? null);
  const entitiesKey = JSON.stringify([workspace.scenario.entities, workspace.scenario.presentation ?? null]);
  const layerKey = useMemo(() => LAYER_NAMES.map(name => layers.includes(name) ? 1 : 0).join(''), [layers]);

  useEffect(() => {
    if (!root.current) return;
    const host = root.current, abort = new AbortController(), { workspace: ws } = latest.current;
    const scene = new THREE.Scene(); scene.background = new THREE.Color('#edf2f6'); setError(undefined); setLoading(false);
    let pendingBuildings = 0;
    const beginLoad = () => { pendingBuildings++; setLoading(true); };
    const finishLoad = () => { pendingBuildings--; if (!abort.signal.aborted) setLoading(pendingBuildings > 0); };
    let assets: Assets | undefined, renderer: THREE.WebGLRenderer | undefined, controls: OrbitControls | undefined, observer: ResizeObserver | undefined, frame = 0;
    let cleaned = false;
    const cleanup = () => { if (cleaned) return; cleaned = true; abort.abort(); cancelAnimationFrame(frame); observer?.disconnect(); controls?.dispose(); disposeObject(scene); assets?.dispose(); renderer?.dispose(); renderer?.domElement.remove(); ctx.current = undefined; };
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.domElement.setAttribute('aria-hidden', 'true'); host.append(renderer.domElement);
      const width = ws.scene?.ground.width_m ?? 200, depth = ws.scene?.ground.depth_m ?? 200;
      const extent = Math.max(width, depth, 100);
      const camera = new THREE.PerspectiveCamera(45, 1, 0.1, extent * 20);
      camera.position.set(extent * 0.65, extent * 0.8, extent * 0.65);
      controls = new OrbitControls(camera, renderer.domElement); controls.target.set(0, 0, 0); controls.update();
      const cityScene = ws.demo_console?.scene;
      if (cityScene?.camera) { camera.position.set(...cityScene.camera.position); controls.target.set(...cityScene.camera.target); controls.update(); }
      scene.add(new THREE.HemisphereLight(0xffffff, 0x65705b, 2));
      const sun = new THREE.DirectionalLight(0xffffff, 2.5); sun.position.set(100, 200, 80); scene.add(sun);
      const groups = Object.fromEntries(LAYER_NAMES.map(name => { const group = new THREE.Group(); group.name = `layer-${name}`; scene.add(group); return [name, group]; })) as Record<LayerName, THREE.Group>;
      const ground = new THREE.Mesh(new THREE.PlaneGeometry(width, depth), new THREE.MeshStandardMaterial({ color: '#dbe2d3', side: THREE.DoubleSide }));
      ground.rotation.x = -Math.PI / 2; ground.position.y = -0.05; groups.ground.add(ground, new THREE.GridHelper(extent, 20, '#9da9ab', '#c6d0d1'));
      const context: Context = { scene, abort, groups, buildingsRequested: false, loadBuildings: () => {
        if (context.buildingsRequested) return; context.buildingsRequested = true;
        if (cityScene?.city) {
          if (cityScene.city.kind !== 'traffic-city') { setError({ kind: 'descriptor' }); }
          else {
            assets = new Assets(renderer!); beginLoad();
            void loadTrafficCity(cityScene.city.url, assets, abort.signal).then(group => { if (abort.signal.aborted) disposeObject(group); else groups.buildings.add(group); })
              .catch(problem => { if (!abort.signal.aborted) setError({ kind: 'runtime', detail: String(problem) }); }).finally(finishLoad);
          }
        }
        if (ws.scene) {
          beginLoad();
          void loadOsmBuildings(`${api}/v1/studio/workspaces/${ws.id}/buildings.geojson`, ws.scene.origin, abort.signal).then(group => {
            if (abort.signal.aborted) disposeObject(group); else groups.buildings.add(group);
          }).catch(e => { if (!abort.signal.aborted) setError({ kind: 'runtime', detail: String(e) }); }).finally(finishLoad);
        }
      } };
      ctx.current = context;
      const resize = () => { const w = Math.max(1, host.clientWidth), h = Math.max(1, host.clientHeight); renderer!.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); };
      observer = new ResizeObserver(resize); observer.observe(host); resize();
      const animate = () => { if (abort.signal.aborted) return; controls!.update(); renderer!.render(scene, camera); frame = requestAnimationFrame(animate); }; animate();
    } catch (e) { setError({ kind: 'runtime', detail: String(e) }); cleanup(); }
    return cleanup;
  }, [sceneKey]);

  // Visibility only: no disposal, no network, camera untouched. The first time buildings are shown they load once.
  useEffect(() => {
    const current = ctx.current; if (!current) return;
    LAYER_NAMES.forEach((name, index) => { current.groups[name].visible = layerKey[index] === '1'; });
    if (layerKey[1] === '1') current.loadBuildings();
  }, [sceneKey, layerKey]);

  useEffect(() => {
    const current = ctx.current; if (!current) return;
    clearGroup(current.groups.roads);
    latest.current.workspace.scene?.roads?.forEach(road => {
      const geometry = new THREE.BufferGeometry().setFromPoints(road.points.map(([e, n, u]) => new THREE.Vector3(e, u + 0.08, -n)));
      current.groups.roads.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: '#6b737c' })));
    });
  }, [sceneKey, roadsKey]);

  useEffect(() => {
    const current = ctx.current; if (!current) return;
    clearGroup(current.groups.entities); clearGroup(current.groups.airspace);
    const scenario = latest.current.workspace.scenario;
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
      const binding = Array.isArray(scenario.presentation) && scenario.presentation.find((p: any) => p.typeId === entity.type);
      const pos = binding && entity.facts?.[binding.positionField];
      if (!Array.isArray(pos) || pos.length !== 3 || !pos.every(Number.isFinite)) return;
      const facility = entity.type === 'aas:StudioFacility';
      const marker = new THREE.Mesh(facility ? new THREE.BoxGeometry(8, 4, 8) : new THREE.OctahedronGeometry(2), new THREE.MeshStandardMaterial({ color: facility ? '#45a565' : '#1677ff' }));
      marker.position.set(pos[0], pos[2] + (facility ? 2 : 0), -pos[1]); current.groups.entities.add(marker);
    });
  }, [sceneKey, entitiesKey]);

  return <>
    <p id={hintId}>{t('Draft scene preview, not a live run. Drag to orbit, scroll to zoom, and use the layer controls to show or hide geometry. Entities need a position binding; airspace uses its own polygon.')}</p>
    <div className="studio-preview" ref={root} role="img" aria-label={t('Scene preview')} aria-describedby={hintId} aria-busy={loading}>
      {loading && <div className="studio-preview-status" role="status">{t('Loading city geometry…')}</div>}
      {error && <div className="studio-preview-error" role="alert">{error.kind === 'descriptor' ? t('Studio city preview requires a traffic-city descriptor') : t('Scene preview could not load: {error}', { error: error.detail })}</div>}
    </div>
  </>;
}
