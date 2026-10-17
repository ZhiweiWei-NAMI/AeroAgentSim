"""Companion routes from public geometry and locally observed resource state."""
from __future__ import annotations

import math
from typing import Any

from aeroagentsim.algorithms.builtins import CorridorAstar
from aeroagentsim.algorithms.protocols import Snapshot, Zone
from aeroagentsim.packs.geometry import segment_hits_box


class PublicMapPlanner:
    def __init__(self, public_map: dict[str, Any]):
        self.map = public_map
        self.clearance = float(public_map.get('clearance_m', 1.0))
        self.occluders = tuple((tuple(b['lower']), tuple(b['upper']))
                               for b in public_map.get('obstacles', []))
        self.zones = tuple(Zone(str(b['id']), 'no_fly',
                               tuple(float(v)-self.clearance for v in b['lower']),
                               tuple(float(v)+self.clearance for v in b['upper']), True)
                           for b in public_map.get('obstacles', []))
        self.snapshot = Snapshot(at_ns=0, vehicles=(), zones=self.zones)
        self.planner = CorridorAstar(clearance_m=self.clearance)

    def route(self, start: list[float], goal: list[float]) -> dict[str, Any]:
        if len(start) != 3 or len(goal) != 3 or not all(math.isfinite(v) for v in (*start, *goal)):
            return {'status': 'unavailable', 'reason': 'finite ENU start and goal required'}
        try:
            points = self.planner.plan(self.snapshot, tuple(start), tuple(goal))
        except ValueError as exc:
            return {'status': 'unavailable', 'reason': str(exc)}
        path = [list(start), *[list(p) for p in points]]
        return {'status': 'succeeded', 'path_enu_m': path,
                'distance_m': sum(math.dist(a, b) for a, b in zip(path, path[1:])),
                'source': 'public-map corridor_astar', 'map_id': self.map.get('id')}

    def search(self, start: list[float], polygon: list[list[float]], altitude_m: float,
               spacing_m: float) -> dict[str, Any]:
        """Clip lawnmower rows to the public polygon and route their connectors.

        Planned rows do not count as observed coverage; captured camera footprints
        are recorded separately by the observation worker.
        """
        if len(polygon) < 3 or not math.isfinite(spacing_m) or spacing_m <= 0:
            return {'status': 'unavailable', 'reason': 'polygon and positive row spacing required'}
        y0, y1 = min(p[1] for p in polygon), max(p[1] for p in polygon)
        points: list[list[float]] = []
        count = max(1, math.ceil((y1-y0)/spacing_m))
        edges = list(zip(polygon, polygon[1:]+polygon[:1]))
        for i in range(count):
            y = y0 + (i+.5)*(y1-y0)/count
            intersections = sorted(a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])
                                   for a, b in edges if (a[1] <= y < b[1]) or (b[1] <= y < a[1]))
            row = []
            # Sample each interior span, not only its boundary endpoints.
            for left, right in zip(intersections[::2], intersections[1::2]):
                segments = max(1, math.ceil((right-left)/spacing_m))
                row.extend([[left+(right-left)*j/segments, y, altitude_m]
                            for j in range(segments+1)])
            if i % 2:
                row.reverse()
            points.extend(row)
        path = [list(start)]
        unreachable = []
        for point in points:
            leg = self.route(path[-1], point)
            if leg['status'] != 'succeeded':
                unreachable.append({'point_enu_m': point, 'reason': leg['reason']})
                continue
            path.extend(leg['path_enu_m'][1:])
        if len(path) == 1:
            return {'status': 'unavailable', 'reason': 'no reachable search rows', 'unreachable': unreachable}
        return {'status': 'succeeded', 'path_enu_m': path, 'unreachable': unreachable,
                'coverage_status': 'planned_only', 'observed_coverage': None,
                'source': 'public-polygon stripes with corridor_astar connectors'}

    def viewpoints(self, start: list[float], target: list[float], radius_m: float,
                   altitude_m: float, previous_camera: list[float] | None = None,
                   camera: dict[str, Any] | None = None,
                   observed_surface_points: list[list[float]] | None = None) -> dict[str, Any]:
        candidates = []
        if camera is None or camera.get('orientation') != 'downward':
            return {'status': 'unavailable', 'reason': 'declared downward camera geometry required'}
        half_fov = math.radians(min(camera['horizontal_fov_deg'], camera['vertical_fov_deg'])/2)
        camera_offset = camera.get('origin_offset_enu_m', [0, 0, 0])
        height_above = altitude_m+camera_offset[2]-target[2]
        if (height_above <= 0 or radius_m > height_above*math.tan(half_fov)
                or math.hypot(radius_m, height_above) > camera['depth_max_m']):
            return {'status': 'unavailable', 'reason': 'target outside camera view or range'}
        if radius_m <= 0:
            return {'status': 'unavailable', 'reason': 'positive viewpoint radius required'}
        for index in range(12):
            angle = index*math.tau/12
            point = [target[0]+radius_m*math.cos(angle), target[1]+radius_m*math.sin(angle), altitude_m]
            camera_origin = [point[i]+camera_offset[i] for i in range(3)]
            sight_targets = [target, *(observed_surface_points or [])]
            if any(segment_hits_box(tuple(camera_origin), tuple(surface), lower, upper)
                   for surface in sight_targets for lower, upper in self.occluders):
                continue
            change = None
            if previous_camera is not None:
                a = [previous_camera[i]-target[i] for i in range(3)]
                b = [camera_origin[i]-target[i] for i in range(3)]
                norm = math.sqrt(sum(v*v for v in a)*sum(v*v for v in b))
                if norm <= 0:
                    continue
                change = math.degrees(math.acos(max(-1, min(1, sum(x*y for x,y in zip(a,b))/norm))))
                if change < 20:
                    continue
            leg = self.route(start, point)
            if leg['status'] == 'succeeded':
                candidates.append({**leg, 'viewpoint_enu_m': point, 'view_angle_change_deg': change,
                                   'visible_surface_sample_count': len(observed_surface_points or []),
                                   'camera_origin_enu_m': camera_origin,
                                   'visibility_scope': 'recorded near-depth samples plus detector-box planar support; not entire person visibility'})
        candidates.sort(key=lambda c: c['distance_m'])
        return {'status': 'succeeded' if candidates else 'unavailable', 'candidates': candidates,
                'reason': None if candidates else 'no reachable public-map view with the required geometry'}

    def estimate_legs(self, state: dict[str, Any], actions: list[dict[str, Any]],
                      now_ns: int) -> dict[str, Any]:
        if state.get('position_enu_m') is None or state.get('valid_until_ns', -1) < now_ns:
            return {'status': 'unavailable', 'reason': 'local position missing or expired'}
        speed, per_m = state.get('speed_m_s'), state.get('energy_per_m_j')
        energy_source = state.get('energy_model_source')
        if speed is None or not math.isfinite(speed) or speed <= 0:
            return {'status': 'unavailable', 'reason': 'local speed estimate unavailable'}
        def task_route(origin, action, prepare_departure=False):
            prefix = [list(origin)]
            if prepare_departure and state.get('landed_state') in {'ON_GROUND', 'on_ground'}:
                # The execution workflow first issues relative-altitude takeoff.
                launch = [origin[0], origin[1], origin[2]+self.map['search_altitude_m']]
                climb = self.route(origin, launch)
                if climb['status'] != 'succeeded':
                    return climb
                prefix = climb['path_enu_m']
                origin = launch
            if action.get('role') == 'inspect':
                result = self.search(origin, action.get('search_polygon', self.map['search_polygon']),
                                     action.get('search_altitude_m', self.map['search_altitude_m']),
                                     action.get('search_row_spacing_m', self.map['search_row_spacing_m']))
                if result['status'] == 'succeeded':
                    result['path_enu_m'] = prefix[:-1]+result['path_enu_m']
                    path = result['path_enu_m']
                    result['distance_m'] = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
                return result
            result = self.route(origin, action['target'])
            if result['status'] == 'succeeded':
                result['path_enu_m'] = prefix[:-1]+result['path_enu_m']
                path = result['path_enu_m']
                result['distance_m'] = sum(math.dist(a,b) for a,b in zip(path,path[1:]))
            return result

        origins = [('current', state['position_enu_m'])]
        legs, unavailable = [], []
        for action in actions:
            route = task_route(state['position_enu_m'], action, True)
            if route['status'] == 'succeeded':
                origins.append((action['id'], route['path_enu_m'][-1]))
            else:
                unavailable.append({'from_action': action['id'], 'reason': route['reason']})
        for from_action, origin in origins:
            for action in actions:
                if from_action == action['id']:
                    continue
                leg = task_route(origin, action, from_action == 'current')
                if leg['status'] != 'succeeded':
                    unavailable.append({'from_action': from_action, 'to_action': action['id'], 'reason': leg['reason']})
                    continue
                service_s = float(action.get('duration_s', 0))
                hover_w = state.get('hover_power_w')
                service_energy = service_s*hover_w if hover_w is not None and math.isfinite(hover_w) and hover_w >= 0 else 0 if service_s == 0 else None
                energy_known = service_energy is not None and per_m is not None and math.isfinite(per_m) and per_m >= 0 and bool(energy_source)
                legs.append({**leg, 'from_action': from_action, 'to_action': action['id'],
                             'travel_s': leg['distance_m']/speed,
                             'energy_j': leg['distance_m']*per_m+service_energy if energy_known else None,
                             'service_energy_j': service_energy,
                             'end_position_enu_m': leg['path_enu_m'][-1],
                             'cost_scope': 'full planned action route including search; duration_s is additional non-flight service time',
                             'energy_source': energy_source if energy_known else 'unknown',
                             'observed_at_ns': state.get('observed_at_ns'),
                             'valid_until_ns': state['valid_until_ns'],
                             'commitment_version': state.get('commitment_version')})
        return {'status': 'succeeded', 'feasible_leg_costs': legs, 'unavailable_legs': unavailable,
                'energy_scope': 'declared flight-distance plus duration-times-hover-power model; missing service power remains unknown'}
