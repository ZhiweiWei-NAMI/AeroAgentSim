import * as T from 'three';
import { fxHash } from './fx-shared';
import type { Quality } from '../pipeline';

/**
 * Camera-local rain streak volume, closed-form in simulation time.
 * Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it.
 */

const RATE_HIGH_MM_H = 2.5, RATE_HEAVY_MM_H = 7.6;
const COUNT_LIGHT = 1500, COUNT_MODERATE = 4000, COUNT_HEAVY = 8000;
const FALL_MPS = 9, RADIUS = 120, HEIGHT = 90;

/** Light <2.5, moderate <7.6, heavy from there; low quality keeps a third (spec §3.4). */
export function rainCount(rateMmH: number, quality: Quality): number {
  const base = rateMmH < RATE_HIGH_MM_H ? COUNT_LIGHT : rateMmH < RATE_HEAVY_MM_H ? COUNT_MODERATE : COUNT_HEAVY;
  return Math.round(quality === 'low' ? base / 3 : base);
}

/** One instanced line-quad mesh; count and uniforms change, geometry never reallocates. */
export class Rain {
  readonly mesh: T.Mesh;
  private geometry: T.InstancedBufferGeometry;
  private material: T.ShaderMaterial;
  private quality: Quality;
  constructor(quality: Quality) {
    this.quality = quality;
    const quad = new T.PlaneGeometry(1, 1);
    this.geometry = new T.InstancedBufferGeometry();
    this.geometry.index = quad.index;
    this.geometry.attributes.position = quad.attributes.position;
    this.geometry.attributes.uv = quad.attributes.uv;
    const seeds = new T.InstancedBufferAttribute(new Float32Array(COUNT_HEAVY * 2), 2);
    for (let i = 0; i < COUNT_HEAVY; i++) seeds.setXY(i, fxHash(7919, i * 2 + 1), fxHash(7919, i * 2 + 2));
    this.geometry.setAttribute('dropSeed', seeds);
    this.geometry.instanceCount = 0;
    this.material = new T.ShaderMaterial({
      // Falling quads reverse the plane's winding in camera space; keep one visible pass.
      transparent: true, depthWrite: false, side: T.DoubleSide, forceSinglePass: true,
      uniforms: { uTime: { value: 0 }, uSlant: { value: new T.Vector2() }, uOpacity: { value: 0.28 }, uCenter: { value: new T.Vector3() } },
      vertexShader: `attribute vec2 dropSeed;uniform float uTime;uniform vec2 uSlant;uniform vec3 uCenter;
        varying float dropFade;
        void main(){
          // Closed-form fall: each seed is a fixed phase in a 90 m column, reset when it passes the ground.
          float span=${(RADIUS * 2).toFixed(1)};
          float column=dropSeed.x*span-${RADIUS}.0;
          float height=mod(dropSeed.y*${HEIGHT}.0-uTime*${FALL_MPS}.0,${HEIGHT}.0);
          float depth=fract(dropSeed.x*7.31)*span-${RADIUS}.0;
          vec2 drift=uSlant*(${HEIGHT}.0-height)*0.5;
          vec3 world=uCenter+vec3(column+drift.x,height,depth+drift.y);
          float dropLength=1.2+length(uSlant)*2.0;
          vec3 fall=normalize(vec3(uSlant.x*0.5,-1.0,uSlant.y*0.5));
          vec4 view=modelViewMatrix*vec4(world+fall*position.y*dropLength,1.0);
          view.x+=position.x*0.02;
          gl_Position=projectionMatrix*view;
          dropFade=1.0-length(vec2(column,depth))/${RADIUS}.0;
        }`,
      fragmentShader: `uniform float uOpacity;varying float dropFade;
        void main(){float alpha=uOpacity*dropFade;if(alpha<=0.004)discard;
        gl_FragColor=vec4(0.788,0.839,0.890,alpha);}`,
    });
    this.mesh = new T.Mesh(this.geometry, this.material);
    this.mesh.frustumCulled = false; this.mesh.renderOrder = 3; this.mesh.visible = false;
  }
  setQuality(quality: Quality) { this.quality = quality; }
  update(rateMmH: number, simSeconds: number, camera: T.Camera, wind?: { east: number; north: number }) {
    const count = rateMmH > 0 ? rainCount(rateMmH, this.quality) : 0;
    const speed = wind ? Math.hypot(wind.east, wind.north) : 0;
    this.geometry.instanceCount = count;
    this.mesh.visible = count > 0;
    if (!count) return;
    this.material.uniforms.uTime.value = simSeconds;
    // Rain slants with the same wind vector the smoke and streaks use (scaled for a 120 m column).
    const lean = wind && speed > 0 ? Math.min(2, speed * 0.12) : 0;
    this.material.uniforms.uSlant.value.set(wind ? wind.east / (speed || 1) * lean : 0, wind ? -wind.north / (speed || 1) * lean : 0);
    this.material.uniforms.uCenter.value.copy(camera.position);
    this.material.uniforms.uCenter.value.y = 0;
    // Streaks read but stay subtle: 0.22 light, 0.30 moderate, 0.38 heavy.
    this.material.uniforms.uOpacity.value = rateMmH < RATE_HIGH_MM_H ? 0.22 : rateMmH < RATE_HEAVY_MM_H ? 0.3 : 0.38;
  }
  dispose() {
    this.geometry.dispose(); this.material.dispose(); this.mesh.removeFromParent();
  }
}
