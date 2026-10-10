"""Declared station-egress workload candidate contract for the P09 L6-4 experiments.

This module builds on the adopted cochannel-backup contract. It rebinds the
declared bulk UDP workload from a UAV-to-UAV peer flow to two station-egress
flows from the authored primary radio tower, one to each mission UAV, on the
same shared channel. The offered schedule is numerically identical to the
original single 8 Mb/s aggregate (1200 B every 1.2 ms from the fault tick to
the preloaded rendezvous); only the egress direction and recipients change.

The workload is a declared application-load experiment. The overload
prediction below is a theoretical bound, not an observed RF or hardware fact,
and recovery claims stay receipt-bound: recovery may coincide with bulk
completion and the preloaded radio rendezvous, and nothing here certifies a
channel-switch-only cause or a wideband jamming attack.
"""
from __future__ import annotations

import copy

from .linked_contract import load_contract

CANDIDATE_REVISION = 'p09.station-egress-congestion/v2-metadata-candidate'
SCENARIOS = ('L6-4_v1', 'L6-4_v2')
SEEDS = (0, 1, 2)
WORKLOAD_VERSION = 'p09.station-egress-two-recipient/v1'
REQUIRED_EVENT = ('declared station-egress offered UDP workload > owner-local heartbeat '
                  'deadline-miss guard > received backup response > landing')
COCHANNEL_REQUIRED_EVENT = ('cochannel UDP competition > owner-local safe hold > '
                            'received backup response > landing')
EGRESS_DESCRIPTION = ('Declared station-egress offered UDP workload; owner-local '
                      'heartbeat deadline-miss guard and receipt-bound backup response')
ASSUMPTION = ('declared station-egress application workload; offered bulk payload '
              'exceeds the ideal shared-channel capacity')
BOUND = ('theoretical capacity bound: ideal HtMcs0 PHY rate 6.5 Mb/s < 8 Mb/s offered '
         'payload; this predicts shared-channel queue overload pressure, not guaranteed '
         'heartbeat failure')
LEGACY_NOTE = ('legacy wideband_jamming event/trigger/action/topic IDs are retained as '
               'source provenance; they do not certify wideband jamming')
RECOVERY_NOTE = ('recovery may coincide with bulk completion and the preloaded absolute '
                 'radio rendezvous; this contract does not certify channel-switch-only '
                 'causation and all commands stay receipt-bound')


def station_egress_contract(root, scenario_id, seed):
    """Return the station-egress candidate (profile, scene, script) copy.

    Only L6-4_v1/L6-4_v2 with seeds 0/1/2 are declared. The input authored
    source files and the adopted cochannel contract are never modified; every
    change below is applied to a deep copy of the loaded private contract.
    """
    if scenario_id not in SCENARIOS or type(seed) is not int or seed not in SEEDS:
        raise ValueError('undeclared station-egress candidate scenario/seed')
    profile, scene, script = load_contract(root, scenario_id, seed)
    if profile['mechanism'] != 'cochannel_backup':
        raise ValueError('station-egress candidate requires the cochannel_backup mechanism')
    if len(profile['actor_ids']) != 2:
        raise ValueError('station-egress candidate requires exactly two bound actors')
    profile, scene, script = copy.deepcopy(profile), copy.deepcopy(scene), copy.deepcopy(script)

    # Bulk schedule identity: the original aggregate is one 1200 B flow
    # every 1.2 ms on [wideband_jamming tick, rendezvous tick). Two staggered
    # station-egress flows at 2.4 ms interleave into exactly that schedule.
    jamming_tick = next(t['tick'] for t in script['triggers']
                        if t['trigger_id'] == 'trig_wideband_jamming')
    rendezvous = profile['rendezvous']
    start_ns = jamming_tick * 100_000_000
    end_ns = rendezvous['time_ns']
    if rendezvous['tick'] <= jamming_tick:
        raise ValueError('preloaded rendezvous must follow the declared fault tick')
    receivers = sorted(profile['actor_ids'])
    profile['station_egress_workload'] = {
        'version': WORKLOAD_VERSION,
        'candidate_revision': CANDIDATE_REVISION,
        'source_owner': profile['primary_station'],
        'flows': [{'flow_id': f'station-egress:{profile["primary_station"]}:to:{owner}',
                   'receiver_owner': owner,
                   'payload_bytes': 1200, 'period_ns': 2_400_000,
                   'start_ns': start_ns if index == 0 else start_ns + 1_200_000,
                   'end_ns': end_ns}
                  for index, owner in enumerate(receivers)],
        'interleave_basis': 'two staggered 2.4 ms station-egress flows reproduce the '
                            'original aggregate 1200 B/1.2 ms schedule exactly',
        'application_workload_assumption': ASSUMPTION,
        'theoretical_bound': BOUND,
        'recovery_interpretation': RECOVERY_NOTE,
        'legacy_provenance': 'adopted cochannel_backup contract; legacy peer-load '
                             'revision retained separately, workload direction only',
    }

    semantic = script['parameters']['semantic_event_contract']
    if semantic['required_event'] != COCHANNEL_REQUIRED_EVENT:
        raise ValueError('loaded cochannel required_event differs from the declared string')
    semantic['required_event'] = REQUIRED_EVENT
    rules = [rule for rule in scene['validation_rules']
             if rule.get('contract', {}).get('required_event') == COCHANNEL_REQUIRED_EVENT]
    if len(rules) != 1:
        raise ValueError('exactly one scene validation contract must carry the cochannel required_event')
    rules[0]['contract']['required_event'] = REQUIRED_EVENT

    script['description'] = EGRESS_DESCRIPTION
    scene['description'] = EGRESS_DESCRIPTION
    for event in script['events']:
        if event['event_id'] == 'wideband_jamming':
            event['log_event']['title'] = ('Declared station-egress offered UDP workload '
                                           'competes with two UAV heartbeat flows')
        elif event['event_id'] in ('multi_uav_hold_entry', 'multi_uav_safe_hold'):
            event['log_event']['title'] = ('Owner-local heartbeat deadline-miss guards enter '
                                           'the declared workload hold'
                                           if event['event_id'] == 'multi_uav_hold_entry'
                                           else 'Local safe-hold policy retains two UAV control delays')
    script['parameters']['station_egress_candidate'] = {
        'version': WORKLOAD_VERSION, 'revision': CANDIDATE_REVISION,
        'required_event': REQUIRED_EVENT,
        'assumption': ASSUMPTION, 'theoretical_bound': BOUND,
        'recovery_interpretation': RECOVERY_NOTE, 'legacy_provenance': LEGACY_NOTE,
        'adopted_cochannel_provenance': {
            'profile_version': profile['schema_version'],
            'cochannel_required_event': COCHANNEL_REQUIRED_EVENT,
            'interference_class': profile['interference_class'],
            'channel_switch_authority': profile['channel_switch_authority']}}
    return profile, scene, script
