import { afterEach, describe, expect, it, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import { loadCityPack, disposeObject } from './assets';

async function fixture() {
  const bytes = new ArrayBuffer(108);
  new Float32Array(bytes, 0, 9).set([0, 0, 0, 1, 0, 0, 0, 0, 1]);
  new Float32Array(bytes, 36, 9).set([0, 1, 0, 0, 1, 0, 0, 1, 0]);
  new Uint32Array(bytes, 96, 3).set([0, 1, 2]);
  const hash = [...new Uint8Array(await webcrypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
  const provenance = 'a'.repeat(64);
  return { bytes, hash, manifest: {
    schema_version: 'aero-bench.osm2world-mesh-pack/v1', source: { sha256: provenance, size_bytes: 1 },
    generator: { runtime_sha256: provenance, patch_sha256: provenance, config_sha256: provenance, revision: 'a'.repeat(40) },
    projection: { name: 'MetricMapProjection', axes: 'east-up-south', origin: { latitude_deg: 31, longitude_deg: 121 } },
    coordinate_contract: { schema_version: 'aero-bench.osm2world-source-coordinates/v1', recipe: 'source-node-bounds-local-Mercator-then-declared-origin-translation',
      converter_origin: { latitude_deg: 31, longitude_deg: 121 }, stored_translation_xz_m: [0, 0], earth_circumference_m: 40075016.686, native_point_quantization_m: 0.001,
      storage: 'source-mesh-float32-converter-coordinates-then-declared-origin-translation', source_json_sha256: provenance, producer_sha256: provenance, projection_helper_sha256: provenance },
    extent: { west: 0, east: 1, south: 0, north: 1 }, original_mesh_count: 1, objects: [], textures: {},
    batches: [{ file: { sha256: hash, size_bytes: 108 }, vertices: 3, indices: 3, layer: 'buildings', ranges: [{ end: 1, target: null }],
      material: { color: [0.2, 0.3, 0.4], base_color_texture: null, normal_texture: null, orm_texture: null, opacity_texture: null, transparent: false, clamp: false } }],
  } };
}
afterEach(() => { vi.unstubAllGlobals(); });

describe('content-addressed city pack', () => {
  it('loads the real binary layout, verifies bytes, and aligns the declared origin once', async () => {
    const { bytes, hash, manifest } = await fixture();
    vi.stubGlobal('crypto', webcrypto);
    const fetch = vi.fn(async (url: URL | string) => String(url).includes('/assets/')
      ? { ok: true, arrayBuffer: async () => bytes } : { ok: true, json: async () => manifest });
    vi.stubGlobal('fetch', fetch);
    const city = await loadCityPack('https://city.example/manifest.json', 'https://city.example/', { lat: 31, lon: 121, alt: 22 }, new AbortController().signal);
    expect(fetch.mock.calls.map(call => String(call[0]))).toContain(`https://city.example/assets/${hash}`);
    expect(city.position.length()).toBe(0);
    expect(city.children).toHaveLength(1);
    expect(city.children[0]).toMatchObject({ castShadow: true, receiveShadow: true });
    disposeObject(city);
  });
  it('surfaces corrupt referenced bytes instead of claiming a loaded city', async () => {
    const { manifest } = await fixture(); vi.stubGlobal('crypto', webcrypto);
    vi.stubGlobal('fetch', async (url: URL | string) => String(url).includes('/assets/')
      ? { ok: true, arrayBuffer: async () => new ArrayBuffer(108) } : { ok: true, json: async () => manifest });
    await expect(loadCityPack('https://city.example/manifest.json', 'https://city.example/', { lat: 31, lon: 121, alt: 0 }, new AbortController().signal)).rejects.toThrow(/SHA-256/);
  });
});
