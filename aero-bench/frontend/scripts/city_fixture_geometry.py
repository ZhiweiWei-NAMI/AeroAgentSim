"""Read source GLB triangles and measure their displayed street-fixture geometry."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon


# Displayed signal height. The measured traffic_light_4 arm starts at 0.5628 of the model
# height, so 6.4 m puts its lower edge at 3.60 m: above the declared 3.41 m motor height band
# (city_effective_fixtures.MOTOR_TOP_UP_M) by the 0.15 m fixture clearance. At the former
# 5.4 m the arm hung at 3.04 m over the carriageway.
SIGNAL_DISPLAY_HEIGHT_M = 6.4


def fixture_display_transform(kind: str) -> dict:
    """Machine-readable metadata for the same displayed_fixture_triangles contract."""
    if kind == 'signal':
        return {"height_scale": f"{SIGNAL_DISPLAY_HEIGHT_M}/(native_max_y-native_min_y)",
                "native_anchor_m": [1.12309, 0., -.24263], "up_offset_m": 0.,
                "viewer_yaw_deg": "source_location.heading"}
    if kind == 'street_lamp':
        return {"height_scale": "7.2/(native_max_y-native_min_y)",
                "native_anchor_m": ["native_max_x-0.2", 0., 0.],
                "up_offset_m": "0.225-native_min_y*height_scale",
                "viewer_yaw_deg": "-source_location.rotation_deg"}
    raise ValueError(f"Unknown displayed fixture kind: {kind}")


def glb_triangles(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if len(data) < 20 or struct.unpack_from('<III', data) != (0x46546c67, 2, len(data)):
        raise ValueError(f"Invalid GLB header: {path}")
    offset, description, binary = 12, None, None
    while offset < len(data):
        size, kind = struct.unpack_from('<II', data, offset)
        if offset + 8 + size > len(data):
            raise ValueError(f"Truncated GLB chunk: {path}")
        chunk = data[offset + 8:offset + 8 + size]
        if kind == 0x4e4f534a:
            description = json.loads(chunk)
        elif kind == 0x004e4942:
            binary = chunk
        offset += size + 8
    if description is None or binary is None:
        raise ValueError(f"GLB requires JSON and embedded binary chunks: {path}")
    types = {5120: 'i1', 5121: 'u1', 5122: '<i2', 5123: '<u2', 5125: '<u4', 5126: '<f4'}
    arity = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}

    def accessor(index):
        item = description['accessors'][index]
        view = description['bufferViews'][item['bufferView']]
        if 'sparse' in item or view['buffer'] != 0:
            raise ValueError("Street fixture accessors require the declared embedded buffer")
        dtype, count = np.dtype(types[item['componentType']]), arity[item['type']]
        start = view.get('byteOffset', 0) + item.get('byteOffset', 0)
        stride = view.get('byteStride', count * dtype.itemsize)
        return np.ndarray((item['count'], count), dtype=dtype, buffer=binary,
                          offset=start, strides=(stride, dtype.itemsize)).copy()

    triangles = []

    def visit(index, parent):
        node = description['nodes'][index]
        if 'matrix' in node:
            transform = np.array(node['matrix']).reshape(4, 4, order='F')
        else:
            x, y, z, w = node.get('rotation', [0, 0, 0, 1])
            transform = np.eye(4)
            transform[:3, :3] = np.array([
                [1-2*y*y-2*z*z, 2*x*y-2*z*w, 2*x*z+2*y*w],
                [2*x*y+2*z*w, 1-2*x*x-2*z*z, 2*y*z-2*x*w],
                [2*x*z-2*y*w, 2*y*z+2*x*w, 1-2*x*x-2*y*y]]) @ np.diag(node.get('scale', [1, 1, 1]))
            transform[:3, 3] = node.get('translation', [0, 0, 0])
        world = parent @ transform
        if 'mesh' in node:
            for primitive in description['meshes'][node['mesh']]['primitives']:
                if primitive.get('mode', 4) != 4:
                    raise ValueError("Street fixture geometry must contain explicit triangles")
                positions = accessor(primitive['attributes']['POSITION'])
                xyz = (np.c_[positions, np.ones(len(positions))] @ world.T)[:, :3]
                indices = (accessor(primitive['indices']).reshape(-1) if 'indices' in primitive
                           else np.arange(len(xyz)))
                triangles.append(xyz[indices.reshape(-1, 3)])
        for child in node.get('children', []):
            visit(child, world)

    for node in description['scenes'][description.get('scene', 0)]['nodes']:
        visit(node, np.eye(4))
    if not triangles:
        raise ValueError("Street fixture GLB contains no triangles")
    result = np.concatenate(triangles).astype(float)
    if not np.isfinite(result).all():
        raise ValueError("Street fixture GLB has nonfinite coordinates")
    return result


def displayed_fixture_triangles(triangles: np.ndarray, kind: str) -> np.ndarray:
    """Use the exact transforms in trafficLampTemplate and addStreetLamps."""
    minimum = triangles.min(axis=(0, 1))
    maximum = triangles.max(axis=(0, 1))
    if maximum[1] <= minimum[1]:
        raise ValueError("Street fixture has no measured height")
    if kind == 'signal':
        return (triangles - np.array([1.12309, 0, -.24263])) * (SIGNAL_DISPLAY_HEIGHT_M / (maximum[1] - minimum[1]))
    if kind == 'street_lamp':
        scale = 7.2 / (maximum[1] - minimum[1])
        result = (triangles - np.array([maximum[0] - .2, 0, 0])) * scale
        result[:, :, 1] += .225 - minimum[1] * scale
        return result
    raise ValueError(f"Unknown displayed fixture kind: {kind}")


def world_fixture_triangles(displayed_triangles: np.ndarray, kind: str, location: dict) -> np.ndarray:
    """Transform triangle vertices before unioning to preserve floating point validity."""
    if kind not in ('signal', 'street_lamp'):
        raise ValueError(f"Unknown displayed fixture kind: {kind}")
    angle = np.deg2rad(location['heading'] if kind == 'signal' else -location['rotation_deg'])
    cosine, sine = np.cos(angle), np.sin(angle)
    result = displayed_triangles.copy()
    east, south = displayed_triangles[:, :, 0], displayed_triangles[:, :, 2]
    result[:, :, 0] = cosine * east - sine * south + location['x']
    result[:, :, 2] = sine * east + cosine * south + location['z']
    if not np.isfinite(result).all():
        raise ValueError("World fixture vertices are not finite")
    return result


# Plan-view footprints and height-band clips are unioned on a 1 micrometre grid. A floating-point union of tens of
# thousands of rotated model triangles can raise a GEOS topology error; snapping each triangle
# and then snap-rounding the union is deterministic and changes the area by far less than the
# 1 mm surface grid used downstream.
FOOTPRINT_PRECISION_M = 1e-6


def _footprint_union(parts):
    snapped = [shapely.set_precision(part, FOOTPRINT_PRECISION_M) for part in parts if part.area > 1e-12]
    return shapely.union_all([part for part in snapped if not part.is_empty], grid_size=FOOTPRINT_PRECISION_M)


def projected_fixture(triangles: np.ndarray):
    return _footprint_union([Polygon(triangle[:, [0, 2]]) for triangle in triangles])


def fixture_in_height_band(triangles: np.ndarray, base_y: float, top_y: float):
    """Clip actual triangle faces to a building's vertical extent before projecting."""
    def clip(points, level, above):
        result = []
        for index, point in enumerate(points):
            previous = points[index - 1]
            inside = point[1] >= level if above else point[1] <= level
            previous_inside = previous[1] >= level if above else previous[1] <= level
            if inside != previous_inside:
                ratio = (level - previous[1]) / (point[1] - previous[1])
                result.append(previous + ratio * (point - previous))
            if inside:
                result.append(point)
        return result

    parts = []
    for triangle in triangles:
        if triangle[:, 1].max() < base_y or triangle[:, 1].min() > top_y:
            continue
        points = clip(list(triangle), base_y, True)
        if len(points) < 3:
            continue
        points = clip(points, top_y, False)
        if len(points) < 3:
            continue
        parts.append(Polygon([(point[0], point[2]) for point in points]))
    return _footprint_union(parts)
