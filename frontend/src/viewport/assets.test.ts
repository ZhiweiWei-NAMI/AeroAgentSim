import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadCityPack, disposeObject } from './assets';

const CHUNK_ID = 'city-pack/chunk-0001@3';
const LEGACY_HASH = 'a'.repeat(64);

function realChunk(): ArrayBuffer {
  const bytes = new ArrayBuffer(108);
  new Float32Array(bytes, 0, 9).set([0, 0, 0, 1, 0, 0, 0, 0, 1]);
  new Float32Array(bytes, 36, 9).set([0, 1, 0, 0, 1, 0, 0, 1, 0]);
  new Uint32Array(bytes, 96, 3).set([0, 1, 2]);
  return bytes;
}

function manifest() {
  return {
    schema_version: 'aero-bench.osm2world-mesh-pack/v1', source: { asset_id: 'city-pack/source@1', size_bytes: 1 },
    generator: { revision: 'a'.repeat(40) },
    projection: { name: 'MetricMapProjection', axes: 'east-up-south', origin: { latitude_deg: 31, longitude_deg: 121 } },
    coordinate_contract: { schema_version: 'aero-bench.osm2world-source-coordinates/v1', recipe: 'source-node-bounds-local-Mercator-then-declared-origin-translation',
      converter_origin: { latitude_deg: 31, longitude_deg: 121 }, stored_translation_xz_m: [0, 0], earth_circumference_m: 40075016.686, native_point_quantization_m: 0.001,
      storage: 'source-mesh-float32-converter-coordinates-then-declared-origin-translation' },
    extent: { west: 0, east: 1, south: 0, north: 1 }, original_mesh_count: 1, objects: [], textures: {},
    batches: [{ file: { asset_id: CHUNK_ID, size_bytes: 108 }, vertices: 3, indices: 3, layer: 'buildings', ranges: [{ end: 1, target: null }],
      material: { color: [0.2, 0.3, 0.4], base_color_texture: null, normal_texture: null, orm_texture: null, opacity_texture: null, transparent: false, clamp: false } }],
  };
}

afterEach(() => { vi.unstubAllGlobals(); });

describe('plain-id city pack', () => {
  it('loads chunks by opaque asset id and aligns the declared origin once', async () => {
    const bytes = realChunk();
    const fetch = vi.fn(async (url: URL | string) => String(url).includes('/assets/')
      ? { ok: true, arrayBuffer: async () => bytes } : { ok: true, json: async () => manifest() });
    vi.stubGlobal('fetch', fetch);
    const city = await loadCityPack('https://city.example/manifest.json', 'https://city.example/', { lat: 31, lon: 121, alt: 22 }, new AbortController().signal);
    expect(fetch.mock.calls.map(call => String(call[0]))).toContain(`https://city.example/assets/${CHUNK_ID}`);
    expect(city.position.length()).toBe(0);
    expect(city.children).toHaveLength(1);
    expect(city.children[0]).toMatchObject({ castShadow: true, receiveShadow: true });
    disposeObject(city);
  });
  it('ignores obsolete legacy hash annotations on old packs while keeping the geometry', async () => {
    const bytes = realChunk();
    const old = { ...manifest(),
      generator: { revision: 'a'.repeat(40), runtime_sha256: LEGACY_HASH, patch_sha256: LEGACY_HASH, config_sha256: LEGACY_HASH },
      coordinate_contract: { ...manifest().coordinate_contract, source_json_sha256: LEGACY_HASH, producer_sha256: LEGACY_HASH, projection_helper_sha256: LEGACY_HASH },
      source: { sha256: LEGACY_HASH, size_bytes: 1 } };
    vi.stubGlobal('fetch', async (url: URL | string) => String(url).includes('/assets/')
      ? { ok: true, arrayBuffer: async () => bytes } : { ok: true, json: async () => old });
    const city = await loadCityPack('https://city.example/manifest.json', 'https://city.example/', { lat: 31, lon: 121, alt: 22 }, new AbortController().signal);
    expect(city.children).toHaveLength(1);
    disposeObject(city);
  });
  it('surfaces a wrong chunk size instead of claiming a loaded city', async () => {
    const wrong = manifest(); (wrong.batches as { file: { size_bytes: number } }[])[0].file.size_bytes = 104;
    vi.stubGlobal('fetch', async (url: URL | string) => String(url).includes('/assets/')
      ? { ok: true, arrayBuffer: async () => realChunk() } : { ok: true, json: async () => wrong });
    await expect(loadCityPack('https://city.example/manifest.json', 'https://city.example/', { lat: 31, lon: 121, alt: 0 }, new AbortController().signal)).rejects.toThrow(/declared counts/);
  });
});
