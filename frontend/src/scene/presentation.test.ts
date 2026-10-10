import { afterEach, expect, it, vi } from 'vitest';
import type { RunHeader } from '../contracts/viewer-feed';
import { scenePresentation } from './presentation';

afterEach(() => { vi.unstubAllGlobals(); });
it('uses the shared city endpoint and retains the backend lite download hint', () => {
  vi.stubGlobal('location', { search: '?scene=huangpu' });
  const scene = {
    city: { kind: 'traffic-city' as const, url: '/v1/studio/demo-assets/scene.json' },
    hint: 'Detailed 3D city available: run `aeroagentsim assets fetch`',
  };
  expect(scenePresentation({ scene } as RunHeader)).toBe(scene);
  expect(scenePresentation({} as RunHeader)?.city?.url).toBe('/v1/studio/demo-assets/scene.json');
});
