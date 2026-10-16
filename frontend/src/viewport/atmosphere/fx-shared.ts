import * as T from 'three';

/** Keep numeric values separate from texture/vector uniform shapes. One shared
 * instance supplies simulation time to every effect. Three only needs `value`. */
export class ScalarUniform {
  declare value: number;
  // Establish a fractional numeric field before assigning the actual uniform value.
  constructor(value = 0) { this.value = 0.1; this.value = value; }
}
export function scalarUniforms(material: T.ShaderMaterial): T.ShaderMaterial {
  for (const key in material.uniforms) {
    const value = material.uniforms[key].value;
    if (typeof value === 'number') material.uniforms[key] = new ScalarUniform(value);
  }
  return material;
}

/** Deterministic 0..1 hash — every particle is a closed-form function of (seed, index), never accumulated. */
export function fxHash(seed: number, index: number): number {
  let value = Math.imul(seed ^ index, 2654435761);
  value ^= value >>> 15; value = Math.imul(value, 2246822519);
  value ^= value >>> 13;
  return (value >>> 0) / 4294967296;
}

export const fxSeed = (id: string) => {
  let hash = 2166136261;
  for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return hash >>> 0;
};

/**
 * The shared 2×2 smoke atlas. Loaded once per page from the public asset with
 * the same base-URL handling as the other public assets; on failure the caller
 * falls back to a procedural radial puff so the look survives without the file.
 */
let atlas: Promise<T.Texture | undefined> | undefined;
export function smokeAtlas(): Promise<T.Texture | undefined> {
  atlas ??= new Promise(resolve => {
    new T.TextureLoader().load(`${import.meta.env.BASE_URL}textures/fx/smoke-atlas.png`,
      (texture: T.Texture) => { texture.colorSpace = T.SRGBColorSpace; resolve(texture); },
      undefined, () => resolve(undefined));
  });
  return atlas;
}

/** Procedural stand-in with the same 2×2 cell layout as the authored atlas. */
export function proceduralSmokeAtlas(): T.Texture {
  const size = 512, cell = size / 2;
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
  const context = canvas.getContext('2d')!;
  for (let row = 0; row < 2; row++) for (let column = 0; column < 2; column++) {
    const center = cell / 2, gradient = context.createRadialGradient(column * cell + center, row * cell + center, cell * 0.06, column * cell + center, row * cell + center, cell * 0.48);
    gradient.addColorStop(0, 'rgba(255,255,255,0.95)'); gradient.addColorStop(0.6, 'rgba(255,255,255,0.4)'); gradient.addColorStop(1, 'rgba(255,255,255,0)');
    context.fillStyle = gradient; context.fillRect(column * cell, row * cell, cell, cell);
  }
  const texture = new T.CanvasTexture(canvas); texture.colorSpace = T.SRGBColorSpace;
  return texture;
}
