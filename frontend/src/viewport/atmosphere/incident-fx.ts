import * as T from 'three';
import { incidentLabel, type Conditions } from '../conditions';
import { fxHash, fxSeed, smokeAtlas, proceduralSmokeAtlas, scalarUniforms, ScalarUniform } from './fx-shared';
import type { Quality } from '../pipeline';
import { SceneDepth } from './scene-depth';

/**
 * Fire-incident visuals: pin, affected-area decal, flames and smoke, all closed-form
 * in (age, seed) from (t − activation) — never accumulated per frame. Smoke is not an
 * entity and nothing simulates combustion or spread.
 * Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it.
 */

const FLAME_MIN = 5, FLAME_MAX = 7;
// A soft flame mask needs this projected height to leave a glint outside the 26 px pin,
// including the half-resolution low-quality drawing buffer. Close views keep their world size.
const FLAME_MIN_CSS_PX = 40;
// Connected plume: 2.0 puffs/s at high quality (limit 56, life 24 s).
// Low quality is art-directed for the weak GPUs and darker pipeline it selects: 1.5 puffs/s
// over a 12 s life, so its 18 puffs all stay alive and the plume never gaps.
const SMOKE_RATE = 2.0, SMOKE_RATE_LOW = 1.5, SMOKE_LIFE_HIGH_S = 24, SMOKE_LIFE_LOW_S = 12, SMOKE_HIGH = 56, SMOKE_LOW = 18;
// Low-tier growth: 6 m at the root, 16 m at 3 s, 40 m at the end of its 12 s life.
const SMOKE_SIZE_LOW = 6, SMOKE_SIZE_LOW_3S = 16, SMOKE_SIZE_LOW_END = 40, SMOKE_GROW_ROOT_S = 3;
// Low-tier opacity: a 0.82 peak held to 60 % of life, then a linear fade to the cutoff.
const SMOKE_ALPHA_LOW = 0.82, SMOKE_ALPHA_LOW_HOLD = 0.6;
const AREA = new T.Color('#ff6a3d'), ENDED = new T.Color('#8b95a1');
// Smoke absorbs: a charcoal body stays clearly below the luminance of a hazy, fog-lit city.
const SMOKE_BASE = new T.Color('#1c1a19'), SMOKE_MID = new T.Color('#2f2c2a'), SMOKE_HIGH_TONE = new T.Color('#4a4643');

type Row = Conditions['incidents'][number] & { seed: number };
const PERSON = new T.Color('#d55e00');
export interface AtmosphereOptions {
  annotations: boolean;
  /** Fired when async resources (the smoke atlas) finish loading; the idle renderer invalidates. */
  onReady?: () => void;
  /** The run uses EntityLayer's labels; the draft has a small overlay of its own. */
  preview?: { overlay: HTMLElement; locale: () => string };
}

interface Visual {
  id: string; unit: T.Group; flames: T.InstancedMesh<T.PlaneGeometry, T.ShaderMaterial>;
  smoke: T.InstancedMesh<T.InstancedBufferGeometry, T.Material>; decal: T.Mesh<T.BufferGeometry, T.ShaderMaterial>; flameCount: number; puffSeeds: Float64Array; flameSeeds: Float64Array;
  seen: number; time: number; roof: number; surfaceY: number; smokeBase: number; surface: number; roofHeight?: (east: number, north: number) => number; radius: number; since: number; age: number; kind: string; phase: string;
  tint: T.InstancedBufferAttribute; cells: T.InstancedBufferAttribute; light?: T.PointLight; active: boolean; glowRamp: number; flameTop: number;
  annotation?: { ring: T.Mesh<T.BufferGeometry, T.ShaderMaterial>; pin: T.Sprite; leader: T.Line<T.BufferGeometry, T.LineBasicMaterial>; label?: HTMLElement };
}

// Chunks must start on their own lines for Three's shader preprocessor.
const FOG_PARS_V = '\n#include <fog_pars_vertex>\n', FOG_V = '\n#include <fog_vertex>\n',
  FOG_PARS_F = '\n#include <fog_pars_fragment>\n', FOG_F = '\n#include <fog_fragment>\n';
// The run composer handles display conversion; direct previews/captures need these chunks.
const OUTPUT_F = '\n#include <tonemapping_fragment>\n#include <colorspace_fragment>\n';

// Emissive flames lose intensity through fog, while retaining their orange hue.
// Adding fog colour to an additive core would brighten the fog itself.
const FOG_VISIBILITY = `float visibility=1.0;
  #ifdef USE_FOG
    #ifdef FOG_EXP2
      visibility=exp(-fogDensity*fogDensity*vFogDepth*vFogDepth);
    #else
      visibility=1.0-smoothstep(fogNear,fogFar,vFogDepth);
    #endif
  #endif
`;

/**
 * Body instances followed by core instances in ONE draw. Premultiplied body alpha
 * attenuates the destination; additive cores write zero alpha and retain their RGB.
 * This preserves the original two-pass blend order and both independent masks.
 * Colours: #ffd27a only in the lowest 20 %, body #ff8a2b, tips #e2461b into a hint of
 * dark smoke at the very top.
 */
const flameMaterial = () => scalarUniforms(new T.ShaderMaterial({
  transparent: true, depthWrite: false, premultipliedAlpha: true, blending: T.NormalBlending, fog: true, side: T.DoubleSide, forceSinglePass: true,
  uniforms: T.UniformsUtils.merge([T.UniformsLib.fog, { uTime: { value: 0 }, uRamp: { value: 1 }, uFlameCount: { value: 0 } }]),
  vertexShader: `uniform float uFlameCount;varying float flameCore;varying vec2 flameUv;${FOG_PARS_V}
    void main(){flameUv=uv;flameCore=step(uFlameCount,float(gl_InstanceID));
    vec4 mvPosition=modelViewMatrix*instanceMatrix*vec4(position,1.0);
    gl_Position=projectionMatrix*mvPosition;${FOG_V}}`,
  // Two octaves of scrolling value noise times a teardrop mask; colour core → tip by height.
  fragmentShader: `uniform float uTime;uniform float uRamp;varying float flameCore;varying vec2 flameUv;${FOG_PARS_F}
    float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);
    float a=fract(sin(dot(i,vec2(127.1,311.7)))*43758.5453),b=fract(sin(dot(i+vec2(1,0),vec2(127.1,311.7)))*43758.5453),
    c=fract(sin(dot(i+vec2(0,1),vec2(127.1,311.7)))*43758.5453),d=fract(sin(dot(i+vec2(1,1),vec2(127.1,311.7)))*43758.5453);
    return mix(mix(a,b,f.x),mix(c,d,f.x),f.y);}
    void main(){vec2 uv=flameUv;float t=uTime;
    float n=noise(uv*3.0+vec2(t*0.9,-t*1.6))*0.6+noise(uv*7.0-vec2(t*1.4,t*2.2))*0.4;
    float teardrop=1.0-smoothstep(0.0,max(0.001,0.9*pow(max(0.0,1.0-uv.y),0.7)),abs(uv.x-0.5)*2.0);
    float mask=(1.0-smoothstep(0.25,1.0,uv.y))*teardrop*(0.55+0.45*n);
    float core=teardrop*(1.0-smoothstep(0.0,1.0,uv.y))*(1.0-smoothstep(0.08,0.45,abs(uv.x-0.5)*2.0))*(0.5+0.5*n);
    ${FOG_VISIBILITY}
    float alpha=(flameCore<0.5?min(0.85,mask*1.15):min(0.6,core*0.6))*uRamp*visibility;
    if(alpha<=0.01)discard;
    // #ffd27a → #ff8a2b → #e2461b → hint of #3b3632 at the very top.
    // Palette in the renderer's linear colour space.
    vec3 color=mix(mix(vec3(1.0,0.644,0.195),vec3(1.0,0.254,0.024),clamp(uv.y*5.0,0.0,1.0)),
      vec3(0.761,0.061,0.011),smoothstep(0.45,0.95,uv.y));
    color=mix(color,vec3(0.044,0.037,0.032),smoothstep(0.9,1.0,uv.y)*0.35);
    if(flameCore>0.5)color=mix(color,vec3(1.0,0.644,0.195),0.5);
    gl_FragColor=vec4(color,alpha);${OUTPUT_F}
    gl_FragColor.rgb*=alpha;if(flameCore>0.5)gl_FragColor.a=0.0;}`,
}));

// Scattered ambient/sky light: smoke reads LIGHTER than the dark dusk ground and darker than
// the light day ground. Rain greys both toward the same wet tone (0.3·r).
const SMOKE_LIGHT_DAY = new T.Color('#3a3733'), SMOKE_LIGHT_DUSK = new T.Color('#6a625c'), SMOKE_LIGHT_RAIN = new T.Color('#34373b');
// The low-quality pipeline renders a darker scene, so its smoke light is lifted 15 % to separate.
const SMOKE_LIGHT_LOW_GAIN = 1.15;
const GLOW_ORANGE = new T.Color('#ff8a2b');

const smokeMaterial = () => scalarUniforms(new T.ShaderMaterial({
  transparent: true, depthWrite: false, depthTest: true, fog: true,
  uniforms: { ...T.UniformsUtils.clone(T.UniformsLib.fog), uMap: { value: null as unknown as T.Texture },
    uSmokeLight: { value: new T.Color('#5d5a63') }, uGlow: { value: GLOW_ORANGE.clone() },
    uGlowStrength: { value: 0.55 }, uScatter: { value: 0.55 }, uFlameTop: { value: 10 } },
  vertexShader: `attribute vec4 puffTint;attribute float puffCell;varying vec2 puffUv;varying vec4 puffColor;varying float puffHeight;${FOG_PARS_V}
    void main(){puffUv=uv*0.5+vec2(mod(puffCell,2.0),floor(puffCell/2.0))*0.5;puffColor=puffTint;
    puffHeight=instanceMatrix[3].y;
    vec4 mvPosition=modelViewMatrix*instanceMatrix*vec4(position,1.0);
    gl_Position=projectionMatrix*mvPosition;${FOG_V}}`,
  // Smoke is art-directed, not shaded by the rig: the plume scatters the ambient city/sky
  // light (mix with uSmokeLight) and the fire underlights the root while the flames burn.
  fragmentShader: `uniform sampler2D uMap;uniform vec3 uSmokeLight;uniform vec3 uGlow;uniform float uGlowStrength;uniform float uScatter;uniform float uFlameTop;varying vec2 puffUv;varying vec4 puffColor;varying float puffHeight;${FOG_PARS_F}
    void main(){vec4 puff=texture2D(uMap,puffUv);
    // The sparse atlas needs its softened density at small projected sizes. Keep the
    // close-view alpha, and restore the former sqrt density below 32–8 raster pixels.
    float footprint=max(fwidth(puffUv.x),fwidth(puffUv.y));
    float coverage=smoothstep(0.5/32.0,0.5/8.0,footprint);
    // Density curve: the soft atlas averages ~0.3 alpha, so puff cores are pushed toward solid.
    float density=mix(puff.a,sqrt(puff.a),coverage);
    float alpha=smoothstep(0.0,0.55,density)*puffColor.a;
    if(alpha<=0.002)discard;
    vec3 base=puffColor.rgb;
    vec3 lit=mix(base,uSmokeLight,uScatter);
    // The fire underlights only the root (6 m falloff); a low, wind-bent plume must not turn orange.
    float under=uGlowStrength*exp(-max(puffHeight-uFlameTop,0.0)/5.0);
    vec3 color=lit+uGlow*under*0.35;
    gl_FragColor=vec4(color,alpha);${OUTPUT_F}
    ${FOG_VISIBILITY}
    #ifdef USE_FOG
      // Same fog response as the city (no sqrt), so smoke and buildings agree at distance.
      gl_FragColor.rgb=mix(gl_FragColor.rgb,fogColor,1.0-visibility);
    #endif
    }`,
}));


/** Fragment-shader width for the ring line: ~0.8 m at close range (0.4/R in d-units), a crisp
 *  ~1.5 px line far away (0.75·fwidth) (finding 7). */
const ringWidth = 'max(0.4/uRadius,0.75*fwidth(d))';

/** Ring/fill, faint occluded boundary and additive roof glow in one draw.
 * The shared depth snapshot replaces the former LessEqual/Greater pair. */
const ringMaterial = () => scalarUniforms(new T.ShaderMaterial({
  transparent: true, depthWrite: false, depthTest: false, premultipliedAlpha: true, fog: true,
  uniforms: T.UniformsUtils.merge([T.UniformsLib.fog, { uColor: { value: AREA.clone() }, uOpacity: { value: 0.85 },
    uRadius: { value: 30 }, uExtent: { value: 34.5 }, uFill: { value: 1 }, uReveal: { value: 1 },
    uOccluded: { value: 0.22 }, uRing: { value: 1 }, uGround: { value: 0 }, uRoof: { value: 0 },
    uGlowRadius: { value: 0 }, uTime: { value: 0 }, uRamp: { value: 0 },
    uDepth: { value: null }, uTexel: { value: new T.Vector2() } }]),
  vertexShader: `attribute float decalPart;varying float glowPart;varying vec2 ringUv;${FOG_PARS_V}
    uniform float uExtent;uniform float uGround;uniform float uRoof;uniform float uGlowRadius;
    void main(){ringUv=uv;glowPart=decalPart;
    vec3 p=position;p.xz*=decalPart<0.5?uExtent:uGlowRadius;p.y+=decalPart<0.5?uGround:uRoof;
    vec4 mvPosition=modelViewMatrix*vec4(p,1.0);gl_Position=projectionMatrix*mvPosition;${FOG_V}}`,
  fragmentShader: `uniform vec3 uColor;uniform float uOpacity;uniform float uRadius;uniform float uExtent;
    uniform float uFill;uniform float uReveal;uniform float uOccluded;uniform float uRing;
    uniform float uTime;uniform float uRamp;uniform sampler2D uDepth;uniform vec2 uTexel;
    varying float glowPart;varying vec2 ringUv;${FOG_PARS_F}
    float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
    void main(){
      // Match the polygon-offset ground decal, without lifting it onto roofs.
      bool occluded=false;
      if(uRing>0.5){
        float offset=2.0*max(abs(dFdx(gl_FragCoord.z)),abs(dFdy(gl_FragCoord.z)));
        occluded=gl_FragCoord.z-offset>texture2D(uDepth,gl_FragCoord.xy*uTexel).r;
      }
      if(glowPart>0.5){
        if(occluded)discard;
        float d=length(ringUv-0.5)*2.0;
        float flicker=0.75+0.25*sin(uTime*7.3+hash(vec2(3.0,7.0)))*sin(uTime*11.1+hash(vec2(5.0,1.0)));
        float alpha=(1.0-smoothstep(0.0,1.0,d))*0.25*flicker*uRamp;
        if(alpha<=0.004)discard;
        gl_FragColor=vec4(1.0,0.541,0.169,alpha);${OUTPUT_F}
        gl_FragColor.rgb*=alpha;gl_FragColor.a=0.0;
      }else{
        if(uRing<0.5)discard;
        float d=length(ringUv-0.5)*2.0*uExtent/uRadius;
        float angle=fract(atan(0.5-ringUv.y,ringUv.x-0.5)/6.28318530718+1.0);
        float width=${ringWidth};
        float alpha=occluded?0.0:max(0.0,1.0-d)*0.10*uFill;
        if(abs(d-1.0)<width&&fract(angle*16.0)<=0.6&&angle<uReveal)alpha=occluded?uOccluded:uOpacity;
        if(alpha<=0.002)discard;
        gl_FragColor=vec4(uColor,alpha);${OUTPUT_F}${FOG_F}
        gl_FragColor.rgb*=alpha;
      }
    }`,
}));

/** Both horizontal quads share vertices/indices across all incidents. */
function decalGeometry(): T.BufferGeometry {
  const geometry = new T.PlaneGeometry(2, 2).rotateX(-Math.PI / 2), result = new T.BufferGeometry();
  for (const name of ['position', 'uv']) {
    const attribute = geometry.getAttribute(name), array = new Float32Array(attribute.array.length * 2);
    array.set(attribute.array); array.set(attribute.array, attribute.array.length);
    result.setAttribute(name, new T.BufferAttribute(array, attribute.itemSize));
  }
  result.setAttribute('decalPart', new T.BufferAttribute(new Float32Array([0,0,0,0,1,1,1,1]), 1));
  result.setIndex([0,2,1,2,3,1,4,6,5,6,7,5]); geometry.dispose();
  return result;
}

/** Two-tone flame (or person) glyph on a disc with a 2 px white ring, drawn once to a 64 px canvas. */
function pinTexture(kind: 'fire' | 'person_fall', grey = false): T.Texture {
  const size = 64, canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
  const context = canvas.getContext('2d')!;
  // The flame glyph fills ~60 % of the disc.
  const glyph = size * 0.6;
  context.beginPath(); context.arc(size / 2, size / 2, size / 2 - 3, 0, Math.PI * 2);
  context.fillStyle = grey ? '#8b95a1' : kind === 'fire' ? '#e2461b' : '#d55e00'; context.fill();
  context.lineWidth = 3; context.strokeStyle = '#ffffff'; context.stroke();
  context.fillStyle = '#ffd27a';
  if (kind === 'fire') {
    const top = (size - glyph) / 2, bottom = top + glyph, half = glyph * 0.42;
    context.beginPath(); context.moveTo(size / 2, top);
    context.bezierCurveTo(size / 2 + half, top + glyph * 0.32, size / 2 + half * 0.8, bottom - glyph * 0.22, size / 2, bottom);
    context.bezierCurveTo(size / 2 - half * 0.8, bottom - glyph * 0.22, size / 2 - half, top + glyph * 0.32, size / 2, top); context.fill();
    context.fillStyle = '#fff1dc'; context.beginPath(); context.moveTo(size / 2, top + glyph * 0.42);
    context.bezierCurveTo(size / 2 + half * 0.4, top + glyph * 0.58, size / 2 + half * 0.32, bottom - glyph * 0.1, size / 2, bottom - glyph * 0.04);
    context.bezierCurveTo(size / 2 - half * 0.32, bottom - glyph * 0.1, size / 2 - half * 0.4, top + glyph * 0.58, size / 2, top + glyph * 0.42); context.fill();
  } else {
    context.beginPath(); context.arc(size / 2, size / 2 - 8, 8, 0, Math.PI * 2); context.fill();
    context.fillRect(size / 2 - 8, size / 2 - 1, 16, 18);
  }
  const texture = new T.CanvasTexture(canvas); texture.colorSpace = T.SRGBColorSpace;
  return texture;
}

let pinTextures: Partial<Record<'fire' | 'person_fall' | 'fire-ended' | 'person_fall-ended', T.Texture>> | undefined;
let pinUsers = 0;

const QUARTER_TURN = new T.Euler();
const ZERO = new T.Vector3();

export class IncidentFX {
  readonly root = new T.Group();
  readonly ready: Promise<void>;
  private quality: Quality;
  private visuals = new Map<string, Visual>();
  private visualList: Visual[] = [];
  private surfaceRevision = 0;
  invalidateSurface() { this.surfaceRevision++; }
  private flames = flameMaterial();
  private smoke?: T.ShaderMaterial;
  private smokeLightScratch = new T.Color();
  private plane = new T.PlaneGeometry(1, 1);
  private ringGeometry = decalGeometry();
  private depth = new SceneDepth();
  private leaderGeometry = new T.BufferGeometry().setFromPoints([new T.Vector3(), new T.Vector3(0, 14, 0)]);
  private matrix = new T.Matrix4();
  private quaternion = new T.Quaternion();
  private scale = new T.Vector3();
  private position = new T.Vector3();
  private color = new T.Color();
  private vertical = new T.Vector3(0, 1, 0);
  // Flame orientation blend: cylindrical → camera-facing as the camera pitches down.
  private flameFace = 0;
  private cameraYaw = 0;
  private cameraForward = new T.Vector3();
  private flameSpherical = new T.Quaternion();
  private spin = new T.Quaternion();
  structureRevision = 0;
  private frame = 0;
  private lastCameraPosition = new T.Vector3(Infinity, Infinity, Infinity);
  private lastCameraRotation = new T.Quaternion();
  private lastProjection = new T.Matrix4();
  private lastWind = new T.Vector2(Infinity, Infinity);
  private lastPinScale = NaN;
  private lastQuality?: Quality;
  private reducedMotion?: MediaQueryList;
  private pinScale = 26 / 900;
  private disposed = false;
  constructor(quality: Quality, private options: AtmosphereOptions = { annotations: true }) {
    this.quality = quality;
    if (options.annotations) pinUsers++;
    else this.ringGeometry.setDrawRange(6, 6); // sensor glow only; no annotation quad
    this.ready = smokeAtlas().then(texture => {
      if (this.disposed) return;
      this.smoke = smokeMaterial();
      this.smoke.uniforms.uMap.value = texture ?? proceduralSmokeAtlas();
      for (const visual of this.visuals.values()) { visual.smoke.material.dispose(); visual.smoke.material = this.smoke; visual.time = NaN; }
      // The atlas usually lands after the first frames; tell the owner's idle
      // renderer to redraw, or the plume stays invisible until the camera moves.
      options.onReady?.();
    });
  }
  /** NDC pin height; update cancels the camera's vertical projection scale as well as distance. */
  setPinScale(heightCssPx: number, _pixelRatio?: number) { this.pinScale = 26 / Math.max(1, heightCssPx) * 2; }
  setQuality(quality: Quality) {
    this.quality = quality;
    // Low quality drops the PointLight (spec §3.7).
    for (const visual of this.visuals.values()) if (visual.light) visual.light.visible = false;
  }
  private create(row: Row): Visual {
    const unit = new T.Group(); unit.name = `fx-incident-${row.id}`;
    const flameCount = Math.round(FLAME_MIN + fxHash(row.seed, 1) * (FLAME_MAX - FLAME_MIN));
    // Body and smaller additive core keep their original instance order in one mesh.
    const flames = new T.InstancedMesh(this.plane, scalarUniforms(this.flames.clone()), flameCount * 2);
    flames.material.uniforms.uFlameCount.value = flameCount;
    flames.count = flameCount * 2; flames.frustumCulled = false; flames.renderOrder = 4;
    // Per-fire puff storage: instance attributes ride a private geometry clone.
    const capacity = SMOKE_HIGH;
    const geometry = new T.InstancedBufferGeometry();
    geometry.index = this.plane.index; geometry.attributes.position = this.plane.attributes.position; geometry.attributes.uv = this.plane.attributes.uv;
    const tint = new T.InstancedBufferAttribute(new Float32Array(capacity * 4), 4);
    tint.setUsage(T.DynamicDrawUsage);
    geometry.setAttribute('puffTint', tint);
    const cells = new T.InstancedBufferAttribute(new Float32Array(capacity), 1);
    cells.setUsage(T.DynamicDrawUsage); geometry.setAttribute('puffCell', cells);
    const smoke = new T.InstancedMesh(geometry, this.smoke ?? new T.MeshBasicMaterial({ visible: false }), capacity);
    smoke.count = 0; smoke.frustumCulled = false; smoke.renderOrder = 2;
    const puffSeeds = new Float64Array(SMOKE_HIGH * 4), flameSeeds = new Float64Array(flameCount * 3);
    for (let i = 0; i < SMOKE_HIGH; i++) for (let j = 0; j < 4; j++) puffSeeds[i * 4 + j] = fxHash(row.seed, i * 8 + j + 1);
    for (let i = 0; i < flameCount; i++) for (let j = 0; j < 3; j++) flameSeeds[i * 3 + j] = fxHash(row.seed, i * 4 + j + 2);
    const decal = new T.Mesh(this.ringGeometry, ringMaterial()); decal.renderOrder = 3; decal.frustumCulled = false;
    if (this.options.annotations) decal.onBeforeRender = renderer => {
      decal.material.uniforms.uDepth.value = this.depth.copy(renderer);
      decal.material.uniforms.uTexel.value.copy(this.depth.texel);
    };
    else {
      // A sensor has no occluded ring. Its glow uses hardware depth, just like
      // the original decal, without a depth snapshot or framebuffer switch.
      decal.material.depthTest = true; decal.material.polygonOffset = true; decal.material.polygonOffsetFactor = -4;
    }
    unit.add(flames, smoke, decal);
    const visual: Visual = { id: row.id, unit, flames, flameCount, smoke, decal, tint, cells, puffSeeds, flameSeeds, active: false, glowRamp: 0, flameTop: 10, seen: 0, time: NaN, roof: NaN, surfaceY: NaN, smokeBase: NaN, surface: -1, radius: NaN, since: NaN, age: NaN, kind: row.kind, phase: row.phase };
    // The material/program is shared, but underlight belongs to this incident.
    // Upload these uniforms for every draw, including consecutive smoke meshes.
    smoke.onBeforeRender = () => {
      if (!this.smoke) return;
      this.smoke.uniforms.uGlowStrength.value = this.smokeGlow * visual.glowRamp;
      this.smoke.uniforms.uFlameTop.value = visual.flameTop;
      this.smoke.uniformsNeedUpdate = true;
    };
    if (this.options.annotations) {
      pinTextures ??= { fire: pinTexture('fire'), person_fall: pinTexture('person_fall'), 'fire-ended': pinTexture('fire', true), 'person_fall-ended': pinTexture('person_fall', true) };
      const ring = decal;
      const pin = new T.Sprite(new T.SpriteMaterial({ map: pinTextures[row.kind], sizeAttenuation: false, depthTest: false, transparent: true, fog: false }));
      pin.renderOrder = 12; pin.center.set(0.5, 0.5); pin.position.y = 14;
      const leader = new T.Line(this.leaderGeometry, new T.LineBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.6, depthTest: false, fog: false }));
      leader.renderOrder = 11; leader.frustumCulled = false;
      const label = this.options.preview ? document.createElement('span') : undefined;
      if (label) { label.className = 'entity-label'; label.dataset.entityId = row.id; this.options.preview!.overlay.append(label); }
      visual.annotation = { ring, pin, leader, label }; unit.add(pin, leader);
    }
    if (row.kind === 'fire') {
      visual.light = new T.PointLight('#ff8a2b', 2.5, row.radiusM * 3);
      visual.light.visible = false; unit.add(visual.light);
    }
    this.root.add(unit); this.structureRevision++;
    this.visuals.set(row.id, visual); this.visualList.push(visual);
    return visual;
  }
  update(rows: Conditions['incidents'], simTime: number | ScalarUniform, camera: T.Camera, ringStart: Map<string, number>, origin?: T.Vector3, roofHeight?: (east: number, north: number) => number) {
    if (this.disposed) return;
    const simSeconds = typeof simTime === 'number' ? simTime : simTime.value;
    if (!rows.length && !this.visuals.size) return;
    const frame = ++this.frame;
    const viewChanged = !this.lastCameraPosition.equals(camera.position) || !this.lastCameraRotation.equals(camera.quaternion)
      || !this.lastProjection.equals(camera.projectionMatrix) || this.lastPinScale !== this.pinScale
      || !this.lastWind.equals(this.wind) || this.lastQuality !== this.quality;
    this.lastCameraPosition.copy(camera.position); this.lastCameraRotation.copy(camera.quaternion);
    this.lastProjection.copy(camera.projectionMatrix); this.lastPinScale = this.pinScale;
    this.lastWind.copy(this.wind); this.lastQuality = this.quality;
    const reduced = (this.reducedMotion ??= matchMedia('(prefers-reduced-motion: reduce)')).matches;
    const pinScale = this.pinScale / camera.projectionMatrix.elements[5];
    camera.updateMatrixWorld();
    // Cylinder → sphere blend from the camera direction: below 45° pitch the flames stay
    // upright; from 45° to 80° below horizontal they progressively turn to face the camera.
    this.cameraForward.set(0, 0, -1).applyQuaternion(camera.quaternion);
    const pitchDeg = Math.asin(Math.min(1, Math.max(-1, -this.cameraForward.y))) * 180 / Math.PI;
    this.flameFace = T.MathUtils.clamp((pitchDeg - 45) / 35, 0, 1);
    // Azimuth that keeps an upright quad facing the camera horizontally.
    this.cameraYaw = Math.atan2(-this.cameraForward.x, -this.cameraForward.z);
    for (const row of rows) {
      // A run's pending incidents allocate nothing. Only the draft shows their grey pins.
      if (row.phase === 'pending' && !this.options.preview) continue;
      const visual = this.visuals.get(row.id) ?? this.create({ ...row, seed: fxSeed(row.id) });
      visual.seen = frame;
      visual.unit.visible = true;
      const pending = row.phase === 'pending';
      const ended = row.phase === 'ended';
      // Conditions already validated these ENU coordinates at the source cut.
      this.position.set(row.position[0], row.position[2], -row.position[1]).sub(origin ?? ZERO);
      // The unit sits at ground level — ring and fill are ground decals. Only the flames,
      // glow and smoke base ride the roof height.
      const moved = !visual.unit.position.equals(this.position);
      visual.unit.position.copy(this.position);
      const refreshRoof = moved || visual.surface !== this.surfaceRevision || visual.roofHeight !== roofHeight;
      const roofY = refreshRoof ? Math.max(roofHeight?.(row.position[0], row.position[1]) ?? -(origin?.y ?? 0), this.position.y)
        : visual.surfaceY;
      visual.surfaceY = roofY;
      visual.surface = this.surfaceRevision; visual.roofHeight = roofHeight;
      const baseY = roofY - visual.unit.position.y;
      const dirty = moved || viewChanged || visual.time !== simSeconds || visual.roof !== baseY || visual.radius !== row.radiusM
        || visual.since !== row.sinceS || visual.age !== row.activeS || visual.kind !== row.kind || visual.phase !== row.phase;
      visual.time = simSeconds; visual.roof = baseY; visual.radius = row.radiusM;
      visual.since = row.sinceS; visual.age = row.activeS; visual.kind = row.kind; visual.phase = row.phase;
      const baseHeight = T.MathUtils.clamp(8 * Math.sqrt(row.radiusM / 30), 5, 12);
      // The procedural mask only fills part of its quad. At overview distances its
      // former 2–4 raster pixels disappeared under the screen-space pin. Size the same
      // billboards from projected depth, retaining the physical sizes when they read.
      this.position.copy(visual.unit.position); this.position.y += baseY + baseHeight / 2;
      const depth = -this.position.applyMatrix4(camera.matrixWorldInverse).z;
      // Upright flame quads foreshorten as the operator pitches down; include that
      // projection so the top view keeps the same floor as the oblique overview.
      const verticalProjection = Math.max(0.25, Math.sqrt(camera.matrixWorld.elements[8] ** 2 + camera.matrixWorld.elements[10] ** 2));
      const displayScale = Math.max(1, depth * pinScale * FLAME_MIN_CSS_PX / 26 / baseHeight / verticalProjection);
      const flameTop = baseHeight * displayScale;
      // Ring draw-in: 0.6 s wall-clock, once per activation (spec §5). Grey tint after ending.
      const start = ringStart.get(row.id), drawIn = !reduced && start !== undefined ? Math.min(1, (performance.now() - start) / 600) : 1;
      if (visual.annotation) {
        const { ring, pin, label } = visual.annotation, ringMat = ring.material;
        ringMat.uniforms.uGround.value = -(origin?.y ?? 0) - visual.unit.position.y + 0.15;
        pin.position.y = baseY + 14;
        visual.annotation.leader.position.y = baseY;
        const ringColor = ended ? ENDED : row.kind === 'fire' ? AREA : PERSON;
        const ringOpacity = ended ? 0.5 : 0.85, ringFill = ended ? 0 : drawIn;
        ring.visible = !pending;
        ringMat.uniforms.uColor.value.set(ringColor);
        ringMat.uniforms.uOpacity.value = ringOpacity;
        ringMat.uniforms.uRadius.value = row.radiusM;
        ringMat.uniforms.uFill.value = ringFill;
        ringMat.uniforms.uReveal.value = drawIn;
        const extent = row.radiusM + Math.max(1.5, row.radiusM * 0.15);
        ringMat.uniforms.uExtent.value = extent;
        ringMat.uniforms.uOccluded.value = ended ? 0.12 : 0.22;
        const pinKind = pending || ended ? row.kind === 'fire' ? 'fire-ended' : 'person_fall-ended' : row.kind;
        pin.material.map = pinTextures![pinKind]!;
        pin.scale.set(pinScale, pinScale, 1);
        if (label && this.options.preview) {
          const overlay = this.options.preview.overlay;
          label.textContent = incidentLabel(row, this.options.preview.locale().startsWith('zh'));
          this.position.copy(visual.unit.position); this.position.y += pin.position.y + 2; this.position.project(camera);
          label.hidden = this.position.z < -1 || this.position.z > 1 || Math.abs(this.position.x) > 0.96 || Math.abs(this.position.y) > 0.96;
          label.style.left = `${(this.position.x + 1) * overlay.clientWidth / 2}px`;
          label.style.top = `${(1 - this.position.y) * overlay.clientHeight / 2 - 14}px`;
        }
      }
      // Flames: ramp 3 s in sim time after activation, fade 4 s after ending.
      const ramp = pending ? 0 : ended ? Math.max(0, 1 - row.sinceS / 4) : Math.min(1, row.sinceS / 3);
      const burning = row.kind === 'fire' && ramp > 0;
      visual.glowRamp = burning ? ramp : 0;
      visual.flameTop = baseY + flameTop;
      visual.active = row.kind === 'fire' && row.phase === 'active';
      visual.flames.visible = burning;
      const decal = visual.decal.material.uniforms;
      visual.decal.visible = burning || (!!visual.annotation && !pending);
      decal.uRing.value = visual.annotation && !pending ? 1 : 0;
      if (typeof simTime === 'number') decal.uTime.value = simTime; else decal.uTime = simTime;
      decal.uRamp.value = burning ? ramp : 0;
      decal.uGlowRadius.value = Math.max(row.radiusM * 0.35, 3);
      decal.uRoof.value = baseY + 0.1;
      if (burning) {
        if (typeof simTime === 'number') visual.flames.material.uniforms.uTime.value = simTime;
        else visual.flames.material.uniforms.uTime = simTime;
        visual.flames.material.uniforms.uRamp.value = ramp;
        // Physical height 5–12 m scaled by sqrt(radius/30), with a projected-size floor;
        // cluster radius min(0.2·R, 6 m); width 0.8 × height.
        const cluster = Math.min(0.2 * row.radiusM, 6);
        if (dirty) {
          for (let i = 0; i < visual.flameCount; i++) {
            const angle = visual.flameSeeds[i * 3] * Math.PI * 2, radius = visual.flameSeeds[i * 3 + 1] * cluster;
            const yaw = this.cameraYaw + angle + Math.sin(simSeconds * 0.6 + i) * 0.15;
            // Keep the per-flame yaw as the upright cylinder blends toward the camera.
            this.quaternion.setFromAxisAngle(this.vertical, yaw);
            if (this.flameFace > 0) {
              this.spin.setFromAxisAngle(this.vertical, yaw);
              this.flameSpherical.copy(camera.quaternion).multiply(this.spin);
              this.quaternion.slerp(this.flameSpherical, this.flameFace);
            }
            const height = T.MathUtils.clamp(baseHeight * (0.8 + 0.4 * visual.flameSeeds[i * 3 + 2]), 5, 12) * displayScale;
            this.scale.set(height * 0.8, height, 1);
            this.position.set(Math.cos(angle) * radius, roofY - visual.unit.position.y + this.scale.y / 2, Math.sin(angle) * radius);
            this.matrix.compose(this.position, this.quaternion, this.scale);
            visual.flames.setMatrixAt(i, this.matrix);
            this.scale.set(this.scale.x * 0.35, this.scale.y * 0.55, 1);
            this.position.y = baseY + this.scale.y / 2;
            this.matrix.compose(this.position, this.quaternion, this.scale);
            visual.flames.setMatrixAt(i + visual.flameCount, this.matrix);
          }
          visual.flames.instanceMatrix.needsUpdate = true;
        }
      }
      visual.smokeBase = baseY + flameTop;
      if (row.kind === 'fire' && !pending) { if (dirty) this.writeSmoke(visual, camera, row); }
      else { visual.smoke.visible = false; visual.smoke.count = 0; }
    }
    for (let i = this.visualList.length - 1; i >= 0; i--) {
      const visual = this.visualList[i];
      if (visual.seen !== frame) { this.disposeVisual(visual); this.visuals.delete(visual.id); this.visualList.splice(i, 1); }
    }
    // One warm PointLight per scene for the nearest active fire.
    let nearest: Visual | undefined, distance = Infinity;
    if (this.quality === 'high') for (let i = 0; i < this.visualList.length; i++) {
      const visual = this.visualList[i];
      if (!visual.light || !visual.active || visual.unit.visible !== true) continue;
      const d = visual.unit.position.distanceToSquared(camera.position);
      if (d < distance) { distance = d; nearest = visual; }
    }
    for (let i = 0; i < this.visualList.length; i++) { const visual = this.visualList[i]; if (visual.light) visual.light.visible = visual === nearest; }
  }
  /** Puffs are closed-form in (age, seed): position, size, spin and alpha need no history. */
  private writeSmoke(visual: Visual, camera: T.Camera, row: Conditions['incidents'][number]) {
    const simSeconds = visual.time, baseHeight = visual.smokeBase;
    if (!this.smoke) { visual.smoke.visible = false; return; }
    const capacity = visual.smoke.instanceMatrix.count;
    const windEast = this.wind.x, windNorth = this.wind.y;
    const high = this.quality !== 'low';
    const rate = high ? SMOKE_RATE : SMOKE_RATE_LOW, lifetime = high ? SMOKE_LIFE_HIGH_S : SMOKE_LIFE_LOW_S;
    const limit = high ? SMOKE_HIGH : SMOKE_LOW;
    const ended = row.phase === 'ended';
    const elapsed = row.activeS, window = ended ? row.activeS - row.sinceS : row.activeS;
    let count = 0;
    for (let slot = 0; slot < limit && count < capacity; slot++) {
      const born = slot / rate;
      if (window < born) break;
      // Equivalent to floor((window - born) / (limit / RATE)), without subtractive
      // rounding losing the emission exactly at a low-quality period boundary.
      const k = Math.floor((window * rate - slot) / limit);
      // A period shorter than the lifetime lets both k and k-1 survive.
      for (let cycle = k; cycle >= Math.max(0, k - 1) && count < capacity; cycle--) {
        const age = elapsed - (slot + cycle * limit) / rate;
        if (age < 0 || age >= lifetime) continue;
        // Keep puff arithmetic in this loop: passing its floating values through
        // a separate JS call boxes several Numbers per puff on Chromium.
        // Normalise by this tier's own lifetime, so the low plume's colour and opacity
        // follow its 12 s clock instead of ageing at the high tier's 24 s rate.
        const life = age / lifetime;
        const seedA = visual.puffSeeds[slot * 4], seedB = visual.puffSeeds[slot * 4 + 1], seedC = visual.puffSeeds[slot * 4 + 2];
        // Rise ~2.6 m/s slowing with height: ≥ 15 m before the column bends downwind.
        // Drift (0.55 × wind) ramps in with min(1, age/6), so young puffs climb straight out of the flames
        // and the column stays dense near the fire instead of streaming away as thin wisps.
        const risen = 2.6 * age * (1 - 0.2 * age / SMOKE_LIFE_HIGH_S), drift = 0.55 * Math.min(1, age / 6) * age;
        const jitterX = (seedA - 0.5) * 6 + Math.sin(simSeconds * 0.2 + seedB * 6) * 1.5;
        const jitterZ = (seedB - 0.5) * 6 + Math.cos(simSeconds * 0.17 + seedC * 6) * 1.5;
        // Dense dark root: high grows 6 → 14 m at 3 s then billows to 44 m; the low tier is
        // art-directed across its 12 s life as 6 → 16 m at 3 s then a 40 m billow.
        let size: number;
        if (high) size = age < SMOKE_GROW_ROOT_S ? 6 + (14 - 6) * (age / SMOKE_GROW_ROOT_S)
          : 14 + (44 - 14) * ((age - SMOKE_GROW_ROOT_S) / (SMOKE_LIFE_HIGH_S - SMOKE_GROW_ROOT_S));
        else size = age < SMOKE_GROW_ROOT_S ? SMOKE_SIZE_LOW + (SMOKE_SIZE_LOW_3S - SMOKE_SIZE_LOW) * (age / SMOKE_GROW_ROOT_S)
          : SMOKE_SIZE_LOW_3S + (SMOKE_SIZE_LOW_END - SMOKE_SIZE_LOW_3S) * ((age - SMOKE_GROW_ROOT_S) / (SMOKE_LIFE_LOW_S - SMOKE_GROW_ROOT_S));
        this.quaternion.copy(camera.quaternion);
        QUARTER_TURN.setFromQuaternion(this.quaternion, 'YXZ');
        QUARTER_TURN.z = simSeconds * (0.1 + seedC * 0.2) + seedA * Math.PI * 2;
        this.quaternion.setFromEuler(QUARTER_TURN);
        this.position.set(jitterX + windEast * drift, baseHeight + risen, jitterZ - windNorth * drift);
        this.scale.set(size, size, 1);
        this.matrix.compose(this.position, this.quaternion, this.scale);
        visual.smoke.setMatrixAt(count, this.matrix);
        const tint = life < 0.5 ? this.color.copy(SMOKE_BASE).lerp(SMOKE_MID, life * 2) : this.color.copy(SMOKE_MID).lerp(SMOKE_HIGH_TONE, (life - 0.5) * 2);
        // High: dense root (0.72 for 3 s), body held at 0.6 to 45 % of life, then a fade.
        // Low: a brighter 0.82 peak held to 60 % of life, then a linear fade to the cutoff,
        // so the plume separates from the darker low-quality scene for its whole life.
        let alpha: number;
        if (high) alpha = age < SMOKE_GROW_ROOT_S ? 0.72 - 0.12 * (age / SMOKE_GROW_ROOT_S)
          : life < 0.45 ? 0.6 : 0.6 * (1 - (life - 0.45) / 0.55);
        else alpha = life <= SMOKE_ALPHA_LOW_HOLD ? SMOKE_ALPHA_LOW
          : SMOKE_ALPHA_LOW * (1 - (life - SMOKE_ALPHA_LOW_HOLD) / (1 - SMOKE_ALPHA_LOW_HOLD));
        visual.tint.setXYZW(count, tint.r, tint.g, tint.b, alpha);
        visual.cells.setX(count, Math.floor(visual.puffSeeds[slot * 4 + 3] * 4));
        count++;
      }
    }
    visual.smoke.count = count;
    visual.smoke.geometry.instanceCount = count;
    visual.smoke.instanceMatrix.needsUpdate = true;
    visual.tint.needsUpdate = true; visual.cells.needsUpdate = true;
    visual.smoke.visible = count > 0;
  }

  setWind(east: number, north: number) { this.wind.set(east, north); }
  /** Art-directed smoke lighting: scattered ambient colour, day/dusk scatter mix and the
   *  fire underlight strength (which fades with the flame ramp), reusing the colour scratch. */
  setSmokeLight(scene: T.Scene, conditions: Conditions): void;
  setSmokeLight(dusk: boolean, rain: number): void;
  setSmokeLight(scene: T.Scene | boolean, conditions: Conditions | number) {
    if (!this.smoke) return;
    const dusk = typeof scene === 'boolean' ? scene : scene.userData.dusk === true;
    const rain = typeof conditions === 'number' ? conditions : (scene as T.Scene).userData.weatherWet as number | undefined ?? Math.min(conditions.precipitationMmH / 8, 1);
    const u = this.smoke.uniforms;
    u.uSmokeLight.value.copy(this.smokeLightScratch.copy(dusk ? SMOKE_LIGHT_DUSK : SMOKE_LIGHT_DAY).lerp(SMOKE_LIGHT_RAIN, 0.3 * Math.min(1, Math.max(0, rain))));
    // Only the dark low-quality pipeline lifts the smoke light; the other tiers stay untouched.
    if (this.quality === 'low') u.uSmokeLight.value.multiplyScalar(SMOKE_LIGHT_LOW_GAIN);
    u.uScatter.value = dusk ? 0.42 : 0.15;
    this.smokeGlow = dusk ? 0.55 : 0.25;
  }
  private smokeGlow = 0.25;
  private wind = new T.Vector2();
  private disposeVisual(visual: Visual) {
    this.structureRevision++; visual.unit.removeFromParent(); visual.flames.material.dispose();
    visual.flames.dispose(); visual.smoke.dispose(); visual.decal.material.dispose();
    visual.smoke.geometry.dispose(); if (visual.smoke.material !== this.smoke) visual.smoke.material.dispose();
    if (visual.annotation) {
      visual.annotation.pin.material.dispose(); visual.annotation.leader.material.dispose(); visual.annotation.label?.remove();
    }
    visual.light?.dispose();
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const visual of this.visuals.values()) {
      this.disposeVisual(visual);
    }
    this.visuals.clear(); this.visualList.length = 0;
    this.flames.dispose(); this.smoke?.dispose(); this.depth.dispose();
    this.plane.dispose(); this.ringGeometry.dispose(); this.leaderGeometry.dispose();
    if (this.options.annotations && --pinUsers === 0) {
      for (const texture of Object.values(pinTextures ?? {})) texture?.dispose();
      pinTextures = undefined;
    }
  }
}
