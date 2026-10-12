import {it,expect,vi,afterEach} from 'vitest';
import {captureError,recordedCameraPose,renderCapture} from './capture';
import { loadAssets } from './capture';
import type { RunHeader } from '../contracts/viewer-feed';

it('reads the traffic bridge flat pose snapshot without inventing a nested actor reference',()=>{
 const pose={id:'uav.bravo',generation:{$integer:'9007199254740993'},position:[12,34,70],field:'he.aircraft.position_enu_m',version:{journal_index:52,item_ordinal:0},acquired:{clock_id:'canonical'}};
 expect(recordedCameraPose(pose)).toEqual({key:{id:'uav.bravo',generation:'9007199254740993'},position:[12,34,70],field:'he.aircraft.position_enu_m'});
 expect(()=>recordedCameraPose({...pose,position:undefined})).toThrow('finite 3-vector');
});

const ASSET_ID='traffic-city-assets/v1';
function header(): RunHeader {
  return { contract:'aeroagentsim.viewer-feed/v1', runId:'run', epoch:'1', registryDigest:'traffic-accident/registry',
    types:[], fields:[], presentation:[], start:{ns:'0',microstep:0},
    scene:{ city:{ kind:'traffic-city', url:'https://studio.example/v1/studio/demo-assets/scene.json' } } } as unknown as RunHeader;
}
function manifest(files: unknown) {
  return { format:'aeroagentsim.capture-assets/v1', asset_id:ASSET_ID, environment:'viewer-default/v1', files };
}
afterEach(()=>{ vi.unstubAllGlobals(); });

it('requires a request asset ID before loading or rendering',async()=>{
 await expect(renderCapture({} as import('../feeds/http').RunsApi,'https://studio.example/manifest',{service_run_id:'run',request:{}},document.createElement('div'))).rejects.toThrow('asset set ID is missing');
});

it('loads plain-ID capture assets, ignores old hash annotations and checks request correlation',async()=>{
 const encode=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value)).slice().buffer as ArrayBuffer;
 const city='https://studio.example/v1/studio/demo-assets/scene.json';
 const building='https://studio.example/v1/studio/demo-assets/building.glb';
 const files=[{url:city,asset_id:'city@1',sha256:'obsolete',byte_count:0},{url:building,asset_id:'building@1'}];
 const fetch=vi.fn(async(address:string)=>({ok:true,arrayBuffer:async()=>encode(address.includes('demo-capture-assets')?manifest(files):address===city?{buildings:[{url:building}]}:'real mesh bytes')}));
 vi.stubGlobal('fetch',fetch);
 const loaded=await loadAssets(ASSET_ID,'https://studio.example/v1/studio/demo-capture-assets',header(),new AbortController().signal);
 expect(loaded.scene?.city?.url).toBe(city);
 expect(fetch).toHaveBeenCalledTimes(2);
 await expect(loadAssets('other-assets/v1','https://studio.example/v1/studio/demo-capture-assets',header(),new AbortController().signal)).rejects.toThrow('does not match the request');
 vi.stubGlobal('fetch',async()=>({ok:false,status:404}));
 await expect(loadAssets(ASSET_ID,'https://x/manifest',header(),new AbortController().signal)).rejects.toThrow('HTTP 404');
});

it('loads public OSM footprints and keeps the console procedural actors for city captures',async()=>{
 const city=header().scene!.city!.url;
 const bundle={format:'traffic-city-lite/v1',frame:'enu',buildings:[{id:'osm.1',footprint:[[0,0],[8,0],[8,6],[0,6]],height_m:18}],roads:[]};
 const encode=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value)).slice().buffer as ArrayBuffer;
 const fetch=vi.fn(async(address:string)=>({ok:true,arrayBuffer:async()=>encode(address===city?bundle:{...manifest([{url:city,asset_id:'traffic-lite-city/v1'}]),asset_id:'traffic-lite-city/v1'})}));
 vi.stubGlobal('fetch',fetch);
 const input=header(); input.presentation=[{typeId:'aas:TrafficUAV',positionField:'position',frame:'enu',visual:{kind:'model',asset:'procedural:uav'}}];
 const result=await loadAssets('traffic-lite-city/v1','https://studio.example/manifest',input,new AbortController().signal);
 expect(result.scene?.city?.url).toBe(city);
 expect(result.presentation[0].visual.asset).toBe('procedural:uav');
 expect(fetch).toHaveBeenCalledTimes(2);
});

it('localizes capture diagnostics for the UI without translating opaque IDs or changing protocol errors',()=>{
 const original=new Error('Capture required pose missing: uav.bravo.position_enu_m');
 expect(captureError(original,'zh-CN')).toBe('缺少拍摄所需的位置：uav.bravo.position_enu_m');
 expect(original.message).toBe('Capture required pose missing: uav.bravo.position_enu_m');
 expect(captureError(new Error('Capture asset manifest HTTP 404'),'zh-CN')).toBe('无法获取拍摄资源清单（HTTP 404）');
 expect(captureError(new Error('Capture timed out'),'zh-CN')).toBe('拍摄超时');
 expect(captureError(original,'en-US')).toBe(original.message);
});

it('keeps detailed actor model URLs so GLTF relative textures resolve at the service', async () => {
 const city=header().scene!.city!.url;
 const model='https://studio.example/v1/studio/demo-assets/models/uav.glb';
 const input=header(); input.presentation=[{typeId:'aas:TrafficUAV',positionField:'position',frame:'enu',visual:{kind:'model',asset:model}}];
 const encode=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value)).slice().buffer as ArrayBuffer;
 vi.stubGlobal('fetch',async(address:string)=>({ok:true,arrayBuffer:async()=>encode(address===city?{buildings:[]} : address.includes('manifest')?manifest([{url:city},{url:model}]):'model bytes')}));
 const result=await loadAssets(ASSET_ID,'https://studio.example/manifest',input,new AbortController().signal);
 expect(result.presentation[0].visual.asset).toBe(model);
});

it('validates unused inventory entries without downloading unrelated or obsolete models', async () => {
 const city=header().scene!.city!.url;
 const encode=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value)).slice().buffer as ArrayBuffer;
 const fetch=vi.fn(async(address:string)=>({ok:true,arrayBuffer:async()=>encode(address===city?{buildings:[]} : manifest([{url:city},{url:'https://studio.example/unused.glb'}]))}));
 vi.stubGlobal('fetch',fetch);
 await loadAssets(ASSET_ID,'https://studio.example/manifest',header(),new AbortController().signal);
 expect(fetch.mock.calls.map(call=>call[0])).toEqual(['https://studio.example/manifest',city]);
});

it('rejects buildings outside the declared inventory before rendering a partial city', async () => {
 const city=header().scene!.city!.url;
 const encode=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value)).slice().buffer as ArrayBuffer;
 vi.stubGlobal('fetch',async(address:string)=>({ok:true,arrayBuffer:async()=>encode(address===city?{buildings:[{url:'/assets/missing.glb'}]} : manifest([{url:city}]))}));
 await expect(loadAssets(ASSET_ID,'https://studio.example/manifest',header(),new AbortController().signal)).rejects.toThrow('absent from the manifest');
});

import {obliqueCamera,obliqueCaption} from './capture';
function oblique(){
 const eye:[number,number,number]=[91.5,-90.7,70],subject={id:'incident.01',generation:0,field:'traffic.incident.position_enu_m',position:[91.9,-93,0]};
 const radius=70/Math.tan(50*Math.PI/180),bearing=Math.atan2(-2.3,0.4);
 return {revision:'uav-gimbal-oblique/v1',frame:'enu',pitch_deg:50,fov:96,coincident_azimuth_deg:-90,target_field:subject.field,eye,target:[eye[0]+radius*Math.cos(bearing),eye[1]+radius*Math.sin(bearing),0],subject};
}
const actor={id:'uav.bravo',generation:'0'};
it('checks an oblique camera against its actual actor and nonvisual incident facts',()=>{
 const camera=oblique(), sample=(key:{id:string},field:string)=>key.id===actor.id&&field==='pose'?camera.eye:key.id==='incident.01'&&field===camera.target_field?camera.subject.position:undefined;
 const resolved=obliqueCamera(camera,actor,'pose',sample);
 expect(resolved).toMatchObject({eye:camera.eye,subject:{key:{id:'incident.01',generation:'0'}}});resolved.target.forEach((v,i)=>expect(v).toBeCloseTo(camera.target[i],8));
 expect(()=>obliqueCamera({...camera,eye:[0,0,70]},actor,'pose',sample)).toThrow('committed actor pose');
 expect(()=>obliqueCamera(camera,actor,'pose',(key,field)=>key.id==='incident.01'?[0,0,0]:sample(key,field))).toThrow();
 expect(()=>obliqueCamera({...camera,pitch_deg:45},actor,'pose',sample)).toThrow('declared pitch/bearing');
 expect(()=>obliqueCamera({...camera,fov:30},actor,'pose',sample)).toThrow('outside the sensor field');
});
it('keeps bilingual photo captions tied to measured ENU pose, incident and exact cut',()=>{
 const caption=obliqueCaption({camera:oblique(),actor,source_cut:{index:{$integer:'9007199254740993'}}});
 expect(caption[0]).toBe('uav.bravo · ENU (91.5, -90.7, 70.0) m · gimbal 50° down · cut #9007199254740993');
 expect(caption[1]).toBe('目标 / Target incident.01 · ENU (91.9, -93.0, 0.0) m · 已提交位置 / committed poses');
});

it('resolves a semantic target at the read cut without adding a scheduling dependency or synthetic pose',()=>{
 const camera=oblique();
 const declaration={...camera,eye:undefined,target:undefined,subject:{id:camera.subject.id,generation:0,field:camera.subject.field}};
 const sample=(key:{id:string})=>key.id===actor.id?camera.eye:camera.subject.position;
 const first=obliqueCamera(declaration,actor,'pose',sample);expect(first.eye).toEqual(camera.eye);first.target.forEach((v,i)=>expect(v).toBeCloseTo(camera.target[i],8));
 expect(()=>obliqueCamera(declaration,actor,'pose',key=>key.id===actor.id?camera.eye:undefined)).toThrow('finite 3-vector');
 const overhead={...declaration,subject:{id:'incident.01',generation:0,field:camera.subject.field}};
 const resolved=obliqueCamera(overhead,actor,'pose',key=>key.id===actor.id?camera.eye:[camera.eye[0],camera.eye[1],0]);
 expect(resolved.target[0]).toBeCloseTo(camera.eye[0]);expect(resolved.target[1]).toBeLessThan(camera.eye[1]);
 expect(()=>obliqueCamera({...overhead,coincident_azimuth_deg:undefined},actor,'pose',key=>key.id===actor.id?camera.eye:[camera.eye[0],camera.eye[1],0])).toThrow('explicit azimuth');
});
