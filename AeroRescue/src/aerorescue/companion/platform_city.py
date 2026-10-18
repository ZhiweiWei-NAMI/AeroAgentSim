"""Explicit official-city camera adapter for the software-model world.

The platform's existing Three.js capture path renders committed mock poses.
City collision is a conservative AABB envelope of those same installed GLBs;
this is not Gazebo/PX4 and does not claim triangle-contact accuracy.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import math
import os
import struct
import zlib
from itertools import product
from functools import lru_cache
from pathlib import Path

import numpy as np
from aerokernel import Dependency, Partition, Stamp
from aerokernel.sdk import ContextEngine
from aeroagentsim.engines.common import policies
from aeroagentsim.observations.committed import CommittedBrowserRenderer
from aeroagentsim.observations.contracts import CaptureRequest
from aeroagentsim.platform.plugins import EngineCatalog

from .geometry import quaternion_matrix

POSITION = 'aerorescue.visual.position'
ORIENTATION = 'aerorescue.visual.orientation'
TYPES = ['aerorescue:VisualAircraft', 'aerorescue:VisualPerson', 'aerorescue:VisualParcel', 'aerorescue:VisualReceiver']


@lru_cache(maxsize=1)
def city_geometry():
    """Read GLB POSITION bounds and node transforms; no hand-authored buildings."""
    from aeroagentsim.authoring.demo import city_file, detailed_city
    if not detailed_city():
        raise RuntimeError('platform_city requires the installed official Huangpu asset pack')
    city = json.loads(city_file('scene.json').read_text())
    obstacles, triangles = [], []
    for building in city['buildings']:
        path = building['url'].removeprefix('/assets/')
        raw = city_file(path).read_bytes()
        if raw[:4] != b'glTF' or raw[16:20] != b'JSON':
            raise ValueError('Expected an official GLB building: ' + path)
        doc = json.loads(raw[20:20+struct.unpack_from('<I', raw, 12)[0]])
        binary_start = 28+struct.unpack_from('<I', raw, 12)[0]
        def values(index):
            accessor = doc['accessors'][index]; view = doc['bufferViews'][accessor['bufferView']]
            count = {'SCALAR': 1, 'VEC3': 3}[accessor['type']]
            dtype = {5126: '<f4', 5125: '<u4', 5123: '<u2', 5121: 'u1'}[accessor['componentType']]
            offset = binary_start+view.get('byteOffset', 0)+accessor.get('byteOffset', 0)
            stride = view.get('byteStride', np.dtype(dtype).itemsize*count)
            return np.ndarray((accessor['count'], count), dtype=dtype, buffer=raw, offset=offset,
                              strides=(stride, np.dtype(dtype).itemsize)).copy()
        points = []
        def visit(index, parent):
            node = doc['nodes'][index]
            if 'matrix' in node:
                transform = np.asarray(node['matrix']).reshape(4, 4).T
            else:
                transform = np.eye(4)
                transform[:3, :3] = quaternion_matrix(node.get('rotation', [0, 0, 0, 1])) @ np.diag(node.get('scale', [1, 1, 1]))
                transform[:3, 3] = node.get('translation', [0, 0, 0])
            transform = parent @ transform
            if 'mesh' in node:
                for primitive in doc['meshes'][node['mesh']]['primitives']:
                    accessor = doc['accessors'][primitive['attributes']['POSITION']]
                    corners = np.array([list(p)+[1] for p in product(*zip(accessor['min'], accessor['max']))])
                    points.extend((corners @ transform.T)[:, :3])
                    vertices = values(primitive['attributes']['POSITION'])
                    homogeneous = np.column_stack((vertices, np.ones(len(vertices))))
                    render = (homogeneous @ transform.T)[:, :3] + [building['x'], building['y'], building['z']]
                    enu = render[:, [0, 2, 1]] * [1, -1, 1]
                    indices = values(primitive['indices']).ravel() if 'indices' in primitive else np.arange(len(vertices))
                    if primitive.get('mode', 4) != 4: raise ValueError('building primitive is not triangles')
                    triangles.extend(enu[indices.reshape(-1, 3)])
            for child in node.get('children', []): visit(child, transform)
        for index in doc['scenes'][doc.get('scene', 0)]['nodes']: visit(index, np.eye(4))
        if not points: raise ValueError('Building has no source geometry: ' + path)
        render = np.asarray(points) + [building['x'], building['y'], building['z']]
        enu = render[:, [0, 2, 1]] * [1, -1, 1]
        obstacles.append({'id': building['id'], 'lower': enu.min(0).tolist(), 'upper': enu.max(0).tolist(),
                          'source_asset': path, 'collision_model': 'conservative-source-mesh-aabb'})
    return obstacles, np.asarray(triangles)


def city_obstacles():
    return copy.deepcopy(city_geometry()[0])


def city_surface_height(x, y):
    """Highest actual source-mesh triangle below the vertical ray, plus ground."""
    triangles = city_geometry()[1]
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    v0, v1, p = b-a, c-a, np.array([x, y])-a[:, :2]
    determinant = v0[:, 0]*v1[:, 1]-v0[:, 1]*v1[:, 0]
    with np.errstate(divide='ignore', invalid='ignore'):
        u = (p[:, 0]*v1[:, 1]-p[:, 1]*v1[:, 0])/determinant
        v = (v0[:, 0]*p[:, 1]-v0[:, 1]*p[:, 0])/determinant
        valid = (abs(determinant) > 1e-12) & (u >= -1e-9) & (v >= -1e-9) & (u+v <= 1+1e-9)
        z = a[:, 2]+u*v0[:, 2]+v*v1[:, 2]
    return max(0., float(np.max(z[valid]))) if valid.any() else 0.


def prepare_platform_scene(scene, roof_id):
    """Use an explicitly selected source roof, with no target-dependent view choice.

    The caller authors mission geometry separately; incompatible old gray-world
    polygons are rejected rather than silently translated into this city.
    """
    scene = copy.deepcopy(scene)
    obstacles = city_obstacles()
    roof = next((row for row in obstacles if row['id'] == roof_id), None)
    if roof is None: raise ValueError('Unknown source-city roof_id: ' + roof_id)
    scene['public_map']['obstacles'] = obstacles
    scene['public_map']['rescue_roof_id'] = roof_id
    scene['public_map']['geometry_source'] = {'asset_pack': 'huangpu-city/1.1.0',
        'frame': 'ENU metres; render [x,y,z] -> [x,-z,y]',
        'collision_model': 'conservative source GLB AABBs, not triangle surfaces'}
    scene.setdefault('mock_backend', {})['visual_backend'] = 'platform_city'
    scene['mock_backend']['platform_city'] = {'roof_id': roof_id}
    return scene


def validate_geometry(scene):
    """Ensure motion/contact and the camera use the same fixed city geometry."""
    expected = city_obstacles()
    actual = scene['public_map']['obstacles']
    if actual != expected:
        raise ValueError('platform_city public obstacles must come from city_obstacles(), not the legacy gray world')
    cfg = scene['mock_backend']['platform_city']
    roof = next(row for row in expected if row['id'] == cfg['roof_id'])
    polygon = scene['public_map']['receiver_polygon']
    if any(not (roof['lower'][0] <= x <= roof['upper'][0] and roof['lower'][1] <= y <= roof['upper'][1]) for x, y in polygon):
        raise ValueError('receiver polygon is outside the selected source-city roof envelope')
    if abs(scene['payload']['receiver_pose'][2] - scene['payload']['receiver_size'][2]/2 - roof['upper'][2]) > 1e-6:
        raise ValueError('receiver surface support must match the selected source-city roof height')
    if any(abs(city_surface_height(x, y)-roof['upper'][2]) > 1e-5 for x, y in polygon):
        raise ValueError('receiver polygon is not supported by the actual source-city roof triangles')
    for uid, v in scene['vehicles'].items():
        if any(all(o['lower'][i] < v['spawn'][i] < o['upper'][i] for i in range(3)) for o in expected):
            raise ValueError('vehicle spawn lies inside a source-city obstacle: ' + uid)
        if abs(v['spawn'][2]-city_surface_height(*v['spawn'][:2])-v['spawn_clearance_m']) > 1e-5:
            raise ValueError('vehicle spawn must match source geometry plus its declared clearance: ' + uid)
    return expected


def add_visual_document(document, scene):
    """Declare only simulator-owned display facts in the existing kernel."""
    registry = document['registry']
    registry.setdefault('types', []).append({'id': 'aerorescue:VisualBody', 'parents': [], 'abstract': True})
    registry['types'].extend({'id': typ, 'parents': ['aerorescue:VisualBody'], 'abstract': False} for typ in TYPES)
    for name, length in [(POSITION, 3), (ORIENTATION, 4)]:
        registry['fields'].append({'id': name, 'type': 'aerorescue:VisualBody',
            'schema': {'type': 'vector', 'length': length, 'items': {'type': 'number'}},
            'metadata': {'role': 'state', 'frame': 'enu', 'source': 'explicit_mock_world'}})
    document['entities'].extend({'id': 'visual/'+uid, 'type': TYPES[0], 'facts': {}} for uid in scene['vehicles'])
    document['entities'].extend({'id': 'visual/person/'+str(i), 'type': TYPES[1], 'facts': {}} for i, _ in enumerate(scene['mock_backend'].get('people', [])))
    if scene.get('payload'):
        document['entities'].extend([{'id': 'visual/parcel', 'type': TYPES[2], 'facts': {}},
                                    {'id': 'visual/receiver', 'type': TYPES[3], 'facts': {}}])
    document['bindings']['rules'].append({'writer': 'visual_state', 'type': 'aerorescue:VisualBody', 'fields': [POSITION, ORIENTATION]})
    document['bindings']['lifecycle'].append({'controller': 'visual_state', 'type': 'aerorescue:VisualBody'})
    document['engines']['visual_state'] = {'plugin': 'mock_visual_state', 'config': {}}


class VisualState(ContextEngine):
    """Publish actual world geometry after the flight owner's committed boundary."""
    def __init__(self, build, world):
        self.world = world
        self.refs = {ref.id: ref for ref in build.entities if ref.id.startswith('visual/')}
        super().__init__(Partition(build.id, build.id, lifecycle=True,
            produces=(POSITION, ORIENTATION), consumes=(Dependency('e1.px4.position'),)),
            policies=policies((POSITION, ORIENTATION)))

    def bootstrap(self, ctx):
        for ref in self.refs.values(): ctx.create(ref)
        self.publish(ctx)

    def on_inputs(self, ctx):
        self.publish(ctx)

    def publish(self, ctx):
        world = self.world
        if world.now != ctx.now.ns:
            raise RuntimeError('visual publication does not match the granted mock frontier')
        for uid, vehicle in world.vehicles.items():
            ref = self.refs['visual/'+uid]
            ctx.set(ref, POSITION, vehicle['position'].tolist())
            ctx.set(ref, ORIENTATION, [0, 0, math.sin(vehicle['yaw']/2), math.cos(vehicle['yaw']/2)])
        for i, person in enumerate(world.config.get('people', [])):
            ref = self.refs['visual/person/'+str(i)]
            ctx.set(ref, POSITION, person['feet_enu_m'])
            yaw = math.radians(person.get('heading_enu_deg', 0))
            ctx.set(ref, ORIENTATION, [0, 0, math.sin(yaw/2), math.cos(yaw/2)])
        if world.payload:
            p = world.payload
            position = p['position'] if p['released'] else (world.vehicles[p['carrier']]['position'] + world.scene['payload']['attachment_offset_enu_m']).tolist()
            ctx.set(self.refs['visual/parcel'], POSITION, position)
            ctx.set(self.refs['visual/parcel'], ORIENTATION, [0, 0, 0, 1])
            ctx.set(self.refs['visual/receiver'], POSITION, world.scene['payload']['receiver_pose'][:3])
            ctx.set(self.refs['visual/receiver'], ORIENTATION, [0, 0, 0, 1])


class VisualCatalog(EngineCatalog):
    def __init__(self, base, world):
        self.base, self.world = base, world
    def build(self, plugin, context):
        return VisualState(context, self.world) if plugin == 'mock_visual_state' else self.base.build(plugin, context)


def parcel_gltf(config):
    """A small textured box authored from the public inventory dimensions/tag."""
    from PIL import Image
    size = np.asarray(config['package_size_m'], float)/2
    # Body FLU/ENU vertices; the existing entity layer applies ENU->render basis.
    vertices, normals, uv = [], [], []
    for axis, sign in [(0, 1), (0, -1), (1, 1), (1, -1), (2, 1), (2, -1)]:
        other = [i for i in range(3) if i != axis]
        corners = []
        for a, b in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
            point = np.zeros(3); point[axis] = sign*size[axis]; point[other] = [a*size[other[0]], b*size[other[1]]]
            corners.append(point)
        for j in [0, 1, 2, 0, 2, 3]:
            vertices.append(corners[j]); normal = [0., 0., 0.]; normal[axis] = sign; normals.append(normal)
            uv.append([(corners[j][0]/size[0]+1)/2, (1-corners[j][1]/size[1])/2] if axis == 2 and sign == 1 else [-1., -1.])
    data = b''; views = []; accessors = []
    for values, kind in [(vertices, 'VEC3'), (normals, 'VEC3'), (uv, 'VEC2')]:
        array = np.asarray(values, dtype='<f4'); views.append({'buffer': 0, 'byteOffset': len(data), 'byteLength': array.nbytes}); data += array.tobytes()
        accessors.append({'bufferView': len(views)-1, 'componentType': 5126, 'count': len(values), 'type': kind,
                          'min': array.min(0).tolist(), 'max': array.max(0).tolist()})
    # Atlas: white tagged top in upper-right half, orange elsewhere. The box's
    # side UV points sample the orange lower-left region, with clamp-to-edge.
    texture = Image.new('RGB', (512, 512), config.get('color_rgb', (224, 115, 25)))
    marker = config.get('visual_marker')
    if marker:
        import cv2
        texture.paste((255, 255, 255), (256, 256, 512, 512))
        pixels = round(256*marker['size_m']/(size[0]*2))
        if not 0 < marker['size_m'] < min(size[:2]*2): raise ValueError('marker must fit the public box top')
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, marker['dictionary']))
        tag = cv2.aruco.generateImageMarker(dictionary, marker['id'], pixels, borderBits=marker.get('border_bits', 1))
        texture.paste(Image.fromarray(tag).convert('RGB'), (256+(256-pixels)//2, 256+(256-pixels)//2))
    # glTF image origin is top-left; top UV maps into the atlas bottom-right.
    uv_array = np.asarray(uv, dtype='<f4')
    top = uv_array[:, 0] >= 0
    uv_array[top] = .5 + uv_array[top]*.5
    uv_array[~top] = .1
    offset = views[2]['byteOffset']; data = data[:offset]+uv_array.tobytes()
    image = io.BytesIO(); texture.save(image, format='PNG')
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
        'buffers': [{'uri': 'data:application/octet-stream;base64,'+base64.b64encode(data).decode(), 'byteLength': len(data)}],
        'bufferViews': views, 'accessors': accessors,
        'images': [{'uri': 'data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()}],
        'textures': [{'source': 0}], 'materials': [{'doubleSided': True, 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}, 'roughnessFactor': .8, 'metallicFactor': 0}}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 1, 'TEXCOORD_0': 2}, 'material': 0}]}]}
    return 'data:model/gltf+json;base64,'+base64.b64encode(json.dumps(doc).encode()).decode()


class PlatformCityCamera:
    def __init__(self, world, simulation):
        from aeroagentsim.assets import model_catalog
        from aeroagentsim.authoring.demo import city_presentation, CITY_ASSET_ID
        self.world, self.simulation = world, simulation
        self.catalog = model_catalog()
        modules = Path(os.environ.get('AEROAGENTSIM_FRONTEND_NODE_MODULES', Path(__file__).resolve().parents[4]/'frontend/node_modules'))
        self.renderer = CommittedBrowserRenderer({'mode': 'traffic_browser', 'node_modules': str(modules),
            'timeout_s': 120, 'asset_digest': CITY_ASSET_ID}, world.root)
        self.renderer.header = {'contract': 'aeroagentsim.viewer-feed/v1', 'runId': world.root.name,
            'registryDigest': 'runtime-registry', 'start': {'ns': '0', 'microstep': 0},
            'types': [{'typeId': t.id, 'displayName': t.id, 'ancestors': []} for t in simulation.scenario.registry.types],
            'fields': [{'fieldId': f.id, 'displayName': f.id, 'valueType': f.schema['type'], 'frame': 'enu'} for f in simulation.scenario.registry.fields],
            'presentation': [{'typeId': typ, 'positionField': POSITION, 'orientationField': ORIENTATION, 'frame': 'enu',
                'visual': {'kind': 'model', 'asset': asset, 'scale': 1.0,
                           **({'catalog': 'uav'} if typ == TYPES[0] else {})}} for typ, asset in [
                    (TYPES[0], self.catalog['x500']['url']), (TYPES[1], self.catalog['pedestrian']['url']),
                    (TYPES[2], parcel_gltf(world.scene['payload'])),
                    (TYPES[3], parcel_gltf({'package_size_m': world.scene['payload']['receiver_size'], 'color_rgb': (150, 150, 150)}))]],
            'scene': city_presentation(), 'modelCatalog': self.catalog}
        self.renderer.header['scene']['camera']['target'] = [0, 0, 0]

    def render(self, uid):
        world = self.world; view = self.simulation.kernel.view()
        if view.instant.ns != world.now: raise RuntimeError('camera cannot acquire across an uncommitted world advance')
        ref = next(r for r in self.simulation.scenario.manifest.entities if r.id == 'visual/'+uid)
        fact = view.field((ref, POSITION), view.instant)
        attitude = view.field((ref, ORIENTATION), view.instant)
        estimate = world.estimate(uid)
        if list(fact.value) != estimate['position_enu_m']: raise RuntimeError('camera pose is not the current committed aircraft position')
        c = world.scene['simulator']['rgbd_camera']; w, h = c['width'], c['height']
        x, y, z, roll, pitch, yaw = c['pose_flu']
        cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
        rotation = np.array([[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr], [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr], [-sp, cp*sr, cp*cr]])
        mount = np.eye(4); mount[:3,:3] = np.diag([1,-1,-1]) @ rotation @ np.array([[0,0,1],[-1,0,0],[0,-1,0]])
        mount[:3,3] = [x, -y, -z]
        body = quaternion_matrix(list(attitude.value)) @ np.diag([1,-1,-1])
        if not np.allclose(body, quaternion_matrix(estimate['attitude_body_frd_to_world_enu_xyzw']), atol=1e-12):
            raise RuntimeError('camera attitude is not the current committed aircraft attitude')
        eye = np.asarray(estimate['position_enu_m']) + body @ mount[:3,3]; optical = body @ mount[:3,:3]
        fov = math.degrees(2*math.atan(h/w*math.tan(c['horizontal_fov_rad']/2)))
        frame_id = f'{world.run_id}:{world.epoch}:{uid}:platform-rgbd:{world.now}'
        request = CaptureRequest(frame_id, ref.run_id, ref, view.cut,
            {'revision': 'mounted-rgbd/v1', 'frame': 'enu', 'eye': eye.tolist(),
             'target': (eye+optical[:,2]).tolist(), 'up': (-optical[:,1]).tolist(),
             'exclude_actor': True, 'fov': fov, 'near': c['near_m'], 'far': c['far_m'], 'depth': True},
            self.renderer.asset_id, Stamp('canonical', world.now, 1, 'canonical'), w, h, 120)
        rendered = self.renderer.render_cut(request, view._store)
        if rendered.depth is None or rendered.calibration is None or rendered.camera_pose is None:
            raise RuntimeError('platform camera returned no registered RGB-D metadata')
        raw = base64.b64decode(rendered.depth['data_base64'], validate=True)
        if len(raw) != w*h*4: raise ValueError('platform depth dimensions do not match RGB')
        metadata = {'run_id': world.run_id, 'uav_id': uid, 'sensor_id': c['sensor_id'], 'frame_id': frame_id,
            'source': 'platform_threejs_city_camera', 'execution_backend': 'mock', 'source_clock': 'mock_episode',
            'source_time': world.now, 'clock_epoch': world.epoch, 'observed_at': world.now,
            'depth_observed_at': world.now, 'available_sim_ns': world.now, 'coordinate_frame': 'camera_optical',
            'vehicle_estimate': estimate, 'localization_status': 'available', 'source_cut': request.to_data()['source_cut'],
            'calibration': {**rendered.calibration, 'T_body_frd_from_camera_optical': mount.tolist(),
                'orientation': c.get('orientation', 'configured'),
                'horizontal_fov_deg': math.degrees(2*math.atan(w/(2*rendered.calibration['K'][0]))),
                'vertical_fov_deg': math.degrees(2*math.atan(h/(2*rendered.calibration['K'][4]))),
                'version': world.epoch+':platform-camera', 'extrinsic_source': 'configured mock mount',
                'render_model': 'official Huangpu source meshes; static skinned person; inventory box'},
            'camera_pose': rendered.camera_pose}
        directory = world.root/'platform-frames'/uid/str(world.now); directory.mkdir(parents=True, exist_ok=True)
        (directory/'rgb.png').write_bytes(rendered.png); (directory/'depth.f32').write_bytes(raw)
        (directory/'capture.json').write_text(json.dumps({'request': request.to_data(), 'png_path': 'rgb.png',
            'depth': {k:v for k,v in rendered.depth.items() if k != 'data_base64'} | {'data_path': 'depth.f32'},
            'calibration': rendered.calibration, 'camera_pose': rendered.camera_pose, 'observation': metadata}, indent=2))
        return metadata, {'rgb': rendered.png, 'depth': zlib.compress(raw, 3)}

    def close(self): self.renderer.close()
