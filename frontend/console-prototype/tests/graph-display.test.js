import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_GRAPH, graphEdges } from '../src/graph-config.js';
import { RELATION_LAYERS, graphRelationLayers, graphDisplayEdges, expandGraphDisplayEdges, validateRelationLayerInventory } from '../src/graph-display.js';

test('every canonical relation retains exactly one audited primary review layer', () => {
  assert.deepEqual(validateRelationLayerInventory(), { valid: true, missing: [], unknown: [] });
  assert.deepEqual(RELATION_LAYERS.map(layer => layer.relations.length), [38, 27, 11, 8, 8]);
  assert.equal(graphRelationLayers(graphEdges(DEFAULT_GRAPH)).length, 5);
});
test('only three proven default PRODUCES mirrors bundle and exact source projection roundtrips', () => {
  const edges = graphEdges(DEFAULT_GRAPH), before = JSON.stringify(edges), display = graphDisplayEdges(DEFAULT_GRAPH, edges);
  assert.equal(display.filter(edge => edge.display_bundle).length, 3);
  assert.equal(display.length, edges.length - 3);
  assert.deepEqual(expandGraphDisplayEdges(display), edges);
  assert.equal(JSON.stringify(edges), before);
});
test('different times, scope, condition, version or provenance never bundle', () => {
  for (const key of ['time', 'scope', 'condition', 'version', 'source_id']) {
    const edges = graphEdges(DEFAULT_GRAPH);
    const target = edges.find(edge => edge.relation === 'PRODUCES' && edge.role === 'strategy_id');
    target[key] = key === 'scope' ? ['different-scope'] : 'different-context';
    const display = graphDisplayEdges(DEFAULT_GRAPH, edges);
    assert.equal(display.filter(edge => edge.display_bundle).length, 2, key);
    assert.deepEqual(expandGraphDisplayEdges(display), edges);
  }
});
test('parallel INPUT_TO roles and repeated scheduled checks remain separate', () => {
  const edges = graphEdges(DEFAULT_GRAPH), display = graphDisplayEdges(DEFAULT_GRAPH, edges);
  for (const relation of ['INPUT_TO', 'SCHEDULES_CHECK']) assert.equal(display.filter(edge => edge.relation === relation).length, edges.filter(edge => edge.relation === relation).length);
  const checks = edges.filter(edge => edge.relation === 'SCHEDULES_CHECK' && edge.source === 'constraint-change');
  assert.equal(checks.length, 2);
  assert.deepEqual(checks.map(edge => edge.time).sort(), ['1000000000', '2000000000']);
});
