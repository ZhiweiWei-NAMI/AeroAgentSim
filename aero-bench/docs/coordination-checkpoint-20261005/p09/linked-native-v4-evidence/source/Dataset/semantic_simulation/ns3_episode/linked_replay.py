"""Coupled native UDP / authored linked-event replay in a new namespace.

Numerical iterations are provisional. Only a fixed point is an adopted trace.
Native receptions and actor-available measurements admit events; the original
executor remains responsible for motion and local weather/business actions.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import time

from .linked_contract import GROUPS, STEP_NS, load_contract, filtered_source_action
from .linked_observations import available_weather, consecutive_rain
from .linked_transport import (episode_from_engine, owner_node, flow,
                               receiver_monitors, message_flow, message_receipts)
from .controller_receipt_consumer import consume_received_commands
from .command_receipts import controller_receipts_before
from .provider_adapter import ProviderAdapter

ROOT = Path(__file__).resolve().parents[3]
RADIO_SOURCE = Path('/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09/receipt_l6_2_v2_seed00_v5_candidate/coupled/L6-2_v2__seed00/receipt_replay/run1/run_config.json')
REVISION = 'p09.linked-native-causal/v4'


def write_json(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')


def write_rows(path, rows):
    with Path(path).open('x') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')


def event_times(run):
    return {key: value.last_fired_tick * STEP_NS
            for key, value in run['interpreter'].event_states.items() if value.fired}


def due_time(script, event_id, times):
    event = next(e for e in script['events'] if e['event_id'] == event_id)
    trigger = next(t for t in script['triggers'] if t['trigger_id'] == event['trigger_ref'])
    if trigger['type'] == 'tick':
        return trigger['tick'] * STEP_NS
    if trigger['type'] == 'event_fired_after' and trigger['event_id'] in times:
        return times[trigger['event_id']] + trigger['delay_ticks'] * STEP_NS
    return None


def heartbeat_id(source, owner):
    return f'heartbeat:{source}:to:{owner}:heartbeat:expected'


def make_network_plan(profile, script, run, original_times, previous_receipts, *, healthy=False):
    episode = episode_from_engine(profile, run['engine'])
    times = event_times(run)
    primary, actors, mechanism = profile['primary_station'], profile['actor_ids'], profile['mechanism']
    flows, messages, controls = [], [], []
    duration = episode['duration_ns']
    backup_discovery_ns = None
    if mechanism == 'backup_station' and 'uav_link_response' in times:
        owner, = actors
        move = next(a for e in script['events'] if e['event_id'] == 'uav_link_response'
                    for a in e['actions'] if a['type'] == 'move_entity' and a['entity_id'] == owner)
        target = move['waypoints_enu_m'][-1]
        observed_arrivals = [r['tick'] for r in run['engine'].trajectory_rows
            if r['entity_id'] == owner and r['tick'] * STEP_NS > times['uav_link_response']
            and all(abs(x-y) < 1e-5 for x,y in zip(r['pos_enu'], target))]
        if observed_arrivals:
            backup_discovery_ns = (observed_arrivals[0] + 2) * STEP_NS
    # Backup station cannot know actor-local arrival. Its reply is sent only
    # after actual request RX below; no unused remote heartbeat is invented.
    for station in (primary,):
        if station == profile.get('backup_station') and backup_discovery_ns is None:
            continue
        for actor in actors:
            life = owner_node(episode, actor, 0)
            end = duration if life['death_ns'] is None else life['death_ns']
            start = backup_discovery_ns if station == profile.get('backup_station') else life['birth_ns']
            flows.append(flow(episode, heartbeat_id(station, actor), station, actor, start, end))

    def radio(owner, t, operation, channel=0):
        if t is not None and t < duration:
            node = owner_node(episode, owner, t)
            controls.append({'owner': owner, 'life_epoch': node['life_epoch'], 'time_ns': t,
                             'operation': operation, 'channel_number': channel})

    def message(mid, source, receiver, t, evidence_ns, action):
        if t is not None and t < duration:
            m = message_flow(episode, profile, mid, source, receiver, t, evidence_ns, action)
            messages.append(m)
            flows.append(m['flow'])

    def accepted(mid):
        item = previous_receipts.get(mid)
        return None if item is None else item['accepted']

    def remote(event_id, source, receiver, *, required_request=None):
        planned = due_time(script, event_id, times)
        if planned is None:
            return
        evidence = max(0, planned - STEP_NS)
        if required_request is not None:
            request = accepted(required_request)
            if request is None:
                return
            evidence = request['accepted_ns']
            planned = max(planned, evidence + STEP_NS)
        message(f'command:{event_id}:{receiver}', source, receiver, planned, evidence, event_id)

    if mechanism == 'local_c2' and not healthy:
        radio(primary, original_times['c2_loss'], 'off')
        radio(primary, original_times['c2_recovered'], 'on')
    elif mechanism == 'backup_station':
        if not healthy:
            radio(primary, original_times['station_degraded'], 'off')
        actor, = actors
        request_id = f'backup-request:{actor}'
        if backup_discovery_ns is not None:
            message(request_id, actor, profile['backup_station'], backup_discovery_ns,
                    backup_discovery_ns - STEP_NS, 'request_backup_path')
        remote('patrol_resume_via_backup_link', profile['backup_station'], actor,
               required_request=request_id)
    elif mechanism == 'rain_service_descent' and not healthy:
        radio(primary, times.get('c2_loss_tick'), 'off')
        radio(primary, times.get('x1_recovery'), 'on')
    elif mechanism == 'cochannel_backup':
        switch_ns = due_time(script, 'alternate_channel', times)
        if not healthy:
            start = original_times['wideband_jamming']
            end = duration if switch_ns is None else switch_ns
            if start < end:
                # One declared peer UDP workload, sharing the existing Yans channel.
                flows.append(flow(episode, 'cochannel:peer-workload', actors[0], actors[1], start, end,
                                  payload_bytes=1200, period_ns=1_200_000))
        if switch_ns is not None:
            for owner in [primary, *actors]:
                radio(owner, switch_ns, 'channel', profile['backup_channel'])
                message(f'command:alternate_channel:{owner}', primary, owner,
                        switch_ns + STEP_NS, switch_ns, 'alternate_channel') if owner != primary else None
        for actor in actors:
            remote(f'lifecycle_landing_{actor}', primary, actor)
    elif mechanism == 'pad_service':
        if not healthy:
            radio(primary, original_times['station_failure'], 'off')
            radio(primary, times.get('station_recovered'), 'on')
        contention = times.get('dual_uav_pad_contention')
        if contention is not None:
            for actor in actors:
                message(f'pad-request:{actor}', actor, profile['pad_owner'], contention + STEP_NS,
                        contention, 'request_pad')
        arbitration = times.get('pad_priority_arbitration')
        if arbitration is not None:
            for actor in actors:
                request = accepted(f'pad-request:{actor}')
                if request is not None:
                    send = max(arbitration + profile['service_delay_ns'], request['accepted_ns'] + STEP_NS)
                    message(f'pad-result:{actor}', profile['pad_owner'], actor, send,
                            arbitration, 'priority_granted' if actor == actors[0] else 'reroute')
        for actor in actors:
            remote(f'lifecycle_landing_{actor}', primary, actor)
    else:
        if mechanism not in ('local_c2', 'rain_service_descent'):
            raise ValueError('undeclared native mechanism')
    return episode, flows, messages, controls


class Admission:
    """One run-local decision table. No instance state survives another run."""
    def __init__(self, profile, monitors, receipts, episode=None):
        self.profile, self.monitors, self.receipts = profile, monitors, receipts
        self.episode = episode
        self.state_rows, self.transitions = [], []
        self.last = {}
        self.weather = None
        self.pose_prefix = []
        self.pad_requests = None

    def monitor(self, owner, tick, station=None):
        station = self.profile['primary_station'] if station is None else station
        fid = heartbeat_id(station, owner)
        exact = [rows for (receiver, _, flow_id), rows in self.monitors.items()
                 if receiver == owner and flow_id == fid]
        if len(exact) != 1 or tick < 1 or tick > len(exact[0]):
            return None
        return exact[0][tick - 1]  # strict availability before this decision tick

    def observe(self, tick, engine, interpreter, current_rows):
        self.pose_prefix.append((tick, copy.deepcopy(current_rows)))
        self.pose_prefix = self.pose_prefix[-3:]
        if self.profile['mechanism'] in ('rain_service_descent', 'pad_service'):
            available = [rows for sample_tick, rows in self.pose_prefix if sample_tick + 1 < tick]
            # Proximity is observed one original tick later, strictly before
            # this barrier. No future/current global geometry enters the rule.
            interpreter.entity_states.clear()
            if available:
                for row in available[-1]:
                    interpreter.update_entity_state(row['entity_id'], row['pos_enu'], {}, row['vel_mps'])
        if self.profile['mechanism'] == 'pad_service':
            owners = [owner for owner in self.profile['actor_ids']
                if self.received_fact(f'pad-request:{owner}', self.profile['pad_owner'], tick)['allow']]
            states = interpreter.event_states
            arbitration = states['pad_priority_arbitration']
            if arbitration.fired and (tick-arbitration.last_fired_tick)*STEP_NS >= self.profile['service_delay_ns']:
                owners = [self.profile['priority_order'][0]]
            if states['station_recovered'].fired:
                owners = []
            if owners != self.pad_requests:
                engine._handle_set_runtime_state({'type': 'set_runtime_state',
                    'action_id': 'p09_received_pad_requests', 'entity_id': self.profile['pad_owner'],
                    'state_patch': {'facility_state': {'request_count': len(owners), 'requester_ids': owners}}}, tick)
                self.pad_requests = owners
        self.weather = available_weather(engine.weather_rows, tick * STEP_NS)
        if self.profile['mechanism'] in ('local_weather', 'rain_service_descent'):
            # The selected scenarios use actual numeric weather; omitted fields
            # stay missing and the admission gate preserves UNKNOWN.
            interpreter.update_weather_state({} if self.weather is None else self.weather['weather'])
        for owner in self.profile['actor_ids']:
            row = self.monitor(owner, tick)
            if row is None:
                continue
            scoped = {**row, 'decision_tick': tick, 'available_ns': row['observation_ns']}
            self.state_rows.append(scoped)
            bad = row['degraded']
            if owner not in self.last or self.last[owner] != bad:
                self.transitions.append(scoped)
                self.last[owner] = bad
                # Receiver state and rule evaluations are materialized in the
                # declared sidecar namespace below. The old engine's global
                # communication_unavailable field has a different source and
                # is neither relabelled nor used to admit actor events.

    def command(self, mid, owner, tick):
        item = self.receipts.get(mid)
        receipt = None if item is None else item['accepted']
        if receipt is None:
            return {'allow': False, 'evidence': {'message_id': mid, 'receipt': None, 'availability': 'UNKNOWN'}}
        now = tick * STEP_NS
        node = owner_node(self.episode, owner, now)
        if not controller_receipts_before([receipt], now, receiver_owner=owner,
                                          receiver_epoch=node['life_epoch']):
            return {'allow': False, 'evidence': {'message_id': mid, 'receipt': receipt,
                'current_receiver_epoch': node['life_epoch'], 'reason': 'not available to exact current owner/epoch before barrier'}}
        # Receipt availability uses strict now above. The helper's open cutoff
        # now+1 only admits the executor slot AT now; it reads no extra record.
        decisions, _ = consume_received_commands([receipt], observation_ns=now + 1,
            receiver_owner=owner, receiver_epoch=node['life_epoch'],
            policy_delay_ns=self.profile['command']['policy_delay_ns'], queue_available_ns=now)
        allow = any(d['status'] == 'ready_for_executor' for d in decisions)
        return {'allow': allow, 'evidence': {'message_id': mid, 'receipt': receipt,
                                            'controller_decisions': decisions}}

    def received_fact(self, mid, owner, tick):
        """Durable service facts accepted before their delivery deadline."""
        item = self.receipts.get(mid)
        receipt = None if item is None else item['accepted']
        now = tick * STEP_NS
        node = owner_node(self.episode, owner, now)
        ready = () if receipt is None else controller_receipts_before(
            [receipt], now, receiver_owner=owner, receiver_epoch=node['life_epoch'])
        return {'allow': bool(ready), 'evidence': {'message_id': mid, 'receipt': receipt,
            'current_receiver_epoch': node['life_epoch'], 'use_ns': now,
            'service_policy': self.profile['service_record_policy'], 'custody_claim': False}}

    def bad(self, tick, *, healthy=False):
        rows = [self.monitor(owner, tick) for owner in self.profile['actor_ids']]
        allow = all(row is not None and row['degraded'] is (False if healthy else True) for row in rows)
        return {'allow': allow, 'evidence': {'rule_version': 'p09.actor-heartbeat-watchdog/v1',
            'rows': rows, 'required': 'healthy' if healthy else 'degraded',
            'decision_ns': tick * STEP_NS, 'authority': 'local onboard', 'physical_motion_evidence': None}}

    def gate(self, event, tick, engine, interpreter):
        event_id, mechanism = event['event_id'], self.profile['mechanism']
        if mechanism == 'local_c2':
            if event_id == 'c2_loss':
                return self.bad(tick)
            if event_id == 'c2_recovered':
                return self.bad(tick, healthy=True)
        elif mechanism == 'backup_station':
            if event_id == 'p09_actor_observed_station_loss':
                return self.bad(tick)
            if event_id == 'patrol_resume_via_backup_link':
                owner, = self.profile['actor_ids']
                return self.command(f'command:{event_id}:{owner}', owner, tick)
            if event_id.startswith('lifecycle_landing_'):
                return {'allow': True, 'evidence': {'authority': self.profile['landing_authority'],
                    'predecessor': 'patrol_resume_via_backup_link', 'decision_ns': tick * STEP_NS}}
        elif mechanism == 'local_weather':
            if event_id == 'rain_condition_met':
                guard = self.profile['rain_guard']
                evidence = [consecutive_rain(engine.weather_rows, tick * STEP_NS, owner,
                    threshold=guard['threshold'], count=guard['consecutive_samples'])
                    for owner in self.profile['weather_owner_ids']]
                return {'allow': all(e['predicate'] is True for e in evidence), 'evidence': {'rows': evidence}}
        elif mechanism == 'rain_service_descent':
            if event_id == 'rain_threshold':
                return {'allow': self.weather is not None and self.weather['weather'].get('rain') is not None,
                        'evidence': {'local_weather': self.weather, 'legacy_sustain_semantics_retained': True}}
            if event_id == 'c2_loss_after_rain':
                return self.bad(tick)
        elif mechanism == 'cochannel_backup':
            if event_id == 'multi_uav_hold_entry':
                return self.bad(tick)
            if event_id == 'alternate_channel':
                decisions = [self.command(f'command:{event_id}:{owner}', owner, tick)
                             for owner in self.profile['actor_ids']]
                return {'allow': all(d['allow'] for d in decisions), 'evidence': {'owners': decisions}}
            if event_id.startswith('lifecycle_landing_'):
                owner = event_id.removeprefix('lifecycle_landing_')
                return self.command(f'command:{event_id}:{owner}', owner, tick)
        elif mechanism == 'pad_service':
            if event_id == 'dual_uav_pad_contention':
                return self.bad(tick)
            if event_id == 'pad_priority_arbitration':
                decisions = [self.received_fact(f'pad-request:{owner}', self.profile['pad_owner'], tick)
                             for owner in self.profile['actor_ids']]
                return {'allow': all(d['allow'] for d in decisions), 'evidence': {'requests': decisions,
                    'service_policy': self.profile['priority_order'], 'custody_claim': False}}
            if event_id == 'second_uav_reroute':
                owner = self.profile['actor_ids'][1]
                return self.received_fact(f'pad-result:{owner}', owner, tick)
            if event_id.startswith('lifecycle_landing_'):
                owner = event_id.removeprefix('lifecycle_landing_')
                return self.command(f'command:{event_id}:{owner}', owner, tick)
        return {'allow': True, 'evidence': {'authority': 'authored exogenous/local event',
                                          'source_event': event_id, 'decision_ns': tick * STEP_NS}}


def pose_difference(old_rows, new_rows):
    old = {(r['entity_id'], r['tick']): r for r in old_rows}
    changed = {}
    for row in new_rows:
        before = old.get((row['entity_id'], row['tick']))
        fields = ['pos_enu', 'vel_mps', 'yaw_deg', 'state', 'activity_type']
        difference = fields if before is None else [f for f in fields if before.get(f) != row.get(f)]
        if difference and row['entity_id'] not in changed:
            changed[row['entity_id']] = {'first_changed_tick': row['tick'], 'fields': difference,
                'before': None if before is None else {f: before.get(f) for f in difference},
                'after': {f: row.get(f) for f in difference}}
    new_keys = {(r['entity_id'], r['tick']) for r in new_rows}
    for key in old.keys() - new_keys:
        changed.setdefault(key[0], {'first_changed_tick': key[1], 'fields': ['presence_removed']})
    return changed


def run_one(scenario, seed, output_root, provider=None, *, healthy=False):
    from .linked_engine import execute_linked
    profile, scene, script = load_contract(ROOT, scenario, seed)
    output = Path(output_root) / profile['episode_id'] / ('healthy' if healthy else 'adopted')
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'scene_setup.json', scene)
    write_json(output / 'event_script.json', script)
    write_json(output / 'adoption_profile.json', profile)
    script_path = output / 'event_script.json'
    # A source replay is a numerical initialization and comparison only.
    source_path = ROOT / profile['source_script']
    original_script = json.loads(source_path.read_text())
    original = execute_linked(scene, original_script, source_path, profile['episode_id'], seed=seed)
    acquisition = profile.get('motion_acquisition')
    dwell = None if acquisition is None else acquisition['landing_dwell_ticks']
    bootstrap = execute_linked(scene, script, script_path, profile['episode_id'], seed=seed,
                               landing_dwell_ticks=dwell)
    if profile['mechanism'] == 'local_weather':
        admission = Admission(profile, {}, {})
        run = execute_linked(scene, script, script_path, profile['episode_id'], seed=seed,
            decision_gate=admission.gate, observe_tick=admission.observe,
            action_filter=lambda a, t: filtered_source_action(a, t, profile=profile))
        history, final_network, final_receipts = [], None, {}
    else:
        if provider is None:
            raise ValueError('native mechanisms require an explicit live provider')
        radio_config = json.loads(RADIO_SOURCE.read_text())['radio']
        previous, receipts, prior_signature, history = bootstrap, {}, None, []
        started = time.perf_counter()
        for iteration in range(12):
            episode, flows, messages, controls = make_network_plan(profile, script, previous,
                event_times(original), receipts, healthy=healthy)
            config = {'network_profile': 'timed_flow', 'network': {'traffic_windows': flows},
                      'seed': profile['ns3_seed'], 'run': profile['ns3_run'], 'radio': radio_config,
                      'radio_actions': controls, 'radio_config_source_ref': str(RADIO_SOURCE)}
            folder = output / f'iteration{iteration:02d}'
            folder.mkdir()
            write_json(folder / 'run_config.json', config)
            network = provider.run_episode(episode, config, folder)
            monitor = receiver_monitors(episode, flows, network, profile)
            receipts = message_receipts(episode, messages, network)
            admission = Admission(profile, monitor, receipts, episode)
            run = execute_linked(scene, script, script_path, profile['episode_id'], seed=seed,
                decision_gate=admission.gate, observe_tick=admission.observe,
                action_filter=lambda a, t: filtered_source_action(a, t, profile=profile),
                landing_dwell_ticks=dwell)
            signature = {'events': event_times(run), 'flows': flows, 'radio_actions': controls,
                'receipts': {k: None if v['accepted'] is None else v['accepted']['accepted_ns']
                             for k, v in receipts.items()}}
            history.append({'iteration': iteration, 'event_times_ns': event_times(run),
                'packet_count': len(network['network_packets']),
                'accepted_messages': signature['receipts'],
                'native_wall_time_s': network['wall_time_s']})
            write_json(folder / 'iteration_receipt.json', history[-1])
            if signature == prior_signature:
                final_network, final_receipts = network, receipts
                break
            previous, prior_signature = run, signature
        else:
            write_json(output / 'not_adopted.json', {'reason': 'coupled event/flow/receipt fixed point not reached',
                                                     'iterations': history})
            raise RuntimeError(f'coupled solution not adopted: {output}')
        write_json(output / 'network_backend.json', final_network['backend'])
        write_rows(output / 'network_packets.jsonl', final_network['network_packets'])
        write_rows(output / 'network_events.jsonl', final_network['network_events'])
        write_rows(output / 'radio_actions.jsonl', final_network['radio_action_records'])
        write_rows(output / 'receiver_observed_states.jsonl', admission.state_rows)
        write_rows(output / 'predicate_flips.jsonl', admission.transitions)
        write_json(output / 'command_receipts.json', final_receipts)
        write_json(output / 'coupled_iterations.json', history)
        write_json(output / 'execution_time.json', {'wall_time_s': time.perf_counter() - started})
    differences = pose_difference(original['engine'].trajectory_rows, run['engine'].trajectory_rows)
    write_rows(output / 'trajectories.jsonl', run['engine'].trajectory_rows)
    write_rows(output / 'weather.jsonl', run['engine'].weather_rows)
    write_json(output / 'actions.json', run['audit'])
    write_json(output / 'event_admission.json', run['gate_evidence'])
    times = event_times(run)
    missing = [e['event_id'] for e in script['events'] if e['event_id'] not in times]
    summary = {'schema_version': REVISION, 'episode_id': profile['episode_id'], 'seed': seed,
        'scenario_id': scenario, 'source_refs': [profile['source_script'], profile['source_scene']],
        'guard_version': profile.get('rain_guard', {}).get('schema_version'),
        'healthy_control': healthy, 'event_times_ns': times, 'unfired_events': missing,
        'pose_and_visual_differences_from_same_seed_source_replay': differences,
        'weather_changed': original['engine'].weather_rows != run['engine'].weather_rows,
        'adoption_class': 'A' if differences or original['engine'].weather_rows != run['engine'].weather_rows else 'B',
        'published210_modified': False, 'collection_status': 'one-shot collection pending final freeze',
        'iteration_count': len(history), 'backend': None if final_network is None else final_network['backend']['name'],
        'duration_ticks': run['engine'].duration_ticks,
        'motion_acquisition': acquisition,
        'output_dir': str(output), 'motion_scope': 'authored waypoint engine; not PX4 motion feedback',
        'missing_event_disposition': 'healthy-control events remain conditional' if healthy else 'requires resolution before adoption' if missing else 'complete',
        'rf_weather_scope': profile['rain_rf']}
    write_json(output / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=GROUPS, required=True)
    parser.add_argument('--seed', type=int, choices=(0, 1, 2), required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--healthy', action='store_true')
    args = parser.parse_args()
    if GROUPS[args.scenario] == 'local_weather':
        run_one(args.scenario, args.seed, args.output_root, healthy=args.healthy)
    else:
        with ProviderAdapter() as provider:
            run_one(args.scenario, args.seed, args.output_root, provider, healthy=args.healthy)


if __name__ == '__main__':
    main()
