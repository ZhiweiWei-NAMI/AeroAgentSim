"""Compile once and run the actual isolated ns-3.48 frozen-episode backend."""
from __future__ import annotations
import json
import math
from pathlib import Path
import subprocess
import time
import uuid

DEFAULT_IMAGE = '127.0.0.1:5000/aero-bench/ns3:urban-current'
SOURCE = Path(__file__).with_name('p01_ns3_provider.cc')
RADIO_FIELDS = {
    'channel_number': (1, '1', int),
    'channel_width_mhz': (20, 'MHz', int),
    'standard': ('802.11n', '1', str),
    'data_mode': ('HtMcs0', '1', str),
    'control_mode': ('HtMcs0', '1', str),
    'tx_power_dbm': (16.0, 'dBm', float),
    'tx_gain_db': (0.0, 'dB', float),
    'rx_gain_db': (0.0, 'dB', float),
    'rx_sensitivity_dbm': (-95.0, 'dBm', float),
    'rx_noise_figure_db': (7.0, 'dB', float),
    'logdistance_exponent': (3.0, '1', float),
    'logdistance_reference_distance_m': (1.0, 'm', float),
    'logdistance_reference_loss_db': (46.6777, 'dB', float),
    'mac_max_packets': (500, 'packet', int),
    'mac_max_delay_ns': (500_000_000, 'ns', int),
}
RADIO_STANDARDS = {'802.11b', '802.11g', '802.11n', '802.11ax'}


def default_radio_config():
    """Current assumptions, with source/calibration fields awaiting evidence."""
    return {'schema_version': 'p01.radio-config/v1', 'profile_id': 'p01.wifi.adhoc.v1',
        'parameters': {name: {'value': value, 'unit': unit, 'status': 'proposal_assumption',
            'source': ['Dataset/semantic_simulation/ns3_episode/provider_adapter.py'],
            'assumption': 'Current declared P01 value; not a calibrated low-altitude parameter.',
            'calibration_need': 'Source-backed parameter matrix and scenario-specific calibration pending.'}
            for name, (value, unit, _) in RADIO_FIELDS.items()}}


def resolve_radio_config(document):
    """Resolve one complete typed configuration once, before episode execution."""
    if set(document) != {'schema_version', 'profile_id', 'parameters'}:
        raise ValueError('radio JSON requires exactly schema_version, profile_id, parameters')
    if document['schema_version'] != 'p01.radio-config/v1':
        raise ValueError('undeclared radio configuration version')
    if not isinstance(document['profile_id'], str) or not document['profile_id']:
        raise ValueError('radio profile_id must be a nonempty string')
    parameters = document['parameters']
    if not isinstance(parameters, dict) or set(parameters) != set(RADIO_FIELDS):
        raise ValueError('radio JSON must declare exactly the complete RADIO_FIELDS parameter set')
    statuses = {'observed_current', 'derived_from_current', 'proposal_assumption', 'unknown_current', 'proposed_policy'}
    for name, (_, unit, kind) in RADIO_FIELDS.items():
        row = parameters[name]
        if not isinstance(row, dict) or set(row) != {'value', 'unit', 'status', 'source', 'assumption', 'calibration_need'}:
            raise ValueError(f'{name}: exact value/unit/status/source/assumption/calibration_need fields required')
        value = row['value']
        valid = type(value) is kind if kind is not float else type(value) in (int, float) and math.isfinite(value)
        if not valid or row['unit'] != unit:
            raise ValueError(f'{name}: requires {kind.__name__} value in {unit}')
        if kind is str and (not value or any(character.isspace() for character in value)):
            raise ValueError(f'{name}: requires one nonempty configuration token')
        if row['status'] not in statuses or row['status'] == 'unknown_current':
            raise ValueError(f'{name}: applied parameter must have explicit known status')
        if not isinstance(row['source'], list) or not row['source'] or any(not isinstance(ref, str) or not ref for ref in row['source']):
            raise ValueError(f'{name}: nonempty source reference list required')
        for field in ('assumption', 'calibration_need'):
            if row[field] is not None and not isinstance(row[field], str):
                raise ValueError(f'{name}: {field} must be a string or null')
    if parameters['standard']['value'] not in RADIO_STANDARDS:
        raise ValueError('radio standard must support the retained 2.4GHz band')
    return document


def _period_ns(payload, rate):
    if type(payload) is not int or payload <= 0 or type(rate) is not int or rate <= 0:
        raise ValueError('traffic payload/rate require positive exact integers')
    period, remainder = divmod(payload * 8 * 1_000_000_000, rate)
    if remainder or period <= 0:
        raise ValueError('offered load requires an exact positive integer-nanosecond period')
    return period


def timed_links(episode, windows):
    """Bind owner/epoch once; repeated windows reuse a persistent flow socket."""
    owner_nodes = {(node['owner'],node['life_epoch']):node for node in episode['nodes']}
    grouped = {}
    link_ids = set()
    fields = {'flow_id','link_id','src_owner','life_epoch','payload_bytes','period_ns','start_ns','end_ns'}
    for row in windows:
        if set(row) not in (fields, fields | {'dst_owner', 'dst_life_epoch'}):
            raise ValueError('traffic window requires the exact declared timed-flow fields')
        if any(type(row[key]) is not int for key in ('life_epoch','payload_bytes','period_ns','start_ns','end_ns')):
            raise ValueError('traffic window numeric fields must be exact integers')
        if any(not isinstance(row[key],str) or not row[key] or any(c.isspace() for c in row[key]) for key in ('flow_id','link_id','src_owner')):
            raise ValueError('timed-flow identity fields require exact nonempty tokens')
        node = owner_nodes[(row['src_owner'],row['life_epoch'])]
        if 'dst_owner' in row:
            if not isinstance(row['dst_owner'],str) or not row['dst_owner'] or any(c.isspace() for c in row['dst_owner']):
                raise ValueError('timed-flow dst_owner requires an exact nonempty token')
            if type(row['dst_life_epoch']) is not int or row['dst_life_epoch'] < 0:
                raise ValueError('timed-flow destination epoch requires an exact nonnegative integer')
            destination = owner_nodes[(row['dst_owner'],row['dst_life_epoch'])]
            if destination['node_id'] == node['node_id']:
                raise ValueError('timed flow requires distinct source and destination nodes')
        else:
            destination = None
        end = episode['duration_ns'] if node['death_ns'] is None else node['death_ns']
        if row['payload_bytes']<=0 or row['period_ns']<=0 or not node['birth_ns']<=row['start_ns']<row['end_ns']<=end:
            raise ValueError('timed flow must have positive payload/period and an exact window inside its radio epoch')
        dst_id = episode['gateway_node_id'] if destination is None else destination['node_id']
        if destination is not None:
            dst_end = episode['duration_ns'] if destination['death_ns'] is None else destination['death_ns']
            if not destination['birth_ns'] <= row['start_ns'] < row['end_ns'] <= dst_end:
                raise ValueError('timed flow must be inside its destination radio epoch')
        identity = (row['link_id'],node['node_id'],dst_id,row['payload_bytes'],row['period_ns'])
        if row['flow_id'] in grouped:
            previous = grouped[row['flow_id']]
            if identity != (previous['link_id'],previous['src_node_id'],previous['dst_node_id'],previous['payload_bytes'],previous['period_ns']):
                raise ValueError('persistent flow windows must retain exact source/destination/link/payload/period')
        else:
            if row['link_id'] in link_ids:
                raise ValueError('distinct flows require distinct link IDs')
            link_ids.add(row['link_id'])
            grouped[row['flow_id']] = {'flow_id':row['flow_id'],'link_id':row['link_id'],
                'src_node_id':node['node_id'],
                'dst_node_id':dst_id,
                'traffic_kind':'timed_intervention','payload_bytes':row['payload_bytes'],
                'period_ns':row['period_ns'],'traffic_windows':[]}
        grouped[row['flow_id']]['traffic_windows'].append({'start_ns':row['start_ns'],'end_ns':row['end_ns']})
    links = list(grouped.values())
    for link in links:
        link['traffic_windows'].sort(key=lambda row:row['start_ns'])
        if any(left['end_ns']>right['start_ns'] for left,right in zip(link['traffic_windows'],link['traffic_windows'][1:])):
            raise ValueError('windows of one persistent flow cannot overlap')
    return links


class ProviderAdapter:
    def __init__(self, image=DEFAULT_IMAGE):
        self.image = image
        self.container = 'p01-ns3-' + uuid.uuid4().hex[:12]
        self.compilation_wall_time_s = None
        subprocess.run(['docker','create','--name',self.container,'--user','1007:1007',
            '--network','none','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--read-only','--tmpfs','/tmp:rw,exec,nosuid,nodev,size=1g',
            '--memory','2g','--pids-limit','256','--workdir','/tmp',
            '--entrypoint','sleep',image,'infinity'],check=True,capture_output=True,text=True)
        try:
            subprocess.run(['docker','start',self.container],check=True,capture_output=True,text=True)
            subprocess.run(['docker','exec','-i',self.container,'sh','-c','cat > /tmp/p01_ns3_provider.cc'],
                input=SOURCE.read_text(),check=True,capture_output=True,text=True)
            compile_started = time.perf_counter()
            command = ['docker','exec',self.container,'g++','-std=c++20','-O2',
                '-I/opt/ns-3.48/build/include','-L/opt/ns-3.48/build/lib',
                '-Wl,-rpath,/opt/ns-3.48/build/lib','/tmp/p01_ns3_provider.cc','-o','/tmp/p01_ns3_provider',
                '-lns3.48-wifi-optimized','-lns3.48-internet-optimized',
                '-lns3.48-mobility-optimized','-lns3.48-propagation-optimized',
                '-lns3.48-network-optimized','-lns3.48-core-optimized']
            result = subprocess.run(command,capture_output=True,text=True)
            if result.returncode:
                raise RuntimeError('ns-3 extension compilation failed:\n'+result.stderr)
            self.compilation_wall_time_s = time.perf_counter()-compile_started
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        if self.container is not None:
            result = subprocess.run(['docker','rm','-f',self.container],capture_output=True,text=True)
            self.container = None
            if result.returncode:
                raise RuntimeError('could not remove task-owned ns3 container: '+result.stderr)

    def run_episode(self, episode, config, output_dir, *, geometry_export=None):
        """Return actual packet/socket events; projection/log ownership is external."""
        if self.container is None:
            raise RuntimeError('ProviderAdapter is closed')
        profile = config['network_profile']
        if profile not in ('baseline','controlled_congestion','coverage_load','timed_flow'):
            raise ValueError('undeclared P01 traffic profile')
        duration = episode['duration_ns']
        nodes = episode['nodes']
        by_id = {node['node_id']: node for node in nodes}
        gateway = by_id[episode['gateway_node_id']]
        if gateway['birth_ns'] != 0 or gateway['death_ns'] is not None:
            raise ValueError('P01 requires a continuously present gateway')
        selected = set()
        offered_load = None
        if profile == 'coverage_load':
            traffic = config['network']
            selected = set(traffic['traffic_owner_ids'])
            if len(selected) != 1 or len(traffic['traffic_owner_ids']) != 1:
                raise ValueError('coverage diagnostic requires one explicitly selected traffic owner')
            selected_nodes = [node for node in nodes if node['owner'] in selected]
            if len(selected_nodes) != 1 or selected_nodes[0]['node_type'] != 'uav':
                raise ValueError('coverage traffic owner must resolve to one exact UAV life epoch')
            payload = traffic['payload_bytes']
            rate = traffic['load_bps']
            offered_load = (payload, _period_ns(payload, rate), rate)
        traffic_config = config['network']
        background = set(traffic_config['congestion_uav_owners']) if profile == 'controlled_congestion' else set()
        links = []
        for node in nodes:
            if profile == 'timed_flow':
                continue
            if node['node_type'] != 'uav' or node['birth_ns'] >= duration:
                continue
            if profile == 'coverage_load':
                traffic = [('coverage_load',offered_load[0],offered_load[1])] if node['owner'] in selected else []
            else:
                traffic = [('telemetry',traffic_config['telemetry_payload_bytes'],traffic_config['telemetry_period_ns'])]
            if node['owner'] in background:
                payload = traffic_config['congestion_payload_bytes']
                traffic.append(('congestion',payload,_period_ns(payload,traffic_config['congestion_bps_per_owner'])))
            for kind, payload, period in traffic:
                flow = f"{node['node_id']}:{kind}"
                links.append({'link_id': flow+':to:'+gateway['node_id'],
                    'src_node_id':node['node_id'],'dst_node_id':gateway['node_id'],
                    'flow_id':flow,'traffic_kind':kind,'payload_bytes':payload,'period_ns':period,
                    'traffic_windows':[{'start_ns':node['birth_ns'],'end_ns':duration if node['death_ns'] is None else node['death_ns']}]})
        if profile == 'timed_flow':
            links = timed_links(episode,config['network']['traffic_windows'])
        positions = {node['node_id']: [] for node in nodes}
        for frame in episode['mobility']:
            for row in frame['positions']:
                if row['active']:
                    positions[row['node_id']].append((frame['time_ns'],*row['position_enu_m']))
        lines = [f"{duration} {len(nodes)} {len(links)} {config['seed']} {config['run']}"]
        lines.append(' '.join(str(config['radio']['parameters'][field]['value']) for field in RADIO_FIELDS))
        if geometry_export is None:
            lines.append('0')
        else:
            prior = config['geometry']['configuration']['propagation_prior']
            parts = [(building, polygon) for building in geometry_export['buildings'] for polygon in building['polygons']]
            lines.append(f"1 {prior['n_los']} {prior['n_nlos']} {len(parts)}")
            for building, polygon in parts:
                rings = [polygon['exterior_enu_xy_m'], *polygon['holes_enu_xy_m']]
                lines.append(f"{building['feature_index']} {building['base_enu_m']} {building['roof_enu_m']} {len(rings)}")
                for ring in rings:
                    lines.append(str(len(ring)))
                    lines.extend(' '.join(map(str, point)) for point in ring)
        for node in nodes:
            path = positions[node['node_id']]
            death = duration if node['death_ns'] is None else node['death_ns']
            lines.append(f"{node['node_id']} {node['birth_ns']} {death} {len(path)}")
            lines.extend(' '.join(map(str,point)) for point in path)
        for link in links:
            destination = by_id[link['dst_node_id']]
            lines.append(f"{link['flow_id']} {link['link_id']} {by_id[link['src_node_id']]['ns3_node_id']} "
                f"{destination['ns3_node_id']} {link['payload_bytes']} {link['period_ns']} {len(link['traffic_windows'])}")
            lines.extend(f"{row['start_ns']} {row['end_ns']}" for row in link['traffic_windows'])
        radio_actions = config.get('radio_actions', [])
        controls = []
        opcodes = {'off': 0, 'on': 1, 'channel': 2}
        for action in radio_actions:
            if set(action) != {'owner', 'life_epoch', 'time_ns', 'operation', 'channel_number'}:
                raise ValueError('radio action must declare exact owner/epoch/time/operation/channel')
            matched = [n for n in nodes if (n['owner'], n['life_epoch']) ==
                       (action['owner'], action['life_epoch'])]
            if len(matched) != 1:
                raise ValueError('radio action must resolve to one exact installed life epoch')
            node = matched[0]
            t = action['time_ns']
            end = duration if node['death_ns'] is None else node['death_ns']
            if type(t) is not int or not node['birth_ns'] <= t < end:
                raise ValueError('radio action time must be an integer ns inside its lifetime')
            operation = action['operation']
            channel = action['channel_number']
            if operation not in opcodes or type(channel) is not int:
                raise ValueError('radio action operation/channel is undeclared')
            if channel not in ((1, 6) if operation == 'channel' else (0,)):
                raise ValueError('radio action channel must be 1/6 for switch and 0 otherwise')
            controls.append((t, node['ns3_node_id'], opcodes[operation], channel))
        if len({(t, node) for t, node, _, _ in controls}) != len(controls):
            raise ValueError('one radio action per exact node/time is required')
        lines.append(str(len(controls)))
        lines.extend(' '.join(map(str, row)) for row in sorted(controls))
        started = time.perf_counter()
        result = subprocess.run(['docker','exec','-i',self.container,'/tmp/p01_ns3_provider'],
            input='\n'.join(lines)+'\n',capture_output=True,text=True)
        if result.returncode:
            raise RuntimeError(f'actual ns-3 episode failed (exit {result.returncode}):\n'+result.stderr+'\nstdout tail:\n'+result.stdout[-1600:])
        wall = time.perf_counter()-started
        events, packets, queue_samples, diagnostics, propagation, loaded_radio, geometry_samples, summary = [], {}, [], [], None, [], [], None
        radio_action_records = []
        for line in result.stdout.splitlines():
            row = json.loads(line)
            record_type = row.pop('record_type')
            if record_type == 'event':
                events.append(row)
                if row['event_type'] == 'rx':
                    packet = packets[row['packet_id']]
                    packet['rx_ns'] = row['time_ns']
            elif record_type == 'packet':
                row['rx_ns'] = None
                packets[row['packet_id']] = row
            elif record_type == 'queue_sample':
                queue_samples.append(row)
            elif record_type == 'diagnostic':
                diagnostics.append(row)
            elif record_type == 'propagation':
                propagation = row
            elif record_type == 'radio':
                loaded_radio.append(row)
            elif record_type == 'radio_action':
                radio_action_records.append(row)
            elif record_type == 'geometry_link':
                geometry_samples.append(row)
            elif record_type == 'summary':
                summary = row
            else:
                raise ValueError('ns-3 returned an undeclared output record')
        if summary is None or propagation is None or not loaded_radio:
            raise ValueError('ns-3 did not finish the complete episode')
        if geometry_export is not None:
            building_ids = {row['feature_index']: row['building_id'] for row in geometry_export['buildings']}
            for row in geometry_samples:
                row['building_ids'] = [building_ids[index] for index in row['building_feature_indices']]
                src,dst = by_id[row['src_node_id']],by_id[row['dst_node_id']]
                row.update({'owner':src['owner'],'life_epoch':src['life_epoch'],
                    'receiver_owner':dst['owner'],'receiver_life_epoch':dst['life_epoch']})
        return {'network_events':events,'network_packets':list(packets.values()),'links':links,
            'radio_action_records': radio_action_records,
            'network_queue_samples':queue_samples,'network_diagnostics':diagnostics,
            'geometry_samples':geometry_samples,
            'backend':{'name':summary['provider_version'],'ns3_version':summary['ns3_version'],
                'image':self.image,'source_ref':str(SOURCE),'radio_config':config['radio'],
                'loaded_radio':loaded_radio,
                'propagation_attributes':propagation,'network_profile':profile,
                'geometry': {'enabled': geometry_export is not None,
                    'configuration_ref': None if geometry_export is None else config['geometry']['source_ref'],
                    'support': 'inactive' if geometry_export is None else config['geometry']['applied_support'],
                    'metadata': None if geometry_export is None else geometry_export['metadata'],
                    'initial_application_link_geometry': [row for row in geometry_samples if row['time_ns']==0],
                    'propagation_los_evaluations': summary['propagation_los_evaluations'],
                    'propagation_nlos_evaluations': summary['propagation_nlos_evaluations'],
                    'shadowing_applied': False,
                    'applied_shadowing': False, 'extra_fast_fading': False,
                    'shadowing': None if geometry_export is None else config['geometry']['shadowing']},
                'traffic_selection':{'owner_ids':sorted(selected),'offered_load_bps':None if offered_load is None else offered_load[2],
                    'condition': {'coverage_load':'predeclared coverage-conditioned mechanism diagnostic; one selected source UAV carries UDP load, all source radios and frozen motion retained; no radio calibration claim',
                        'timed_flow':'explicit owner/epoch-bound application windows; persistent sockets remain open after generation stops',
                        'baseline':'original declared wide-area application traffic','controlled_congestion':'original declared wide-area application traffic'}[profile]},
                'diagnostic_observation':'actual PhyTxBegin/retry-bit, PhyTxDrop, PhyRxDrop, DroppedMpdu, MpduResponseTimeout, ArpL3Protocol.Drop and ArpCache.Drop; tagged endpoints per packet, untagged/control/overheard counts aggregated',
                'arp_diagnostic_observation':'protocol/cache Drop callbacks expose packet only, no reason argument; ARP pending occupancy has no public read-only getter and remains unobserved',
                'container_uid_gid':'1007:1007','network':'none','host_mounts':[],
                'compilation_wall_time_s':self.compilation_wall_time_s,
                'simulation_wall_time_s':summary['wall_time_s'],
                'mobility':'WaypointMobilityModel; linear interpolation of frozen 10Hz ENU positions',
                'gateway_position_enu_m':gateway['position_enu_m'],
                'gateway_position_source_ref':episode['source_ref'],
                'gateway_antenna_offset_m':episode['gateway_antenna_offset_m'],
                'queue_observation':'sum of actual device non-QoS and supported EDCA MAC queues; GetNPackets/GetNBytes; 2Hz at t-1ns, initial before t0 applications'},
            'wall_time_s':wall,'peak_rss_kb':summary['peak_rss_kb']}
