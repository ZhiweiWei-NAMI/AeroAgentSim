import unittest
import xml.etree.ElementTree as ET
from city_native_signals import canonical_signal_inventory, signal_inventory


class NativeSignalsTest(unittest.TestCase):
    def test_small_delivery_only_network_has_actual_motor_signal(self):
        network = ET.fromstring('''<net><edge id="42" from="a" to="b">
          <lane id="42_0" index="0" allow="pedestrian" width="2" shape="0,0 10,0" />
          <lane id="42_1" index="1" allow="delivery" width="3.2" shape="0,0 10,0" />
          </edge><connection from="42" to="43" fromLane="1" toLane="0" tl="j" linkIndex="4" dir="s" /></net>''')
        inventory = signal_inventory(network)
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["id"], "j:42")
        self.assertEqual(inventory[0]["x"], 7.5)
        self.assertEqual(inventory[0]["z"], -2.6)
        class Projection:
            def point(self, x, y): return (x, -y)
            def heading(self, x, y, heading): return heading + .1
        projected = canonical_signal_inventory(network, Projection())
        self.assertEqual(projected[0]["z"], 2.6)
        self.assertAlmostEqual(projected[0]["heading"], 90.1)

    def test_no_native_traffic_lights_is_an_explicit_empty_inventory(self):
        self.assertEqual(signal_inventory(ET.fromstring('<net/>')), [])

    def test_degenerate_source_tangent_is_not_silently_dropped(self):
        network = ET.fromstring('''<net><edge id="42"><lane id="42_0" index="0" shape="1,1 1,1" /></edge>
          <connection from="42" to="43" fromLane="0" tl="j" linkIndex="0" /></net>''')
        with self.assertRaisesRegex(ValueError, 'tangent is degenerate'):
            signal_inventory(network)


if __name__ == "__main__": unittest.main()
