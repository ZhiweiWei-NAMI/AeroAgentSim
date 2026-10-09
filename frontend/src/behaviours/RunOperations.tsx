import { useEffect, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { RunsApi } from '../feeds/http';
import { stringifyLossless } from '../feeds/lossless-json';
import { exactValue } from '../feeds/format';
import { mapping, type Draft } from './model';
import { TypedPayloadForm } from './TypedPayloadForm';
interface Props { api:RunsApi;runId:string;store:TemporalFeedStore;commitCut?:number;onSelect:(key:EntityKey)=>void;onSeek:(index:number)=>void;mode:'live'|'replay' }
export function advertisedMessages(store:TemporalFeedStore):Draft[] {
  const registry=store.header.runtimeRegistry;const rows=store.header.messages??(mapping(registry)?registry.messages:undefined);
  if(rows===undefined)return [];
  if(!Array.isArray(rows)||!rows.every(mapping))throw Error('Run message catalog is malformed');return rows;
}
export function injectionPointsFromScenario(scenario:Draft):Draft[] {
  const engines=mapping(scenario.engines)?Object.values(scenario.engines).filter(mapping):[];
  const owners=engines.filter(engine=>engine.plugin==='behaviour');
  const packages=scenario.behaviours??(owners.length===1&&mapping(owners[0].config)?owners[0].config.packages:undefined);
  if(packages===undefined)return [];
  if(!Array.isArray(packages)||!packages.every(mapping))throw Error('Pinned behaviour packages unavailable');
  const points=packages.flatMap(item=>{
    const document=mapping(item.document)?item.document:item;
    if('path' in document)throw Error('Run configuration contains unresolved behaviour references');
    const declared=document.injection_points;
    if(declared===undefined)return [];
    if(!Array.isArray(declared)||!declared.every(mapping))throw Error('Pinned injection points malformed');
    for(const point of declared)for(const key of ['id','stream_id','command','target','emits'])if(typeof point[key]!=='string'||!point[key])throw Error(`Injection point ${key} is required`);
    return declared;
  });
  if(new Set(points.map(point=>point.id)).size!==points.length)throw Error('Injection point IDs are ambiguous across packages');
  return points;
}
export function ingressBody(fields:Record<string,string>,payload:unknown):Draft {
  for(const key of ['schema','target','stream_id','at_ns','clock_id','mapping_id','numerator','denominator']) if(!fields[key])throw Error(`${key}: explicitly required`);
  for(const key of ['at_ns','numerator','denominator'])if(!/^-?(0|[1-9]\d*)$/.test(fields[key]))throw Error(`${key}: exact integer required`);
  if(BigInt(fields.at_ns)<0n||BigInt(fields.denominator)<1n)throw Error('Occurrence must be nonnegative; source denominator must be positive');
  return {schema:fields.schema,target:fields.target,stream_id:fields.stream_id,at_ns:{$integer:fields.at_ns},payload,source_stamp:{clock_id:fields.clock_id,mapping_id:fields.mapping_id,numerator:{$integer:fields.numerator},denominator:{$integer:fields.denominator}},...(fields.idempotency_key?{idempotency_key:fields.idempotency_key}:{})};
}
export function RunOperations({api,runId,store,onSelect,onSeek,mode}:Props) {
  const [fields,setFields]=useState<Record<string,string>>({schema:'',target:'',stream_id:'',at_ns:'',clock_id:'',mapping_id:'',numerator:'',denominator:'',idempotency_key:''}),[payload,setPayload]=useState<unknown>({}),[point,setPoint]=useState(''),[points,setPoints]=useState<Draft[]>([]),[error,setError]=useState(''),[admissions,setAdmissions]=useState<unknown[]>([]),[busy,setBusy]=useState(false),[operation,setOperation]=useState<Draft>(),[effective,setEffective]=useState<string>();
  const [payloadValid,setPayloadValid]=useState(true);
  const messages=advertisedMessages(store),commands=messages.filter(row=>row.kind==='command');const chosen=commands.find(row=>row.id===fields.schema),injection=points.find(row=>row.id===point);
  const event=injection&&messages.find(row=>row.id===injection.emits);
  const definitions=mapping(store.header.runtimeRegistry)&&mapping(store.header.runtimeRegistry.schemas)?store.header.runtimeRegistry.schemas:{};
  useEffect(()=>{const declared=store.header.behaviour?.injectionPoints??store.header.behaviour?.injection_points;if(declared){setPoints(declared);return;}
    const abort=new AbortController();void api.request(`/v1/studio/runs/${encodeURIComponent(runId)}/configuration`,{signal:abort.signal}).then(value=>{
      if(!mapping(value)||!mapping(value.scenario))throw Error('Pinned run configuration is malformed');
      if(!abort.signal.aborted)setPoints(injectionPointsFromScenario(value.scenario));
    }).catch(problem=>{if(!abort.signal.aborted)setError(String(problem));});return()=>abort.abort();
  },[api,runId,store]);
  useEffect(()=>{
    if(!operation||typeof operation.requested!=='string')return;
    const expected=operation.requested==='pause'?'paused':operation.requested==='resume'?'running':'stopped';let alive=true,timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const run=(await api.runs()).find(row=>row.id===runId);if(!run)throw Error('Operational run status unavailable');if(!alive)return;setEffective(run.status);if(run.status!==expected&&!['completed','faulted','interrupted','stopped'].includes(run.status))timer=setTimeout(()=>void poll(),1000);}catch(problem){if(alive)setError(String(problem));}};
    void poll();return()=>{alive=false;clearTimeout(timer);};
  },[api,runId,operation]);
  const submit=async()=>{if(!payloadValid){setError('Correct invalid typed payload before submitting');return;}setBusy(true);setError('');try{if(!chosen)throw Error('Select a registered command schema');const body=ingressBody(fields,injection?{injection_point:injection.id,payload}:payload);const receipt=await api.request(`/v1/runs/${encodeURIComponent(runId)}/ingress`,{method:'POST',headers:{'Content-Type':'application/json'},body:stringifyLossless(body)});setAdmissions(rows=>[...rows,receipt]);}catch(problem){setError(String(problem));}finally{setBusy(false);}};
  const control=async(action:string)=>{setError('');try{const reply=await api.request(`/v1/runs/${encodeURIComponent(runId)}/${action}`,{method:'POST'});if(!mapping(reply))throw Error('Malformed run control receipt');setOperation(reply);setEffective(typeof reply.status==='string'?reply.status:undefined);}catch(problem){setError(String(problem));}};
  return <section aria-label="Run operations"><h2>Operate / recorded decisions</h2><p>Occurrence, source acquisition, admission and execution are separate. Replay inspection sends no commands.</p>
    <div>{['pause','resume','stop'].map(action=><button key={action} disabled={mode!=='live'} onClick={()=>void control(action)}>Simulation {action}</button>)}</div>
    {operation&&<p>Requested: {String(operation.requested)} · last observed effective status: {effective??'not received'}{effective!==({pause:'paused',resume:'running',stop:'stopped'} as Record<string,string>)[String(operation.requested)]?' · pending at safe boundary':''}</p>}
    <details><summary>Inject event / issue typed command</summary><label>Injection point <select aria-label="Injection point" value={point} onChange={event=>{const id=event.target.value,next=points.find(row=>row.id===id);setPoint(id);setPayload({});if(next)setFields({...fields,schema:String(next.command),target:String(next.target),stream_id:String(next.stream_id)});}}><option value="">Direct typed command</option>{points.map(row=><option key={String(row.id)} value={String(row.id)}>{String(row.id)} → {String(row.emits)}</option>)}</select></label>
      {!points.length&&<p>No declared injection points available for this run.</p>}
      <label>Command schema <select aria-label="Command schema" value={fields.schema} disabled={!!injection} onChange={event=>{setFields({...fields,schema:event.target.value});setPayload({});}}><option value="">Select registered command</option>{commands.map(row=><option key={String(row.id)} value={String(row.id)}>{String(row.id)}</option>)}</select></label>
      {Object.keys(fields).filter(key=>key!=='schema').map(key=><label key={key}>{key}<input aria-label={`Ingress ${key}`} value={fields[key]} onChange={event=>setFields({...fields,[key]:event.target.value})}/></label>)}
      <TypedPayloadForm schema={injection?event?.schema:chosen?.schema} value={payload} onChange={setPayload} definitions={definitions} onValidityChange={setPayloadValid}/>
      <button disabled={busy||!payloadValid||mode!=='live'} onClick={()=>void submit()}>Submit typed ingress</button>
      <p>Cancellation support: {chosen?.cancel_support===true?'declared by command schema':'not advertised'}. This service has no general HTTP cancel transport. Use an authored cancellation injection point and inspect its actual child receipts.</p>
    </details>
    {error&&<p role="alert">{error}</p>}
    <details open={admissions.length>0}><summary>Admission receipts · {admissions.length}</summary>{admissions.map((receipt,index)=><pre key={index}>{exactValue(receipt)}</pre>)}</details>
    <details><summary>Execution receipts at this cut · {store.receipts.length}</summary>{store.receipts.map((receipt,index)=><pre key={index}>{exactValue(receipt)}</pre>)}</details>
    <details><summary>Decision / LangGraph and typed event records at this cut</summary>{store.messages.map(message=>{
      const commit=store.commits.find(row=>row.messages.some(item=>item.id===message.id));return <article key={message.id}><strong>{message.schemaId}</strong> · {message.at.ns}:{message.at.microstep}
        {commit&&<button onClick={()=>onSeek(commit.commitIndex)}>Seek record cut {commit.commitIndex}</button>}{message.subjects?.map(key=><button key={`${key.id}/${key.generation}`} onClick={()=>onSelect(key)}>{key.id} · g{key.generation}</button>)}<pre>{exactValue(message.payload)}</pre></article>;
    })}</details>
  </section>;
}
