"""Prepare the 0.1 two-leg parcel scene from declared native city inputs."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import shutil
import time
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aero_bench.serialization import canonical_json_bytes
from aero_bench.config.models import AgentSpec, EnvironmentSpec, TaskSpec
from aero_bench.providers.logistics_business.native_parcel import NativeParcelBusinessConfig
from aero_bench.tasks.logistics.contracts import lower_logistics_task_package
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.native_parcel_contract import ATTACHMENT_FRAME_NOTE, SEALED_VERIFIER_REPLAY_REQUIREMENT
from aero_bench.tasks.logistics.bundle_builder import LogisticsBundleRequest, build_logistics_bundle
from aero_bench.world.contracts import WorldPackageContent, WorldPackage, asset_digest_value, world_digest_value
from aero_bench.world.scene_compiler import SceneOrigin

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', required=True, type=Path)
parser.add_argument('--image-lock', required=True, type=Path)
parser.add_argument('--calibration', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
BASE = args.source.resolve()
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=False)
SOURCE = OUT / 'authored-source'
start = time.monotonic()
shutil.copytree(BASE, SOURCE)
images = {item['key']: item for item in json.loads(args.image_lock.read_bytes())['built']}

def ref(path):
    return {'path': path, 'sha256': hashlib.sha256((SOURCE / path).read_bytes()).hexdigest()}

def put(path, document):
    p = SOURCE / path
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix == '.yaml': p.write_text(yaml.safe_dump(document, sort_keys=True))
    else: p.write_bytes(canonical_json_bytes(document) + b'\n')
    return ref(path)

def bound(path, document, schema):
    return {'file': put(path, document), 'schema_file': put('schemas/' + Path(path).stem + '-parcel.json', schema)}

def workload(key, resources):
    return {'runtime': {'image': images[key]['image'], 'command': ['serve' if key == 'harness' else 'provider' if key in ('flight', 'business', 'traffic') else 'verify' if key == 'verifier' else 'run'] + (['serve'] if key in ('flight', 'business', 'traffic') else [])},
            'resources': resources, 'implementation': images[key]['implementation']}

def rename(value):
    if isinstance(value, dict): return {key: rename(val) for key, val in value.items()}
    if isinstance(value, list): return [rename(val) for val in value]
    return {'uav.inspector': 'uav.p02.carrier', 'uav.inspector/body': 'uav.p02.carrier/body'}.get(value, value) if isinstance(value, str) else value

world = rename(json.loads((BASE / 'world/package.json').read_bytes()))
world.pop('world_digest'); world.pop('asset_digest')
world['sensors'] = []
for requirement in world['provider_requirements']:
    if requirement['provider_id'] == 'flight':
        requirement['required_capability_ids'] = [c for c in requirement['required_capability_ids'] if c not in ('camera.rgb', 'observation.capture')]
        requirement['roles'] = ['motion']
world['semantic_targets'] = []; world['mission_requirements'] = []; world['expected_public_assets'] = []
world['provider_requirements'] = [p for p in world['provider_requirements'] if p['provider_id'] != 'business']
world['provider_requirements'].append({'provider_id': 'logistics.native-business', 'required_capability_ids': ['logistics.parcel.authority'], 'roles': ['mission']})
world['provider_requirements'].sort(key=lambda p: p['provider_id'])
# Pickup is a hub endpoint, preserving the canonical order's required hub.
facilities = [
    {'id': 'facility.p02.pickup', 'name': 'Parcel pickup hub', 'kind': 'hub', 'placement': 'ground', 'buildingId': None, 'supportHeightM': None,
     'position': {'x': -450.0, 'z': 451.155}, 'rotationDeg': 0, 'widthM': 16, 'depthM': 12, 'heightM': 6,
     'landing': {'parkingSlots': 1, 'movementsPerHour': 20}, 'cargo': {'storageCapacityKg': 100, 'throughputPerHourKg': 50}, 'charging': None},
    {'id': 'facility.p02.dropoff', 'name': 'Parcel destination pad', 'kind': 'vertiport', 'placement': 'ground', 'buildingId': None, 'supportHeightM': None,
     'position': {'x': -420.0, 'z': 449.92}, 'rotationDeg': 0, 'widthM': 12, 'depthM': 8, 'heightM': 4,
     'landing': {'parkingSlots': 1, 'movementsPerHour': 30}, 'cargo': None, 'charging': None},
]
fleet = [{'id': 'uav.p02.carrier', 'assetId': 'model:holybro-x500', 'count': 1, 'homeFacilityId': 'facility.p02.dropoff', 'batteryWh': 20000, 'reserveRatio': 0.2, 'maxPayloadKg': 5}]
profiles = [{'fleetEntryId': 'uav.p02.carrier', 'sourceLabel': 'Declared parcel demonstration profile', 'provenance': 'Existing authored logistics profile; no measured energy claim',
             'aircraftBody': {'xM': 0.6, 'yM': 0.6, 'zM': 0.3}, 'cruiseSpeedMps': 15, 'cruisePowerW': 900, 'hoverPowerW': 700, 'chargeEfficiency': 0.85}]
carrier = 'uav.p02.carrier:1'
package_doc = {'schema_version': 'aero-bench.logistics-task/v1', 'package_id': 'logistics.task.v1', 'task_id': 'logistics.native-parcel.v1', 'verifier_id': 'logistics.parcel.verifier',
               'scene': {'scene_id': world['world_id'], 'coordinate_frame': 'scene_east_south_m', 'origin_latitude_deg': 31.2288, 'origin_longitude_deg': 121.481, 'origin_altitude_m': 50.0, 'scene_source_sha256': asset_digest_value(WorldPackageContent.model_validate(world))},
               'facilities': facilities, 'fleet': fleet, 'performanceProfiles': profiles,
               'orders': [{'id': 'order.p02.single', 'sourceFacilityId': 'facility.p02.pickup', 'destinationFacilityId': 'facility.p02.dropoff', 'hubHandoffFacilityId': None, 'cargoKg': 1, 'releaseAtS': 0, 'deliverByS': 300}],
               'noFlyZones': [], 'actors': [{'actor_id': carrier, 'role': 'aircraft_agent'}]}
package = lower_logistics_task_package(package_doc)
pickup = facility_landing_pads(package.facilities.require('facility.p02.pickup'))[0]
dropoff = facility_landing_pads(package.facilities.require('facility.p02.dropoff'))[0]
calibration = args.calibration.resolve()
calibration_bytes = calibration.read_bytes()
calibration_record = json.loads(calibration_bytes)
reference_offset = calibration_record['source_transform']['pose_reference_above_contact_m']
if type(reference_offset) not in (int, float) or not math.isfinite(reference_offset):
    raise ValueError('source calibration must declare a finite signed model pose offset')
for entity in world['entities']:
    if entity['entity_id'] == 'uav.p02.carrier':
        entity['pose'].update(east_m=dropoff.x, north_m=-dropoff.z, up_m=dropoff.y + reference_offset)
world['launch_sites'][0]['pose'].update(east_m=dropoff.x, north_m=-dropoff.z, up_m=dropoff.y + reference_offset)
for pad in (pickup, dropoff):
    world['launch_sites'].append({
        'launch_site_id': f'launch_pad.{pad.facility_id}.{pad.pad_index}',
        'primary_uav_entity_id': 'uav.p02.carrier',
        'allowed_uav_entity_ids': ['uav.p02.carrier'],
        'pad_radius_m': min(pad.width_m, pad.depth_m) / 2,
        'pose': {**world['launch_sites'][0]['pose'], 'east_m': pad.x,
                 'north_m': -pad.z, 'up_m': pad.y + reference_offset},
    })
content = WorldPackageContent.model_validate(world)
world.update(asset_digest=asset_digest_value(content), world_digest=world_digest_value(content))
WorldPackage.model_validate(world)
world_ref = put('world/package.json', world)
policy = {'schema_version': 'aero-bench.native-parcel-policy/v1', 'vehicle_id': 'uav.p02.carrier', 'carrier_actor_id': carrier, 'order_id': 'order.p02.single', 'parcel_id': 'parcel.p02.single',
          'pickup_east_m': pickup.x, 'pickup_north_m': -pickup.z, 'destination_east_m': dropoff.x, 'destination_north_m': -dropoff.z, 'cruise_up_m': 20, 'dwell_s': 2}
policy_ref = put('agent/native-parcel-policy.json', policy)
(SOURCE / 'configs/pose-calibration.json').write_bytes(calibration_bytes)
schema_version = 'aero-bench.logistics-native-parcel-contract/v1'
quaternion = {'qw': 1.0, 'qx': 0.0, 'qy': 0.0, 'qz': 0.0}
tolerances = {'vertical_tolerance_m': 0.15, 'horizontal_uncertainty_m': 0.1, 'max_stationary_speed_m_s': 0.2}
contract = {'schema_version': schema_version, 'contract_id': 'native.parcel.p02.single',
            'identities': {'schema_version': schema_version, 'task_id': 'logistics.native-parcel.v1', 'order_id': 'order.p02.single', 'parcel_entity_id': 'parcel.p02.single', 'carrier_entity_id': carrier, 'authorized_principal_id': 'participant.agent', 'pickup_facility_id': pickup.facility_id, 'dropoff_facility_id': dropoff.facility_id},
            'carrier': {'schema_version': schema_version, 'carrier_entity_id': carrier, 'fleet_entry_id': 'uav.p02.carrier', 'visual_asset_id': 'model:holybro-x500', 'provider_id': 'flight', 'native_vehicle_id': 'uav.p02.carrier', 'pose_reference_above_contact_m': reference_offset},
            'attachment': {'schema_version': schema_version, 'parcel_entity_id': 'parcel.p02.single', 'carrier_entity_id': carrier, 'offset_x_m': 0.0, 'offset_y_m': -0.2, 'offset_z_m': 0.0, 'orientation': quaternion, 'frame_note': ATTACHMENT_FRAME_NOTE},
            'policy': {'schema_version': schema_version, 'minimum_pickup_dwell_s': 2.0, 'minimum_dropoff_dwell_s': 2.0, **tolerances}, 'sealed_verifier_replay_requirement': SEALED_VERIFIER_REPLAY_REQUIREMENT}
native = {'schema_version': 'aero-bench.logistics-native-business/v1', 'provider_id': 'logistics.native-business', 'scheduled_orders': [], 'task_package': package.model_dump(mode='json'),
          'principal_bindings': [{'principal_id': 'participant.agent', 'actor_id': carrier, 'role': 'aircraft_agent'}],
          'observation': {'schema_version': 'aero-bench.logistics-observation-config/v1',
                          'bindings': {'schema_version': 'aero-bench.logistics-bindings/v1', 'aircraft': [{'aircraft_id': carrier, 'fleet_entry_id': 'uav.p02.carrier', 'visual_asset_id': 'model:holybro-x500', 'provider_id': 'flight', 'vehicle_id': 'uav.p02.carrier', 'controlling_agent_id': 'participant.agent'}], 'principals': []},
                          'pose_references': [{'aircraft_id': carrier, 'pose_reference_above_contact_m': reference_offset}], 'tolerances': tolerances,
                          'spec': {'schema_version': 'aero-bench.logistics-observation-spec/v1', 'items': [{'aircraft_id': carrier, 'facility_id': pad.facility_id, 'pad_index': pad.pad_index} for pad in (pickup, dropoff)]}},
          'native_parcel': {'schema_version': 'aero-bench.native-parcel-session/v1', 'contract': contract, 'pickup_pad': pickup.model_dump(mode='json'), 'dropoff_pad': dropoff.model_dump(mode='json'),
                            'initial_parcel_pose': {'x_m': pickup.x, 'y_m': pickup.y, 'z_m': pickup.z, 'orientation': quaternion}, 'final_parcel_pose': {'x_m': dropoff.x, 'y_m': dropoff.y, 'z_m': dropoff.z, 'orientation': quaternion},
                            'step_ns': 1000000000, 'max_steps': 300, 'calibration_digest': hashlib.sha256(calibration_bytes).hexdigest()}}
config = NativeParcelBusinessConfig.model_validate(native)
env = rename(yaml.safe_load((BASE / 'environment/environment.yaml').read_text()))
env['clock'].update(step_ns=1000000000, max_steps=300)
env['harness'] = workload('harness', env['harness']['resources'])
flight = next(p for p in env['providers'] if p['provider_id'] == 'flight')
flight['capabilities'] = [c for c in flight['capabilities'] if c not in ('camera.rgb', 'observation.capture')]
flight['artifact_requirements'] = [a for a in flight['artifact_requirements'] if a['artifact_type'] == 'trajectory']
flight_doc = rename(json.loads((BASE / 'configs/px4.json').read_bytes()))
flight_doc['vehicles'][0]['initial_pose'].update(x_m=dropoff.x, y_m=-dropoff.z, z_m=dropoff.y + reference_offset)
flight['config']['file'] = put('configs/px4.json', flight_doc)
flight['workload'] = workload('flight', flight['workload']['resources'])
traffic = next(p for p in env['providers'] if p['provider_id'] == 'traffic')
traffic_path = traffic['config']['file']['path']
traffic_doc = rename(json.loads((BASE / traffic_path).read_bytes()))
traffic_doc['step_length_ns'] = env['clock']['step_ns']
traffic['config']['file'] = put(traffic_path, traffic_doc)
traffic['workload'] = workload('traffic', traffic['workload']['resources'])
business_artifact = {'artifact_id': 'artifact.native-parcel-state', 'artifact_type': 'logistics.business.state', 'producer_id': 'logistics.native-business', 'visibility': 'private', 'relative_path': 'business/native-parcel-state.json', 'max_size_bytes': 134217728, 'source_asset_id': None}
provider = {'provider_id': 'logistics.native-business', 'adapter': 'logistics.native-parcel', 'port': 17435,
            'workload': workload('business', {'cpu_millicores': 1000, 'memory_mib': 4096, 'gpu_count': 0}),
            'config': bound('configs/native-business.json', config.model_dump(mode='json'), NativeParcelBusinessConfig.model_json_schema()),
            'protocol_schema': ref('schemas/provider-protocol.json'), 'capabilities': ['logistics.facilities.state', 'logistics.orders.authority', 'logistics.parcel.authority'], 'artifact_requirements': [business_artifact]}
env['providers'] = [p for p in env['providers'] if p['provider_id'] != 'business'] + [provider]
env['providers'].sort(key=lambda p: p['provider_id'])
env['harness_artifact_requirements'] = [a for a in env['harness_artifact_requirements'] if a['artifact_type'] in ('event.log', 'scene.state-history')]
env_ref = put('environment/environment.yaml', EnvironmentSpec.model_validate(env).model_dump(mode='json'))
agent = yaml.safe_load((BASE / 'agent/participant.yaml').read_text())
agent['workload'] = workload('agent', agent['workload']['resources'])
agent['tools'] = [grant for grant in agent['tools'] if grant['tool_id'].startswith('flight.')]
parcel_request = put('schemas/parcel-command.json', {'type': 'object', 'additionalProperties': False, 'required': ['actor_id', 'order_id', 'parcel_id'], 'properties': {key: {'type': 'string'} for key in ('actor_id', 'order_id', 'parcel_id')}})
for action in ('pickup', 'dropoff'):
    agent['tools'].append({'tool_id': 'logistics.parcel.' + action, 'provider_id': 'logistics.native-business', 'request_schema': parcel_request, 'response_schema': ref('schemas/tool-response.json'), 'timeout_ms': 180000, 'idempotent': True})
agent['tools'].sort(key=lambda g: g['tool_id'])
agent['queries'] = []; agent['observations'] = []; agent['artifact_requirements'] = []
agent_ref = put('agent/participant.yaml', AgentSpec.model_validate(agent).model_dump(mode='json'))
task = yaml.safe_load((BASE / 'task/task.yaml').read_text())
task['task_id'] = 'logistics.native-parcel.v1'
task['package'] = {'package_id': 'logistics.task.native-parcel.v1', 'config': bound('task/native-parcel-package.json', package_doc, {'type': 'object', 'additionalProperties': False, 'required': list(package_doc), 'properties': {key: {} for key in package_doc}})}
task['required_capabilities'] = ['flight.command', 'logistics.facilities.state', 'logistics.orders.authority', 'logistics.parcel.authority']
task['required_tools'] = [grant['tool_id'] for grant in agent['tools']]
task['assets'] = [{'asset_id': 'asset.native-parcel-policy', 'file': policy_ref, 'classification': 'public', 'audiences': [{'role': 'agent', 'workload_ids': ['participant.agent']}]}]
task['goals'] = [{'goal_id': goal, 'verifier_id': 'logistics.parcel.verifier', 'metric_id': goal, 'operator': 'ge', 'threshold': 1.0, 'evidence': 'artifact', 'parameters': []} for goal in ('parcel.pickup', 'parcel.transport', 'parcel.dropoff', 'parcel.delivered', 'carrier.terminal')]
verifier = task['verifier']; verifier['verifier_id'] = 'logistics.parcel.verifier'
verifier['workload'] = workload('verifier', verifier['workload']['resources'])
verifier['config'] = bound('configs/native-verifier.json', {'schema_version': 'aero-bench.native-parcel-verifier/v1', 'business_provider_id': 'logistics.native-business'}, {'type': 'object', 'additionalProperties': False, 'required': ['schema_version', 'business_provider_id'], 'properties': {'schema_version': {'const': 'aero-bench.native-parcel-verifier/v1'}, 'business_provider_id': {'const': 'logistics.native-business'}}})
verifier['artifact_requirements'] = [a for a in verifier['artifact_requirements'] if (a['producer_id'] == 'harness' and a['artifact_type'] in ('event.log', 'scene.state-history')) or (a['producer_id'] == 'flight' and a['artifact_type'] == 'trajectory')] + [business_artifact]
for requirement in verifier['output_artifacts']: requirement['producer_id'] = 'logistics.parcel.verifier'
task_ref = put('task/task.yaml', TaskSpec.model_validate(task).model_dump(mode='json'))
suite = yaml.safe_load((BASE / 'suite.yaml').read_text())
suite['suite_id'] = 'logistics.native-parcel.0.1'
suite['cases'][0].update(case_id='logistics.native-parcel.p02.single', task=task_ref, environment=env_ref, agents=[agent_ref], world_package=world_ref)
put('suite.yaml', suite)
origin = SceneOrigin(latitude_deg=31.2288, longitude_deg=121.481, ellipsoid_height_m=50, geoid_undulation_m=30, amsl_m=20)
result = build_logistics_bundle(LogisticsBundleRequest(source_root=SOURCE, output_root=OUT / 'native-bundle', base_world_sdf=SOURCE / next(a['artifact']['selector'] for a in world['assets'] if a['artifact']['artifact_id'] == next(b['scene_asset_id'] for b in world['engine_frame_bindings'] if b['engine'] == 'gazebo')), origin=origin))
print(json.dumps({'bundle': str(result.bundle_root), 'world_sha256': result.world_sha256, 'elapsed_s': time.monotonic() - start}), flush=True)
