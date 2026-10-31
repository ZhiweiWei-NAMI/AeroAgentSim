import { describe, expect, it } from 'vitest';
import { rescueConsoleScene, resolveSceneAssetUrl } from './ScenePreview';
import type { Workspace } from './api';

function workspace(consoleScene: unknown): Workspace {
  return { id: 'w', name: 'w', scenario: { engines: { rescue: { config: { console_scene: consoleScene } } } } } as Workspace;
}

const scene = {
  id: 'SC07 rescue',
  city: { kind: 'glb', url: '/data/scenes/SC07.glb', offset: [0, 0, 0] },
  camera: { position: [-276, 52, -127], target: [-221, 4, -192], fixed: true },
  groundY: 0.55,
  splats: [{ url: '/data/splats/SC07.spz', scale: 51.42, quaternion: [0.999988, -0.004833, '5e-06', -0.001079] }],
};

describe('rescueConsoleScene', () => {
  it('selects the declared rescue console scene before the Huangpu demo', () => {
    const ws = workspace(scene);
    ws.scenario.city = 'huangpu';
    ws.demo_console = { capture_mode: 'none', city_available: true, scene: { id: 'Huangpu' } as never };
    expect(rescueConsoleScene(ws)).toMatchObject({ id: 'SC07 rescue', groundY: 0.55 });
  });

  it('returns undefined when no rescue scene is declared so Huangpu stays the fallback', () => {
    expect(rescueConsoleScene({ id: 'w', name: 'w', scenario: { city: 'huangpu' } } as Workspace)).toBeUndefined();
  });

  it('marks a malformed declared descriptor instead of silently showing Huangpu', () => {
    expect(rescueConsoleScene(workspace({ id: 'broken' }))).toEqual({ id: '' });
    expect(rescueConsoleScene(workspace({ id: '', city: { url: '/x' } }))).toEqual({ id: '' });
  });
});

describe('resolveSceneAssetUrl', () => {
  it('resolves root-relative service assets against the API origin', () => {
    expect(resolveSceneAssetUrl('/data/scenes/SC07.glb', 'http://127.0.0.1:18931'))
      .toBe('http://127.0.0.1:18931/data/scenes/SC07.glb');
  });

  it('resolves relative API bases through location and keeps absolute URLs', () => {
    expect(resolveSceneAssetUrl('/data/x.glb', 'https://example.test/base')).toBe('https://example.test/data/x.glb');
    expect(resolveSceneAssetUrl('https://cdn.test/x.spz', 'http://127.0.0.1:18931')).toBe('https://cdn.test/x.spz');
    expect(resolveSceneAssetUrl('blob:abc', 'http://127.0.0.1:18931')).toBe('blob:abc');
  });
});
