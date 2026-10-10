import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { isCompleteGroundCoverDrawnSet, verifiedGroundCoverInventory } from './capture-city-canonical-ground.mjs';
const fixture = () => ({ status: 'pass', geometry_id: 'geometry', omission_count: 0,
  parser_accepted_ids: ['a', 'b'], drawable_ids: ['a', 'b'], drawn_ids: ['a', 'b'],
  omitted_ids: [], unexpected_ids: [] });
test('accepts only a complete canonical drawn set', () => {
  assert.equal(isCompleteGroundCoverDrawnSet(fixture(), 'geometry', ['a', 'b']), true);
});
test('rejects the wrong geometry identity', () => {
  assert.equal(isCompleteGroundCoverDrawnSet(fixture(), 'other', ['a', 'b']), false);
});
test('rejects missing counters and ID lists', () => {
  assert.equal(isCompleteGroundCoverDrawnSet(null, 'geometry', ['a', 'b']), false);
  const drawn = fixture(); delete drawn.drawable_ids;
  assert.equal(isCompleteGroundCoverDrawnSet(drawn, 'geometry', ['a', 'b']), false);
});
test('rejects omissions even when a status string says pass', () => {
  const drawn = fixture(); drawn.omitted_ids = ['a'];
  assert.equal(isCompleteGroundCoverDrawnSet(drawn, 'geometry', ['a', 'b']), false);
});
test('rejects duplicated or substituted cover IDs', () => {
  for (const ids of [['a', 'a'], ['a', 'c']]) {
    const drawn = fixture(); drawn.drawn_ids = ids;
    assert.equal(isCompleteGroundCoverDrawnSet(drawn, 'geometry', ['a', 'b']), false);
  }
});
test('rejects a wholly substituted parser/drawable/drawn inventory of the same size', () => {
  const drawn = fixture();
  for (const field of ['parser_accepted_ids', 'drawable_ids', 'drawn_ids']) drawn[field] = ['c', 'd'];
  assert.equal(isCompleteGroundCoverDrawnSet(drawn, 'geometry', ['a', 'b']), false);
});
test('checks each published region against its verified source identities', async () => {
  for (const [name, count] of [['default-scene-v1.json', 19], ['jingan-engineering-preview-v2.json', 3],
    ['jingan-engineering-preview-v3.json', 3]]) {
    const publicRoot = new URL('../public/', import.meta.url);
    const scene = JSON.parse(await readFile(new URL(`city-presentation/${name}`, publicRoot), 'utf8'));
    const bytes = await readFile(new URL(scene.environment_source.url.slice(1), publicRoot));
    const inventory = verifiedGroundCoverInventory(bytes, scene.environment_source, scene);
    assert.equal(inventory.count, count);
    const drawn = fixture();
    for (const field of ['parser_accepted_ids', 'drawable_ids', 'drawn_ids']) drawn[field] = inventory.ids;
    assert.equal(isCompleteGroundCoverDrawnSet(drawn, 'geometry', inventory.ids), true);
    assert.throws(() => verifiedGroundCoverInventory(Buffer.concat([bytes, Buffer.from(' ')]), scene.environment_source, scene), /byte identity/);
  }
});
test('rejects invalid source identities, counts, authority and a reduced Huangpu inventory', () => {
  const scene = { road_assets: { mesh_pack_source_sha256: 'osm', source_scene_id: 'test' } };
  const source = { schemaVersion: 'aero-bench.city-environment-source/v1', source: { osmSha256: 'osm' },
    inspection: { groundCoverCount: 2 }, groundCovers: [{ id: 'a' }, { id: 'b' }] };
  function verify(value, selected = scene) {
    const bytes = Buffer.from(JSON.stringify(value));
    return verifiedGroundCoverInventory(bytes, { sha256: createHash('sha256').update(bytes).digest('hex'), size_bytes: bytes.length }, selected);
  }
  assert.throws(() => verify({ ...source, groundCovers: [{ id: 'a' }, { id: 'a' }] }), /invalid identities/);
  assert.throws(() => verify({ ...source, inspection: { groundCoverCount: 3 } }), /invalid identities/);
  assert.throws(() => verify({ ...source, source: { osmSha256: 'other' } }), /authority/);
  assert.throws(() => verify(source, { road_assets: { ...scene.road_assets, source_scene_id: 'shanghai-huangpu-east-v1' } }), /exactly its 19/);
});
