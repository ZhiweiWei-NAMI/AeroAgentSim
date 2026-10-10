"""X5 service lifetime and receiver-owned result timer.

Allocation payloads are frozen in the simulator submission ledger and bound to
native UDP RX. They are not decoded wire JSON or a physical PX4 acknowledgement.
"""
from __future__ import annotations


def service_expiry_ns(profile):
    lifetime = profile['pad_service_lifetime']
    expiry = lifetime['valid_until_ns']
    if type(expiry) is not int or expiry <= 0:
        raise ValueError('pad service expiry must be a positive absolute simulation timestamp')
    return expiry


def allocation_payload(profile, actor, arbitration_ns, action):
    if (actor not in profile['actor_ids'] or type(arbitration_ns) is not int
            or not 0 <= arbitration_ns < service_expiry_ns(profile)):
        raise ValueError('pad allocation must bind an actor and unexpired arbitration time')
    expected = 'priority_granted' if actor == profile['priority_order'][0] else 'reroute'
    if action != expected:
        raise ValueError('pad allocation differs from declared priority policy')
    return {'schema_version': 'p09.pad-allocation-result/v1',
        'kind': 'pad_allocation_result', 'pad_owner': profile['pad_owner'],
        'receiver_owner': actor, 'arbitration_event_id': 'pad_priority_arbitration',
        'arbitration_ns': arbitration_ns, 'allocation_action': action,
        'valid_until_ns': service_expiry_ns(profile),
        'timer_policy_version': profile['pad_result_timer']['version']}


def received_reroute_timer(profile, item, owner, now_ns, step_ns, fact):
    """Use only an available native receipt and its exact submission payload.

    The original 30-tick delay is measured from the trusted pad arbitration
    timestamp carried in that received result. No global event table is read.
    """
    if not fact['allow']:
        return fact
    receipt, submission = item['accepted'], item['submission']
    payload = submission['application_payload']
    mid = 'pad-result:' + owner
    if (owner != profile['actor_ids'][1]
            or submission['message_id'] != mid or receipt['command_id'] != mid
            or submission['source'] != profile['pad_owner']
            or receipt['source_owner'] != profile['pad_owner']
            or submission['receiver'] != owner or receipt['receiver_owner'] != owner
            or submission['action'] != 'reroute' or receipt['action'] != 'reroute'
            or not isinstance(payload, dict)
            or type(payload.get('arbitration_ns')) is not int):
        raise ValueError('reroute timer requires the exact received pad allocation result')
    arbitration = payload['arbitration_ns']
    if payload != allocation_payload(profile, owner, arbitration, 'reroute'):
        raise ValueError('received pad allocation payload differs from the bound timer policy')
    if (not 0 <= arbitration == submission['evidence_ns'] <= submission['send_ns']
            <= receipt['send_ns'] <= receipt['accepted_ns'] < now_ns
            or receipt['accepted_ns'] >= service_expiry_ns(profile)):
        raise ValueError('received pad allocation timer has inconsistent causal timestamps')
    due_ns = arbitration + profile['pad_result_timer']['delay_original_ticks'] * step_ns
    return {'allow': now_ns >= due_ns, 'evidence': {**fact['evidence'],
        'received_application_payload': payload, 'timer_due_ns': due_ns,
        'timer_policy': profile['pad_result_timer'],
        'reason': 'received result timer due' if now_ns >= due_ns else 'received result timer pending'}}
