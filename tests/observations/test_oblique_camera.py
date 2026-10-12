"""The sensor changes observation framing, never the actor or target state."""
from dataclasses import replace
from aeroagentsim.observations.committed import committed_scene
from tests.observations.test_capture import ACTOR, FixtureRenderer, png, setup_capture
from tests.observations.test_committed_camera import HEADER

CONFIG = {'revision': 'uav-gimbal-oblique/v1', 'frame': 'enu', 'pitch_deg': 50.0,
          'fov': 96.0, 'coincident_azimuth_deg': -90.0, 'target_field': 'pose'}
SUBJECT = {'id': ACTOR.id, 'generation': ACTOR.generation, 'field': 'pose'}


def test_nonvisual_target_is_available_at_exact_capture_cut(tmp_path):
    kernel, _, request = setup_capture(tmp_path, FixtureRenderer(png()))
    # A semantic target needs its actual fact even without a render binding.
    camera = {**CONFIG, 'subject': SUBJECT}
    request = replace(request,camera=camera)
    header = {**HEADER,'presentation':[]}
    assert committed_scene(kernel.view()._store,request,header)['entities'][0]['fields'][0]['value'] == [3.0, 1.0, 2.0]
    old = replace(request,camera={'revision':'traffic-nadir/v1','fov':55})
    assert committed_scene(kernel.view()._store,old,header)['entities'] == []
    kernel.close()
