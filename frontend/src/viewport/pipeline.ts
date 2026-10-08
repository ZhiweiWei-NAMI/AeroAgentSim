import * as T from 'three';
import { BloomEffect, EffectComposer, EffectPass, NormalPass, RenderPass, SMAAEffect, SMAAPreset, SSAOEffect, ToneMappingEffect, ToneMappingMode } from 'postprocessing';

export type Quality = 'low' | 'med' | 'high';
export const PRESETS = { low: { ratio: 1, shadow: 512 }, med: { ratio: 1.5, shadow: 1024 }, high: { ratio: 2, shadow: 2048 } };

export function pipeline(renderer: T.WebGLRenderer, scene: T.Scene, camera: T.PerspectiveCamera, quality: Quality) {
  const composer = new EffectComposer(renderer, { frameBufferType: T.HalfFloatType });
  composer.addPass(new RenderPass(scene, camera));
  if (quality !== 'low') {
    const normals = new NormalPass(scene, camera);
    composer.addPass(normals);
    const ao = new SSAOEffect(camera, normals.texture, { samples: quality === 'high' ? 24 : 12, rings: 4, radius: 4, intensity: 1.1, resolutionScale: 0.5 });
    composer.addPass(new EffectPass(camera, ao));
  }
  composer.addPass(new EffectPass(camera,
    new BloomEffect({ intensity: quality === 'low' ? 0 : 0.12, luminanceThreshold: 1, mipmapBlur: true }),
    new ToneMappingEffect({ mode: ToneMappingMode.ACES_FILMIC }),
    new SMAAEffect({ preset: quality === 'high' ? SMAAPreset.HIGH : SMAAPreset.LOW })));
  return composer;
}
