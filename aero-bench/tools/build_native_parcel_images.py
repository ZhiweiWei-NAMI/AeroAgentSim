#!/usr/bin/env python3
"""Build selected native parcel images and preserve the other pinned identities."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import build_agent_inspection_images as build
from aero_bench.serialization import canonical_json_bytes

COMPONENTS = {
    item.key: item for item in (
        build.Component('harness', 'harness', 'aero-bench.harness', '0.4.0-harness.1'),
        build.Component('flight', 'native-parcel-flight', 'px4.gazebo', '0.4.0-px4-gazebo.1'),
        build.Component('business', 'native-parcel-business', 'logistics.native-parcel', '0.4.0-native-parcel-business.1'),
        build.Component('traffic', 'native-parcel-sumo', 'sumo.traci', '0.4.0-sumo.2'),
        build.Component('verifier', 'native-parcel-verifier', 'logistics.parcel.verifier', '0.1.0-native-parcel'),
        build.Component('agent', 'native-parcel-participant', 'participant.agent', '0.4.0-native-parcel-participant.1'),
    )
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image-lock', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--component', required=True, action='append', choices=tuple(COMPONENTS))
    args = parser.parse_args()
    previous = json.loads(args.base_image_lock.read_bytes())['built']
    identities = {item['key']: item for item in previous}
    if len(identities) != len(previous) or set(identities) != set(COMPONENTS):
        raise ValueError('base image lock must contain each native component exactly once')
    if len(set(args.component)) != len(args.component):
        raise ValueError('duplicate component selection')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = out / 'source'
    source.mkdir()
    build.SOURCE_URI = 'https://github.com/ZhiweiWei-NAMI/AeroAgentSim'
    build._copy_managed_source(source)
    inventory = build._source_inventory(source)
    revision = build._source_revision(inventory)
    context = out / 'build-context.tar.gz'
    build._capture_build_context(source, context)
    (out / 'build-inputs.json').write_bytes(canonical_json_bytes({'source_revision': revision, 'files': inventory}))

    def build_one(key):
        component = COMPONENTS[key]
        repository = f'localhost:5000/aero-bench/{key}'
        tag = f'{repository}:native-parcel-{revision}'
        build._run_logged(['docker', 'build', '--progress=plain', '--tag', tag,
                          '--build-arg', f'AERO_BENCH_REVISION={revision}', '--file',
                          f'containers/{component.directory}/Dockerfile', '-'],
                         out / f'{key}.build.log', build_context=context)
        image_id = build._validate_identity(build._inspect(tag, 'docker'), component, revision)
        build._run_logged(['docker', 'push', tag], out / f'{key}.push.log')
        digest = build._exact_repository_digest(build._inspect(tag, 'docker'), repository)
        print(json.dumps({'component': key, 'image': digest}), flush=True)
        return {'key': key, 'image': digest, 'image_id': image_id, 'implementation': {
            'component_id': component.component_id, 'kind': 'production',
            'source_uri': build.SOURCE_URI, 'source_revision': revision, 'version': component.version}}

    with ThreadPoolExecutor(max_workers=len(args.component)) as pool:
        for item in pool.map(build_one, args.component):
            identities[item['key']] = item
    (out / 'images.json').write_bytes(canonical_json_bytes({
        'source_revision': revision, 'built': [identities[key] for key in COMPONENTS]}))


if __name__ == '__main__':
    main()
