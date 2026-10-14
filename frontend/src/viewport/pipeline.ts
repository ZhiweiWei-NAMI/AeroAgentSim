import * as T from 'three';
import { SelectiveBloomEffect, EffectComposer, EffectPass, Pass, OutlineEffect, RenderPass, SMAAEffect, SMAAPreset, ToneMappingEffect, ToneMappingMode } from 'postprocessing';
import { N8AOPostPass } from '../scene/vendor/N8AO';
export type Quality = 'low' | 'med' | 'high';
// Reserved only for atmosphere renderables (lights still illuminate the base scene).
const ATMOSPHERE_LAYER = 30;
class AtmospherePass extends Pass {
  root?: T.Object3D;
  source?: T.WebGLRenderTarget | null;
  constructor(scene: T.Scene, camera: T.Camera) {
    super('Atmosphere', scene, camera); this.needsSwap = false; this.needsDepthTexture = true;
  }
  dispose() {} // The source target belongs to the composer, not this pass.
  render(renderer: T.WebGLRenderer, input: T.WebGLRenderTarget) {
    if (!this.root?.visible) return;
    // AO/bloom swap colour buffers. Restore the base scene's depth with a blit,
    // without redrawing the city, before depth-testing flames and rain.
    // Allocate the destination attachment before Three resolves its framebuffer.
    renderer.setRenderTarget(input);
    if (this.source !== input && this.source?.depthTexture && input.depthTexture) {
      renderer.copyTextureToTexture(this.source.depthTexture, input.depthTexture);
    }
    const mask = this.camera.layers.mask, background = this.scene.background, clear = renderer.autoClear;
    this.camera.layers.set(ATMOSPHERE_LAYER); this.scene.background = null; renderer.autoClear = false;
    renderer.setRenderTarget(input);
    try { renderer.render(this.scene, this.camera); }
    finally { this.camera.layers.mask = mask; this.scene.background = background; renderer.autoClear = clear; }
  }
}
// Software rasterizers use a smaller drawing buffer; DOM labels keep native resolution.
export const PRESETS = { low: { ratio: 0.5, shadow: 512 }, med: { ratio: 1.25, shadow: 2048 }, high: { ratio: 1.5, shadow: 4096 } };
export function pipeline(renderer: T.WebGLRenderer, scene: T.Scene, camera: T.PerspectiveCamera, quality: Quality) {
  const composer = new EffectComposer(renderer, { frameBufferType: T.HalfFloatType });
  const base = new RenderPass(scene, camera), atmosphere = quality === 'low' ? undefined : new AtmospherePass(scene, camera);
  const renderBase = base.render.bind(base);
  base.render = (renderer, input, output, delta, stencil) => {
    if (atmosphere) atmosphere.source = input;
    const mask = camera.layers.mask;
    // Without AO/bloom, the base pass can draw FX once. Restore the ordinary
    // camera layers before outline's depth/mask passes so they still exclude FX.
    if (quality === 'low') camera.layers.enable(ATMOSPHERE_LAYER);
    try { renderBase(renderer, input, output, delta, stencil); }
    finally { camera.layers.mask = mask; }
  };
  composer.addPass(base);
  let ao: N8AOPostPass | undefined;
  if (quality !== 'low') {
    ao = new N8AOPostPass(scene, camera, 1, 1);
    ao.setQualityMode(quality === 'high' ? 'High' : 'Low');
    ao.configuration.aoRadius = 2; ao.configuration.intensity = 0.65;
    ao.configuration.distanceFalloff = 1; ao.configuration.halfRes = true; ao.configuration.gammaCorrection = false;
    composer.addPass(ao);
  }
  const outline = new OutlineEffect(scene, camera, { edgeStrength: 2, visibleEdgeColor: 0x9ce9ff, hiddenEdgeColor: 0x436172, blur: quality !== 'low', xRay: false });
  const tone = new ToneMappingEffect({ mode: ToneMappingMode.ACES_FILMIC });
  const bloom = quality === 'low' ? undefined : new SelectiveBloomEffect(scene, camera, { intensity: 0.09, luminanceThreshold: 1.3, mipmapBlur: true });
  if (bloom) bloom.inverted = true;
  // FX never enter AO's transparency redraw or bloom/outline's masks. Composite
  // them once after those effects, retaining the shared depth and final tone/SMAA.
  if (atmosphere) {
    composer.addPass(new EffectPass(camera, outline, bloom!));
    composer.addPass(atmosphere);
    composer.addPass(new EffectPass(camera, tone, new SMAAEffect({ preset: quality === 'high' ? SMAAPreset.HIGH : SMAAPreset.MEDIUM })));
  } else composer.addPass(new EffectPass(camera, outline, tone));
  let rootRevision: number | undefined, lastDusk: boolean | undefined, effectRoot: T.Object3D | undefined;
  const assignLayer = (node: T.Object3D) => {
    if (node instanceof T.Mesh || node instanceof T.Sprite || node instanceof T.Line) node.layers.set(ATMOSPHERE_LAYER);
  };
  return Object.assign(composer, { outline, excludeBloom: (root: T.Object3D) => {
    const revision = root.userData.structureRevision as number | undefined;
    if (effectRoot === root && rootRevision === revision) return;
    effectRoot = root; if (atmosphere) atmosphere.root = root;
    rootRevision = revision; root.traverse(assignLayer);
  }, setDusk: (dusk: boolean) => {
    if (lastDusk === dusk) return; lastDusk = dusk;
    if(ao){ao.configuration.aoRadius=dusk?3:2;ao.configuration.intensity=dusk?1.4:0.65;}
  } });
}
