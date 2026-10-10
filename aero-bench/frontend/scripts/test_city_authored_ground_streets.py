import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from shapely.geometry import Polygon
from city_authored_ground_streets import FleetWidth,plan_authored_streets
from city_preview_coordinates import CityEnuProjection


def inputs(road_type='service',opposite=False,pedestrian=False,sidewalk=False):
    origin={'latitude_deg':0.,'longitude_deg':0.,'ellipsoid_height_m':0.,'amsl_m':0.,'geoid_undulation_m':0.}
    network=ET.fromstring('<net><location netOffset="0,0" projParameter="+proj=aeqd +lat_0=0 +lon_0=0 +datum=WGS84 +units=m +no_defs" /><junction id="a" x="0" y="0" /><junction id="b" x="10" y="0" /></net>')
    edge=ET.SubElement(network,'edge',id='42',**{'from':'a','to':'b'},shape='0,0 10,0')
    if sidewalk:
        ET.SubElement(edge,'lane',id='42_0',index='0',speed='1.39',width='2.0',allow='pedestrian',shape='0,-2.6 10,-2.6')
        ET.SubElement(edge,'lane',id='42_1',index='1',speed='8.33',width='3.2',disallow='pedestrian',shape='0,0 10,0')
    else:
        ET.SubElement(edge,'lane',id='42_0',index='0',speed='8.33',width='3.2',shape='0,-1.6 10,-1.6')
    if opposite:
        edge=ET.SubElement(network,'edge',id='-42',**{'from':'b','to':'a'},shape='10,0 0,0')
        ET.SubElement(edge,'lane',id='-42_0',index='0',speed='8.33',width='3.2',allow='pedestrian' if pedestrian else 'passenger',shape='10,2.6 0,2.6' if pedestrian else '10,1.6 0,1.6')
    projection=CityEnuProjection(network,origin)
    lon,lat=projection.to_geographic.transform(10.,0.)
    source=ET.fromstring('<osm><node id="a" lon="0" lat="0" /><way id="42"><nd ref="a" /><nd ref="b" /></way></osm>')
    ET.SubElement(source,'node',id='b',lon=str(lon),lat=str(lat))
    ET.SubElement(source.find('way'),'tag',k='highway',v=road_type)
    geometry=SimpleNamespace(footprints=[])
    return network,source,geometry,projection


class AuthoredStreetsTest(unittest.TestCase):
    fleet=(FleetWidth('passenger',1.8,1.8),FleetWidth('bus',2.5,2.5),FleetWidth('delivery',2.2,2.2),FleetWidth('bicycle',.65,.65))
    def test_one_way_motor_lane_is_centered_on_source_spine(self):
        net,src,geo,proj=inputs();before=ET.tostring(src)
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        self.assertEqual(plan['designed'][0]['lanes'][0]['signed_viewer_left_offset_m'],0)
        self.assertEqual(ET.tostring(src),before)
        self.assertFalse(plan['surveyed_geometry'])
        self.assertTrue(plan['design_widths_are_not_source_measurements'])

    def test_bidirectional_lanes_share_a_carriageway_spine(self):
        net,src,geo,proj=inputs(opposite=True)
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        self.assertEqual(len(plan['designed']),2)
        self.assertTrue(all(row['bidirectional_motor'] for row in plan['designed']))
        self.assertTrue(all(abs(row['lanes'][0]['signed_viewer_left_offset_m']-1.6)<1e-8 for row in plan['designed']))

    def test_pedestrian_reverse_side_is_rederived_from_motor_source_spine(self):
        net,src,geo,proj=inputs(opposite=True,pedestrian=True)
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        self.assertEqual(len(plan['designed']),2)
        ped=next(row for row in plan['designed'] if row['edge_id']=='-42')
        self.assertEqual(ped['lanes'][0]['role'],'pedestrian')
        self.assertEqual(ped['lanes'][0]['allowed'],['pedestrian'])
        self.assertIn('rederived',ped['spine_basis'])

    def test_service_shrinks_to_explicit_design_and_limits_bus(self):
        net,src,geo,proj=inputs()
        obstacles=[Polygon([[-1,1.5],[11,1.5],[11,10],[-1,10]]),Polygon([[-1,-1.5],[11,-1.5],[11,-10],[-1,-10]])]
        geo.footprints=[SimpleNamespace(polygon=p,rendered_polygon=p) for p in obstacles]
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        lane=plan['designed'][0]['lanes'][0]
        self.assertEqual(lane['width_m'],2.6)
        self.assertNotIn('bus',lane['allowed'])
        self.assertIn('delivery',lane['allowed'])
        self.assertEqual(lane['width_basis'],'authored-design-not-surveyed')
        self.assertEqual(lane['native_source_width_m'],3.2)

    def test_ordinary_motor_minimum_is_not_lowered_to_fit(self):
        net,src,geo,proj=inputs('residential')
        p=Polygon([[-1,1.5],[11,1.5],[11,10],[-1,10]])
        geo.footprints=[SimpleNamespace(polygon=p,rendered_polygon=p)]
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        # No motor lane is narrowed below the residential minimum; the street stays walkable.
        self.assertFalse(any(lane['role']=='motor' for row in plan['designed'] for lane in row['lanes']))
        self.assertEqual(plan['designed'][0]['cross_section'],'pedestrian-passage')
        self.assertEqual(plan['designed'][0]['lanes'][0]['allowed'],['pedestrian'])

    def test_arterial_without_room_for_any_lane_is_excluded(self):
        net,src,geo,proj=inputs('primary')
        p=Polygon([[-1,1.5],[11,1.5],[11,10],[-1,10]])
        geo.footprints=[SimpleNamespace(polygon=p,rendered_polygon=p)]
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        self.assertEqual(len(plan['designed']),0)
        self.assertEqual(plan['excluded'][0]['reason'],'unsupported_free_corridor')

    def test_local_street_shares_carriageway_when_no_sidewalk_fits(self):
        net,src,geo,proj=inputs('service',sidewalk=True)
        p=Polygon([[-1,2.0],[11,2.0],[11,10],[-1,10]])
        geo.footprints=[SimpleNamespace(polygon=p,rendered_polygon=p)]
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        row=plan['designed'][0]
        self.assertEqual(row['cross_section'],'shared-carriageway')
        self.assertEqual(len(row['lanes']),1)
        self.assertIn('pedestrian',row['lanes'][0]['allowed'])
        self.assertIn('OSM default foot access',row['pedestrian_access_basis'])

    def test_arterial_omits_colliding_sidewalk_but_never_shares(self):
        net,src,geo,proj=inputs('primary',sidewalk=True)
        p=Polygon([[-1,2.0],[11,2.0],[11,10],[-1,10]])
        geo.footprints=[SimpleNamespace(polygon=p,rendered_polygon=p)]
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        row=plan['designed'][0]
        self.assertEqual(row['cross_section'],'sidewalk-omitted')
        self.assertEqual([lane['role'] for lane in row['lanes']],['motor'])
        self.assertNotIn('pedestrian',row['lanes'][0]['allowed'])
        self.assertEqual(row['omitted_sidewalk_lanes'][0]['source_lane_id'],'42_0')

    def test_walking_area_feedback_caps_then_forbids_separate_sidewalk(self):
        net,src,geo,proj=inputs('primary',sidewalk=True)
        feedback=lambda level:{'42':{'level':level,'reason':'native walking area :j_w0_0 is not drawable'}}
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet,feedback(1))
        self.assertEqual(plan['designed'][0]['pedestrian_width_design_m'],1.5)
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet,feedback(2))
        row=plan['designed'][0]
        self.assertEqual(row['cross_section'],'sidewalk-omitted')
        self.assertEqual(row['omitted_sidewalk_lanes'][0]['reason'],'native walking area :j_w0_0 is not drawable')
        net,src,geo,proj=inputs('service',sidewalk=True)
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet,feedback(2))
        self.assertEqual(plan['designed'][0]['cross_section'],'shared-carriageway')
        # Level 3 removes pedestrian access from the shared carriageway as well.
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet,feedback(3))
        row=plan['designed'][0]
        self.assertEqual(row['cross_section'],'sidewalk-omitted')
        self.assertFalse(any('pedestrian' in lane['allowed'] for lane in row['lanes']))

    def test_foot_no_blocks_shared_surface_and_passage(self):
        net,src,geo,proj=inputs('residential',sidewalk=True)
        ET.SubElement(src.find('way'),'tag',k='foot',v='no')
        p=Polygon([[-1,2.0],[11,2.0],[11,10],[-1,10]])
        geo.footprints=[SimpleNamespace(polygon=p,rendered_polygon=p)]
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        self.assertNotIn(plan['designed'][0]['cross_section'],('shared-carriageway','pedestrian-passage'))

    def test_bidirectional_passage_folds_the_reverse_edge(self):
        net,src,geo,proj=inputs('service',opposite=True)
        obstacles=[Polygon([[-1,1.2],[11,1.2],[11,10],[-1,10]]),Polygon([[-1,-1.2],[11,-1.2],[11,-10],[-1,-10]])]
        geo.footprints=[SimpleNamespace(polygon=p,rendered_polygon=p) for p in obstacles]
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        self.assertEqual([row['edge_id'] for row in plan['designed']],['-42'])
        self.assertEqual(plan['designed'][0]['cross_section'],'pedestrian-passage')
        self.assertEqual(plan['excluded'][0]['reason'],'folded_into_pedestrian_passage')

    def test_source_spine_entering_building_is_excluded_without_moving_building(self):
        net,src,geo,proj=inputs()
        polygon=Polygon([[4,-1],[6,-1],[6,1],[4,1]])
        geo.footprints=[SimpleNamespace(polygon=polygon,rendered_polygon=polygon)]
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        self.assertEqual(plan['excluded'][0]['reason'],'source_geometry_conflict')
        self.assertEqual(geo.footprints[0].polygon,polygon)

    def test_declared_width_is_preserved_or_marked_unsupported(self):
        net,src,geo,proj=inputs('residential');ET.SubElement(src.find('way'),'tag',k='width',v='2.8')
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        # 2.8 m cannot hold a 3.0 m residential motor lane; the walkway stays within the declared width.
        self.assertEqual(plan['designed'][0]['source_tags']['width'],'2.8')
        self.assertEqual(plan['designed'][0]['cross_section'],'pedestrian-passage')
        self.assertLessEqual(plan['designed'][0]['lanes'][0]['width_m'],2.8)
        src.find('way/tag[@k="width"]').set('v','1.2')
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        self.assertEqual(plan['excluded'][0]['reason'],'unsupported_free_corridor')
        src.find('way/tag[@k="width"]').set('v','4')
        _,plan=plan_authored_streets(net,src,geo,proj,self.fleet)
        self.assertEqual(plan['designed'][0]['lanes'][0]['width_m'],4)

    def test_missing_source_spine_is_rejected_before_using_native_geometry(self):
        net,src,geo,proj=inputs()
        for node in list(src.find('way').findall('nd')):src.find('way').remove(node)
        with self.assertRaisesRegex(ValueError,'no declared node spine'):
            plan_authored_streets(net,src,geo,proj,self.fleet)


if __name__=='__main__':unittest.main()
