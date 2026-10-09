import {it,expect,vi,beforeEach,afterEach} from 'vitest';
import {recordedCameraPose,renderCapture} from './capture';
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
/** jsdom does not implement blob URLs; the code only needs a stable fake handle. */
class FakeBlobURL extends URL { static #counter = 0; static createObjectURL = () => `blob:probe-${++FakeBlobURL.#counter}`; static revokeObjectURL = () => {}; }
beforeEach(()=>{ vi.stubGlobal('URL',FakeBlobURL); });
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
 const blobs:string[]=[];
 const loaded=await loadAssets(ASSET_ID,'https://studio.example/v1/studio/demo-capture-assets',header(),new AbortController().signal,blobs);
 expect(loaded.scene?.city?.url).toMatch(/^blob:/);
 expect(fetch).toHaveBeenCalledTimes(3);
 expect(blobs.length).toBe(3);
 await expect(loadAssets('other-assets/v1','https://studio.example/v1/studio/demo-capture-assets',header(),new AbortController().signal,[])).rejects.toThrow('does not match the request');
 vi.stubGlobal('fetch',async()=>({ok:false,status:404}));
 await expect(loadAssets(ASSET_ID,'https://x/manifest',header(),new AbortController().signal,[])).rejects.toThrow('HTTP 404');
});
