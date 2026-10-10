/** Reproducible fixture contract report; no simulator, Atlas or network calls. */
import { DEFAULT_CONFIG, clone, validateConfig } from '../src/config.js';
import { executeGraphFixture } from '../src/graph-runtime.js';
import { sha256 } from '../src/runtime.js';

const config = clone(DEFAULT_CONFIG);
const validation = validateConfig(config);
const cases = validation.valid ? config.graph.scenarios.map(scenario => executeGraphFixture(config.graph, scenario.id)) : [];
const report = {
  schema_version: 'aeroagentsim.graph-fixture-report/v1',
  config_schema: config.schema_version,
  graph_schema: config.graph.schema_version,
  config_digest: await sha256(config),
  graph_digest: await sha256(config.graph),
  validation,
  cases,
  real_connected: false,
  native_predicate_evaluation: 'unavailable',
  actuator_effects: 'not_executed',
};
console.log(JSON.stringify(report, null, 2));
if (!validation.valid || cases.some(item => item.status !== 'fixture_executed')) process.exitCode = 1;
