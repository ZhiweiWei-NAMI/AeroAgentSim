import {clone,validateConfig,compileConfig,diffConfig} from './config.js';

export const STORAGE_KEY='aero-console.workspace.v1';
export const RUNTIME_SCHEMA='aero-console.workspace/v1';
export const ADAPTERS=Object.freeze([
  {id:'fixture',name:'Synthetic fixture',owner:'Console',status:'ready',transport:'In-memory immutable snapshots',note:'Independently authored fixture. Playback advances a view cursor only.'},
  {id:'bench',name:'AERO_BENCH',owner:'Physical authority',status:'not_connected',transport:'Authenticated fetch SSE / sealed replay',note:'Future host is the existing BENCH Three.js viewer. No harness commands are sent.'},
  {id:'sumo',name:'SUMO mobility',owner:'BENCH motion provider',status:'not_connected',transport:'BENCH-owned RPC',note:'One source clock; this console never calls simulationStep.'},
  {id:'ns3',name:'ns-3 network',owner:'BENCH network provider',status:'not_connected',transport:'ns3.state.v4 / network frame / link properties',note:'Configured rates, aggregate receipts, and per-link observations remain separate.'},
  {id:'atlas',name:'Atlas semantic engine',owner:'Evidence projection',status:'not_connected',transport:'Binding-isolated evaluator context',note:'No runtime or full catalog is bundled. Unknown means not evaluated, not false.'},
]);
export function canonicalJSON(value){if(value===null||typeof value!=='object')return JSON.stringify(value);if(Array.isArray(value))return '['+value.map(canonicalJSON).join(',')+']';return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonicalJSON(value[k])).join(',')+'}';}
export async function sha256(value){const bytes=new TextEncoder().encode(typeof value==='string'?value:canonicalJSON(value));const hash=await globalThis.crypto.subtle.digest('SHA-256',bytes);return [...new Uint8Array(hash)].map(x=>x.toString(16).padStart(2,'0')).join('');}
export function safeImport(text){if(text.length>2_000_000)throw new Error('Configuration exceeds 2 MB.');const value=JSON.parse(text,(key,val)=>{if(['__proto__','constructor','prototype'].includes(key))throw new Error('Unsafe object key.');return val;});const config=value?.schema_version==='aero-console.export/v1'?value.config:value;const validation=validateConfig(config);if(!validation.valid)throw new Error(validation.errors.map(e=>`${e.path}: ${e.message}`).join('\n'));return clone(config);}
export function exportConfig(config){return {schema_version:'aero-console.export/v1',exported_at:new Date().toISOString(),mode:'local-fixture',config:clone(config)};}
export function saveWorkspace(storage,workspace){const prior=storage.getItem(STORAGE_KEY);if(prior){try{if(JSON.parse(prior)?.schema_version!==RUNTIME_SCHEMA)storage.setItem(STORAGE_KEY+'.recovery',prior);}catch{storage.setItem(STORAGE_KEY+'.recovery',prior);}}storage.setItem(STORAGE_KEY,JSON.stringify({...workspace,schema_version:RUNTIME_SCHEMA}));}
function renderableDraft(d){return d&&typeof d==='object'&&d.metadata&&d.scenario&&Array.isArray(d.entities)&&d.entities.every(e=>e&&Array.isArray(e.position_enu_m))&&d.mobility?.sumo&&d.mobility?.uav&&d.network?.link&&Array.isArray(d.network.radio_profiles)&&d.network.radio_profiles.every(p=>p&&typeof p==='object')&&d.compute&&Array.isArray(d.compute.profiles)&&d.compute.profiles.every(p=>p&&typeof p==='object')&&d.semantics&&Array.isArray(d.semantics.bindings)&&d.semantics.bindings.every(b=>b&&typeof b==='object');}
function renderableRun(r){
  try{
    const vec=v=>v===null||(Array.isArray(v)&&v.length===3&&v.every(x=>Number.isFinite(x)&&Math.abs(x)<=1e9));
    if(r?.schema_version!=='aero-console.fixture-run/v1'||r.mode!=='synthetic-fixture'||typeof r.id!=='string'||typeof r.name!=='string'||typeof r.created_at!=='string'||!validateConfig(r.config).valid||!/^[a-f0-9]{64}$/.test(r.config_digest)||!Array.isArray(r.frames)||r.frames.length<1||r.frames.length>121||!Number.isFinite(r.duration_s)||r.duration_s<=0||!Array.isArray(r.events)||r.events.some(e=>!e||!Number.isSafeInteger(e.tick)||typeof e.type!=='string'||typeof e.message!=='string'))return false;
    let lastTick=0,lastTime=-1;
    for(const f of r.frames){
      if(!f||!Number.isSafeInteger(f.tick)||f.tick<=lastTick||!Number.isFinite(f.relative_time_s)||f.relative_time_s<=lastTime||!/^\d+$/.test(f.sim_time_ns)||!/^[a-f0-9]{64}$/.test(f.hash)||!f.stages||!Array.isArray(f.entities)||f.entities.length>250||!f.entities.length)return false;
      if(f.entities.some(e=>!e||typeof e.entity_id!=='string'||!['uav','vehicle','pedestrian','base_station','edge','cloud'].includes(e.type)||!vec(e.position_enu_m)||!vec(e.velocity_enu_mps)||!['present','unavailable'].includes(e.validity)))return false;
      lastTick=f.tick;lastTime=f.relative_time_s;
    }
    return r.duration_s===lastTime;
  }catch{return false;}
}
export function loadWorkspace(storage,fallback){
  try{
    const raw=storage.getItem(STORAGE_KEY);if(!raw)return {draft:clone(fallback),versions:[],runs:[],warning:null};
    const saved=JSON.parse(raw,(key,value)=>{if(['__proto__','constructor','prototype'].includes(key))throw new Error('Unsafe saved key');return value;});
    if(saved.schema_version!==RUNTIME_SCHEMA)throw new Error('Unsupported workspace');
    const versions=Array.isArray(saved.versions)?saved.versions.filter(v=>v&&v.config&&validateConfig(v.config).valid):[];
    const runs=Array.isArray(saved.runs)?saved.runs.filter(renderableRun).map(r=>({...r,integrity:'unverified-local-cache'})):[];
    const draft=renderableDraft(saved.draft)?clone(saved.draft):clone(fallback);
    const skipped=(saved.versions?.length||0)-versions.length+(saved.runs?.length||0)-runs.length;
    const warning=skipped?`已跳过 ${skipped} 条损坏记录，保留其余有效版本与运行。`:validateConfig(draft).valid&&renderableDraft(saved.draft)?null:'已保留版本与运行记录。草稿有待修正字段或结构，请检查配置。';
    return {draft,versions,runs,warning};
  }catch{return {draft:clone(fallback),versions:[],runs:[],warning:'本地保存内容无法读取，已打开默认配置。原始存储未覆盖。'};}
}
export function createVersion(config,versions,note=''){const checked=validateConfig(config);if(!checked.valid)throw new Error('Fix validation errors before saving a version.');return {id:`version-${Date.now()}-${versions.length+1}`,number:versions.length+1,created_at:new Date().toISOString(),note:note||'配置快照',config:clone(config)};}
export function expandedEntities(config){let remaining=10000;return config.entities.flatMap(entity=>{const count=Number.isSafeInteger(entity.count)?Math.max(0,Math.min(entity.count,remaining)):0;remaining-=count;return Array.from({length:count},(_,i)=>({...clone(entity),entity_id:entity.count===1?entity.id:`${entity.id}-${i+1}`,template_id:entity.id,instance_index:i}));});}
/** Authored fixture snapshots, not a simulation. Offsets are visual demonstration paths. */
export async function createFixtureRun(config){
  const validation=validateConfig(config);if(!validation.valid)throw new Error('Invalid configuration');
  if(config.entities.reduce((sum,e)=>sum+e.count,0)>250)throw new Error('本地合成回放最多支持 250 个实体；完整配置仍可保存和导出。');
  const compiled=compileConfig(config);const snapshot=clone(config);const digest=await sha256(snapshot);const instances=expandedEntities(snapshot);
  if(config.scenario.step_ms>120000)throw new Error('本地合成回放的步长上限为 120 秒；配置仍可保存和导出。');
  const duration=Math.min(config.scenario.duration_s,120);const lastAllowedTick=Math.floor(duration*1000/config.scenario.step_ms);const frameCount=Math.min(Math.ceil(duration)+1,121);
  const frames=[];
  for(let i=0;i<frameCount;i++){
    const seconds=Math.min(i,duration);const tick=Math.max(1,Math.min(lastAllowedTick,Math.round(seconds*1000/config.scenario.step_ms)));
    if(frames.at(-1)?.tick===tick)continue;
    const elapsed=tick*config.scenario.step_ms/1000;
    const entities=instances.map((entity,j)=>{
      const position=entity.position_enu_m||[0,0,0];const moving=['uav','vehicle','pedestrian'].includes(entity.type);const factor=entity.type==='uav'?1:entity.type==='vehicle'?.65:.13;
      const offset=moving?Math.min(elapsed,45)*factor:0;
      const missing=entity.type==='uav'&&elapsed>=24&&elapsed<28;
      return {entity_id:entity.entity_id,type:entity.type,generation:0,position_enu_m:missing?null:[position[0]+offset*2+entity.instance_index*8,position[1]+offset*.7+entity.instance_index*6,position[2]],velocity_enu_mps:missing?null:[moving&&elapsed<45?factor*2:0,moving&&elapsed<45?factor*.7:0,0],source_provider_id:'fixture.authored',provenance:'hypothetical',validity:missing?'unavailable':'present',reason:missing?'authored evidence gap':null};
    });
    const frame={schema_version:'aero-console.fixture-frame/v1',tick,sim_time_ns:(BigInt(tick)*BigInt(config.scenario.step_ms)*1_000_000n).toString(),relative_time_s:elapsed,entities,stages:{motion:'fixture',network:'unavailable',compute:'unavailable',semantics:'not_evaluated'}};
    frame.hash=await sha256(frame);frames.push(frame);
  }
  return {schema_version:'aero-console.fixture-run/v1',integrity:'generated-local-not-authenticated',id:`fixture-${Date.now()}`,mode:'synthetic-fixture',status:'sealed',created_at:new Date().toISOString(),name:snapshot.metadata.name,config_digest:digest,config:snapshot,manifest_revision:1,epoch:'fixture-epoch-1',evaluation_revision:null,compiler:compiled,frames,events:[{tick:frames[0].tick,type:'fixture.prepared',message:'Configuration frozen; authored replay snapshots prepared.'},...(frames.some(f=>f.entities.some(e=>e.validity==='unavailable'))?[{tick:frames.find(f=>f.entities.some(e=>e.validity==='unavailable')).tick,type:'evidence.gap',message:'Authored UAV evidence gap. Missing values stay unknown.'}]:[]),{tick:frames.at(-1).tick,type:'fixture.sealed',message:'Fixture record sealed locally. No external simulation was started.'}],source_cursor:null,host_contract:{physical_authority:'AERO_BENCH (not connected)',renderer_host:'BENCH Three.js (not mounted)',clock:'view-cursor-only',atlas:'not connected'},duration_s:frames.at(-1).relative_time_s,requested_duration_s:config.scenario.duration_s,fixture_window_s:duration,truncated:config.scenario.duration_s>duration};
}
export function seekFrame(run,seconds){if(!run||!run.frames.length)return null;const clamped=Math.min(Math.max(0,seconds),run.frames.at(-1).relative_time_s);let result=run.frames[0];for(const frame of run.frames){if(frame.relative_time_s>clamped)break;result=frame;}return result;}
export function selectionKey(run,frame,entityId){return {attachment_id:run.id,run_id:run.id,epoch:run.epoch,manifest_revision:run.manifest_revision,frame_sequence:frame.tick,frame_hash:frame.hash,evaluation_revision:run.evaluation_revision,binding_epoch:'unbound',entity_id:entityId};}
export function semanticEvidence(config,frame,entityId){return config.semantics.bindings.filter(b=>b.entity_id===entityId).map(binding=>({binding_id:binding.id,target:binding.target,truth:'unknown',reason:'Atlas runtime is not connected; no evaluation has been executed.',parameters:clone(binding.parameters),frame_tick:frame.tick,frame_hash:frame.hash,evidence:frame.entities.find(e=>e.entity_id===entityId)||null}));}
export function versionDiff(config,version){return version?diffConfig(version.config,config):[];}
