import * as T from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { RGBELoader } from 'three/addons/loaders/RGBELoader.js';
import { parseMeshPack, unpackMeshBatch, type PackFile } from './mesh-pack';
import type { RunHeader } from '../contracts/viewer-feed';

export interface AssetOptions { dracoPath?: string; ktx2Path?: string }
export function disposeObject(root: T.Object3D) {
  root.traverse(node => {
    if (!(node instanceof T.Mesh || node instanceof T.Line)) return;
    node.geometry.dispose();
    if (node instanceof T.InstancedMesh) node.dispose();
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      for (const value of Object.values(material)) if (value instanceof T.Texture) value.dispose();
      material.dispose();
    }
  });
  root.removeFromParent();
}

export class Assets {
  private draco = new DRACOLoader();
  private ktx: KTX2Loader;
  private loader: GLTFLoader;
  private models = new Map<string, Promise<T.Object3D>>();
  private disposed = false;
  constructor(renderer: T.WebGLRenderer, options: AssetOptions = {}) {
    this.draco.setDecoderPath(options.dracoPath ?? '/decoders/draco/');
    this.ktx = new KTX2Loader().setTranscoderPath(options.ktx2Path ?? '/decoders/basis/').detectSupport(renderer);
    this.loader = new GLTFLoader().setDRACOLoader(this.draco).setKTX2Loader(this.ktx).setMeshoptDecoder(MeshoptDecoder);
  }
  model(url: string): Promise<T.Object3D> {
    let task = this.models.get(url);
    if (!task) {
      task = this.loader.loadAsync(url).then(gltf => {
        const lights: T.Object3D[] = [];
        gltf.scene.traverse(node => {
          if (node instanceof T.Light) lights.push(node);
          if (node instanceof T.Mesh) { node.castShadow = true; node.receiveShadow = true; }
        });
        for (const light of lights) light.removeFromParent();
        if (this.disposed) disposeObject(gltf.scene);
        return gltf.scene;
      });
      this.models.set(url, task);
    }
    return task;
  }
  async hdri(url: string, renderer: T.WebGLRenderer) {
    const hdr = await new RGBELoader().loadAsync(url);
    const generator = new T.PMREMGenerator(renderer);
    const environment = generator.fromEquirectangular(hdr);
    hdr.dispose(); generator.dispose();
    return environment;
  }
  dispose() {
    this.disposed = true;
    for (const task of this.models.values()) void task.then(disposeObject, () => {});
    this.models.clear(); this.draco.dispose(); this.ktx.dispose();
  }
}

async function verified(url: URL, file: PackFile, signal: AbortSignal) {
  const response = await fetch(url, { signal });
  if (!response.ok) throw Error(`Asset HTTP ${response.status}: ${url}`);
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength !== file.size_bytes) throw Error('Mesh pack byte count mismatch');
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
  if (digest !== file.sha256) throw Error('Mesh pack SHA-256 mismatch');
  return bytes;
}

/** Content-addressed assets/<sha256>, matching aero-bench's pack store. */
export async function loadCityPack(manifestUrl: string, assetsBase: string, origin: RunHeader['origin'], signal: AbortSignal) {
  const response = await fetch(manifestUrl, { signal });
  if (!response.ok) throw Error(`City manifest HTTP ${response.status}`);
  const pack = parseMeshPack(await response.json());
  if (!origin) throw Error('OSM2World packs require the run geographic origin');
  const group = new T.Group();
  const urls: string[] = [];
  const textures = new Map<string, T.Texture>();
  try {
    for (const [path, file] of Object.entries(pack.textures)) {
      const bytes = await verified(new URL(`assets/${file.sha256}`, assetsBase), file, signal);
      const type = path.endsWith('.png') ? 'image/png' : path.endsWith('.svg') ? 'image/svg+xml' : 'image/jpeg';
      const url = URL.createObjectURL(new Blob([bytes], { type })); urls.push(url);
      textures.set(path, await new T.TextureLoader().loadAsync(url));
    }
    for (const batch of pack.batches) {
      const arrays = unpackMeshBatch(batch, await verified(new URL(`assets/${batch.file.sha256}`, assetsBase), batch.file, signal));
      const geometry = new T.BufferGeometry();
      geometry.setAttribute('position', new T.BufferAttribute(arrays.positions, 3));
      geometry.setAttribute('normal', new T.BufferAttribute(arrays.normals, 3));
      geometry.setAttribute('uv', new T.BufferAttribute(arrays.uvs, 2));
      geometry.setIndex(new T.BufferAttribute(arrays.indices, 1));
      const m = batch.material;
      const tex = (path: string | null, color = false) => {
        if (path === null) return null;
        const texture = textures.get(path)!.clone();
        texture.colorSpace = color ? T.SRGBColorSpace : T.NoColorSpace;
        texture.wrapS = texture.wrapT = m.clamp ? T.ClampToEdgeWrapping : T.RepeatWrapping;
        texture.needsUpdate = true; return texture;
      };
      const orm = tex(m.orm_texture);
      const material = new T.MeshStandardMaterial({ color: new T.Color(...m.color), map: tex(m.base_color_texture, true), normalMap: tex(m.normal_texture), roughnessMap: orm, metalnessMap: orm, aoMap: orm, alphaMap: tex(m.opacity_texture), transparent: m.transparent, roughness: 0.85 });
      const mesh = new T.Mesh(geometry, material); mesh.castShadow = batch.layer === 'buildings'; mesh.receiveShadow = true; group.add(mesh);
    }
    // Stored vertices already contain converter->pack-origin translation. Apply only pack->run once.
    const rad = Math.PI / 180;
    const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + lat * rad / 2));
    const scale = 6378137 * Math.cos(pack.projection.origin.latitude_deg * rad);
    const east = scale * (origin.lon - pack.projection.origin.longitude_deg) * rad;
    const north = scale * (mercY(origin.lat) - mercY(pack.projection.origin.latitude_deg));
    group.position.set(-east, 0, north);
    return group;
  } catch (error) { disposeObject(group); throw error; }
  finally { for (const texture of textures.values()) texture.dispose(); for (const url of urls) URL.revokeObjectURL(url); }
}
