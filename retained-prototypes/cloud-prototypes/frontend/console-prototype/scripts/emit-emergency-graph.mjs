/** One config is authoritative. Everything emitted below is derived and disposable. */
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { validateConfig } from '../src/config.js';
import { safeImport, exportConfig, canonicalJSON } from '../src/runtime.js';
import { validateGraph, graphNodes, graphEdges, graphContractInventory, inspectDefinitionAST } from '../src/graph-config.js';
import { executeGraphFixture, resolveBehaviorPrerequisites } from '../src/graph-runtime.js';
import { graphDisplayEdges, graphRelationLayers } from '../src/graph-display.js';

export const EMERGENCY_CONFIG_PATH = fileURLToPath(new URL('../examples/emergency-delivery.config.json', import.meta.url));
const digest = value => createHash('sha256').update(canonicalJSON(value)).digest('hex');

export function projectEmergencyConfig(config) {
  const validation = { config: validateConfig(config), graph: validateGraph(config.graph, config) };
  if (!validation.config.valid || !validation.graph.valid) throw new Error(JSON.stringify(validation));
  const exported_config = exportConfig(config);
  const roundtrip = safeImport(JSON.stringify(exported_config));
  if (JSON.stringify(roundtrip) !== JSON.stringify(config)) throw new Error('Canonical full-config export/import changed data.');
  const graph = config.graph, nodes = graphNodes(graph), edges = graphEdges(graph), inventory = graphContractInventory(graph);
  const fixture_runs = graph.scenarios.map(scenario => executeGraphFixture(graph, scenario.id, { config }));
  const runtimeNodes = [], runtimeEdges = [];
  const makeInstance = (run, collection, value, index, definitionId, kind) => {
    const id = `runtime-${encodeURIComponent(JSON.stringify([run.scenario_id,collection,index,value.id || value.node_id || null]))}`;
    runtimeNodes.push({ id, kind, definition_id: definitionId, source_scenario_id: run.scenario_id, source_path: `fixture_runs[${fixture_runs.indexOf(run)}].${collection}[${index}]`, record: value, provenance: 'derived_fixture_result', actual_execution: 'not_established' });
    if (definitionId) runtimeEdges.push({ id: `${id}-definition`, source: id, target: definitionId, relation: 'INSTANCE_OF_DEFINITION', role: kind, source_path: runtimeNodes.at(-1).source_path, scope: 'runtime_artifact_reference' });
    return id;
  };
  for (const run of fixture_runs) {
    for (const [i, value] of run.decisions.entries()) makeInstance(run, 'decisions', value, i, value.id, 'authored_decision_resolution');
    for (const [i, value] of run.commands.entries()) makeInstance(run, 'commands', value, i, value.id, 'fixture_command_dispatch');
    for (const [i, value] of run.behaviors.entries()) makeInstance(run, 'behaviors', value, i, value.id, 'fixture_behavior_plan');
    for (const [i, value] of run.events.entries()) makeInstance(run, 'events', value, i, value.definition_id, 'fixture_event_occurrence');
    for (const [i, value] of run.check_results.entries()) makeInstance(run, 'check_results', value, i, value.check_id, 'fixture_admission_result');
    for (const [i, value] of run.predicate_results.entries()) makeInstance(run, 'predicate_results', value, i, value.predicate_id, 'unexecuted_predicate_result');
    for (const [i, value] of run.bindings.entries()) makeInstance(run, 'bindings', value, i, value.node_id, 'fixture_binding_resolution');
    for (const [i, value] of run.trace.entries()) if (value.kind === 'strategy_called') makeInstance(run, 'trace', value, i, value.node_id, 'authored_strategy_invocation');
  }
  const inventoryRows = [];
  const row = (category, key, matching, reason) => inventoryRows.push({ category, key, status: matching.length ? 'represented' : 'not_applicable', root_cause: matching.length ? null : 'not_applicable', canonical_ids: matching, explanation: matching.length ? 'Direct canonical graph/source projection; no renamed relation alias.' : reason });
  for (const collection of inventory.collections) row('collection', collection.key, nodes.filter(node => node.collection === collection.key).map(node => node.id), 'This scenario has no authored instance of this optional contract collection; capability remains implemented and inventoried.');
  for (const type of inventory.node_types) row('node_type', type.id, nodes.filter(node => node.node.type_id === type.id).map(node => node.id), 'Type is declared in the schema registry but no instance is needed in this scenario.');
  for (const type of inventory.entity_types) row('entity_type', type.id, nodes.filter(node => node.collection === 'entities' && node.node.type_id === type.id).map(node => node.id), 'No entity of this type is involved in the scenario.');
  for (const relation of inventory.relations) row('relation', relation.relation, edges.filter(edge => edge.relation === relation.relation).map(edge => edge.id), 'No authored slot in this scenario uses this supported relation. This is absence of a scenario instance, not an implementation failure.');
  for (const operator of inventory.definition_operators) row('definition_operator', operator.operator, nodes.filter(node => node.collection === 'expressions' && node.node.expression?.op === operator.operator).map(node => node.id), 'The three authored decision-definition trees do not require this implemented inspection operator.');
  for (const capability of inventory.runtime) inventoryRows.push({ category: 'runtime_capability', key: capability.capability, status: capability.support === 'supported' ? 'represented' : 'unrepresentable', root_cause: capability.support === 'supported' ? null : 'implementation_missing', canonical_ids: [], explanation: capability.reason || 'Used by canonical validation or fixture resolution, not native simulation.' });
  const perception = graph.modules.find(module => module.id.endsWith('.perception'));
  const mapping_diagnostics = perception?.parameters.raw_sensor_input_mapping ? [{ category: 'scenario_input_mapping', key: 'raw_sensor_to_perception', status: 'unrepresentable', root_cause: 'schema_mapping_gap', canonical_ids: [perception.id], source_path: `graph.modules[${graph.modules.indexOf(perception)}].parameters.raw_sensor_input_mapping`, explanation: perception.parameters.raw_sensor_input_mapping.explanation }] : [];
  inventoryRows.push(...mapping_diagnostics);
  const business = graph.modules.find(module => module.id.endsWith('.business'));
  const expected_effects = Object.entries(business?.parameters?.authored_branch_expectations || {}).map(([branch, record]) => ({ branch, source_path: `graph.modules[${graph.modules.indexOf(business)}].parameters.authored_branch_expectations.${branch}`, ...record }));
  const prerequisite_probes = [
    ['before_window','demo.emergency_delivery.behavior.approach','15500000000'],
    ['in_window','demo.emergency_delivery.behavior.approach','16000000000'],
    ['expired','demo.emergency_delivery.behavior.approach','22000000000'],
    ['return_before_window','demo.emergency_delivery.behavior.return','43000000000'],
    ['return_in_window','demo.emergency_delivery.behavior.return','44000000000'],
    ['return_expired','demo.emergency_delivery.behavior.return','78000000000'],
  ].map(([name,behavior,time]) => ({ name, ...resolveBehaviorPrerequisites(graph,behavior,time) }));
  const counterfactual_probes = [];
  for (const variantName of ['missing_grant_receipt','rejected_approval']) {
    const variant = structuredClone(config), overrides = [];
    const change = (collection,id,key,value) => {
      const index = variant.graph[collection].findIndex(record => record.id === id), record = variant.graph[collection][index];
      overrides.push({ path: `/graph/${collection}/${index}/${key}`, record_id: id, before: structuredClone(record[key]), after: structuredClone(value) });
      record[key] = value;
    };
    if (variantName === 'missing_grant_receipt') {
      change('delivery_receipts','demo.emergency_delivery.grant_receipt','status','UNKNOWN');
      change('delivery_receipts','demo.emergency_delivery.grant_receipt','received_at_ns',null);
    } else {
      change('lease_approvals','demo.emergency_delivery.approval','decision','rejected');
      change('lease_approvals','demo.emergency_delivery.approval','approved_window',null);
      change('lease_approvals','demo.emergency_delivery.approval','approved_altitude',null);
      change('space_time_allocations','demo.emergency_delivery.allocation','status','cancelled');
    }
    counterfactual_probes.push({ name: variantName, record_kind: 'derived_counterfactual_test', authoritative: false, base_config_digest: digest(config), variant_config_digest: digest(variant), overrides, validation: validateConfig(variant), result: resolveBehaviorPrerequisites(variant.graph,'demo.emergency_delivery.behavior.approach','16000000000'), boundary: 'Read-only counterfactual test artifact. Overrides are explicit; canonical configuration is unchanged. A denied/cancelled allocation makes the unchanged proposed approach statically inadmissible.' });
  }
  return {
    schema_version: 'aeroagentsim.canonical-graph-projection/v1',
    authoritative_source: 'examples/emergency-delivery.config.json',
    config, graph, config_digest: digest(config), graph_digest: digest(graph), inventory_digest: digest(inventory),
    exported_config, validation, roundtrip: { valid: true, config_digest: digest(roundtrip) },
    nodes, edges, review_projection: { authoritative: false, layers: graphRelationLayers(edges), display_edges: graphDisplayEdges(graph, edges), boundary: 'Review layers preserve every canonical relation. Only proven reciprocal PRODUCES declarations are bundled; origin_edges retain the exact source records.' }, definition_asts: graph.rules.map(rule => ({ rule_id: rule.id, ...inspectDefinitionAST(graph,rule.id) })),
    fixture_runs, inventory, expected_effects, prerequisite_probes, counterfactual_probes, mapping_diagnostics,
    runtime_overlay: { schema_version: 'aeroagentsim.fixture-instance-overlay/v1', authoritative: false, source: 'fixture_runs', nodes: runtimeNodes, edges: runtimeEdges, relation_contracts: [{ relation: 'INSTANCE_OF_DEFINITION', from: 'derived_runtime_record', to: 'existing_canonical_definition', meaning: 'Identity/basis only; never native execution or physical causality.' }] },
    coverage: { relation_layers: graphRelationLayers(edges).map(layer => ({ id: layer.id, label: layer.label, label_zh: layer.label_zh, total_relation_types: layer.declared_type_count, represented_relation_types: layer.present_relations.length, rows: inventoryRows.filter(row => row.category === 'relation' && layer.relations.includes(row.key)), variants: layer.variants })), inventory_scope: inventory.inventory_scope, total_items: inventoryRows.length, accounted_items: inventoryRows.length, disposition_coverage_percent: 100, represented_items: inventoryRows.filter(row=>row.status==='represented').length, root_cause_categories: ['implementation_missing','schema_mapping_gap','not_applicable'], rows: inventoryRows, claim: '100% of the confirmed inventory is dispositioned. This does not mean every type/operator is instantiated, nor native runtime completeness.' },
    boundaries: ['One editable authoring configuration. All node/edge/AST/runtime views are derived.', 'Scenario decisions and expected effects are authored; no actual medical diagnosis, child imagery, upload, flight, lease authority or legal mandate.', 'Native semantic truth and actual feasibility/permission/suitability remain UNKNOWN.'],
  };
}

export async function loadEmergencyProjection(path = EMERGENCY_CONFIG_PATH) {
  return projectEmergencyConfig(safeImport(await readFile(path, 'utf8')));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const projection = await loadEmergencyProjection(process.argv[2] ? resolve(process.argv[2]) : EMERGENCY_CONFIG_PATH);
  process.stdout.write(`${JSON.stringify(projection,null,2)}\n`);
}
