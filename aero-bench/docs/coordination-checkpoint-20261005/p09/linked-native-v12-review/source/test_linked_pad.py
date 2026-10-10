"""Synthetic service boundary controls; native evidence is reported separately."""
import copy
from pathlib import Path
from unittest import TestCase, main

from .linked_contract import load_contract, STEP_NS
from .linked_pad import allocation_payload
from .linked_replay import Admission


class PadLifetimeTests(TestCase):
    def setUp(self):
        self.profile, _, self.script = load_contract(Path(__file__).resolve().parents[3],
            'X5_comm_failure_to_pad_contention', 0)
        self.owner = self.profile['actor_ids'][1]
        self.pad = self.profile['pad_owner']
        self.mid = 'pad-result:' + self.owner
        self.item = {'accepted': {'command_id': self.mid, 'packet_id': 'synthetic-control',
            'source_owner': self.pad, 'source_epoch': 0, 'receiver_owner': self.owner,
            'receiver_epoch': 0, 'status': 'accepted', 'action': 'reroute',
            'send_ns': 585 * STEP_NS, 'accepted_ns': 585 * STEP_NS + 3_000_000},
            'submission': {'message_id': self.mid, 'source': self.pad, 'receiver': self.owner,
                'evidence_ns': 580 * STEP_NS, 'send_ns': 585 * STEP_NS, 'action': 'reroute',
                'application_payload': allocation_payload(self.profile, self.owner, 580 * STEP_NS, 'reroute')}}
        self.episode = {'nodes': [{'owner': owner, 'life_epoch': 0,
            'birth_ns': 0, 'death_ns': None} for owner in (*self.profile['actor_ids'], self.pad)]}
        self.event = next(e for e in self.script['events'] if e['event_id'] == 'second_uav_reroute')

    def admission(self, item=None):
        return Admission(self.profile, {}, {self.mid: self.item if item is None else item}, self.episode)

    def test_half_open_expiry_for_requests_and_results(self):
        admission = self.admission()
        request_mid = 'pad-request:' + self.owner
        request = copy.deepcopy(self.item)
        request['accepted'].update(command_id=request_mid, source_owner=self.owner,
            receiver_owner=self.pad, action='request_pad')
        admission.receipts[request_mid] = request
        for mid, owner in ((self.mid, self.owner), (request_mid, self.pad)):
            self.assertTrue(admission.received_fact(mid, owner, 654)['allow'])
            for tick in (655, 656, 660, 900):
                decision = admission.received_fact(mid, owner, tick)
                self.assertFalse(decision['allow'])
                self.assertEqual(decision['evidence']['valid_until_ns'], 655 * STEP_NS)

    def test_received_timer_due_and_strict_rx_availability_without_interpreter(self):
        admission = self.admission()
        # No interpreter/global arbitration table is supplied to this gate.
        self.assertFalse(admission.gate(self.event, 585, None, None)['allow'])
        self.assertFalse(admission.gate(self.event, 609, None, None)['allow'])
        due = admission.gate(self.event, 610, None, None)
        self.assertTrue(due['allow'])
        self.assertEqual(due['evidence']['timer_due_ns'], 610 * STEP_NS)
        self.assertFalse(admission.gate(self.event, 655, None, None)['allow'])

    def test_late_result_waits_for_native_rx_instead_of_global_due_time(self):
        item = copy.deepcopy(self.item)
        item['accepted']['accepted_ns'] = 620 * STEP_NS
        admission = self.admission(item)
        for tick in (610, 620):
            self.assertFalse(admission.gate(self.event, tick, None, None)['allow'])
        self.assertTrue(admission.gate(self.event, 621, None, None)['allow'])
        absent = Admission(self.profile, {}, {}, self.episode)
        self.assertFalse(absent.gate(self.event, 610, None, None)['allow'])

    def test_wrong_owner_payload_clock_or_policy_fails_explicitly(self):
        for key, value in (('receiver_owner', 'peer'), ('arbitration_ns', 590 * STEP_NS),
                           ('valid_until_ns', 900 * STEP_NS), ('timer_policy_version', 'wrong')):
            item = copy.deepcopy(self.item)
            item['submission']['application_payload'][key] = value
            with self.assertRaises(ValueError):
                self.admission(item).gate(self.event, 610, None, None)
        item = copy.deepcopy(self.item)
        item['submission']['application_payload'] = None
        with self.assertRaises(ValueError):
            self.admission(item).gate(self.event, 610, None, None)

    def test_scoped_trigger_and_phase_topics_are_distinct(self):
        trigger = next(t for t in self.script['triggers'] if t['trigger_id'] == self.event['trigger_ref'])
        self.assertEqual(trigger, {'trigger_id': 'p09.trigger:received-pad-result-reroute',
                                  'type': 'tick', 'tick': 0})
        self.assertNotIn('causal_predecessor_intent', self.event)
        for owner in self.profile['actor_ids']:
            approach = next(e for e in self.script['events'] if e.get('p09_owner_id') == owner
                            and e.get('p09_phase') == 'pad_approach')
            request = next(e for e in self.script['events'] if e.get('p09_owner_id') == owner
                           and e.get('p09_phase') == 'pad_request_intent')
            self.assertEqual(request['log_event']['topic'], request['event_id'])
            self.assertNotEqual(request['log_event']['topic'], approach['log_event']['topic'])
        for scenario in ('L6-4_v1', 'L6-4_v2'):
            _, scene, script = load_contract(Path(__file__).resolve().parents[3], scenario, 0)
            self.assertIn('Cochannel UDP', script['description'])
            self.assertNotIn('Wideband', script['description'])
            self.assertEqual(scene['description'], script['description'])
            self.assertNotIn('jamming', script['parameters']['semantic_event_contract']['required_event'])
            for rule in scene['validation_rules']:
                if 'contract' in rule and 'required_event' in rule['contract']:
                    self.assertNotIn('jamming', rule['contract']['required_event'])


if __name__ == '__main__':
    main()
