"""Execute the existing fitted-map checker on the three modified contact segments."""
from pathlib import Path
import sys
from dataclasses import asdict


def check_contact_geometry(root, paths):
    root = Path(root)
    # These existing authoring modules use the repository's tools namespace.
    tools = str(root / 'Dataset/tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from map_spatial_index import MapSpatialIndex
    from uav_corridor_planner import BuildingObstacleIndex
    spatial = MapSpatialIndex.default(root)
    checker = BuildingObstacleIndex(spatial, horizontal_clearance_m=6., vertical_clearance_m=5.,
                                    fallback_height_m=18.)
    rows = []
    for action_id, points in paths.items():
        for i, (a, b) in enumerate(zip(points, points[1:])):
            clear = bool(checker.air_segment_clear(a, b))
            collision = checker.segment_collision(a, b)
            rows.append({'action_id': action_id, 'segment_index': i, 'start_enu_m': a,
                         'end_enu_m': b, 'clear': clear,
                         'building_collision_id': None if collision is None else collision.building_id})
    if len(rows) != 3:
        raise ValueError('Contact geometry requires the three explicitly revised action segments')
    fit = asdict(spatial.geojson_fit)
    fit['matrix'] = [list(row) for row in fit['matrix']]
    result = {'map_package': str(spatial.map_package_path.relative_to(root)),
        'buildings': str(spatial.source_geojson_paths['building'].relative_to(root)),
        'checker': 'Dataset/tools/uav_corridor_planner.py:BuildingObstacleIndex.air_segment_clear',
        'horizontal_clearance_m': checker.horizontal_clearance_m,
        'vertical_clearance_m': checker.vertical_clearance_m,
        'fallback_building_height_m': checker.fallback_height_m,
        'fitted_geojson_center_xy_m': list(spatial.geojson_center_xy_m),
        'fitted_geojson_transform': fit,
        'checked_segments': len(rows), 'all_clear': all(row['clear'] for row in rows), 'segments': rows,
        'meaning': 'Executed fitted source geometry check, with explicit fallback heights; not surveyed physical clearance'}
    if not result['all_clear']:
        raise ValueError('Adopted contact route intersects source geometry: ' + repr(rows))
    return result
