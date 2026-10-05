import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const ledger = JSON.parse(await readFile(new URL('../verification/completeness-ledger.json', import.meta.url), 'utf8'));

test('completeness ledger pins eight source contracts and keeps independent evidence dimensions', () => {
  assert.equal(ledger.schema_version, 'aeroagentsim.completeness-ledger/v1');
  assert.equal(ledger.source_contracts.length, 8);
  assert.equal(new Set(ledger.source_contracts.map(source => source.name)).size, 8);
  assert.ok(ledger.source_contracts.every(source => /^[a-f0-9]{64}$/.test(source.sha256)));
  assert.deepEqual(ledger.evidence_dimensions, ['declared', 'implemented', 'tested', 'real_connected']);
  assert.deepEqual(Object.values(ledger.relation_review_layers), [38, 27, 11, 8, 8]);
});
test('every ledger issue has a concrete source, observed gap, bounded action and acceptance evidence', () => {
  assert.equal(new Set(ledger.items.map(item => item.id)).size, ledger.items.length);
  for (const item of ledger.items) {
    assert.ok(['P0', 'P1', 'P2'].includes(item.priority), item.id);
    assert.ok(['in_progress', 'deferred', 'partial', 'verified_fixture', 'blocked_source_access'].includes(item.status), item.id);
    for (const key of ['requirement', 'observed', 'bounded_action']) assert.ok(item[key]?.length > 15, `${item.id}.${key}`);
    assert.ok(item.sources.length && item.acceptance.length, item.id);
  }
});
test('unavailable catalog and real source gaps cannot become an invented completeness denominator', () => {
  assert.equal(ledger.catalog_boundary.status, 'blocked_source_access');
  assert.equal(ledger.catalog_boundary.catalog_denominator, null);
  assert.equal(ledger.catalog_boundary.independently_recounted, false);
  assert.equal(ledger.catalog_boundary.native_rule_parity, 'not_verified');
  assert.ok(ledger.items.some(item => item.id === 'NATIVE-001' && item.status === 'blocked_source_access'));
  assert.ok(ledger.items.some(item => item.id === 'VISUAL-001' && item.status === 'deferred'));
});
