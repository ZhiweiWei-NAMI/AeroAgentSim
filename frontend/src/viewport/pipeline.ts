import * as T from 'three';
import { BloomEffect, EffectComposer, EffectPass, OutlineEffect, RenderPass, SMAAEffect, SMAAPreset, ToneMappingEffect, ToneMappingMode } from 'postprocessing';
import { N8AOPostPass } from '../scene/vendor/N8AO';
export type Quality = 'low' | 'med' | 'high';
export const PRESETS = { low: { ratio: 1, shadow: 512 }, med: { ratio: 1.25, shadow: 2048 }, high: { ratio: 1.5, shadow: 4096 } };
export function pipeline(renderer: T.WebGLRenderer, scene: T.Scene, camera: T.PerspectiveCamera, quality: Quality) {
  const composer = new EffectComposer(renderer, { frameBufferType: T.HalfFloatType });
  composer.addPass(new RenderPass(scene, camera));
  if (quality !== 'low') {
    const ao = new N8AOPostPass(scene, camera, 1, 1);
    ao.setQualityMode(quality === 'high' ? 'High' : 'Low');
    ao.configuration.aoRadius = 3; ao.configuration.intensity = 1.4;
    ao.configuration.distanceFalloff = 1; ao.configuration.halfRes = true; ao.configuration.gammaCorrection = false;
    composer.addPass(ao);
  }
  const outline = new OutlineEffect(scene, camera, { edgeStrength: 2, visibleEdgeColor: 0x9ce9ff, hiddenEdgeColor: 0x436172, blur: true, xRay: false });
  composer.addPass(new EffectPass(camera, outline,
    new BloomEffect({ intensity: quality === 'low' ? 0 : 0.09, luminanceThreshold: 1.3, mipmapBlur: true }),
    new ToneMappingEffect({ mode: ToneMappingMode.ACES_FILMIC }),
    new SMAAEffect({ preset: quality === 'high' ? SMAAPreset.HIGH : SMAAPreset.MEDIUM })));
  return Object.assign(composer, { outline });
}
