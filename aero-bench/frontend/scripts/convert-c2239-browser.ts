/** Browser side of build-c2239-library.py; invoked by convert-c2239-browser.mjs. */
import * as THREE from "three";
import { MTLLoader } from "three/addons/loaders/MTLLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

interface ConversionStats {
  triangles: number;
  materials: string[];
  bounds: number[];
}

function stats(root: THREE.Object3D): ConversionStats {
  const materials = new Set<string>();
  let triangles = 0;
  root.traverse(child => {
    if (!(child instanceof THREE.Mesh)) return;
    triangles += (child.geometry.index?.count ?? child.geometry.attributes.position.count) / 3;
    for (const material of Array.isArray(child.material) ? child.material : [child.material]) {
      materials.add(material.name);
    }
  });
  const bounds = new THREE.Box3().setFromObject(root);
  return { triangles, materials: [...materials].sort(), bounds: bounds.getSize(new THREE.Vector3()).toArray() };
}

export async function convertFurniture(id: string, mtlFile: string): Promise<{ base64: string; source: ConversionStats; reloaded: ConversionStats }> {
  const base = "/models/incoming/furniture/source/";
  const creator = await new MTLLoader().loadAsync(`${base}${encodeURIComponent(mtlFile)}`);
  creator.preload();
  const object = await new OBJLoader().setMaterials(creator).loadAsync(`${base}${encodeURIComponent(id)}.obj`);
  object.name = id;
  object.scale.setScalar(0.01);
  object.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(object);
  const center = bounds.getCenter(new THREE.Vector3());
  object.position.set(-center.x, -bounds.min.y, -center.z);
  object.updateMatrixWorld(true);
  const source = stats(object);
  const raw = await new GLTFExporter().parseAsync(object, { binary: true, onlyVisible: true });
  if (!(raw instanceof ArrayBuffer)) throw new Error(`GLB exporter returned non-binary output for ${id}`);
  const parsed = await new Promise<THREE.Object3D>((resolve, reject) =>
    new GLTFLoader().parse(raw, "", value => resolve(value.scene), reject));
  const reloaded = stats(parsed);
  if (source.triangles !== reloaded.triangles || source.materials.length !== reloaded.materials.length) {
    throw new Error(`GLB round-trip changed triangles or material count for ${id}`);
  }
  const bytes = new Uint8Array(raw);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return { base64: btoa(binary), source, reloaded };
}

export async function inspectFurnitureGlb(id: string): Promise<ConversionStats> {
  const glb = await new GLTFLoader().loadAsync(`/models/incoming/furniture/glb/${encodeURIComponent(id)}.glb`);
  const result = stats(glb.scene);
  if (result.triangles <= 0 || result.materials.length === 0) {
    throw new Error(`Converted GLB has no drawable geometry or materials: ${id}`);
  }
  return result;
}
