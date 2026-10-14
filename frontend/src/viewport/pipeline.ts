import * as T from 'three';
import { BloomEffect, EffectComposer, EffectPass, OutlineEffect, RenderPass, SMAAEffect, SMAAPreset, ToneMappingEffect, ToneMappingMode } from 'postprocessing';
import { N8AOPostPass } from '../scene/vendor/N8AO';
export type Quality = 'low' | 'med' | 'high';
// Software rasterizers need a smaller drawing buffer, even on a DPR=1 screen.
// DOM labels and controls remain at native resolution in every preset.
export const PRESETS = { low: { ratio: 0.5, shadow: 512 }, med: { ratio: 1.25, shadow: 2048 }, high: { ratio: 1.5, shadow: 4096 } };
export function pipeline(renderer: T.WebGLRenderer, scene: T.Scene, camera: T.PerspectiveCamera, quality: Quality) {
  const composer = new EffectComposer(renderer, { frameBufferType: T.HalfFloatType });
  composer.addPass(new RenderPass(scene, camera));
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
  // Weather/fire FX (viewport/atmosphere) glow by colour alone and must never
  // enter this pass: their materials keep alpha/emissive below the threshold and
  // their layers stay out of any outline selection.
  // A zero-intensity bloom still executes its luminance and mipmap passes. Low
  // quality keeps selection and tone mapping, without bloom/blur/SMAA buffers.
  composer.addPass(quality === 'low' ? new EffectPass(camera, outline, tone) : new EffectPass(camera, outline,
    new BloomEffect({ intensity: 0.09, luminanceThreshold: 1.3, mipmapBlur: true }), tone,
    new SMAAEffect({ preset: quality === 'high' ? SMAAPreset.HIGH : SMAAPreset.MEDIUM })));
  return Object.assign(composer, { outline, setDusk: (dusk: boolean) => {
    if(ao){ao.configuration.aoRadius=dusk?3:2;ao.configuration.intensity=dusk?1.4:0.65;}
  } });
}
