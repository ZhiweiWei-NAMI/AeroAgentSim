import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { loadOsmBuildings } from '../scene/source-geometry';
import { disposeObject } from '../viewport/assets';
import type { Workspace } from './api';

export function ScenePreview({ workspace, api, layers }: { workspace: Workspace; api: string; layers: string[] }) {
  const root = useRef<HTMLDivElement>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!root.current) return;
    const host = root.current, abort = new AbortController();
    let renderer: THREE.WebGLRenderer | undefined, controls: OrbitControls | undefined, frame = 0;
    const scene = new THREE.Scene(); scene.background = new THREE.Color('#edf2f6'); setError('');
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); host.append(renderer.domElement);
      const width = workspace.scene?.ground.width_m ?? 200, depth = workspace.scene?.ground.depth_m ?? 200;
      const extent = Math.max(width, depth, 100);
      const camera = new THREE.PerspectiveCamera(45, 1, 0.1, extent * 20);
      camera.position.set(extent * 0.65, extent * 0.8, extent * 0.65);
      controls = new OrbitControls(camera, renderer.domElement); controls.target.set(0, 0, 0); controls.update();
      scene.add(new THREE.HemisphereLight(0xffffff, 0x65705b, 2));
      const sun = new THREE.DirectionalLight(0xffffff, 2.5); sun.position.set(100, 200, 80); scene.add(sun);
      if (layers.includes('ground')) {
        const ground = new THREE.Mesh(new THREE.PlaneGeometry(width, depth), new THREE.MeshStandardMaterial({ color: '#dbe2d3', side: THREE.DoubleSide }));
        ground.rotation.x = -Math.PI / 2; ground.position.y = -0.05; scene.add(ground);
        scene.add(new THREE.GridHelper(extent, 20, '#9da9ab', '#c6d0d1'));
      }
      if (workspace.scene && layers.includes('buildings')) {
        void loadOsmBuildings(`${api}/v1/studio/workspaces/${workspace.id}/buildings.geojson`, workspace.scene.origin, abort.signal).then(group => {
          if (abort.signal.aborted) disposeObject(group); else scene.add(group);
        }).catch(e => { if (!abort.signal.aborted) setError(String(e)); });
      }
      if (layers.includes('roads')) workspace.scene?.roads.forEach(road => {
        const geometry = new THREE.BufferGeometry().setFromPoints(road.points.map(([e, n, u]) => new THREE.Vector3(e, u + 0.08, -n)));
        scene.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: '#6b737c' })));
      });
      workspace.scenario.entities.forEach((entity: any) => {
        const binding = workspace.scenario.presentation?.find((p: any) => p.typeId === entity.type);
        const pos = binding && entity.facts[binding.positionField];
        if (!Array.isArray(pos) || pos.length !== 3) return; // Non-spatial entities have no geometry.
        const facility = entity.type === 'aas:StudioFacility';
        const marker = new THREE.Mesh(facility ? new THREE.BoxGeometry(8, 4, 8) : new THREE.OctahedronGeometry(2), new THREE.MeshStandardMaterial({ color: facility ? '#45a565' : '#1677ff' }));
        marker.position.set(pos[0], pos[2] + (facility ? 2 : 0), -pos[1]); if (layers.includes('entities')) scene.add(marker); else disposeObject(marker);
        const airspace = entity.facts['aas.studio.airspace'];
        if (airspace && layers.includes('airspace')) {
          for (const altitude of [airspace.floor_m, airspace.ceiling_m]) {
            const geo = new THREE.BufferGeometry().setFromPoints(airspace.polygon.map(([e, n]: number[]) => new THREE.Vector3(e, altitude, -n)));
            scene.add(new THREE.LineLoop(geo, new THREE.LineBasicMaterial({ color: '#e99c36' })));
          }
        }
      });
      const resize = () => { const w = host.clientWidth, h = host.clientHeight; renderer!.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); };
      const observer = new ResizeObserver(resize); observer.observe(host); resize();
      const animate = () => { if (abort.signal.aborted) return; controls!.update(); renderer!.render(scene, camera); frame = requestAnimationFrame(animate); }; animate();
      return () => { abort.abort(); cancelAnimationFrame(frame); observer.disconnect(); controls?.dispose(); disposeObject(scene); renderer?.dispose(); renderer?.domElement.remove(); };
    } catch (e) { setError(String(e)); }
    return () => { abort.abort(); cancelAnimationFrame(frame); controls?.dispose(); disposeObject(scene); renderer?.dispose(); renderer?.domElement.remove(); };
  }, [workspace, api, layers]);
  return <div className="studio-preview" ref={root} aria-label="Scene preview">{error && <div className="studio-preview-error" role="alert">{error}</div>}</div>;
}
