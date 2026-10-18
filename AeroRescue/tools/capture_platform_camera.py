#!/usr/bin/env python3
"""Acquire two onboard source-city RGB-D frames through real mock flight commands.

This is a focused renderer/observation driver, not a rescue acceptance verdict.
--source-only checks the two committed motion cuts without launching a browser.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import yaml
from aerokernel import CommandRequest, Instant
from aerorescue.companion.coordinator import Coordinator
from aerorescue.companion.platform_city import prepare_platform_scene, POSITION
from aerorescue.companion.planning import PublicMapPlanner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', default='platform-camera')
    parser.add_argument('--uav', default='u2')
    parser.add_argument('--source-only', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    scene = yaml.safe_load(args.scenario.read_text())
    if scene.get('mock_backend', {}).get('visual_backend') != 'platform_city':
        raise ValueError('Select an explicitly authored platform-city scenario')
    scene = prepare_platform_scene(scene, scene['mock_backend']['platform_city']['roof_id'])
    (args.output/'private-scene.yaml').write_text(yaml.safe_dump(scene, sort_keys=False))
    c = Coordinator(scene, args.run_id, args.output/'coordinator', args.output/'ipc', backend='mock', until_ns=120_000_000_000)
    summary = {'execution_backend': 'mock', 'visual_backend': 'platform_city', 'status': 'pending',
               'source_only': args.source_only, 'captures': [], 'operations': [], 'routes': [], 'native': False}
    planner = PublicMapPlanner(scene['public_map'])
    def command(action, params):
        now = c.simulation.kernel.view().instant.ns
        mid = c.simulation.kernel.submit(CommandRequest('adapters.px4_gazebo.'+action, 'flight', Instant(now+1), {'entity': args.uav, **params}))
        deadline = now+60_000_000_000
        while c.simulation.kernel.view().instant.ns < deadline:
            c.simulation.run_until(c.simulation.kernel.view().instant.ns+200_000_000)
            result = c.simulation.kernel.view().action(mid)
            if result.status in ('succeeded', 'failed', 'canceled', 'rejected'):
                summary['operations'].append({'command_id': mid, 'action': action, 'status': result.status,
                    'sim_ns': c.simulation.kernel.view().instant.ns, 'params': params})
                if result.status != 'succeeded': raise RuntimeError('flight command did not succeed: '+action+' '+result.status)
                return
        raise TimeoutError('flight operation deadline: '+action)
    try:
        c.simulation.start()
        # Camera positions use only the public receiver centre and search area.
        polygon = scene['public_map']['receiver_polygon']
        x = sum(p[0] for p in polygon)/len(polygon); y = sum(p[1] for p in polygon)/len(polygon)
        altitude = scene['public_map']['search_altitude_m']
        command('arm', {})
        start = scene['vehicles'][args.uav]['spawn'][:3]
        climb = planner.route(start, [*start[:2], altitude])
        summary['routes'].append(climb)
        if climb['status'] != 'succeeded': raise RuntimeError('public-map climb unavailable')
        command('takeoff', {'altitude_m': altitude-scene['vehicles'][args.uav]['spawn'][2]})
        points = [[x, y, altitude], [x, (y+max(p[1] for p in scene['public_map']['search_polygon']))/2, altitude]]
        for target in points:
            start = c.mock_world.vehicles[args.uav]['position'].tolist()
            route = planner.route(start, target); summary['routes'].append(route)
            if route['status'] != 'succeeded': raise RuntimeError('public-map camera route unavailable')
            for waypoint in route['path_enu_m'][1:]:
                command('goto', {'position_enu': waypoint, 'yaw_deg': 0})
            command('hold', {})
            view = c.simulation.kernel.view()
            ref = next(r for r in c.simulation.scenario.manifest.entities if r.id == 'visual/'+args.uav)
            row = {'source_cut': {'index': view.cut.index, 'instant': [view.instant.ns, view.instant.microstep]},
                   'committed_position_enu_m': list(view.field((ref, POSITION), view.instant).value)}
            if not args.source_only:
                metadata, _ = c.mock_world.platform_camera.render(args.uav)
                row.update(frame_id=metadata['frame_id'], capture_json=str(Path('coordinator/platform-frames')/args.uav/str(view.instant.ns)/'capture.json'))
            summary['captures'].append(row)
        summary['status'] = 'source_cuts_only' if args.source_only else 'rendered'
    except Exception as error:
        summary.update(status='blocked', reason=str(error))
        raise
    finally:
        (args.output/'report.json').write_text(json.dumps(summary, indent=2)+'\n')
        c.simulation.close(); c.mock_world.platform_camera.close()


if __name__ == '__main__': main()
