import assert from 'node:assert/strict';
import test from 'node:test';
import { rendererCounterStability, matchedSceneCounterDrift } from './profile-city-frame.mjs';

const frame = { drawCalls: 10, triangles: 400, geometries: 3, textures: 2, programCount: 4 };
const summary = (repetition, overrides = {}, camera = 'overview', caseName = 'baseline') => ({
  camera, caseName, repetition,
  ...Object.fromEntries(Object.entries(frame).map(([key, value]) => [key, { p50: value }])), ...overrides,
});
test('accepts complete steady renderer counters', () => {
  assert.equal(rendererCounterStability([frame, { ...frame }]).status, 'stable');
});
test('rejects within-repetition geometry and draw-count drift', () => {
  const result = rendererCounterStability([frame, { ...frame, geometries: 4, drawCalls: 11 }]);
  assert.equal(result.status, 'drifted');
  assert.deepEqual(result.driftedFields, ['drawCalls', 'geometries']);
});
test('does not accept empty or incomplete renderer measurements', () => {
  assert.equal(rendererCounterStability([]).status, 'missing');
  const result = rendererCounterStability([frame, { ...frame, textures: undefined }]);
  assert.equal(result.status, 'missing');
  assert.deepEqual(result.missingFields, ['textures']);
});
test('compares repetitions only within the same camera and feature case', () => {
  assert.deepEqual(matchedSceneCounterDrift([summary(1), summary(2),
    summary(1, { drawCalls: { p50: 5 } }, 'overview', 'no-traffic')]), []);
});
test('rejects matched repetition drift and missing measurements', () => {
  const result = matchedSceneCounterDrift([summary(1), summary(2, { geometries: { p50: 4 }, textures: null })]);
  assert.deepEqual(result.map(row => row.field), ['geometries', 'textures']);
});
