"""Register the authored parcel bundle through the existing native scene API."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aero_bench.authoring.native_registry import (
    NativeParcelSceneDefinition, NativeSceneRegistryManifest, native_parcel_traffic_counts,
    verify_native_parcel_scene,
)
from aero_bench.authoring.reference_registration import _references
from aero_bench.authoring.workspace import CityOrderGeneration, WORKSPACE_SCHEMA
from aero_bench.config.loader import BundleReader, load_suite
from aero_bench.config.models import FileRef
from aero_bench.config.resolver import resolve_suite
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.trace.projector import project_public_scenario
from aero_bench.world.contracts import WorldPackage

start = time.monotonic()
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bundle', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
loaded = load_suite(args.bundle / 'suite.yaml')
reader = BundleReader(loaded.root)
run, = resolve_suite(str(loaded.suite_path), executor_kind='docker_reference',
                    provider_registry=builtin_provider_registry(),
                    task_package_resolvers=builtin_task_package_resolvers())
world_ref = loaded.suite.cases[0].world_package
world = WorldPackage.model_validate(reader.load_document(world_ref))
package = reader.validate_schema_bound_file(run.task.package.config)
raw_files = {pin.path: reader.resolve_file(pin).read_bytes()
             for pin in (*_references(loaded.suite.model_dump(mode='json')),
                         *_references(run.model_dump(mode='json')))}
scene_path = '/city-presentation/p02-native-parcel.json'
scene_relative = scene_path.removeprefix('/')
raw_files[scene_relative] = canonical_json_bytes(project_public_scenario(run).model_dump(mode='json'))
output = args.output.resolve()
output.mkdir()
for relative, raw in raw_files.items():
    dest = output / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
def ref(relative):
    return FileRef(path=relative, sha256=hashlib.sha256(raw_files[relative]).hexdigest())
draft = {
    'purpose': 'scenario-authoring', 'schema_version': WORKSPACE_SCHEMA,
    'name': 'Native parcel transport', 'seed': run.seed, 'scenePath': scene_path,
    'environment': {'cloudCover': 0, 'precipitation': 'none', 'precipitationRateMmPerH': 0,
                    'visibilityM': 10000, 'windMps': 0, 'windDirectionDeg': 0,
                    'timeOfDay': 'day', 'reflectionsEnabled': True},
    'fleet': [{k: v for k, v in item.items() if k != 'maxPayloadKg'} for item in package['fleet']],
    'facilities': [{'id': f['id'], 'name': f['name'], 'kind': f['kind'], 'position': f['position'],
                    'rotationDeg': f['rotationDeg'], 'widthM': f['widthM'], 'depthM': f['depthM'],
                    'heightM': f['heightM'], 'capacity': f['landing']['parkingSlots'],
                    'chargingPowerW': 0} for f in package['facilities']],
    'traffic': native_parcel_traffic_counts(world=world, reader=reader),
    'airspace': [], 'algorithms': {'mode': 'centralized', 'assignment': 'external',
        'routing': 'external', 'energy': 'external', 'parameters': {'profile': 'logistics.native-parcel.v1'}},
    'deployment': {'executor': 'docker_reference', 'imageRef': run.agents[0].workload.runtime.image},
    'events': [], 'actionRules': [], 'stateKeyframes': [], 'labelRules': [],
    'orders': package['orders'], 'orderGeneration': CityOrderGeneration.disabled(run.seed).model_dump(mode='json'),
    'performanceProfiles': package['performanceProfiles'], 'authoredLandscape': [],
}
definition = NativeParcelSceneDefinition.model_validate({
    'schema_version': 'aero-bench.native-parcel-scene-registration/v1',
    'registration_id': 'logistics.native-parcel.p02', 'profile_id': 'logistics.native-parcel.v1',
    'scene_path': scene_path, 'suite': run.suite.model_dump(mode='json'),
    'world': world_ref.model_dump(mode='json'), 'world_id': world.world_id, 'world_digest': world.world_digest,
    'scene_document': {'file': ref(scene_relative).model_dump(mode='json'), 'size_bytes': len(raw_files[scene_relative])},
    'files': [{'file': ref(relative).model_dump(mode='json'), 'size_bytes': len(raw)} for relative, raw in sorted(raw_files.items())],
    'reference_draft': draft,
})
verified = verify_native_parcel_scene(output, definition)
(output / 'native-scene.json').write_bytes(canonical_json_bytes(definition.model_dump(mode='json')))
manifest = NativeSceneRegistryManifest(schema_version='aero-bench.native-scene-registry/v1',
    registrations=(FileRef(path='native-scene.json', sha256=hashlib.sha256((output / 'native-scene.json').read_bytes()).hexdigest()),))
(output / 'native-scenes.json').write_bytes(canonical_json_bytes(manifest.model_dump(mode='json')))
(output / 'reference-workspace.json').write_bytes(canonical_json_bytes(definition.reference_draft.snapshot()))
(output / 'registration-public.json').write_bytes(canonical_json_bytes(verified.public().model_dump(mode='json')))
print(json.dumps({'registration': str(output / 'native-scenes.json'), 'run_id': run.run_id,
                  'feasible': run.feasibility.feasible, 'elapsed_s': time.monotonic() - start}), flush=True)
