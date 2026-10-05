import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_GRAPH, validateGraph, graphEdges, graphContractInventory } from '../src/graph-config.js';
import { resolveBehaviorPrerequisites, executeGraphFixture } from '../src/graph-runtime.js';

function fixture() {
  const graph = structuredClone(DEFAULT_GRAPH);
  graph.spatial_zones.push({ id: 'corridor-test', label: 'Fixture corridor', type_id: 'spatial-zone-type', kind: 'corridor', geometry: { frame: 'ENU', axis_order: 'east_north', unit: 'm', geometry_type: 'polyline', coordinates: [[0,0],[100,0]] }, altitude: { min_m: 10, max_m: 20 }, time_windows: [{ start_ns: '0', end_ns: '60000000000' }], direction: 'forward', access: 'approval_required', authority_agent_ids: ['fleet-dispatch'], capacity_slots: 1, source_id: 'fixture-source' });
  graph.spatial_zones.push({ id: 'no-fly-test', label: 'Fixture no-fly', type_id: 'spatial-zone-type', kind: 'no_fly_zone', geometry: { frame: 'ENU', axis_order: 'east_north', unit: 'm', geometry_type: 'polygon', coordinates: [[0,0],[1,0],[1,1],[0,0]] }, altitude: { min_m: 0, max_m: 100 }, time_windows: [{ start_ns: '0', end_ns: '60000000000' }], direction: 'bidirectional', access: 'prohibited', authority_agent_ids: ['fleet-dispatch'], capacity_slots: 0, source_id: 'fixture-source' });
  graph.lease_requests.push({ id: 'request-test', label: 'Fixture corridor request', type_id: 'lease-request-type', record_kind: 'fixture_lease_request', agent_id: 'alpha-autonomy', entity_ids: ['uav-alpha'], zone_id: 'corridor-test', requested_window: { start_ns: '16000000000', end_ns: '32000000000' }, requested_altitude: { min_m: 12, max_m: 18 }, direction: 'forward', issued_at_ns: '10000000000', purpose: 'Declared fixture traversal only', source_id: 'fixture-source' });
  graph.delivery_receipts.push({ id: 'receipt-test', label: 'Fixture authorization message receipt', type_id: 'delivery-receipt-type', record_kind: 'fixture_delivery_receipt', receipt_kind: 'message_delivery', command_id: 'return-alpha', task_id: 'return-task', sender_entity_id: 'edge-west', receiver_entity_id: 'uav-alpha', status: 'acknowledged', emitted_at_ns: '11000000000', received_at_ns: '12000000000', evidence_fact_ids: [], source_id: 'fixture-source' });
  graph.lease_approvals.push({ id: 'approval-test', label: 'Fixture corridor approval', type_id: 'lease-approval-type', record_kind: 'fixture_lease_approval', request_id: 'request-test', authority_agent_id: 'fleet-dispatch', decision: 'approved', approved_window: { start_ns: '16000000000', end_ns: '32000000000' }, approved_altitude: { min_m: 12, max_m: 18 }, constraint_ids: ['airspace-policy'], evidence_receipt_ids: ['receipt-test'], issued_at_ns: '13000000000', source_id: 'fixture-source' });
  graph.space_time_allocations.push({ id: 'allocation-test', label: 'Fixture corridor allocation', type_id: 'space-time-allocation-type', record_kind: 'fixture_space_time_allocation', approval_id: 'approval-test', zone_id: 'corridor-test', entity_ids: ['uav-alpha'], window: { start_ns: '16000000000', end_ns: '32000000000' }, altitude: { min_m: 12, max_m: 18 }, direction: 'forward', slots: 1, status: 'declared', source_id: 'fixture-source' });
  graph.behaviors.find(behavior => behavior.id === 'fly-home').required_allocation_ids = ['allocation-test'];
  graph.behaviors.find(behavior => behavior.id === 'fly-home').required_receipt_ids = ['receipt-test'];
  const scenario = graph.scenarios.find(scenario => scenario.id === 'comm-return');
  Object.assign(scenario, { lease_request_ids: ['request-test'], lease_approval_ids: ['approval-test'], allocation_ids: ['allocation-test'], delivery_receipt_ids: ['receipt-test'] });
  return graph;
}
const codes = graph => validateGraph(graph).errors.map(issue => issue.code);

test('typed spatial zones/leases/allocations/receipts validate with fixture-only boundaries', () => {
  const graph = fixture(), result = validateGraph(graph);
  assert.equal(result.valid, true, JSON.stringify(result.errors));
  assert.ok(result.warnings.some(issue => issue.code === 'W_SPATIAL_FIXTURE_ONLY'));
  const edges = graphEdges(graph);
  for (const relation of ['MANAGED_BY','REQUESTS_ZONE','DECIDES_REQUEST','APPROVED_BY','ALLOCATED_UNDER','ALLOCATES_ZONE','REQUIRES_ALLOCATION','REQUIRES_RECEIPT','RECEIPT_FOR','SENT_BY','RECEIVED_BY']) assert.ok(edges.some(edge => edge.relation === relation), relation);
});

test('static spatial geometry/time/direction/authority/type mistakes are caught', () => {
  for (const [modify, expected] of [
    [g => { g.spatial_zones[0].geometry.unit = 'deg'; }, 'E_SPATIAL_FRAME_UNIT'],
    [g => { g.spatial_zones[1].access = 'approval_required'; }, 'E_NO_FLY_ACCESS'],
    [g => { g.spatial_zones[1].geometry.coordinates.pop(); }, 'E_SPATIAL_POLYGON'],
    [g => { g.lease_approvals[0].authority_agent_id = 'alpha-autonomy'; }, 'E_LEASE_AUTHORITY'],
    [g => { g.lease_approvals[0].issued_at_ns = '0'; }, 'E_LEASE_TIME_ORDER'],
    [g => { g.space_time_allocations[0].direction = 'reverse'; }, 'E_ALLOCATION_DIRECTION'],
    [g => { g.space_time_allocations[0].window.end_ns = '33000000000'; }, 'E_ALLOCATION_ENVELOPE'],
    [g => { g.delivery_receipts[0].received_at_ns = '0'; }, 'E_RECEIPT_TIME'],
  ]) { const graph = fixture(); modify(graph); assert.ok(codes(graph).includes(expected), expected); }
});

test('pending/rejected approval cannot allocate and no-fly zone cannot be approved', () => {
  const graph = fixture(); graph.lease_approvals[0].decision = 'pending'; graph.lease_approvals[0].approved_window = null; graph.lease_approvals[0].approved_altitude = null;
  assert.ok(codes(graph).includes('E_ALLOCATION_APPROVAL'));
  const blocked = fixture(); blocked.lease_requests[0].zone_id = 'no-fly-test'; blocked.space_time_allocations[0].zone_id = 'no-fly-test';
  assert.ok(codes(blocked).includes('E_NO_FLY_ALLOCATION'));
});

test('half-open space-time allocations reject overlapping capacity without assuming geometric separation', () => {
  const graph = fixture(); graph.space_time_allocations.push({ ...structuredClone(graph.space_time_allocations[0]), id: 'allocation-overlap' });
  assert.ok(codes(graph).includes('E_SPACE_TIME_OVERCOMMIT'));
  graph.space_time_allocations[1].status = 'cancelled';
  assert.equal(validateGraph(graph).valid, true);
});

test('before/missing/expired allocation and unreceived messages fail closed before behavior admission', () => {
  const graph = fixture();
  assert.equal(resolveBehaviorPrerequisites(graph, 'fly-home', '15000000000').verdict, 'BLOCK');
  assert.equal(resolveBehaviorPrerequisites(graph, 'fly-home', '16000000000').allowed_in_fixture, true);
  assert.equal(resolveBehaviorPrerequisites(graph, 'fly-home', '32000000000').verdict, 'BLOCK');
  graph.delivery_receipts[0].received_at_ns = '20000000000';
  assert.equal(resolveBehaviorPrerequisites(graph, 'fly-home', '16000000000').verdict, 'UNKNOWN');
  graph.delivery_receipts = [];
  assert.equal(resolveBehaviorPrerequisites(graph, 'fly-home', '16000000000').allowed_in_fixture, false);
});

test('spatial fixture output preserves approval/receipt distinction and cannot claim actual delivery', () => {
  const result = executeGraphFixture(fixture(), 'comm-return', { at_ns: '16000000000' });
  assert.equal(result.status, 'fixture_executed');
  assert.equal(result.lease_approvals[0].actual_authority_verified, false);
  assert.equal(result.delivery_receipts[0].receipt_kind, 'message_delivery');
  assert.equal(result.delivery_receipts[0].actual_delivery_verified, false);
  assert.equal(result.delivery_receipts[0].task_success, 'UNKNOWN');
  assert.equal(result.space_time_allocations[0].actual_reservation, false);
  assert.equal(result.behaviors.find(behavior => behavior.id === 'fly-home').prerequisites.allowed_in_fixture, true);
});

test('canonical inventory covers authored and derived types/relations and explicit unsupported boundaries', () => {
  const inventory = graphContractInventory(fixture());
  assert.equal(inventory.inventory_scope, 'confirmed_local_contract_only');
  assert.ok(inventory.collections.some(collection => collection.key === 'spatial_zones'));
  assert.ok(inventory.collections.some(collection => collection.key === 'expressions'));
  assert.ok(inventory.relations.some(relation => relation.relation === 'ARGUMENT'));
  assert.ok(inventory.runtime.some(capability => capability.capability === 'spatial_collision_or_no_fly_intersection' && capability.support === 'unrepresentable'));
  assert.equal(inventory.coverage_policy.private_catalog_completeness_claim, false);
});

function chargingWithLease() {
  const graph = fixture();
  const behavior = graph.behaviors.find(behavior => behavior.id === 'recharge');
  behavior.required_allocation_ids = ['allocation-test'];
  behavior.required_receipt_ids = ['receipt-test'];
  // Keep pad fact available through targeted checks so only lease/receipt changes drive these cases.
  graph.facts.find(fact => fact.id === 'pad-open').valid_until_ns = '60000000000';
  graph.facts.find(fact => fact.id === 'pad-closed').value = true;
  const scenario = graph.scenarios.find(scenario => scenario.id === 'constraint-change');
  scenario.observed_at_ns = '33000000000';
  scenario.admission_timeline = [{ at_ns: '15000000000', check_ids: ['pad-admission'], event_ids: [], updated_fact_ids: [] }];
  return graph;
}

test('passing pad check cannot override a blocked window or unknown delivery receipt', () => {
  const graph = chargingWithLease();
  let result = executeGraphFixture(graph, 'constraint-change');
  assert.equal(result.check_results[0].verdict, 'PERMIT');
  assert.equal(result.behaviors[0].status, 'fixture_prerequisite_blocked');
  assert.equal(result.behaviors[0].admission.verdict, 'BLOCK');
  assert.equal(result.behaviors[0].admission.prerequisite_verdict, 'BLOCK');
  graph.scenarios.find(scenario => scenario.id === 'constraint-change').admission_timeline[0].at_ns = '16000000000';
  graph.delivery_receipts[0].status = 'UNKNOWN'; graph.delivery_receipts[0].received_at_ns = null;
  // Approval remains an independent authored decision; remove report-evidence use of this grant receipt.
  graph.lease_approvals[0].evidence_receipt_ids = [];
  result = executeGraphFixture(graph, 'constraint-change');
  assert.equal(result.check_results[0].verdict, 'PERMIT');
  assert.equal(result.behaviors[0].status, 'fixture_prerequisite_unknown');
  assert.equal(result.behaviors[0].admission.verdict, 'UNKNOWN');
});

test('targeted timeline checks re-resolve allocation expiry and never invent a pause policy', () => {
  const graph = chargingWithLease();
  graph.scenarios.find(scenario => scenario.id === 'constraint-change').admission_timeline = [
    { at_ns: '16000000000', check_ids: ['pad-admission'], event_ids: [], updated_fact_ids: [] },
    { at_ns: '32000000000', check_ids: ['pad-admission'], event_ids: [], updated_fact_ids: [] },
  ];
  const result = executeGraphFixture(graph, 'constraint-change');
  assert.deepEqual(result.check_results.map(record => record.verdict), ['PERMIT', 'PERMIT']);
  assert.equal(result.behaviors[0].prerequisites.at_ns, '32000000000');
  assert.equal(result.behaviors[0].prerequisites.checks[0].reason, 'expired_allocation');
  assert.equal(result.behaviors[0].admission.verdict, 'BLOCK');
  assert.equal(result.behaviors[0].status, 'fixture_prerequisite_blocked');
  assert.equal(result.feedback.length, 0);
  assert.equal(result.trace.filter(entry => entry.kind === 'behavior_admission_combined').length, 2);
});
