#!/usr/bin/env python3
"""Materialize the explicitly derived authored-ground-streets/v1 design."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aero_bench.world.ground_roads import audit_ground_network
from city_authored_ground_streets import PROFILE, plan_authored_streets, read_fleet_widths
from city_preview_coordinates import CityEnuProjection, load_canonical_city_geometry
from city_road_topology import lane_kind
from city_road_physical_clearance import lane_building_conflicts
from city_ground_fleet import fleet_width_contract
from city_ground_junction_completion import collapsed_passthrough_pads, consumed_crop_remnants, crop_rectangle, road_end_completion
from audit_native_road_physical import NETCONVERT_PRECISION_OPTIONS

SUMO_IMAGE = "sha256:6974eeb6110526b9f65c6766ff725cefa9bd48e856ebed6fd828b3c3ea6d80cd"


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)+'\n')


def build(network_path, source_path, objects_path, render_path, pack_path, fleet_path, output,
          *, common_scene_path, netconvert_options=(), sidewalk_overrides=None):
    network_path, source_path, objects_path, render_path, pack_path, fleet_path, output, common_scene_path = [Path(path).resolve()
        for path in (network_path, source_path, objects_path, render_path, pack_path, fleet_path, output, common_scene_path)]
    network, source = ET.parse(network_path).getroot(), ET.parse(source_path).getroot()
    geometry = load_canonical_city_geometry(objects_path, render_path, pack_path)
    input_context = geometry.source_context(network_path, source_path)
    projection = CityEnuProjection(network, geometry.origin)
    source_proof = audit_ground_network(network_path.read_bytes(), source_path.read_bytes(), expected_projection=projection.projection)
    fleet_document = json.loads(fleet_path.read_text())
    if fleet_document != fleet_width_contract():
        raise ValueError('Authored street widths differ from the actual declared native/displayed fleet contract')
    patch, plan = plan_authored_streets(network, source, geometry, projection, read_fleet_widths(fleet_document), sidewalk_overrides)
    crop = crop_rectangle(json.loads(common_scene_path.read_text()), geometry.origin)
    output.mkdir(parents=True, exist_ok=True)
    if (output/'network.net.xml').exists(): raise FileExistsError(output/'network.net.xml')
    plan.update({'source_context':input_context, 'fleet_widths_sha256':sha(fleet_path), 'fleet_widths':fleet_document,
        'crop_source':{'path':str(common_scene_path),'sha256':sha(common_scene_path),'enu_bounds_m':crop}})
    base_command=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,nosuid,size=256m',
        '--mount',f'type=bind,source={network_path.parent},target=/input,readonly',
        '--mount',f'type=bind,source={output},target=/output','--entrypoint','netconvert',SUMO_IMAGE,
        '--sumo-net-file',f'/input/{network_path.name}','--edge-files','/output/authored-streets.edg.xml',
        '--node-files','/output/authored-junctions.nod.xml','--connection-files','/output/authored-crossings.con.xml',
        '--offset.disable-normalization','--walkingareas','true',*NETCONVERT_PRECISION_OPTIONS,'--output-file','/output/network.net.xml']
    # Consumed crop remnants and pass-through pads depend on the materialized native geometry,
    # so each is applied as a further native pass; each rule may fire at most once.
    pads, pad_records, remnant_records, passes = [], [], [], []
    for native_pass in range(3):
        junction_nodes, junction_connections, junction_records = road_end_completion(network, plan['designed'], projection, crop)
        junction_nodes.extend(pads)
        designed_ids={item['edge_id'] for item in plan['designed']}
        for element in [element for element in patch if element.get('id') not in designed_ids]: patch.remove(element)
        ET.indent(patch, space='  ')
        ET.ElementTree(patch).write(output/'authored-streets.edg.xml',encoding='utf-8',xml_declaration=True)
        (output/'excluded-edge-ids.txt').write_text('\n'.join(sorted(item['edge_id'] for item in plan['excluded']))+'\n')
        for element,name in ((junction_nodes,'authored-junctions.nod.xml'),(junction_connections,'authored-crossings.con.xml')):
            ET.indent(element, space='  '); ET.ElementTree(element).write(output/name,encoding='utf-8',xml_declaration=True)
        command=list(base_command)
        if plan['excluded']: command.extend(['--remove-edges.input-file','/output/excluded-edge-ids.txt'])
        command.extend(netconvert_options)
        (output/'network.net.xml').unlink(missing_ok=True)
        with (output/'netconvert.log').open('w' if native_pass == 0 else 'a') as log: subprocess.run(command,check=True,stdout=log,stderr=subprocess.STDOUT)
        final_net=ET.parse(output/'network.net.xml').getroot()
        final_projection=CityEnuProjection(final_net, geometry.origin)
        crop_nodes={row['junction_id'] for row in junction_records if row['rule']=='crop-cut-road-end'}
        remnants=consumed_crop_remnants(final_net, plan['designed'], crop_nodes, final_projection)
        new_pads, new_pad_records = collapsed_passthrough_pads(final_net, final_projection)
        passes.append({'pass':native_pass,'network_sha256':sha(output/'network.net.xml'),
            'consumed_crop_remnants':[row['edge_id'] for row in remnants],'collapsed_pass_through':[row['junction_id'] for row in new_pad_records]})
        if remnants:
            if remnant_records: raise ValueError('Excluding consumed crop remnants exposed further consumed remnants')
            removed={row['edge_id'] for row in remnants}
            # A road is removed in both directions: one direction alone would be a one-way remnant.
            removed|={item['edge_id'] for item in plan['designed'] if item['edge_id'].startswith('-') and item['edge_id'][1:] in removed
                      or '-'+item['edge_id'] in removed}
            evidence={row['edge_id']:row for row in remnants}
            for item in [item for item in plan['designed'] if item['edge_id'] in removed]:
                plan['designed'].remove(item)
                plan['excluded'].append({'edge_id':item['edge_id'],'source_way_id':item['source_way_id'],
                    'original_osm_way_id':item['original_osm_way_id'],'source_tags':item['source_tags'],
                    'reason':'crop_remnant_consumed_by_junction','native_evidence':evidence.get(item['edge_id']),
                    'detail':'the in-crop part of this road lies inside its junction; netconvert cannot place its lanes on the designed spine'})
            remnant_records.extend(remnants)
            continue
        if new_pads:
            if pads: raise ValueError('Junction pads did not give every pass-through a drawable internal lane')
            pads, pad_records = new_pads, new_pad_records
            continue
        break
    else:
        raise ValueError('Native junction completion did not converge')
    write_json(output/'netconvert-command.json',command)
    write_json(output/'authored-ground-streets-plan.json',plan)
    write_json(output/'junction-completion.json',{'schema_version':'aero-bench.ground-junction-completion/v1',
        'records':junction_records+pad_records+remnant_records,'native_passes':passes})
    final_ids={edge.attrib['id'] for edge in final_net.findall('edge') if edge.get('function','normal')=='normal'}
    expected_ids={item['edge_id'] for item in plan['designed']}
    if final_ids != expected_ids: raise ValueError(f'Native authored edge inventory differs from the design: {final_ids ^ expected_ids}')
    designed={item['edge_id']:item for item in plan['designed']}
    # Netconvert must preserve each explicit designed cross section and class restriction.
    for edge in final_net.findall('edge'):
        if edge.attrib['id'] not in designed: continue
        expected={str(item['lane_index']):item for item in designed[edge.attrib['id']]['lanes']}
        actual={lane.attrib['index']:lane for lane in edge.findall('lane')}
        if expected.keys()!=actual.keys(): raise ValueError('Native authored lane inventory differs from the design')
        for index,item in expected.items():
            lane=actual[index]
            if abs(float(lane.get('width'))-item['width_m'])>1e-8 or set(lane.get('allow','').split()) != set(item['allowed']):
                raise ValueError('Native authored width or class permission differs from the design')
    retained_parent_ids={item['source_way_id'] for item in plan['designed']}
    removed_ways=[]
    for way in list(source.findall('way')):
        if way.attrib['id'] not in retained_parent_ids:
            removed_ways.append(way.attrib['id']); source.remove(way)
        else:
            ET.SubElement(way,'tag',k='aero_bench:authored_ground_profile',v=PROFILE)
            ET.SubElement(way,'tag',k='aero_bench:authored_ground_surveyed',v='no')
    ET.indent(source, space='  ')
    ET.ElementTree(source).write(output/'authored-ground.osm',encoding='utf-8',xml_declaration=True)
    closure=audit_ground_network((output/'network.net.xml').read_bytes(),(output/'authored-ground.osm').read_bytes(),expected_projection=projection.projection)
    write_json(output/'ground-network-proof.json',closure)
    engineering={'schema_version':'aero-bench.urban-engineering-traffic-inputs/v1','road_scope':'ground-only',
        'projection':projection.projection,'net_offset':'0.00,0.00','net_offset_required':'0,0',
        'source_network':str(network_path),'source_network_sha256':sha(network_path),
        'original_source_osm':str(source_path),'original_source_osm_sha256':sha(source_path),
        'source_osm':str(output/'authored-ground.osm'),'source_osm_sha256':sha(output/'authored-ground.osm'),
        'filtered_source_osm':'authored-ground.osm','filtered_source_osm_sha256':sha(output/'authored-ground.osm'),
        'network_sha256':sha(output/'network.net.xml'),'ground_network_audit_module_sha256':sha(audit_ground_network.__code__.co_filename),
        'network_generation':'real native netconvert materialization of explicit authored-ground-streets/v1',
        'network_closure':'ground-network-proof.json','authored_street_plan':'authored-ground-streets-plan.json',
        'authored_street_plan_sha256':sha(output/'authored-ground-streets-plan.json'),
        'source_engineering_inputs_sha256':sha(network_path.parent/'engineering-inputs.json'),
        'sumo_image_id':SUMO_IMAGE,'derived_geometry_is_not_surveyed':True,'netconvert_options':list(netconvert_options),
        'fleet_widths_sha256':sha(fleet_path),'unsupported_source_parent_ways':removed_ways,
        'crop_source_sha256':sha(common_scene_path),'junction_completion':'junction-completion.json',
        'junction_completion_sha256':sha(output/'junction-completion.json')}
    write_json(output/'engineering-inputs.json',engineering)
    final_projection=CityEnuProjection(final_net,geometry.origin)
    lanes=[]
    for edge in final_net.findall('edge'):
        function=edge.get('function','normal')
        for lane in edge.findall('lane'):
            points=final_projection.shape(lane.get('shape'))
            lanes.append({'id':lane.attrib['id'],'width':float(lane.get('width','3.2')),'function':function,
                'kind':'walk' if function in {'walkingarea','crossing'} else lane_kind(lane),'shape':points})
    physical=lane_building_conflicts(final_net,source,lanes,geometry.footprints,final_projection)
    write_json(output/'native-physical-clearance.json',{'source_context':geometry.source_context(output/'network.net.xml',output/'authored-ground.osm'),
        'native_lane_clearance':physical})
    result={'schema_version':'aero-bench.city-authored-ground-streets-result/v1','profile':PROFILE,
        'source_normal_edges':plan['original_normal_edge_count'],'final_normal_edges':len(final_ids),'excluded_edges':len(plan['excluded']),
        'network_sha256':sha(output/'network.net.xml'),'source_osm_sha256':sha(output/'authored-ground.osm'),
        'source_ground_counts':source_proof['source_counts'],'physical_status':physical['status'],
        'physical_pair_count':physical['pair_count'],'unrenderable_generated_area_count':len(physical['unrenderable_generated_areas']),
        'final_real_recording':'PENDING_NATIVE_PHYSICAL_AND_DISPLAYED_SURFACE_PASS'}
    write_json(output/'authored-ground-streets-result.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('network','source-osm','objects','render','pack','fleet-widths','output'): parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--netconvert-option',action='append',default=[],help='Extra netconvert argument (repeatable), recorded in engineering inputs')
    parser.add_argument('--sidewalk-overrides',type=Path,help='JSON {edge_id: {"level": 1|2, "reason": ...}} from native walking-area feedback')
    parser.add_argument('--common-scene',type=Path,required=True,help='Urban common-scene document declaring this region\'s ENU crop rectangle')
    args=parser.parse_args()
    overrides=json.loads(args.sidewalk_overrides.read_text()) if args.sidewalk_overrides else None
    print(json.dumps(build(args.network,args.source_osm,args.objects,args.render,args.pack,args.fleet_widths,args.output,
        common_scene_path=args.common_scene,netconvert_options=tuple(args.netconvert_option),sidewalk_overrides=overrides)))
