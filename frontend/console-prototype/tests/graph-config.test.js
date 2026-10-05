import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { DEFAULT_GRAPH, GRAPH_SCHEMA, GRAPH_COLLECTIONS, validateGraph, validateGraphSchema, graphNodes, graphEdges, graphNeighbors, inspectGraphNode, inspectDefinitionAST, commandResourceDemand } from '../src/graph-config.js';

const fresh = () => structuredClone(DEFAULT_GRAPH);
const codes = graph => validateGraph(graph).errors.map(issue => issue.code);
const edit = fn => { const graph = fresh(); fn(graph); return graph; };

test('checked-in JSON Schema is the exact browser-consumed versioned contract', async () => {
  assert.deepEqual(JSON.parse(await readFile(new URL('../src/schemas/unified-graph-v1.schema.json', import.meta.url))), GRAPH_SCHEMA);
  assert.equal(GRAPH_SCHEMA.$schema, 'https://json-schema.org/draft/2020-12/schema');
  assert.equal(validateGraphSchema(DEFAULT_GRAPH).valid, true);
  const result = validateGraph(DEFAULT_GRAPH);
  assert.equal(result.valid, true, JSON.stringify(result.errors));
  assert.deepEqual(result.readiness, { declared: true, static_checked: true, fixture_executed: false, real_connected: false });
  assert.ok(result.warnings.some(issue => issue.code === 'W_LEGAL_UNVERIFIED'));
});

test('all graph concepts use explicit type instances and independent multi-entity control', () => {
  assert.ok(GRAPH_COLLECTIONS.some(item => item.key === 'capabilities'));
  assert.ok(GRAPH_COLLECTIONS.some(item => item.key === 'behaviors'));
  assert.ok(graphNodes(DEFAULT_GRAPH).filter(item => !['node_types','expression_types'].includes(item.collection)).every(item => item.node.type_id));
  assert.deepEqual(DEFAULT_GRAPH.agents.find(agent => agent.id === 'fleet-dispatch').controls[0].entity_ids, ['uav-beta', 'vehicle-1']);
  assert.equal(DEFAULT_GRAPH.facts[0].record_kind, 'fixture_fact');
  assert.equal(DEFAULT_GRAPH.events[0].record_kind, 'event_definition');
  assert.equal(DEFAULT_GRAPH.commands[0].record_kind, 'command_definition');
});

test('invalid persisted drafts remain inspectable without throwing', () => {
  for (const graph of [null, {}, { modules: [null], facts: [null], fields: {}, feedback_policies: [null], rules: [null] }, edit(g => { g.modules[0].writes = null; }), edit(g => { g.commands[0].composition = null; })]) {
    assert.doesNotThrow(() => validateGraph(graph));
    assert.doesNotThrow(() => graphNodes(graph));
    assert.doesNotThrow(() => graphEdges(graph));
    assert.doesNotThrow(() => graphNeighbors(graph, 'uav-alpha'));
    assert.equal(validateGraph(graph).valid, false);
  }
  const cyclic = fresh(); cyclic.modules[0].parameters.loop = cyclic;
  assert.equal(validateGraph(cyclic).valid, false);
});

test('schema rejects unknown versions, undeclared properties and forged real connection status', () => {
  assert.equal(validateGraph(edit(g => { g.schema_version = 'v999'; })).valid, false);
  assert.equal(validateGraph(edit(g => { g.modules[0].unknown = true; })).valid, false);
  const result = validateGraph(edit(g => { g.modules[0].binding.status = 'real_connected'; }));
  assert.equal(result.valid, false);
  assert.equal(result.readiness.real_connected, false);
});

test('unresolved entity/type references and duplicate IDs are exact errors', () => {
  assert.ok(codes(edit(g => { g.facts[0].entity_id = 'ghost'; })).includes('E_UNRESOLVED_REFERENCE'));
  assert.ok(codes(edit(g => { g.entities[0].type_id = 'ghost'; })).includes('E_TYPE_REFERENCE'));
  assert.ok(codes(edit(g => { g.modules[0].type_id = 'agent-type'; })).includes('E_TYPE_MISMATCH'));
  assert.ok(codes(edit(g => { g.entities[0].id = 'battery'; })).includes('E_DUPLICATE_ID'));
});

test('typed fact values, units, frames, time and producer write scopes are checked', () => {
  for (const [change, expected] of [
    [g => { g.facts[1].value = 'eighteen'; }, 'E_FACT_VALUE_TYPE'],
    [g => { g.facts[1].unit = 'kg'; }, 'E_UNIT_MISMATCH'],
    [g => { g.facts[0].frame = 'body'; }, 'E_FRAME_MISMATCH'],
    [g => { g.facts[2].available_time_ns = '0'; }, 'E_TIME_ORDER'],
    [g => { g.facts[2].valid_until_ns = '0'; }, 'E_TIME_ORDER'],
    [g => { g.facts[1].producer_module_id = 'mobility-module'; }, 'E_MISSING_STATE_PRODUCER'],
    [g => { g.fields[0].unit = 'unicorn'; }, 'E_UNKNOWN_UNIT'],
    [g => { g.modules[1].writes = []; }, 'E_MISSING_STATE_PRODUCER'],
  ]) assert.ok(codes(edit(change)).includes(expected), expected);
  assert.equal(validateGraph(edit(g => { g.facts[0].valid_time_ns = 0; })).valid, false);
});

test('fixture evidence cannot masquerade as legal evidence or vice versa', () => {
  assert.ok(codes(edit(g => { g.facts[0].source_id = 'legal-source'; })).includes('E_FACT_SOURCE_KIND'));
  assert.ok(codes(edit(g => { g.constraints[4].source_id = 'fixture-source'; })).includes('E_LEGAL_SOURCE_KIND'));
  const source = DEFAULT_GRAPH.sources.find(source => source.kind === 'legal_reference');
  for (const key of ['issuer', 'jurisdiction', 'applicable_entity_type_ids', 'activities', 'effective_from', 'effective_until', 'version', 'uri', 'clause', 'verification']) assert.ok(Object.hasOwn(source, key));
});

test('same-config scene links resolve expanded IDs without allocating expanded scene', () => {
  const graph = fresh(); graph.entities.forEach(entity => { entity.scene_entity_id = null; });
  graph.entities[0].scene_entity_id = 'fleet-999999';
  const scene = { entities: [{ id: 'fleet', count: 1000000, type: 'uav' }] };
  assert.equal(validateGraph(graph, scene).valid, true);
  graph.entities[0].scene_entity_id = 'fleet';
  assert.ok(validateGraph(graph, scene).errors.some(issue => issue.code === 'E_SCENE_ENTITY_REFERENCE'));
});

test('strategy inputs and outputs are matched to agent observations and decision/task contract', () => {
  assert.ok(codes(edit(g => { g.strategies[0].input_fields[0].unit = 'kg'; })).includes('E_STRATEGY_INPUT_MISMATCH'));
  assert.ok(codes(edit(g => { g.agents[0].observable_fact_ids = []; })).includes('E_STRATEGY_INPUT_MISMATCH'));
  assert.ok(codes(edit(g => { g.strategies[0].output_command_ids = []; })).includes('E_STRATEGY_OUTPUT_MISMATCH'));
  assert.ok(codes(edit(g => { g.decisions[0].agent_id = 'fleet-dispatch'; })).includes('E_STRATEGY_OUTPUT_MISMATCH'));
});

test('overlapping exclusive scopes require an applicable shared arbitration', () => {
  const graph = edit(g => { g.agents[1].controls[0].entity_ids.push('uav-alpha'); });
  assert.ok(codes(graph).includes('E_EXCLUSIVE_SCOPE_OVERLAP'));
  graph.arbitrations.push({ id: 'control-arbiter', type_id: 'arbitration-type', label: 'Explicit arbiter', agent_ids: ['alpha-autonomy', 'fleet-dispatch'], entity_ids: ['uav-alpha'], policy: 'priority', description: 'Authored priority policy.' });
  graph.agents.forEach(agent => { agent.controls[0].arbitration_id = 'control-arbiter'; });
  assert.equal(validateGraph(graph).valid, true);
});

test('resource caps, units and mandatory constraints are checked', () => {
  assert.ok(codes(edit(g => { g.tasks[0].resource_claims[0].amount = 2; })).includes('E_RESOURCE_OVERCOMMIT'));
  assert.ok(codes(edit(g => { g.tasks[0].resource_claims[0].unit = 'kg'; })).includes('E_RESOURCE_UNIT_MISMATCH'));
  assert.ok(codes(edit(g => { g.tasks[0].constraint_ids = []; })).includes('E_MISSING_CONSTRAINT'));
  assert.ok(codes(edit(g => { g.resources[0].constraint_ids = []; })).includes('E_MISSING_CONSTRAINT'));
  assert.ok(codes(edit(g => { g.behaviors[0].module_ids = []; })).includes('E_BEHAVIOR_BINDING'));
});

test('sequential behavior claims take peak demand; parallel claims aggregate', () => {
  const graph = fresh();
  graph.behaviors.push({ ...structuredClone(graph.behaviors.find(item => item.id === 'recharge')), id: 'recharge-again' });
  graph.commands[0].composition.behavior_ids.push('recharge-again');
  assert.equal(commandResourceDemand(graph, 'charge-alpha')['west-charge-slot'], 1);
  assert.equal(validateGraph(graph).valid, true);
  graph.commands[0].composition.mode = 'parallel';
  assert.equal(commandResourceDemand(graph, 'charge-alpha')['west-charge-slot'], 2);
  assert.ok(codes(graph).includes('E_RESOURCE_OVERCOMMIT'));
});

test('command and behavior scopes cannot exceed the controlling agent', () => {
  assert.ok(codes(edit(g => { g.commands[0].target_entity_ids = ['uav-beta']; })).includes('E_COMMAND_SCOPE'));
  assert.ok(codes(edit(g => { g.behaviors[3].entity_ids = ['uav-beta']; })).includes('E_BEHAVIOR_COMMAND_SCOPE'));
});

test('event references never substitute for commands', () => {
  assert.ok(codes(edit(g => { g.rules[0].command_ids = ['charge-requested']; })).includes('E_EVENT_COMMAND_CONFUSION'));
  assert.ok(codes(edit(g => { g.rules[0].event_ids = ['charge-alpha']; })).includes('E_EVENT_COMMAND_CONFUSION'));
});

test('directed multigraph preserves same-pair edges, roles, conditions and unique identities', () => {
  const edges = graphEdges(DEFAULT_GRAPH).filter(edge => edge.source === 'alpha-battery' && edge.target === 'alpha-strategy');
  assert.ok(edges.length >= 3);
  assert.equal(new Set(edges.map(edge => edge.id)).size, edges.length);
  assert.ok(edges.some(edge => edge.role === 'safety_observation' && edge.condition));
  const constraintEdges = graphEdges(DEFAULT_GRAPH).filter(edge => edge.source === 'airspace-policy' && edge.target === 'alpha-strategy' || edge.source === 'alpha-strategy' && edge.target === 'airspace-policy');
  assert.ok(constraintEdges.some(edge => edge.relation === 'INPUT_TO'));
  assert.ok(constraintEdges.some(edge => edge.relation === 'OUTPUT_MUST_SATISFY'));
  assert.ok(graphNeighbors(DEFAULT_GRAPH, 'alpha-strategy').incoming.length);
  assert.equal(inspectGraphNode(DEFAULT_GRAPH, 'missing'), null);
});

test('authored edge endpoint direction, role, reference and ID are validated', () => {
  assert.ok(codes(edit(g => { g.edges[0].source = 'alpha-strategy'; g.edges[0].target = 'alpha-battery'; })).includes('E_EDGE_ENDPOINT_TYPE'));
  assert.ok(codes(edit(g => { g.edges[0].role = 'command'; })).includes('E_EDGE_ROLE'));
  assert.ok(codes(edit(g => { g.edges[0].target = 'missing'; })).includes('E_EDGE_REFERENCE'));
  assert.ok(codes(edit(g => { g.edges[1].id = g.edges[0].id; })).includes('E_DUPLICATE_EDGE'));
});

test('bounded admission requires correctly typed conditions and scoped hard constraints', () => {
  assert.ok(codes(edit(g => { g.constraints.find(c => c.id === 'pad-availability').condition.expected = 1; })).includes('E_ADMISSION_TYPE'));
  assert.ok(codes(edit(g => { g.admission_checks[0].constraint_id = 'airspace-policy'; })).includes('E_LEGAL_ADMISSION'));
  assert.equal(DEFAULT_GRAPH.objectives.every(objective => objective.preference === 'soft'), true);
  assert.equal(DEFAULT_GRAPH.constraints.every(constraint => constraint.enforcement === 'hard'), true);
});

test('AST inspector preserves ordered branches, requirements and target dependencies separately', () => {
  const state = inspectDefinitionAST(DEFAULT_GRAPH, 'energy-link-definition');
  assert.deepEqual(state.requirements, ['battery', 'link-quality']);
  assert.deepEqual(state.dependencies, []);
  assert.equal(state.errors.length, 0);
  assert.ok(state.edges.some(edge => edge.relation === 'ARGUMENT' && edge.role === '0'));
  const composite = inspectDefinitionAST(DEFAULT_GRAPH, 'return-needed-definition');
  assert.deepEqual(composite.requirements, []);
  assert.deepEqual(composite.dependencies, ['low-battery', 'link-degraded']);
  assert.equal(composite.native_truth, 'UNKNOWN');
});

test('AST scalar roots, applicability and named controls are independent definition branches', () => {
  const graph = fresh(), rule = graph.rules.find(rule => rule.id === 'low-battery-definition');
  rule.definition_ast = { state: 'battery' };
  rule.applicability_ast = { op: 'hold', args: [{ const: true }], window_s: 5, duration_seconds: { const: 3, unit: 's' }, max_gap_s: 1, scope: [{ state: 'battery' }] };
  const report = inspectDefinitionAST(graph, rule.id);
  assert.equal(report.errors.length, 0);
  assert.ok(report.roots.definition_ast && report.roots.applicability_ast);
  assert.equal(report.edges.filter(edge => edge.relation === 'CONTROL_INPUT').length, 4);
  assert.equal(report.edges.find(edge => edge.role === 'duration_seconds').status, 'shadowed_alias');
  assert.equal(report.scope_status, 'UNKNOWN');
});

test('AST rejects unit/type/arity/window/unresolved reference/unsupported operator errors', () => {
  for (const [ast, expected] of [
    [{ op: 'lt', args: [{ state: 'battery' }, { const: 10, unit: 'm' }] }, 'E_AST_UNIT'],
    [{ op: 'and', args: [{ state: 'battery' }, { const: true }] }, 'E_AST_TYPE'],
    [{ op: 'not', args: [] }, 'E_AST_ARITY'],
    [{ op: 'eq', args: [1, 1], window_s: -1 }, 'E_AST_WINDOW'],
    [{ state: 'missing' }, 'E_AST_REFERENCE'],
    [{ op: 'private-native-op', args: [1, 2] }, 'E_AST_OPERATOR_UNSUPPORTED'],
  ]) {
    const graph = fresh(); graph.rules.find(rule => rule.id === 'low-battery-definition').definition_ast = ast;
    assert.ok(codes(graph).includes(expected), expected);
  }
});

test('AST predicate reference cycles fail and graph JSON roundtrips unchanged', () => {
  const graph = fresh(); graph.rules.find(rule => rule.id === 'low-battery-definition').definition_ast = { predicate: 'return-needed' };
  assert.ok(codes(graph).includes('E_PREDICATE_CYCLE'));
  const roundtrip = JSON.parse(JSON.stringify(DEFAULT_GRAPH));
  assert.deepEqual(roundtrip, DEFAULT_GRAPH);
  assert.deepEqual(graphEdges(roundtrip), graphEdges(DEFAULT_GRAPH));
});

test('all derived default relations have typed direction contracts and honest scope/provenance', async () => {
  const { GRAPH_EDGE_CONTRACTS } = await import('../src/graph-config.js');
  const nodes = new Map(graphNodes(DEFAULT_GRAPH).map(node => [node.id, node]));
  for (const edge of graphEdges(DEFAULT_GRAPH)) {
    const contract = GRAPH_EDGE_CONTRACTS[edge.relation];
    assert.ok(contract, edge.relation);
    assert.ok(contract.from === '*' || contract.from.includes(nodes.get(edge.source).collection), edge.relation);
    assert.ok(contract.to.includes(nodes.get(edge.target).collection), edge.relation);
  }
  const edges = graphEdges(DEFAULT_GRAPH);
  const update = edges.find(edge => edge.source === 'network-module' && edge.target === 'link-quality' && edge.relation === 'UPDATES');
  assert.deepEqual(update.scope, ['uav-alpha', 'uav-beta']);
  assert.equal(update.source_id, null);
  assert.ok(edges.some(edge => edge.source === 'network-module' && edge.target === 'uav-alpha' && edge.relation === 'WRITE_SCOPE'));
  assert.ok(edges.some(edge => edge.source === 'uav-beta' && edge.target === 'battery' && edge.relation === 'OWNS'));
  assert.ok(edges.some(edge => edge.source === 'low-battery' && edge.target === 'low-battery-definition' && edge.relation === 'USES_RULE'));
  assert.equal(validateGraph(DEFAULT_GRAPH).warnings.some(issue => issue.code === 'W_UNMAPPED_REFERENCE'), false);
});

test('parameter environment type mismatch and behavior dependency cycles fail explicitly', () => {
  const graph = fresh(); graph.rules.find(rule => rule.id === 'low-battery-definition').definition_ast = { predicate: 'link-degraded', parameters: { 'reserve-threshold': { const: true } } };
  assert.ok(codes(graph).includes('E_AST_PARAMETER_TYPE'));
  const cycle = fresh(); cycle.behaviors[0].after_behavior_ids = ['land-home'];
  assert.ok(codes(cycle).includes('E_BEHAVIOR_CYCLE'));
});

test('conditional composition conservatively sums potential concurrent claims', () => {
  const graph = fresh();
  graph.behaviors.push({ ...structuredClone(graph.behaviors.find(item => item.id === 'recharge')), id: 'recharge-conditional' });
  graph.commands[0].composition = { mode: 'conditional', behavior_ids: ['recharge', 'recharge-conditional'], conditions: [{ behavior_id: 'recharge', predicate_id: 'low-battery', expected: 'TRUE' }, { behavior_id: 'recharge-conditional', predicate_id: 'link-degraded', expected: 'TRUE' }] };
  assert.equal(commandResourceDemand(graph, 'charge-alpha')['west-charge-slot'], 2);
  assert.ok(codes(graph).includes('E_RESOURCE_OVERCOMMIT'));
  assert.ok(codes(edit(g => { g.constraints.find(c => c.id === 'pad-availability').condition.expected_unit = 'kg'; })).includes('E_ADMISSION_UNIT'));
});

test('verified literal/unknown operator leaves are preserved and extra AST keys rejected', () => {
  for (const expression of [{ op: 'literal', value: 20, value_type: 'number', unit: '%' }, { op: 'unknown', reason: 'No observation' }]) {
    const graph = fresh(); graph.rules.find(rule => rule.id === 'low-battery-definition').definition_ast = expression;
    assert.equal(validateGraph(graph).valid, true);
    const ast = inspectDefinitionAST(graph, 'low-battery-definition');
    assert.equal(ast.nodes.length, 1);
    assert.equal(ast.errors.length, 0);
  }
  const graph = fresh(); graph.rules.find(rule => rule.id === 'low-battery-definition').definition_ast = { op: 'and', args: [true, false], undocumented_control: { state: 'battery' } };
  assert.ok(inspectDefinitionAST(graph, 'low-battery-definition').errors.some(issue => issue.code === 'E_AST_UNMAPPED_KEY'));
});

test('canonical graph includes readonly AST topology and collision-safe parameter paths', () => {
  const graph = fresh();
  graph.parameter_definitions.push(...['p.x','p-x'].map(id => ({ ...structuredClone(graph.parameter_definitions[0]), id })));
  graph.rules.find(rule => rule.id === 'low-battery-definition').definition_ast = { predicate: 'link-degraded', parameters: { 'p.x': { const: 1, unit: '%' }, 'p-x': { const: 2, unit: '%' } } };
  const ast = inspectDefinitionAST(graph, 'low-battery-definition');
  assert.equal(ast.errors.length, 0);
  assert.equal(new Set(ast.nodes.map(node => node.id)).size, ast.nodes.length);
  const nodes = graphNodes(graph), edges = graphEdges(graph);
  assert.equal(new Set(nodes.map(node => node.id)).size, nodes.length);
  assert.equal(new Set(edges.map(edge => edge.id)).size, edges.length);
  assert.ok(nodes.some(node => node.collection === 'expressions' && node.read_only && node.owner_rule_id === 'low-battery-definition'));
  assert.ok(edges.some(edge => edge.relation === 'BINDS_PARAMETER' && edge.role === 'p.x'));
  const constant = ast.nodes.find(node => node.expression?.const === 1);
  assert.ok(graphNeighbors(graph, constant.id).outgoing.some(edge => edge.relation === 'BINDS_PARAMETER'));
  assert.equal(validateGraph(graph).valid, true);
});

test('event Boolean definitions are separate from script occurrences and cross-kind cycles fail', () => {
  const graph = fresh();
  const event = { ...structuredClone(graph.events[0]), id: 'defined-event', trigger: null };
  graph.events.push(event);
  graph.rules.push({ ...structuredClone(graph.rules.find(rule => rule.id === 'low-battery-definition')), id: 'event-definition', kind: 'event_definition_rule', output_predicate_id: null, output_event_id: event.id, state_field_ids: [], parameter_ids: [], definition_ast: { predicate: 'low-battery' } });
  assert.equal(validateGraph(graph).valid, true);
  const ast = inspectDefinitionAST(graph, 'event-definition');
  assert.ok(graphEdges(graph).some(edge => edge.source === ast.roots.definition_ast && edge.target === event.id && edge.relation === 'DEFINES'));
  graph.rules.find(rule => rule.id === 'low-battery-definition').definition_ast = { event: event.id };
  assert.ok(codes(graph).includes('E_PREDICATE_CYCLE'));
  const unsupported = fresh(); unsupported.rules.find(rule => rule.id === 'low-battery-definition').definition_ast = { event: 'charge-requested' };
  assert.ok(codes(unsupported).includes('E_AST_EVENT_UNAVAILABLE'));
});

test('temporal controls follow per-operator syntax and preserve alias/edge semantics without evaluation', () => {
  const graph = fresh(), rule = graph.rules.find(rule => rule.id === 'low-battery-definition');
  rule.definition_ast = { op: 'eq', args: [true, false], window_s: 5 };
  assert.ok(codes(graph).includes('E_AST_CONTROL_UNSUPPORTED'));
  rule.definition_ast = { op: 'rise', args: [true], window_s: 5, duration_seconds: 2, max_gap_s: 1, scope: [{ state: 'battery' }] };
  const ast = inspectDefinitionAST(graph, rule.id);
  assert.equal(ast.errors.length, 0);
  assert.equal(ast.edges.find(edge => edge.role === 'window_s').status, 'parsed_not_active_window');
  assert.equal(ast.edges.find(edge => edge.role === 'duration_seconds').status, 'shadowed_alias');
  assert.ok(ast.edges.some(edge => edge.role === 'scope[0]'));
  assert.equal(ast.native_truth, 'UNKNOWN');
});

test('parallel timeline references retain their exact nested step timestamps', () => {
  const edges = graphEdges(DEFAULT_GRAPH).filter(edge => edge.source === 'constraint-change' && edge.target === 'pad-admission' && edge.relation === 'SCHEDULES_CHECK');
  assert.equal(edges.length, 2);
  assert.notEqual(edges[0].id, edges[1].id);
  assert.deepEqual(edges.map(edge => edge.time), ['1000000000','2000000000']);
});
