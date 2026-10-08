#!/usr/bin/env python3
"""Sync redistributable viewer assets; runtime kernel dependencies are unaffected.

Python stdlib only. Model conversion uses Three.js/linkedom and optional
@gltf-transform/cli in frontend/src/scene/.work/converter (npm tooling).
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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCH = Path(
    "/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim/aero-bench/frontend/public"
)
PX4 = "https://raw.githubusercontent.com/PX4/PX4-gazebo-models/main/"
STYLE = "https://raw.githubusercontent.com/tordanik/OSM2World-default-style/master/"
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
    parser.add_argument("--source", type=Path, default=DEFAULT_BENCH)
    parser.add_argument(
        "--optimize", choices=("meshopt", "draco", "none"), default="meshopt"
    )
    parser.add_argument(
        "--ktx2",
        action="store_true",
        help="Run UASTC texture conversion when toktx is installed",
    )
    args = parser.parse_args()
    dest = ROOT / "frontend/public/assets"
    work = ROOT / "frontend/src/scene/.work/converter"
    work.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    sources: list[dict] = []

    def record(
        path: Path, license: str, attribution: str, source: str, transform: str = "copy"
    ) -> None:
        if not path.is_relative_to(dest):
            data = path.read_bytes()
            sources.append(
                {
                    "source": source,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "license": license,
                    "attribution": attribution,
                }
            )
            return
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

    def copy(src: Path, relative: str, license: str, attribution: str) -> Path:
        path = dest / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, path)
        record(path, license, attribution, str(src))
        return path

    def download(url: str, relative: str, license: str, attribution: str) -> Path:
        path = (
            work / relative.removeprefix(".work/converter/")
            if relative.startswith(".work/converter/")
            else dest / relative
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=90) as response:
            path.write_bytes(response.read())
        record(path, license, attribution, url)
        return path

    cc0 = "OSM2World-default-style contributors; https://github.com/tordanik/OSM2World-default-style (CC0)"
    osm = "© OpenStreetMap contributors; https://www.openstreetmap.org/copyright; OSM2World produced work"
    px4 = "Copyright (c) 2022 Rudis Laboratories (x500_base); PX4 Autopilot for Drones; https://github.com/PX4/PX4-gazebo-models"
    download(STYLE + "COPYING.txt", "licenses/OSM2World-CC0.txt", "CC0-1.0", cc0)
    download(PX4 + "LICENSE", "licenses/PX4-BSD.txt", "BSD-3-Clause", px4)
    download(
        PX4 + "models/x500_base/LICENSE", "licenses/X500-BSD.txt", "BSD-3-Clause", px4
    )
    notice = dest / "licenses/OSM-attribution.txt"
    notice.write_text(
        osm
        + "\nDatabase license: https://opendatacommons.org/licenses/odbl/1.0/\nThe source OSM JSON is included at city/assets/<source.sha256> from city/manifest.json.\n"
    )
    record(notice, "ODbL-1.0", osm, "https://www.openstreetmap.org/copyright")
    packdir = args.source / "osm2world/packs/shanghai-huangpu-east-v1"
    pack = json.loads((packdir / "manifest.json").read_text())
    copy(
        packdir / "manifest.json",
        "city/manifest.json",
        "ODbL-1.0; CC0-1.0 textures",
        osm + "; " + cc0,
    )
    references = [
        pack["source"],
        *[batch["file"] for batch in pack["batches"]],
        *pack["textures"].values(),
    ]
    texture_hashes = {ref["sha256"] for ref in pack["textures"].values()}
    for ref in {r["sha256"]: r for r in references}.values():
        src = packdir / "assets" / ref["sha256"]
        raw = src.read_bytes()
        if (
            len(raw) != ref["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != ref["sha256"]
        ):
            raise ValueError(f"Corrupt source pack chunk: {src}")
        is_texture = ref["sha256"] in texture_hashes
        copy(
            src,
            "city/assets/" + ref["sha256"],
            "CC0-1.0" if is_texture else "ODbL-1.0",
            cc0 if is_texture else osm,
        )
    copy(
        args.source / "osm2world/style/textures/sky/DaySkyHDRI041B.hdr",
        "environment/day.hdr",
        "CC0-1.0",
        cc0,
    )
    download(STYLE + "models/car/car.gltf", "models/car.gltf", "CC0-1.0", cc0)
    download(STYLE + "models/car/car.bin", "models/car.bin", "CC0-1.0", cc0)
    source = work / "px4"
    source.mkdir(exist_ok=True)
    download(
        PX4 + "models/x500_base/model.sdf",
        ".work/converter/px4/model.sdf",
        "BSD-3-Clause",
        px4,
    )
    for name in (
        "NXP-HGD-CF.dae",
        "5010Base.dae",
        "5010Bell.dae",
        "1345_prop_ccw.stl",
        "1345_prop_cw.stl",
    ):
        download(
            PX4 + "models/x500_base/meshes/" + name,
            ".work/converter/px4/" + name,
            "BSD-3-Clause",
            px4,
        )
    # Tools live only in the ignored asset work directory; no package.json/lockfile changes.
    if not (work / "node_modules/linkedom").exists():
        subprocess.run(
            [
                "npm",
                "install",
                "--prefix",
                str(work),
                "--cache",
                str(work.parent / "npm-cache"),
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
    subprocess.run(["node", str(helper), str(source), str(model)], check=True, cwd=ROOT)
    cli = work / "node_modules/.bin/gltf-transform"
    transform = "PX4 SDF visual poses + Collada units, Y-up metres, authored PBR and welded smooth display normals; source labels/planes omitted"
    if args.optimize != "none":
        raw = work / "x500-uncompressed.glb"
        shutil.copyfile(model, raw)
        subprocess.run(
            [
                str(cli),
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
        transform += (
            "; gltf-transform "
            + args.optimize
            + "; simplify ratio 0.05/error 0.01/border unlocked"
        )
    if args.ktx2:
        if not shutil.which("toktx"):
            raise RuntimeError("--ktx2 requires the real toktx executable")
        # This X500 uses texture-free PBR, so KTX2 applies to caller-provided textured GLBs only.
        print("X500 has no image textures; no KTX2 texture conversion is applicable.")
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
    record(model, "BSD-3-Clause", px4, PX4 + "models/x500_base/model.sdf", transform)
    # Source bytes remain available for reproducibility but are excluded from deployable inventory.
    records = [r for r in records if not r["path"].startswith(".work/")]
    manifest = {
        "schema": "aeroagentsim.assets/v1",
        "assets": sorted(records, key=lambda r: r["path"]),
        "sources": sources,
        "excluded": [
            {
                "path": "models/city-runtime/holybro-x500-textured-preview.glb",
                "reason": "No local redistribution license; preview-normalized, replaced by BSD PX4 source",
            },
            {
                "path": "models/bigcity/**",
                "reason": "Unity package redistribution rights not established",
            },
        ],
    }
    (ROOT / "frontend/assets.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Synced {len(records)} assets, {sum(r['size'] for r in records):,} bytes")


if __name__ == "__main__":
    main()
