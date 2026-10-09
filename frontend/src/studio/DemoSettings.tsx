import { useState } from 'react';
import type { StudioApi, Workspace } from './api';
export function DemoSettings({api,workspace,save,onApply,profiles}:{api:StudioApi;workspace:Workspace;save:()=>Promise<Workspace>;onApply:(workspace:Workspace)=>void;profiles:Record<string,Record<string,unknown>>}) {
 const [provider,setProvider]=useState({base_url:'',model:'',api_key_env:''}),[error,setError]=useState('');
 const apply=async(body:unknown)=>{setError('');try{await save();onApply(await api.request(`/v1/studio/workspaces/${workspace.id}/decision-profile`,body));}catch(problem){setError(String(problem));}};
 const live=workspace.scenario.engines?.decisions?.plugin==='langgraph';
 return <section aria-label="Traffic demo profiles"><h3>Decision mode · {live?'live LangGraph':'scripted'}</h3><p>{live?'Model proposals and provider failures are journaled as LangGraph records.':'Content-pinned scripted proposals; no model service calls.'} Profile edits require validation and a new run.</p>
 <button onClick={()=>void apply({mode:'scripted'})}>Use scripted decisions</button>
 <details><summary>Configure live LangGraph</summary>{Object.entries(provider).map(([key,value])=><label key={key}>{key}<input aria-label={`Live ${key}`} value={value} onChange={e=>setProvider({...provider,[key]:e.target.value})}/></label>)}<button disabled={Object.values(provider).some(value=>!value)} onClick={()=>void apply({provider})}>Use live LangGraph for next run</button></details>
 <h3>Plugin / ownership profiles</h3>{Object.entries(profiles).map(([id,profile])=><details key={id}><summary>{id} · {String(profile.readiness)}</summary><pre>{JSON.stringify(profile,null,2)}</pre></details>)}<p>Kinematic is the configured profile. SUMO/PX4 profiles list the required converters, writers and native capabilities; native deployment must pass full validation before a new run.</p>
 {error&&<p role="alert">{error}</p>}</section>;
}
