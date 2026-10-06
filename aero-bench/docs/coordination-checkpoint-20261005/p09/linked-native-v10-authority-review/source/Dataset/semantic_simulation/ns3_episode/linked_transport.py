"""Exact lifetime, flow and receipt plumbing for linked P09 replay.

All receipt timestamps come from native UDP RX. The command application policy
is declared simulator acceptance of an exact tagged flow/datagram; it is not a
hardware ACK or PX4 command receipt.
"""
from __future__ import annotations

from collections import defaultdict
from .command_receipts import CommandTransmission, CommandReception, record_downlink_receipt
from .gateway_sequence_metric import ScheduledDatagram, GatewayReception, GatewaySequenceMetric
from .linked_contract import STEP_NS


def episode_from_engine(profile, engine):
    endpoints = list(profile['actor_ids'])
    for field in ('primary_station', 'backup_station', 'pad_owner'):
        owner = profile.get(field)
        if owner is not None and owner not in endpoints:
            endpoints.append(owner)
    by_owner = defaultdict(list)
    for row in engine.trajectory_rows:
        if row['entity_id'] in endpoints:
            by_owner[row['entity_id']].append(row)
    nodes, positions = [], defaultdict(list)
    for owner in endpoints:
        if owner not in engine.entities or not by_owner[owner]:
            raise ValueError(f'authored endpoint has no actual source trajectory: {owner}')
        rows = by_owner[owner]
        groups = []
        for row in rows:
            if not groups or row['tick'] != groups[-1][-1]['tick'] + 1:
                groups.append([])
            groups[-1].append(row)
        for epoch, lifetime in enumerate(groups):
            node_id = f"{profile['episode_id']}:{owner}:life{epoch}"
            mobile = owner in profile['actor_ids']
            offset = [0, 0, 0 if mobile else 3]
            first = [v + d for v, d in zip(lifetime[0]['pos_enu'], offset)]
            death = (lifetime[-1]['tick'] + 1) * STEP_NS
            nodes.append({'node_id': node_id, 'owner': owner, 'life_epoch': epoch,
                          'node_type': 'uav' if mobile else 'gateway',
                          'birth_ns': lifetime[0]['tick'] * STEP_NS,
                          'death_ns': None if death > engine.duration_ticks * STEP_NS else death,
                          'position_enu_m': first, 'source_ref': profile['source_script'],
                          'infra_id': None if mobile else owner,
                          'ontology_class': engine.entities[owner].get('ontology_class'),
                          'lifecycle_authority': 'actual source presence, not physical birth/death'})
            for row in lifetime:
                positions[row['tick'] * STEP_NS].append({
                    'node_id': node_id, 'position_enu_m': [v + d for v, d in zip(row['pos_enu'], offset)],
                    'active': True})
    for index, node in enumerate(nodes):
        node['ns3_node_id'] = index
    primary, = [n for n in nodes if n['owner'] == profile['primary_station']]
    return {'episode_id': profile['episode_id'], 'source_ref': profile['source_script'],
            'duration_ns': engine.duration_ticks * STEP_NS, 'nodes': nodes,
            'mobility': [{'time_ns': t, 'positions': positions[t]} for t in sorted(positions)],
            'gateway_node_id': primary['node_id'], 'gateway_owner': primary['owner'],
            'gateway_entity_id': primary['owner'], 'roster_uav_ids': list(profile['actor_ids']),
            'gateway_antenna_offset_m': [0, 0, 3], 'mobile_antenna_offset_m': [0, 0, 0]}


def owner_node(episode, owner, time_ns):
    nodes = [n for n in episode['nodes'] if n['owner'] == owner and n['birth_ns'] <= time_ns
             and (n['death_ns'] is None or time_ns < n['death_ns'])]
    if len(nodes) != 1:
        raise ValueError(f'endpoint must resolve to one actual lifetime: {owner} at {time_ns}')
    return nodes[0]


def flow(episode, flow_id, source, receiver, start_ns, end_ns, *, payload_bytes=256, period_ns=STEP_NS):
    src = owner_node(episode, source, start_ns)
    dst = owner_node(episode, receiver, start_ns)
    for node in (src, dst):
        if node['death_ns'] is not None and end_ns > node['death_ns']:
            raise ValueError('flow crosses its explicit endpoint generation')
    return {'flow_id': flow_id, 'link_id': flow_id + ':link',
            'src_owner': source, 'life_epoch': src['life_epoch'],
            'dst_owner': receiver, 'dst_life_epoch': dst['life_epoch'],
            'start_ns': start_ns, 'end_ns': end_ns, 'payload_bytes': payload_bytes, 'period_ns': period_ns}


def receiver_monitors(episode, flows, network, profile):
    """Evaluate only pre-agreed heartbeat slots and available receiver-local RX."""
    by_node = {n['node_id']: n for n in episode['nodes']}
    hb = [f for f in flows if ':heartbeat:' in f['flow_id']]
    schedules, receptions = defaultdict(list), defaultdict(list)
    slots = {}
    for f in hb:
        receiver = (f['dst_owner'], f['dst_life_epoch'], f['flow_id'])
        for seq, t in enumerate(range(f['start_ns'], f['end_ns'], f['period_ns'])):
            schedules[receiver].append(ScheduledDatagram(
                f['flow_id'], f['src_owner'], f['life_epoch'], *receiver[:2], seq, t,
                profile['heartbeat']['ttl_ns'], 0))
            slots[f['flow_id'], t] = seq
    for p in network['network_packets']:
        key = (p['flow_id'], p['first_tx_ns'])
        if key not in slots or p['rx_ns'] is None:
            continue
        src, dst = by_node[p['src_node_id']], by_node[p['dst_node_id']]
        receiver = (dst['owner'], dst['life_epoch'], p['flow_id'])
        receptions[receiver].append(GatewayReception(p['flow_id'], src['owner'], src['life_epoch'],
                                                     *receiver[:2], slots[key], p['rx_ns']))
    result = {}
    for receiver, schedule in schedules.items():
        metric = GatewaySequenceMetric(schedule, receptions[receiver])
        # A contiguous exact scheduled sequence is a local watchdog input.
        received = {(r.flow_id, r.sequence): r.receive_ns for r in receptions[receiver]}
        rows = []
        for t in range(0, episode['duration_ns'] + 1, STEP_NS):
            row = metric.observe(observation_ns=t, window_ns=profile['heartbeat']['window_ns'],
                                 receiver_owner=receiver[0], receiver_epoch=receiver[1])
            mature = [s for s in schedule if s.generation_ns + s.ttl_ns < t]
            missing = 0
            for s in reversed(mature):
                rx = received.get((s.flow_id, s.sequence))
                if rx is not None and rx < s.generation_ns + s.ttl_ns:
                    break
                missing += 1
            row['consecutive_missing'] = missing
            row['degraded'] = (None if row['loss_ratio'] is None else
                               row['loss_ratio'] > profile['heartbeat']['loss_limit']
                               and missing >= profile['heartbeat']['consecutive_limit'])
            row['rule_version'] = 'p09.actor-heartbeat-watchdog/v1'
            row['state_source'] = 'native receiver RX versus declared expected sequence; not global accepted-TX loss'
            rows.append(row)
        result[receiver] = rows
    return result


def message_flow(episode, profile, message_id, source, receiver, send_ns, evidence_ns, action):
    if not evidence_ns <= send_ns < episode['duration_ns']:
        raise ValueError('message send must follow available evidence inside the episode')
    command = profile['pad_request_transport'] if action == 'request_pad' else profile['command']
    end = min(send_ns + command['attempts'] * command['period_ns'], episode['duration_ns'])
    return {'message_id': message_id, 'source': source, 'receiver': receiver,
            'send_ns': send_ns, 'evidence_ns': evidence_ns, 'action': action,
            'deadline_ns': send_ns + command['deadline_ns'],
            'flow': flow(episode, message_id, source, receiver, send_ns, end,
                         payload_bytes=command['payload_bytes'], period_ns=command['period_ns'])}


def message_receipts(episode, messages, network):
    by_node = {n['node_id']: n for n in episode['nodes']}
    result = {}
    for message in messages:
        attempts = []
        for p in network['network_packets']:
            if p['flow_id'] != message['message_id']:
                continue
            source, receiver = by_node[p['src_node_id']], by_node[p['dst_node_id']]
            if (source['owner'], receiver['owner']) != (message['source'], message['receiver']):
                raise ValueError('native message endpoints differ from immutable submission identity')
            if (source['life_epoch'], receiver['life_epoch']) != (
                    message['flow']['life_epoch'], message['flow']['dst_life_epoch']):
                raise ValueError('native message generation differs from immutable submission identity')
            transmission = CommandTransmission(
                message['message_id'], p['packet_id'], source['owner'], source['life_epoch'],
                receiver['owner'], receiver['life_epoch'], message['send_ns'], p['first_tx_ns'],
                message['deadline_ns'], message['evidence_ns'], message['action'])
            rx = None if p['rx_ns'] is None else CommandReception(
                p['packet_id'], receiver['owner'], receiver['life_epoch'], p['rx_ns'])
            attempts.append(record_downlink_receipt(transmission, rx))
        accepted = sorted((a for a in attempts if a['status'] == 'accepted'),
                          key=lambda a: a['accepted_ns'])
        result[message['message_id']] = {
            'attempts': attempts, 'accepted': accepted[0] if accepted else None,
            'submission': {k: v for k, v in message.items() if k != 'flow'}}
    return result
