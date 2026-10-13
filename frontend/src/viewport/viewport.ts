import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { EntityKey, RunHeader } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { entityId } from './bindings';
import { Assets, disposeObject, type AssetOptions } from './assets';
import { StaticOcclusion } from './occlusion';
import { GpuTimer } from './gpu-timer';
import { EntityLayer } from './entities';
import { parseModelCatalog } from './model-catalog';
import { SemanticLayer } from './semantic-layer';
import {activeZones,semanticSites} from './semantic';
import {ProceduralDistrict} from '../scene/procedural-district';
import { pipeline, PRESETS, type Quality } from './pipeline';
import { setupScene } from './scene';
import { scenePresentation, type ScenePresentation } from '../scene/presentation';
import { loadSceneLayer } from '../scene/city-layer';
import type { CityProgress } from '../scene/traffic-city';
import { CameraDirector, type CameraMode } from '../scene/camera-director';
import { translate } from '../i18n/translate';
import type {DirectorTarget} from './business-director';
import { workbenchLayoutKey } from './label-placement';
export type { CameraMode } from '../scene/camera-director';
// Look down far enough to retain city context when following an airborne unit.
export const FOLLOW_CAMERA_OFFSET = [22, 48, 24] as const;
export const GROUND_CAMERA_OFFSET = [65, 100, 80] as const;
/** Ease from a street-context view at ground level to the airborne framing. */
export function followCameraOffset(altitude: number): T.Vector3 {
  return new T.Vector3(...GROUND_CAMERA_OFFSET).lerp(new T.Vector3(...FOLLOW_CAMERA_OFFSET), Math.min(1, Math.max(0, altitude / 100)));
}
export interface ViewportOptions extends AssetOptions {
  quality?: Quality;
  locale?: string;
  city?: { url: string; kind: 'glb' | 'osm2world'; assetsBase?: string; offset?: [number, number, number] };
  hdri?: string;
  onSelect?: (entity: EntityKey) => void;
  onError?: (error: unknown) => void;
  onQuality?: (quality: Quality, fps?: number) => void;
  onStatus?: (status: string) => void;
  onCityProgress?: (progress?: CityProgress) => void;
  /** A city/asset load failed; the owner must show it persistently, not as a toast. */
  onCityIssue?: (issue: string | undefined) => void;
  onCameraInteraction?: () => void;
  /** Newcomer semantic overlay: friendly labels, zones and site markers. */
  semantic?: boolean;
  showAllLabels?: boolean;
  onLegend?: (legend: Array<{ label: string; color: string }>, zoneCount: number) => void;
}
/** Imperative display renderer; never writes simulation state or infers domain kinds. */
export class Viewport {
  readonly scene = new T.Scene();
  readonly camera = new T.PerspectiveCamera(48, 1, 0.15, 16000);
  readonly renderer: T.WebGLRenderer;
  private controls: OrbitControls;
  private assets: Assets;
  private layer: EntityLayer;
  private lighting: ReturnType<typeof setupScene>;
  private composer: ReturnType<typeof pipeline>;
  private overlay = document.createElement('div');
  private observer: ResizeObserver;
  private abort = new AbortController();
  private mode: CameraMode = 'orbit';
  private selected?: EntityKey;
  private quality: Quality;
  private disposed = false;
  private qualityTime = 0;
  private qualityFrames = 0;
  private environment?: T.WebGLRenderTarget;
  private staticRoot = new T.Group();
  private occlusion = new StaticOcclusion();
  private origin = new T.Vector3();
  private framed = false;
  private director = new CameraDirector();
  private businessShot?: DirectorTarget;
  private authoredCamera = false;
  private manualQuality = false;
  private dusk = false;
  private profile = new URLSearchParams(location.search).has('perf');
  private gpuTimer?: GpuTimer;
  private revision = 0;
  private lastFrame = '';
  private cameraSettled = false;
  private renderedFrames = 0;
  private shadowKey = '';
  private shadowRevision = -1;
  private semanticLayer?: SemanticLayer;
  private district?:ProceduralDistrict;
  private assetIssues = new Set<string>();
  private lastStatus = '';
  private attribution = '';
  private hoverMarkerId?: string;
  private cameraTransition?: { position: T.Vector3; target: T.Vector3 };
  private home?: { position: T.Vector3; target: T.Vector3 };
  private lastLegend = '';
  private cityGeometryRevision = 0;
  constructor(private container: HTMLElement, private header: RunHeader, private options: ViewportOptions = {}) {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('webgl2', { alpha: false, antialias: false, powerPreference: 'high-performance' });
    if (!context) throw Error(translate(options.locale, 'WebGL2 is unavailable in this browser'));
    this.renderer = new T.WebGLRenderer({ canvas, context });
    this.renderer.outputColorSpace = T.SRGBColorSpace; this.renderer.toneMapping = T.NoToneMapping;
    this.renderer.info.autoReset=false; this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.autoUpdate = false; this.renderer.shadowMap.type = T.PCFSoftShadowMap;
    if(this.profile)this.gpuTimer = new GpuTimer(context);
    this.container.dataset.gpuTimer = this.gpuTimer?.available ? 'available' : 'unavailable';
    this.container.append(canvas); this.container.dataset.backend = 'webgl2';
    const info = context.getExtension('WEBGL_debug_renderer_info');
    this.container.dataset.gpu = info ? context.getParameter(info.UNMASKED_RENDERER_WEBGL) : context.getParameter(context.RENDERER);
    this.overlay.className = 'viewport-labels'; this.container.append(this.overlay);
    this.camera.position.set(140, 95, 170);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.target.set(0, 5, 0); this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.485; this.controls.minDistance = 3; this.controls.maxDistance = 9000;
    this.lighting = setupScene(this.renderer, this.scene); this.assets = new Assets(this.renderer, options);
    this.layer = new EntityLayer(header, this.assets, this.overlay, key => options.onSelect?.(key), error => options.onError?.(error), this.status);
    this.layer.locale = options.locale ?? 'en-US';
    try{this.layer.setCatalog(parseModelCatalog(header.modelCatalog));}catch(error){this.status(`Model catalog unavailable: ${String(error)} · stand-ins active`);}
    if(options.semantic!==undefined)this.layer.semantic=options.semantic;
    if(options.showAllLabels!==undefined)this.layer.showAllLabels=options.showAllLabels;
    this.scene.add(this.layer.root, this.staticRoot);
    if(options.semantic){this.semanticLayer=new SemanticLayer(header,{locale:options.locale,onSelect:key=>options.onSelect?.(key)});this.scene.add(this.semanticLayer.root);}
    this.quality = options.quality ?? 'med'; this.composer = pipeline(this.renderer, this.scene, this.camera, this.quality); this.applyPreset();
    this.observer = new ResizeObserver(this.resize); this.observer.observe(container);
    const sceneFrame = container.closest('.wb')?.querySelector('.wb-scene-frame');
    if (sceneFrame) this.observer.observe(sceneFrame);
    // Capture before OrbitControls receives the gesture, so a drag can take
    // over an automatic shot immediately (not one React/render frame later).
    canvas.addEventListener('pointerdown', this.cameraInteraction, true);
    canvas.addEventListener('wheel', this.cameraInteraction, {capture:true,passive:true});
    canvas.addEventListener('pointerdown', this.pointerDown); canvas.addEventListener('pointerup', this.pointerUp);
    canvas.addEventListener('pointermove', this.pointerMove); this.resize();
    const presentation: ScenePresentation | undefined = options.city || options.hdri ? { city: options.city, hdri: options.hdri } : scenePresentation(header);
    if(options.semantic&&!presentation?.city){this.district=new ProceduralDistrict();this.scene.add(this.district.root);this.container.dataset.context='illustrative';this.setAttribution('Illustrative city · recorded actors and airspace');}
    if(presentation?.camera){this.camera.position.fromArray(presentation.camera.position);this.controls.target.fromArray(presentation.camera.target);this.authoredCamera=true;}
    if(presentation){
      if(presentation.city)this.options.onCityProgress?.({phase:'roads',loaded:0,total:0});
      if(presentation.attribution)this.setAttribution(presentation.attribution);
      if(presentation.city?.kind==='osm2world')this.setAttribution('© OpenStreetMap contributors · ODbL · Procedural display materials');
      void loadSceneLayer(presentation,header,this.assets,this.abort.signal,object=>{this.revision++;if(object.parent!==this.staticRoot)this.staticRoot.add(object);this.staticRoot.updateMatrixWorld(true);if(object.name==='city'&&object.userData.geometryRevision!==undefined&&object.userData.geometryRevision!==this.cityGeometryRevision){this.cityGeometryRevision=object.userData.geometryRevision;this.occlusion.setObjects(this.staticRoot.children);}else this.occlusion.addObjects([object]);this.lighting.grid.visible=false;this.applyCityDisplay(object);if(object.name==='city'){container.dataset.city=object.userData.loading?'loading':'loaded';container.dataset.cityProgress=JSON.stringify(object.userData.progress??{});this.options.onCityProgress?.(object.userData.loading?object.userData.progress:undefined);container.dataset.cityStyle=String(object.userData.displayStyle ?? 'source-model');container.dataset.landscape=JSON.stringify(object.userData.landscape ?? {});this.options.onCityIssue?.(undefined);}},this.status)
        .catch(error=>{if(!this.disposed){this.options.onCityProgress?.(undefined);this.status(String(error));if(presentation?.city)this.options.onCityIssue?.(String(error));options.onError?.(error);}});
      if(presentation.hdri)void this.assets.hdri(presentation.hdri,this.renderer).then(environment=>{
        if(this.disposed){environment.dispose();return;}this.environment=environment;this.scene.environment=environment.texture;this.revision++;container.dataset.ibl='hdri';
      }).catch(error=>{if(!this.disposed){container.dataset.ibl='analytic-sky';this.status(`HDRI unavailable: ${String(error)} · analytic sky IBL active`);}});
    }else this.status('Base scene · no city selected');
  }
  private displayStatus(message: string): string {
    const locale = this.options.locale;
    if (message === 'Base scene · no city selected') return translate(locale, 'Base scene · no city selected');
    if (message === 'Loading city geometry…') return translate(locale, 'Loading city geometry…');
    const progress = /^Loading city: (roads|buildings) · (\d+)\/(\d+) buildings$/.exec(message);
    if (progress) return locale?.startsWith('zh') ? `${progress[1]==='roads'?'道路几何':'建筑模型'}加载中 · ${progress[2]}/${progress[3]} 栋` : message;
    let match = /^Loading model: (.*) · markers active$/.exec(message);
    if (match) return translate(locale, 'Loading model: {asset} · markers active', { asset: match[1] });
    match = /^Loading model: (.*) · stand-ins active$/.exec(message);
    if (match) return translate(locale, 'Loading model: {asset} · stand-ins active', { asset: match[1] });
    match = /^Models loaded · (.*)$/.exec(message);
    if (match) return translate(locale, 'Models loaded · {asset}', { asset: match[1] });
    match = /^Model unavailable: (.*) · markers active$/.exec(message);
    if (match) return translate(locale, 'Model unavailable: {error} · markers active', { error: match[1] });
    match = /^Model unavailable: (.*) · stand-ins active$/.exec(message);
    if (match) return translate(locale, 'Model unavailable: {error} · stand-ins active', { error: match[1] });
    match = /^Model catalog unavailable: (.*) · stand-ins active$/.exec(message);
    if (match) return translate(locale, 'Model catalog unavailable: {error} · stand-ins active', { error: match[1] });
    match = /^HDRI unavailable: (.*) · analytic sky IBL active$/.exec(message);
    if (match) return translate(locale, 'HDRI unavailable: {error} · analytic sky IBL active', { error: match[1] });
    match = /^City unavailable: (.*) · base scene active$/.exec(message);
    if (match) return translate(locale, 'City unavailable: {error} · base scene active', { error: match[1] });
    match = /^SUMO roads unavailable: (.*)$/.exec(message);
    if (match) return translate(locale, 'SUMO roads unavailable: {error}', { error: match[1] });
    match = /^(.*) · geometry loaded(.*)$/.exec(message);
    if (match) return translate(locale, '{scene} · geometry loaded{hint}', { scene: match[1] === 'City' ? translate(locale, 'City') : match[1], hint: match[2] });
    // Unknown service/loader diagnostics are exact technical data, not guessed prose.
    return message;
  }
  private status = (message: string) => {
    if(this.disposed)return;
    this.revision++;
    if(message.includes('unavailable'))this.assetIssues.add(message);
    const parts=[...new Set([...this.assetIssues,message])];
    this.lastStatus=message;
    this.container.dataset.assetStatus=parts.join(' | ');
    if(message.startsWith('Models loaded'))this.container.dataset.models='loaded';
    this.options.onStatus?.(parts.map(item=>this.displayStatus(item)).join(' | '));
  };
  private setAttribution(message: string) {
    this.attribution=message;
    const locale=this.options.locale;
    switch(message){
      case 'Illustrative city · recorded actors and airspace': this.container.dataset.attribution=translate(locale, 'Illustrative city · recorded actors and airspace');break;
      case '© OpenStreetMap contributors · ODbL · Procedural display materials': this.container.dataset.attribution=translate(locale, '© OpenStreetMap contributors · ODbL · Procedural display materials');break;
      case '© OpenStreetMap contributors · ODbL 1.0': this.container.dataset.attribution=translate(locale, '© OpenStreetMap contributors · ODbL 1.0');break;
      default:this.container.dataset.attribution=message;
    }
  }
  setLocale(locale: string) {
    if(this.options.locale===locale)return;
    this.options.locale=locale;this.layer.locale=locale;this.semanticLayer?.setLocale(locale);this.revision++;
    this.setAttribution(this.attribution);
    this.options.onStatus?.([...new Set([...this.assetIssues,this.lastStatus])].filter(Boolean).map(item=>this.displayStatus(item)).join(' | '));
  }
  private applyCityDisplay(object: T.Object3D) {
    object.traverse(node=>{if(node instanceof T.Mesh)for(const material of Array.isArray(node.material)?node.material:[node.material]){
      const uniform=material.userData.displayDusk as {value:number}|undefined;if(uniform)uniform.value=this.dusk?1:0;
    }});
  }
  setDusk(dusk: boolean) {
    this.dusk=dusk;this.lighting.setDusk(dusk);this.composer.setDusk(dusk);this.applyCityDisplay(this.staticRoot);this.revision++;
    this.container.dataset.lighting=dusk?'dusk-display':'day-display';
  }
  setSelection(key?: EntityKey) { this.selected = key; this.revision++; }
  setHighlights(keys: EntityKey[]) { this.layer.setHighlights(new Set(keys.map(entityId))); this.revision++; }
  setCameraMode(mode: CameraMode) {
    if(mode!=='orbit')this.cameraTransition=undefined;
    this.revision++; this.mode = mode; this.controls.enabled = mode === 'orbit'; this.container.dataset.camera=mode;
  }
  setDirectorTarget(target?:DirectorTarget) {
    if(this.businessShot===target)return;
    this.businessShot=target;this.revision++;
    this.container.dataset.directorEntity=target?.entity?.id??'';
    this.container.dataset.directorEvent=target?.eventKey??'';
  }
  private cameraInteraction=()=>{
    this.cameraTransition=undefined;
    this.setDirectorTarget(undefined);this.setCameraMode('orbit');
    this.options.onCameraInteraction?.();
  };
  /** Event coordinates are ENU; camera positions are local east/up/south. */
  focusPosition(position: [number,number,number], radius?: number, immediate = false) {
    const target=new T.Vector3(position[0],position[2],-position[1]).sub(this.origin);
    const offset=new T.Vector3(...GROUND_CAMERA_OFFSET);
    if(radius!==undefined){const vertical=this.camera.fov*Math.PI/360,horizontal=Math.atan(Math.tan(vertical)*this.camera.aspect),distance=radius/Math.sin(Math.min(vertical,horizontal))*1.12;if(distance>offset.length())offset.setLength(distance);}
    const destination=this.occlusion.liftCamera(target.clone().add(offset));
    this.setCameraMode('orbit');
    if(immediate){this.controls.target.copy(target);this.camera.position.copy(destination);this.camera.lookAt(target);this.cameraTransition=undefined;}
    else this.cameraTransition={position:destination,target};
    this.authoredCamera=true;this.revision++;
  }
  /** Display-only camera pad: these never change simulation state. */
  zoom(factor: number) {
    const target=(this.cameraTransition?.target??this.controls.target).clone(),from=this.cameraTransition?.position??this.camera.position;
    const offset=from.clone().sub(target),length=Math.min(6000,Math.max(6,offset.length()*factor));
    this.setCameraMode('orbit');this.cameraTransition={position:target.clone().add(offset.setLength(length)),target};this.authoredCamera=true;this.revision++;
  }
  orbitBy(radians: number) {
    const target=(this.cameraTransition?.target??this.controls.target).clone(),from=this.cameraTransition?.position??this.camera.position;
    const offset=from.clone().sub(target).applyAxisAngle(new T.Vector3(0,1,0),radians);
    this.setCameraMode('orbit');this.cameraTransition={position:target.clone().add(offset),target};this.authoredCamera=true;this.revision++;
  }
  resetView() {
    if(!this.home)return;
    this.setCameraMode('orbit');this.cameraTransition={position:this.home.position.clone().sub(this.origin),target:this.home.target.clone().sub(this.origin)};this.revision++;
  }
  /** Frame the city geometry and all live entities together — not only the objects. */
  fitRegion() {
    const bounds=new T.Box3();
    const city=new T.Box3().setFromObject(this.staticRoot);
    if(!city.isEmpty())bounds.union(city);
    if(this.layer.positions.size)for(const position of this.layer.positions.values())bounds.expandByPoint(position.clone().add(this.origin));
    if(bounds.isEmpty())return;
    const center=bounds.getCenter(new T.Vector3()),span=Math.max(120,bounds.getSize(new T.Vector3()).length());
    this.setCameraMode('orbit');
    this.cameraTransition={position:center.clone().add(new T.Vector3(0.7,0.55,0.9).multiplyScalar(span*1.15)),target:center.clone().sub(this.origin)};
    this.authoredCamera=true;this.revision++;
  }
  setQuality(quality: Quality, manual = false) {
    if(manual)this.manualQuality=true;
    if (quality === this.quality) return;
    this.revision++; this.quality = quality; this.composer.dispose(); this.composer = pipeline(this.renderer, this.scene, this.camera, quality);
    this.composer.setDusk(this.dusk);this.applyPreset(); this.resize(); this.qualityTime = this.qualityFrames = 0; this.options.onQuality?.(quality);
  }
  private applyPreset() {
    const preset = PRESETS[this.quality]; this.renderer.setPixelRatio(Math.min(devicePixelRatio, preset.ratio));
    this.renderer.shadowMap.enabled = this.quality !== 'low'; this.lighting.sun.shadow.mapSize.set(preset.shadow, preset.shadow);
    this.lighting.sun.shadow.map?.dispose(); this.lighting.sun.shadow.map = null; this.container.dataset.quality = this.quality;
  }
  render(store: FeedStore, ns: string, deltaSeconds: number, trails = true) {
    if (this.disposed) return;
    const frameStarted = performance.now();
    if(this.cameraTransition){
      const {position,target}=this.cameraTransition,amount=1-Math.exp(-7*Math.min(0.1,Math.max(0,deltaSeconds)));
      this.camera.position.lerp(position,amount);this.controls.target.lerp(target,amount);this.camera.lookAt(this.controls.target);
      if(this.camera.position.distanceToSquared(position)<0.0025&&this.controls.target.distanceToSquared(target)<0.0025){this.camera.position.copy(position);this.controls.target.copy(target);this.cameraTransition=undefined;}
    }
    if(this.mode==='orbit')this.controls.update();
    const frameKey = `${ns}/${store.revision}/${this.revision}/${trails}/${this.camera.position.toArray()}/${this.camera.quaternion.toArray()}/${this.controls.target.toArray()}/${workbenchLayoutKey(this.container)}`;
    const continuousShot=this.mode==='cinematic'&&!this.options.semantic;
    if(!continuousShot&&!this.cameraTransition&&(this.mode==='orbit'||this.cameraSettled)&&frameKey===this.lastFrame){this.container.dataset.idle='true';return;}
    this.lastFrame=frameKey;this.container.dataset.idle='false';
    this.district?.update(store,this.origin);
    const chosen = this.mode === 'cinematic' ? (this.businessShot ? this.businessShot.entity : (this.options.semantic ? this.selected : this.director.choose(store,ns,deltaSeconds,this.selected))) : this.selected;
    this.cameraSettled=true;
    const aimCamera=()=>{
      if(!this.framed && this.layer.positions.size){
        const bounds=this.district?.bounds?.clone().translate(this.origin.clone().negate())??new T.Box3().setFromPoints([...this.layer.positions.values()]),center=bounds.getCenter(new T.Vector3());
        if(!this.authoredCamera){const span=Math.max(100,bounds.getSize(new T.Vector3()).length());this.controls.target.copy(center);this.camera.position.copy(center).add(new T.Vector3(0.7,0.55,0.9).multiplyScalar(span));}
        this.framed=true;this.home={position:this.camera.position.clone().add(this.origin),target:this.controls.target.clone().add(this.origin)};
      }
      const eventPosition=this.businessShot?.position;
      const focus=(chosen?this.layer.positions.get(entityId(chosen)):undefined)??(eventPosition?new T.Vector3(eventPosition[0],eventPosition[2],-eventPosition[1]).sub(this.origin):undefined);
      if(focus&&this.mode!=='orbit'){
        let offset=followCameraOffset(focus.y + this.origin.y);
        if(this.mode==='chase'){
          const orientation=this.layer.orientations.get(entityId(chosen!));offset=new T.Vector3(-4,2.4,5.5);if(orientation)offset.applyQuaternion(orientation);
        }else if(this.mode==='cinematic'&&!this.options.semantic)offset=this.director.offset();
        const delta=Math.min(0.1,Math.max(0,deltaSeconds));
        const destination=this.occlusion.liftCamera(focus.clone().add(offset));
        this.camera.position.lerp(destination,1-Math.exp(-(this.mode==='cinematic'?1.8:5)*delta));
        // The transition itself must stay clear when crossing a building, not just its endpoint.
        this.camera.position.copy(this.occlusion.liftCamera(this.camera.position));
        this.controls.target.lerp(focus,1-Math.exp(-(this.mode==='cinematic'?2.4:8)*delta));
        this.cameraSettled=this.camera.position.distanceToSquared(destination)<0.0025&&this.controls.target.distanceToSquared(focus)<0.0025;
        // Finish the display transition at the same tolerance as orbit/zoom.
        // An unchanged paused subject then needs no repeated city draw.
        if(this.cameraSettled){this.camera.position.copy(destination);this.controls.target.copy(focus);}
        this.camera.lookAt(this.controls.target);
      }
    };
    this.staticRoot.updateMatrixWorld(); this.layer.update(store, ns, this.camera, chosen, trails, this.origin, this.occlusion, aimCamera);
    if(this.semanticLayer&&this.layer.semantic){
      this.semanticLayer.update(store,ns,this.origin);
      this.semanticLayer.root.visible=true;
      if(this.hoverMarkerId)this.container.dataset.semanticHover=this.semanticLayer.labelFor(this.hoverMarkerId)??'';
      if(this.options.onLegend)this.emitLegend(store);
    }else if(this.semanticLayer)this.semanticLayer.root.visible=false;
    this.container.dataset.payloadCount=String(this.layer.payloadCount);
    this.container.dataset.chargingPadCount=String(this.semanticLayer?.chargingPadCount??0);
    const entitiesFinished = performance.now();
    // Rebase all display coordinates together once the camera leaves a 2 km cell.
    if(Math.hypot(this.controls.target.x,this.controls.target.z)>2000){
      const shift=this.controls.target.clone();shift.y=0;this.origin.add(shift);this.camera.position.sub(shift);this.controls.target.sub(shift);
      if(this.cameraTransition){this.cameraTransition.position.sub(shift);this.cameraTransition.target.sub(shift);}
      this.staticRoot.position.copy(this.origin).negate();
      this.staticRoot.updateMatrixWorld(true);this.occlusion.translate(shift.clone().negate());this.revision++;
      this.layer.update(store,ns,this.camera,chosen,trails,this.origin,this.occlusion);
    }
    this.lighting.update(this.controls.target);
    this.composer.outline.selection.set(this.layer.selection.length?this.layer.selection:this.layer.ring.visible?[this.layer.ring]:[]);
    const updateFinished = performance.now();
    const shadowKey = this.lighting.sun.position.toArray().join(',');
    if(this.layer.transformsChanged||shadowKey!==this.shadowKey||this.shadowRevision!==this.revision){
      this.renderer.shadowMap.needsUpdate=true;this.shadowKey=shadowKey;this.shadowRevision=this.revision;
    }
    this.gpuTimer?.begin();
    this.renderer.info.reset(); this.composer.render(deltaSeconds);
    this.gpuTimer?.end();
    if(this.gpuTimer?.milliseconds!==undefined){
      this.container.dataset.gpuMs=String(this.gpuTimer.milliseconds);
      this.container.dataset.gpuSample=String(this.gpuTimer.serial);
    }else{delete this.container.dataset.gpuMs;delete this.container.dataset.gpuSample;}
    // T4-06: per-frame attribute writes invalidate style/layout on every frame.
    // The e2e assertions only need cameraPosition/cameraTarget; the counters and
    // timings are diagnostics, written only under the ?perf profile.
    if(this.profile){
      this.container.dataset.renderCount=String(++this.renderedFrames);
      const renderFinished = performance.now();
      this.container.dataset.updateMs=String(updateFinished-frameStarted);
      this.container.dataset.labelsMs=String(this.layer.labelsMs);
      this.container.dataset.entitiesMs=String(entitiesFinished-frameStarted);
      this.container.dataset.submitMs=String(renderFinished-updateFinished);
      performance.measure('viewport.update',{start:frameStarted,end:updateFinished});
      performance.measure('viewport.submit',{start:updateFinished,end:renderFinished});
      performance.clearMeasures('viewport.update');performance.clearMeasures('viewport.submit');
      this.container.dataset.renderMs=String(performance.now()-frameStarted);
      this.container.dataset.drawCalls=String(this.renderer.info.render.calls);
      this.container.dataset.triangles=String(this.renderer.info.render.triangles);
      this.container.dataset.geometries=String(this.renderer.info.memory.geometries);
      this.container.dataset.textures=String(this.renderer.info.memory.textures);
    } else this.renderedFrames++;
    this.container.dataset.rendered='true';
    this.container.dataset.spatialCount=String(this.layer.positions.size);
    this.container.dataset.highlightCount=String(this.layer.highlightedShown);
    this.container.dataset.cameraPosition=JSON.stringify(this.camera.position.toArray());
    this.container.dataset.cameraTarget=JSON.stringify(this.controls.target.toArray());
    this.container.dataset.cameraFov=String(this.camera.fov);
    if(deltaSeconds>0&&!document.hidden){
      this.qualityTime+=deltaSeconds;this.qualityFrames++;
      if(this.qualityTime>5){
        const fps=this.qualityFrames/this.qualityTime;this.container.dataset.fps=fps.toFixed(1);
        if(fps<50&&this.quality!=='low'&&!this.manualQuality)this.setQuality(this.quality==='high'?'med':'low');
        this.options.onQuality?.(this.quality,fps);this.qualityTime=this.qualityFrames=0;
      }
    }
  }
  /** Legend from actual business kinds present in the committed cut, plus active zones. */
  private emitLegend(store: FeedStore) {
    const entries = new Map<string, string>();
    for (const [label, color] of this.layer.businessLegend(store)) entries.set(label, color);
    const activeKinds=new Set(activeZones(store.header,store).map(zone=>zone.kind));
    const locale=this.options.locale??'en-US';
    const labels={no_fly:translate(locale, 'No-fly zone'),restricted:translate(locale, 'Restricted zone'),altitude_band:translate(locale, 'Altitude band'),corridor:translate(locale, 'Corridor')};
    for (const [label,color] of SemanticLayer.zoneLegend(locale)) if([...activeKinds].some(kind=>labels[kind]===label))entries.set(label,color);
    const sites=semanticSites(store.header,store);if(sites.some(site=>site.isVertiport))entries.set(translate(locale, 'Vertiport'),'#009e73');if(sites.some(site=>site.isPad))entries.set(translate(locale, 'Charging pad'),'#e69f00');
    const legend=[...entries].map(([label,color])=>({label,color})),zones=this.layer.activeZoneCount(store),key=JSON.stringify([legend,zones]);
    if(key!==this.lastLegend){this.lastLegend=key;this.options.onLegend?.(legend,zones);}
  }
  setSemantic(next: { semantic?: boolean; showAllLabels?: boolean }) {
    if (next.semantic !== undefined) {
      this.layer.semantic = next.semantic;
      if(next.semantic&&!this.semanticLayer){this.semanticLayer=new SemanticLayer(this.header,{locale:this.options.locale,onSelect:key=>this.options.onSelect?.(key)});this.scene.add(this.semanticLayer.root);}
      if(!next.semantic){this.hoverMarkerId=undefined;this.layer.setHover();delete this.container.dataset.semanticHover;}
    }
    if (next.showAllLabels !== undefined) this.layer.showAllLabels = next.showAllLabels;
    this.revision++;
  }
  private resize = () => {
    this.revision++;
    const width=Math.max(1,this.container.clientWidth),height=Math.max(1,this.container.clientHeight);
    const rect=this.container.getBoundingClientRect(),frame=this.container.closest('.wb')?.querySelector('.wb-scene-frame')?.getBoundingClientRect();
    if(frame&&frame.width>0&&frame.height>0)this.camera.setViewOffset(frame.width,frame.height,rect.left-frame.left,rect.top-frame.top,width,height);
    else {this.camera.clearViewOffset();this.camera.aspect=width/height;}
    this.camera.updateProjectionMatrix();this.renderer.setSize(width,height);this.composer.setSize(width,height);
  };
  private press?: {x:number;y:number};
  // T4-06: reused pick objects — a mouse-move storm must not allocate per event.
  private pickRaycaster = new T.Raycaster();
  private pickPoint = new T.Vector2();
  private pickRay=(event:PointerEvent)=>{
    const rect=this.renderer.domElement.getBoundingClientRect();
    this.pickPoint.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
    this.pickRaycaster.setFromCamera(this.pickPoint,this.camera);
    return this.pickRaycaster;
  };
  private pointerMove=(event:PointerEvent)=>{
    if(!this.semanticLayer||!this.layer.semantic)return;
    const raycaster=this.pickRay(event);
    const marker=this.semanticLayer.pick(raycaster);
    const id=marker?`${marker.key.id}:${marker.key.generation}`:undefined;this.hoverMarkerId=id;
    const entityKey=this.layer.pick(raycaster);
    this.layer.setHover(entityKey?`${entityKey.id}:${entityKey.generation}`:id);this.revision++;
    this.container.dataset.semanticHover=marker?.label??'';
  };
  private pointerDown=(event:PointerEvent)=>{this.press={x:event.clientX,y:event.clientY};};
  private pointerUp=(event:PointerEvent)=>{
    if(!this.press||Math.hypot(event.clientX-this.press.x,event.clientY-this.press.y)>5)return;
    const key=this.layer.pick(this.pickRay(event));
    if(!key&&this.semanticLayer&&this.layer.semantic){const marker=this.semanticLayer.pick(this.pickRay(event));if(marker)this.options.onSelect?.(marker.key);}
    if(key)this.options.onSelect?.(key);
  };
  dispose(){
    this.disposed=true;this.abort.abort();this.observer.disconnect();
    this.renderer.domElement.removeEventListener('pointerdown',this.cameraInteraction,true);this.renderer.domElement.removeEventListener('wheel',this.cameraInteraction,true);
    this.renderer.domElement.removeEventListener('pointerdown',this.pointerDown);this.renderer.domElement.removeEventListener('pointerup',this.pointerUp);
    this.renderer.domElement.removeEventListener('pointermove',this.pointerMove);
    this.controls.dispose();this.occlusion.dispose();this.layer.dispose();this.assets.dispose();disposeObject(this.scene);this.lighting.dispose();this.environment?.dispose();
    this.district?.dispose();
    if(this.semanticLayer){this.semanticLayer.dispose();}
    this.gpuTimer?.dispose();this.composer.dispose();this.renderer.dispose();this.renderer.domElement.remove();this.overlay.remove();
  }
}
