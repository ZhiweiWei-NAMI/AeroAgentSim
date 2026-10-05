"""Adversarial interpolation geometry tests, without a simulation claim."""
import unittest
from shapely.geometry import Point, Polygon, box
from city_motion_continuity import body_pose, continuity_contacts, pair_interval, static_interval


def vehicle(x,z,heading=0,kind="sedan",identifier="v"):
    return [identifier,x,z,heading,kind,0.]


class ContinuousMotionTest(unittest.TestCase):
    def test_translation_contact_between_endpoint_and_half_frame_is_rejected(self):
        first,last=vehicle(0,0),vehicle(12,0)
        obstacle=box(2.8,-.1,3.0,.1)
        self.assertEqual(body_pose(first,last,0).intersection(obstacle).area,0)
        self.assertEqual(body_pose(first,last,.5).intersection(obstacle).area,0)
        self.assertEqual(body_pose(first,last,1).intersection(obstacle).area,0)
        proof=static_interval(first,last,obstacle)
        self.assertEqual(proof["status"],"CONTACT")
        self.assertGreater(proof["area_m2"],0)
    def test_rotating_body_sweep_is_not_replaced_by_its_centerline(self):
        first,last=vehicle(0,0,0),vehicle(0,0,90)
        obstacle=Point(-1.5,1.5).buffer(.05)
        self.assertEqual(body_pose(first,last,0).intersection(obstacle).area,0)
        self.assertEqual(body_pose(first,last,1).intersection(obstacle).area,0)
        self.assertEqual(static_interval(first,last,obstacle)["status"],"CONTACT")
    def test_shortest_angle_wrap_does_not_rotate_the_long_way(self):
        first,last=vehicle(0,0,359),vehicle(0,0,1)
        self.assertEqual(static_interval(first,last,box(1.5,-.1,1.6,.1))["status"],"CLEAR")
    def test_raw_hole_remains_free_occupied_space(self):
        courtyard=Polygon([[-5,-5],[5,-5],[5,5],[-5,5],[-5,-5]],
                          [[[-2,-3],[2,-3],[2,3],[-2,3],[-2,-3]]])
        self.assertEqual(static_interval(vehicle(0,0),vehicle(.2,0),courtyard)["status"],"CLEAR")
        self.assertEqual(static_interval(vehicle(0,0),vehicle(2,0),courtyard)["status"],"CONTACT")
    def test_moving_bodies_contact_before_half_frame(self):
        first,last=vehicle(0,0,90),vehicle(20,0,90)
        other=vehicle(5,0,90,identifier="other")
        self.assertEqual(body_pose(first,last,0).intersection(body_pose(other,other,0)).area,0)
        self.assertEqual(body_pose(first,last,.5).intersection(body_pose(other,other,0)).area,0)
        self.assertEqual(pair_interval(first,last,other,other)["status"],"CONTACT")
    def test_stationary_disjoint_bodies_are_certified(self):
        first,second=vehicle(0,0),vehicle(3,0,identifier="other")
        self.assertEqual(pair_interval(first,first,second,second)["status"],"CLEAR")
    def test_person_swept_disk_keeps_radius_between_frames(self):
        traffic={"frames":[{"second":0,"vehicles":[],"persons":[["p",0,0,0,0]]},
                           {"second":.25,"vehicles":[],"persons":[["p",2,0,0,0]]}]}
        proof=continuity_contacts(traffic,[box(.99,.29,1.01,.31)])
        self.assertEqual(len(proof["person_static_contacts"]),1)
        self.assertLess(proof["person_static_contacts"][0]["minimum_circle_clearance_m"],0)
    def test_missing_next_actor_holds_pose_and_final_pose_is_checked(self):
        traffic={"frames":[{"second":0,"vehicles":[vehicle(0,0)],"persons":[]},
                           {"second":.25,"vehicles":[],"persons":[]}]}
        proof=continuity_contacts(traffic,[box(.5,-1,2,1)])
        self.assertEqual(proof["vehicle_intervals"],1)
        self.assertEqual(len(proof["vehicle_static_contacts"]),1)


if __name__ == "__main__":
    unittest.main()
