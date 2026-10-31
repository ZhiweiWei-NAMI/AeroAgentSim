import type { RunHeader } from '../contracts/viewer-feed';

/** Optional display metadata; never an engine state or inferred entity type. */
export interface ScenePresentation {
  id?: string;
  hint?: string;
  city?: { kind: 'osm2world' | 'glb' | 'geojson' | 'traffic-city'; url: string; assetsBase?: string; offset?: [number, number, number] };
  roads?: { url: string; offset?: [number, number, number] };
  trees?: Array<[number, number, number]>;
  hdri?: string;
  /** `fixed`: keep this authored load pose instead of auto-framing the main actors. It does not lock the controls. */
  camera?: { target: [number, number, number]; position: [number, number, number]; fixed?: boolean };
  /** Display-plane height in renderer metres (default -0.1). Set it below a terrain GLB's lowest point so the plane cannot cut through it. */
  groundY?: number;
  /** Optional Gaussian-splat (.spz) display overlays drawn with Spark; the transform maps the file's own frame into renderer metres. */
  splats?: Array<{ url: string; position?: [number, number, number]; quaternion?: [number, number, number, number]; scale?: number }>;
  attribution?: string;
}
declare module '../contracts/viewer-feed' { interface RunHeader { scene?: ScenePresentation } }

/** An explicit UI choice can override display metadata without mutating the feed. */
export function scenePresentation(header: RunHeader): ScenePresentation | undefined {
  const scene = new URLSearchParams(location.search).get('scene');
  if (scene === 'huangpu') return header.scene ?? {
    id: 'Shanghai · Huangpu', city: { kind: 'traffic-city', url: '/v1/studio/demo-assets/scene.json' },
    camera: { position: [280, 220, 300], target: [91.95, 0, 93.04] },
    attribution: '© OpenStreetMap contributors · ODbL 1.0',
  };
  return header.scene;
}
