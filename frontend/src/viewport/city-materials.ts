import * as T from 'three';
import type { PackedBatch, PackedObject } from './mesh-pack';
function seed(id: string) {
    let hash = 2166136261;
    for (const char of id)
        hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    return (hash >>> 0) / 4294967296;
}
function tagColor(value: string | undefined, fallback: string) {
    if (value && (/^#[\da-f]{3}([\da-f]{3})?$/i.test(value) || value.toLowerCase() in T.Color.NAMES))
        return new T.Color(value.toLowerCase());
    return new T.Color(fallback);
}
/** Batched OSM geometry, per-object display style. Windows/roof finish are estimates. */
export function decorateBuildings(geometry: T.BufferGeometry, batch: PackedBatch, objects: readonly PackedObject[], dusk: boolean): T.MeshStandardMaterial {
    // Ranges partition triangles, not vertices. Expansion keeps shared corners from
    // interpolating two buildings' tags into each other, without adding draw calls.
    if (geometry.index) {
        const expanded = geometry.toNonIndexed();
        geometry.copy(expanded);
        expanded.dispose();
    }
    const position = geometry.getAttribute('position');
    const wall = new Float32Array(position.count * 3), roof = new Float32Array(position.count * 3), style = new Float32Array(position.count * 4);
    const tagsById = new Map(objects.map(object => [object.id, object.tags]));
    let start = 0, tagged = 0;
    for (const range of batch.ranges) {
        const end = range.end * 3;
        const id = range.target?.id ?? `unattributed-${start}`;
        const tags = tagsById.get(id);
        if (tags)
            tagged++;
        let bottom = Infinity, top = -Infinity;
        for (let i = start; i < end; i++) {
            bottom = Math.min(bottom, position.getY(i));
            top = Math.max(top, position.getY(i));
        }
        const height = Math.max(top - bottom, top), variation = seed(id);
        const levels = tags?.['building:levels'] === undefined ? undefined : Number(tags['building:levels']);
        const material = tags?.['building:material'] ?? tags?.material;
        const type = tags?.building;
        const glass = material === 'glass' || (material === undefined && (height >= 28 || type === 'office' || type === 'commercial'));
        const brick = material === 'brick';
        const wallColor = tagColor(tags?.['building:colour'] ?? tags?.colour, glass ? '#788a96' : brick ? '#ad8b70' : variation > .5 ? '#c0bdb5' : '#b3b6b4');
        wallColor.multiplyScalar(0.91 + variation * .16);
        const roofColor = tagColor(tags?.['roof:colour'], brick ? '#68554b' : '#777f83');
        const pitch = levels !== undefined && Number.isFinite(levels) && levels > 0 && top > bottom ? (top - bottom) / levels : 3.2;
        const pitched = tags?.['roof:shape'] !== undefined && tags['roof:shape'] !== 'flat';
        for (let i = start; i < end; i++) {
            wallColor.toArray(wall, i * 3);
            roofColor.toArray(roof, i * 3);
            style.set([glass ? 1 : 0, Math.max(1.8, pitch), variation, pitched ? 1 : 0], i * 4);
        }
        start = end;
    }
    geometry.setAttribute('displayWallColor', new T.BufferAttribute(wall, 3));
    geometry.setAttribute('displayRoofColor', new T.BufferAttribute(roof, 3));
    geometry.setAttribute('displayFacadeStyle', new T.BufferAttribute(style, 4));
    const material = new T.MeshStandardMaterial({ color: '#ffffff', roughness: 0.72, metalness: 0.12 });
    const displayDusk = { value: dusk ? 1 : 0 };
    material.userData = { displayDusk, taggedRanges: tagged, displayEstimate: 'facade windows, finish and roof texture; original geometry retained' };
    material.customProgramCacheKey = () => 'procedural-facade-v2';
    material.onBeforeCompile = shader => {
        shader.uniforms.displayDusk = displayDusk;
        shader.vertexShader = `attribute vec3 displayWallColor;
attribute vec3 displayRoofColor;
attribute vec4 displayFacadeStyle;
varying vec3 facadePosition;
varying vec3 facadeNormal;
varying vec3 facadeWall;
varying vec3 facadeRoof;
varying vec4 facadeStyle;
` + shader.vertexShader;
        shader.vertexShader = shader.vertexShader.replace('#include <begin_vertex>', `#include <begin_vertex>
facadePosition=position;facadeNormal=normal;facadeWall=displayWallColor;facadeRoof=displayRoofColor;facadeStyle=displayFacadeStyle;`);
        shader.fragmentShader = `uniform float displayDusk;
varying vec3 facadePosition;
varying vec3 facadeNormal;
varying vec3 facadeWall;
varying vec3 facadeRoof;
varying vec4 facadeStyle;
float facadeHash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float facadeBox(float p,float lo,float hi,float width){return smoothstep(lo-width,lo+width,p)*(1.0-smoothstep(hi-width,hi+width,p));}
` + shader.fragmentShader;
        shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', `#include <color_fragment>
float roofFace=smoothstep(0.45,0.85,facadeNormal.y);
float horizontal=abs(facadeNormal.x)>abs(facadeNormal.z)?facadePosition.z:facadePosition.x;
vec2 cell=vec2(horizontal/(1.9+facadeStyle.z*0.8),facadePosition.y/facadeStyle.y);
vec2 f=fract(cell),aa=max(fwidth(cell),vec2(0.001));
float glass=facadeStyle.x;
float windowMask=facadeBox(f.x,mix(0.16,0.055,glass),mix(0.84,0.945,glass),aa.x)*facadeBox(f.y,mix(0.22,0.11,glass),0.85,aa.y);
windowMask*=1.0-roofFace;
float room=facadeHash(floor(cell)+floor(facadeStyle.z*1024.0+0.5)*3.0);
vec3 glazing=mix(vec3(0.075,0.12,0.155),vec3(0.27,0.38,0.47),room);
float reflectedSky=0.5+0.5*sin(facadePosition.y*0.035+facadeStyle.z*12.0);
glazing*=0.85+reflectedSky*0.3;
float floorBand=1.0-smoothstep(0.025-aa.y,0.025+aa.y,min(f.y,1.0-f.y));
vec3 finish=facadeWall*(1.0-floorBand*0.16);
vec3 facadeColor=mix(finish,glazing,windowMask);
vec2 roofCell=facadePosition.xz/3.0;
vec2 roofFract=fract(roofCell);
vec2 roofAA=max(fwidth(roofCell),vec2(0.001));
float roofSeam=1.0-smoothstep(0.018,0.018+max(roofAA.x,roofAA.y),min(min(roofFract.x,1.0-roofFract.x),min(roofFract.y,1.0-roofFract.y)));
float roofGrain=facadeHash(floor(facadePosition.xz*4.0));
float skylight=step(0.87,facadeHash(floor(roofCell)+floor(facadeStyle.z*1024.0+0.5)*3.0))*facadeBox(roofFract.x,0.25,0.75,roofAA.x)*facadeBox(roofFract.y,0.28,0.72,roofAA.y)*(1.0-facadeStyle.w);
vec3 roofFinish=facadeRoof*(0.94+roofGrain*0.12-roofSeam*0.18);
roofFinish=mix(roofFinish,vec3(0.16,0.24,0.3),skylight);
diffuseColor.rgb*=mix(facadeColor,roofFinish,roofFace);
float litWindow=windowMask*step(0.82,room)*displayDusk;`);
        shader.fragmentShader = shader.fragmentShader.replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
roughnessFactor=mix(roughnessFactor,0.24,windowMask);`);
        shader.fragmentShader = shader.fragmentShader.replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>
metalnessFactor=mix(metalnessFactor,0.35,windowMask);`);
        shader.fragmentShader = shader.fragmentShader.replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
totalEmissiveRadiance+=vec3(1.0,0.64,0.28)*litWindow*0.9;`);
    };
    return material;
}
