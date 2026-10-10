"""Reject source drift before measuring any static street fixture."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location('fixture_builder', SCRIPT_DIR / 'build-city-static-fixture-clearance.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
ROOT = SCRIPT_DIR.parents[1]


class SourceBindingTest(unittest.TestCase):
    def inputs(self):
        public = ROOT / 'frontend/public'
        return [ROOT / 'validation/scene-compiler-shanghai-huangpu-east-v1/metadata/objects.json',
                public / 'building-renders/shanghai-huangpu-east-v1/manifest.json',
                public / 'city-presentation/huangpu-ground-road-preview-v1.json',
                public / 'city-presentation/huangpu-ground-sumo-preview-v1.json',
                public / 'models/incoming/furniture/glb/traffic_light_4.glb',
                public / 'models/incoming/furniture/glb/street_light_8.glb']

    def modified_input(self, index, mutate, message):
        inputs = self.inputs()
        value = json.loads(inputs[index].read_text())
        mutate(value)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'changed-source.json'
            path.write_text(json.dumps(value))
            inputs[index] = path
            with self.assertRaisesRegex(ValueError, message):
                builder.build(*inputs)

    def test_render_must_bind_actual_canonical_building_bytes(self):
        self.modified_input(1, lambda value: value['scene'].update(objects_json_sha256='0' * 64),
                            'canonical render source')

    def test_render_cannot_omit_a_source_building(self):
        self.modified_input(1, lambda value: value['buildings'].pop(), 'render inventory')

    def test_traffic_from_another_network_cannot_supply_fixture_locations(self):
        self.modified_input(3, lambda value: value.update(source_network_sha256='0' * 64), 'different sources')

    def test_changed_render_bytes_cannot_reuse_a_previous_traffic_recording(self):
        inputs = self.inputs()
        render = json.loads(inputs[1].read_text())
        # A changed render release must be recorded again even if its source
        # footprints and inventory have not changed.
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / 'new-render.json'
            changed.write_text(json.dumps(render, sort_keys=True))
            self.assertNotEqual(builder.sha256(changed), builder.sha256(inputs[1]))
            inputs[1] = changed
            with mock.patch.object(builder, 'glb_triangles', side_effect=AssertionError('geometry was reached')) as geometry:
                with self.assertRaisesRegex(ValueError, 'not recorded against these rendered buildings'):
                    builder.build(*inputs)
                geometry.assert_not_called()

    def test_traffic_visual_basis_must_describe_the_actual_building_source(self):
        for key, value in [('objects_json_sha256', '0' * 64),
                           ('render_manifest_sha256', '0' * 64),
                           ('pack_source_sha256', '0' * 64),
                           ('rendered_footprint_count', 413),
                           ('policy', 'placement-proxies-only'),
                           ('route_obstacle_basis', 'placement-proxies-only')]:
            with self.subTest(key=key):
                self.modified_input(3, lambda item: item['visual_obstacle_basis'].update({key: value}),
                                    'not recorded against these rendered buildings')

    def test_missing_recorded_visual_basis_is_rejected(self):
        self.modified_input(3, lambda value: value.pop('visual_obstacle_basis'),
                            'not recorded against these rendered buildings')

    def test_render_origin_must_match_source_enu_origin(self):
        self.modified_input(1, lambda value: value['scene']['origin_wgs84'].update(ellipsoid_height_m=51),
                            'origins differ')


if __name__ == '__main__':
    unittest.main()
