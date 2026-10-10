/** Browser fixture projection into the independently authored Python contracts.
 * This module has no transport, lifecycle control, evaluator or simulation loop.
 */
export function runIdentity(run) {
  return { attachment_id: run.id, run_id: run.id, run_epoch: run.epoch, manifest_revision: String(run.manifest_revision) };
}

export function projectObservation(run, frame) {
  const frames = run.frames;
  const index = frames.indexOf(frame);
  if (index < 0) throw new TypeError('Frame is not part of this sealed fixture');
  const start = frames[0].sim_time_ns;
  return {
    schema: 'aeroagentsim.observation/v1',
    run: runIdentity(run), frame_seq: frame.tick,
    stage_evidence_key: `fixture.motion.${frame.tick}`,
    sim_time_ns: frame.sim_time_ns, engine_origin_ns: '0', coordinate_frame: 'ENU',
    units: { position: 'm', velocity: 'm/s', time: 'ns' },
    entities: frame.entities.map(entity => ({
      entity_id: entity.entity_id,
      kind: entity.type === 'uav' ? 'aerial' : ['vehicle', 'pedestrian'].includes(entity.type) ? 'ground' : 'static',
      born_ns: start, ended_ns: null, sample_time_ns: frame.sim_time_ns,
      valid_until_ns: frames[index + 1]?.sim_time_ns ?? frame.sim_time_ns,
      position_enu_m: entity.position_enu_m, velocity_enu_mps: entity.velocity_enu_mps,
      body: null,
      provenance: { source: 'fixture.authored', source_provider_id: entity.source_provider_id, quantity_kind: 'hypothetical', source_pointer: `entities/${entity.entity_id}`, validity: entity.validity },
      unknown_reasons: [...(entity.reason ? ['authored_evidence_gap'] : []), 'body_extent_not_supplied'],
    })),
    provenance: { source: 'console.synthetic', is_actual_bench_data: false, config_digest: run.config_digest, stage_status: frame.stages },
  };
}

export function viewKey(run, frame) {
  if (!frame.observation_hash) throw new TypeError('Fixture observation hash is unavailable');
  return {
    frame: { run: runIdentity(run), frame_seq: frame.tick, frame_hash: frame.observation_hash, stage_evidence_key: `fixture.motion.${frame.tick}` },
    evaluation_revision: run.evaluation_revision ?? 'not-evaluated', binding_epoch: `fixture-unbound.${run.config_digest}`,
  };
}

export function acceptsViewResult(current, candidate) {
  if (!current || !candidate) return false;
  const a = current.frame, b = candidate.frame;
  return !!a && !!b && ['attachment_id', 'run_id', 'run_epoch', 'manifest_revision'].every(key => a.run?.[key] === b.run?.[key])
    && ['frame_seq', 'frame_hash', 'stage_evidence_key'].every(key => a[key] === b[key])
    && current.evaluation_revision === candidate.evaluation_revision && current.binding_epoch === candidate.binding_epoch;
}

export function seekObservation(run, seconds, mode = 'previous') {
  if (!Number.isFinite(seconds) || !['exact', 'previous'].includes(mode)) throw new TypeError('Invalid seek request');
  if (!run?.frames?.length) return { status: 'not_indexed', frame: null };
  const first = run.frames[0], last = run.frames.at(-1);
  if (seconds < first.relative_time_s) return { status: 'before_start', frame: null };
  if (seconds > last.relative_time_s) return { status: 'after_end', frame: null };
  let previous = null;
  for (const frame of run.frames) {
    if (frame.relative_time_s === seconds) return { status: 'exact', frame };
    if (frame.relative_time_s > seconds) break;
    previous = frame;
  }
  return { status: mode === 'previous' ? 'previous' : 'not_indexed', frame: mode === 'previous' ? previous : null };
}
