#!/usr/bin/env python3
"""Bind explicit presentation fixture omissions to unchanged buildings and traffic."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from shapely import affinity
from shapely.geometry import Polygon
from shapely.strtree import STRtree

from city_fixture_geometry import (displayed_fixture_triangles, fixture_in_height_band,
                                    glb_triangles, projected_fixture)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(objects_path: Path, render_path: Path, road_path: Path, traffic_path: Path,
          signal_model: Path, lamp_model: Path) -> dict:
    objects, render, road, traffic = [json.loads(path.read_text())
                                     for path in (objects_path, render_path, road_path, traffic_path)]
    buildings = objects['buildings']
    objects_digest, render_digest = sha256(objects_path), sha256(render_path)
    if (objects['coordinate_frame'] != 'ENU' or render['scene']['coordinate_frame'] != 'ENU'
            or render['scene']['objects_json_sha256'] != objects_digest):
        raise ValueError("Fixture clearance buildings differ from the canonical render source")
    if {item['object_id'] for item in render['buildings']} != {item['object_id'] for item in buildings}:
        raise ValueError("Fixture clearance render inventory differs from source buildings")
    if (road['source_network_sha256'] != traffic['source_network_sha256']
            or road['mesh_pack_source_sha256'] != traffic['mesh_pack_source_sha256']
            or render['scene']['mesh_pack_source_sha256'] != road['mesh_pack_source_sha256']):
        raise ValueError("Fixture clearance road, traffic and buildings use different sources")
    origin_keys = ('latitude_deg', 'longitude_deg', 'amsl_m', 'ellipsoid_height_m',
                   'geoid_undulation_m')
    source_origin, render_origin = objects['origin'], render['scene']['origin_wgs84']
    if any(key not in source_origin or key not in render_origin
           or source_origin[key] != render_origin[key] for key in origin_keys):
        raise ValueError("Fixture clearance source and render origins differ")
    expected_basis = {
        'policy': 'placement-proxies-union-true-rendered-footprints',
        'route_obstacle_basis': 'placement-proxies-union-true-rendered-footprints',
        'objects_json_sha256': objects_digest,
        'render_manifest_sha256': render_digest,
        'rendered_footprint_count': len(buildings),
        'pack_source_sha256': road['mesh_pack_source_sha256'],
    }
    basis = traffic.get('visual_obstacle_basis')
    if not isinstance(basis, dict) or any(basis.get(key) != value
                                          for key, value in expected_basis.items()):
        raise ValueError("Fixture clearance traffic was not recorded against these rendered buildings")
    footprints = [Polygon([(point[0], -point[1]) for point in item['footprint_enu_m']])
                  for item in buildings]
    if not footprints or len({item['object_id'] for item in buildings}) != len(buildings) \
            or any(part.is_empty or not part.is_valid for part in footprints):
        raise ValueError("Fixture clearance requires valid unique source building footprints")
    tree = STRtree(footprints)
    sources = {
        'objects_json_sha256': objects_digest,
        'building_render_manifest_sha256': render_digest,
        'road_preview_sha256': sha256(road_path), 'traffic_preview_sha256': sha256(traffic_path),
        'signal_model_sha256': sha256(signal_model), 'street_lamp_model_sha256': sha256(lamp_model),
        'source_network_sha256': road['source_network_sha256'],
    }
    omitted = {}
    model_basis = {}
    for kind, model, inventory, angle_key in [
            ('signal', signal_model, traffic['signals'], 'heading'),
            ('street_lamp', lamp_model, road['street_lamps'], 'rotation_deg')]:
        triangles = displayed_fixture_triangles(glb_triangles(model), kind)
        full_shape = projected_fixture(triangles)
        cache = {}
        records = []
        for source_index, item in enumerate(inventory):
            heading = item[angle_key] if kind == 'signal' else -item[angle_key]
            world_shape = affinity.translate(affinity.rotate(full_shape, heading, origin=(0, 0)), item['x'], item['z'])
            hits = []
            for index in tree.query(world_shape):
                building = buildings[index]
                band = (building['base_enu_up_m'], building['top_enu_up_m'])
                if band[1] <= band[0]:
                    raise ValueError(f"Source building has invalid vertical extent: {building['object_id']}")
                if band not in cache:
                    cache[band] = fixture_in_height_band(triangles, *band)
                clipped = affinity.translate(affinity.rotate(cache[band], heading, origin=(0, 0)), item['x'], item['z'])
                area = clipped.intersection(footprints[index]).area
                if area > 1e-8:
                    hits.append({'building_id': building['object_id'], 'overlap_m2': area})
            if hits:
                records.append({'source_index': source_index, 'source_location': item,
                                'reason': 'fixture_intersects_source_building',
                                'hits': sorted(hits, key=lambda hit: hit['building_id'])})
        omitted[kind] = records
        model_basis[kind] = {'displayed_bounds_m': [triangles.min(axis=(0, 1)).tolist(),
                                                    triangles.max(axis=(0, 1)).tolist()],
                             'projected_area_m2': full_shape.area}
    return {
        'schema_version': 'aero-bench.city-static-fixture-clearance/v1',
        'source_kind': 'canonical-building-footprints-and-transformed-fixture-triangles',
        'policy': 'omit-conflicting-render-fixtures-preserve-source-traffic',
        'sources': sources,
        'source_inventories': {'signals': traffic['signals'], 'street_lamps': road['street_lamps']},
        'model_basis': model_basis,
        'omitted_signals': omitted['signal'], 'omitted_street_lamps': omitted['street_lamp'],
        'statistics': {'source_buildings': len(buildings), 'source_signals': len(traffic['signals']),
                       'source_street_lamps': len(road['street_lamps']),
                       'omitted_source_signals': len(omitted['signal']),
                       'omitted_source_street_lamps': len(omitted['street_lamp'])},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ['objects', 'render', 'road', 'traffic', 'signal-model', 'lamp-model', 'output']:
        parser.add_argument(f'--{argument}', required=True, type=Path)
    arguments = parser.parse_args()
    result = build(arguments.objects, arguments.render, arguments.road, arguments.traffic,
                   arguments.signal_model, arguments.lamp_model)
    arguments.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'output': str(arguments.output), 'sha256': sha256(arguments.output),
                      'bytes': arguments.output.stat().st_size, **result['statistics']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
