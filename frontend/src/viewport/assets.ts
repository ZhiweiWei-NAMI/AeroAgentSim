import * as T from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { RGBELoader } from 'three/addons/loaders/RGBELoader.js';
import { parseMeshPack, unpackMeshBatch, type PackFile } from './mesh-pack';
import { createRoofDetails } from './roof-details';
import { decorateBuildings } from './city-materials';
import { surfaceMaterial, createCityVegetation } from './city-surfaces';
import type { RunHeader } from '../contracts/viewer-feed';

export interface AssetOptions { dracoPath?: string; ktx2Path?: string }
export function disposeObject(root: T.Object3D) {
  root.traverse(node => {
    if (!(node instanceof T.Mesh || node instanceof T.Line)) return;
    if (node instanceof T.InstancedMesh) node.dispose();
    // Session-cached model resources are shared between views; never dispose them per view.
    if (!node.geometry.userData.sharedAsset) node.geometry.dispose();
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      if (material.userData.sharedAsset) continue;
      for (const value of Object.values(material)) if (value instanceof T.Texture) value.dispose();
      material.dispose();
    }
  });
  root.removeFromParent();
}

/** Page-session cache of parsed GLB scenes, keyed by URL. Views receive clones sharing geometry/materials. */
const sharedModels = new Map<string, Promise<T.Object3D>>();
export function clearSharedModels() { sharedModels.clear(); }
function markShared(root: T.Object3D) {
  root.traverse(node => {
    if (!(node instanceof T.Mesh)) return;
    node.geometry.userData.sharedAsset = true;
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) material.userData.sharedAsset = true;
  });
}

export class Assets {
  private draco = new DRACOLoader();
  private ktx: KTX2Loader;
  private loader: GLTFLoader;
  private pending = new Set<Promise<unknown>>();
  constructor(renderer: T.WebGLRenderer, options: AssetOptions = {}) {
    this.draco.setDecoderPath(options.dracoPath ?? '/decoders/draco/');
    this.ktx = new KTX2Loader().setTranscoderPath(options.ktx2Path ?? '/decoders/basis/').detectSupport(renderer);
    this.loader = new GLTFLoader().setDRACOLoader(this.draco).setKTX2Loader(this.ktx).setMeshoptDecoder(MeshoptDecoder);
  }
  model(url: string): Promise<T.Object3D> {
    let task = sharedModels.get(url);
    if (!task) {
      task = this.loader.loadAsync(url).then(gltf => {
        gltf.scene.userData.bodyToAssetQuaternion = gltf.parser.json.asset?.extras?.bodyToAssetQuaternion;
        const lights: T.Object3D[] = [];
        gltf.scene.traverse(node => {
          if (node instanceof T.Light) lights.push(node);
          if (node instanceof T.Mesh) { node.castShadow = true; node.receiveShadow = true; }
        });
        for (const light of lights) light.removeFromParent();
        markShared(gltf.scene);
        return gltf.scene;
      });
      sharedModels.set(url, task);
      const own = task.then(() => {}, () => { if (sharedModels.get(url) === task) sharedModels.delete(url); });
      this.pending.add(own); void own.finally(() => this.pending.delete(own));
    }
    return task.then(scene => scene.clone(true));
  }
  async hdri(url: string, renderer: T.WebGLRenderer) {
    const hdr = await new RGBELoader().loadAsync(url);
    const generator = new T.PMREMGenerator(renderer);
    const environment = generator.fromEquirectangular(hdr);
    hdr.dispose(); generator.dispose();
    return environment;
  }
  dispose() {
    // Loaders may still be serving a cache entry other views wait on; release them afterwards.
    void Promise.allSettled([...this.pending]).then(() => { this.draco.dispose(); this.ktx.dispose(); });
  }
}

/** Chunks/textures are addressed by opaque asset IDs; their declared size is still checked. */
async function loadPackFile(url: URL, file: PackFile, signal: AbortSignal) {
  const response = await fetch(url, { signal });
  if (!response.ok) throw Error(`Asset HTTP ${response.status}: ${url}`);
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength !== file.size_bytes) throw Error(`Mesh pack byte count mismatch: ${url}`);
  return bytes;
}

/** Pack chunks and textures resolve as assets/<asset_id> below assetsBase. */
export async function loadCityPack(manifestUrl: string, assetsBase: string, origin: RunHeader['origin'], signal: AbortSignal) {
  const response = await fetch(manifestUrl, { signal });
  if (!response.ok) throw Error(`City manifest HTTP ${response.status}`);
  const pack = parseMeshPack(await response.json());
  if (!origin) throw Error('OSM2World packs require the run geographic origin');
  const group = new T.Group();
  try {
    // Only geometry and original OSM data are read. Historical facade/terrain pixels
    // are deliberately replaced by procedural materials; D-assets remains open.
    const chunks = await Promise.all(pack.batches.map(batch => loadPackFile(new URL(`assets/${batch.file.asset_id}`, assetsBase), batch.file, signal)));
    const buildings: Array<{geometry:T.BufferGeometry;batch:typeof pack.batches[number]}> = [];
    for (const [index, batch] of pack.batches.entries()) {
      const arrays = unpackMeshBatch(batch, chunks[index]);
      const geometry = new T.BufferGeometry();
      geometry.setAttribute('position', new T.BufferAttribute(arrays.positions, 3));
      geometry.setAttribute('normal', new T.BufferAttribute(arrays.normals, 3));
      geometry.setAttribute('uv', new T.BufferAttribute(arrays.uvs, 2));
      geometry.setIndex(new T.BufferAttribute(arrays.indices, 1));
      const material = batch.layer === 'buildings'
        ? decorateBuildings(geometry, batch, pack.objects, false) : surfaceMaterial(batch);
      const mesh = new T.Mesh(geometry, material);
      mesh.castShadow = batch.layer === 'buildings'; mesh.receiveShadow = true; group.add(mesh);
      if(batch.layer==='buildings')buildings.push({geometry,batch});
    }
    const roofDetails=createRoofDetails(buildings);
    if(roofDetails.children.length)group.add(roofDetails);
    group.userData.roofDetails=roofDetails.userData;
    if (pack.objects.length) {
      const sourceBytes = await loadPackFile(new URL(`assets/${pack.source.asset_id}`, assetsBase), pack.source, signal);
      const source: unknown = JSON.parse(new TextDecoder().decode(sourceBytes));
      const landscape = createCityVegetation(source, pack);
      group.add(landscape); group.userData.landscape = landscape.userData;
    }
    group.userData.displayStyle = 'procedural-city';
    // Stored vertices already contain converter->pack-origin translation. Apply only pack->run once.
    const rad = Math.PI / 180;
    const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + lat * rad / 2));
    const scale = 6378137 * Math.cos(pack.projection.origin.latitude_deg * rad);
    const east = scale * (origin.lon - pack.projection.origin.longitude_deg) * rad;
    const north = scale * (mercY(origin.lat) - mercY(pack.projection.origin.latitude_deg));
    group.position.set(-east, 0, north);
    return group;
  } catch (error) { disposeObject(group); throw error; }

}
