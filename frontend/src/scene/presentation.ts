import type { RunHeader } from '../contracts/viewer-feed';

/** Optional display metadata; never an engine state or inferred entity type. */
export interface ScenePresentation {
  id?: string;
  city?: { kind: 'osm2world' | 'glb' | 'geojson'; url: string; assetsBase?: string; offset?: [number, number, number] };
  roads?: { url: string; offset?: [number, number, number] };
  trees?: Array<[number, number, number]>;
  hdri?: string;
  camera?: { target: [number, number, number]; position: [number, number, number] };
  attribution?: string;
}
declare module '../contracts/viewer-feed' { interface RunHeader { scene?: ScenePresentation } }

/** An explicit UI choice can override display metadata without mutating the feed. */
export function scenePresentation(header: RunHeader): ScenePresentation | undefined {
  const scene = new URLSearchParams(location.search).get('scene');
  if (scene === 'huangpu') return {
    id: 'Shanghai · Huangpu east', city: { kind: 'osm2world', url: '/assets/city/manifest.json', assetsBase: new URL('/assets/city/', location.href).href },
    hdri: '/assets/environment/day.hdr',
    camera: { position: [280, 220, 300], target: [30, 25, -25] },
    attribution: '© OpenStreetMap contributors · ODbL · OSM2World textures CC0',
  };
  return header.scene;
}
