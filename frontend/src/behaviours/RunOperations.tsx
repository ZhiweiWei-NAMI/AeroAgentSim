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
import { useI18n } from '../i18n/I18nProvider';
import { translate } from '../i18n/translate';
import { exactValue } from '../feeds/format';
import { mapping, type Draft } from './model';
import { TypedPayloadForm } from './TypedPayloadForm';
import { emptyTransport, newIdempotencyKey, schemaDefaults, transportForPoint } from './injection-defaults';
import './run-operations.css';
interface Props { api:RunsApi;runId:string;store:TemporalFeedStore;selected?:EntityKey;commitCut?:number;onSelect:(key:EntityKey)=>void;onSeek:(index:number)=>void;mode:'live'|'replay';interactive?:boolean;onInjections?:(available:boolean)=>void;onRequestInject?:()=>void }
type Translator = (key: string, vars?: Record<string, string | number>) => string;
const english: Translator = (key, vars) => translate('en-US', key, vars);
const problemMessage = (problem: unknown) => problem instanceof Error ? problem.message : String(problem);
type ControlAction = 'pause' | 'resume' | 'stop';

export function advertisedMessages(store:TemporalFeedStore, t:Translator=english):Draft[] {
  const registry=store.header.runtimeRegistry;const rows=store.header.messages??(mapping(registry)?registry.messages:undefined);
  if(rows===undefined)return [];
  if(!Array.isArray(rows)||!rows.every(mapping))throw Error(t('Run message catalog is malformed'));return rows;
}
export function injectionPointsFromScenario(scenario:Draft, t:Translator=english):Draft[] {
  const engines=mapping(scenario.engines)?Object.values(scenario.engines).filter(mapping):[];
  const owners=engines.filter(engine=>engine.plugin==='behaviour');
  const packages=scenario.behaviours??(owners.length===1&&mapping(owners[0].config)?owners[0].config.packages:undefined);
  if(packages===undefined)return [];
  if(!Array.isArray(packages)||!packages.every(mapping))throw Error(t('Behaviour packages unavailable'));
  const points=packages.flatMap(item=>{
    const document=mapping(item.document)?item.document:item;
    if('path' in document)throw Error(t('Run configuration contains unresolved behaviour references'));
    const declared=document.injection_points;
    if(declared===undefined)return [];
    if(!Array.isArray(declared)||!declared.every(mapping))throw Error(t('Injection points malformed'));
    for(const point of declared)for(const key of ['id','stream_id','command','target','emits'])if(typeof point[key]!=='string'||!point[key])throw Error(t('Injection point {field} is required', { field: key }));
    return declared;
  });
  if(new Set(points.map(point=>point.id)).size!==points.length)throw Error(t('Injection point IDs are ambiguous across packages'));
  return points;
}
export function ingressBody(fields:Record<string,string>,payload:unknown,t:Translator=english):Draft {
  for(const key of ['schema','target','stream_id','at_ns','clock_id','mapping_id','numerator','denominator']) if(!fields[key])throw Error(t('{field}: explicitly required', { field: key }));
  for(const key of ['at_ns','numerator','denominator'])if(!/^-?(0|[1-9]\d*)$/.test(fields[key]))throw Error(t('{field}: exact integer required', { field: key }));
  if(BigInt(fields.at_ns)<0n||BigInt(fields.denominator)<1n)throw Error(t('Occurrence must be nonnegative; source denominator must be positive'));
  return {schema:fields.schema,target:fields.target,stream_id:fields.stream_id,at_ns:{$integer:fields.at_ns},payload,source_stamp:{clock_id:fields.clock_id,mapping_id:fields.mapping_id,numerator:{$integer:fields.numerator},denominator:{$integer:fields.denominator}},...(fields.idempotency_key?{idempotency_key:fields.idempotency_key}:{})};
}
export function operatorTiming(scenario:Draft,t:Translator=english):{step:bigint;initial:bigint} {
  const exact=(value:unknown,label:string)=>{
    const text=typeof value==='number'&&Number.isSafeInteger(value)?String(value):mapping(value)?value.$integer:undefined;
    if(typeof text!=='string'||!/^(0|[1-9]\d*)$/.test(text))throw Error(t('{field}: declared exact nonnegative integer required', { field: label }));
    return BigInt(text);
  };
  const stream=Array.isArray(scenario.ingress_streams)?scenario.ingress_streams.find(row=>mapping(row)&&row.id==='operator'):undefined;
  if(!mapping(scenario.run)||!mapping(stream))throw Error(t('Operator timing requires the authored run and ingress source'));
  const step=exact(scenario.run.advance_ns,t('Run advance'));if(step===0n)throw Error(t('Run advance must be positive'));
  return {step,initial:exact(stream.initial_watermark_ns,t('Operator initial watermark'))};
}
export function recordedAccidentPayload(store:TemporalFeedStore):Draft|undefined {
  if(!store.header.kernelRunId||store.header.epoch===undefined)return undefined;
  const incident=[...store.entities.values()].find(row=>row.typeId==='aas:TrafficIncident');
  if(!incident)return undefined;
  return {incident:{$ref:{run_id:store.header.kernelRunId,epoch:store.header.epoch,id:incident.key.id,generation:{$integer:String(incident.key.generation)},type_id:incident.typeId}}};
}
export function RunOperations({api,runId,store,selected,onSelect,onSeek,mode,interactive=true,onInjections,onRequestInject}:Props) {
  const { t, locale } = useI18n();
  const notify=useConsoleNotice();
  const cardRef=useRef<HTMLDivElement>(null),injectionRef=useRef<HTMLSelectElement>(null),pointInitialized=useRef(false);
  const [fields,setFields]=useState(emptyTransport()),[payload,setPayload]=useState<unknown>({}),[point,setPoint]=useState(''),[points,setPoints]=useState<Draft[]>([]),[error,setError]=useState(''),[admissions,setAdmissions]=useState<unknown[]>([]),[busy,setBusy]=useState(false),[operation,setOperation]=useState<Draft>(),[effective,setEffective]=useState<string>();
  const [operator,setOperator]=useState(false),[horizon,setHorizon]=useState('0'),[scheduledOnly,setScheduledOnly]=useState(false);
  const operatorNs=useRef(0n), operatorStep=useRef(1000000000n), operatorEnd=useRef<bigint>(), operations=useRef<Promise<unknown>>(Promise.resolve());
  const serialized=(action:()=>Promise<unknown>)=>{const result=operations.current.then(action,action);operations.current=result;return result;};
  const busyRef = useRef(false);
  const canOperate = interactive && mode === 'live' && ['running','waiting_for_input','paused'].includes(effective ?? '');
  // Read the lightweight per-run feed status, not the expensive all-runs/KPI listing.
  const readStatus = async (signal?: AbortSignal) => {
    const next = (store.commits.at(-1)?.commitIndex ?? 0) + 1;
    const reply = await api.request(`/v1/runs/${encodeURIComponent(runId)}/commits?from=${next}&limit=1`, { signal });
    if (!mapping(reply) || typeof reply.status !== 'string') throw Error(t('Operational run status unavailable'));
    return reply.status;
  };
  // Lock immediately, before React renders, so double clicks and shortcuts cannot queue duplicates.
  const execute = (action: (status: string) => Promise<unknown>) => {
    if (!canOperate || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    void serialized(async () => {
      const status = await readStatus();
      setEffective(status);
      if (!['running','waiting_for_input','paused'].includes(status)) { notify({ kind: 'info', message: t('This run is {status}; controls are unavailable.', { status: statusLabel(status) }) }); return; }
      return action(status);
    }).catch(problem => { setError(problemMessage(problem)); notify({ kind: 'error', message: problemMessage(problem) }); }).finally(() => { busyRef.current = false; setBusy(false); });
  };
  const [payloadValid,setPayloadValid]=useState(true);
  const [showInternal,setShowInternal]=useState(false),[recordPage,setRecordPage]=useState(0);
  const records=useMemo(()=>store.messages.filter(message=>showInternal||message.schemaId.startsWith('traffic.')||['aas.langgraph.record','aas.runtime.inject_event'].includes(message.schemaId)),[store,store.messages.length,store.viewCursor?.knownAt,showInternal]);
  const recordPages=Math.max(1,Math.ceil(records.length/50)),shownPage=Math.min(recordPage,recordPages-1);
  const recordCuts=useMemo(()=>new Map(store.commits.flatMap(commit=>commit.messages.map(message=>[message.id,commit.commitIndex] as const))),[store,store.commits.length]);

  const messages=advertisedMessages(store,t),commands=messages.filter(row=>row.kind==='command');const chosen=commands.find(row=>row.id===fields.schema),injection=points.find(row=>row.id===point);
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
  useEffect(()=>{onInjections?.(points.length>0&&!scheduledOnly&&canOperate);},[points,scheduledOnly,canOperate,onInjections]);
  const friendly=()=>{if(!canOperate||busyRef.current)return;onRequestInject?.();requestAnimationFrame(()=>{const card=cardRef.current;card?.scrollIntoView?.({block:'center'});const button=card?.querySelector<HTMLButtonElement>('[data-injection-submit]');if(button&&!button.disabled)button.focus();else card?.querySelector<HTMLElement>('input:not(:disabled),select:not(:disabled),textarea:not(:disabled)')?.focus();});};
  useEffect(()=>{const declared=store.header.behaviour?.injectionPoints??store.header.behaviour?.injection_points;if(declared)setPoints(declared);
    const abort=new AbortController();void awaitRunConfiguration(api, runId, abort.signal).then(value=>{
      if(!mapping(value)||!mapping(value.scenario))throw Error(t('Run configuration is malformed'));
      if(!abort.signal.aborted){setPoints(injectionPointsFromScenario(value.scenario,t));if(value.scenario.id==='traffic-accident'){const timing=operatorTiming(value.scenario,t);operatorStep.current=timing.step;const until=mapping(value.scenario.run)?value.scenario.run.until_ns:undefined;const end=typeof until==='number'&&Number.isSafeInteger(until)?String(until):mapping(until)?until.$integer:undefined;if(typeof end!=='string'||!/^(0|[1-9]\d*)$/.test(end))throw Error(t('Operator run limit must be a declared exact nonnegative integer'));operatorEnd.current=BigInt(end);const recorded=store.commits.at(-1);operatorNs.current=recorded&&BigInt(recorded.at.ns)>timing.initial?BigInt(recorded.at.ns):timing.initial;setHorizon(operatorNs.current.toString());setScheduledOnly(timing.initial>=BigInt(end));setOperator(timing.initial<BigInt(end));}}
    }).catch(problem=>{if(!abort.signal.aborted)setError(problemMessage(problem));});return()=>abort.abort();
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
    if(!operator||!canOperate)return;
    let active=true;
    // Source progress is acknowledged independently of event production: an idle
    // closed prefix need not have a journal event at its right boundary.
    const timer=setInterval(()=>{if(!store.commits.length)return;
      void serialized(async()=>{if(!active||operatorEnd.current===undefined||operatorNs.current>=operatorEnd.current)return;const next=operatorNs.current+operatorStep.current>operatorEnd.current?operatorEnd.current:operatorNs.current+operatorStep.current;const reply=await api.request(`/v1/runs/${encodeURIComponent(runId)}/watermark`,{method:'POST',headers:{'Content-Type':'application/json'},body:stringifyLossless({stream_id:'operator',watermark_ns:{$integer:next.toString()}})});if(!mapping(reply)||String(reply.watermark_ns)!==next.toString())throw Error(t('Operator watermark receipt mismatch'));operatorNs.current=next;setHorizon(next.toString());}).catch(problem=>{if(active){setError(problemMessage(problem));setOperator(false);}});
    },1000);
    return()=>{active=false;clearInterval(timer);};
  },[operator,canOperate,api,runId,store]);
  useEffect(()=>{
    setEffective(undefined);
    if(mode!=='live')return;
    const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{
      let terminal=false;
      try{const status=await readStatus(abort.signal);if(abort.signal.aborted)return;setEffective(status);terminal=isTerminalStatus(status);}
      catch(problem){if(!abort.signal.aborted){setEffective(undefined);setError(problemMessage(problem));}}
      if(!abort.signal.aborted&&!terminal)timer=setTimeout(()=>void poll(),1000);
    };
    void poll();return()=>{abort.abort();clearTimeout(timer);};
  },[api,runId,store,mode]); // eslint-disable-line react-hooks/exhaustive-deps
  const requestKey=()=>{try{return newIdempotencyKey();}catch{throw Error(t('Idempotency keys require a UUID-capable crypto runtime'));}};
  const submit=async()=>{
    if(!payloadValid){setError(t('Correct the payload before submitting'));return;}
    setError('');
    try{
      if(!chosen)throw Error(t('Select a registered command'));
      const effective=operator?{...fields,at_ns:(operatorNs.current+1n).toString(),clock_id:'canonical',mapping_id:'canonical',numerator:(operatorNs.current+1n).toString(),denominator:'1'}:{...fields};
      if(!effective.idempotency_key)effective.idempotency_key=requestKey();
      const descriptor=typeof event?.schema==='string'?definitions[event.schema]:event?.schema;
      const members=mapping(descriptor)&&mapping(descriptor.members)?descriptor.members:{};
      const eventPayload=mapping(payload)?{...payload}:payload;
      if(injection&&mapping(eventPayload)){
        if('request_id' in members&&!eventPayload.request_id)eventPayload.request_id=requestKey();
        if('source_cut' in members&&eventPayload.source_cut===undefined&&store.viewCursor&&store.viewCursor.knownAt>=0)eventPayload.source_cut={$integer:String(store.viewCursor.knownAt)};
      }
      const body=ingressBody(effective,injection?{injection_point:injection.id,payload:eventPayload}:payload,t);
      const receipt=await api.request(`/v1/runs/${encodeURIComponent(runId)}/ingress`,{method:'POST',headers:{'Content-Type':'application/json'},body:stringifyLossless(body)});
      setAdmissions(rows=>[...rows,receipt]);notify({kind:'info',message:mapping(receipt)&&receipt.disposition==='accepted'?t('Event accepted. Follow its response in the timeline.'):t('Event receipt received. Check the result below.')});
    }catch(problem){setError(problemMessage(problem));notify({kind:'error',message:problemMessage(problem)});}
  };
  const actionLabel=(action:string)=>action==='pause'?t('pause'):action==='resume'?t('resume'):action==='stop'?t('stop'):action;
  const statusLabel=(status:string)=>{
    switch(status){
      case 'running':return t('running');
      case 'paused':return t('paused');
      case 'waiting_for_input':return t('waiting for input');
      case 'stopped':return t('stopped');
      case 'completed':return t('completed');
      case 'faulted':return t('faulted');
      case 'interrupted':return t('interrupted');
      case 'input_timeout':return t('input timed out');
      case 'accepted':return t('accepted');
      case 'delayed':return t('delayed');
      case 'rejected':return t('rejected');
      default:return status;
    }
  };
  const control=async(action:ControlAction)=>{setError('');try{const reply=await api.request(`/v1/runs/${encodeURIComponent(runId)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!mapping(reply)||reply.requested!==action)throw Error(t('Malformed run control receipt'));setOperation(reply);setEffective(typeof reply.status==='string'?reply.status:undefined);notify({kind:'info',message:action==='pause'?t('Simulation pause requested.'):action==='resume'?t('Simulation resume requested.'):t('Simulation stop requested.')});}catch(problem){setError(problemMessage(problem));notify({kind:'error',message:problemMessage(problem)});}};
  const toggle=async(status:string)=>{try{if(status==='running'||status==='waiting_for_input')await control('pause');else if(status==='paused')await control('resume');else notify({kind:'info',message:t('Run is {status}; pause/resume is unavailable.',{status:statusLabel(status)})});}catch(problem){setError(problemMessage(problem));notify({kind:'error',message:problemMessage(problem)});}};
  const setPoint_=(id:string)=>{const next=points.find(row=>row.id===id);setPoint(id);setPayload(id==='accident'?(recordedAccidentPayload(store)??{}):{});setFields(next?transportForPoint(next,{operator,operatorNs:operatorNs.current}):emptyTransport());};
  const ready=point!=='accident'||mapping(payload)&&mapping(payload.incident);
  const descriptor=typeof event?.schema==='string'?definitions[event.schema]:event?.schema;
  const payloadSchema=mapping(descriptor)&&mapping(descriptor.members)?{...descriptor,members:Object.fromEntries(Object.entries(descriptor.members).filter(([key])=>!['request_id','source_cut'].includes(key)))}:descriptor;
  const choices=[...store.entities.values()].map(entity=>({label:entityLabel(entity,locale),value:{$ref:{run_id:store.header.kernelRunId,epoch:store.header.epoch,id:entity.key.id,generation:{$integer:String(entity.key.generation)},type_id:entity.typeId}},typeId:entity.typeId,ancestors:store.header.types.find(row=>row.typeId===entity.typeId)?.ancestors??[]}));
  const eventLabel=(row:Draft)=>readableLabel(String(row.name??row.id),locale).toLocaleLowerCase(locale);
  const targetSummary=(row:Draft)=>{
    if(row.id!=='accident')return t('Send {event} to its configured plugin.',{event:eventLabel(row)});
    const incidents=[...store.entities.values()].filter(entity=>entity.typeId==='aas:TrafficIncident');
    return incidents.length?t('Trigger the accident response for {entities}. Choose an actor or add a reason if needed.',{entities:incidents.map(entity=>entityLabel(entity,locale)).join(locale.startsWith('zh')?'、':', ')}):t('Waiting for the run’s incident entity to load.');
  };
  const admissionSummary=(receipt:unknown)=>{
    if(!mapping(receipt))return t('Receipt could not be interpreted; read the raw record below.');
    switch(receipt.disposition){
      case 'accepted':return t('Event accepted for processing. The execution receipts show what happened next.');
      case 'delayed':return t('Event scheduled for later. Check its execution receipt after it runs.');
      case 'rejected':return t('Event rejected ({code}): it was not executed.',{code:String(receipt.code??t('no code'))});
      default:return t('Receipt without an admission disposition; read the raw record below.');
    }
  };
  return <section className="run-operations" aria-label={t('Run operations')} aria-busy={busy}>
    <RunShortcuts enabled={canOperate&&!busy&&store.commits.length>0} onToggle={()=>execute(toggle)} onInject={friendly} />
    <h2>{t('Run operations')}<ConceptHelp topic={t('Run operations')} description={t('Pause, resume or stop the runtime, and submit typed external events while it runs.')} guide="console.md" /></h2>
    <p>{mode==='replay'?t('Replay is read-only; switch to live mode to operate an active run.'):!interactive?t('Run controls are unavailable in this view.'):!effective?t('Checking run status…'):!canOperate?t('This run is {status}; controls are unavailable.',{status:statusLabel(effective)}):t('Inject an event and follow the simulation’s response.')}</p>
    <div className="run-control-buttons" role="group" aria-label={t('Simulation controls')}>
      {(['pause','resume','stop'] as const).map(action=><button type="button" key={action} disabled={!canOperate||busy||action==='pause'&&effective==='paused'||action==='resume'&&['running','waiting_for_input'].includes(effective??'')} onClick={()=>execute(()=>control(action))}>{action==='pause'?t('Pause simulation'):action==='resume'?t('Resume simulation'):t('Stop simulation')}</button>)}
    </div>
    {operation&&<p role="status">{t('Requested: {action} · last observed effective status: {status}',{action:actionLabel(String(operation.requested)),status:effective?statusLabel(effective):t('not received')})}{effective!==({pause:'paused',resume:'running',stop:'stopped'} as Record<string,string>)[String(operation.requested)]&&!(operation.requested==='resume'&&effective==='waiting_for_input')?t(' · pending at safe boundary'):''}</p>}
    {operator&&mode==='live'&&<Details title={t('Operator source progress')} buttonLabel={t('Source timing')}><p>{t('Operator source progress is closed through {time}. Events enter at the next unclosed simulation instant.',{time:displayTime(horizon)})}</p></Details>}
    <div className="injection-options" role="group" aria-label={t('Authored events')}>{points.map(row=><button type="button" className="console-btn" disabled={busy} aria-pressed={row.id===point} key={String(row.id)} onClick={()=>setPoint_(String(row.id))}>{t('Inject {event}',{event:eventLabel(row)})}</button>)}</div>
    {injection?<div className="injection-card" data-testid="injection-card" ref={cardRef}>
      <h3>{t('Inject {event}',{event:eventLabel(injection)})}</h3>
      <p>{targetSummary(injection)}</p>
      <TypedPayloadForm schema={payloadSchema} value={payload} onChange={setPayload} definitions={definitions} onValidityChange={setPayloadValid} entityChoices={choices}/>
      <button type="button" className="console-btn console-btn-primary" data-injection-submit data-testid={`inject-${point}`} aria-label={t('Submit typed ingress')} disabled={busy||!payloadValid||!canOperate||!ready||scheduledOnly||!chosen} onClick={()=>execute(submit)}>{t('Inject {event}',{event:eventLabel(injection)})}</button>
      <p className="injection-hint">{scheduledOnly?t('This run uses a scheduled accident. Change Accident time in Customize to try a different moment.'):point==='accident'?t('You can introduce an accident at any time. Detection follows when the reporter is nearby and the vehicles have stopped.'):operator?t('Timing and request information are filled in automatically.'):t('Complete the required timing fields in Advanced before submitting.')}</p>
      <Details title={t('Injection transport')} buttonLabel={t('Advanced')}><div className="run-transport-fields">{Object.entries(fields).map(([key,value])=><label key={key}>{key}<input aria-label={t('Ingress {field}',{field:key})} disabled={busy} value={value} onChange={event=>setFields({...fields,[key]:event.target.value})}/></label>)}</div><pre>{exactValue({request_id:'generated when sent',source_cut:store.viewCursor?.knownAt,point:injection})}</pre></Details>
    </div>:<div className="injection-card" ref={cardRef}><h3>{t('Issue command')}</h3>
      <p className="injection-hint">{t('No guided external event is selected. Pause, resume and stop work without an event. Advanced commands need a declared input source and the timing fields below.')}</p>
      <label>{t('Command')} <select aria-label={t('Command schema')} disabled={busy} value={fields.schema} onChange={event=>{setFields({...fields,schema:event.target.value});setPayload({});}}><option value="">{t('Select registered command')}</option>{commands.filter(row=>row.id!=='aas.runtime.inject_event').map(row=><option key={String(row.id)} value={String(row.id)}>{readableLabel(String(row.id),locale)}</option>)}</select></label>
      {!commands.length&&<p>{t('No registered commands are available for this run.')}</p>}
      <TypedPayloadForm schema={chosen?.schema} value={payload} onChange={setPayload} definitions={definitions} onValidityChange={setPayloadValid} entityChoices={choices}/>
      <button type="button" className="console-btn" data-injection-submit disabled={busy||!payloadValid||!canOperate||!chosen} onClick={()=>execute(submit)}>{t('Submit typed ingress')}</button>
      <Details title={t('Command transport')} buttonLabel={t('Advanced')}><div className="run-transport-fields">{Object.entries(fields).filter(([key])=>key!=='schema').map(([key,value])=><label key={key}>{key}<input aria-label={t('Ingress {field}',{field:key})} disabled={busy} value={value} onChange={event=>setFields({...fields,[key]:event.target.value})}/></label>)}</div></Details>
    </div>}
    <Details title={t('Other commands')} buttonLabel={t('Direct typed command')}><label>{t('Injection point')} <select ref={injectionRef} aria-label={t('Injection point')} disabled={busy} value={point} onChange={event=>setPoint_(event.target.value)}><option value="">{t('Direct typed command')}</option>{points.map(row=><option key={String(row.id)} value={String(row.id)}>{readableLabel(String(row.id),locale)}</option>)}</select></label><button type="button" disabled={busy} onClick={()=>setPoint_('')}>{t('Configure a direct command')}</button><p>{chosen?.cancel_support===true?t('This command supports cancellation.'):t('Send an authored cancellation event where available.')}</p></Details>
    {error&&<p role="alert">{error}</p>}
    <details open={admissions.length>0}><summary>{t('Admission receipts · {count}',{count:admissions.length})}</summary>{!admissions.length&&<p>{t('No admission receipts yet.')}</p>}{admissions.map((receipt,index)=><article key={index}>{t('Admission #{number}',{number:index+1})}{mapping(receipt)&&typeof receipt.status==='string'&&` · ${statusLabel(receipt.status)}`}<p className="receipt-help">{admissionSummary(receipt)}</p><Details title={t('Admission receipt')} buttonLabel={t('Details')}><pre>{exactValue(receipt)}</pre></Details></article>)}</details>
    <details><summary>{t('Execution receipts at this cut · {count}',{count:store.receipts.length})}</summary>{!store.receipts.length&&<p>{t('No execution receipts at this cut.')}</p>}{store.receipts.length>100&&<p>{t('Latest 100 of {count} execution receipts. All records are retained in the journal.',{count:store.receipts.length})}</p>}{store.receipts.slice(-100).map((receipt,index)=><article key={index}>{t('Execution #{number}',{number:Math.max(0,store.receipts.length-100)+index+1})} <Details title={t('Execution receipt')} buttonLabel={t('Details')}><pre>{exactValue(receipt)}</pre></Details></article>)}</details>
    <details><summary>{t('Decision / LangGraph and typed event records at this cut')}</summary>
      <label><input type="checkbox" checked={showInternal} onChange={event=>{setShowInternal(event.target.checked);setRecordPage(0);}}/>{t('Include internal runtime records')}</label>
      <p>{t('{count} records · page {page} of {pages}',{count:records.length,page:shownPage+1,pages:recordPages})}</p><button type="button" disabled={shownPage===0} onClick={()=>setRecordPage(shownPage-1)}>{t('Previous records')}</button><button type="button" disabled={shownPage===recordPages-1} onClick={()=>setRecordPage(shownPage+1)}>{t('Next records')}</button>
      {!records.length&&<p>{t('No event records match the current filters at this cut.')}</p>}
      {records.slice(shownPage*50,(shownPage+1)*50).map(message=>{
        const commit=recordCuts.get(message.id);return <article key={message.id}><strong>{readableLabel(message.schemaId,locale)}</strong> · {displayTime(message.at.ns)}
          {commit!==undefined&&<button type="button" onClick={()=>onSeek(commit)}>{t('Seek record moment')}</button>}{message.subjects?.map(key=><button type="button" key={`${key.id}/${key.generation}`} onClick={()=>onSelect(key)}>{t('{entity} · generation {generation}',{entity:key.id,generation:key.generation})}</button>)}<Details title={t('Event record')} buttonLabel={t('Details')}><pre>{exactValue(message)}</pre></Details></article>;
      })}
    </details>
  </section>;
}
