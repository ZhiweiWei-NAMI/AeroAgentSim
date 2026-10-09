import { useEffect, useState, useRef, useMemo } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { awaitRunConfiguration, RunsApi, isTerminalStatus } from '../feeds/http';
import { stringifyLossless } from '../feeds/lossless-json';
import { Details } from '../console/Details';
import { RunShortcuts } from '../console/shortcuts';
import { useConsoleNotice } from '../console/Notifications';
import { displayTime } from '../pages/display-time';
import { ConceptHelp } from '../console/ConceptHelp';
import { entityLabel, readableLabel } from '../pages/inspection-format';
import { exactValue } from '../feeds/format';
import { mapping, type Draft } from './model';
import { TypedPayloadForm } from './TypedPayloadForm';
import { actualTargetSummary, emptyTransport, newIdempotencyKey, receiptSummary, schemaDefaults, transportForPoint } from './injection-defaults';
import './run-operations.css';
interface Props { api:RunsApi;runId:string;store:TemporalFeedStore;selected?:EntityKey;commitCut?:number;onSelect:(key:EntityKey)=>void;onSeek:(index:number)=>void;mode:'live'|'replay';interactive?:boolean }
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
  if(!Array.isArray(packages)||!packages.every(mapping))throw Error('Behaviour packages unavailable');
  const points=packages.flatMap(item=>{
    const document=mapping(item.document)?item.document:item;
    if('path' in document)throw Error('Run configuration contains unresolved behaviour references');
    const declared=document.injection_points;
    if(declared===undefined)return [];
    if(!Array.isArray(declared)||!declared.every(mapping))throw Error('Injection points malformed');
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
export function operatorTiming(scenario:Draft):{step:bigint;initial:bigint} {
  const exact=(value:unknown,label:string)=>{
    const text=typeof value==='number'&&Number.isSafeInteger(value)?String(value):mapping(value)?value.$integer:undefined;
    if(typeof text!=='string'||!/^(0|[1-9]\d*)$/.test(text))throw Error(`${label}: declared exact nonnegative integer required`);
    return BigInt(text);
  };
  const stream=Array.isArray(scenario.ingress_streams)?scenario.ingress_streams.find(row=>mapping(row)&&row.id==='operator'):undefined;
  if(!mapping(scenario.run)||!mapping(stream))throw Error('Operator timing requires the authored run and ingress source');
  const step=exact(scenario.run.advance_ns,'Run advance');if(step===0n)throw Error('Run advance must be positive');
  return {step,initial:exact(stream.initial_watermark_ns,'Operator initial watermark')};
}
export function recordedAccidentPayload(store:TemporalFeedStore):Draft|undefined {
  if(!store.header.kernelRunId||store.header.epoch===undefined)return undefined;
  const incident=[...store.entities.values()].find(row=>row.typeId==='aas:TrafficIncident');
  if(!incident)return undefined;
  return {incident:{$ref:{run_id:store.header.kernelRunId,epoch:store.header.epoch,id:incident.key.id,generation:{$integer:String(incident.key.generation)},type_id:incident.typeId}}};
}
export function RunOperations({api,runId,store,selected,onSelect,onSeek,mode,interactive=true}:Props) {
  const notify=useConsoleNotice();
  const cardRef=useRef<HTMLDivElement>(null),injectionRef=useRef<HTMLSelectElement>(null),pointInitialized=useRef(false);
  const [fields,setFields]=useState(emptyTransport()),[payload,setPayload]=useState<unknown>({}),[point,setPoint]=useState(''),[points,setPoints]=useState<Draft[]>([]),[error,setError]=useState(''),[admissions,setAdmissions]=useState<unknown[]>([]),[busy,setBusy]=useState(false),[operation,setOperation]=useState<Draft>(),[effective,setEffective]=useState<string>();
  const [operator,setOperator]=useState(false),[horizon,setHorizon]=useState('0');
  const operatorNs=useRef(0n), operatorStep=useRef(1000000000n), operatorEnd=useRef<bigint>(), operations=useRef<Promise<unknown>>(Promise.resolve());
  const serialized=(action:()=>Promise<unknown>)=>{const result=operations.current.then(action,action);operations.current=result;return result;};
  const [payloadValid,setPayloadValid]=useState(true);
  const [showInternal,setShowInternal]=useState(false),[recordPage,setRecordPage]=useState(0);
  const records=useMemo(()=>store.messages.filter(message=>showInternal||message.schemaId.startsWith('traffic.')||['aas.langgraph.record','aas.runtime.inject_event'].includes(message.schemaId)),[store,store.messages.length,store.viewCursor?.knownAt,showInternal]);
  const recordPages=Math.max(1,Math.ceil(records.length/50)),shownPage=Math.min(recordPage,recordPages-1);
  const recordCuts=useMemo(()=>new Map(store.commits.flatMap(commit=>commit.messages.map(message=>[message.id,commit.commitIndex] as const))),[store,store.commits.length]);

  const messages=advertisedMessages(store),commands=messages.filter(row=>row.kind==='command');const chosen=commands.find(row=>row.id===fields.schema),injection=points.find(row=>row.id===point);
  const event=injection&&messages.find(row=>row.id===injection.emits);
  const definitions=mapping(store.header.runtimeRegistry)&&mapping(store.header.runtimeRegistry.schemas)?store.header.runtimeRegistry.schemas:{};
  useEffect(()=>{
    if(!injection||!mapping(payload)||payload.actor!==undefined||!selected||!store.header.kernelRunId||store.header.epoch===undefined)return;
    const descriptor=typeof event?.schema==='string'?definitions[event.schema]:event?.schema;
    const actor=mapping(descriptor)&&mapping(descriptor.members)&&mapping(descriptor.members.actor)?descriptor.members.actor:undefined;
    const entity=[...store.entities.values()].find(row=>row.key.id===selected.id&&row.key.generation===selected.generation);
    if(!actor||!entity)return;
    const type=store.header.types.find(row=>row.typeId===entity.typeId);
    if(entity.typeId!==actor.target_type&&!type?.ancestors.includes(String(actor.target_type)))return;
    setPayload((previous:unknown)=>({...(mapping(previous)?previous:{}),actor:{$ref:{run_id:store.header.kernelRunId,epoch:store.header.epoch,id:entity.key.id,generation:{$integer:String(entity.key.generation)},type_id:entity.typeId}}}));
  },[injection,event,selected,store,definitions]);

  useEffect(()=>{
    if(!injection)return;
    const defaults=schemaDefaults(event?.schema,definitions);
    setPayload(mapping(defaults)?{...defaults,...(point==='accident'?recordedAccidentPayload(store):{})}:point==='accident'?(recordedAccidentPayload(store)??{}):{});
  },[point,event?.schema]);
  const friendly=()=>{cardRef.current?.scrollIntoView({block:'center'});cardRef.current?.querySelector<HTMLButtonElement>('[data-injection-submit]')?.focus();};
  useEffect(()=>{const declared=store.header.behaviour?.injectionPoints??store.header.behaviour?.injection_points;if(declared)setPoints(declared);
    const abort=new AbortController();void awaitRunConfiguration(api, runId, abort.signal).then(value=>{
      if(!mapping(value)||!mapping(value.scenario))throw Error('Run configuration is malformed');
      if(!abort.signal.aborted){setPoints(injectionPointsFromScenario(value.scenario));if(value.scenario.id==='traffic-accident'){const timing=operatorTiming(value.scenario);operatorStep.current=timing.step;const until=mapping(value.scenario.run)?value.scenario.run.until_ns:undefined;const end=typeof until==='number'&&Number.isSafeInteger(until)?String(until):mapping(until)?until.$integer:undefined;if(typeof end!=='string'||!/^(0|[1-9]\d*)$/.test(end))throw Error('Operator run limit must be a declared exact nonnegative integer');operatorEnd.current=BigInt(end);const recorded=store.commits.at(-1);operatorNs.current=recorded&&BigInt(recorded.at.ns)>timing.initial?BigInt(recorded.at.ns):timing.initial;setHorizon(operatorNs.current.toString());setOperator(true);}}
    }).catch(problem=>{if(!abort.signal.aborted)setError(String(problem));});return()=>abort.abort();
  },[api,runId,store]);
  // Select the single authored point by default and prefill its transport from
  // the authored declaration; missing operator timing stays empty (no invention).
  useEffect(()=>{if(pointInitialized.current||!points.length)return;pointInitialized.current=true;const only=points.length===1?points[0]:points.find(row=>row.id==='accident');if(only)setPoint(String(only.id));},[points]);
  useEffect(()=>{if(!injection)return;setFields(transportForPoint(injection,{operator,operatorNs:operatorNs.current}));},[injection,operator]);
  useEffect(()=>{
    if(point!=='accident'||!mapping(payload)||'incident' in payload)return;
    const recorded=recordedAccidentPayload(store);if(recorded)setPayload((previous:unknown)=>({...(mapping(previous)?previous:{}),...recorded}));
  },[point,payload,store,store.viewCursor?.knownAt]);
  useEffect(()=>{
    if(!operator||mode!=='live')return;
    let active=true;
    // Source progress is acknowledged independently of event production: an idle
    // closed prefix need not have a journal event at its right boundary.
    const timer=setInterval(()=>{if(!store.commits.length)return;
      void serialized(async()=>{if(!active||operatorEnd.current===undefined||operatorNs.current>=operatorEnd.current)return;const next=operatorNs.current+operatorStep.current>operatorEnd.current?operatorEnd.current:operatorNs.current+operatorStep.current;const reply=await api.request(`/v1/runs/${encodeURIComponent(runId)}/watermark`,{method:'POST',headers:{'Content-Type':'application/json'},body:stringifyLossless({stream_id:'operator',watermark_ns:{$integer:next.toString()}})});if(!mapping(reply)||String(reply.watermark_ns)!==next.toString())throw Error('Operator watermark receipt mismatch');operatorNs.current=next;setHorizon(next.toString());}).catch(problem=>{if(active){setError(String(problem));setOperator(false);}});
    },1000);
    return()=>{active=false;clearInterval(timer);};
  },[operator,mode,api,runId,store]);
  useEffect(()=>{
    if(!operation||typeof operation.requested!=='string')return;
    const expected=operation.requested==='pause'?'paused':operation.requested==='resume'?'running':'stopped';let alive=true,timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const run=(await api.runs()).find(row=>row.id===runId);if(!run)throw Error('Operational run status unavailable');if(!alive)return;setEffective(run.status);if(run.status!==expected&&!(operation.requested==='resume'&&run.status==='waiting_for_input')&&!isTerminalStatus(run.status))timer=setTimeout(()=>void poll(),1000);}catch(problem){if(alive)setError(String(problem));}};
    void poll();return()=>{alive=false;clearTimeout(timer);};
  },[api,runId,operation]);
  const submit=async()=>{
    if(!payloadValid){setError('Correct the payload before submitting');return;}
    setBusy(true);setError('');
    try{
      if(!chosen)throw Error('Select a registered command');
      const effective=operator?{...fields,at_ns:(operatorNs.current+1n).toString(),clock_id:'canonical',mapping_id:'canonical',numerator:(operatorNs.current+1n).toString(),denominator:'1'}:fields;
      if(!effective.idempotency_key)effective.idempotency_key=newIdempotencyKey();
      const descriptor=typeof event?.schema==='string'?definitions[event.schema]:event?.schema;
      const members=mapping(descriptor)&&mapping(descriptor.members)?descriptor.members:{};
      const eventPayload=mapping(payload)?{...payload}:payload;
      if(injection&&mapping(eventPayload)){
        if('request_id' in members&&!eventPayload.request_id)eventPayload.request_id=newIdempotencyKey();
        if('source_cut' in members&&eventPayload.source_cut===undefined&&store.viewCursor&&store.viewCursor.knownAt>=0)eventPayload.source_cut={$integer:String(store.viewCursor.knownAt)};
      }
      const body=ingressBody(effective,injection?{injection_point:injection.id,payload:eventPayload}:payload);
      const receipt=await api.request(`/v1/runs/${encodeURIComponent(runId)}/ingress`,{method:'POST',headers:{'Content-Type':'application/json'},body:stringifyLossless(body)});
      setAdmissions(rows=>[...rows,receipt]);notify({kind:'info',message:mapping(receipt)&&receipt.disposition==='accepted'?'Event accepted. Follow its response in the timeline.':'Event receipt received. Check the result below.'});
    }catch(problem){setError(String(problem));notify({kind:'error',message:String(problem)});}finally{setBusy(false);}
  };
  const control=async(action:string)=>{setError('');try{const reply=await api.request(`/v1/runs/${encodeURIComponent(runId)}/${action}`,{method:'POST'});if(!mapping(reply))throw Error('Malformed run control receipt');setOperation(reply);setEffective(typeof reply.status==='string'?reply.status:undefined);notify({kind:'info',message:`Simulation ${action} requested.`});}catch(problem){setError(String(problem));notify({kind:'error',message:String(problem)});}};
  const toggle=async()=>{try{const run=(await api.runs()).find(row=>row.id===runId);if(!run)throw Error('Run status unavailable');if(run.status==='running'||run.status==='waiting_for_input')await control('pause');else if(run.status==='paused')await control('resume');else notify({kind:'info',message:`Run is ${run.status}; pause/resume is unavailable.`});}catch(problem){setError(String(problem));notify({kind:'error',message:String(problem)});}};
  const setPoint_=(id:string)=>{const next=points.find(row=>row.id===id);setPoint(id);setPayload(id==='accident'?(recordedAccidentPayload(store)??{}):{});setFields(next?transportForPoint(next,{operator,operatorNs:operatorNs.current}):emptyTransport());};
  const ready=point!=='accident'||mapping(payload)&&mapping(payload.incident);
  const descriptor=typeof event?.schema==='string'?definitions[event.schema]:event?.schema;
  const payloadSchema=mapping(descriptor)&&mapping(descriptor.members)?{...descriptor,members:Object.fromEntries(Object.entries(descriptor.members).filter(([key])=>!['request_id','source_cut'].includes(key)))}:descriptor;
  const choices=[...store.entities.values()].map(entity=>({label:entityLabel(entity),value:{$ref:{run_id:store.header.kernelRunId,epoch:store.header.epoch,id:entity.key.id,generation:{$integer:String(entity.key.generation)},type_id:entity.typeId}},typeId:entity.typeId,ancestors:store.header.types.find(row=>row.typeId===entity.typeId)?.ancestors??[]}));
  return <section aria-label="Run operations"><RunShortcuts enabled={interactive&&mode==='live'&&!busy&&store.commits.length>0} onToggle={()=>void serialized(toggle)} onInject={friendly} /><h2>Run operations<ConceptHelp topic="Run operations" description="Pause, resume or stop the runtime, and submit typed external events while it runs." guide="console.md"/></h2><p>{mode==='live'?'Inject an event and follow the simulation’s response.':'Replay is read-only; switch to live mode to operate an active run.'}</p>
    <div>{['pause','resume','stop'].map(action=><button key={action} disabled={mode!=='live'} onClick={()=>void control(action)}>Simulation {action}</button>)}</div>
    {operation&&<p>Requested: {String(operation.requested)} · last observed effective status: {effective??'not received'}{effective!==({pause:'paused',resume:'running',stop:'stopped'} as Record<string,string>)[String(operation.requested)]&&!(operation.requested==='resume'&&effective==='waiting_for_input')?' · pending at safe boundary':''}</p>}
    {operator&&mode==='live'&&<Details title="Operator source progress" buttonLabel="Source timing"><p>Operator source progress is closed through {displayTime(horizon)}. Events enter at the next unclosed simulation instant.</p></Details>}
    <div className="injection-options" aria-label="Authored events">{points.map(row=><button className="console-btn" aria-pressed={row.id===point} key={String(row.id)} onClick={()=>setPoint_(String(row.id))}>{`Inject ${readableLabel(String(row.id)).toLowerCase()}`}</button>)}</div>
    {injection?<div className="injection-card" data-testid="injection-card" ref={cardRef}>
      <h3>Inject {readableLabel(String(injection.id)).toLowerCase()}</h3>
      <p>{actualTargetSummary(injection,store)}</p>
      <TypedPayloadForm schema={payloadSchema} value={payload} onChange={setPayload} definitions={definitions} onValidityChange={setPayloadValid} entityChoices={choices}/>
      <button className="console-btn console-btn-primary" data-injection-submit data-testid={`inject-${point}`} aria-label="Submit typed ingress" disabled={busy||!payloadValid||mode!=='live'||!ready} onClick={()=>void serialized(submit)}>Inject {readableLabel(String(injection.id)).toLowerCase()}</button>
      <p className="injection-hint">Ready to send. Timing and request information are filled in automatically.</p>
      <Details title="Injection transport" buttonLabel="Advanced">{Object.entries(fields).map(([key,value])=><label key={key}>{key}<input aria-label={`Ingress ${key}`} value={value} onChange={event=>setFields({...fields,[key]:event.target.value})}/></label>)}<pre>{exactValue({request_id:'generated when sent',source_cut:store.viewCursor?.knownAt,point:injection})}</pre></Details>
    </div>:<div className="injection-card"><h3>Issue command</h3>
      <label>Command <select aria-label="Command schema" value={fields.schema} onChange={event=>{setFields({...fields,schema:event.target.value});setPayload({});}}><option value="">Select registered command</option>{commands.map(row=><option key={String(row.id)} value={String(row.id)}>{readableLabel(String(row.id))}</option>)}</select></label>
      <TypedPayloadForm schema={chosen?.schema} value={payload} onChange={setPayload} definitions={definitions} onValidityChange={setPayloadValid} entityChoices={choices}/>
      <button className="console-btn" disabled={busy||!payloadValid||mode!=='live'} onClick={()=>void serialized(submit)}>Submit typed ingress</button>
      <Details title="Command transport" buttonLabel="Advanced">{Object.entries(fields).filter(([key])=>key!=='schema').map(([key,value])=><label key={key}>{key}<input aria-label={`Ingress ${key}`} value={value} onChange={event=>setFields({...fields,[key]:event.target.value})}/></label>)}</Details>
    </div>}
    <Details title="Other commands" buttonLabel="Direct typed command"><label>Injection point <select ref={injectionRef} aria-label="Injection point" value={point} onChange={event=>setPoint_(event.target.value)}><option value="">Direct typed command</option>{points.map(row=><option key={String(row.id)} value={String(row.id)}>{readableLabel(String(row.id))}</option>)}</select></label><button onClick={()=>{setPoint_('');}}>Configure a direct command</button><p>{chosen?.cancel_support===true?'This command supports cancellation.':'Send an authored cancellation event where available.'}</p></Details>
    {error&&<p role="alert">{error}</p>}
    <details open={admissions.length>0}><summary>Admission receipts · {admissions.length}</summary>{admissions.map((receipt,index)=><article key={index}>Admission #{index+1}{mapping(receipt)&&typeof receipt.status==='string'&&` · ${receipt.status}`}<p className="receipt-help">{receiptSummary(receipt)}</p> <Details title="Admission receipt"><pre>{exactValue(receipt)}</pre></Details></article>)}</details>
    <details><summary>Execution receipts at this cut · {store.receipts.length}</summary>{store.receipts.length>100&&<p>Latest 100 of {store.receipts.length} execution receipts. All records are retained in the journal.</p>}{store.receipts.slice(-100).map((receipt,index)=><article key={index}>Execution #{index+1} <Details title="Execution receipt"><pre>{exactValue(receipt)}</pre></Details></article>)}</details>
    <details><summary>Decision / LangGraph and typed event records at this cut</summary>
      <label><input type="checkbox" checked={showInternal} onChange={event=>{setShowInternal(event.target.checked);setRecordPage(0);}}/>Include internal runtime records</label>
      <p>{records.length} records · page {shownPage+1} of {recordPages}</p><button disabled={shownPage===0} onClick={()=>setRecordPage(shownPage-1)}>Previous records</button><button disabled={shownPage===recordPages-1} onClick={()=>setRecordPage(shownPage+1)}>Next records</button>
      {records.slice(shownPage*50,(shownPage+1)*50).map(message=>{
      const commit=recordCuts.get(message.id);return <article key={message.id}><strong>{readableLabel(message.schemaId)}</strong> · {displayTime(message.at.ns)}
        {commit!==undefined&&<button onClick={()=>onSeek(commit)}>Seek record moment</button>}{message.subjects?.map(key=><button key={`${key.id}/${key.generation}`} onClick={()=>onSelect(key)}>{key.id} · g{key.generation}</button>)}<Details title="Event record"><pre>{exactValue(message)}</pre></Details></article>;
    })}</details>
  </section>;
}
