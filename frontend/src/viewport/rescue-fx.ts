import * as T from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { scalarUniforms } from './atmosphere/fx-shared';

/**
 * Flood-rescue visuals, closed-form in simulation time (no wall clock, no
 * accumulation): murky floodwater, screen-space rooftop-survivor pin, mission
 * marker glyphs and flowing radio links. Geometry is rebuilt only when the
 * committed cut changes; per frame only uniforms and link endpoints move.
 */

// The same fog chunks the atmosphere materials include (Three's preprocessor
// needs them on their own lines).
const FOG_PARS_V = '\n#include <fog_pars_vertex>\n', FOG_V = '\n#include <fog_vertex>\n',
  FOG_PARS_F = '\n#include <fog_pars_fragment>\n', FOG_F = '\n#include <fog_fragment>\n',
  OUTPUT_F = '\n#include <tonemapping_fragment>\n#include <colorspace_fragment>\n';

export const FLOOD_BODY = '#3e5654', FLOOD_SHALLOW = '#6f7a6a', FLOOD_FOAM = '#d8d2bc';
const SILT = new T.Color(FLOOD_BODY), SILT_SHALLOW = new T.Color(FLOOD_SHALLOW), FOAM = new T.Color(FLOOD_FOAM);

/** Fixed sky-light direction for the water glint, normalized. */
export const WATER_LIGHT = new T.Vector3(0.3, 1, 0.2).normalize();

/**
 * One water material shared by flood areas and submerged-road ribbons: per-
 * vertex local metres/extent let a single program shade every patch. Ripples
 * are 4 directional waves at non-commensurate angles (λ 9.3/13.7/6.1/21.0 m)
 * evaluated through a slow value-noise domain warp, so crests wander and never
 * tile into a regular lattice; the normal stays tilted ~0.15 so fresnel and
 * the sheen shimmer as simulation time advances; phase follows simulation time
 * via uTime (still when the replay is paused). Fresnel mixes toward uSky at
 * pow(1 − NdotV, 3) · 0.75; a fixed sky light adds a broad soft specular sheen
 * (exponent 18, strength 0.16, faded by fwidth-based antialiasing at distance)
 * modulated by the rippled normal. The silt/foam edge band is 2.5 m wide,
 * `#d8d2bc`, alpha 0.5, broken by noise so it never reads as a clean rectangle.
 */
export function waterMaterial(): T.ShaderMaterial {
  return scalarUniforms(new T.ShaderMaterial({
    transparent: true, depthWrite: false, premultipliedAlpha: true, fog: true,
    uniforms: T.UniformsUtils.merge([T.UniformsLib.fog, {
      uTime: { value: 0 }, uSky: { value: new T.Color('#2b3440') },
      uBody: { value: SILT.clone() }, uShallow: { value: SILT_SHALLOW.clone() }, uFoam: { value: FOAM.clone() },
    }]),
    // Three r170 targets WebGL2: fwidth is core, and extension directives in
    // the shader body would follow Three's generated GLSL declarations.
    vertexShader: `attribute vec2 aLocal;attribute vec2 aExtent;attribute float aKind;
      varying vec2 vLocal;varying vec2 vExtent;varying float vKind;varying vec3 vView;${FOG_PARS_V}
      void main(){vLocal=aLocal;vExtent=aExtent;vKind=aKind;
      vec4 mvPosition=modelViewMatrix*vec4(position,1.0);vView=mvPosition.xyz;${FOG_V}
      gl_Position=projectionMatrix*mvPosition;}`,
    fragmentShader: `uniform float uTime;uniform vec3 uSky;uniform vec3 uBody;uniform vec3 uShallow;uniform vec3 uFoam;
      varying vec2 vLocal;varying vec2 vExtent;varying float vKind;varying vec3 vView;${FOG_PARS_F}
      float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      // Value noise and its analytic gradient share the same four lattice hashes.
      // x is the value in -1..1; yz are derivatives in noise coordinates.
      vec3 vnoise(vec2 p){
        vec2 i=floor(p),f=fract(p);vec2 u=f*f*(3.0-2.0*f),du=6.0*f*(1.0-f);
        float a=hash(i),b=hash(i+vec2(1.0,0.0)),c=hash(i+vec2(0.0,1.0)),d=hash(i+vec2(1.0,1.0));
        return vec3(mix(mix(a,b,u.x),mix(c,d,u.x),u.y)*2.0-1.0,
          2.0*du*vec2(mix(b-a,d-c,u.y),mix(c-a,d-b,u.x)));}
      // 4 directional waves at non-commensurate angles (directions normalized,
      // k=2*PI/wavelength for λ 9.3/13.7/6.1/21.0 m), so crossings never tile.
      // A slow domain warp makes the crests wander instead of forming a lattice.
      vec2 rippleGradient(vec2 p,float t){
        vec3 wx=vnoise(p*0.05+t*0.03),wy=vnoise(p*0.05+17.0-t*0.025);
        p+=2.5*vec2(wx.x,wy.x);
        vec2 k1=vec2(0.974555,0.224148)*0.675611,k2=vec2(-0.379353,0.925252)*0.458627;
        vec2 k3=vec2(0.778117,-0.628119)*1.030030,k4=vec2(-0.958098,-0.286442)*0.299199;
        vec2 g=k1*cos(dot(p,k1)+t*0.9)+0.70*k2*cos(dot(p,k2)-t*0.6)
              +0.35*k3*cos(dot(p,k3)+t*1.3)+0.90*k4*cos(dot(p,k4)+t*0.4);
        // Chain rule through the warp, including its 0.05 spatial frequency.
        return g+0.125*(g.x*wx.yz+g.y*wy.yz);}
      void main(){
        // ~0.15 normal tilt: the ripple must stay visible at oblique views.
        vec2 slope=rippleGradient(vLocal,uTime);
        vec3 N=normalize(vec3(-slope.x*0.15,1.0,-slope.y*0.15));
        N=normalize(mat3(viewMatrix)*N);
        vec3 V=normalize(-vView);
        float f=pow(1.0-max(dot(N,V),0.0),3.0)*0.75;
        vec3 color=mix(uBody,uSky,f);
        // Fixed sky-light glint, modulated by the rippled normal.
        vec3 L=normalize(mat3(viewMatrix)*vec3(0.3,1.0,0.2));
        vec3 Hv=normalize(L+V);
        color+=vec3(pow(max(dot(N,Hv),0.0),18.0)*0.16*(1.0-smoothstep(0.15,0.6,length(fwidth(vLocal)))));
        float alpha=0.82;
        if(vKind<0.5){
          float edge=min(vExtent.x-abs(vLocal.x),vExtent.y-abs(vLocal.y));
          color=mix(uShallow,color,smoothstep(0.0,6.0,edge));
          alpha=mix(0.55,0.82,smoothstep(0.0,6.0,edge));
          // Broken silt/foam line, 2.5 m wide at alpha 0.5, dithered by a
          // cheap hash so the boundary never reads as a clean rectangle.
          float foam=(1.0-smoothstep(0.5,2.5,edge))*step(0.35,hash(floor(vLocal*1.7)+vec2(3.0,7.0)));
          color=mix(color,uFoam,foam);
          alpha=mix(alpha,0.5,foam);
        }else{
          alpha=0.5*(1.0-smoothstep(0.55,1.0,abs(vLocal.y)/max(vExtent.y,0.001)));
        }
        gl_FragColor=vec4(color,alpha);${OUTPUT_F}${FOG_F}
        gl_FragColor.rgb*=alpha;}`,
  }));
}

/** A flat patch in the XZ plane carrying per-vertex local metres and extents. */
export function waterGeometry(width: number, depth: number, kind: number): T.BufferGeometry {
  const geometry = new T.PlaneGeometry(width, depth).rotateX(-Math.PI / 2);
  const position = geometry.getAttribute('position') as T.BufferAttribute;
  const local = new Float32Array(position.count * 2), extent = new Float32Array(position.count * 2), kinds = new Float32Array(position.count);
  for (let index = 0; index < position.count; index++) {
    local[index * 2] = position.getX(index); local[index * 2 + 1] = position.getZ(index);
    extent[index * 2] = width / 2; extent[index * 2 + 1] = depth / 2; kinds[index] = kind;
  }
  geometry.setAttribute('aLocal', new T.BufferAttribute(local, 2));
  geometry.setAttribute('aExtent', new T.BufferAttribute(extent, 2));
  geometry.setAttribute('aKind', new T.BufferAttribute(kinds, 1));
  return geometry;
}

/** Clip the full 7 m road footprint against every flood rectangle, preserving the outside pieces. */
export function roadWaterGeometry(start: T.Vector3, end: T.Vector3, areas: T.Box3[]): T.BufferGeometry | undefined {
  type Point = [number, number];
  const length = Math.hypot(end.x - start.x, end.z - start.z), dx = (end.x - start.x) / length, dz = (end.z - start.z) / length;
  const cx = (start.x + end.x) / 2, cz = (start.z + end.z) / 2;
  let polygons: Point[][] = [[
    [start.x - dz * 3.5, start.z + dx * 3.5], [end.x - dz * 3.5, end.z + dx * 3.5],
    [end.x + dz * 3.5, end.z - dx * 3.5], [start.x + dz * 3.5, start.z - dx * 3.5],
  ]];
  for (const area of areas) {
    const outside: Point[][] = [];
    for (const polygon of polygons) {
      let remaining = polygon;
      const planes = [[1, 0, area.min.x], [-1, 0, -area.max.x], [0, 1, area.min.z], [0, -1, -area.max.z]];
      for (const [nx, nz, boundary] of planes) {
        const inside: Point[] = [], piece: Point[] = [];
        for (let i = 0; i < remaining.length; i++) {
          const a = remaining[i], b = remaining[(i + 1) % remaining.length];
          const da = a[0] * nx + a[1] * nz - boundary, db = b[0] * nx + b[1] * nz - boundary;
          (da >= 0 ? inside : piece).push(a);
          if ((da >= 0) !== (db >= 0)) {
            const t = da / (da - db), crossing: Point = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
            inside.push(crossing); piece.push(crossing);
          }
        }
        if (piece.length >= 3) outside.push(piece);
        remaining = inside;
        if (remaining.length < 3) break;
      }
    }
    polygons = outside;
  }
  if (!polygons.length) return undefined;
  const positions: number[] = [], normals: number[] = [], uv: number[] = [], local: number[] = [], extent: number[] = [], kinds: number[] = [], indices: number[] = [];
  for (const polygon of polygons) {
    const base = positions.length / 3;
    for (const [x, z] of polygon) {
      const along = (x - cx) * dx + (z - cz) * dz, across = -(x - cx) * dz + (z - cz) * dx;
      positions.push(x - cx, 0, z - cz); normals.push(0, 1, 0);
      uv.push(along / length + 0.5, across / 7 + 0.5); local.push(along, across); extent.push(length / 2, 3.5); kinds.push(1);
    }
    for (let i = 1; i < polygon.length - 1; i++) indices.push(base, base + i, base + i + 1);
  }
  const geometry = new T.BufferGeometry();
  geometry.setAttribute('position', new T.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new T.Float32BufferAttribute(normals, 3));
  geometry.setAttribute('uv', new T.Float32BufferAttribute(uv, 2));
  geometry.setAttribute('aLocal', new T.Float32BufferAttribute(local, 2));
  geometry.setAttribute('aExtent', new T.Float32BufferAttribute(extent, 2));
  geometry.setAttribute('aKind', new T.Float32BufferAttribute(kinds, 1));
  geometry.setIndex(indices);
  return geometry;
}

/** One draw for all patches, retaining their local ripple/edge coordinates.
 * Transparent patches keep Three's back-to-front centre ordering as the camera
 * moves. Only indices change; geometry, materials and scratch storage persist. */
export function mergeWater(group: T.Group): void {
  if (group.children.length < 2) return;
  const patches = group.children as T.Mesh[];
  const anchor = patches[0].position.clone();
  const centers = patches.map(patch => patch.position.clone().sub(anchor));
  const geometries = patches.map(patch => {
    patch.updateMatrix();
    return patch.geometry.applyMatrix4(patch.matrix).translate(-anchor.x, -anchor.y, -anchor.z);
  });
  const offsets = [0];
  for (const patch of geometries) offsets.push(offsets[offsets.length - 1] + patch.getIndex()!.count);
  const geometry = mergeGeometries(geometries);
  if (!geometry) throw Error('Water patches have incompatible vertex attributes');
  const mesh = new T.Mesh(geometry, patches[0].material);
  mesh.position.copy(anchor); mesh.renderOrder = 2;
  const index = geometry.getIndex()!, source = index.array.slice();
  const order = centers.map((_, i) => i), depths = new Float64Array(centers.length);
  const transform = new T.Matrix4(), previous = new T.Matrix4(), point = new T.Vector3();
  let initialized = false;
  mesh.onBeforeRender = (_renderer, _scene, camera) => {
    transform.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).multiply(mesh.matrixWorld);
    if (initialized && transform.equals(previous)) return;
    initialized = true; previous.copy(transform);
    for (let i = 0; i < centers.length; i++) depths[i] = point.copy(centers[i]).applyMatrix4(transform).z;
    for (let i = 1; i < order.length; i++) {
      const patch = order[i]; let j = i - 1;
      while (j >= 0 && (depths[order[j]] < depths[patch] || depths[order[j]] === depths[patch] && order[j] > patch)) {
        order[j + 1] = order[j]; j--;
      }
      order[j + 1] = patch;
    }
    let destination = 0;
    for (let i = 0; i < order.length; i++) {
      const patch = order[i];
      for (let j = offsets[patch]; j < offsets[patch + 1]; j++) index.array[destination++] = source[j];
    }
    index.needsUpdate = true;
  };
  for (const patch of patches) patch.geometry.dispose();
  group.clear(); group.add(mesh);
}

/** 26 CSS px incident-style disc: state colour, 2 px white ring, person with a raised arm. */
const pinCache = new Map<string, T.Texture>();
export function rescuePinTexture(color: string): T.Texture {
  const cached = pinCache.get(color); if (cached) return cached;
  const size = 64, canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
  const context = canvas.getContext('2d')!;
  context.beginPath(); context.arc(size / 2, size / 2, size / 2 - 3, 0, Math.PI * 2);
  context.fillStyle = color; context.fill();
  context.lineWidth = 3; context.strokeStyle = '#ffffff'; context.stroke();
  context.fillStyle = '#fff3dc'; context.strokeStyle = '#fff3dc'; context.lineWidth = 4; context.lineCap = 'round';
  // Head, torso, standing leg, raised arm.
  context.beginPath(); context.arc(size / 2, 20, 6, 0, Math.PI * 2); context.fill();
  context.fillRect(size / 2 - 3, 27, 6, 14);
  context.beginPath(); context.moveTo(size / 2, 41); context.lineTo(size / 2, 50); context.stroke();
  context.beginPath(); context.moveTo(size / 2 + 1, 30); context.lineTo(size / 2 + 12, 16); context.stroke();
  context.beginPath(); context.moveTo(size / 2 - 1, 30); context.lineTo(size / 2 - 9, 38); context.stroke();
  const texture = new T.CanvasTexture(canvas); texture.colorSpace = T.SRGBColorSpace;
  pinCache.set(color, texture);
  return texture;
}

/** 18 CSS px white glyphs; the sprite material tint gives the role colour. */
const glyphCache = new Map<string, T.Texture>();
export function markerGlyphTexture(kind: 'relay' | 'command' | 'inspect'): T.Texture {
  const cached = glyphCache.get(kind); if (cached) return cached;
  const size = 64, canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
  const context = canvas.getContext('2d')!;
  context.strokeStyle = '#ffffff'; context.fillStyle = '#ffffff'; context.lineWidth = kind === 'command' ? 4 : 6; context.lineCap = 'round';
  if (kind === 'relay') {
    // Mast with two broadcast arcs.
    context.beginPath(); context.moveTo(size / 2, 58); context.lineTo(size / 2, 26); context.stroke();
    context.beginPath(); context.arc(size / 2, 32, 14, -Math.PI * 0.8, -Math.PI * 0.2); context.stroke();
    context.beginPath(); context.arc(size / 2, 32, 26, -Math.PI * 0.8, -Math.PI * 0.2); context.stroke();
    context.beginPath(); context.arc(size / 2, 26, 4, 0, Math.PI * 2); context.fill();
  } else if (kind === 'inspect') {
    // Hollow inspection diamond (replaces the 4 m world ring).
    context.beginPath();
    context.moveTo(size / 2, 4); context.lineTo(size - 4, size / 2);
    context.lineTo(size / 2, size - 4); context.lineTo(4, size / 2);
    context.closePath(); context.stroke();
    context.beginPath(); context.arc(size / 2, size / 2, 5, 0, Math.PI * 2); context.fill();
  } else {
    // Lattice command tower.
    context.beginPath(); context.moveTo(size / 2 - 14, 52); context.lineTo(size / 2 - 5, 14); context.lineTo(size / 2 + 5, 14); context.lineTo(size / 2 + 14, 52); context.stroke();
    context.beginPath(); context.moveTo(size / 2 - 10, 38); context.lineTo(size / 2 + 10, 38); context.stroke();
    context.beginPath(); context.moveTo(size / 2 - 12, 46); context.lineTo(size / 2 + 12, 46); context.stroke();
    context.fillRect(size / 2 - 7, 10, 14, 4);
  }
  const texture = new T.CanvasTexture(canvas); texture.colorSpace = T.SRGBColorSpace;
  glyphCache.set(kind, texture);
  return texture;
}

export function screenSprite(map: T.Texture, color: string, cssPxScale: number): T.Sprite {
  const sprite = new T.Sprite(new T.SpriteMaterial({ map, color, sizeAttenuation: false, depthTest: false, depthWrite: false, transparent: true, fog: false }));
  sprite.renderOrder = 12;
  sprite.scale.set(cssPxScale, cssPxScale, 1);
  return sprite;
}

/** Radius 6 m roof pulse, expanded to a 2 CSS px line in the vertex shader. */
export function pulseMaterial(resolution: T.Vector2): T.ShaderMaterial {
  return scalarUniforms(new T.ShaderMaterial({
    transparent: true, depthWrite: false, depthTest: false, premultipliedAlpha: true, fog: false,
    side: T.DoubleSide, forceSinglePass: true,
    uniforms: { uTime: { value: 0 }, uColor: { value: new T.Color('#ffb74b') }, uResolution: { value: resolution } },
    vertexShader: `uniform vec2 uResolution;varying float vEdge;varying float vClipW;
      void main(){
        vec3 radial=normalize(position);
        vec4 clip=projectionMatrix*modelViewMatrix*vec4(radial*6.0,1.0);
        vec4 tangent=projectionMatrix*modelViewMatrix*vec4(radial*6.0+vec3(-radial.y,radial.x,0.0),1.0);
        vec2 direction=(tangent.xy/tangent.w-clip.xy/clip.w)*uResolution;
        vec2 normal=vec2(-direction.y,direction.x)/max(length(direction),1e-6);
        float side=length(position.xy)<5.995?-1.0:1.0;
        vEdge=side*1.5*clip.w;vClipW=clip.w;
        gl_Position=clip+vec4(normal*side*1.5*2.0/uResolution*clip.w,0.0,0.0);
      }`,
    fragmentShader: `uniform float uTime;uniform vec3 uColor;varying float vEdge;varying float vClipW;
      void main(){float alpha=0.6*(1.0-fract(uTime/1.6))*(1.0-smoothstep(0.5,1.5,abs(vEdge/vClipW)));
      gl_FragColor=vec4(uColor,alpha);${OUTPUT_F}
      gl_FragColor.rgb*=alpha;}`,
  }));
}

/** Thin leader from an elevated glyph down to the ground/roof; never occluded. */
export function leaderMaterial(): T.LineBasicMaterial {
  return new T.LineBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.6, depthTest: false, depthWrite: false, fog: false });
}

/** Screen-space link ribbons: one quad per cached slot, one transparent draw. */
export const LINK_DASH_PX = 10, LINK_GAP_PX = 8, LINK_FLOW_PX_PER_S = 40;
const LINK_PERIOD_PX = LINK_DASH_PX + LINK_GAP_PX;

export function linkLineMaterial(): T.ShaderMaterial {
  return scalarUniforms(new T.ShaderMaterial({
    transparent: true, depthWrite: false, depthTest: false, premultipliedAlpha: true, fog: false,
    side: T.DoubleSide, forceSinglePass: true,
    uniforms: {
      uTime: { value: 0 }, uResolution: { value: new T.Vector2(1, 1) }, uPixelRatio: { value: 1 },
    },
    vertexShader: `attribute vec3 aFrom;attribute vec3 aTo;attribute vec3 aColor;
      attribute vec4 aStyle;attribute vec2 aCorner;
      uniform vec2 uResolution;uniform float uPixelRatio;
      varying float vAlong;varying float vClipW;varying vec3 vColor;varying float vAlpha;
      varying float vWidth;varying float vHalo;varying float vFlow;varying float vEdge;
      void main(){
        vColor=aColor;vAlpha=aStyle.x;vWidth=aStyle.y;vHalo=aStyle.z;vFlow=aStyle.w;
        vec4 clipFrom=projectionMatrix*modelViewMatrix*vec4(aFrom,1.0);
        vec4 clipTo=projectionMatrix*modelViewMatrix*vec4(aTo,1.0);
        // Trim at the near plane before dividing by w, including links behind the camera.
        float nearFrom=clipFrom.z+clipFrom.w,nearTo=clipTo.z+clipTo.w;
        if(nearFrom<=0.0&&nearTo<=0.0){
          vAlpha=0.0;vAlong=0.0;vEdge=0.0;vClipW=1.0;gl_Position=vec4(0.0,0.0,0.0,1.0);return;
        }
        if(nearFrom<=0.0)clipFrom=mix(clipFrom,clipTo,(-nearFrom+1e-5)/(nearTo-nearFrom));
        else if(nearTo<=0.0)clipTo=mix(clipTo,clipFrom,(-nearTo+1e-5)/(nearFrom-nearTo));
        vec2 resolution=uResolution*uPixelRatio;
        vec2 pixelDir=(clipTo.xy/clipTo.w-clipFrom.xy/clipFrom.w)*resolution*0.5;
        float pixelLength=max(length(pixelDir),1e-6);
        vec2 normal=vec2(-pixelDir.y,pixelDir.x)/pixelLength;
        float halfCss=max(aStyle.z,aStyle.y*0.5)+0.5;
        vec4 clip=mix(clipFrom,clipTo,aCorner.x);
        vec2 ndcOffset=normal*aCorner.y*halfCss*uPixelRatio*2.0/resolution;
        // Undo perspective interpolation in the fragment shader: dashes measure CSS pixels.
        vAlong=aCorner.x*pixelLength/uPixelRatio*clip.w;
        vEdge=aCorner.y*halfCss*clip.w;vClipW=clip.w;
        gl_Position=clip+vec4(ndcOffset*clip.w,0.0,0.0);
      }`,
    fragmentShader: `uniform float uTime;
      varying float vAlong;varying float vClipW;varying vec3 vColor;varying float vAlpha;
      varying float vWidth;varying float vHalo;varying float vFlow;varying float vEdge;
      void main(){
        float across=abs(vEdge/vClipW);
        float core=1.0-smoothstep(vWidth*0.5-0.5,vWidth*0.5+0.5,across);
        float halo=vHalo>0.0?0.25*(1.0-smoothstep(vWidth*0.5,vHalo,across)):0.0;
        float distancePx=vAlong/vClipW;
        float dash=1.0-step(${LINK_DASH_PX}.0,mod(distancePx-max(vFlow,0.0)*uTime*${LINK_FLOW_PX_PER_S}.0,${LINK_PERIOD_PX}.0));
        // Failed hops retain static dashes; only sending hops advance their phase.
        float dashed=abs(vFlow)>0.5?dash:1.0;
        float alpha=max(vAlpha*core*dashed,halo);
        if(vAlpha<=0.0||alpha<=0.01)discard;
        gl_FragColor=vec4(vColor,alpha);${OUTPUT_F}
        gl_FragColor.rgb*=alpha;}`,
  }));
}

export interface LinkRibbon {
  readonly capacity: number;
  geometry: T.BufferGeometry;
  move(index: number, from: T.Vector3, to: T.Vector3): void;
  style(index: number, color: string, alpha: number, widthPx: number, haloPx: number, flow: number): void;
  setAlpha(index: number, alpha: number): void;
  setVisible(count: number): void;
  dispose(): void;
}

export function linkRibbonGeometry(capacity: number): LinkRibbon {
  const geometry = new T.BufferGeometry();
  const from = new T.BufferAttribute(new Float32Array(capacity * 18), 3).setUsage(T.DynamicDrawUsage);
  const to = new T.BufferAttribute(new Float32Array(capacity * 18), 3).setUsage(T.DynamicDrawUsage);
  const color = new T.BufferAttribute(new Float32Array(capacity * 18), 3).setUsage(T.DynamicDrawUsage);
  const style = new T.BufferAttribute(new Float32Array(capacity * 24), 4).setUsage(T.DynamicDrawUsage);
  const corners = new Float32Array(capacity * 12);
  const pattern = [0, -1, 1, -1, 1, 1, 0, -1, 1, 1, 0, 1];
  for (let link = 0; link < capacity; link++) corners.set(pattern, link * 12);
  geometry.setAttribute('position', new T.BufferAttribute(new Float32Array(capacity * 18), 3));
  geometry.setAttribute('aFrom', from); geometry.setAttribute('aTo', to);
  geometry.setAttribute('aColor', color); geometry.setAttribute('aStyle', style);
  geometry.setAttribute('aCorner', new T.BufferAttribute(corners, 2));
  geometry.setDrawRange(0, 0);
  const rgb = new T.Color();
  return {
    geometry, capacity,
    move(index, a, b) {
      for (let vertex = index * 6; vertex < index * 6 + 6; vertex++) {
        from.setXYZ(vertex, a.x, a.y, a.z); to.setXYZ(vertex, b.x, b.y, b.z);
      }
      from.needsUpdate = to.needsUpdate = true;
    },
    style(index, colorHex, alpha, widthPx, haloPx, flow) {
      rgb.set(colorHex);
      for (let vertex = index * 6; vertex < index * 6 + 6; vertex++) {
        color.setXYZ(vertex, rgb.r, rgb.g, rgb.b);
        style.setXYZW(vertex, alpha, widthPx, haloPx, flow);
      }
      color.needsUpdate = style.needsUpdate = true;
    },
    setAlpha(index, alpha) {
      for (let vertex = index * 6; vertex < index * 6 + 6; vertex++) style.setX(vertex, alpha);
      style.needsUpdate = true;
    },
    setVisible(count) { geometry.setDrawRange(0, count * 6); },
    dispose() { geometry.dispose(); },
  };
}
