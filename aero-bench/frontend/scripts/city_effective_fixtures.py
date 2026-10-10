"""Compile a complete effective/omitted fixture inventory from displayed triangles."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

from shapely.geometry import Polygon,LineString
from shapely.ops import unary_union
from shapely.strtree import STRtree

from city_fixture_geometry import (glb_triangles,displayed_fixture_triangles,world_fixture_triangles,
    projected_fixture,fixture_in_height_band,fixture_display_transform)
from city_surface_identity import displayed_surface_sha256

SCHEMA = 'aero-bench.city-effective-fixture-geometry/v1'
AREA_EPSILON_M2 = 1e-8
POLE_CLEARANCE_M = .15
MOTOR_TOP_UP_M = .1+.01+3.3


def inventory_sha256(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def polygon_parts(geometry):
    if geometry.geom_type=='Polygon':yield geometry
    elif geometry.geom_type in {'MultiPolygon','GeometryCollection'}:
        for part in geometry.geoms:yield from polygon_parts(part)


def serialized_footprints(geometry):
    if geometry.is_empty or not geometry.is_valid:raise ValueError('Actual world fixture projection is invalid; repair is not permitted')
    return [{'outline':[[float(x),float(z)] for x,z in part.exterior.coords],
        'holes':[[[float(x),float(z)] for x,z in hole.coords] for hole in part.interiors]}
        for part in polygon_parts(geometry)]


def road_polygons(parts):
    polygons=[Polygon(part['outline'],part['holes']) for part in parts]
    if not polygons or any(part.is_empty or not part.is_valid for part in polygons):
        raise ValueError('Effective fixtures require valid displayed pavement polygons')
    return unary_union(polygons)


def build_effective_fixtures(source_context:dict,road:dict,signals:list,buildings,models:dict) -> dict:
    if road.get('schema_version')!='aero-bench.city-road-preview/v3' or road.get('source_context')!=source_context \
            or road.get('physical_clearance',{}).get('status')!='PASS':
        raise ValueError('Effective fixtures require the actual canonical road with physical clearance PASS')
    surface_sha=displayed_surface_sha256(road['roadbed'],road['walkbed'])
    if road.get('displayed_surface_sha256')!=surface_sha:
        raise ValueError('Effective fixture road surface digest differs from the displayed geometry')
    walkbed=road_polygons(road['walkbed']); roadbed=road_polygons(road['roadbed'])
    crossing_zone=unary_union([LineString(item['shape']).buffer(item['width']/2+.45,cap_style=2,join_style=2)
        for item in road['crossings']])
    building_polygons=[item.rendered_polygon for item in buildings]
    building_index=STRtree(building_polygons)
    inventories={'signals':signals,'street_lamps':road['street_lamps']}
    effective=[]; omitted={'signal':[],'street_lamp':[]}; model_refs={}; accepted_shapes=[]
    for kind,key in [('signal','signals'),('street_lamp','street_lamps')]:
        model=models[kind]; path=Path(model['path']); raw=path.read_bytes()
        displayed=displayed_fixture_triangles(glb_triangles(path),kind)
        base,top=float(displayed[:,:,1].min()),float(displayed[:,:,1].max())
        model_refs[kind]={'url':model['url'],'sha256':hashlib.sha256(raw).hexdigest(),
            'size_bytes':len(raw),'display_transform':fixture_display_transform(kind)}
        for index,location in enumerate(inventories[key]):
            world=world_fixture_triangles(displayed,kind,location)
            footprint=projected_fixture(world)
            if footprint.is_empty or not footprint.is_valid:
                raise ValueError(f'{kind}:{index}: actual transformed fixture model is invalid; repair is not permitted')
            reason=None; hits=[]
            for building_id in building_index.query(footprint):
                building=buildings[building_id]
                band=fixture_in_height_band(world,building.base_up_m,building.top_up_m)
                area=band.intersection(building.rendered_polygon).area
                if area>AREA_EPSILON_M2:
                    hits.append({'building_id':building.object_id,'area_m2':area,
                        'height_band_m':[building.base_up_m,building.top_up_m]})
            if hits: reason='actual-model-building-contact'
            pole=fixture_in_height_band(world,base,min(top,base+.4))
            if pole.is_empty:raise ValueError(f'{kind}:{index}: model has no measured lower pole geometry')
            if reason is None and not walkbed.covers(pole.buffer(POLE_CLEARANCE_M)):
                reason='measured-lower-pole-outside-dedicated-walkbed'
            low_motor=fixture_in_height_band(world,0,MOTOR_TOP_UP_M)
            if reason is None and low_motor.buffer(POLE_CLEARANCE_M).intersection(roadbed).area>AREA_EPSILON_M2:
                reason='measured-model-in-declared-motor-height-band-over-roadbed'
            if reason is None and pole.buffer(POLE_CLEARANCE_M).intersection(crossing_zone).area>AREA_EPSILON_M2:
                reason='measured-lower-pole-in-crossing-or-wait-zone'
            if reason is None and any(footprint.intersection(prior).area>AREA_EPSILON_M2 for prior in accepted_shapes):
                reason='conservative-full-height-projected-effective-fixture-contact'
            if reason is not None:
                omitted[kind].append({'source_index':index,'source_location':location,'reason':reason,
                    'building_contacts':hits})
                continue
            fixture_id=f'signal:{location["id"]}' if kind=='signal' else f'street_lamp:{index}'
            effective.append({'id':fixture_id,'kind':kind,'source_index':index,'source_location':location,
                'model_sha256':model_refs[kind]['sha256'],'footprints':serialized_footprints(footprint),
                'motion_footprints':serialized_footprints(low_motor),
                'base_up_m':base,'top_up_m':top})
            accepted_shapes.append(footprint)
    return {'schema_version':SCHEMA,'source_context':source_context,'models':model_refs,
        'displayed_surface_sha256':surface_sha,'source_inventories':inventories,
        'source_inventory_sha256':{key:inventory_sha256(value) for key,value in inventories.items()},
        'effective_fixtures':effective,'omitted_signals':omitted['signal'],'omitted_street_lamps':omitted['street_lamp'],
        'policy':{'building_contact':'actual-model-triangles-clipped-to-source-building-height-band',
            'pole_paving':'measured-lower-model-band-on-dedicated-walkbed','pole_clearance_m':POLE_CLEARANCE_M,
            'motor_height_band_up_m':[0,MOTOR_TOP_UP_M],'motor_height_basis':'declared-viewer-bus-body-0.1-ground-plus-0.01-body-offset-plus-3.3-height',
            'mutual_fixture_contact':'conservative-full-height-projection-of-actual-displayed-triangles',
            # Every declared actor (tallest: the bus body) lies within the motor band, so model
            # parts above it, such as lamp heads and signal arms, cannot touch an actor.
            'routing_fixture_geometry':'actual-displayed-triangles-clipped-to-declared-motor-height-band',
            'omissions_preserve_all_source_signals_and_tls':True,'source_locations_are_not_relocated':True},
        'counts':{'source_signals':len(signals),'source_street_lamps':len(inventories['street_lamps']),
            'effective_signals':sum(item['kind']=='signal' for item in effective),
            'effective_street_lamps':sum(item['kind']=='street_lamp' for item in effective),
            'omitted_signals':len(omitted['signal']),'omitted_street_lamps':len(omitted['street_lamp'])}}
