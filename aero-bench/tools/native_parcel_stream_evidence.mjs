import { closeSync, openSync, writeFileSync } from 'node:fs';
import { basename } from 'node:path';

/** Append actual SSE envelopes once; retain only the observations used by capture. */
export class NativeParcelStreamEvidence {
  constructor({ runId, path, sanitize }) {
    this.runId = runId;
    this.path = path;
    this.sanitize = sanitize;
    // A new capture must not replace or mix an earlier capture's transcript.
    this.fd = openSync(path, 'wx');
    this.ids = new Set();
    this.recordCount = 0;
    this.duplicateCount = 0;
    this.idlessCount = 0;
    this.eventCounts = new Map();
    this.latestParcelRecord = null;
    this.carrierPositionsByTick = new Map();
  }

  append(record) {
    if (this.fd === null) throw new Error('Stream evidence is closed');
    if (record.payload.run_id !== this.runId) throw new Error('UI stream returned a different run');
    const identified = typeof record.id === 'string' && record.id.length > 0;
    const key = identified ? `${record.event}:${record.id}` : null;
    if (key !== null && this.ids.has(key)) {
      this.duplicateCount += 1;
      return;
    }
    // Only this new envelope is serialized. The full transcript stays on disk.
    writeFileSync(this.fd, JSON.stringify(this.sanitize(record)) + '\n');
    if (key !== null) this.ids.add(key); else this.idlessCount += 1;
    this.recordCount += 1;
    this.eventCounts.set(record.event, (this.eventCounts.get(record.event) ?? 0) + 1);
    if (record.event === 'run.event' && record.payload.event?.event_type === 'public.parcel-projection') {
      const sequence = record.payload.event.sequence;
      if (!Number.isInteger(sequence) || sequence < 0) throw new Error('Parcel projection has no valid event sequence');
      if (this.latestParcelRecord === null || sequence > this.latestParcelRecord.payload.event.sequence) {
        this.latestParcelRecord = record;
      }
    }
    if (record.event === 'scene.state') {
      const state = record.payload.scene_state;
      const carrier = state.samples.find(sample => sample.entity_id === 'uav.p02.carrier');
      if (carrier !== undefined) this.carrierPositionsByTick.set(state.at.tick, JSON.stringify(carrier.pose.position.enu));
    }
  }

  summary() {
    return {
      relative_path: basename(this.path), format: 'application/x-ndjson',
      record_count: this.recordCount, duplicate_count: this.duplicateCount,
      idless_record_count: this.idlessCount, event_counts: Object.fromEntries(this.eventCounts),
      last_parcel_event_id: this.latestParcelRecord?.payload.event.event_id ?? null,
      last_parcel_sequence: this.latestParcelRecord?.payload.event.sequence ?? null,
      carrier_pose_tick_count: this.carrierPositionsByTick.size,
    };
  }

  close() {
    if (this.fd !== null) { closeSync(this.fd); this.fd = null; }
  }
}
