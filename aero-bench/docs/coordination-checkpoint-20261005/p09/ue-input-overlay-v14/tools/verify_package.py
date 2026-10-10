"""Bounded verification of assembled files only; no data generation."""
import hashlib,json,pathlib,sys
import orjson
root=pathlib.Path(sys.argv[1]).resolve();m=json.load((root/'assembly_manifest.json').open())
errors=[];checked=[];total_entity_rows=0
window_report=json.load((root/'capture_window_report.json').open())
partition_by_id={row['episode_id']:row['byte_preserving_partitions'] for row in window_report['episodes']}
for ep in m['episodes']:
 d=root/pathlib.Path(ep['ue_entry']).parent
 pkg=json.load((d/'scenario_package.json').open());cfg=json.load((d/'render_host_config.json').open());mep=json.load((d/'episode_manifest.json').open())
 assert pkg['episode_id']==ep['episode_id'] and cfg['episode_dir']==str(d.relative_to(root))
 for k in ['truth_frames','weather_meta','scenario_plan','episode_manifest','scene_occupancy_manifest','event_realization','scene_setup','event_script']:
  p=(root/pkg[k]).resolve();assert p.is_relative_to(root) and p.is_file(),(ep['episode_id'],k,p)
 assert cfg['event_script_path']==pkg['event_script']
 assert cfg['output_dir']=='G:/aw_cap/_direct_render_host_capture_filtered_v14/'+ep['episode_id']
 window=json.load((root/pkg['p09_capture_support_window']).open())
 assert window['tick_end_inclusive']==900 and window['planned_capture_ticks']==list(range(0,901,5))
 assert window['planned_capture_frames']==181 and window['capture_rate_hz']==2
 assert mep['duration_ticks']==900 and mep['time_range']['tick_end']==900
 assert window['outside_window']['multimodal_support_mask'] is False
 assert window['outside_window']['not_an_event_negative'] is True
 assert mep['source_scene_setup_path']==pkg['scene_setup']
 assert mep['source_event_script_path']==pkg['event_script']
 for name in ['truth_frames.jsonl','weather_meta.jsonl']:
  count=0;end=None
  with (d/name).open() as f:
   for l in f:
    a=orjson.loads(l);assert a['tick']==count,(ep['episode_id'],name,count,a['tick'])
    if name=='truth_frames.jsonl':
     assert a['tick_hz']==10 and a['dt_s']==0.1 and abs(a['sim_time_s']-count/10)<1e-9
     assert 'p09_background_suffix' not in a,(ep['episode_id'],'missing background entered UE capture',count)
     assert a['sumo_segment'] is not None and a['uav_segment'] is not None,(ep['episode_id'],'missing background binding',count)
     total_entity_rows+=len(a['entities'])
    count+=1;end=a['tick']
  assert end==900 and count==901,(ep['episode_id'],name,end,count)
  assert mep['record_counts'][name.removesuffix('.jsonl')]==count
 for name,part in partition_by_id[ep['episode_id']].items():
  combined=hashlib.sha256();count=0;first=None;last=None
  with (d/name).open('rb') as f:
   for b in iter(lambda:f.read(1048576),b''):combined.update(b)
  with (d/'additional_recorded_suffix'/name).open('rb') as f:
   for line in f:
    combined.update(line);a=orjson.loads(line);last=a['tick']
    if first is None:first=last
    assert last>900;count+=1
  assert combined.hexdigest()==part['original_sha256'],(ep['episode_id'],name,'partition altered bytes')
  assert count==part['suffix_records'] and [first,last]==part['suffix_ticks']
 for src in ep['files']:
  p=root/src['path'];h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(1048576),b''):h.update(b)
  assert p.stat().st_size==src['bytes'] and h.hexdigest()==src['sha256'],p
 checked.append({'episode_id':ep['episode_id'],'all_entry_files_exist':True,'frame_and_weather_clock_complete':True,
                 'source_files_match':True,'story_status':ep['story_status'],
                 'unfired_events':ep['unfired_events'],'ue_capture_tick_range':[0,900],
                 'planned_capture_frames':181,'capture_background_bindings_present':True,
                 'retained_extra_suffix_bytes_verified':bool(partition_by_id[ep['episode_id']]),
                 'technical_window_file_checks_passed':True,
                 'actual_ue_execution_or_sensor_calibration_verified':False})
assert len(checked)==36
print(json.dumps({'schema_version':'p09.input-assembly-verification/v2','episodes_checked':len(checked),'errors':errors,
 'ue_truth_frames':36*901,'planned_capture_grid_frames':36*181,'ue_entity_rows':total_entity_rows,
 'new_simulations':0,'actual_sensor_captures':0,'results':checked},indent=2))
