import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { EntityKey, RunHeader } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { entityId } from './bindings';
import { Assets, disposeObject, type AssetOptions } from './assets';
import { StaticOcclusion } from './occlusion';
import { GpuTimer } from './gpu-timer';
import { EntityLayer } from './entities';
import { pipeline, PRESETS, type Quality } from './pipeline';
import { setupScene } from './scene';
import { scenePresentation, type ScenePresentation } from '../scene/presentation';
import { loadSceneLayer } from '../scene/city-layer';
import { CameraDirector, type CameraMode } from '../scene/camera-director';
export type { CameraMode } from '../scene/camera-director';
export interface ViewportOptions extends AssetOptions {
  quality?: Quality;
  city?: { url: string; kind: 'glb' | 'osm2world'; assetsBase?: string; offset?: [number, number, number] };
  hdri?: string;
  onSelect?: (entity: EntityKey) => void;
  onError?: (error: unknown) => void;
  onQuality?: (quality: Quality, fps?: number) => void;
  onStatus?: (status: string) => void;
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
  private followOffset = new T.Vector3(22, 14, 24);
  private environment?: T.WebGLRenderTarget;
  private staticRoot = new T.Group();
  private occlusion = new StaticOcclusion();
  private origin = new T.Vector3();
  private framed = false;
  private director = new CameraDirector();
  private authoredCamera = false;
  private manualQuality = false;
  private dusk = false;
  private profile = new URLSearchParams(location.search).has('perf');
  private gpuTimer?: GpuTimer;
  private revision = 0;
  private lastFrame = '';
  private renderedFrames = 0;
  private shadowKey = '';
  private shadowRevision = -1;
  private assetIssues = new Set<string>();
  constructor(private container: HTMLElement, header: RunHeader, private options: ViewportOptions = {}) {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('webgl2', { alpha: false, antialias: false, powerPreference: 'high-performance' });
    if (!context) throw Error('WebGL2 is unavailable in this browser');
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
    this.scene.add(this.layer.root, this.staticRoot);
    this.quality = options.quality ?? 'med'; this.composer = pipeline(this.renderer, this.scene, this.camera, this.quality); this.applyPreset();
    this.observer = new ResizeObserver(this.resize); this.observer.observe(container);
    canvas.addEventListener('pointerdown', this.pointerDown); canvas.addEventListener('pointerup', this.pointerUp); this.resize();
    const presentation: ScenePresentation | undefined = options.city || options.hdri ? { city: options.city, hdri: options.hdri } : scenePresentation(header);
    if(presentation?.camera){this.camera.position.fromArray(presentation.camera.position);this.controls.target.fromArray(presentation.camera.target);this.authoredCamera=true;}
    if(presentation){
      if(presentation.attribution)container.dataset.attribution=presentation.attribution;
      if(presentation.city?.kind==='osm2world')container.dataset.attribution='© OpenStreetMap contributors · ODbL · Procedural display materials';
      void loadSceneLayer(presentation,header,this.assets,this.abort.signal,object=>{this.revision++;this.staticRoot.add(object);this.staticRoot.updateMatrixWorld(true);this.occlusion.setObjects(this.staticRoot.children);this.lighting.grid.visible=false;this.applyCityDisplay(object);if(object.name==='city'){container.dataset.city='loaded';container.dataset.cityStyle=String(object.userData.displayStyle ?? 'source-model');container.dataset.landscape=JSON.stringify(object.userData.landscape ?? {});}},this.status)
        .catch(error=>{if(!this.disposed){this.status(String(error));options.onError?.(error);}});
      if(presentation.hdri)void this.assets.hdri(presentation.hdri,this.renderer).then(environment=>{
        if(this.disposed){environment.dispose();return;}this.environment=environment;this.scene.environment=environment.texture;this.revision++;container.dataset.ibl='hdri';
      }).catch(error=>{if(!this.disposed){container.dataset.ibl='analytic-sky';this.status(`HDRI unavailable: ${String(error)} · analytic sky IBL active`);}});
    }else this.status('Base scene · no city selected');
  }
  private status = (message: string) => {
    if(this.disposed)return;
    this.revision++;
    if(message.includes('unavailable'))this.assetIssues.add(message);
    const combined=[...new Set([...this.assetIssues,message])].join(' | ');
    this.container.dataset.assetStatus=combined;
    if(message.startsWith('Models loaded'))this.container.dataset.models='loaded';
    this.options.onStatus?.(combined);
  };
  private applyCityDisplay(object: T.Object3D) {
    object.traverse(node=>{if(node instanceof T.Mesh)for(const material of Array.isArray(node.material)?node.material:[node.material]){
      const uniform=material.userData.displayDusk as {value:number}|undefined;if(uniform)uniform.value=this.dusk?1:0;
    }});
  }
  setDusk(dusk: boolean) {
    this.dusk=dusk;this.lighting.setDusk(dusk);this.applyCityDisplay(this.staticRoot);this.revision++;
    this.container.dataset.lighting=dusk?'dusk-display':'day-display';
  }
  setSelection(key?: EntityKey) { this.selected = key; this.revision++; }
  setCameraMode(mode: CameraMode) {
    this.revision++; this.mode = mode; this.controls.enabled = mode === 'orbit'; this.container.dataset.camera=mode;
  }
  setQuality(quality: Quality, manual = false) {
    if(manual)this.manualQuality=true;
    if (quality === this.quality) return;
    this.revision++; this.quality = quality; this.composer.dispose(); this.composer = pipeline(this.renderer, this.scene, this.camera, quality);
    this.applyPreset(); this.resize(); this.qualityTime = this.qualityFrames = 0; this.options.onQuality?.(quality);
  }
  private applyPreset() {
    const preset = PRESETS[this.quality]; this.renderer.setPixelRatio(Math.min(devicePixelRatio, preset.ratio));
    this.renderer.shadowMap.enabled = this.quality !== 'low'; this.lighting.sun.shadow.mapSize.set(preset.shadow, preset.shadow);
    this.lighting.sun.shadow.map?.dispose(); this.lighting.sun.shadow.map = null; this.container.dataset.quality = this.quality;
  }
  render(store: FeedStore, ns: string, deltaSeconds: number, trails = true) {
    if (this.disposed) return;
    const frameStarted = performance.now();
    if(this.mode==='orbit')this.controls.update();
    const frameKey = `${ns}/${store.revision}/${this.revision}/${trails}/${this.camera.position.toArray()}/${this.camera.quaternion.toArray()}/${this.controls.target.toArray()}`;
    if(this.mode!=='cinematic'&&frameKey===this.lastFrame){this.container.dataset.idle='true';return;}
    this.lastFrame=frameKey;this.container.dataset.idle='false';
    const chosen = this.mode === 'cinematic' ? this.director.choose(store,ns,deltaSeconds,this.selected) : this.selected;
    this.staticRoot.updateMatrixWorld(); this.layer.update(store, ns, this.camera, chosen, trails, this.origin, this.occlusion);
    const entitiesFinished = performance.now();
    if(!this.framed && this.layer.positions.size){
      const bounds=new T.Box3().setFromPoints([...this.layer.positions.values()]),center=bounds.getCenter(new T.Vector3());
      if(!this.authoredCamera){const span=Math.max(100,bounds.getSize(new T.Vector3()).length());this.controls.target.copy(center);this.camera.position.copy(center).add(new T.Vector3(0.7,0.55,0.9).multiplyScalar(span));}
      this.framed=true;
    }
    const focus=chosen?this.layer.positions.get(entityId(chosen)):undefined;
    if(focus&&this.mode!=='orbit'){
      let offset=this.followOffset;
      if(this.mode==='chase'){
        const orientation=this.layer.orientations.get(entityId(chosen!));offset=new T.Vector3(-4,2.4,5.5);if(orientation)offset.applyQuaternion(orientation);
      }else if(this.mode==='cinematic')offset=this.director.offset();
      this.camera.position.lerp(focus.clone().add(offset),1-Math.exp(-5*deltaSeconds));this.controls.target.copy(focus);this.camera.lookAt(focus);
    }
    // Rebase all display coordinates together once the camera leaves a 2 km cell.
    if(Math.hypot(this.controls.target.x,this.controls.target.z)>2000){
      const shift=this.controls.target.clone();shift.y=0;this.origin.add(shift);this.camera.position.sub(shift);this.controls.target.sub(shift);
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
    this.container.dataset.renderCount=String(++this.renderedFrames);
    const renderFinished = performance.now();
    this.container.dataset.updateMs=String(updateFinished-frameStarted);
    this.container.dataset.labelsMs=String(this.layer.labelsMs);
    this.container.dataset.entitiesMs=String(entitiesFinished-frameStarted);
    this.container.dataset.submitMs=String(renderFinished-updateFinished);
    if(this.profile){
      performance.measure('viewport.update',{start:frameStarted,end:updateFinished});
      performance.measure('viewport.submit',{start:updateFinished,end:renderFinished});
      performance.clearMeasures('viewport.update');performance.clearMeasures('viewport.submit');
    }
    this.container.dataset.rendered='true';
    this.container.dataset.spatialCount=String(this.layer.positions.size);
    this.container.dataset.drawCalls=String(this.renderer.info.render.calls);
    this.container.dataset.triangles=String(this.renderer.info.render.triangles);
    this.container.dataset.geometries=String(this.renderer.info.memory.geometries);
    this.container.dataset.textures=String(this.renderer.info.memory.textures);
    this.container.dataset.cameraPosition=JSON.stringify(this.camera.position.toArray());
    this.container.dataset.cameraTarget=JSON.stringify(this.controls.target.toArray());
    this.container.dataset.cameraFov=String(this.camera.fov);
    this.container.dataset.renderMs=String(performance.now()-frameStarted);
    if(deltaSeconds>0&&!document.hidden){
      this.qualityTime+=deltaSeconds;this.qualityFrames++;
      if(this.qualityTime>5){
        const fps=this.qualityFrames/this.qualityTime;this.container.dataset.fps=fps.toFixed(1);
        if(fps<50&&this.quality!=='low'&&!this.manualQuality)this.setQuality(this.quality==='high'?'med':'low');
        this.options.onQuality?.(this.quality,fps);this.qualityTime=this.qualityFrames=0;
      }
    }
  }
  private resize = () => {
    this.revision++;
    const width=Math.max(1,this.container.clientWidth),height=Math.max(1,this.container.clientHeight);
    this.camera.aspect=width/height;this.camera.updateProjectionMatrix();this.renderer.setSize(width,height);this.composer.setSize(width,height);
  };
  private press?: {x:number;y:number};
  private pointerDown=(event:PointerEvent)=>{this.press={x:event.clientX,y:event.clientY};};
  private pointerUp=(event:PointerEvent)=>{
    if(!this.press||Math.hypot(event.clientX-this.press.x,event.clientY-this.press.y)>5)return;
    const rect=this.renderer.domElement.getBoundingClientRect(),raycaster=new T.Raycaster();
    raycaster.setFromCamera(new T.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),this.camera);
    const key=this.layer.pick(raycaster);if(key)this.options.onSelect?.(key);
  };
  dispose(){
    this.disposed=true;this.abort.abort();this.observer.disconnect();
    this.renderer.domElement.removeEventListener('pointerdown',this.pointerDown);this.renderer.domElement.removeEventListener('pointerup',this.pointerUp);
    this.controls.dispose();this.occlusion.dispose();this.layer.dispose();this.assets.dispose();disposeObject(this.scene);this.lighting.dispose();this.environment?.dispose();
    this.gpuTimer?.dispose();this.composer.dispose();this.renderer.dispose();this.renderer.domElement.remove();this.overlay.remove();
  }
}
