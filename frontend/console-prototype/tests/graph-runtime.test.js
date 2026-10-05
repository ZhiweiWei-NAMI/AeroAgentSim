import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_GRAPH } from '../src/graph-config.js';
import { executeGraphFixture, resolveGraphFact, evaluateFixtureAdmission } from '../src/graph-runtime.js';
const fresh = () => structuredClone(DEFAULT_GRAPH);

test('all four fixture cases resolve deterministic authored bindings without input mutation', () => {
  const graph = fresh(), before = JSON.stringify(graph);
  for (const scenario of graph.scenarios) {
    const result = executeGraphFixture(graph, scenario.id);
    assert.equal(result.status, 'fixture_executed');
    assert.deepEqual(executeGraphFixture(graph, scenario.id), result);
    assert.equal(result.readiness.real_connected, false);
    assert.equal(result.predicate_truth, 'UNKNOWN');
    assert.deepEqual(result.assessments, { feasibility: 'UNKNOWN', permission: 'UNKNOWN', suitability: 'UNKNOWN' });
    assert.ok(result.commands.every(command => command.effect === 'not_executed'));
    assert.ok(result.bindings.every(binding => binding.mode === 'fixture' && binding.real_connected === false));
  }
  assert.equal(JSON.stringify(graph), before);
});

test('fixture decisions, commands, events and resource usage come from editable graph records', () => {
  const graph = fresh(); graph.decisions[0].rationale = 'Authored revised charging reason'; graph.commands[0].action = 'request_custom_charge'; graph.resources[0].capacity = 2;
  const result = executeGraphFixture(graph, 'charging');
  assert.equal(result.decisions[0].rationale, graph.decisions[0].rationale);
  assert.equal(result.commands[0].action, 'request_custom_charge');
  assert.equal(result.events[0].definition_id, 'charge-requested');
  assert.equal(result.events[0].record_kind, 'fixture_event_occurrence');
  assert.equal(result.resource_usage[0].capacity, 2);
  result.decisions[0].rationale = 'changed output';
  assert.equal(graph.decisions[0].rationale, 'Authored revised charging reason');
});

test('fact resolution uses both valid time and availability time with exact nanoseconds', () => {
  const input = { entity_id: 'uav-alpha', field_id: 'link-quality' };
  assert.equal(resolveGraphFact(DEFAULT_GRAPH, { ...input, at_ns: '1000000000' }).reason, 'not_yet_available');
  assert.equal(resolveGraphFact(DEFAULT_GRAPH, { ...input, at_ns: '1200000000' }).value, 0.2);
  assert.equal(resolveGraphFact(DEFAULT_GRAPH, { ...input, at_ns: '60000000001' }).reason, 'stale_fact');
  assert.equal(resolveGraphFact(DEFAULT_GRAPH, { ...input, at_ns: '900719925474099300000' }).reason, 'stale_fact');
  assert.equal(resolveGraphFact(DEFAULT_GRAPH, { ...input, at_ns: 1200000000 }).reason, 'invalid_time');
});

test('missing, null, context-mismatched and ambiguous observations remain UNKNOWN', () => {
  const input = { entity_id: 'uav-alpha', field_id: 'battery', at_ns: '2000000000' };
  assert.equal(resolveGraphFact(DEFAULT_GRAPH, { ...input, fact_ids: [] }).reason, 'missing_fact');
  assert.equal(resolveGraphFact(DEFAULT_GRAPH, { ...input, source_id: 'other-source' }).reason, 'source_mismatch');
  let graph = fresh(); graph.facts.find(fact => fact.id === 'alpha-battery').value = null;
  assert.equal(resolveGraphFact(graph, input).reason, 'missing_value');
  graph = fresh(); graph.facts.find(fact => fact.id === 'alpha-battery').unit = 'kg';
  assert.equal(resolveGraphFact(graph, input).reason, 'field_context_mismatch');
  graph = fresh(); graph.facts.push({ ...graph.facts.find(fact => fact.id === 'alpha-battery'), id: 'second-battery' });
  assert.equal(resolveGraphFact(graph, input).reason, 'ambiguous_observation');
});

test('bounded fixture admission evaluates available scoped facts and records exact evidence', () => {
  const start = evaluateFixtureAdmission(DEFAULT_GRAPH, 'pad-admission', { at_ns: '1000000000' });
  const blocked = evaluateFixtureAdmission(DEFAULT_GRAPH, 'pad-admission', { at_ns: '2000000000' });
  assert.equal(start.verdict, 'PERMIT'); assert.equal(blocked.verdict, 'BLOCK');
  assert.equal(start.evidence[0].fact_id, 'pad-open');
  assert.equal(blocked.evidence[0].fact_id, 'pad-closed');
  assert.equal(blocked.scope, 'fixture_only');
  assert.equal(blocked.actual_assessments.permission, 'UNKNOWN');
  assert.equal(evaluateFixtureAdmission(DEFAULT_GRAPH, 'pad-admission', { at_ns: '2000000000', fact_ids: [] }).verdict, 'UNKNOWN');
});

test('constraint change closes authored feedback loop and pauses only through declared policy', () => {
  const result = executeGraphFixture(DEFAULT_GRAPH, 'constraint-change');
  assert.deepEqual(result.check_results.map(check => [check.phase, check.verdict]), [['start', 'PERMIT'], ['continue', 'BLOCK']]);
  assert.equal(result.updated_facts[0].id, 'pad-closed');
  assert.equal(result.feedback[0].policy_id, 'pause-on-pad-loss');
  assert.equal(result.feedback[0].response, 'pause');
  assert.equal(result.behaviors[0].status, 'fixture_paused');
  assert.equal(result.events.find(event => event.definition_id === 'pad-unavailable-event').check_result_id, result.check_results[1].id);
  const graph = fresh(); graph.feedback_policies = [];
  const noPolicy = executeGraphFixture(graph, 'constraint-change');
  assert.equal(noPolicy.feedback.length, 0);
  assert.equal(noPolicy.behaviors[0].status, 'fixture_blocked');
});

test('configured abort/replan policies and changed threshold actually change fixture results', () => {
  for (const response of ['abort', 'replan']) {
    const graph = fresh(); graph.feedback_policies[0].response = response;
    const result = executeGraphFixture(graph, 'constraint-change');
    assert.equal(result.feedback[0].response, response);
    assert.equal(result.behaviors[0].status, response === 'abort' ? 'fixture_aborted' : 'fixture_replan_requested');
  }
  const graph = fresh(); graph.constraints.find(constraint => constraint.id === 'pad-availability').condition.expected = false;
  const result = executeGraphFixture(graph, 'constraint-change');
  assert.deepEqual(result.check_results.map(check => check.verdict), ['BLOCK', 'PERMIT']);
  assert.equal(result.feedback.length, 0);
});

test('fixture time cutoff omits future constraint changes and future evidence', () => {
  const result = executeGraphFixture(DEFAULT_GRAPH, 'constraint-change', { at_ns: '1500000000' });
  assert.equal(result.check_results.length, 1);
  assert.equal(result.check_results[0].verdict, 'PERMIT');
  assert.equal(result.updated_facts.length, 0);
  assert.equal(result.feedback.length, 0);
  assert.equal(result.facts.find(fact => fact.field_id === 'pad-available').fact_id, 'pad-open');
});

test('conditional command stays UNKNOWN instead of replacing native predicate evaluation', () => {
  const graph = fresh(); const command = graph.commands[0]; command.composition.mode = 'conditional'; command.composition.conditions = [{ behavior_id: 'recharge', predicate_id: 'low-battery', expected: 'TRUE' }];
  const result = executeGraphFixture(graph, 'constraint-change');
  assert.equal(result.validation.valid, true);
  // Feedback may pause the declared behavior, but no native condition was ever evaluated.
  assert.equal(result.behaviors[0].condition_result, 'UNKNOWN');
  assert.equal(result.commands[0].effect, 'not_executed');
});

test('physical feasibility, permission and task suitability remain independent UNKNOWN', () => {
  const graph = fresh(); graph.decisions[0].assessments = { feasibility: 'TRUE', permission: 'FALSE', suitability: 'TRUE' };
  const result = executeGraphFixture(graph, 'charging');
  assert.deepEqual(result.decisions[0].authored_assessments, graph.decisions[0].assessments);
  assert.deepEqual(result.decisions[0].assessments, { feasibility: 'UNKNOWN', permission: 'UNKNOWN', suitability: 'UNKNOWN' });
});

test('malformed graph, unknown scenario and invalid time fail closed', () => {
  assert.equal(executeGraphFixture({}, 'charging').status, 'blocked');
  assert.equal(executeGraphFixture(DEFAULT_GRAPH, 'missing').error, 'unknown_scenario');
  assert.equal(executeGraphFixture(DEFAULT_GRAPH, 'charging', { at_ns: -1 }).error, 'invalid_time');
  assert.equal(executeGraphFixture(DEFAULT_GRAPH, 'charging', { source_id: 'unknown-source' }).facts.every(fact => fact.status === 'UNKNOWN'), true);
});

test('predicate result basis carries exact facts and unavailable history instead of fabricated truth', () => {
  const result = executeGraphFixture(DEFAULT_GRAPH, 'comm-return');
  const predicate = result.predicate_results.find(record => record.predicate_id === 'link-degraded');
  assert.equal(predicate.truth, 'UNKNOWN');
  assert.equal(predicate.scope_status, 'UNKNOWN');
  assert.equal(predicate.evidence[0].fact_id, 'alpha-link');
  assert.equal(predicate.evidence[0].available_time_ns, '1200000000');
  assert.equal(predicate.history_status, 'not_supplied');
});

test('one passing fixture check never claims complete hard-constraint admission or module execution', () => {
  const result = executeGraphFixture(DEFAULT_GRAPH, 'constraint-change', { at_ns: '1500000000' });
  assert.equal(result.check_results[0].verdict, 'PERMIT');
  assert.equal(result.behaviors[0].status, 'fixture_partial_admission');
  assert.equal(result.behaviors[0].admission.verdict, 'UNKNOWN');
  assert.ok(result.behaviors[0].admission.unchecked_constraint_ids.includes('reserve-policy'));
  assert.ok(result.behaviors[0].admission.unchecked_constraint_ids.includes('charger-policy'));
  assert.ok(result.bindings.every(binding => binding.status === 'contract_resolved' && binding.execution === 'not_executed'));
  assert.equal(result.stages.strategy, 'authored_decision_replay');
  assert.equal(result.stages.modules, 'not_executed');
  assert.equal(result.predicate_results[0].binding_revision, null);
});

test('closed-loop fixture trace is chronological and initial decision cannot read future facts', () => {
  const graph = fresh();
  graph.strategies[0].observable_fact_ids.push('pad-closed');
  graph.strategies[0].input_fields.push({ field_id: 'pad-available', value_type: 'boolean', unit: '1', frame: 'none' });
  graph.agents[0].observable_fact_ids.push('pad-closed');
  const result = executeGraphFixture(graph, 'constraint-change');
  assert.equal(result.status, 'fixture_executed');
  const times = result.trace.map(entry => BigInt(entry.at_ns));
  assert.ok(times.every((time, index) => index === 0 || time >= times[index - 1]));
  assert.equal(result.decisions[0].decision_time_ns, '1000000000');
  const observation = result.decisions[0].input_observations.find(item => item.field_id === 'pad-available');
  assert.equal(observation.fact_id, 'pad-open');
  assert.equal(observation.value, true);
  assert.equal(result.facts.find(item => item.field_id === 'pad-available').value, false);
});
