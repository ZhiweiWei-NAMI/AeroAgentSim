import * as T from 'three';
import { orderCaptureCity } from './capture-city-order';
import type { EntityKey, RunHeader } from '../contracts/viewer-feed';
import { RunsApi, validateCommit, validateHeader } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { parseLosslessJson } from '../feeds/lossless-json';
import { entityId, resolveBinding } from '../viewport/bindings';
import { worldOrientation, worldPosition } from '../viewport/coordinates';
import { Assets, disposeObject } from '../viewport/assets';
import { EntityLayer } from '../viewport/entities';
import { applyWeatherMaterials, setupScene } from '../viewport/scene';
import { pipeline } from '../viewport/pipeline';
import { AtmosphereLayer } from '../viewport/atmosphere';
import { conditionsAt } from '../viewport/conditions';
import { loadSceneLayer } from '../scene/city-layer';
import { isLiteCityScene } from '../viewport/procedural-traffic';
import { fitModel, parseModelCatalog } from '../viewport/model-catalog';
import '../scene/presentation';
import { translate } from '../i18n/translate';

/** Localize capture diagnostics for UI only; the automation protocol keeps its original errors. */
export function captureError(problem: unknown, locale = 'en-US'): string {
  const message = problem instanceof Error ? problem.message : String(problem);
  let match: RegExpMatchArray | null;
  if ((match = message.match(/^Capture asset manifest HTTP (\d+)$/))) return translate(locale,'Capture asset manifest HTTP {status}',{status:match[1]});
  if ((match = message.match(/^Capture asset HTTP (\d+): (.+)$/))) return translate(locale,'Capture asset HTTP {status}: {asset}',{status:match[1],asset:match[2]});
  if ((match = message.match(/^Capture asset is absent from the manifest: (.*)$/))) return translate(locale,'Capture asset is absent from the manifest: {asset}',{asset:match[1]});
  if ((match = message.match(/^Unloaded traffic building (.+)$/))) return translate(locale,'Unloaded traffic building {asset}',{asset:match[1]});
  if ((match = message.match(/^Capture required pose missing: (.+)$/))) return translate(locale,'Capture required pose missing: {field}',{field:match[1]});
  if ((match = message.match(/^Capture required orientation missing: (.+)$/))) return translate(locale,'Capture required orientation missing: {field}',{field:match[1]});
  return translate(locale,message);
}

type ObjectData = Record<string, unknown>;
function object(value: unknown): ObjectData { if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Capture requires a record'); return value as ObjectData; }
export function integerText(value: unknown): string {
  const text = typeof value === 'number' && Number.isSafeInteger(value) ? String(value) : typeof value === 'string' ? value : value && typeof value === 'object' ? object(value).$integer : undefined;
  if (typeof text !== 'string' || !/^(0|[1-9]\d*)$/.test(text)) throw Error('Capture requires an exact nonnegative integer'); return text;
}
function counter(value: unknown): number { const text = integerText(value); if (BigInt(text) > BigInt(Number.MAX_SAFE_INTEGER)) throw Error('Capture counter exceeds supported journal index range'); return Number(text); }
function vector(value: unknown): [number, number, number] { if (!Array.isArray(value) || value.length !== 3 || !value.every(item => typeof item === 'number' && Number.isFinite(item))) throw Error('Capture camera requires a finite 3-vector'); return value as [number, number, number]; }
/** Traffic capture bridge records flat id/generation pose rows at its read cut. */
export function recordedCameraPose(value: unknown) {
  const pose = object(value);
  if (typeof pose.id !== 'string' || typeof pose.field !== 'string') throw Error('Malformed snapshot pose');
  return { key: {id:pose.id, generation:integerText(pose.generation)}, field:pose.field, position:vector(pose.position) };
}

/** Every gimbal coordinate is checked against the issued committed scene. */
export function obliqueCamera(manifest: ObjectData, key: EntityKey, positionField: string,
  sample: (key: EntityKey, field: string) => unknown) {
  if (manifest.revision !== 'uav-gimbal-oblique/v1' || manifest.frame !== 'enu') throw Error('Unsupported oblique camera revision/frame');
  const declared=object(manifest.subject);
  if(typeof declared.id!=='string'||typeof declared.field!=='string'||declared.field!==manifest.target_field)throw Error('Invalid camera subject descriptor');
  const subjectKey={id:declared.id,generation:integerText(declared.generation)};
  const subject={key:subjectKey,field:declared.field,position:vector(sample(subjectKey,declared.field))};
  const eye=vector(sample(key,positionField)), pitch=manifest.pitch_deg, fov=manifest.fov;
  if (typeof pitch !== 'number' || !(pitch >= 35 && pitch <= 50) || typeof fov !== 'number' || !(fov > 0 && fov < 150)) throw Error('Invalid oblique camera pitch/FOV');
  const sx=subject.position[0]-eye[0],sy=subject.position[1]-eye[1],height=eye[2]-subject.position[2];
  if(height<=0)throw Error('Oblique camera must be above its declared subject');
  const distance=Math.hypot(sx,sy),bearing=distance?Math.atan2(sy,sx):typeof manifest.coincident_azimuth_deg==='number'?manifest.coincident_azimuth_deg*Math.PI/180:NaN;
  if(!Number.isFinite(bearing))throw Error('Overhead camera requires an explicit azimuth');
  const radius=height/Math.tan(pitch*Math.PI/180),target:[number,number,number]=[eye[0]+radius*Math.cos(bearing),eye[1]+radius*Math.sin(bearing),subject.position[2]];
  if (Math.abs(Math.atan2(height,distance)*180/Math.PI-pitch)>=fov/2) throw Error('Camera subject lies outside the sensor field');
  // Explicit resolved coordinates (e.g. a diagnostic replay) must agree with
  // the same facts and authored sensor; they can never relocate the actor.
  if(manifest.eye!==undefined&&JSON.stringify(vector(manifest.eye))!==JSON.stringify(eye))throw Error('Camera eye differs from committed actor pose');
  if(manifest.target!==undefined&&vector(manifest.target).some((v,i)=>Math.abs(v-target[i])>1e-8))throw Error('Camera optical axis differs from declared pitch/bearing');
  if(declared.position!==undefined&&JSON.stringify(vector(declared.position))!==JSON.stringify(subject.position))throw Error('Camera subject differs from committed facts');
  return {eye,target,subject};
}

/** Bilingual caption derived from the capture request's measured pose and cut. */
export function obliqueCaption(request: ObjectData): [string,string] {
  const camera=object(request.camera), subject=recordedCameraPose(camera.subject), actor=object(request.actor), source=object(request.source_cut);
  const point=(value:unknown)=>vector(value).map(v=>v.toFixed(1)).join(', ');
  return [`${String(actor.id)} · ENU (${point(camera.eye)}) m · gimbal ${String(camera.pitch_deg)}° down · cut #${integerText(source.index)}`,
          `目标 / Target ${subject.key.id} · ENU (${point(subject.position)}) m · 已提交位置 / committed poses`];
}

export interface CaptureScene {
  header: unknown;
  configuration: ObjectData;
  snapshot: { source_cut: ObjectData; entities: Array<{key: EntityKey; typeId: string; fields: Array<{fieldId: string; value: unknown}>}> };
  asset_manifest: ObjectData;
}
export interface CaptureInput { request: ObjectData; service_run_id: string; label?: string; scene?: CaptureScene }
export interface CaptureBinding { request_id: string; source_cut: ObjectData }
export interface CaptureDepth extends CaptureBinding {
  encoding: 'float32-le-metres'; width: number; height: number; depth_convention: 'optical_z';
  data_base64: string; near_m: number; far_m: number; vertical_origin: 'top-left'; invalid: 'NaN';
  source: 'same-camera-webgl-depth-buffer';
}
export interface CaptureCalibration extends CaptureBinding {
  K: number[]; width: number; height: number; registered_rgb_depth: true; depth_convention: 'optical_z';
  depth_min_m: number; depth_max_m: number; distortion: [];
  depth_semantics: 'rendered geometry (optical z); not physical ranging or weather sensing';
}
export interface CaptureCameraPose extends CaptureBinding {
  T_world_enu_from_camera_optical: number[][]; source: 'applied_committed_capture_camera';
}
export interface CaptureResult {
  request: ObjectData; png_data_url: string; depth?: CaptureDepth;
  calibration?: CaptureCalibration; camera_pose?: CaptureCameraPose;
}
export interface CapturePreparation {
  service_run_id: string; asset_digest: string; header: unknown; asset_manifest: ObjectData; oblique: boolean;
}
export interface CaptureReady { contract: 'aeroagentsim.viewer-assets-ready/v1'; service_run_id: string; asset_digest: string }

/** Match the receiver's allocation limit before resizing ANY capture GL target. */
export const MAX_CAPTURE_DEPTH_PIXELS = 4_194_304;
export function captureDepthPixelCount(width: number, height: number): number {
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0 || width > 8192 || height > 8192 || width * height > MAX_CAPTURE_DEPTH_PIXELS) {
    throw Error('Capture depth dimensions must fit 8192 per axis and 4,194,304 pixels / 深度拍摄每边不得超过 8192，且总像素不得超过 4,194,304');
  }
  return width * height;
}

/** Opt-in only. Retain the exact request/cut values, including lossless integers. */
export function captureDepthBinding(request: ObjectData, width: number, height: number, label?: string): CaptureBinding | undefined {
  const depth = object(request.camera).depth;
  if (depth !== undefined && typeof depth !== 'boolean') throw Error('Capture depth option must be boolean / 拍摄深度选项必须为布尔值');
  if (depth !== true) return undefined;
  captureDepthPixelCount(width,height);
  if (label) throw Error('Registered RGB-D capture cannot contain a diagnostic text overlay / 配准 RGB-D 拍摄不允许诊断文字覆盖');
  if (typeof request.request_id !== 'string' || !request.request_id) throw Error('Capture depth request ID is missing / 深度拍摄缺少请求标识');
  return {request_id:request.request_id,source_cut:object(request.source_cut)};
}

/** GPU rows start at bottom-left; wire rows start at top-left. R is optical z
 * in metres, G is the shader's validity flag. Never export infinity/out-of-clip
 * values. Any IEEE float32 NaN denotes missing (no particular payload required). */
export function encodeCaptureDepth(rgba: Float32Array, width: number, height: number, near: number, far: number): Uint8Array {
  const pixels = captureDepthPixelCount(width,height);
  if (!Number.isFinite(near) || !Number.isFinite(far) || near <= 0 || far <= near) throw Error('Invalid depth clip planes / 无效的深度裁剪平面');
  if (rgba.length !== pixels * 4) throw Error('Depth readback size mismatch / 深度回读尺寸不匹配');
  const bytes = new Uint8Array(pixels * 4), view = new DataView(bytes.buffer);
  for (let y=0;y<height;y++) for (let x=0;x<width;x++) {
    const index = ((height-1-y)*width+x)*4, z = rgba[index], valid = rgba[index+1];
    view.setFloat32((y*width+x)*4,Number.isFinite(valid)&&valid>.5&&Number.isFinite(z)&&z>=near&&z<=far ? z : NaN,true);
  }
  return bytes;
}

/** K is flat row-major. Pixel coordinates use an OUTER-EDGE top-left origin:
 * pixel (x,y)'s centre is (x+.5,y+.5), and the centred principal point is
 * (width/2,height/2), not ((width-1)/2,(height-1)/2). */
export function captureCalibration(camera: Pick<T.PerspectiveCamera,'projectionMatrix'|'near'|'far'>,
  width: number, height: number, binding: CaptureBinding): CaptureCalibration {
  const p = camera.projectionMatrix.elements;
  return {...binding,K:[p[0]*width/2,0,(1-p[8])*width/2,0,p[5]*height/2,(1+p[9])*height/2,0,0,1],
    width,height,registered_rgb_depth:true,depth_convention:'optical_z',depth_min_m:camera.near,depth_max_m:camera.far,
    distortion:[],depth_semantics:'rendered geometry (optical z); not physical ranging or weather sensing'};
}

/** Three world = east/up/south, camera = right/up/back. Export the APPLIED
 * optical right/down/forward pose into ENU, not a nominal actor/PX4 pose. */
export function captureCameraPose(matrixWorld: T.Matrix4, binding: CaptureBinding): CaptureCameraPose {
  const enuFromRender = new T.Matrix4().set(1,0,0,0, 0,0,-1,0, 0,1,0,0, 0,0,0,1);
  const opticalToCamera = new T.Matrix4().makeScale(1,-1,-1);
  const transform = enuFromRender.multiply(matrixWorld).multiply(opticalToCamera).elements;
  return {...binding,T_world_enu_from_camera_optical:Array.from({length:4},(_,row)=>Array.from({length:4},(_,column)=>transform[column*4+row])),
    source:'applied_committed_capture_camera'};
}

/** Pure packing shared by the renderer and helper-only contract tests. */
export function captureDepthProducts(rgba: Float32Array, width: number, height: number, camera: T.PerspectiveCamera, binding: CaptureBinding):
  {depth: CaptureDepth; calibration: CaptureCalibration; camera_pose: CaptureCameraPose} {
  const bytes = encodeCaptureDepth(rgba,width,height,camera.near,camera.far);
  let binary = '';
  for (let start=0;start<bytes.length;start+=32768) binary+=String.fromCharCode(...bytes.subarray(start,start+32768));
  return {
    depth:{...binding,encoding:'float32-le-metres',width,height,depth_convention:'optical_z',data_base64:btoa(binary),near_m:camera.near,far_m:camera.far,
      vertical_origin:'top-left',invalid:'NaN',source:'same-camera-webgl-depth-buffer'},
    calibration:captureCalibration(camera,width,height,binding),camera_pose:captureCameraPose(camera.matrixWorld,binding),
  };
}

/** Static skinned captures use the authored binding, including ancestor fallback. */
export function staticSkinnedBinding(header: RunHeader, typeId: string, catalog = parseModelCatalog(header.modelCatalog)) {
  const binding = resolveBinding(header,typeId);
  const model = binding?.visual.kind === 'model' && Object.values(catalog).find(model => model.skinned && model.url === binding.visual.asset);
  return binding && model ? {binding,model} : undefined;
}

/** Label-only placeholders keep skinned subjects OUT of instancing/fallback
 * meshes. Clear catalog selection too, so it cannot resurrect an extra mesh.
 * The label overlay is detached and never participates in the sensor image. */
export function captureMeshHeader(header: RunHeader): RunHeader {
  const catalog = parseModelCatalog(header.modelCatalog);
  return {...header,presentation:header.presentation.map(binding => staticSkinnedBinding(header,binding.typeId,catalog)
    ? {...binding,visual:{...binding.visual,kind:'label' as const,catalog:undefined}} : binding)};
}

/** Validate the inventory, then let the SAME viewport loaders load only needed
 * resources. Stable service URLs preserve relative textures and page caches.
 * Downloading every file as a fresh blob defeated both caches on every photo. */
export async function loadAssets(assetId: string, url: string, header: RunHeader, signal: AbortSignal, supplied?: ObjectData): Promise<RunHeader> {
  if (typeof assetId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/.test(assetId)) throw Error('Capture asset set id is invalid');
  let manifest = supplied;
  if (!manifest) {
    const response = await fetch(url, { signal });
    if (!response.ok) throw Error(`Capture asset manifest HTTP ${response.status}`);
    manifest = object(parseLosslessJson(new TextDecoder().decode(await response.arrayBuffer())));
  }
  if (manifest.format !== 'aeroagentsim.capture-assets/v1' || manifest.environment !== 'viewer-default/v1' || !Array.isArray(manifest.files)) throw Error('Unsupported capture asset manifest / environment');
  if (manifest.asset_id !== undefined && manifest.asset_id !== assetId) throw Error('Capture asset set ID does not match the request');
  const address = (value: string) => new URL(value, new URL(url, location.href)).href;
  const files = new Set<string>();
  for (const value of manifest.files) {
    const file = object(value);
    if (typeof file.url !== 'string' || !file.url || (file.asset_id !== undefined && (typeof file.asset_id !== 'string' || !file.asset_id))) throw Error('Invalid capture asset descriptor');
    const resolved = address(file.url);
    if (files.has(resolved)) throw Error('Duplicate capture asset URL');
    files.add(resolved);
  }
  const requireAsset = (value: string) => {
    // Authored inline glTFs carry their bytes in the pinned run header; they
    // have no external file to download or substitute from an inventory.
    if (value.startsWith('data:')) return value;
    const resolved = address(value);
    if (!files.has(resolved)) throw Error(`Capture asset is absent from the manifest: ${value}`);
    return resolved;
  };
  const presentation = header.presentation.map(binding => binding.visual.kind === 'model' && binding.visual.asset && !binding.visual.asset.startsWith('procedural:') ?
    { ...binding, visual: { ...binding.visual, asset: requireAsset(binding.visual.asset) } } : binding);
  const scene = header.scene && { ...header.scene,
    city: header.scene.city && { ...header.scene.city, url: requireAsset(header.scene.city.url) },
    roads: header.scene.roads && { ...header.scene.roads, url: requireAsset(header.scene.roads.url) },
    hdri: header.scene.hdri && requireAsset(header.scene.hdri),
  };
  if (scene?.city?.kind === 'traffic-city') {
    const response = await fetch(scene.city.url, { signal });
    if (!response.ok) throw Error(`Capture asset HTTP ${response.status}: ${scene.city.url}`);
    const city = object(parseLosslessJson(new TextDecoder().decode(await response.arrayBuffer())));
    if (!Array.isArray(city.buildings)) throw Error('Traffic city buildings unavailable');
    if (!isLiteCityScene(city)) for (const value of city.buildings) {
      const building = object(value);
      if (typeof building.url !== 'string') throw Error('Traffic building has no asset');
      requireAsset(new URL(building.url.replace(/^\/assets\//, ''), scene.city.url).href);
    }
  }
  const catalog = parseModelCatalog(header.modelCatalog);
  for (const model of Object.values(catalog)) {
    model.url = requireAsset(model.url);
    if (model.lodUrl) model.lodUrl = requireAsset(model.lodUrl);
  }
  return { ...header, presentation, scene, ...(header.modelCatalog ? {modelCatalog: catalog} : {}) };
}

class ExactCaptureStore extends TemporalFeedStore {
  override sample(key: EntityKey, field: string): number[] | undefined {
    const value = this.entities.get(entityId(key))?.fields.get(field)?.value;
    return Array.isArray(value) && value.every(item => typeof item === 'number' && Number.isFinite(item)) ? [...value] as number[] : undefined;
  }
}

/** A page owns one WebGL context, static city, lighting, model cache and pipeline.
 * Each photo replaces only its committed state and camera; it cannot operate a run. */
export class CaptureRenderer {
  private renderer?: T.WebGLRenderer;
  private scene?: T.Scene;
  private assets?: Assets;
  private layer?: EntityLayer;
  private atmosphere?: AtmosphereLayer;
  private lighting?: ReturnType<typeof setupScene>;
  private composer?: ReturnType<typeof pipeline>;
  private hdr?: T.WebGLRenderTarget;
  private camera = new T.PerspectiveCamera();
  private captureHeader?: RunHeader;
  private staticSkinned = new T.Group();
  private signature?: string;
  private failures: string[] = [];
  constructor(private mount: HTMLElement) {}

  /** Initialize the pinned camera resources without acquiring any image. */
  async prepare(assetManifestUrl: string, input: CapturePreparation): Promise<CaptureReady> {
    const header = validateHeader(input.header);
    if (header.runId !== input.service_run_id || typeof input.oblique !== 'boolean') throw Error('Capture run / epoch identity mismatch');
    const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 30_000);
    try {
      await this.resources(assetManifestUrl, input.service_run_id, input.asset_digest, header, abort.signal, input.asset_manifest, input.oblique);
      if (abort.signal.aborted || this.failures.length) throw Error(this.failures.join('; ') || 'Capture timed out');
      return {contract:'aeroagentsim.viewer-assets-ready/v1',service_run_id:input.service_run_id,asset_digest:input.asset_digest};
    } catch (error) { this.dispose(); throw error; }
    finally { clearTimeout(timer); abort.abort(); }
  }

  private async resources(assetManifestUrl: string, serviceRunId: string, assetId: string, header: RunHeader,
    signal: AbortSignal, manifest: ObjectData | undefined, oblique: boolean) {
    const signature = JSON.stringify([serviceRunId, assetId, header.presentation, header.scene, header.modelCatalog]);
    if (signature === this.signature) return;
    this.dispose();
    header = await loadAssets(assetId,assetManifestUrl,header,signal,manifest);
    this.captureHeader = header;
    this.renderer = new T.WebGLRenderer({antialias:false,preserveDrawingBuffer:true});
    this.renderer.setPixelRatio(1); this.renderer.outputColorSpace = T.SRGBColorSpace;
    this.mount.replaceChildren(this.renderer.domElement);
    this.scene = new T.Scene(); this.lighting = setupScene(this.renderer,this.scene); this.assets = new Assets(this.renderer);
    const status = (message: string) => { if (message.includes('unavailable')) this.failures.push(message); };
    if (header.scene) await loadSceneLayer(header.scene,header,this.assets,signal,item => {this.scene!.add(item);this.lighting!.grid.visible=false;},status);
    if (oblique) orderCaptureCity(this.scene);
    if (header.scene?.hdri) {this.hdr = await this.assets.hdri(header.scene.hdri,this.renderer);this.scene.environment = this.hdr.texture;}
    this.layer = new EntityLayer(captureMeshHeader(header),this.assets,document.createElement('div'),() => {},error => this.failures.push(String(error)),status);
    this.layer.hideIncidentMarkers = true;
    this.layer.setCatalog(parseModelCatalog(header.modelCatalog)); this.scene.add(this.layer.root,this.staticSkinned);
    // Captures show the same smoke/rain/fog as the run viewport: the cut's journaled conditions.
    // A capture is a one-shot sensor render, so the atmosphere gets the high tier
    // (full plume) while the render pipeline itself stays 'low'.
    this.atmosphere = new AtmosphereLayer(this.scene,this.renderer,'high',{ annotations: false });
    // The atlas must be live before the first photograph or the plume is missing from captures.
    await this.atmosphere.ready;
    this.composer = pipeline(this.renderer,this.scene,this.camera,'low');
    // Shader compilation is sensor initialization, not a warm-up photograph.
    await this.renderer.compileAsync(this.scene,this.camera);
    if (signal.aborted || this.failures.length) throw Error(this.failures.join('; ') || 'Capture timed out');
    this.signature = signature;
    const city = this.scene.children.find(item => item.name === 'city');
    this.mount.dataset.city = city ? 'loaded' : 'absent';
    if (typeof city?.userData.buildingCount === 'number') this.mount.dataset.buildings = String(city.userData.buildingCount);
    else delete this.mount.dataset.buildings;
  }

  private lastCity?: T.Object3D;
  private lastWet = NaN;
  private lastDusk?: boolean;
  private applyCityDisplay(object: T.Object3D) {
    const wet = this.scene?.userData.weatherWet as number | undefined ?? 0;
    if (this.lastCity === object && this.lastWet === wet && this.lastDusk === this.dusk) return;
    this.lastCity = object; this.lastWet = wet; this.lastDusk = this.dusk;
    applyWeatherMaterials(object, wet, this.dusk);
  }
  private dusk = false;
  private atmosphereState = { dusk: false, rain: NaN, visibilityM: null as number | null };
  private clearStaticSkinned() {
    for (const root of [...this.staticSkinned.children]) {
      const skeletons = new Set<T.Skeleton>();
      root.traverse(node => { if (node instanceof T.SkinnedMesh) skeletons.add(node.skeleton); });
      for (const skeleton of skeletons) skeleton.dispose();
      disposeObject(root);
    }
  }
  private async updateStaticSkinned(store: ExactCaptureStore, check: () => void) {
    this.clearStaticSkinned();
    const header = this.captureHeader!, catalog = parseModelCatalog(header.modelCatalog);
    for (const entity of store.entities.values()) {
      const selected = staticSkinnedBinding(header,entity.typeId,catalog);
      if (!selected) continue;
      const {binding,model:spec} = selected;
      if (binding.frame !== 'enu') throw Error('Static skinned capture requires an ENU binding / 静态蒙皮拍摄需要 ENU 绑定');
      const model = await this.assets!.model(spec.url,true);
      // Track ownership BEFORE validation/fitting: the outer failure path must
      // also release skeletons when an authored pose or model is malformed.
      const root = new T.Group(); root.name=entityId(entity.key); root.add(model); this.staticSkinned.add(root);
      check();
      model.updateMatrixWorld(true);
      model.traverse(node => { if (node instanceof T.SkinnedMesh) node.skeleton.update(); });
      const fit = fitModel(new T.Box3().setFromObject(model),spec);
      const fitted = new T.Group(); fitted.matrixAutoUpdate=false; fitted.matrix.copy(fit.matrix); fitted.add(model); root.add(fitted);
      root.position.copy(worldPosition(vector(store.sample(entity.key,binding.positionField)),'enu'));
      root.quaternion.copy(worldOrientation(binding.orientationField ? store.sample(entity.key,binding.orientationField)! : [0,0,0,1],'enu'));
      root.scale.setScalar(binding.visual.scale ?? 1);
      root.userData.poseMode='authored static GLB pose; no walking animation';
      root.updateMatrixWorld(true);
    }
  }
  async render(api: RunsApi, assetManifestUrl: string, input: CaptureInput): Promise<CaptureResult> {
    const request = object(input.request);
    if (typeof request.asset_digest !== 'string' || !request.asset_digest) throw Error('Capture request asset set ID is missing');
    const actor = object(request.actor), source = object(request.source_cut);
    if (!Array.isArray(source.instant) || source.instant.length !== 2) throw Error('Capture source cut lacks ns/microstep');
    const index = counter(source.index), ns = integerText(source.instant[0]), microstep = counter(source.instant[1]);
    const width = counter(request.width), height = counter(request.height);
    if (!width || !height || width > 8192 || height > 8192 || typeof request.timeout_s !== 'number' || !Number.isFinite(request.timeout_s) || request.timeout_s <= 0 || request.timeout_s > 300) throw Error('Invalid capture dimensions / timeout');
    const depthBinding = captureDepthBinding(request,width,height,input.label);
    const deadline = performance.now() + request.timeout_s * 1000;
    const abort = new AbortController(), timer = setTimeout(() => abort.abort(), request.timeout_s * 1000);
    // A synchronous GPU readback can block the timer callback; check elapsed
    // time as well for depth requests, including AFTER readback/serialization.
    const check = () => { if (this.failures.length || abort.signal.aborted || (depthBinding && performance.now() >= deadline)) throw Error(this.failures.join('; ') || 'Capture timed out'); };
    try {
      const path = `/v1/runs/${encodeURIComponent(input.service_run_id)}`;
      const [rawHeader, configuration, rawScene] = input.scene ? [input.scene.header, input.scene.configuration, input.scene.snapshot] : await Promise.all([
        api.request(`${path}/header`, {signal: abort.signal}),
        api.request(`/v1/studio/runs/${encodeURIComponent(input.service_run_id)}/configuration`, {signal: abort.signal}),
        api.request(`${path}/capture-requests/${encodeURIComponent(String(request.request_id))}/scene`, {signal: abort.signal}),
      ]);
      const identity = object(configuration), prefix = object(rawScene);
      let header = validateHeader(rawHeader);
      if (header.runId !== input.service_run_id || identity.service_run_id !== input.service_run_id || identity.kernel_run_id !== request.run_id || actor.run_id !== request.run_id || actor.epoch !== identity.epoch) throw Error('Capture run / epoch identity mismatch');
      header.epoch = String(identity.epoch);
      if (typeof actor.id !== 'string' || typeof actor.type_id !== 'string') throw Error('Capture actor / committed prefix unavailable');
      const store = new ExactCaptureStore(header);
      if (input.scene) {
        const cut = object(prefix.source_cut);
        if (counter(cut.index) !== index || !Array.isArray(cut.instant) || integerText(cut.instant[0]) !== ns || counter(cut.instant[1]) !== microstep || !Array.isArray(prefix.entities)) throw Error('Capture cut disagrees with recorded journal instant');
        for (const raw of input.scene.snapshot.entities) {
          const key = {id: raw.key.id, generation: integerText(raw.key.generation)};
          if (store.entities.has(entityId(key)) || !header.types.some(type => type.typeId === raw.typeId)) throw Error('Invalid capture snapshot entity');
          // Facts retain their actual producer, validity and version. No fake
          // lifecycle/commit events are created to hydrate a state snapshot.
          store.entities.set(entityId(key), {key, typeId: raw.typeId, fields: new Map(raw.fields.map(field => [field.fieldId, {...field, entity:key} as import('../contracts/viewer-feed').FeedCommit['facts'][number]]))});
        }
      } else {
        if (prefix.service_run_id !== input.service_run_id || prefix.kernel_run_id !== request.run_id || !Array.isArray(prefix.commits)) throw Error('Capture run / epoch identity mismatch');
        for (const raw of prefix.commits) { const commit = validateCommit(raw); if (commit.commitIndex > index) throw Error('Capture prefix extends beyond its requested cut'); store.ingest(commit); }
        const cut = store.commits.find(row => row.commitIndex === index);
        if (!cut || cut.at.ns !== ns || cut.at.microstep !== microstep) throw Error('Capture cut disagrees with recorded journal instant');
        store.seek(ns, index);
      }
      const key = {id: actor.id, generation: integerText(actor.generation)};
      const entity = store.entities.get(entityId(key));
      if (!entity || entity.typeId !== actor.type_id) throw Error('Capture actor generation/type absent at source cut');
      const manifest = object(request.camera);
      const supported = new Set(['revision','preset','eye','target','fov','near','far','frame','anchor','snapshot','provenance','pitch_deg','coincident_azimuth_deg','target_field','subject','depth']);
      if (Object.keys(manifest).some(name => !supported.has(name)) || typeof manifest.revision !== 'string' || !manifest.revision) throw Error('Unsupported / incomplete capture camera manifest');
      if (typeof manifest.fov !== 'number' || !(manifest.fov > 0 && manifest.fov < 180)) throw Error('Invalid capture camera fov');
      // The original traffic-nadir/v1 protocol declares its camera at the
      // snapshot actor pose. Interpret it exactly; do not rewrite the request.
      const oblique = manifest.revision === 'uav-gimbal-oblique/v1';
      const nadir = manifest.preset === 'actor-nadir' || manifest.revision === 'traffic-nadir/v1' || manifest.revision === 'traffic-city-nadir/v1';
      let eye: [number,number,number], target: [number,number,number];
      if (nadir || oblique) {
        if (nadir && manifest.revision !== 'traffic-nadir/v1' && (manifest.revision !== 'traffic-city-nadir/v1' || manifest.frame !== 'enu')) throw Error('Unsupported actor-nadir camera revision/frame');
        const snapshot = object(manifest.snapshot), cut = object(snapshot.cut);
        if (counter(cut.index) !== index || !Array.isArray(cut.instant) || integerText(cut.instant[0]) !== ns || counter(cut.instant[1]) !== microstep || !Array.isArray(snapshot.entities)) throw Error('Camera pose snapshot cut mismatch');
        for (const value of snapshot.entities) { const pose = recordedCameraPose(value), actual = store.sample(pose.key, pose.field); if (!actual || JSON.stringify(actual) !== JSON.stringify(pose.position)) throw Error('Camera snapshot pose differs from committed facts'); }
        const binding = resolveBinding(header, entity.typeId);
        if (!binding) throw Error('Actor-nadir needs a spatial actor');
        if (oblique) {
          const resolved=obliqueCamera(manifest,key,binding.positionField,(key,field)=>store.sample(key,field));
          ({eye,target}=resolved);
        }
        else {
          eye = vector(store.sample(key, binding.positionField)); target = [eye[0],eye[1],0];
          if (eye[2] <= 0) throw Error('Actor-nadir camera must be above ground');
        }
      } else { eye = vector(manifest.eye); target = vector(manifest.target); }
      if (eye.every((value,i) => value === target[i])) throw Error('Capture eye and target coincide');
      if (!resolveBinding(header,entity.typeId)) vector(manifest.anchor);
      for (const item of store.entities.values()) {
        const binding = resolveBinding(header,item.typeId); if (!binding) continue;
        if (!store.sample(item.key,binding.positionField)) throw Error(`Capture required pose missing: ${item.key.id}.${binding.positionField}`);
        if (binding.orientationField && !store.sample(item.key,binding.orientationField)) throw Error(`Capture required orientation missing: ${item.key.id}.${binding.orientationField}`);
      }
      await this.resources(assetManifestUrl,input.service_run_id,request.asset_digest,header,abort.signal,input.scene?.asset_manifest,oblique);
      check();
      const near = manifest.near ?? 0.15, far = manifest.far ?? 16000;
      if (typeof near !== 'number' || typeof far !== 'number' || !Number.isFinite(near) || !Number.isFinite(far) || near <= 0 || far <= near) throw Error('Invalid capture clip planes');
      Object.assign(this.camera,{fov:manifest.fov,aspect:width/height,near,far});
      this.camera.updateProjectionMatrix(); this.camera.up.set(0,1,0);
      const position = (value: [number,number,number]) => nadir || manifest.frame === 'enu' ? worldPosition(value,'enu') : manifest.frame === undefined || manifest.frame === 'render-world' ? new T.Vector3(...value) : (() => {throw Error('Unsupported capture camera frame');})();
      if (nadir) this.camera.up.set(0,0,-1);
      this.camera.position.copy(position(eye)); this.camera.lookAt(position(target)); this.camera.updateMatrixWorld();
      this.renderer!.setSize(width,height);this.composer!.setSize(width,height);
      this.lighting!.update(position(target));
      // A photographer is excluded from its own photograph, not from real state.
      if (nadir || oblique) store.entities.delete(entityId(key));
      await this.updateStaticSkinned(store,check); check();
      do {
        this.layer!.update(store,ns,this.camera,undefined,false);
        check();
        if (this.layer!.modelsReady) break;
        await new Promise(resolve => setTimeout(resolve,0));
      } while (true);
      // Catalog selection can change after its needed tier becomes available.
      const atmosphereConditions = conditionsAt(store.entities,ns,header);
      this.layer!.update(store,ns,this.camera,undefined,false,undefined,undefined,undefined,atmosphereConditions.incidents); check();
      // Conditions at the capture cut drive the same atmosphere layer as the run viewport.
      this.dusk = atmosphereConditions.light==='dusk';
      const state = this.atmosphereState, rain = Math.min(atmosphereConditions.precipitationMmH / 8, 1);
      if (state.dusk !== this.dusk || state.rain !== rain || state.visibilityM !== atmosphereConditions.visibilityM) {
        state.dusk = this.dusk; state.rain = rain; state.visibilityM = atmosphereConditions.visibilityM;
        this.lighting!.setAtmosphere(state);
      }
      this.composer!.setDusk(this.dusk);
      this.atmosphere!.update(atmosphereConditions,Number(BigInt(ns))/1e9,this.camera);
      this.composer!.excludeBloom(this.atmosphere!.root);
      const city = this.scene!.getObjectByName('city');
      if (city) this.applyCityDisplay(city);
      this.composer!.render(0); this.renderer!.getContext().finish(); check();
      let canvas = this.renderer!.domElement;
      // Sensor pixels contain weather and fire, while identity and cut stay in request metadata.
      if (input.label) {
        const labeled = document.createElement('canvas');labeled.width=width;labeled.height=height;
        const context=labeled.getContext('2d');if(!context)throw Error('PNG readback canvas unavailable');
        context.drawImage(canvas,0,0);
        if(input.label){context.fillStyle='#fff';context.font='12px monospace';context.fillText(input.label,4,height-6);}
        canvas=labeled;
      }
      this.mount.dataset.rendered='true';this.mount.dataset.sourceCut=String(index);
      this.mount.dataset.camera=JSON.stringify({eye:this.camera.position.toArray(),target:position(target).toArray(),fov:manifest.fov,width,height});
      const png_data_url = canvas.toDataURL('image/png');
      if (!depthBinding) return {request,png_data_url};
      const depth = this.readDepth(width,height,depthBinding);
      check();
      return {request,png_data_url,...depth};
    } catch (error) { this.dispose(); throw error; }
    finally {clearTimeout(timer);abort.abort();}
  }
  /** Re-render the SAME committed geometry/camera without advancing simulation
   * or animation. Keep authored alpha tests, skinning and depth-write materials;
   * non-depth-writing transparent weather is not a physical ranging response. */
  private readDepth(width: number, height: number, binding: CaptureBinding) {
    captureDepthPixelCount(width,height);
    const renderer = this.renderer!, camera = this.camera, gl = renderer.getContext();
    if (!renderer.extensions.has('EXT_color_buffer_float')) throw Error('Float depth readback unavailable / 浮点深度回读不可用');
    const maxSize = Math.min(renderer.capabilities.maxTextureSize,gl.getParameter(gl.MAX_RENDERBUFFER_SIZE) as number);
    if (width > maxSize || height > maxSize) throw Error('Capture depth exceeds GPU dimensions / 深度拍摄尺寸超过 GPU 上限');
    // The low RGB base pass also uses a non-multisampled half-float target.
    const sceneTarget = new T.WebGLRenderTarget(width,height,{type:T.HalfFloatType,depthBuffer:true,stencilBuffer:false});
    sceneTarget.depthTexture = new T.DepthTexture(width,height,T.UnsignedIntType);
    const output = new T.WebGLRenderTarget(width,height,{type:T.FloatType,format:T.RGBAFormat,depthBuffer:false,minFilter:T.NearestFilter,magFilter:T.NearestFilter});
    output.texture.colorSpace = T.NoColorSpace;
    const material = new T.ShaderMaterial({
      uniforms:{tDepth:{value:sceneTarget.depthTexture},nearM:{value:camera.near},farM:{value:camera.far}},
      vertexShader:'varying vec2 vUv; void main(){vUv=uv; gl_Position=vec4(position.xy,0.0,1.0);}',
      // Algebraically near*far/(far-(far-near)*d), without the large subtraction.
      fragmentShader:'precision highp float; varying vec2 vUv; uniform sampler2D tDepth; uniform float nearM; uniform float farM; void main(){float d=texture2D(tDepth,vUv).x; float z=nearM/((1.0-d)+(nearM/farM)*d); gl_FragColor=vec4(z,d>=0.0&&d<1.0?1.0:0.0,0.0,1.0);}',
      depthTest:false,depthWrite:false,blending:T.NoBlending,toneMapped:false,
    });
    const geometry = new T.PlaneGeometry(2,2), quad = new T.Scene();
    quad.add(new T.Mesh(geometry,material));
    const oldTarget = renderer.getRenderTarget(), oldFace = renderer.getActiveCubeFace(), oldLevel = renderer.getActiveMipmapLevel();
    const oldViewport = renderer.getViewport(new T.Vector4()), oldScissor = renderer.getScissor(new T.Vector4());
    const oldScissorTest = renderer.getScissorTest(), oldAutoClear = renderer.autoClear, oldMask = camera.layers.mask;
    const oldClearAlpha = renderer.getClearAlpha();
    const oldDepthMask = gl.getParameter(gl.DEPTH_WRITEMASK) as boolean, oldClearDepth = gl.getParameter(gl.DEPTH_CLEAR_VALUE) as number;
    const target = (value: T.WebGLRenderTarget) => {
      renderer.setRenderTarget(value); renderer.setViewport(0,0,width,height); renderer.setScissorTest(false);
      if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw Error('Depth framebuffer incomplete / 深度帧缓冲不完整');
      renderer.state.buffers.depth.setMask(true); renderer.state.buffers.depth.setClear(1);
      renderer.clear(true,true,false);
    };
    try {
      // pipeline(...,'low') enables layer 30 in its RGB base pass. It applies
      // tone/outline only (no AO/bloom/SMAA); capture has no outline selection.
      // Do NOT override materials or mutate visibility between RGB and depth.
      camera.layers.enable(30); renderer.autoClear=false;
      target(sceneTarget); renderer.render(this.scene!,camera);
      // Alpha=0 means unwritten; the conversion shader writes alpha=1 even for
      // missing sky. NaN initialization also catches Three's early-return reads.
      renderer.setClearAlpha(0); target(output); renderer.render(quad,new T.Camera());
      const rgba = new Float32Array(width*height*4).fill(NaN);
      renderer.readRenderTargetPixels(output,0,0,width,height,rgba);
      // Three may report a failed read without throwing. A zero-filled buffer
      // must not masquerade as a successful all-missing (e.g. sky) observation.
      const error = gl.getError();
      if (error !== gl.NO_ERROR || gl.isContextLost()) throw Error(`Depth GPU readback failed (WebGL ${error}) / 深度 GPU 回读失败（WebGL ${error}）`);
      for (let index=3;index<rgba.length;index+=4) if (rgba[index] !== 1) throw Error('Depth conversion/readback incomplete / 深度转换或回读不完整');
      return captureDepthProducts(rgba,width,height,camera,binding);
    } finally {
      try {
        camera.layers.mask=oldMask; renderer.autoClear=oldAutoClear; renderer.setClearAlpha(oldClearAlpha);
        renderer.state.buffers.depth.setMask(oldDepthMask); renderer.state.buffers.depth.setClear(oldClearDepth);
        renderer.setRenderTarget(oldTarget,oldFace,oldLevel);
        renderer.setViewport(oldViewport); renderer.setScissor(oldScissor); renderer.setScissorTest(oldScissorTest);
      } finally {
        geometry.dispose(); material.dispose(); output.dispose(); sceneTarget.depthTexture.dispose(); sceneTarget.dispose();
      }
    }
  }
  dispose() {
    this.atmosphereState.rain = NaN; this.lastCity = undefined;
    this.clearStaticSkinned(); this.staticSkinned.removeFromParent(); this.captureHeader=undefined;
    this.layer?.dispose();if(this.scene)disposeObject(this.scene);this.lighting?.dispose();this.hdr?.dispose();this.assets?.dispose();this.composer?.dispose();this.renderer?.dispose();
    this.atmosphere?.dispose();
    this.layer=undefined;this.scene=undefined;this.lighting=undefined;this.hdr=undefined;this.assets=undefined;this.composer=undefined;this.renderer=undefined;this.atmosphere=undefined;this.signature=undefined;this.failures=[];
    for(const key of ['rendered','city','buildings','sourceCut','camera']) delete this.mount.dataset[key];
    this.mount.replaceChildren();
  }
}

/** One-off callers release resources; CaptureConsole retains its page renderer. */
export async function renderCapture(api: RunsApi, url: string, input: CaptureInput, mount: HTMLElement): Promise<CaptureResult> {
  const renderer = new CaptureRenderer(mount);
  try {return await renderer.render(api,url,input);} finally {renderer.dispose();}
}
