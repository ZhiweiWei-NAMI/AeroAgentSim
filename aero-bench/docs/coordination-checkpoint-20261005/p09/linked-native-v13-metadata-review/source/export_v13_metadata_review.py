"""Bounded current-output metadata repair and missing raw evidence export.

No ns-3 rerun, inference, UE capture or published episode replacement.
All source simulation receipts remain immutable v12 evidence.
"""
import copy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

PROJECT = Path('/mnt/data2/weizhiwei/AERO_WORLD')
BASE = Path('/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09')
DEST = BASE / 'linked_native_v13_metadata_review'
PRIVATE = BASE / 'linked_native_v13_metadata_artifacts'
BATCH = BASE / 'linked_native_v12_remaining'
FROZEN = BASE / 'linked_native_v12_review'
sys.path.insert(0, str(PROJECT))
from Dataset.semantic_simulation.ns3_episode.linked_contract import load_contract
from Dataset.semantic_simulation.ns3_episode.linked_metadata import retire_terminal_planning_proofs
from Dataset.semantic_simulation.ns3_episode.batch_status import persisted_episode_status


def load(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False); handle.write('\n')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1048576),b''):
            value.update(chunk)
    return value.hexdigest()


def native_evidence(folder, target):
    target.mkdir()
    profile = load(folder / 'adoption_profile.json')
    flow_ids, heartbeat_rows = set(), []
    if (folder / 'network_packets.jsonl').exists():
        with (folder / 'network_packets.jsonl').open() as source:
            for line in source:
                row = json.loads(line)
                if row['flow_id'].startswith('heartbeat:'):
                    heartbeat_rows.append(row); flow_ids.add(row['flow_id'])
        with (target / 'heartbeat_native_packets.jsonl').open('x') as out:
            for row in heartbeat_rows: out.write(json.dumps(row, separators=(',', ':'))+'\n')
        for name in ('receiver_observed_states.jsonl', 'event_admission.json', 'radio_actions.jsonl'):
            shutil.copy2(folder / name, target / name)
        iterations = load(folder / 'coupled_iterations.json')
        iteration = folder / ('iteration' + str(iterations[-1]['iteration']).zfill(2))
        # Exact final per-flow and per-owner radio configuration, no PHY claims inferred from labels.
        if not (iteration / 'run_config.json').is_file():
            raise ValueError('Final coupled iteration radio configuration absent: '+str(iteration))
        shutil.copy2(iteration / 'run_config.json', target / 'final_native_run_config.json')
        write(target / 'heartbeat_scope.json', {'source':str(folder/'network_packets.jsonl'),
            'source_sha256':digest(folder/'network_packets.jsonl'), 'raw_complete_heartbeat_rows':len(heartbeat_rows),
            'exact_flow_ids':sorted(flow_ids), 'expected_slot_policy':profile['heartbeat'],
            'actor_ids':profile['actor_ids'],
            'radio_status':'Actual final run configuration; cochannel UDP competition is not wideband jamming',
            'rendezvous_status':'Preloaded absolute schedule, not local backup observation'
                if 'rendezvous' in profile else 'No rendezvous declared in this scenario profile'})


def cached_clearance_provenance(folder, review, profile, actions):
    """Exclude an old annotation without rewriting cached native action records."""
    geometry = profile['backup_contact_geometry']
    annotations = []
    for i, row in enumerate(actions):
        action = row['action']
        if action['action_id'] not in ('move_uav_backup_loiter', 'move_uav_resume_patrol'):
            continue
        old = action['uav_corridor_validation']
        if old['profile_version'] != 'p09.l2-1-v2.backup-contact-route/v1':
            raise ValueError('Unexpected cached clearance annotation version')
        segments = [x for x in geometry['spatial_check']['segments']
                    if x['action_id'] == action['action_id']]
        if not segments:
            raise ValueError('Current geometry receipt has no matching action segments')
        annotations.append({'action_id': action['action_id'], 'actual_dispatch_tick': row['tick'],
            'cached_annotation_pointer': f'/actions.json/{i}/action/uav_corridor_validation',
            'cached_annotation': old, 'disposition': 'HISTORICAL_EXCLUDED_FROM_CURRENT_CLEARANCE_PROOF',
            'current_receipt': 'adoption_profile.json#/backup_contact_geometry/spatial_check',
            'current_profile_version': geometry['version'],
            'current_action_segments': segments})
    if len(annotations) != 2:
        raise ValueError('Expected exactly two cached L2v2 clearance annotations')
    return {'schema_version': 'p09.cached-clearance-provenance/v1',
        'source_native_actions': str(folder / 'actions.json'),
        'source_native_actions_sha256': digest(folder / 'actions.json'),
        'exported_actions_sha256': digest(review / 'actions.json'),
        'current_profile_sha256': digest(review / 'adoption_profile.json'),
        'annotations': annotations, 'cached_motion_and_receipts_rewritten': False,
        'ns3_rerun': False,
        'meaning': 'The cached v1 Boolean annotation is not current checker evidence. The separate v2-metadata fitted-source geometry receipt is current; native motion records remain historical execution evidence.'}


def metadata_episode(folder, review, runtime):
    old_summary, old_profile = load(folder/'summary.json'), load(folder/'adoption_profile.json')
    episode = old_summary['episode_id']; scenario = old_summary['scenario_id']; seed = old_summary['seed']
    profile, scene, script = load_contract(PROJECT, scenario, seed)
    old_script, old_scene = load(folder/'event_script.json'), load(folder/'scene_setup.json')
    before_moves = {a['action_id']:a['waypoints_enu_m'] for e in old_script['events'] for a in e['actions'] if a['type']=='move_entity'}
    after_moves = {a['action_id']:a['waypoints_enu_m'] for e in script['events'] for a in e['actions'] if a['type']=='move_entity'}
    if before_moves != after_moves:
        raise ValueError('Metadata-only migration changed an actual motion action: '+episode)
    runtime.mkdir(); review.mkdir()
    for name, value in [('scene_setup.json',scene),('event_script.json',script),('adoption_profile.json',profile)]:
        write(runtime/name,value); shutil.copy2(runtime/name,review/name)
    actions = load(folder/'actions.json'); late_bounds=[]; executed_landing=[]
    for row in actions:
        action = row['action']
        if 'terminal_feasibility' in action:
            old = copy.deepcopy(action['terminal_feasibility'])
            if row['result']['status']=='ok' and row['tick']>old['dispatch_upper_bound_tick']:
                late_bounds.append({'action_id':action['action_id'],'owner':action['entity_id'],
                    'old_dispatch_upper_bound_tick':old['dispatch_upper_bound_tick'],'actual_dispatch_tick':row['tick']})
            retire_terminal_planning_proofs({'events':[{'event_id':'actual-audit:'+action['action_id'],'actions':[action]}]},profile)
            if row['result']['status']=='ok':
                executed_landing.append({'action_id':action['action_id'],'owner':action['entity_id'],
                    'dispatch_tick':row['tick'],'actual_motion_schedule':row['motion_schedule'],
                    'planning_claim':'No new preflight upper-bound proof; actual execution receipt only',
                    'operating_inputs':action['terminal_feasibility']})
    write(runtime/'actions.json', actions);shutil.copy2(runtime/'actions.json',review/'actions.json')
    if scenario == 'L2-1_v2':
        sidecar = cached_clearance_provenance(folder, review, profile, actions)
        write(review / 'cached_clearance_provenance.json', sidecar)
        write(runtime / 'cached_clearance_provenance.json', sidecar)
    new_entities={e['entity_id']:e for e in scene['entities']}
    old_entities={e['entity_id']:e for e in old_scene['entities']}
    corridors=profile.get('route_metadata_migration',{}).get('changed_corridor_entities',[])
    changed_rows, physical_rows, selected_rows, all_terminal, extended_rows = 0,0,[],[],[]
    causal_ticks={0,old_summary['duration_ticks']}
    for tick_ns in old_summary['event_times_ns'].values():
        t=tick_ns//100_000_000;causal_ticks.update((t-2,t-1,t,t+1,t+2))
    receipts=load(folder/'command_receipts.json') if (folder/'command_receipts.json').exists() else {}
    def sample_times(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in ('sample_ns','available_ns') and type(child) is int:
                    t=child//100_000_000;causal_ticks.update((t-1,t,t+1))
                sample_times(child)
        elif isinstance(value,list):
            for child in value:sample_times(child)
    sample_times(receipts)
    modified=scenario=='L2-1_v2'
    out=(runtime/'trajectories.jsonl').open('x') if modified else None
    with (folder/'trajectories.jsonl').open() as source:
        for line in source:
            row=json.loads(line); before=copy.deepcopy(row); owner=row['entity_id']
            if modified and owner==profile['actor_ids'][0]:
                for field in ('route_waypoints_enu_m','planned_route_waypoints_enu_m'):
                    row[field]=copy.deepcopy(new_entities[owner][field])
            if modified and owner in corridors:
                placement=new_entities[owner]['placement']
                if before['pos_enu'] != old_entities[owner]['placement']['resolved_position_enu_m']:
                    raise ValueError('Corridor unexpectedly moved in source execution')
                row['pos_enu']=copy.deepcopy(placement['resolved_position_enu_m'])
                row['yaw_deg']=placement['rotation_deg']['yaw_deg']
            if row != before: changed_rows += 1
            if owner not in corridors:
                for field in ('pos_enu','vel_mps','yaw_deg','state','activity_type'):
                    if row[field] != before[field]: raise ValueError('Metadata migration changed physical telemetry')
                physical_rows += 1
            if out:out.write(json.dumps(row,separators=(',',':'))+'\n')
            if row['tick'] in causal_ticks and row['label_class']!='airspace_corridor':selected_rows.append(row)
            if row['tick']>=old_summary['duration_ticks']-4:all_terminal.append(row)
            if scenario.startswith('L6-4') and row['tick']>900:extended_rows.append(row)
    if out:out.close()
    write(review/'causal_boundary_poses.json',selected_rows)
    write(review/'whole_scene_last_five_ticks.json',all_terminal)
    if scenario.startswith('L6-4'):write(review/'whole_scene_extended_suffix.json',extended_rows)
    comparison={'schema_version':'p09.metadata-only-cached-receipt-migration/v1',
        'episode_id':episode,'simulation_source_revision':old_summary['schema_version'],
        'metadata_revision':profile['schema_version'],'source_output':str(folder),
        'source_trajectory_sha256':digest(folder/'trajectories.jsonl'),
        'current_trajectory':str(runtime/'trajectories.jsonl') if modified else str(folder/'trajectories.jsonl'),
        'current_trajectory_sha256':digest(runtime/'trajectories.jsonl') if modified else digest(folder/'trajectories.jsonl'),
        'changed_trajectory_rows':changed_rows,'physical_rows_verified_unchanged':physical_rows,
        'numeric_motion_actions_unchanged':True,'ns3_rerun':False,'provider_endpoint_poses_unchanged':True,
        'render_changes':corridors,'route_field_owner':profile['actor_ids'][0] if modified else None,
        'landing_execution_receipts':executed_landing,'late_historical_planning_bounds':late_bounds,
        'actual_unfired_events':old_summary['unfired_events'],
        'adoption_status':'BLOCKED_UNFIRED_EVENTS' if old_summary['unfired_events'] else 'METADATA_REPAIRED_PENDING_NATIVE_REVIEW',
        'published210_modified':False,'UE_capture_started':False}
    write(review/'metadata_migration.json',comparison);write(runtime/'metadata_migration.json',comparison)
    return comparison


def main():
    DEST.mkdir(exist_ok=False);PRIVATE.mkdir(exist_ok=False)
    progress=load(BATCH/'progress.json'); corrected=copy.deepcopy(progress)
    for item in corrected['completed']:
        item.update(persisted_episode_status(Path(item['summary']),item['scenario'],item['seed']))
    write(DEST/'outer_batch_persisted_status.json',corrected)
    folders=[FROZEN/'X5_comm_failure_to_pad_contention__seed00/adopted']+[Path(x['summary']).parent for x in corrected['completed']]
    if len(folders)!=30:raise ValueError('Expected the exact frozen 30 episodes')
    reports=[]
    for folder in folders:
        episode=folder.parent.name
        reports.append(metadata_episode(folder,DEST/episode,PRIVATE/episode))
        if (folder/'network_packets.jsonl').exists():native_evidence(folder,DEST/episode/'native_raw')
    write(DEST/'30_metadata_resolution.json',reports)
    write(DEST/'terminal_planning_bound_resolution.json',{'late_bounds':sum(len(x['late_historical_planning_bounds']) for x in reports),
        'affected_episodes':sum(bool(x['late_historical_planning_bounds']) for x in reports),
        'episodes':[{'episode_id':x['episode_id'],'rows':x['late_historical_planning_bounds']} for x in reports if x['late_historical_planning_bounds']],
        'policy':'Historical proof excluded; retained physical landing inputs; actual execution is a separate receipt, not a planning certificate'})
    l2=DEST/'l2_existing_phy_arp_diagnostics';l2.mkdir()
    inventory=[]
    for revision in ('linked_native_v10','linked_native_v11','linked_native_v12_remaining'):
        seeds=(0,1,2) if revision.endswith('remaining') else (0,)
        for version in (1,2):
            for seed in seeds:
                folder=BASE/revision/f'L2-1_v{version}__seed{seed:02d}'/'adopted'
                if not folder.exists():continue
                target=l2/revision/folder.parent.name;target.mkdir(parents=True)
                names=('network_packets.jsonl','network_events.jsonl','network_diagnostics.jsonl','network_backend.json',
                       'adoption_profile.json','command_receipts.json','summary.json')
                present=[];missing=[]
                for name in names:
                    if (folder/name).exists():shutil.copy2(folder/name,target/name);present.append(name)
                    else:missing.append(name)
                inventory.append({'revision':revision,'episode':folder.parent.name,'source':str(folder),
                    'present':present,'missing':missing,'meaning':'Existing actual rows only; provider callback metadata is not evidence that a particular ARP/PHY callback fired'})
    write(l2/'inventory.json',inventory)
    source=DEST/'source';source.mkdir()
    for name in ('linked_metadata.py','linked_route_geometry.py','linked_backup.py','linked_contract.py','linked_replay.py',
                 'batch_status.py','test_batch_status.py','test_linked_backup.py'):
        shutil.copy2(PROJECT/'Dataset/semantic_simulation/ns3_episode'/name,source/name)
    shutil.copy2(Path(__file__),source/Path(__file__).name)
    shutil.copy2(BASE/'run_remaining_v12.py',source/'run_remaining_v12.py')
    write(DEST/'scope.json',{'source_packet':'65423f411d2f51e3ccbec758ee38b843c5f3ebf6',
        'scope':'Exact same v12 native executions with v13 derived metadata only; no new native generation or UE capture',
        'episodes':len(reports),'two_unfired_L6_preserved':True,'final210_frozen':False,'ARM_replayed':False,
        'missing_old_L2_v10_diagnostic_rows':'network_diagnostics.jsonl was not persisted; no rerun or inferred raw ARP evidence provided',
        'clock':'original tick=100000000ns; receipt/availability preserved in integer absolute ns',
        'sensor_index':'Source scene cameras/map/placements are exact here; actual UE/ARM capture clock/extrinsics linkage remains unresolved, no certification'})
    # The raw rows retain every field; compact JSON whitespace for a bounded review packet.
    for path in DEST.rglob('*.json'):
        if path.stat().st_size > 200_000:
            value = load(path)
            path.write_text(json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n')
    entries=[{'path':str(p.relative_to(DEST)),'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(DEST.rglob('*')) if p.is_file()]
    write(DEST/'manifest.json',entries)
    print(json.dumps({'output':str(DEST),'files':len(entries)+1,'bytes':sum(x['bytes'] for x in entries),
        'late_bounds':sum(len(x['late_historical_planning_bounds']) for x in reports),
        'affected_episodes':sum(bool(x['late_historical_planning_bounds']) for x in reports)},indent=2))


if __name__=='__main__':main()
