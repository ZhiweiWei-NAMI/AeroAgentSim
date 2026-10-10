"""Audit-derived adapter regression tests. Pure fixtures; native Atlas is read-only."""
from dataclasses import replace
import os
from pathlib import Path
import unittest
from contracts import *
from adapter import *
from json_io import read_document, load_case

NS = 1_000_000_000
HERE = Path(__file__).resolve().parent
ATLAS = os.environ.get('ATLAS_ROOT')

SPEED = 'hu.actor.speed_mps'


def ref(id='ugv.01', generation=1, ref_type='object'):
    return Ref('fixture.run', 'epoch.1', id, generation, ref_type)


def obj(r=None, kind='ugv', caps=()):
    r = r or ref()
    return ObjectRecord(r, kind, Interval(0, 100 * NS),
                        tuple(Capability(c, 'provider', r, Interval(0, 100 * NS), 'v1') for c in caps))


def cell(r=None, q=SPEED, value=1, **changes):
    defaults = dict(cell_id='cell.1', holder=r or ref(), quantity=q, value=value, dtype='number', unit='m/s',
                    sample_ns=0, valid=Interval(0, 100 * NS), evidence_domain='provider.domain',
                    observation_commit=1, available_after_commit=1, available_ns=100, authority='provider',
                    source_ref='sensor.1', provenance='hypothetical')
    return StateCell(**(defaults | changes))


def selector(r=None, q=SPEED, **changes):
    return Selector(**(dict(field_id=q, holder=r or ref(), quantity=q, dtype='number', unit='m/s',
                            allowed_authorities=('provider',), allowed_provenances=('hypothetical',)) | changes))


def bind(r=None, target='hu.predicate.actor_moving', kind='ugv', **changes):
    r = r or ref()
    defaults = dict(binding_id='fixture.binding', target_id=target, target_revision='fixture-pinned-atlas',
                    roles=(('actor', r),), requirements=(RoleRequirement('actor', (kind,)),), selectors=(selector(r),))
    return CompiledBinding(Binding(**(defaults | changes)))


def point(t=5, **changes):
    return QueryPoint(**(dict(event_ns=t * NS, evidence_domain='provider.domain', available_ns=500, commit=5) | changes))


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = load_atlas(ATLAS) if ATLAS else None
        cls.document = read_document(HERE / 'fixtures.json')

    def result(self, cells, b=None, objects=None, q=None, relations=()):
        b = b or bind()
        s = Snapshot(objects or [obj()], cells, relations)
        p = b.prepare(s, q or point())
        return b.evaluate(self.engine, p), p

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_seven_kinds_same_pipeline(self):
        self.assertEqual(7, len(self.document['cases']))
        for case in self.document['cases']:
            with self.subTest(case=case['id']):
                snapshot, binding, query = load_case(self.document, case)
                actual = binding.evaluate(self.engine, binding.prepare(snapshot, query))
                self.assertEqual(case['expected'], actual['result'])

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_literal_dotted_entity_id(self):
        world = {'entities': {'ugv.01': {'speed_mps': 0}, 'ugv': {'01': {'speed_mps': 2}}}}
        self.assertIs(True, self.engine.evaluate('hu.predicate.actor_moving', world, owner_map={'actor': 'ugv.01'})['result'])
        value = exact_path(world, ('entities', 'ugv.01', 'speed_mps'))
        actual, prepared = self.result([cell(value=value)])
        self.assertIs(False, actual['result'])
        self.assertEqual({SPEED: 'slot_0'}, prepared.field_map)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_stale_human_motion(self):
        r = ref('human.1')
        actual, p = self.result([cell(r, valid=Interval(0, NS))], bind(r, kind='human'), [obj(r, 'human')])
        self.assertEqual('unknown', actual['result'])
        self.assertEqual('outside_valid_interval', p.resolutions[SPEED].reason)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_unit_conversion_ugv(self):
        actual, p = self.result([cell(value=.5, unit='km/h')])
        self.assertIs(False, actual['result'])
        self.assertAlmostEqual(.5 / 3.6, p.values['slot_0'])
        self.assertIn('km/h->m/s', p.resolutions[SPEED].conversions[0])
        self.assertEqual('unknown', self.result([cell(unit='knots')])[0]['result'])

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_future_available_uav_both_independent_cutoffs(self):
        for changes in ({'available_after_commit': 6}, {'available_ns': 501}):
            with self.subTest(changes=changes):
                actual, p = self.result([cell(**changes)])
                self.assertEqual('unknown', actual['result'])
                self.assertEqual('not_available_as_of', p.resolutions[SPEED].reason)
        # Wall-clock-like availability is not compared against 5-second event time.
        self.assertIs(True, self.result([cell(available_ns=400)])[0]['result'])

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_evidence_domain_cannot_be_mixed(self):
        actual, p = self.result([cell(evidence_domain='other.clock')])
        self.assertEqual('unknown', actual['result'])
        self.assertEqual('evidence_domain_mismatch', p.resolutions[SPEED].reason)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_visualization_is_not_authority(self):
        actual, p = self.result([cell(authority='renderer')])
        self.assertEqual('unknown', actual['result'])
        self.assertEqual('unauthorized_source', p.resolutions[SPEED].reason)
        self.assertEqual('unknown', self.result([cell(provenance='measured')])[0]['result'])

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_conflicting_sources_no_last_writer_wins(self):
        first = cell(value=0)
        second = cell(value=2, cell_id='cell.2', source_ref='sensor.2')
        for sequence in ([first, second], [second, first]):
            actual, p = self.result(sequence)
            self.assertEqual('unknown', actual['result'])
            self.assertEqual('conflicting_source_values', p.resolutions[SPEED].reason)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_same_revision_integrity_conflict(self):
        actual, p = self.result([cell(value=0), cell(value=2, cell_id='cell.2')])
        self.assertEqual('unknown', actual['result'])
        self.assertEqual('integrity_conflict', p.resolutions[SPEED].reason)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_explicit_supersedes_not_latest_arrival(self):
        newer = cell(value=0, cell_id='cell.2', revision='2', observation_commit=2, available_after_commit=2)
        self.assertEqual('unknown', self.result([cell(), newer])[0]['result'])
        self.assertIs(False, self.result([cell(), replace(newer, supersedes=('cell.1',))])[0]['result'])
        wrong_source = replace(newer, source_ref='sensor.other', supersedes=('cell.1',))
        self.assertEqual('invalid_supersession', self.result([cell(), wrong_source])[1].resolutions[SPEED].reason)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_compute_generation_reuse(self):
        r = ref('queue.1', 2)
        q = 'mn.queue.pending_request_count'
        b = bind(r, 'machine_network.queue.compute_backlog', 'compute_resource', selectors=(selector(r, q, dtype='integer', unit='count'),))
        actual, p = self.result([cell(ref('queue.1', 1), q, 20, dtype='integer', unit='count')], b, [obj(r, 'compute_resource')])
        self.assertEqual('unknown', actual['result'])
        self.assertEqual('exact_binding_missing', p.resolutions[q].reason)

    def make_pair(self, role_names=('actor', 'other')):
        a, b = ref(), ref('human.1')
        rr = ref('pair.1', ref_type='relation')
        endpoints = ((role_names[0], a), (role_names[1], b))
        relation = RelationRecord(rr, 'spatial.clearance', endpoints, Interval(0, 10 * NS), 'provider', '1', 'provider.domain', 1, 100)
        q = 'hu.pair.clearance_m'
        binding = bind(a, 'hu.predicate.pair_close', roles=(('actor', a), ('other', b)),
                       requirements=(RoleRequirement('actor', ('ugv',)), RoleRequirement('other', ('human',))),
                       selectors=(selector(rr, q, unit='m', relation_schema='spatial.clearance', endpoints=endpoints),))
        return a, b, rr, relation, binding

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_wrong_pair_geometry(self):
        a, b, rr, relation, binding = self.make_pair()
        actual, p = self.result([cell(rr, 'hu.pair.clearance_m', .1, unit='m')], binding, [obj(a), obj(b, 'human')], relations=[relation])
        self.assertIs(True, actual['result'])
        wrong = replace(relation, endpoints=(('actor', b), ('other', a)))
        actual, p = self.result([cell(rr, 'hu.pair.clearance_m', .1, unit='m')], binding, [obj(a), obj(b, 'human')], relations=[wrong])
        self.assertEqual('unknown', actual['result'])
        self.assertEqual('relation_endpoint_or_schema_mismatch', p.resolutions['hu.pair.clearance_m'].reason)
        self.assertEqual('invalid', p.status)

    def test_directed_network_relation_and_projection(self):
        a, b, rr, relation, _ = self.make_pair(('source', 'destination'))
        snap = Snapshot([obj(a), obj(b, 'human')], relations=[relation])
        forward = snap.scoped_relations('spatial.clearance', (('source', a), ('destination', b)), point(), authorities=('provider',))
        reverse = snap.scoped_relations('spatial.clearance', (('source', b), ('destination', a)), point(), authorities=('provider',))
        self.assertEqual(Truth.TRUE, forward.exists)
        self.assertEqual(Truth.FALSE, forward.absent)
        self.assertEqual(Truth.UNKNOWN, reverse.exists)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_generation_history_isolation(self):
        old, new = ref(generation=1), ref(generation=2)
        b1, b2 = bind(old, 'hu.predicate.actor_stationary'), bind(new, 'hu.predicate.actor_stationary')
        p1 = b1.prepare(Snapshot([obj(old)], [cell(old, value=0)]), point(0))
        p2 = b2.prepare(Snapshot([obj(new)], [cell(new, value=0)]), point(3))
        raw = self.engine.evaluate('hu.predicate.actor_stationary', {SPEED: 0}, t=3, history=[{'t': 0, 'states': {SPEED: 0}}])
        self.assertIs(True, raw['result'])
        self.assertEqual('unknown', b2.evaluate(self.engine, p2)['result'])
        with self.assertRaisesRegex(ValueError, 'history binding'):
            b2.evaluate(self.engine, p2, [p1])

    def test_binding_and_policy_changes_reset_key(self):
        b = bind()
        for changes in ({'parameters': (('speed_threshold', 2),)}, {'evaluation_revision': '2'}, {'policy_revision': '2'}, {'scenario_revision': '2'}, {'binding_epoch': '2'}):
            self.assertNotEqual(b.key, CompiledBinding(replace(b.binding, **changes)).key)
        actual, p = self.result([cell()], b, q=point(observation_policy='strict_left'))
        self.assertEqual('invalid', p.status)
        self.assertEqual('unknown', actual['result'])

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_capability_does_not_follow_kind_or_imply_authorization(self):
        req = RoleRequirement('actor', ('ugv',), ('responsible_custody',), ('provider',))
        b = bind(requirements=(req,))
        self.assertEqual('unsupported', self.result([cell()], b)[1].status)
        self.assertEqual('bound', self.result([cell()], b, [obj(caps=('responsible_custody',))])[1].status)
        # Adapter exposes applicability only; it has no execute/dispatch method.
        self.assertFalse(hasattr(b, 'execute'))

    def test_lifecycle_half_open_and_type_applicability(self):
        for t in (-1, 100):
            self.assertEqual('not_bound', self.result([cell()], q=point(t))[1].status)
        self.assertEqual('invalid', self.result([cell()], objects=[obj(kind='compute_resource')])[1].status)

    def test_scoped_negative_requires_complete_current_exact_coverage(self):
        r = ref('pad.1')
        scope = (('resource', r),)
        cov = ScopeCoverage('occupancy', scope, Interval(0, 10 * NS), 'provider.domain', 5, 1, 100, 'provider', True)
        def query(coverage=()):
            return Snapshot([obj(r, 'facility')], coverage=coverage).scoped_relations('occupancy', scope, point(), authorities=('provider',))
        self.assertEqual(Truth.UNKNOWN, query().absent)
        self.assertEqual(Truth.TRUE, query([cov]).absent)
        for c in (replace(cov, complete=False), replace(cov, through_commit=4), replace(cov, available_after_commit=6),
                  replace(cov, exact_scope=(('resource', ref('pad.other')),)), replace(cov, authority='renderer')):
            self.assertEqual(Truth.UNKNOWN, query([c]).absent)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_strict_left_vs_after_barrier(self):
        exact = cell(sample_ns=5 * NS)
        self.assertIs(True, self.result([exact])[0]['result'])
        b = bind(observation_policy='strict_left')
        actual, p = self.result([exact], b, q=point(observation_policy='strict_left'))
        self.assertEqual('unknown', actual['result'])
        self.assertEqual('observation_boundary', p.resolutions[SPEED].reason)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_strict_left_relations_lifecycles_coverage_share_boundary(self):
        a, b, rr, relation, _ = self.make_pair(('source', 'destination'))
        ended = replace(relation, valid=Interval(0, 5 * NS))
        born = replace(relation, valid=Interval(5 * NS, 10 * NS))
        objects = [replace(obj(a), lifetime=Interval(0, 5 * NS)), obj(b, 'human')]
        q = point(observation_policy='strict_left')
        snap = Snapshot(objects, relations=[ended])
        self.assertEqual(Truth.TRUE, snap.scoped_relations('spatial.clearance', (('source', a),), q, authorities=('provider',)).exists)
        self.assertEqual(Truth.UNKNOWN, Snapshot([obj(a), obj(b, 'human')], relations=[born]).scoped_relations('spatial.clearance', (('source', a),), q, authorities=('provider',)).exists)
        coverage = ScopeCoverage('occupancy', (('resource', a),), Interval(0, 5 * NS), 'provider.domain', 5, 1, 100, 'provider', True)
        self.assertEqual(Truth.TRUE, Snapshot(objects, coverage=[coverage]).scoped_relations('occupancy', (('resource', a),), q, authorities=('provider',)).absent)
        # A cell valid until 5s is still valid immediately before 5s.
        bd = bind(observation_policy='strict_left')
        prepared = bd.prepare(Snapshot(objects, [cell(valid=Interval(0, 5 * NS))]), q)
        self.assertIs(True, bd.evaluate(self.engine, prepared)['result'])

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_late_evidence_only_new_retrospective_revision(self):
        c = cell(available_after_commit=6, available_ns=600)
        b = bind()
        before = b.prepare(Snapshot([obj()], [c]), point())
        newer = bind(evaluation_revision='retrospective.2')
        after = newer.prepare(Snapshot([obj()], [c]), point(commit=6, available_ns=600))
        self.assertEqual('unknown', b.evaluate(self.engine, before)['result'])
        self.assertIs(True, newer.evaluate(self.engine, after)['result'])
        self.assertIsNone(before.values['slot_0'])
        with self.assertRaises(TypeError):
            before.values['slot_0'] = 1

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_IC09_strong_kleene_missing_leaf_does_not_poison_everything(self):
        expected = {'missing_custody': ('unknown', 'unknown'), 'known_bad_payload': (False, True)}
        for name, values in self.document['audit_inputs']['IC09'].items():
            for target, truth in zip(('dt.predicate.custody_handoff_ready', 'dt.predicate.custody_handoff_fault'), expected[name]):
                b = bind(target=target, selectors=tuple(selector(q=q, dtype='string', unit='1') for q in values))
                cells = [cell(q=q, value=v, dtype='string', unit='1', cell_id=q) for q, v in values.items()]
                actual, _ = self.result(cells, b)
                self.assertEqual(truth, actual['result'])

    def test_no_boolean_number_coercion_nan_duplicate_or_bad_ref(self):
        for value in (True, float('nan'), float('inf'), [1]):
            with self.assertRaises(ValueError):
                Snapshot([obj()], [cell(value=value)])
        with self.assertRaises(ValueError):
            Snapshot([obj()], [cell(), cell()])
        with self.assertRaises(ValueError):
            ref('')
        with self.assertRaises(ValueError):
            ref(generation=True)
        with self.assertRaises(KeyError):
            exact_path({'a.b': 1}, ('a', 'b'))

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_frame_ref_and_target_signature_are_enforced(self):
        self.assertEqual('frame_mismatch', self.result([cell(frame_ref='other.enu')])[1].resolutions[SPEED].reason)
        b = bind(selectors=())
        with self.assertRaisesRegex(ValueError, 'not explicitly bound'):
            self.result([], b)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_json_policy_inputs_are_deep_frozen(self):
        import copy
        document = copy.deepcopy(self.document)
        case = document['cases'][1]
        snapshot, binding, query = load_case(document, case)
        key = binding.key
        case['binding']['selectors'][0]['allowed_authorities'].clear()
        case['binding']['requirements'][0]['kinds'].clear()
        self.assertIs(True, binding.evaluate(self.engine, binding.prepare(snapshot, query))['result'])
        self.assertEqual(key, binding.key)
        with self.assertRaises(AttributeError):
            binding.roles = {}
        with self.assertRaises(AttributeError):
            snapshot.cells = ()
        params = [['thresholds', [1, 2]]]
        frozen = replace(bind().binding, parameters=params)
        params[0][1].append(3)
        self.assertEqual((('thresholds', (1, 2)),), frozen.parameters)

    def test_checked_in_ledger_json_fixture(self):
        import json
        from demo import run_ledger_fixture
        result = run_ledger_fixture(json.loads((HERE / 'ledger_fixtures.json').read_text()))
        self.assertEqual(['committed', 'stale_precondition'], result['custody_statuses'])
        self.assertEqual(['admitted', 'stale_precondition', 'capacity_exceeded'], result['resource_outcomes'])
        self.assertEqual(3, result['simultaneous_contact_count'])
        self.assertEqual(0, result['physical_actions_executed'])

    def test_unit_conversion_overflow_is_unavailable(self):
        s = selector(q='rate', unit='bit/s')
        snap = Snapshot([obj()], [cell(q='rate', value=1e308, unit='MB/s')])
        self.assertEqual('unit_conversion_out_of_range', snap.resolve(s, point()).reason)
        self.assertFalse(typed(10 ** 400, 'number'))

    def test_existential_positive_survives_other_incomplete_relation(self):
        a, b, rr, relation, _ = self.make_pair(('source', 'destination'))
        missing = ref('missing.actor')
        partial = replace(relation, ref=ref('pair.2', ref_type='relation'), endpoints=(('source', a), ('destination', missing)))
        snap = Snapshot([obj(a), obj(b, 'human')], relations=[relation, partial])
        answer = snap.scoped_relations('spatial.clearance', (('source', a),), point(), authorities=('provider',))
        self.assertEqual(Truth.TRUE, answer.exists)
        self.assertEqual(Truth.FALSE, answer.absent)
        self.assertEqual(1, len(answer.rows))
        self.assertIsNotNone(answer.reason)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_native_time_precision_loss_rejected(self):
        b = bind()
        large = 10 ** 18
        snap = Snapshot([replace(obj(), lifetime=Interval(0, large + 2))],
                        [cell(valid=Interval(0, large + 2))])
        prepared = b.prepare(snap, point(event_ns=large + 1))
        with self.assertRaisesRegex(ValueError, 'native_time_precision_loss'):
            b.evaluate(self.engine, prepared)

    @unittest.skipUnless(ATLAS, 'Requires caller-provided native Atlas: set ATLAS_ROOT')
    def test_history_cutoff_and_duplicate_time_are_rejected(self):
        b = bind(target='hu.predicate.actor_stationary')
        snap = Snapshot([obj()], [cell(value=0)])
        past, now = b.prepare(snap, point(0)), b.prepare(snap, point(3))
        self.assertIs(True, b.evaluate(self.engine, now, [past])['result'])
        with self.assertRaisesRegex(ValueError, 'unique'):
            b.evaluate(self.engine, now, [past, past])
        future_cutoff = b.prepare(snap, point(0, commit=6))
        with self.assertRaisesRegex(ValueError, 'beyond'):
            b.evaluate(self.engine, now, [future_cutoff])


if __name__ == '__main__':
    unittest.main(verbosity=2)
