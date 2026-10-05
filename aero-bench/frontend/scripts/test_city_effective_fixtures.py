"""Authored input probes using the actual source fixture GLBs."""
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace

from shapely.geometry import Polygon,box
from shapely.ops import unary_union

from city_effective_fixtures import build_effective_fixtures
from city_fixture_geometry import displayed_fixture_triangles,glb_triangles,projected_fixture,world_fixture_triangles
from city_surface_identity import displayed_surface_sha256

ROOT=Path(__file__).resolve().parents[2]
MODELS={kind:{'url':f'/models/incoming/furniture/glb/{name}.glb',
    'path':ROOT/f'frontend/public/models/incoming/furniture/glb/{name}.glb'}
    for kind,name in [('signal','traffic_light_4'),('street_lamp','street_light_8')]}


def inputs():
    part=lambda a,b,c,d:{'outline':[[a,b],[c,b],[c,d],[a,d]],'holes':[]}
    context={'source':'explicit-authored-unit-probe'}
    road={'schema_version':'aero-bench.city-road-preview/v3','source_context':context,
        'physical_clearance':{'status':'PASS'},'roadbed':[part(1000,1000,1010,1010)],
        'walkbed':[part(-100,-100,100,100)],'crossings':[],
        'street_lamps':[{'x':50,'z':50,'rotation_deg':60},{'x':500,'z':500,'rotation_deg':60}]}
    road['displayed_surface_sha256']=displayed_surface_sha256(road['roadbed'],road['walkbed'])
    signals=[{'id':'probe0','tls':'probe','link':0,'x':10,'z':20,'heading':30},
             {'id':'probe1','tls':'probe','link':1,'x':10,'z':20,'heading':30}]
    return context,road,signals


class EffectiveFixturesTest(unittest.TestCase):
    def test_real_rotated_models_are_unioned_after_world_vertex_transform(self):
        for kind,location in [('signal',{'x':10,'z':20,'heading':30}),
                              ('street_lamp',{'x':10,'z':20,'rotation_deg':60})]:
            source=displayed_fixture_triangles(glb_triangles(MODELS[kind]['path']),kind)
            world=world_fixture_triangles(source,kind,location)
            self.assertTrue(projected_fixture(world).is_valid)
            self.assertGreater(projected_fixture(world).area,0)

    def test_complete_partition_retains_source_inventory_and_omits_other_clearance_reasons(self):
        ctx,road,signals=inputs();before=copy.deepcopy(signals)
        data=build_effective_fixtures(ctx,road,signals,[],MODELS)
        self.assertEqual(signals,before)
        for kind,key,omit in [('signal','signals','omitted_signals'),('street_lamp','street_lamps','omitted_street_lamps')]:
            effective={x['source_index'] for x in data['effective_fixtures'] if x['kind']==kind}
            omitted={x['source_index'] for x in data[omit]}
            self.assertFalse(effective & omitted)
            self.assertEqual(effective | omitted,set(range(len(data['source_inventories'][key]))))
        self.assertEqual(data['counts']['effective_signals'],1)
        self.assertEqual(data['counts']['effective_street_lamps'],1)
        self.assertEqual(data['omitted_street_lamps'][0]['reason'],'measured-lower-pole-outside-dedicated-walkbed')
        self.assertEqual(data['omitted_signals'][0]['reason'],'conservative-full-height-projected-effective-fixture-contact')
        for item in data['effective_fixtures']:
            for part in [*item['footprints'],*item['motion_footprints']]:
                self.assertEqual(part['outline'][0],part['outline'][-1])
                for hole in part['holes']:self.assertEqual(hole[0],hole[-1])
            full=unary_union([Polygon(p['outline'],p['holes']) for p in item['footprints']])
            motion=unary_union([Polygon(p['outline'],p['holes']) for p in item['motion_footprints']])
            # The motor-band clip lies within the full projection (up to its 1 um snap grid along
            # the clipped edges) and excludes the overhead parts.
            self.assertLess(motion.difference(full).area,1e-6)
            self.assertLess(motion.area,full.area)
        self.assertEqual(data['policy']['routing_fixture_geometry'],
                         'actual-displayed-triangles-clipped-to-declared-motor-height-band')

    def test_actual_building_contact_is_measured_and_tls_source_remains(self):
        ctx,road,signals=inputs()
        building=SimpleNamespace(object_id='authored-unit-building',rendered_polygon=box(-100,-100,100,100),base_up_m=0,top_up_m=20)
        data=build_effective_fixtures(ctx,road,signals,[building],MODELS)
        self.assertEqual(data['source_inventories']['signals'],signals)
        self.assertEqual(len(data['omitted_signals']),2)
        self.assertGreater(data['omitted_signals'][0]['building_contacts'][0]['area_m2'],0)

    def test_blocked_road_or_changed_surface_cannot_generate_fixture_asset(self):
        ctx,road,signals=inputs()
        road['physical_clearance']['status']='BLOCKED'
        with self.assertRaisesRegex(ValueError,'physical clearance PASS'):
            build_effective_fixtures(ctx,road,signals,[],MODELS)
        road['physical_clearance']['status']='PASS';road['walkbed'][0]['outline'][0][0]-=1
        with self.assertRaisesRegex(ValueError,'surface digest'):
            build_effective_fixtures(ctx,road,signals,[],MODELS)


if __name__=='__main__':unittest.main()
