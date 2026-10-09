import * as T from 'three';
import type { EntityKey, RunHeader } from '../contracts/viewer-feed';
import { RunsApi, validateCommit, validateHeader } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { parseLosslessJson } from '../feeds/lossless-json';
import { entityId, resolveBinding } from '../viewport/bindings';
import { worldPosition } from '../viewport/coordinates';
import { Assets, disposeObject } from '../viewport/assets';
import { EntityLayer } from '../viewport/entities';
import { setupScene } from '../viewport/scene';
import { pipeline } from '../viewport/pipeline';
import { loadSceneLayer } from '../scene/city-layer';
import { isLiteCityScene } from '../viewport/procedural-traffic';
import '../scene/presentation';

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
export interface CaptureInput { request: ObjectData; service_run_id: string; label?: string }
export interface CaptureResult { request: ObjectData; png_data_url: string }

/** Asset sets are addressed by opaque plain IDs; bytes are fetched and loaded as-is. */
export async function loadAssets(assetId: string, url: string, header: RunHeader, signal: AbortSignal, blobs: string[]): Promise<RunHeader> {
  if (typeof assetId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/.test(assetId)) throw Error('Capture asset set id is invalid');
  const response = await fetch(url, { signal }); if (!response.ok) throw Error(`Capture asset manifest HTTP ${response.status}`);
  const manifest = object(parseLosslessJson(new TextDecoder().decode(await response.arrayBuffer())));
  if (manifest.format !== 'aeroagentsim.capture-assets/v1' || manifest.environment !== 'viewer-default/v1' || !Array.isArray(manifest.files)) throw Error('Unsupported capture asset manifest / environment');
  if(manifest.asset_id!==undefined && manifest.asset_id!==assetId) throw Error('Capture asset set ID does not match the request');
  const contents = new Map<string, ArrayBuffer>();
  const files = new Map<string, ObjectData>(), pinned = new Map<string, string>();
  await Promise.all(manifest.files.map(async value => {
    const file = object(value);
    if (typeof file.url !== 'string' || !file.url || (file.asset_id!==undefined && (typeof file.asset_id!=='string' || !file.asset_id))) throw Error('Invalid capture asset descriptor');
    const address = new URL(file.url, new URL(url, location.href)).href;
    if (files.has(address)) throw Error('Duplicate capture asset URL'); files.set(address, file);
    const result = await fetch(address, { signal }); if (!result.ok) throw Error(`Capture asset HTTP ${result.status}: ${address}`);
    const data = await result.arrayBuffer();
    contents.set(address, data); const blob = URL.createObjectURL(new Blob([data])); blobs.push(blob); pinned.set(address, blob);
  }));
  const required = header.presentation.flatMap(binding => binding.visual.kind === 'model' && !binding.visual.asset?.startsWith('procedural:') ? [binding.visual.asset] : []);
  if (header.scene?.city) required.push(header.scene.city.url);
  if (header.scene?.roads) required.push(header.scene.roads.url);
  if (header.scene?.hdri) required.push(header.scene.hdri);
  for (const address of required) if (!address || !files.has(new URL(address, location.href).href)) throw Error(`Capture asset is absent from the manifest: ${address}`);
  if(header.scene?.city?.kind==='traffic-city') {
    const address=new URL(header.scene.city.url,location.href).href;
    const city=object(parseLosslessJson(new TextDecoder().decode(contents.get(address)!)));
    if(!Array.isArray(city.buildings))throw Error('Traffic city buildings unavailable');
    if (!isLiteCityScene(city)) city.buildings=city.buildings.map(value=>{const building=object(value);if(typeof building.url!=='string')throw Error('Traffic building has no asset');const original=new URL(building.url.replace('/assets/',''),address).href;const blob=pinned.get(original);if(!blob)throw Error(`Unloaded traffic building ${original}`);return {...building,url:blob};});
    const blob=URL.createObjectURL(new Blob([JSON.stringify(city)],{type:'application/json'}));blobs.push(blob);pinned.set(address,blob);
  }
  const pin = (address: string) => pinned.get(new URL(address, location.href).href)!;
  return { ...header, presentation: header.presentation.map(binding => binding.visual.asset && !binding.visual.asset.startsWith('procedural:') ? { ...binding, visual: { ...binding.visual, asset: pin(binding.visual.asset) } } : binding),
    scene: header.scene && { ...header.scene, city: header.scene.city && { ...header.scene.city, url: pin(header.scene.city.url), assetsBase: header.scene.city.assetsBase ?? new URL('.', new URL(header.scene.city.url, location.href)).href }, roads: header.scene.roads && { ...header.scene.roads, url: pin(header.scene.roads.url) }, hdri: header.scene.hdri && pin(header.scene.hdri) } }; 
}

class ExactCaptureStore extends TemporalFeedStore {
  override sample(key: EntityKey, field: string): number[] | undefined {
    const value = this.entities.get(entityId(key))?.fields.get(field)?.value;
    return Array.isArray(value) && value.every(item => typeof item === 'number' && Number.isFinite(item)) ? [...value] as number[] : undefined;
  }
}

/** Captures are derived from recorded facts and loaded assets; they never operate the run. */
export async function renderCapture(api: RunsApi, assetManifestUrl: string, input: CaptureInput, mount: HTMLElement): Promise<CaptureResult> {
  const request = object(input.request);
  if(typeof request.asset_digest!=='string' || !request.asset_digest) throw Error('Capture request asset set ID is missing');
  const actor = object(request.actor), source = object(request.source_cut);
  if (!Array.isArray(source.instant) || source.instant.length !== 2) throw Error('Capture source cut lacks ns/microstep');
  const index = counter(source.index), ns = integerText(source.instant[0]), microstep = counter(source.instant[1]);
  const width = counter(request.width), height = counter(request.height);
  if (!width || !height || width > 8192 || height > 8192 || typeof request.timeout_s !== 'number' || !Number.isFinite(request.timeout_s) || request.timeout_s <= 0 || request.timeout_s > 300) throw Error('Invalid capture dimensions / timeout');
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), request.timeout_s * 1000);
  const blobs: string[] = [];
  let hdr: T.WebGLRenderTarget | undefined, composer: ReturnType<typeof pipeline> | undefined;
  let renderer: T.WebGLRenderer | undefined, assets: Assets | undefined, layer: EntityLayer | undefined, scene: T.Scene | undefined, lighting: ReturnType<typeof setupScene> | undefined;
  try {
    const path = `/v1/runs/${encodeURIComponent(input.service_run_id)}`;
    const [rawHeader, configuration, rawScene] = await Promise.all([
      api.request(`${path}/header`, { signal: abort.signal }),
      api.request(`/v1/studio/runs/${encodeURIComponent(input.service_run_id)}/configuration`, { signal: abort.signal }),
      api.request(`${path}/capture-requests/${encodeURIComponent(String(request.request_id))}/scene`, { signal: abort.signal }),
    ]);
    const identity = object(configuration), prefix = object(rawScene); let header = validateHeader(rawHeader);
    if (header.runId !== input.service_run_id || prefix.service_run_id !== input.service_run_id || identity.service_run_id !== input.service_run_id || identity.kernel_run_id !== request.run_id || prefix.kernel_run_id !== request.run_id || actor.run_id !== request.run_id || actor.epoch !== identity.epoch) throw Error('Capture run / epoch identity mismatch');
    header.epoch = String(identity.epoch);
    if (typeof actor.id !== 'string' || typeof actor.type_id !== 'string' || !Array.isArray(prefix.commits)) throw Error('Capture actor / committed prefix unavailable');
    const store = new ExactCaptureStore(header);
    for (const raw of prefix.commits) { const commit = validateCommit(raw); if (commit.commitIndex > index) throw Error('Capture prefix extends beyond its requested cut'); store.ingest(commit); }
    const cut = store.commits.find(row => row.commitIndex === index);
    if (!cut || cut.at.ns !== ns || cut.at.microstep !== microstep) throw Error('Capture cut disagrees with recorded journal instant');
    store.seek(ns, index); const key = { id: actor.id, generation: integerText(actor.generation) };
    const entity = store.entities.get(entityId(key)); if (!entity || entity.typeId !== actor.type_id) throw Error('Capture actor generation/type absent at source cut');
    store.select(key); header = await loadAssets(request.asset_digest, assetManifestUrl, header, abort.signal, blobs);
    const cameraManifest = object(request.camera);
    // Old camera descriptions may carry this annotation; it has no rendering or verification role.
    const supported = new Set(['revision', 'preset', 'eye', 'target', 'fov', 'near', 'far', 'frame', 'anchor', 'snapshot', 'provenance']);
    if (Object.keys(cameraManifest).some(name => !supported.has(name)) || typeof cameraManifest.revision !== 'string' || !cameraManifest.revision) throw Error('Unsupported / incomplete capture camera manifest');
    if (typeof cameraManifest.fov !== 'number' || !(cameraManifest.fov > 0 && cameraManifest.fov < 180)) throw Error('Invalid capture camera fov');
    let eye: [number,number,number], target: [number,number,number];
    if(cameraManifest.preset==='actor-nadir') {
      if(cameraManifest.revision!=='traffic-city-nadir/v1'||cameraManifest.frame!=='enu')throw Error('Unsupported actor-nadir camera revision/frame');
      const snapshot=object(cameraManifest.snapshot), snapshotCut=object(snapshot.cut);
      if(counter(snapshotCut.index)!==index||!Array.isArray(snapshotCut.instant)||integerText(snapshotCut.instant[0])!==ns||counter(snapshotCut.instant[1])!==microstep||!Array.isArray(snapshot.entities))throw Error('Camera pose snapshot cut mismatch');
      for(const value of snapshot.entities) {const pose=recordedCameraPose(value);const actual=store.sample(pose.key,pose.field);if(!actual||JSON.stringify(actual)!==JSON.stringify(pose.position))throw Error('Camera snapshot pose differs from committed facts');}
      const binding=resolveBinding(header,entity.typeId);if(!binding)throw Error('Actor-nadir needs a spatial actor');
      eye=vector(store.sample(key,binding.positionField));target=[eye[0],eye[1],0];
      if(eye[2]<=0)throw Error('Actor-nadir camera must be above ground');
    } else {eye=vector(cameraManifest.eye);target=vector(cameraManifest.target);}
    if (eye.every((value, i) => value === target[i])) throw Error('Capture eye and target coincide');
    if (!resolveBinding(header, entity.typeId)) vector(cameraManifest.anchor); // An explicit nonspatial camera anchor is required.
    for (const item of store.entities.values()) {
      const binding = resolveBinding(header, item.typeId); if (!binding) continue;
      if (!store.sample(item.key, binding.positionField)) throw Error(`Capture required pose missing: ${item.key.id}.${binding.positionField}`);
      if (binding.orientationField && !store.sample(item.key, binding.orientationField)) throw Error(`Capture required orientation missing: ${item.key.id}.${binding.orientationField}`);
    }
    renderer = new T.WebGLRenderer({ antialias: false, preserveDrawingBuffer: true });
    renderer.setPixelRatio(1); renderer.setSize(width, height); renderer.outputColorSpace = T.SRGBColorSpace; mount.replaceChildren(renderer.domElement);
    scene = new T.Scene(); lighting = setupScene(renderer, scene); assets = new Assets(renderer);
    const failures: string[] = []; const status = (message: string) => { if (message.includes('unavailable')) failures.push(message); };
    const models = header.presentation.filter(binding => binding.visual.kind === 'model' && !binding.visual.asset?.startsWith('procedural:')); // EntityLayer constructs local procedural actors.
    await Promise.all(models.map(binding => { if (!binding.visual.asset) throw Error('Capture model has no asset URL'); return assets!.model(binding.visual.asset); }));
    if (header.scene) await loadSceneLayer(header.scene, header, assets, abort.signal, item => {scene!.add(item); lighting!.grid.visible=false;}, status);
    if (header.scene?.hdri) { hdr = await assets.hdri(header.scene.hdri, renderer); scene.environment = hdr.texture; }
    if (failures.length || abort.signal.aborted) throw Error(failures.join('; ') || 'Capture timed out');
    const near = cameraManifest.near ?? 0.15, far = cameraManifest.far ?? 16000;
    if (typeof near !== 'number' || typeof far !== 'number' || !Number.isFinite(near) || !Number.isFinite(far) || near <= 0 || far <= near) throw Error('Invalid capture clip planes');
    const camera = new T.PerspectiveCamera(cameraManifest.fov, width / height, near, far);
    const cameraPosition = (value: [number, number, number]) => cameraManifest.frame === 'enu' ? worldPosition(value, 'enu') : cameraManifest.frame === undefined || cameraManifest.frame === 'render-world' ? new T.Vector3(...value) : (() => { throw Error('Unsupported capture camera frame'); })();
    if(cameraManifest.preset==='actor-nadir')camera.up.set(0,0,-1);
    camera.position.copy(cameraPosition(eye)); camera.lookAt(cameraPosition(target));
    const overlay = document.createElement('div'); layer = new EntityLayer(header, assets, overlay, () => {}, error => failures.push(String(error)), status); scene.add(layer.root);
    layer.update(store, ns, camera, key, false); await Promise.resolve(); await Promise.resolve(); layer.update(store, ns, camera, key, false);
    if (failures.length || abort.signal.aborted) throw Error(failures.join('; ') || 'Capture timed out');
    // Match the console’s low-quality city pipeline, including its tone mapping.
    composer = pipeline(renderer, scene, camera, 'low'); composer.setSize(width,height);
    composer.render(0); renderer.getContext().finish();
    const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
    const context = canvas.getContext('2d'); if (!context) throw Error('PNG readback canvas unavailable'); context.drawImage(renderer.domElement, 0, 0);
    if (input.label) { context.fillStyle = '#fff'; context.font = '12px monospace'; context.fillText(input.label, 4, height - 6); }
    const applied = { ...request, actor: { ...actor, id: entity.key.id, generation: actor.generation, type_id: entity.typeId, epoch: identity.epoch }, source_cut: { index: cut.commitIndex, instant: [BigInt(cut.at.ns) > BigInt(Number.MAX_SAFE_INTEGER) ? {$integer:cut.at.ns} : Number(cut.at.ns), cut.at.microstep] }, width: canvas.width, height: canvas.height };
    return { request: applied, png_data_url: canvas.toDataURL('image/png') };
  } finally { clearTimeout(timer); abort.abort(); layer?.dispose(); if (scene) disposeObject(scene); lighting?.dispose(); hdr?.dispose(); assets?.dispose(); composer?.dispose(); renderer?.dispose(); blobs.forEach(url => URL.revokeObjectURL(url)); }
}
