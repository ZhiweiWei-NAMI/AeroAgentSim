"""Convert curated full UAV STL assemblies from local ZIPs to preview GLBs.

The output is centered and scaled for comparison in the asset library. It is
not a source of physical dimensions or an authority for a formal run.
"""

from __future__ import annotations

import json
import re
import struct
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
ARCHIVES = ROOT.parent / "validation/downloaded-assets/quark-drone-models/003 无人机"
OUTPUT = ROOT / "public/models/uav"
MANIFEST = Path(__file__).with_name("uav-preview-models.json")
LIVERY_COLORS = {
    "airframe": "#a7b9be",
    "graphite": "#34474e",
    "carbon": "#243238",
    "amber": "#bd7536",
    "metal": "#5c7079",
    "rotor": "#202b30",
}
STL_FACET = np.dtype(
    [("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")]
)


def pad(data: bytes, byte: bytes = b"\0") -> bytes:
    return data + byte * (-len(data) % 4)


def encode_glb(document: dict, binary: bytes) -> bytes:
    encoded = pad(json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode(), b" ")
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return b"".join((
        struct.pack("<4sII", b"glTF", 2, length),
        struct.pack("<I4s", len(encoded), b"JSON"), encoded,
        struct.pack("<I4s", len(binary), b"BIN\0"), binary,
    ))


def parse_stl(raw: bytes, source: str) -> np.ndarray:
    if len(raw) >= 84:
        count = struct.unpack_from("<I", raw, 80)[0]
        if len(raw) == 84 + count * 50:
            vertices = np.frombuffer(raw, dtype=STL_FACET, offset=84, count=count)["vertices"].reshape(-1, 3)
            if not np.isfinite(vertices).all():
                raise ValueError(f"Non-finite STL vertices: {source}")
            return vertices
    if not raw.lstrip().startswith(b"solid"):
        raise ValueError(f"Unrecognized STL encoding: {source}")
    values = []
    for line in raw.splitlines():
        parts = line.split()
        if parts and parts[0].lower() == b"vertex":
            if len(parts) != 4:
                raise ValueError(f"Invalid ASCII STL vertex: {source}")
            values.append((float(parts[1]), float(parts[2]), float(parts[3])))
    if not values or len(values) % 3:
        raise ValueError(f"Invalid ASCII STL triangle count: {source}")
    vertices = np.asarray(values, dtype="<f4")
    if not np.isfinite(vertices).all():
        raise ValueError(f"Non-finite STL vertices: {source}")
    return vertices


def load_stl(entry: dict) -> tuple[np.ndarray, list[str]]:
    archive_name = entry["archive"]
    with zipfile.ZipFile(ARCHIVES / archive_name) as archive:
        if "member" in entry:
            matches = [name for name in archive.namelist() if Path(name).name == entry["member"]]
            if len(matches) != 1:
                raise ValueError(f"Expected one {entry['member']} in {archive_name}, found {len(matches)}")
        else:
            prefix = entry["all_stl_under"]
            matches = sorted(name for name in archive.namelist() if name.startswith(prefix) and name.lower().endswith(".stl"))
            if not matches:
                raise ValueError(f"No STL members under {prefix} in {archive_name}")
        arrays = [parse_stl(archive.read(name), f"{archive_name}:{name}") for name in matches]
    return np.concatenate(arrays, axis=0), matches


def geometry(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
    span = np.ptp(unique, axis=0)
    largest = float(span.max())
    if largest <= 0:
        raise ValueError("STL has no spatial extent")
    positions = ((unique - (unique.min(axis=0) + unique.max(axis=0)) / 2) * (3.1 / largest)).astype("<f4")
    indices = inverse.astype("<u4").reshape(-1, 3)
    triangles = positions[indices]
    face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals = np.zeros_like(positions)
    for vertex_column in range(3):
        np.add.at(normals, indices[:, vertex_column], face_normals)
    lengths = np.linalg.norm(normals, axis=1)
    normals[lengths > 0] /= lengths[lengths > 0, None]
    # STL has no UVs. Each triangle uses the plane facing its dominant normal;
    # shared positions split only where that projection changes.
    planes = np.argmax(np.abs(face_normals), axis=1).astype(np.int64)
    keys = indices.reshape(-1).astype(np.int64) * 3 + np.repeat(planes, 3)
    unique_keys, textured_indices = np.unique(keys, return_inverse=True)
    source_indices = unique_keys // 3
    textured_planes = unique_keys % 3
    tex_positions = positions[source_indices]
    tex_normals = normals[source_indices].astype("<f4")
    uv = np.empty((len(unique_keys), 2), dtype="<f4")
    for plane, axes in enumerate(((2, 1), (0, 2), (0, 1))):
        selected = textured_planes == plane
        uv[selected] = tex_positions[selected][:, axes] * 0.75 + 0.5
    return tex_positions, tex_normals, uv, textured_indices.astype("<u4")


def linear_color(hex_color: str) -> tuple[float, float, float, float]:
    srgb = np.asarray([int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)])
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    return (*map(float, linear), 1.0)


def surface_textures(
    base_color: tuple[float, float, float, float], surface: str, roughness: float, metalness: float,
) -> tuple[bytes, bytes, bytes]:
    """Bake restrained aviation finishes into portable glTF color, normal and ORM maps."""
    if surface not in {"paint", "carbon", "metal", "polymer"}:
        raise ValueError(f"Unsupported surface: {surface}")
    size = 256
    y, x = np.indices((size, size))
    linear = np.asarray(base_color[:3])
    srgb = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055)
    base = srgb * 255
    rng = np.random.default_rng(7)
    grain = rng.normal(0, 0.8 if surface == "paint" else 1.2, (size, size))
    height = rng.normal(0, 0.025, (size, size)).astype(np.float32)
    if surface == "carbon":
        # A low-contrast micro weave reads as resin at close range without a tiled checkerboard.
        weave = np.cos(x * np.pi / 4) * np.cos(y * np.pi / 4)
        grain += weave * 1.8
        height += (weave * 0.055).astype(np.float32)
    elif surface == "metal":
        grain += rng.normal(0, 0.6, (size, 1))
    albedo = Image.fromarray((base + grain[..., None]).clip(0, 255).astype("uint8"), "RGB")
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * 0.14
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * 0.14
    normal = np.stack((-dx, -dy, np.ones_like(dx)), axis=-1)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    normal_image = Image.fromarray(((normal * 0.5 + 0.5) * 255).astype("uint8"), "RGB")
    orm = np.empty((size, size, 3), dtype=np.uint8)
    orm[..., 0] = 255
    orm[..., 1] = np.clip((roughness + rng.normal(0, 0.009, (size, size))) * 255, 0, 255).astype("uint8")
    orm[..., 2] = round(metalness * 255)
    outputs = []
    for image in (albedo, normal_image, Image.fromarray(orm, "RGB")):
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        outputs.append(buffer.getvalue())
    return outputs[0], outputs[1], outputs[2]


def material_scheme(positions: np.ndarray, indices: np.ndarray, base_color: tuple[float, float, float, float], scheme: str) -> tuple[np.ndarray, list[tuple[str, tuple[float, float, float, float], str, float, float, float]]]:
    triangles = positions[indices.reshape(-1, 3)]
    centers = triangles.mean(axis=1)
    low = positions.min(axis=0)
    span = np.ptp(positions, axis=0)
    y = (centers[:, 1] - low[1]) / max(float(span[1]), 1e-6)
    groups = np.zeros(len(centers), dtype=np.uint8)
    if scheme == "multirotor":
        radius = np.linalg.norm(centers[:, [0, 2]], axis=1)
        max_radius = max(float(radius.max()), 1e-6)
        groups[(radius < max_radius * 0.39) & (y > 0.48)] = 1
        groups[(radius > max_radius * 0.57) & (y > 0.62)] = 2
        groups[(radius > max_radius * 0.57) & (y > 0.82)] = 3
        groups[y < 0.23] = 2
        materials = [
            ("Carbon composite frame", linear_color(LIVERY_COLORS["carbon"]), "carbon", 0.0, 0.56, 0.12),
            ("Amber equipment shell", linear_color(LIVERY_COLORS["amber"]), "paint", 0.0, 0.38, 0.34),
            ("Anodized motor and fittings", linear_color(LIVERY_COLORS["metal"]), "metal", 0.78, 0.38, 0.0),
            ("Graphite rotor and trim", linear_color(LIVERY_COLORS["rotor"]), "polymer", 0.0, 0.69, 0.0),
        ]
    elif scheme == "fixedwing":
        face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        face_normals /= np.maximum(np.linalg.norm(face_normals, axis=1, keepdims=True), 1e-9)
        groups[face_normals[:, 1] < -0.35] = 1
        wing_axis = 0 if span[0] >= span[2] else 2
        mid = (positions[:, wing_axis].min() + positions[:, wing_axis].max()) / 2
        groups[(np.abs(centers[:, wing_axis] - mid) > float(span[wing_axis]) * 0.40) & (face_normals[:, 1] > 0.2)] = 2
        standard_color = linear_color(LIVERY_COLORS["airframe"])
        airframe_color = tuple(0.88 * standard_color[i] + 0.12 * base_color[i] for i in range(3)) + (1.0,)
        materials = [
            ("Satin ceramic airframe", airframe_color, "paint", 0.0, 0.40, 0.30),
            ("Graphite lower airframe", linear_color(LIVERY_COLORS["graphite"]), "polymer", 0.0, 0.67, 0.0),
            ("Amber safety tips", linear_color(LIVERY_COLORS["amber"]), "paint", 0.0, 0.42, 0.28),
        ]
    else:
        raise ValueError(f"Unsupported material scheme: {scheme}")
    return groups, materials


def source_livery_finish(source_color: list[float], has_image: bool) -> tuple[str, tuple[float, float, float, float], float, float, float]:
    """Recolor 3DXML parts by source material while keeping image artwork and signal colors."""
    if has_image:
        return "Source image", (1.0, 1.0, 1.0, 1.0), 0.0, 0.48, 0.0
    red, green, blue = source_color
    if max(source_color) > 0.75 and max(source_color) - min(source_color) > 0.55:
        if red > 0.75 and green > 0.5 and blue < 0.55:
            return "Amber marking", linear_color(LIVERY_COLORS["amber"]), 0.0, 0.42, 0.28
        return "Source signal marking", (*source_color, 1.0), 0.0, 0.48, 0.0
    if sum(source_color) / 3 >= 0.55:
        return "Satin ceramic airframe", linear_color(LIVERY_COLORS["airframe"]), 0.0, 0.40, 0.30
    if sum(source_color) / 3 >= 0.12:
        return "Graphite composite", linear_color(LIVERY_COLORS["graphite"]), 0.0, 0.67, 0.0
    return "Carbon trim", linear_color(LIVERY_COLORS["carbon"]), 0.0, 0.56, 0.12


def make_glb(
    positions: np.ndarray,
    normals: np.ndarray,
    uv: np.ndarray,
    indices: np.ndarray,
    archive_name: str,
    member_names: list[str],
    base_color: tuple[float, float, float, float],
    scheme: str,
) -> bytes:
    groups, material_definitions = material_scheme(positions, indices, base_color, scheme)
    chunks: list[bytes] = []
    views: list[dict] = []

    def add_view(data: bytes, target: int | None = None) -> int:
        view = {"buffer": 0, "byteOffset": sum(map(len, chunks)), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        views.append(view)
        chunks.append(pad(data))
        return len(views) - 1

    position_view = add_view(positions.tobytes(), 34962)
    normal_view = add_view(normals.tobytes(), 34962)
    uv_view = add_view(uv.tobytes(), 34962)
    accessors = [
        {
            "bufferView": position_view, "componentType": 5126, "count": len(positions), "type": "VEC3",
            "min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist(),
        },
        {"bufferView": normal_view, "componentType": 5126, "count": len(normals), "type": "VEC3"},
        {"bufferView": uv_view, "componentType": 5126, "count": len(uv), "type": "VEC2"},
    ]
    primitives = []
    materials = []
    images = []
    textures = []
    triangle_indices = indices.reshape(-1, 3)
    for group_id, (name, color, surface, metalness, roughness, clearcoat) in enumerate(material_definitions):
        selected = triangle_indices[groups == group_id].reshape(-1)
        if not len(selected):
            continue
        index_accessor = len(accessors)
        accessors.append({"bufferView": add_view(selected.astype("<u4").tobytes(), 34963), "componentType": 5125, "count": len(selected), "type": "SCALAR"})
        albedo, normal_map, orm_map = surface_textures(color, surface, roughness, metalness)
        albedo_image = len(images)
        images.append({"bufferView": add_view(albedo), "mimeType": "image/png"})
        normal_image = len(images)
        images.append({"bufferView": add_view(normal_map), "mimeType": "image/png"})
        orm_image = len(images)
        images.append({"bufferView": add_view(orm_map), "mimeType": "image/png"})
        albedo_texture = len(textures)
        textures.append({"source": albedo_image, "sampler": 0})
        normal_texture = len(textures)
        textures.append({"source": normal_image, "sampler": 0})
        orm_texture = len(textures)
        textures.append({"source": orm_image, "sampler": 0})
        material_index = len(materials)
        material = {
            "name": name, "pbrMetallicRoughness": {
                "baseColorTexture": {"index": albedo_texture},
                "metallicRoughnessTexture": {"index": orm_texture},
                "metallicFactor": 1, "roughnessFactor": 1,
            },
            "normalTexture": {"index": normal_texture, "scale": 0.32},
            "occlusionTexture": {"index": orm_texture},
            "doubleSided": True,
            "extras": {"livery_role": name, "surface": surface},
        }
        if clearcoat:
            material["extensions"] = {"KHR_materials_clearcoat": {
                "clearcoatFactor": clearcoat, "clearcoatRoughnessFactor": 0.25,
            }}
        materials.append(material)
        primitives.append({"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": index_accessor, "material": material_index})
    binary = b"".join(chunks)
    document = {
        "asset": {
            "version": "2.0", "generator": "AERO-BENCH aviation preview converter",
            "extras": {"source_archive": archive_name, "source_members": member_names, "scale": "centered; maximum extent normalized to 3.1 preview units", "texture_origin": "AERO-BENCH standard aviation PBR livery; not source manufacturer livery", "livery_version": "aviation-v1", "material_scheme": scheme},
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": primitives}],
        "materials": materials,
        "extensionsUsed": ["KHR_materials_clearcoat"] if any("extensions" in material for material in materials) else [],
        "images": images,
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "textures": textures,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    return encode_glb(document, binary)


def make_3dxml_glb(raw: bytes, archive_name: str, member_name: str) -> bytes:
    """Preserve the ScanEagle 3DXML's per-part colors, UVs, and image links."""
    namespace = {"d": "http://www.3ds.com/xsd/3DXML"}
    with zipfile.ZipFile(BytesIO(raw)) as source:
        image_tree = ET.fromstring(source.read("CATRepImage.3dxml"))
        image_names = {
            node.attrib["id"]: node.attrib["associatedFile"].split(":")[-1]
            for node in image_tree.findall(".//d:CATRepresentationImage", namespace)
        }
        source_materials = {}
        for name in source.namelist():
            match = re.fullmatch(r"MaterialDef_(\d+)\.3DRep", name)
            if match is None:
                continue
            definition = ET.fromstring(source.read(name))
            attributes = {
                node.attrib["Name"]: node.attrib["Value"]
                for node in definition.iter() if node.tag.endswith("Attr")
            }
            diffuse = attributes["DiffuseColor"].strip("[]")
            color = [float(component) for component in diffuse.split(",")]
            if len(color) != 3 or not np.isfinite(color).all():
                raise ValueError(f"Invalid 3DXML diffuse color: {name}")
            texture_ref = attributes.get("TextureImage")
            texture_name = image_names[texture_ref.rsplit("#", 1)[-1]] if texture_ref else None
            source_materials[match.group(1)] = (color, texture_name)
        representation = ET.fromstring(source.read("TessPart_2.3DRep"))
        positions_parts = []
        normals_parts = []
        uv_parts = []
        triangles_by_material: dict[str, list[np.ndarray]] = {}
        vertex_offset = 0
        for rep in representation.findall(".//d:Rep", namespace):
            face = rep.find("./d:Faces/d:Face", namespace)
            vertices = rep.find("./d:VertexBuffer", namespace)
            if face is None or vertices is None:
                continue
            material_node = rep.find("./d:SurfaceAttributes/d:MaterialApplication/d:MaterialId", namespace)
            if material_node is None:
                raise ValueError("3DXML mesh has no material reference")
            material_id = material_node.attrib["id"].rsplit("#", 1)[-1]
            if material_id not in source_materials:
                raise ValueError(f"Missing 3DXML material {material_id}")
            position_text = vertices.find("d:Positions", namespace)
            normal_text = vertices.find("d:Normals", namespace)
            uv_text = vertices.find("d:TextureCoordinates", namespace)
            if position_text is None or normal_text is None:
                raise ValueError("3DXML mesh has no positions or normals")
            positions = np.fromstring(position_text.text.replace(",", " "), sep=" ", dtype="<f4").reshape(-1, 3)
            normals = np.fromstring(normal_text.text.replace(",", " "), sep=" ", dtype="<f4").reshape(-1, 3)
            uv = np.fromstring(uv_text.text.replace(",", " "), sep=" ", dtype="<f4").reshape(-1, 2) if uv_text is not None else np.zeros((len(positions), 2), dtype="<f4")
            if len(positions) != len(normals) or len(positions) != len(uv) or not np.isfinite(positions).all():
                raise ValueError("Invalid 3DXML vertex attributes")
            if source_materials[material_id][1] and uv_text is None:
                raise ValueError(f"Textured 3DXML material {material_id} has no UVs")
            positions_parts.append(positions)
            normals_parts.append(normals)
            uv_parts.append(uv)
            triangles = []
            for strip in face.attrib["strips"].split(","):
                values = np.fromstring(strip, sep=" ", dtype="<u4")
                if values.size < 3:
                    continue
                if int(values.max()) >= len(positions):
                    raise ValueError("3DXML strip index exceeds vertex count")
                for index in range(values.size - 2):
                    a, b, c = map(int, values[index:index + 3])
                    if len({a, b, c}) == 3:
                        triangles.extend((a, b, c) if index % 2 == 0 else (b, a, c))
            if triangles:
                triangles_by_material.setdefault(material_id, []).append(np.asarray(triangles, dtype="<u4") + vertex_offset)
            vertex_offset += len(positions)
        if not positions_parts or not triangles_by_material:
            raise ValueError("3DXML has no displayable mesh")
        positions = np.concatenate(positions_parts)
        normals = np.concatenate(normals_parts)
        uv = np.concatenate(uv_parts)
        extent = float(np.ptp(positions, axis=0).max())
        if extent <= 0:
            raise ValueError("3DXML has no spatial extent")
        positions = ((positions - (positions.min(axis=0) + positions.max(axis=0)) / 2) * (3.1 / extent)).astype("<f4")
        chunks: list[bytes] = []
        views: list[dict] = []

        def add_view(data: bytes, target: int | None = None) -> int:
            view = {"buffer": 0, "byteOffset": sum(map(len, chunks)), "byteLength": len(data)}
            if target is not None:
                view["target"] = target
            views.append(view)
            chunks.append(pad(data))
            return len(views) - 1

        accessors = [
            {"bufferView": add_view(positions.tobytes(), 34962), "componentType": 5126, "count": len(positions), "type": "VEC3", "min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist()},
            {"bufferView": add_view(normals.tobytes(), 34962), "componentType": 5126, "count": len(normals), "type": "VEC3"},
            {"bufferView": add_view(uv.tobytes(), 34962), "componentType": 5126, "count": len(uv), "type": "VEC2"},
        ]
        images = []
        textures = []
        image_indices: dict[str, int] = {}
        materials = []
        primitives = []
        wing_axis = 0 if np.ptp(positions, axis=0)[0] >= np.ptp(positions, axis=0)[2] else 2
        wing_center = (positions[:, wing_axis].min() + positions[:, wing_axis].max()) / 2
        wing_span = float(np.ptp(positions[:, wing_axis]))
        accent_parts = []
        for material_id, index_parts in sorted(triangles_by_material.items(), key=lambda item: int(item[0])):
            source_color, texture_name = source_materials[material_id]
            role, color, metalness, roughness, clearcoat = source_livery_finish(source_color, bool(texture_name))
            material = {
                "name": f"{role} · source {material_id}",
                "pbrMetallicRoughness": {"baseColorFactor": list(color), "metallicFactor": metalness, "roughnessFactor": roughness},
                "doubleSided": True,
                "extras": {"source_material_id": material_id, "source_color": source_color, "livery_role": role},
            }
            if clearcoat:
                material["extensions"] = {"KHR_materials_clearcoat": {
                    "clearcoatFactor": clearcoat, "clearcoatRoughnessFactor": 0.25,
                }}
            if texture_name:
                if texture_name not in image_indices:
                    image_index = len(images)
                    image_indices[texture_name] = len(textures)
                    images.append({"bufferView": add_view(source.read(texture_name)), "mimeType": "image/jpeg"})
                    textures.append({"source": image_index, "sampler": 0})
                material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": image_indices[texture_name]}
                material["extras"]["source_texture"] = texture_name
            material_index = len(materials)
            materials.append(material)
            triangles = np.concatenate(index_parts).reshape(-1, 3)
            centers = positions[triangles].mean(axis=1)
            accent = np.abs(centers[:, wing_axis] - wing_center) > wing_span * 0.40
            if accent.any():
                accent_parts.append(triangles[accent].reshape(-1))
            indices = triangles[~accent].reshape(-1)
            if indices.size:
                accessor_index = len(accessors)
                accessors.append({"bufferView": add_view(indices.tobytes(), 34963), "componentType": 5125, "count": len(indices), "type": "SCALAR"})
                primitives.append({"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": accessor_index, "material": material_index})
        if accent_parts:
            accent_material = len(materials)
            materials.append({
                "name": "Amber safety tips",
                "pbrMetallicRoughness": {"baseColorFactor": list(linear_color(LIVERY_COLORS["amber"])), "metallicFactor": 0, "roughnessFactor": 0.42},
                "doubleSided": True,
                "extensions": {"KHR_materials_clearcoat": {"clearcoatFactor": 0.28, "clearcoatRoughnessFactor": 0.25}},
                "extras": {"livery_role": "Amber safety tips", "derived_from": "source 3DXML geometry"},
            })
            indices = np.concatenate(accent_parts)
            accessor_index = len(accessors)
            accessors.append({"bufferView": add_view(indices.tobytes(), 34963), "componentType": 5125, "count": len(indices), "type": "SCALAR"})
            primitives.append({"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": accessor_index, "material": accent_material})
        binary = b"".join(chunks)
        document = {
            "asset": {"version": "2.0", "generator": "AERO-BENCH 3DXML source preview converter", "extras": {"source_archive": archive_name, "source_members": [member_name], "scale": "centered; maximum extent normalized to 3.1 preview units", "texture_origin": "source 3DXML part colors mapped to AERO-BENCH standard aviation PBR livery; source images preserved", "livery_version": "aviation-v1"}},
            "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": primitives}], "materials": materials, "images": images,
            "extensionsUsed": ["KHR_materials_clearcoat"] if any("extensions" in material for material in materials) else [],
            "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
            "textures": textures, "buffers": [{"byteLength": len(binary)}],
            "bufferViews": views, "accessors": accessors,
        }
        return encode_glb(document, binary)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for entry in json.loads(MANIFEST.read_text()):
        output = OUTPUT / entry["output"]
        if "source_3dxml" in entry:
            with zipfile.ZipFile(ARCHIVES / entry["archive"]) as archive:
                members = [name for name in archive.namelist() if Path(name).name == entry["source_3dxml"]]
                if len(members) != 1:
                    raise ValueError(f"Expected one {entry['source_3dxml']} in {entry['archive']}, found {len(members)}")
                output.write_bytes(make_3dxml_glb(archive.read(members[0]), entry["archive"], members[0]))
            print(f"{output}: standard livery from 3DXML parts and images, {output.stat().st_size} bytes")
            continue
        vertices, members = load_stl(entry)
        if entry.get("up_axis", "y") == "z":
            vertices = vertices[:, [0, 2, 1]].copy()
            vertices[:, 2] *= -1
        elif entry.get("up_axis", "y") != "y":
            raise ValueError(f"Unsupported up axis: {entry['up_axis']}")
        positions, normals, uv, indices = geometry(vertices)
        scheme = "multirotor" if entry["subgroup"] == "多旋翼" else "fixedwing"
        output.write_bytes(make_glb(positions, normals, uv, indices, entry["archive"], members, entry["color"], scheme))
        print(f"{output}: {len(positions)} vertices, {len(indices) // 3} triangles, {output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
