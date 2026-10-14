import * as T from 'three';
import { fxHash, ScalarUniform, scalarUniforms } from './fx-shared';
import type { Quality } from '../pipeline';

/**
 * Wind streaks: thin translucent lines drifting along the wind, camera-local, closed-form
 * in simulation time. Visual only — flight and sensing don't use them.
 */

const MAX_STREAKS = 160, LOW_STREAKS = 40, ALT_MIN = 20, ALT_MAX = 80, SPAN = 300, MIN_SPEED = 1.5;

/** Count ∝ speed: 0 below 1.5 m/s, at most 160 (spec §3.3). */
export function streakCount(speed: number, quality: Quality): number {
  if (speed < MIN_SPEED) return 0;
  const full = Math.min(MAX_STREAKS, Math.round(speed / 12 * MAX_STREAKS));
  return quality === 'low' ? Math.min(LOW_STREAKS, full) : full;
}

export class WindStreaks {
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
    const seeds = new T.InstancedBufferAttribute(new Float32Array(MAX_STREAKS * 2), 2);
    for (let i = 0; i < MAX_STREAKS; i++) seeds.setXY(i, fxHash(104729, i * 2 + 1), fxHash(104729, i * 2 + 2));
    this.geometry.setAttribute('streakSeed', seeds);
    this.geometry.instanceCount = 0;
    this.material = new T.ShaderMaterial({
      transparent: true, depthWrite: false,
      uniforms: { uTime: { value: 0 }, uWind: { value: new T.Vector2() }, uCenter: { value: new T.Vector3() }, uSpeed: { value: 0 } },
      vertexShader: `attribute vec2 streakSeed;uniform float uTime;uniform vec2 uWind;uniform vec3 uCenter;uniform float uSpeed;
        varying float streakFade;
        void main(){
          // Advection × 1.0 along the wind; the length scales with speed; streaks wrap in the camera volume.
          vec2 direction=uSpeed>0.0?uWind/uSpeed:vec2(0.0);
          float wrap=${SPAN}.0;
          vec2 offsetXZ=vec2(streakSeed.x*wrap,fract(streakSeed.y*3.0)*wrap)-wrap*0.5;
          offsetXZ+=direction*uTime*uSpeed;
          offsetXZ=mod(offsetXZ+wrap*0.5,wrap)-wrap*0.5;
          float altitude=${ALT_MIN}.0+fract(streakSeed.x*13.7)*(${ALT_MAX}.0-${ALT_MIN}.0);
          float streakLength=0.6+uSpeed*0.25;
          vec3 along=vec3(direction.x,0.0,direction.y);
          vec4 view=modelViewMatrix*vec4(uCenter+vec3(offsetXZ.x,altitude,offsetXZ.y)+along*position.y*streakLength,1.0);
          view.x+=position.x*0.06;
          gl_Position=projectionMatrix*view;
          streakFade=(1.0-distance(offsetXZ,vec2(0.0))/(${SPAN}.0*0.5))*(0.4+0.6*fract(streakSeed.y*7.3));
        }`,
      fragmentShader: `varying float streakFade;
        void main(){float alpha=0.16*streakFade;if(alpha<=0.004)discard;
        gl_FragColor=vec4(0.910,0.953,1.0,alpha);}`,
    });
    scalarUniforms(this.material);
    this.mesh = new T.Mesh(this.geometry, this.material);
    this.mesh.frustumCulled = false; this.mesh.renderOrder = 2; this.mesh.visible = false;
  }
  setQuality(quality: Quality) { this.quality = quality; }
  update(wind: { east: number; north: number; speed: number } | null, simTime: number | ScalarUniform, camera: T.Camera) {
    const speed = wind?.speed ?? 0;
    const count = streakCount(speed, this.quality);
    this.geometry.instanceCount = count;
    this.mesh.visible = count > 0;
    if (!count || !wind) return;
    if (typeof simTime === 'number') this.material.uniforms.uTime.value = simTime; else this.material.uniforms.uTime = simTime;
    this.material.uniforms.uWind.value.set(wind.east, -wind.north);
    this.material.uniforms.uSpeed.value = speed;
    this.material.uniforms.uCenter.value.copy(camera.position);
    this.material.uniforms.uCenter.value.y = 0;
  }
  dispose() {
    this.geometry.dispose(); this.material.dispose(); this.mesh.removeFromParent();
  }
}
