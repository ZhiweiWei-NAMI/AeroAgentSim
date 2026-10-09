#!/usr/bin/env python3
"""Build licensed viewer assets directly from pinned public upstreams.

City data is built separately with tools/build_map.py. No neighboring checkout
is read. Conversion tooling installs only into --work-dir, never frontend's
node_modules. Downloads retain upstream notices and hashes in the inventory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PX4_REVISION = "e5997f455e33ec071c7c677fc59a65781de496a4"
STYLE_REVISION = "81ade9bf9793181774891548ac29448f366efd3c"
PX4 = f"https://raw.githubusercontent.com/PX4/PX4-gazebo-models/{PX4_REVISION}/"
STYLE = f"https://raw.githubusercontent.com/tordanik/OSM2World-default-style/{STYLE_REVISION}/"

CONVERTER = r"""
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { DOMParser } from 'linkedom';
const require = createRequire(process.cwd() + '/frontend/package.json');
const T = await import(require.resolve('three'));
const { ColladaLoader } = await import(require.resolve('three/addons/loaders/ColladaLoader.js'));
const { STLLoader } = await import(require.resolve('three/addons/loaders/STLLoader.js'));
const { mergeVertices } = await import(require.resolve('three/addons/utils/BufferGeometryUtils.js'));
const { GLTFExporter } = await import(require.resolve('three/addons/exporters/GLTFExporter.js'));
globalThis.DOMParser = DOMParser;
globalThis.FileReader = class {
  readAsArrayBuffer(blob) { blob.arrayBuffer().then(x => { this.result=x; this.onloadend?.(); }); }
  readAsDataURL(blob) { blob.arrayBuffer().then(x => { this.result='data:application/octet-stream;base64,'+Buffer.from(x).toString('base64'); this.onloadend?.(); }); }
};
const base = process.argv[2], out = process.argv[3];
const sdf = new DOMParser().parseFromString(fs.readFileSync(base+'/model.sdf','utf8'),'text/xml');
const root = new T.Group(); root.name='PX4 X500 · metres · Y up';
// Exported root uses renderer Y-up; native SDF body Z-up is preserved in source files.
root.rotation.x = -Math.PI/2;root.userData.bodyToAssetQuaternion=[-Math.SQRT1_2,0,0,Math.SQRT1_2];
const loaded = new Map();
for (const visual of sdf.querySelectorAll('visual')) {
 const mesh = visual.querySelector('geometry > mesh'); if (!mesh) continue;
 const name = mesh.querySelector('uri').textContent.split('/').pop();
 let model = loaded.get(name);
 if (!model) {
  if (name.endsWith('.dae')) {
   // Keep source geometry/units/transforms. Source diffuse textures become an explicit display PBR material.
   const xml = fs.readFileSync(base+'/'+name,'utf8').replace(/<texture\b[^>]*\/>/g,'<color>0.15 0.18 0.20 1</color>');
   model = new ColladaLoader().parse(xml,'').scene;
   const raw = new T.Group(); raw.rotation.x=Math.PI/2; raw.add(model); model=raw;
  } else if (name.endsWith('.stl')) {
   const b=fs.readFileSync(base+'/'+name); const g=new STLLoader().parse(b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength));
   model=new T.Mesh(g);
  } else throw Error('Unsupported PX4 mesh: '+name);
  const unwanted=[]; model.traverse(n=>{if(n.isLight || n.isLine || n.isCamera)unwanted.push(n);});for(const n of unwanted)n.removeFromParent();
  model.traverse(n=>{if(n.isMesh){const geometry=n.geometry.clone();geometry.deleteAttribute('normal');geometry.deleteAttribute('uv');n.geometry=mergeVertices(geometry,1e-4);n.geometry.computeVertexNormals();n.material=new T.MeshStandardMaterial({color:name.includes('prop')?'#d8dfe1':'#252e35',roughness:0.55,metalness:0.35});}});
  loaded.set(name,model);
 }
 const pose=(visual.querySelector('pose')?.textContent ?? '0 0 0 0 0 0').trim().split(/\s+/).map(Number);
 const holder=new T.Group(); holder.name=visual.getAttribute('name'); holder.position.set(...pose.slice(0,3));
 holder.rotation.set(pose[3],pose[4],pose[5],'ZYX');
 const scale=(mesh.querySelector('scale')?.textContent ?? '1 1 1').trim().split(/\s+/).map(Number);
 holder.scale.set(...scale); holder.add(model.clone(true));
 const link = visual.parentNode;
 const linkPose = Array.from(link.children).find(n=>n.tagName==='pose')?.textContent ?? '0 0 0 0 0 0';
 const lp=linkPose.trim().split(/\s+/).map(Number); const parent=new T.Group();parent.position.set(...lp.slice(0,3));parent.rotation.set(lp[3],lp[4],lp[5],'ZYX');parent.add(holder);root.add(parent);
}
root.updateMatrixWorld(true);
const box=new T.Box3().setFromObject(root);console.log('X500 bounds metres',box.getSize(new T.Vector3()).toArray());
const bytes=await new GLTFExporter().parseAsync(root,{binary:true,onlyVisible:true}); fs.writeFileSync(out,Buffer.from(bytes));
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "frontend/public/assets")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "frontend/.asset-work")
    parser.add_argument("--models", choices=("car", "x500", "all"), default="all")
    parser.add_argument(
        "--optimize", choices=("meshopt", "draco", "none"), default="meshopt"
    )
    args = parser.parse_args()
    dest: Path = args.out.resolve()
    work: Path = args.work_dir.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    def record(
        path: Path,
        license: str,
        attribution: str,
        source: str,
        transform: str = "download",
    ) -> None:
        data = path.read_bytes()
        records.append(
            {
                "path": path.relative_to(dest).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "license": license,
                "attribution": attribution,
                "source": source,
                "transform": transform,
            }
        )

    def download(
        base: str, upstream: str, relative: str, license: str, attribution: str
    ) -> Path:
        path = dest / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        url = base + upstream
        with urllib.request.urlopen(url, timeout=90) as response:
            data = response.read()
        pending = path.with_name(path.name + ".part")
        pending.write_bytes(data)
        pending.replace(path)
        record(path, license, attribution, url)
        return path

    cc0 = "OSM2World-default-style contributors; https://github.com/tordanik/OSM2World-default-style"
    px4 = "Copyright (c) 2022 Rudis Laboratories; PX4 Autopilot for Drones; https://github.com/PX4/PX4-gazebo-models"
    download(STYLE, "COPYING.txt", "licenses/OSM2World-CC0.txt", "CC0-1.0", cc0)
    download(
        STYLE, "textures/sky/DaySkyHDRI041B.hdr", "environment/day.hdr", "CC0-1.0", cc0
    )
    if args.models in {"car", "all"}:
        download(STYLE, "models/car/car.gltf", "models/car.gltf", "CC0-1.0", cc0)
        download(STYLE, "models/car/car.bin", "models/car.bin", "CC0-1.0", cc0)
    if args.models in {"x500", "all"}:
        download(PX4, "LICENSE", "licenses/PX4-BSD.txt", "BSD-3-Clause", px4)
        download(
            PX4,
            "models/x500_base/LICENSE",
            "licenses/X500-BSD.txt",
            "BSD-3-Clause",
            px4,
        )
        source = work / "px4"
        source.mkdir(exist_ok=True)
        for name, upstream in [
            ("model.sdf", "model.sdf"),
            *[
                (name, "meshes/" + name)
                for name in (
                    "NXP-HGD-CF.dae",
                    "5010Base.dae",
                    "5010Bell.dae",
                    "1345_prop_ccw.stl",
                    "1345_prop_cw.stl",
                )
            ],
        ]:
            url = PX4 + "models/x500_base/" + upstream
            with urllib.request.urlopen(url, timeout=90) as response:
                (source / name).write_bytes(response.read())
        subprocess.run(
            [
                "npm",
                "install",
                "--prefix",
                str(work),
                "--no-package-lock",
                "linkedom@0.18.13",
                "@gltf-transform/cli@4.5.1",
            ],
            check=True,
            cwd=ROOT,
        )
        helper = work / "convert.mjs"
        helper.write_text(CONVERTER)
        model = dest / "models/x500.glb"
        model.parent.mkdir(exist_ok=True)
        subprocess.run(
            ["node", str(helper), str(source), str(model)], check=True, cwd=ROOT
        )
        transform = "PX4 SDF visual poses, Collada units, Y-up metres, display PBR and welded normals"
        if args.optimize != "none":
            raw = work / "x500-uncompressed.glb"
            shutil.copyfile(model, raw)
            subprocess.run(
                [
                    str(work / "node_modules/.bin/gltf-transform"),
                    "optimize",
                    str(raw),
                    str(model),
                    "--compress",
                    args.optimize,
                    "--texture-compress",
                    "false",
                    "--simplify-ratio",
                    "0.05",
                    "--simplify-error",
                    "0.01",
                    "--simplify-lock-border",
                    "false",
                ],
                check=True,
            )
            transform += "; gltf-transform " + args.optimize
        raw_glb = model.read_bytes()
        json_size = struct.unpack_from("<I", raw_glb, 12)[0]
        gltf = json.loads(raw_glb[20 : 20 + json_size])
        gltf["asset"]["extras"] = {
            "bodyToAssetQuaternion": [-(2**-0.5), 0, 0, 2**-0.5],
            "units": "metres",
            "source": PX4 + "models/x500_base/model.sdf",
            "displayGeometry": transform,
        }
        encoded = json.dumps(gltf, separators=(",", ":")).encode()
        encoded += b" " * (-len(encoded) % 4)
        tail = raw_glb[20 + json_size :]
        model.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(encoded) + len(tail))
            + struct.pack("<I4s", len(encoded), b"JSON")
            + encoded
            + tail
        )
        record(
            model, "BSD-3-Clause", px4, PX4 + "models/x500_base/model.sdf", transform
        )
    inventory = dest / "inventory.json"
    inventory.write_text(
        json.dumps(
            {"schema": "aeroagentsim.assets/v1", "assets": records},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(f"Built {len(records)} assets; inventory: {inventory}")


if __name__ == "__main__":
    main()
