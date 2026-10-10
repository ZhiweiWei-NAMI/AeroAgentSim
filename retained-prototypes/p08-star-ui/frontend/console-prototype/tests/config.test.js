import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_CONFIG, INITIAL_SOURCE_CURSOR, clone, validateConfig, diffConfig, compileConfig, normalizeSceneState, advanceSourceCursor } from '../src/config.js';

const config = () => clone(DEFAULT_CONFIG);
const hasError = (value, path) => validateConfig(value).errors.some(error => error.path === path);
const syntheticHash = char => char.repeat(64);
function sceneFixture() {
  const time = { tick: 3, sim_time_ns: '300000000' };
  return {
    schema_version: 'aero-bench.scene-state/v1',
    run_id: syntheticHash('a'), scenario_digest: syntheticHash('b'), at: time,
    declared_entity_ids: ['actor.a'],
    samples: [{
      schema_version: 'aero-bench.state-sample/v1',
      run_id: syntheticHash('a'), scenario_digest: syntheticHash('b'), at: clone(time),
      stage: 'motion', entity_id: 'actor.a', provider_id: 'synthetic-motion', sample_kind: 'dynamic',
      pose: { position: { enu: { east_m: 7, north_m: -13, up_m: 29 } } },
      linear_velocity_enu: { frame_id: 'ENU', east_mps: 3, north_mps: 4, up_mps: -2 },
      sample_digest: syntheticHash('c'),
    }],
    stage_barrier: { test_fixture: 'synthetic opaque barrier' },
    contribution_digests: [syntheticHash('d')], previous_scene_state_digest: syntheticHash('e'), scene_state_digest: syntheticHash('f'),
  };
}
const registry = [{ entity_id: 'actor.a', kind: 'uav', owner_kind: 'scenario', owner_id: 'synthetic-scene', generation: 'generation-a' }];

test('synthetic default is valid and advertises disconnected Atlas/local model', () => {
  const result = validateConfig(DEFAULT_CONFIG);
  assert.equal(result.valid, true, JSON.stringify(result.errors));
  assert.equal(DEFAULT_CONFIG.provenance.kind, 'authored_synthetic');
  assert.equal(DEFAULT_CONFIG.semantics.execution, 'not_connected');
  assert.ok(result.warnings.some(item => item.path === 'semantics.execution'));
  assert.ok(result.warnings.some(item => item.path === 'network.provider'));
});

test('clone is deep and preserves undefined and non-finite invalid values for validation', () => {
  const original = { nested: [{ x: 2 }], missing: undefined, bad: NaN };
  const copied = clone(original);
  copied.nested[0].x = 3;
  assert.equal(original.nested[0].x, 2);
  assert.equal(Object.hasOwn(copied, 'missing'), true);
  assert.equal(Number.isNaN(copied.bad), true);
});

test('malformed top-level and nested values produce diagnostics without throwing', () => {
  for (const input of [null, undefined, [], false, {}, { scenario: null, entities: [null], network: false, compute: [], mobility: null, semantics: false }]) {
    assert.equal(validateConfig(input).valid, false);
    assert.equal(compileConfig(input).manifest, null);
  }
});

test('identifiers and source coordinate convention are explicit', () => {
  const value = config();
  value.entities[0].id = 'UAV/invalid';
  value.scenario.frame = 'NED';
  value.scenario.vertical_datum = 'MSL';
  assert.ok(hasError(value, 'entities[0].id'));
  assert.ok(hasError(value, 'scenario.frame'));
  assert.ok(hasError(value, 'scenario.vertical_datum'));
});

test('duration must be whole milliseconds and divisible by step', () => {
  const value = config();
  value.scenario.duration_s = 60.05;
  assert.ok(hasError(value, 'scenario.duration_s'));
  value.scenario.duration_s = 0.3;
  assert.equal(validateConfig(value).valid, true);
  value.scenario.duration_s = 0.10000001;
  assert.ok(hasError(value, 'scenario.duration_s'));
  value.scenario.duration_s = 0;
  assert.ok(hasError(value, 'scenario.duration_s'));
});

test('finite unit validation rejects coercion, infinities, missing vectors and rates', () => {
  const value = config();
  value.scenario.step_ms = '100';
  value.entities[0].position_enu_m[0] = Infinity;
  value.network.link.configured_rate_mbps = -1;
  value.network.radio_profiles[0].frequency_ghz = NaN;
  value.compute.profiles[0].gpu_units = -0.1;
  assert.ok(hasError(value, 'scenario.step_ms'));
  assert.ok(hasError(value, 'entities[0].position_enu_m'));
  assert.ok(hasError(value, 'network.link.configured_rate_mbps'));
  assert.ok(hasError(value, 'network.radio_profiles[0].frequency_ghz'));
  assert.ok(hasError(value, 'compute.profiles[0].gpu_units'));
});

test('template IDs and generated entity IDs cannot collide', () => {
  const value = config();
  value.entities.push({ ...clone(value.entities[0]), id: 'vehicle-1' });
  assert.ok(hasError(value, `entities[${value.entities.length - 1}].id`));
  value.entities.at(-1).id = 'uav-alpha';
  assert.ok(hasError(value, `entities[${value.entities.length - 1}].id`));
});

test('profile IDs and bindings IDs must be unique', () => {
  const value = config();
  value.network.radio_profiles.push(clone(value.network.radio_profiles[0]));
  value.compute.profiles.push(clone(value.compute.profiles[0]));
  value.semantics.bindings.push(clone(value.semantics.bindings[0]));
  assert.ok(hasError(value, 'network.radio_profiles[1].id'));
  assert.ok(hasError(value, 'compute.profiles[3].id'));
  assert.ok(hasError(value, 'semantics.bindings[1].id'));
});

test('profile references require existing IDs or explicit null', () => {
  const value = config();
  value.entities[0].radio_profile_id = 'missing';
  delete value.entities[0].compute_profile_id;
  assert.ok(hasError(value, 'entities[0].radio_profile_id'));
  assert.ok(hasError(value, 'entities[0].compute_profile_id'));
  value.entities[0].radio_profile_id = null;
  value.entities[0].compute_profile_id = null;
  assert.equal(validateConfig(value).valid, true);
});

test('semantic bindings join exact expanded IDs, never count-template names', () => {
  const value = config();
  value.semantics.bindings[0].entity_id = 'vehicle';
  assert.ok(hasError(value, 'semantics.bindings[0].entity_id'));
  value.semantics.bindings[0].entity_id = 'vehicle-2';
  assert.equal(validateConfig(value).valid, true);
});

test('SUMO must be enabled with matching clock and a ground entity type', () => {
  const value = config();
  value.entities[2].provider = 'sumo';
  assert.ok(hasError(value, 'entities[2].provider'));
  value.mobility.sumo.enabled = true;
  value.mobility.sumo.step_ms = 200;
  assert.ok(hasError(value, 'mobility.sumo.step_ms'));
  value.mobility.sumo.step_ms = 100;
  assert.equal(validateConfig(value).valid, true);
  value.entities[0].provider = 'sumo';
  assert.ok(hasError(value, 'entities[0].provider'));
});

test('provider model distinctions reject unsupported LTE and fake Atlas execution', () => {
  const value = config();
  value.network.provider = 'ns3';
  let result = validateConfig(value);
  assert.equal(result.valid, true);
  assert.ok(result.warnings.some(item => item.message.includes('no ns3 process')));
  value.network.radio_profiles[0].wifi_standard = 'LTE';
  value.semantics.execution = 'connected';
  value.compute.provider = 'ns3';
  assert.ok(hasError(value, 'network.radio_profiles[0].wifi_standard'));
  assert.ok(hasError(value, 'semantics.execution'));
  assert.ok(hasError(value, 'compute.provider'));
});

test('Wi-Fi channel width and unit conversion constraints are enforced', () => {
  const value = config();
  value.network.radio_profiles[0].wifi_standard = '802.11n';
  value.network.radio_profiles[0].channel_width_mhz = 80;
  value.network.link.propagation_delay_ms = 0.0000001;
  value.network.link.configured_rate_mbps = 0.0000001;
  assert.ok(hasError(value, 'network.radio_profiles[0].channel_width_mhz'));
  assert.ok(hasError(value, 'network.link.propagation_delay_ms'));
  assert.ok(hasError(value, 'network.link.configured_rate_mbps'));
});

test('unsupported fields and geometry-dependent targets are surfaced', () => {
  const value = config();
  value.network.antenna_gain_db = 7;
  value.semantics.bindings[0].target = 'hu.predicate.pair_close';
  const result = validateConfig(value);
  assert.equal(result.valid, true);
  assert.ok(result.warnings.some(item => item.path === 'network.antenna_gain_db'));
  assert.ok(result.warnings.some(item => item.path === 'semantics.bindings[0].target'));
});

test('browser expansion cap catches unreasonable counts without allocating them', () => {
  const value = config();
  value.entities[0].count = Number.MAX_SAFE_INTEGER;
  assert.ok(hasError(value, 'entities[0].count'));
  assert.equal(validateConfig(value).valid, false);
});

test('parameters reject non-JSON, non-finite, and cyclic inputs', () => {
  const value = config();
  const parameters = { missing: undefined, threshold: Infinity };
  parameters.self = parameters;
  value.semantics.bindings[0].parameters = parameters;
  const result = validateConfig(value);
  assert.equal(result.valid, false);
  assert.ok(result.errors.some(item => item.message.includes('circular')));
});

test('diff reports deterministic nested leaves, additions and removals without mutation', () => {
  const before = { z: true, entities: [{ id: 'a', n: 1 }], keep: null };
  const after = { entities: [{ id: 'a', n: 2 }, { id: 'b' }], keep: null };
  const changes = diffConfig(before, after);
  assert.deepEqual(changes.map(change => change.path), ['entities[0].n', 'entities[1]', 'z']);
  assert.equal(changes[0].before, 1);
  assert.equal(changes[0].after, 2);
  changes[1].after.id = 'changed';
  assert.equal(after.entities[1].id, 'b');
  assert.deepEqual(diffConfig(config(), config()), []);
});

test('compile returns detached desired plan with explicit configured units and unsupported gates', () => {
  const input = config();
  const result = compileConfig(input);
  assert.equal(result.ok, true);
  const plan = result.manifest;
  assert.equal(plan.is_bench_resolved_config, false);
  assert.equal(plan.artifact_kind, 'desired_configuration_plan');
  assert.equal(plan.execution_status, 'not_started');
  assert.equal(plan.desired_clock.step_ns, '100000000');
  assert.equal(plan.desired_clock.duration_ns, '60000000000');
  assert.equal(plan.desired_clock.total_ticks, 600);
  assert.equal(plan.network.configured_link.data_rate_bps, 24000000);
  assert.equal(plan.network.configured_link.propagation_delay_ns, '2000000');
  assert.equal(plan.network.configured_link.measured_throughput_bps, null);
  assert.equal(plan.network.configured_link.measured_end_to_end_latency_ms, null);
  assert.equal(plan.entities.length, 12);
  assert.ok(plan.entities.some(entity => entity.entity_id === 'vehicle-3'));
  assert.ok(plan.source_mapping.some(mapping => mapping.status === 'requires_bench_compiler'));
  assert.equal(plan.semantics.evaluated_count, 0);
  plan.entities[0].initial_position_enu_m[0] = 999;
  plan.metadata.name = 'Different';
  assert.notEqual(input.entities[0].position_enu_m[0], 999);
  assert.notEqual(input.metadata.name, 'Different');
  assert.ok(result.diagnostics.some(diagnostic => diagnostic.code === 'unsupported_extent'));
});

test('compiler distinguishes a desired ns3 connection from local demo execution', () => {
  const value = config();
  value.network.provider = 'ns3';
  const result = compileConfig(value);
  assert.equal(result.ok, true);
  assert.equal(result.manifest.network.connection_status, 'not_connected');
  assert.equal(result.manifest.network.provider, 'ns3');
});

test('strict SceneState motion normalization preserves ENU sign, IDs and source evidence', () => {
  const input = sceneFixture();
  const result = normalizeSceneState(input, registry);
  assert.equal(result.ok, true, JSON.stringify(result.diagnostics));
  const entity = result.frame.entities[0];
  assert.equal(entity.entity_id, 'actor.a');
  assert.deepEqual(entity.position_enu_m, [7, -13, 29]);
  assert.deepEqual(entity.velocity_enu_mps, [3, 4, -2]);
  assert.equal(entity.horizontal_speed_mps, 5);
  assert.equal(entity.vertical_speed_mps, -2);
  assert.equal(entity.generation, 'generation-a');
  assert.equal(entity.provenance.kind, 'simulated');
  assert.equal(result.frame.time_ns, '300000000');
  assert.equal(result.frame.readiness.network, 'not_established');
  result.frame.source.stage_barrier.test_fixture = 'modified';
  assert.equal(input.stage_barrier.test_fixture, 'synthetic opaque barrier');
});

test('large ns use strings and are never silently rounded into a JS number', () => {
  const input = sceneFixture();
  input.at.sim_time_ns = '900719925474099312345';
  input.samples[0].at.sim_time_ns = input.at.sim_time_ns;
  const result = normalizeSceneState(input, registry);
  assert.equal(result.ok, true);
  assert.equal(result.frame.time_ns, '900719925474099312345');
  input.at.sim_time_ns = Number(input.at.sim_time_ns);
  assert.equal(normalizeSceneState(input, registry).ok, false);
});

test('missing velocity cannot become stationary evidence and old flattened shape is rejected', () => {
  const input = sceneFixture();
  delete input.samples[0].linear_velocity_enu;
  input.samples[0].velocity_enu_mps = [0, 0, 0];
  const result = normalizeSceneState(input, registry);
  assert.equal(result.ok, false);
  assert.equal(result.frame, null);
  assert.ok(result.diagnostics.some(item => item.path.endsWith('linear_velocity_enu')));
  const flat = { ...sceneFixture(), samples: undefined, entities: [{ entity_id: 'actor.a', position: [0, 0, 0] }] };
  assert.equal(normalizeSceneState(flat, registry).ok, false);
});

test('tick zero, mixed-stage, mixed-time, or mixed-run samples fail closed', () => {
  for (const mutate of [
    input => { input.at.tick = 0; input.samples[0].at.tick = 0; },
    input => { input.samples[0].stage = 'network'; },
    input => { input.samples[0].at.sim_time_ns = '400000000'; },
    input => { input.samples[0].run_id = syntheticHash('d'); },
    input => { input.samples[0].linear_velocity_enu.frame_id = 'NED'; },
  ]) {
    const input = sceneFixture(); mutate(input);
    assert.equal(normalizeSceneState(input, registry).ok, false);
  }
});

test('declared IDs, samples and static registry join by exact source identity', () => {
  const input = sceneFixture();
  assert.equal(normalizeSceneState(input, [{ entity_id: 'vehicle_actor.a' }]).ok, false);
  assert.equal(normalizeSceneState(input, [{ entity_id: 'actor.a' }, { entity_id: 'actor.a' }]).ok, false);
  input.declared_entity_ids.push('actor.b');
  assert.equal(normalizeSceneState(input, registry).ok, false);
  input.samples.push(clone(input.samples[0]));
  assert.equal(normalizeSceneState(input, registry).ok, false);
});

test('registry omission preserves source IDs and reports missing ownership', () => {
  const result = normalizeSceneState(sceneFixture());
  assert.equal(result.ok, true);
  assert.equal(result.frame.entities[0].type, null);
  assert.equal(result.frame.entities[0].owner_id, null);
  assert.ok(result.diagnostics.some(item => item.code === 'missing_provenance'));
  assert.equal(normalizeSceneState(sceneFixture(), new Map([['actor.a', registry[0]]])).ok, true);
  assert.equal(normalizeSceneState(sceneFixture(), { 'actor.a': registry[0] }).ok, true);
});

test('source barrier is required and key/value registry identity conflicts fail closed', () => {
  const input = sceneFixture();
  delete input.stage_barrier;
  assert.equal(normalizeSceneState(input, registry).ok, false);
  const result = normalizeSceneState(sceneFixture(), registry);
  assert.equal(result.frame.source.integrity_verified, false);
  assert.equal(normalizeSceneState(sceneFixture(), { 'actor.a': { entity_id: 'actor.b' } }).ok, false);
  assert.equal(normalizeSceneState(sceneFixture(), new Map([['actor.a', null]])).ok, false);
});

test('live cursors advance independently in all three exact families', () => {
  let cursor = clone(INITIAL_SOURCE_CURSOR);
  cursor = advanceSourceCursor(cursor, { schema_version: 'aero-bench.run-transition-event/v1', transition: { sequence: 2 } });
  assert.deepEqual(cursor, { afterTransition: 2, afterSceneTick: 0, afterEventSequence: -1 });
  cursor = advanceSourceCursor(cursor, { type: 'scene.state', data: { schema_version: 'aero-bench.scene-state-stream-event/v1', scene_state: { at: { tick: 5 } } } });
  cursor = advanceSourceCursor(cursor, { event: 'run.event', data: JSON.stringify({ schema_version: 'aero-bench.public-run-event-stream-event/v1', event: { sequence: 9 } }) });
  assert.deepEqual(cursor, { afterTransition: 2, afterSceneTick: 5, afterEventSequence: 9 });
  const duplicate = advanceSourceCursor(cursor, { type: 'scene.state', data: { scene_state: { at: { tick: 3 } } } });
  assert.deepEqual(duplicate, cursor);
  assert.deepEqual(INITIAL_SOURCE_CURSOR, { afterTransition: -1, afterSceneTick: 0, afterEventSequence: -1 });
});

test('cursor preserves attachment metadata, never uses wire ID as a recovery cursor', () => {
  const cursor = { ...INITIAL_SOURCE_CURSOR, run_id: 'synthetic-run', epoch: 'epoch-1', manifest_revision: 3 };
  const result = advanceSourceCursor(cursor, { type: 'run.event', id: 'event.0000000000000008', data: { run_id: 'synthetic-run', event: { sequence: 4 } } });
  assert.equal(result.afterEventSequence, 4);
  assert.equal(result.epoch, 'epoch-1');
  assert.equal(result.manifest_revision, 3);
  assert.equal(cursor.afterEventSequence, -1);
  assert.throws(() => advanceSourceCursor(cursor, { type: 'run.event', data: { run_id: 'another-run', event: { sequence: 5 } } }), /different run/);
});

test('cursor rejects malformed, mismatched-family, and sealed replay inputs', () => {
  for (const event of [null, {}, { type: 'run.event', data: '{' }, { type: 'scene.state', data: { scene_state: { at: { tick: 0 } } } }, { type: 'run.event', data: { event: { sequence: '4' } } }, { type: 'run.event', data: { schema_version: 'aero-bench.scene-state-stream-event/v1', scene_state: { at: { tick: 3 } } } }, { schema_version: 'aero-bench.public-replay-index/v1' }]) {
    assert.throws(() => advanceSourceCursor(INITIAL_SOURCE_CURSOR, event), TypeError);
  }
  assert.throws(() => advanceSourceCursor({ afterTransition: NaN }, {}), TypeError);
});

test('sub-millisecond and zero-tick durations cannot compile; decimal milliseconds remain exact', () => {
  const value = config();
  for (const duration of [1e-12, 0.0000000001, 0.0001, 0.001]) {
    value.scenario.duration_s = duration;
    assert.equal(compileConfig(value).ok, false);
  }
  value.scenario.duration_s = 1.001;
  value.scenario.step_ms = 1;
  const compiled = compileConfig(value);
  assert.equal(compiled.ok, true);
  assert.equal(compiled.manifest.desired_clock.duration_ns, '1001000000');
  assert.equal(compiled.manifest.desired_clock.total_ticks, 1001);
});

test('unsupported JSON branches reject nonfinite values, cycles and excessive depth', () => {
  const value = config();
  value.extension = { opaque: Infinity };
  assert.equal(validateConfig(value).valid, false);
  value.extension = {};
  value.extension.self = value.extension;
  assert.equal(validateConfig(value).valid, false);
  let branch = {};
  value.extension = branch;
  for (let i = 0; i < 100; i++) { branch.next = {}; branch = branch.next; }
  assert.equal(validateConfig(value).valid, false);
  delete value.extension;
  value.provenance.value = NaN;
  assert.equal(validateConfig(value).valid, false);
});

test('local authoring bounds coordinates and stops large aggregate instance expansions', () => {
  const value = config();
  value.entities[0].position_enu_m = [1.7e308, 0, 0];
  assert.ok(hasError(value, 'entities[0].position_enu_m'));
  value.entities = Array.from({ length: 1000 }, (_, i) => ({ ...clone(DEFAULT_CONFIG.entities[0]), id: `group-${i}`, count: 10000 }));
  const started = performance.now();
  assert.equal(validateConfig(value).valid, false);
  assert.ok(performance.now() - started < 3000, 'Aggregate cap should reject before millions of expansions');
});

test('unverified barrier never establishes commit and derived velocity must remain finite', () => {
  const input = sceneFixture();
  input.stage_barrier = {};
  const projected = normalizeSceneState(input, registry);
  assert.equal(projected.ok, true);
  assert.equal(projected.frame.readiness.motion, 'barrier_unverified');
  assert.ok(projected.diagnostics.some(item => item.code === 'barrier_unverified'));
  input.samples[0].linear_velocity_enu.east_mps = 1.7e308;
  input.samples[0].linear_velocity_enu.north_mps = 1.7e308;
  assert.equal(normalizeSceneState(input, registry).ok, false);
});
