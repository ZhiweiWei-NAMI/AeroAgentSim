import { afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { NativeParcelStreamEvidence } from './native_parcel_stream_evidence.mjs';

const temporary = [];
afterEach(() => { for (const path of temporary.splice(0)) rmSync(path, { recursive: true, force: true }); });
function evidence() {
  const directory = mkdtempSync(join(tmpdir(), 'native-parcel-stream-test-')); temporary.push(directory);
  const path = join(directory, 'runtime-stream-evidence.jsonl');
  return { path, stream: new NativeParcelStreamEvidence({ runId: 'run.test', path, sanitize: value => value }) };
}
function parcel(sequence, state) {
  return { event: 'run.event', id: `event.${sequence}`, payload: { run_id: 'run.test', event: {
    event_id: `event.${sequence}`, sequence, event_type: 'public.parcel-projection',
    public_payload: [{ name: 'parcel_state', value: state }],
  } } };
}

test('appends new envelopes once, preserves measurements, and does not regress the latest parcel after reconnect', () => {
  const { path, stream } = evidence();
  const scene = { event: 'scene.state', id: 'scene.1', payload: { run_id: 'run.test', scene_state: {
    at: { tick: 1 }, samples: [{ entity_id: 'uav.p02.carrier', pose: { position: { enu: {
      east_m: -419.98, north_m: -449.99, up_m: 11.68,
    } } }, link_quality: null }],
  } } };
  const loaded = parcel(10, 'loaded'), old = parcel(5, 'awaiting_pickup');
  try {
    for (const record of [scene, loaded, scene, loaded, old]) stream.append(record);
    const lines = readFileSync(path, 'utf8').trimEnd().split('\n').map(line => JSON.parse(line));
    assert.deepEqual(lines, [scene, loaded, old]);
    assert.equal(lines[0].payload.scene_state.samples[0].link_quality, null);
    assert.deepEqual(JSON.parse(stream.carrierPositionsByTick.get(1)), scene.payload.scene_state.samples[0].pose.position.enu);
    assert.equal(stream.latestParcelRecord.payload.event.event_id, 'event.10');
    assert.equal(stream.summary().record_count, 3);
    assert.equal(stream.summary().duplicate_count, 2);
  } finally { stream.close(); }
});

test('retains idless envelopes rather than silently deduplicating unrelated records', () => {
  const { path, stream } = evidence();
  const record = { event: 'run.snapshot', id: null, payload: { run_id: 'run.test', phase: 'running' } };
  try {
    stream.append(record); stream.append(record);
    assert.equal(readFileSync(path, 'utf8').trimEnd().split('\n').length, 2);
    assert.equal(stream.summary().idless_record_count, 2);
  } finally { stream.close(); }
});

test('rejects another run without adding it to evidence', () => {
  const { path, stream } = evidence();
  try {
    assert.throws(() => stream.append({ event: 'run.transition', id: 'transition.1', payload: { run_id: 'other.run' } }), /different run/);
    assert.equal(readFileSync(path, 'utf8'), '');
    assert.equal(stream.summary().record_count, 0);
  } finally { stream.close(); }
});

test('does not overwrite an earlier evidence file', () => {
  const directory = mkdtempSync(join(tmpdir(), 'native-parcel-stream-test-')); temporary.push(directory);
  const path = join(directory, 'runtime-stream-evidence.jsonl'); writeFileSync(path, 'earlier capture\n');
  assert.throws(() => new NativeParcelStreamEvidence({ runId: 'run.test', path, sanitize: value => value }), { code: 'EEXIST' });
  assert.equal(readFileSync(path, 'utf8'), 'earlier capture\n');
});
