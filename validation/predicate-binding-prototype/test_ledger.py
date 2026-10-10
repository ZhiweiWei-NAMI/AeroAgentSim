"""Executable fixture-only custody/resource acceptance cases, labelled by audit ID."""
from dataclasses import FrozenInstanceError, replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest

try:
    from .contracts import Ref, Interval
    from .ledger import (AuthorityToken, Quantity, Custody, TransferIntent, TransferEvidence,
                         Resource, Occupancy, OccupancySnapshot, ResourceRequest, BundleIntent,
                         FixtureLedger, LedgerError)
except ImportError:
    from contracts import Ref, Interval
    from ledger import (AuthorityToken, Quantity, Custody, TransferIntent, TransferEvidence,
                        Resource, Occupancy, OccupancySnapshot, ResourceRequest, BundleIntent,
                        FixtureLedger, LedgerError)


def ref(name, generation=1, epoch='run-epoch'):
    return Ref('fixture-run', epoch, name, generation)


def quantity(value, dimension='slots', unit='slot'):
    return (Quantity(dimension, value, unit),)


class LedgerFixture(unittest.TestCase):
    def setUp(self):
        self.ledger = FixtureLedger('fixture-authority', 8)
        self.token = self.ledger.authority
        self.parcel, self.source, self.target = map(ref, ('parcel', 'warehouse', 'ugv'))
        self.task = ref('delivery-task')
        self.ledger.initialize_custody(Custody(self.parcel, self.source, 4, ('seed-custody',)), token=self.token)

    def tx(self, name='tx1', target=None):
        return TransferIntent(name, self.parcel, self.source, target or self.target, self.task, 4)

    def evidence(self, intent):
        return tuple(TransferEvidence(intent.tx_id + ':' + kind, intent.tx_id, intent.parcel,
                                      intent.source, intent.target, intent.task, kind)
                     for kind in ('release', 'acquire'))

    def assert_code(self, code, call):
        with self.assertRaises(LedgerError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)

    def add_resource(self, name='pad', capacity=None, complete=True):
        resource = Resource(ref(name), capacity or quantity(1), 'policy-v1')
        self.ledger.register_resource(resource, token=self.token)
        if complete is not None:
            snapshot = OccupancySnapshot(resource.ref, (), Interval(0, 1000), complete, 1, ('coverage-1',))
            self.ledger.publish_occupancy_snapshot(snapshot, expected_revision=0, token=self.token)
        return resource.ref

    def request(self, resource, claim_id, amounts=None, start=10, end=20, lease=100):
        current = self.ledger.resource(resource)
        return ResourceRequest(claim_id, resource, amounts or quantity(1), Interval(start, end),
                               lease, current.revision, current.policy_revision)

    def bundle(self, name, requests, consumer=None, priority=0):
        return BundleIntent(name, self.task, consumer or self.target, tuple(requests), priority)

    def admit(self, intent, now=0):
        return self.ledger.reserve_bundle(intent, now_ns=now, token=self.token)

    def publish(self, resource, occupancies, complete=True, source_revision=2, valid=None):
        snapshot = OccupancySnapshot(resource, tuple(occupancies), valid or Interval(0, 1000),
                                     complete, source_revision, (f'coverage-{source_revision}',))
        current = self.ledger.resource(resource)
        return self.ledger.publish_occupancy_snapshot(snapshot, expected_revision=current.revision,
                                                      token=self.token)

    def test_ICS01_competing_handoffs_have_one_atomic_winner(self):
        first, second = self.tx(), self.tx('tx2', ref('uav'))
        for intent in (first, second):
            self.ledger.prepare_transfer(intent, token=self.token)
        barrier = Barrier(2)

        def commit(intent):
            barrier.wait()
            try:
                return self.ledger.commit_transfer(intent, self.evidence(intent), token=self.token).status
            except LedgerError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(commit, (first, second)))
        self.assertCountEqual(outcomes, ['committed', 'stale_precondition'])
        custody = self.ledger.custody(self.parcel)
        self.assertEqual(custody.revision, 5)
        self.assertIn(custody.holder, (first.target, second.target))

    def test_ICS02_receive_report_does_not_commit_custody(self):
        intent = self.tx()
        self.ledger.prepare_transfer(intent, token=self.token)
        self.ledger.mark_executing(intent, token=self.token)
        acquired = self.evidence(intent)[1]
        recorded = self.ledger.observe_transfer_evidence(intent, acquired, token=self.token)
        self.assertIn(acquired, recorded.evidence)
        self.assertEqual(recorded.status, 'executing')
        self.assertEqual(self.ledger.custody(self.parcel).holder, self.source)
        self.ledger.commit_transfer(intent, self.evidence(intent), token=self.token)
        self.assertEqual(self.ledger.custody(self.parcel).holder, self.target)

    def test_IC01_physical_contacts_overlap_independently(self):
        self.ledger.observe_contacts(self.parcel, (self.source, self.target), token=self.token)
        self.assertEqual(self.ledger.contacts(self.parcel), frozenset((self.source, self.target)))
        self.assertEqual(self.ledger.custody(self.parcel).holder, self.source)
        self.assertIsNone(self.ledger.contacts(ref('unobserved-parcel')))

    def test_IC01_unknown_custody_is_not_known_unassigned(self):
        other = ref('unassigned-parcel')
        self.assertIsNone(self.ledger.custody(other))
        record = Custody(other, None, 0, ('explicit-unassigned',))
        self.ledger.initialize_custody(record, token=self.token)
        self.assertEqual(self.ledger.custody(other), record)
        self.assert_code('custody_already_initialized', lambda: self.ledger.initialize_custody(record, token=self.token))

    def test_ICS01_exact_transaction_replay_and_payload_collision(self):
        intent = self.tx()
        prepared = self.ledger.prepare_transfer(intent, token=self.token)
        self.assertEqual(self.ledger.prepare_transfer(intent, token=self.token), prepared)
        changed = replace(intent, target=ref('ugv', generation=2))
        self.assert_code('transaction_payload_collision', lambda: self.ledger.prepare_transfer(changed, token=self.token))
        committed = self.ledger.commit_transfer(intent, self.evidence(intent), token=self.token)
        self.assertEqual(self.ledger.commit_transfer(intent, self.evidence(intent), token=self.token), committed)
        self.assertEqual(self.ledger.custody(self.parcel).revision, 5)
        self.assert_code('transaction_payload_collision', lambda: self.ledger.commit_transfer(changed, self.evidence(changed), token=self.token))

    def test_ICS02_transfer_evidence_is_exact_endpoint_task_and_generation_bound(self):
        intent = self.tx()
        self.ledger.prepare_transfer(intent, token=self.token)
        release, acquire = self.evidence(intent)
        for field, wrong in [('source', self.target), ('target', self.source),
                             ('target', ref('ugv', 2)), ('parcel', ref('parcel', 2)),
                             ('task', ref('other-task')), ('tx_id', 'other-tx')]:
            with self.subTest(field=field, wrong=wrong):
                bad = replace(acquire, **{field: wrong})
                self.assert_code('evidence_binding_mismatch', lambda: self.ledger.commit_transfer(intent, (release, bad), token=self.token))
        self.assert_code('required_evidence_missing', lambda: self.ledger.commit_transfer(intent, (acquire,), token=self.token))
        self.assertEqual(self.ledger.custody(self.parcel).revision, 4)

    def test_ICS09_same_event_identity_altered_payload_is_rejected(self):
        intent = self.tx()
        self.ledger.prepare_transfer(intent, token=self.token)
        acquire = self.evidence(intent)[1]
        self.ledger.observe_transfer_evidence(intent, acquire, token=self.token)
        altered = replace(acquire, kind='release')
        self.assert_code('evidence_payload_collision', lambda: self.ledger.observe_transfer_evidence(intent, altered, token=self.token))
        self.assertEqual(self.ledger.transfer_status(intent.tx_id).evidence, (acquire,))

    def test_ICS06_lost_ack_is_indeterminate_same_transaction_reconciles(self):
        intent = self.tx()
        self.ledger.prepare_transfer(intent, token=self.token)
        self.ledger.mark_executing(intent, token=self.token)
        pending = self.ledger.mark_execution_indeterminate(intent, token=self.token)
        self.assertEqual(pending.status, 'execution_indeterminate')
        self.assertEqual(self.ledger.custody(self.parcel).holder, self.source)
        self.assert_code('reconciliation_required', lambda: self.ledger.mark_executing(intent, token=self.token))
        self.assert_code('reconciliation_required', lambda: self.ledger.commit_transfer(intent, self.evidence(intent), token=self.token))
        self.assert_code('reconciliation_required', lambda: self.ledger.prepare_transfer(self.tx('fresh-retry'), token=self.token))
        reconciled = self.ledger.reconcile_transfer(intent, self.evidence(intent), token=self.token)
        self.assertEqual(reconciled.status, 'committed')
        self.assertEqual(self.ledger.transfer_status(intent.tx_id), reconciled)
        self.assertEqual(self.ledger.mark_execution_indeterminate(intent, token=self.token), reconciled)
        self.assertFalse(hasattr(self.ledger, 'execute_command'))
        self.assertFalse(hasattr(self.ledger, 'rollback'))

    def test_ICS06_indeterminate_transfer_fences_prepared_competitor(self):
        first, second = self.tx(), self.tx('tx2', ref('uav'))
        self.ledger.prepare_transfer(first, token=self.token)
        self.ledger.prepare_transfer(second, token=self.token)
        self.ledger.mark_execution_indeterminate(first, token=self.token)
        self.assert_code('reconciliation_required', lambda: self.ledger.commit_transfer(second, self.evidence(second), token=self.token))

    def test_ICS08_stale_authority_rejected_even_at_correct_revision(self):
        resource = self.add_resource()
        intent = self.bundle('stale-writer', (self.request(resource, 'claim'),))
        old = AuthorityToken('fixture-authority', 7)
        self.assert_code('stale_fencing_epoch', lambda: self.ledger.reserve_bundle(intent, now_ns=0, token=old))
        trace = self.ledger.audit[-1]
        self.assertEqual(trace.claimed_token, old)
        self.assertEqual(trace.outcome, 'stale_fencing_epoch')
        self.assertEqual(self.ledger.claims, ())
        self.assert_code('stale_fencing_epoch', lambda: self.ledger.prepare_transfer(self.tx(), token=old))

    def test_ICS08_epoch_rotation_retains_obligations_and_fences_old_writer(self):
        resource = self.add_resource()
        self.assertTrue(self.admit(self.bundle('first', (self.request(resource, 'first-claim'),))).admitted)
        old = self.token
        self.token = self.ledger.rotate_authority(9, token=old)
        second = self.bundle('second', (self.request(resource, 'second-claim'),))
        self.assert_code('stale_fencing_epoch', lambda: self.ledger.reserve_bundle(second, now_ns=0, token=old))
        self.assertEqual(self.admit(second).reason, 'capacity_exceeded')
        self.assertEqual(len(self.ledger.claims), 1)

    def test_ICS07_missing_or_incomplete_coverage_never_means_vacancy(self):
        for complete in (None, False):
            with self.subTest(complete=complete):
                resource = self.add_resource('pad-' + str(complete), complete=complete)
                result = self.admit(self.bundle(str(complete), (self.request(resource, 'claim-' + str(complete)),)))
                self.assertFalse(result.admitted)
                self.assertEqual(result.reason, 'scope_incomplete')
        known = self.add_resource('known-empty')
        self.assertTrue(self.admit(self.bundle('known', (self.request(known, 'known-claim'),))).admitted)

    def test_ICS07_complete_coverage_must_cover_entire_request_interval(self):
        resource = self.add_resource()
        self.publish(resource, (), valid=Interval(10, 15))
        rejected = self.admit(self.bundle('long', (self.request(resource, 'long-claim', end=16),)))
        self.assertEqual(rejected.reason, 'scope_incomplete')
        self.assertTrue(self.admit(self.bundle('covered', (self.request(resource, 'covered-claim', end=15),))).admitted)

    def test_ICS03_higher_priority_cannot_erase_observed_occupancy(self):
        resource = self.add_resource()
        occupied = Occupancy('delivery-uav', resource, self.target, quantity(1), Interval(10), ('sensor-1',))
        self.publish(resource, (occupied,))
        result = self.admit(self.bundle('rescue', (self.request(resource, 'rescue', start=12, end=18),), ref('rescue-uav'), priority=100))
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason, 'capacity_exceeded')
        self.assertIn('occupancy:delivery-uav', result.witness[0].contributor_ids)
        self.assertEqual(self.ledger.snapshot_history[-1].occupancies, (occupied,))

    def test_ICS04_reservation_expiry_does_not_remove_observed_occupancy(self):
        resource = self.add_resource()
        first = self.bundle('first', (self.request(resource, 'first-claim', lease=15),))
        self.assertTrue(self.admit(first).admitted)
        occupied = Occupancy('still-present', resource, self.target, quantity(1), Interval(10), ('sensor-16',), 'first-claim')
        self.publish(resource, (occupied,))
        next_intent = self.bundle('next', (self.request(resource, 'next-claim', start=16, end=18),), ref('uav2'))
        result = self.admit(next_intent, now=16)
        self.assertEqual(result.reason, 'capacity_exceeded')
        self.assertIn('occupancy:still-present', result.witness[0].contributor_ids)
        self.assertNotIn('claim:first-claim', result.witness[0].contributor_ids)
        self.assertEqual(len(self.ledger.claims), 1)

    def test_ICS04_matching_occupancy_and_claim_count_once(self):
        resource = self.add_resource(capacity=quantity(2))
        first = self.bundle('first', (self.request(resource, 'first-claim'),))
        self.assertTrue(self.admit(first).admitted)
        occupied = Occupancy('occupied', resource, self.target, quantity(1), Interval(10, 20), ('sensor',), 'first-claim')
        self.publish(resource, (occupied,))
        next_intent = self.bundle('next', (self.request(resource, 'next-claim'),), ref('uav2'))
        self.assertTrue(self.admit(next_intent).admitted)

    def test_ICS04_false_claim_link_cannot_hide_different_actor_generation(self):
        resource = self.add_resource(capacity=quantity(2))
        self.admit(self.bundle('first', (self.request(resource, 'first-claim'),)))
        occupied = Occupancy('other-generation', resource, ref('ugv', 2), quantity(1), Interval(10, 20), ('sensor',), 'first-claim')
        self.publish(resource, (occupied,))
        self.assertEqual(self.admit(self.bundle('next', (self.request(resource, 'next-claim'),))).reason, 'capacity_exceeded')

    def test_ICS04_occupancy_larger_than_claim_uses_actual_amount(self):
        resource = self.add_resource(capacity=quantity(3))
        self.admit(self.bundle('first', (self.request(resource, 'first-claim'),)))
        occupied = Occupancy('larger', resource, self.target, quantity(3), Interval(10, 20), ('sensor',), 'first-claim')
        self.publish(resource, (occupied,))
        result = self.admit(self.bundle('next', (self.request(resource, 'next-claim'),)))
        self.assertEqual(result.reason, 'capacity_exceeded')
        self.assertEqual(result.witness[0].total, 4)

    def test_ICS05_cross_resource_bundle_is_atomic_and_version_conditioned(self):
        cpu = self.add_resource('cpu', quantity(8, 'cpu', 'core'))
        radio = self.add_resource('radio', quantity(8_000_000, 'rate', 'bit/s'))
        def bundle(name):
            return self.bundle(name, (self.request(cpu, name + '-cpu', quantity(6, 'cpu', 'core')),
                                      self.request(radio, name + '-radio', quantity(6_000_000, 'rate', 'bit/s'))))
        first, second = bundle('landing'), bundle('rescue')
        revisions = ((cpu, 1), (radio, 1))
        admitted = self.admit(first)
        self.assertTrue(admitted.admitted)
        self.assertEqual(admitted.resource_revisions, revisions)
        self.assertEqual(self.admit(second).reason, 'stale_precondition')
        fresh_second = bundle('rescue-reread')
        self.assertEqual(self.admit(fresh_second).reason, 'capacity_exceeded')
        self.assertEqual({c.bundle_id for c in self.ledger.claims}, {'landing'})
        self.assertEqual(len(self.ledger.claims), 2)

    def test_ICS05_failure_on_second_resource_leaves_no_partial_claim(self):
        cpu = self.add_resource('cpu', quantity(8, 'cpu', 'core'))
        radio = self.add_resource('radio', quantity(5, 'rate', 'bit/s'))
        intent = self.bundle('too-large', (self.request(cpu, 'cpu-claim', quantity(6, 'cpu', 'core')),
                                          self.request(radio, 'radio-claim', quantity(6, 'rate', 'bit/s'))))
        self.assertEqual(self.admit(intent).reason, 'capacity_exceeded')
        self.assertEqual(self.ledger.claims, ())
        self.assertEqual(self.ledger.resource(cpu).revision, 1)
        self.assertEqual(self.ledger.resource(radio).revision, 1)

    def test_ICS05_concurrent_bundles_do_not_write_skew(self):
        first = self.add_resource('first', quantity(8))
        second = self.add_resource('second', quantity(8))
        requests = [self.bundle(name, (self.request(first, name + '-1', quantity(6)),
                                      self.request(second, name + '-2', quantity(6)))) for name in ('a', 'b')]
        barrier = Barrier(2)
        def reserve(intent):
            barrier.wait()
            return self.admit(intent)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, requests))
        self.assertEqual(sum(r.admitted for r in results), 1)
        self.assertEqual(len(self.ledger.claims), 2)
        self.assertEqual(len({c.bundle_id for c in self.ledger.claims}), 1)

    def test_IC03_half_open_sweep_does_not_sum_disjoint_claims(self):
        resource = self.add_resource(capacity=quantity(10))
        self.assertTrue(self.admit(self.bundle('a', (self.request(resource, 'a', quantity(6), 10, 15),))).admitted)
        self.assertTrue(self.admit(self.bundle('b', (self.request(resource, 'b', quantity(6), 15, 20),))).admitted)
        # The spanning request overlaps each independently: max load 6+4, not 6+6+4.
        self.assertTrue(self.admit(self.bundle('c', (self.request(resource, 'c', quantity(4), 10, 20),))).admitted)
        blocked = self.admit(self.bundle('d', (self.request(resource, 'd', quantity(1), 14, 16),)))
        self.assertEqual(blocked.reason, 'capacity_exceeded')
        self.assertEqual(tuple(v.interval for v in blocked.witness), (Interval(14, 15), Interval(15, 16)))

    def test_IC08_capacity_vector_units_and_policy_must_agree(self):
        resource = self.add_resource('compute', (Quantity('cpu', 8, 'core'), Quantity('ram', 16, 'GB')))
        base = self.request(resource, 'claim', (Quantity('cpu', 6, 'core'), Quantity('ram', 4, 'GB')))
        for name, amounts in [('missing', quantity(6, 'cpu', 'core')),
                              ('wrong-unit', (Quantity('cpu', 6, 'core'), Quantity('ram', 4, 'MB')))]:
            result = self.admit(self.bundle(name, (replace(base, amounts=amounts),)))
            self.assertEqual(result.reason, 'vector_unit_mismatch')
        wrong_policy = replace(base, policy_revision='other-policy')
        self.assertEqual(self.admit(self.bundle('policy', (wrong_policy,))).reason, 'policy_revision_mismatch')
        self.assertTrue(self.admit(self.bundle('good', (base,))).admitted)

    def test_IC06_each_capacity_dimension_is_checked(self):
        resource = self.add_resource('compute', (Quantity('cpu', 8, 'core'), Quantity('ram', 4, 'GB')))
        request = self.request(resource, 'claim', (Quantity('cpu', 1, 'core'), Quantity('ram', 5, 'GB')))
        result = self.admit(self.bundle('ram-over', (request,)))
        self.assertEqual(result.reason, 'capacity_exceeded')
        self.assertEqual(result.witness[0].dimension, 'ram')

    def test_IC06_capacity_shrink_preserves_claims_and_reports_overcommit(self):
        resource = self.add_resource(capacity=quantity(8))
        self.admit(self.bundle('first', (self.request(resource, 'first-claim', quantity(6)),)))
        before = self.ledger.resource(resource)
        self.ledger.update_capacity(resource, quantity(4), expected_revision=before.revision,
                                    expected_policy_revision='policy-v1', new_policy_revision='policy-v2', token=self.token)
        violations = self.ledger.capacity_conflicts(resource, Interval(10, 20), now_ns=0)
        self.assertEqual(len(self.ledger.claims), 1)
        self.assertEqual(violations[0].total, 6)
        self.assertEqual(violations[0].capacity, 4)

    def test_ICS07_incomplete_snapshot_cannot_erase_known_positive_observation(self):
        resource = self.add_resource()
        occupied = Occupancy('known', resource, self.target, quantity(2), Interval(10, 20), ('sensor',))
        self.publish(resource, (occupied,))
        self.publish(resource, (), complete=False, source_revision=3)
        self.assertEqual(self.admit(self.bundle('request', (self.request(resource, 'claim'),))).reason, 'scope_incomplete')
        witness = self.ledger.capacity_conflicts(resource, Interval(10, 20), now_ns=0)
        self.assertEqual(witness[0].total, 2)

    def test_ICS09_same_occupancy_revision_changed_payload_rejected(self):
        resource = self.add_resource()
        snapshot = OccupancySnapshot(resource, (), Interval(0, 1000), True, 1, ('coverage-1',))
        unchanged = self.ledger.publish_occupancy_snapshot(snapshot, expected_revision=0, token=self.token)
        self.assertEqual(unchanged.revision, 1)
        bad = replace(snapshot, complete=False)
        self.assert_code('occupancy_revision_conflict', lambda: self.ledger.publish_occupancy_snapshot(bad, expected_revision=1, token=self.token))
        self.assertEqual(len(self.ledger.snapshot_history), 1)

    def test_IC03_bundle_replay_is_historical_not_lease_renewal(self):
        resource = self.add_resource()
        intent = self.bundle('first', (self.request(resource, 'first-claim', lease=15),))
        result = self.admit(intent)
        self.assertEqual(self.admit(intent, now=30), result)
        self.assertEqual(self.ledger.claims[0].request.lease_end_ns, 15)
        self.assert_code('bundle_payload_collision', lambda: self.admit(replace(intent, priority=100)))
        self.assertEqual(len(self.ledger.claims), 1)

    def test_IC08_invalid_quantities_and_intervals_fail_closed(self):
        for value in (-1, float('nan'), float('inf'), -float('inf'), True, '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Quantity('slots', value, 'slot')
        with self.assertRaises(ValueError):
            Resource(ref('invalid'), (Quantity('x', 1, 'x'), Quantity('x', 2, 'x')), 'p')
        with self.assertRaises(ValueError):
            Interval(10, 10)
        with self.assertRaises(ValueError):
            TransferIntent('cross-epoch', self.parcel, self.source, ref('target', epoch='other'), self.task, 4)
        with self.assertRaises(FrozenInstanceError):
            self.token.epoch = 1

    def test_IC06_large_integer_capacity_does_not_round_down_overload(self):
        capacity = 2 ** 60
        resource = self.add_resource(capacity=quantity(capacity))
        request = self.request(resource, 'too-large', quantity(capacity + 1))
        result = self.admit(self.bundle('rounded-overload', (request,)))
        self.assertEqual(result.reason, 'capacity_exceeded')
        self.assertEqual(result.witness[0].total, capacity + 1)
        self.assertEqual(self.ledger.claims, ())

    def test_ICS01_self_custody_and_parcel_transfer_endpoints_are_invalid(self):
        with self.assertRaises(ValueError):
            Custody(self.parcel, self.parcel, 0, ('invalid-self-custody',))
        for field in ('source', 'target'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(self.tx(), **{field: self.parcel})

    def test_ICS01_ledger_endpoints_resources_and_tasks_require_object_refs(self):
        relation = replace(ref('relation'), ref_type='relation')
        for field in ('parcel', 'source', 'target', 'task'):
            with self.subTest(contract='TransferIntent', field=field), self.assertRaises(ValueError):
                replace(self.tx(), **{field: relation})
        for field in ('parcel', 'holder'):
            with self.subTest(contract='Custody', field=field), self.assertRaises(ValueError):
                replace(self.ledger.custody(self.parcel), **{field: relation})
        with self.assertRaises(ValueError):
            Resource(relation, quantity(1), 'policy')
        resource = self.add_resource()
        request = self.request(resource, 'claim')
        for field in ('task', 'consumer'):
            with self.subTest(contract='BundleIntent', field=field), self.assertRaises(ValueError):
                replace(self.bundle('bundle', (request,)), **{field: relation})
        with self.assertRaises(ValueError):
            replace(request, resource=relation)
        occupancy = Occupancy('observed', resource, self.target, quantity(1), Interval(10, 20), ('sensor',))
        for field in ('resource', 'consumer'):
            with self.subTest(contract='Occupancy', field=field), self.assertRaises(ValueError):
                replace(occupancy, **{field: relation})

    def test_IC06_arbitrary_size_integer_and_float_sums_do_not_overflow(self):
        capacity = 10 ** 1000
        resource = self.add_resource('huge-int', capacity=quantity(capacity))
        request = self.request(resource, 'too-large', quantity(capacity + 1))
        result = self.admit(self.bundle('integer-overload', (request,)))
        self.assertEqual(result.reason, 'capacity_exceeded')
        self.assertEqual(result.witness[0].total, capacity + 1)
        floats = self.add_resource('huge-float', capacity=quantity(1e308))
        occupancies = tuple(Occupancy(str(index), floats, ref('actor-' + str(index)),
                                     quantity(1e308), Interval(10, 20), ('sensor-' + str(index),))
                            for index in range(2))
        self.publish(floats, occupancies)
        conflicts = self.ledger.capacity_conflicts(floats, Interval(10, 20), now_ns=0)
        self.assertEqual(len(conflicts), 1)
        self.assertGreater(conflicts[0].total, 1e308)

    def test_ICS10_past_requests_are_not_backdated(self):
        resource = self.add_resource()
        intent = self.bundle('late', (self.request(resource, 'claim', start=10, end=20),))
        self.assertEqual(self.admit(intent, now=16).reason, 'past_or_expired_request')
        self.assertEqual(self.ledger.claims, ())


if __name__ == '__main__':
    unittest.main()
