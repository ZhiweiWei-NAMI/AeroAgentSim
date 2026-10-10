import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_CONFIG, clone, validateConfig, compileConfig, diffConfig } from '../src/config.js';
import { safeImport, exportConfig, loadWorkspace, saveWorkspace, createVersion, createFixtureRun, sha256 } from '../src/runtime.js';
import { DEFAULT_GRAPH } from '../src/graph-config.js';

const memoryStorage = () => {
  const entries = new Map();
  return { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) };
};

test('the same graph drives configuration validation, export, version and desired plan', () => {
  const config = clone(DEFAULT_CONFIG);
  assert.deepEqual(config.graph, DEFAULT_GRAPH);
  assert.equal(validateConfig(config).valid, true);
  assert.deepEqual(safeImport(JSON.stringify(exportConfig(config))).graph, config.graph);
  const version = createVersion(config, []);
  const compiled = compileConfig(config);
  assert.equal(compiled.ok, true);
  assert.deepEqual(compiled.manifest.unified_graph, config.graph);
  assert.equal(compiled.manifest.is_bench_resolved_config, false);
  config.graph.metadata = { ...(config.graph.metadata ?? {}), review_note: 'independent edited draft' };
  assert.notDeepEqual(version.config.graph, config.graph);
  assert.notDeepEqual(compiled.manifest.unified_graph, config.graph);
  assert.ok(diffConfig(version.config, config).some(item => item.path.startsWith('graph.')));
});

test('legacy imports and saved drafts do not silently adopt new graph nodes or bindings', () => {
  const legacy = clone(DEFAULT_CONFIG);
  delete legacy.graph;
  assert.equal(validateConfig(legacy).valid, true);
  assert.equal(Object.hasOwn(safeImport(JSON.stringify(exportConfig(legacy))), 'graph'), false);
  const storage = memoryStorage();
  saveWorkspace(storage, { draft: legacy, versions: [createVersion(legacy, [])], runs: [] });
  const loaded = loadWorkspace(storage, DEFAULT_CONFIG);
  assert.equal(Object.hasOwn(loaded.draft, 'graph'), false);
  assert.equal(Object.hasOwn(loaded.versions[0].config, 'graph'), false);
  assert.equal(Object.hasOwn(compileConfig(legacy).manifest, 'unified_graph'), false);
});

test('invalid or null graph is a scoped error rather than a successful fallback', () => {
  for (const graph of [null, {}, [], { schema_version: 'unknown/v900' }]) {
    const config = clone(DEFAULT_CONFIG);
    config.graph = graph;
    const result = validateConfig(config);
    assert.equal(result.valid, false);
    assert.ok(result.errors.some(item => item.path === 'graph' || item.path.startsWith('graph.')));
    assert.throws(() => safeImport(JSON.stringify(exportConfig(config))));
    assert.equal(compileConfig(config).ok, false);
  }
});

test('fixture preparation freezes the exact graph and executes declared cases once', async () => {
  const config = clone(DEFAULT_CONFIG);
  const run = await createFixtureRun(config);
  assert.equal(run.config_digest, await sha256(run.config));
  assert.deepEqual(run.config.graph, config.graph);
  assert.deepEqual(run.graph_fixtures.map(item => item.scenario_id), config.graph.scenarios.map(item => item.id));
  assert.ok(run.graph_fixtures.every(item => item.status === 'fixture_executed'));
  assert.ok(run.graph_fixtures.every(item => item.predicate_truth === 'UNKNOWN'));
  assert.ok(run.frames.every(frame => frame.stages.semantics === 'not_evaluated'));
  const before = JSON.stringify(run.graph_fixtures);
  config.graph.scenarios[0].label = 'Changed after sealing';
  assert.notEqual(run.config.graph.scenarios[0].label, config.graph.scenarios[0].label);
  assert.equal(JSON.stringify(run.graph_fixtures), before);
});

test('invalid persisted graph is retained for repair without discarding unrelated draft work', () => {
  const config = clone(DEFAULT_CONFIG), storage = memoryStorage();
  config.metadata.name = 'Do not discard this authored draft';
  config.graph.entities.push(null);
  saveWorkspace(storage, { draft: config, versions: [], runs: [] });
  const loaded = loadWorkspace(storage, DEFAULT_CONFIG);
  assert.equal(loaded.draft.metadata.name, config.metadata.name);
  assert.equal(loaded.draft.graph.entities.at(-1), null);
  assert.ok(loaded.warning);
  assert.equal(validateConfig(loaded.draft).valid, false);
});
