import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { EntityKey, RunHeader } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { entityId } from './bindings';
import { Assets, disposeObject, loadCityPack, type AssetOptions } from './assets';
import { EntityLayer } from './entities';
import { pipeline, PRESETS, type Quality } from './pipeline';
import { setupScene } from './scene';

export type CameraMode = 'orbit' | 'follow' | 'chase';
export interface ViewportOptions extends AssetOptions {
  quality?: Quality;
  city?: { url: string; kind: 'glb' | 'osm2world'; assetsBase?: string; offset?: [number, number, number] };
  hdri?: string;
  onSelect?: (entity: EntityKey) => void;
  onError?: (error: unknown) => void;
  onQuality?: (quality: Quality, fps?: number) => void;
}

/** Imperative renderer: no React, transport, simulation writes, or domain kinds. */
export class Viewport {
  readonly scene = new T.Scene();
  readonly camera = new T.PerspectiveCamera(48, 1, 0.2, 5000);
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
  private followOffset = new T.Vector3(22, 16, 26);
  private environment?: T.WebGLRenderTarget;
  constructor(private container: HTMLElement, header: RunHeader, private options: ViewportOptions = {}) {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('webgl2', { alpha: false, antialias: false, powerPreference: 'high-performance' });
    if (!context) throw Error('WebGL2 is unavailable in this browser');
    this.renderer = new T.WebGLRenderer({ canvas, context });
    this.renderer.outputColorSpace = T.SRGBColorSpace;
    // Composer uses a linear half-float buffer and applies ACES once at output.
    this.renderer.toneMapping = T.NoToneMapping;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = T.PCFSoftShadowMap;
    this.container.append(canvas); this.container.dataset.backend = 'webgl2';
    this.overlay.className = 'viewport-labels'; this.container.append(this.overlay);
    this.camera.position.set(92, 52, 110);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.target.set(0, 5, 0); this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.485; this.controls.minDistance = 5; this.controls.maxDistance = 500;
    this.lighting = setupScene(this.renderer, this.scene);
    this.assets = new Assets(this.renderer, options);
    this.layer = new EntityLayer(header, this.assets, this.overlay, key => options.onSelect?.(key), error => options.onError?.(error));
    this.scene.add(this.layer.root);
    this.quality = options.quality ?? 'med';
    this.composer = pipeline(this.renderer, this.scene, this.camera, this.quality);
    this.applyPreset();
    this.observer = new ResizeObserver(this.resize); this.observer.observe(container);
    canvas.addEventListener('pointerdown', this.pointerDown); canvas.addEventListener('pointerup', this.pointerUp);
    this.resize();
    if (options.city) void this.loadCity(header).catch(error => { if (!this.disposed) options.onError?.(error); });
    if (options.hdri) void this.assets.hdri(options.hdri, this.renderer).then(environment => {
      if (this.disposed) { environment.dispose(); return; }
      this.environment = environment; this.scene.environment = environment.texture;
    }).catch(error => { if (!this.disposed) options.onError?.(error); });
  }
  private async loadCity(header: RunHeader) {
    const city = this.options.city!;
    const object = city.kind === 'osm2world'
      ? await loadCityPack(city.url, city.assetsBase ?? new URL('.', new URL(city.url, location.href)).href, header.origin, this.abort.signal)
      : (await this.assets.model(city.url)).clone(true);
    if (this.disposed) { if (city.kind === 'osm2world') disposeObject(object); return; }
    if (city.offset) object.position.add(new T.Vector3(...city.offset));
    this.scene.add(object);
  }
  setSelection(key?: EntityKey) { this.selected = key; }
  setCameraMode(mode: CameraMode) {
    if (this.mode === 'orbit' && mode === 'follow') this.followOffset.copy(this.camera.position).sub(this.controls.target).clampLength(40, 80);
    this.mode = mode; this.controls.enabled = mode === 'orbit';
  }
  setQuality(quality: Quality) {
    if (quality === this.quality) return;
    this.quality = quality;
    this.composer.dispose();
    this.composer = pipeline(this.renderer, this.scene, this.camera, quality);
    this.applyPreset(); this.resize(); this.qualityTime = this.qualityFrames = 0;
    this.options.onQuality?.(quality);
  }
  private applyPreset() {
    const preset = PRESETS[this.quality];
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, preset.ratio));
    this.renderer.shadowMap.enabled = this.quality !== 'low';
    this.lighting.sun.shadow.mapSize.set(preset.shadow, preset.shadow);
    this.lighting.sun.shadow.map?.dispose(); this.lighting.sun.shadow.map = null;
    this.container.dataset.quality = this.quality;
  }
  render(store: FeedStore, ns: string, deltaSeconds: number, trails = true) {
    if (this.disposed) return;
    this.layer.update(store, ns, this.camera, this.selected, trails);
    const focus = this.selected ? this.layer.positions.get(entityId(this.selected)) : undefined;
    if (focus && this.mode !== 'orbit') {
      const target = focus.clone();
      let offset = this.followOffset;
      if (this.mode === 'chase') {
        const orientation = this.layer.orientations.get(entityId(this.selected!));
        offset = new T.Vector3(-16, 6, 0); if (orientation) offset.applyQuaternion(orientation);
      }
      this.camera.position.lerp(target.clone().add(offset), 1 - Math.exp(-6 * deltaSeconds));
      this.controls.target.copy(target); this.camera.lookAt(target);
    } else this.controls.update();
    this.composer.render(deltaSeconds);
    this.container.dataset.rendered = 'true';
    if (deltaSeconds > 0 && deltaSeconds < 1 && !document.hidden) {
      this.qualityTime += deltaSeconds; this.qualityFrames++;
      if (this.qualityTime > 5) {
        const fps = this.qualityFrames / this.qualityTime;
        if (fps < 28 && this.quality !== 'low') this.setQuality(this.quality === 'high' ? 'med' : 'low');
        this.options.onQuality?.(this.quality, fps); this.qualityTime = this.qualityFrames = 0;
      }
    }
  }
  private resize = () => {
    const width = Math.max(1, this.container.clientWidth), height = Math.max(1, this.container.clientHeight);
    this.camera.aspect = width / height; this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height); this.composer.setSize(width, height);
  };
  private press?: { x: number; y: number };
  private pointerDown = (event: PointerEvent) => { this.press = { x: event.clientX, y: event.clientY }; };
  private pointerUp = (event: PointerEvent) => {
    if (!this.press || Math.hypot(event.clientX - this.press.x, event.clientY - this.press.y) > 5) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const point = new T.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    const raycaster = new T.Raycaster(); raycaster.setFromCamera(point, this.camera);
    const key = this.layer.pick(raycaster); if (key) this.options.onSelect?.(key);
  };
  dispose() {
    this.disposed = true; this.abort.abort(); this.observer.disconnect();
    this.renderer.domElement.removeEventListener('pointerdown', this.pointerDown);
    this.renderer.domElement.removeEventListener('pointerup', this.pointerUp);
    this.controls.dispose(); this.layer.dispose(); this.assets.dispose();
    disposeObject(this.scene); this.lighting.dispose(); this.environment?.dispose();
    this.composer.dispose(); this.renderer.dispose(); this.renderer.domElement.remove(); this.overlay.remove();
  }
}
