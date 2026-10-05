import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_CONFIG } from '../src/config.js';
import { createFixtureRun, exportObservations, selectionKey, sha256 } from '../src/runtime.js';
import { acceptsViewResult, seekObservation } from '../src/observation-contract.js';

test('export keeps exact hashes, all entity kinds, null motion and absent extents', async () => {
  const run = await createFixtureRun(DEFAULT_CONFIG);
  const bundle = exportObservations(run);
  for (const entry of bundle.manifest.index) assert.equal(await sha256(bundle.artifacts[entry.artifact_id]), entry.sha256);
  const gap = Object.values(bundle.artifacts).find(frame => frame.entities.some(entity => entity.position_enu_m === null));
  assert.deepEqual(new Set(gap.entities.map(entity => entity.kind)), new Set(['aerial', 'ground', 'static']));
  const aerial = gap.entities.find(entity => entity.kind === 'aerial');
  assert.equal(aerial.velocity_enu_mps, null);
  assert.equal(aerial.body, null);
  assert.ok(aerial.unknown_reasons.includes('authored_evidence_gap'));
  assert.equal(bundle.manifest.provenance.is_actual_bench_data, false);
});

test('complete view identity rejects each stale run or revision dimension', async () => {
  const run = await createFixtureRun(DEFAULT_CONFIG), frame = run.frames[0];
  const key = selectionKey(run, frame, frame.entities[0].entity_id).view_key;
  assert.equal(acceptsViewResult(key, structuredClone(key)), true);
  for (const field of ['attachment_id', 'run_id', 'run_epoch', 'manifest_revision']) {
    const stale = structuredClone(key); stale.frame.run[field] += '-stale';
    assert.equal(acceptsViewResult(key, stale), false);
  }
  for (const field of ['frame_seq', 'frame_hash', 'stage_evidence_key']) {
    const stale = structuredClone(key); stale.frame[field] = 'stale';
    assert.equal(acceptsViewResult(key, stale), false);
  }
  for (const field of ['evaluation_revision', 'binding_epoch']) {
    const stale = structuredClone(key); stale[field] += '-stale';
    assert.equal(acceptsViewResult(key, stale), false);
  }
});

test('exact and previous seeks do not clamp or fabricate frames', async () => {
  const run = await createFixtureRun(DEFAULT_CONFIG);
  assert.equal(seekObservation(run, 0).status, 'before_start');
  assert.equal(seekObservation(run, 61).status, 'after_end');
  assert.equal(seekObservation(run, 8.5, 'exact').frame, null);
  assert.equal(seekObservation(run, 8.5, 'previous').frame.relative_time_s, 8);
  assert.throws(() => seekObservation(run, NaN));
  assert.throws(() => seekObservation(run, 1, 'interpolate'));
});
