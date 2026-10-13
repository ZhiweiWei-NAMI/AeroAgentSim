import { useEffect, useState, useRef, useMemo } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { awaitRunConfiguration, RunsApi, isTerminalStatus } from '../feeds/http';
import { stringifyLossless } from '../feeds/lossless-json';
import { Details, RecordFields } from '../console/Details';
import { useConsoleNotice } from '../console/Notifications';
import { displayTime } from '../pages/display-time';
import { ConceptHelp } from '../console/ConceptHelp';
import { readableLabel } from '../pages/inspection-format';
import { useI18n } from '../i18n/I18nProvider';
import { translate } from '../i18n/translate';
import { exactValue } from '../feeds/format';
import { mapping, type Draft } from './model';
import { TypedPayloadForm } from './TypedPayloadForm';
import { emptyTransport, entityPayloadSchema, newIdempotencyKey, payloadProblem, schemaDefaults, transportForPoint } from './injection-defaults';
import { displayName } from '../viewport/semantic';
import { exactNanos, operatorTiming, type OperatorInput } from '../workbench/useOperatorInput';
export { operatorTiming } from '../workbench/useOperatorInput';
import './run-operations.css';
interface Props { api:RunsApi;runId:string;store:TemporalFeedStore;selected?:EntityKey;commitCut?:number;onSelect:(key:EntityKey)=>void;onSeek:(index:number)=>void;mode:'live'|'replay';interactive?:boolean;onInjections?:(available:boolean)=>void;inputSource?:OperatorInput }
type Translator = (key: string, vars?: Record<string, string | number>) => string;
const english: Translator = (key, vars) => translate('en-US', key, vars);
const problemMessage = (problem: unknown) => problem instanceof Error ? problem.message : String(problem);

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
export function recordedAccidentPayload(store:TemporalFeedStore):Draft|undefined {
  if(!store.header.kernelRunId||store.header.epoch===undefined)return undefined;
  const incident=[...store.entities.values()].find(row=>row.typeId==='aas:TrafficIncident');
  if(!incident)return undefined;
  return {incident:{$ref:{run_id:store.header.kernelRunId,epoch:store.header.epoch,id:incident.key.id,generation:{$integer:String(incident.key.generation)},type_id:incident.typeId}}};
}
export function RunOperations({api,runId,store,selected,commitCut,onSelect,onSeek,mode,interactive=true,onInjections,inputSource}:Props) {
  const { t, locale } = useI18n();
  const copy=(en:string,zh:string)=>locale==='zh-CN'?zh:en;
  const notify=useConsoleNotice();
  const pointInitialized=useRef(false);
  const [fields,setFields]=useState(emptyTransport()),[payload,setPayload]=useState<unknown>({}),[point,setPoint]=useState(''),[points,setPoints]=useState<Draft[]>([]),[error,setError]=useState(''),[admissions,setAdmissions]=useState<any[]>([]),[busy,setBusy]=useState(false),[effective,setEffective]=useState<string>();
  const [operator,setOperator]=useState(false),[horizon,setHorizon]=useState<string>(),[scheduledOnly,setScheduledOnly]=useState(false);
  const [progressError,setProgressError]=useState('');
  const [admissionError,setAdmissionError]=useState('');
  const [authoredMessages,setAuthoredMessages]=useState<Draft[]>([]);
  const operatorNs=useRef<bigint>(), operatorEnd=useRef<bigint>(), operations=useRef<Promise<unknown>>(Promise.resolve());
  const serialized=inputSource?.serialize??((action:()=>Promise<unknown>)=>{const result=operations.current.then(action,action);operations.current=result;return result;});
  const busyRef = useRef(false);
  const viewingHistory = commitCut !== undefined || !store.liveFollow && !!store.viewCursor && store.viewCursor.knownAt < (store.commits.at(-1)?.commitIndex ?? -1);
  const canOperate = interactive && mode === 'live' && !viewingHistory && ['running','waiting_for_input','paused'].includes(effective ?? '');
  // Read the lightweight per-run feed status, not the expensive all-runs/KPI listing.
  const readStatus = async (signal?: AbortSignal) => {
    const next = (store.commits.at(-1)?.commitIndex ?? 0) + 1;
    const reply = await api.request(`/v1/runs/${encodeURIComponent(runId)}/commits?from=${next}&limit=1`, { signal });
    if (!mapping(reply) || typeof reply.status !== 'string') throw Error(t('Operational run status unavailable'));
    return reply.status;
  };
  const readOperatorProgress = async (signal?:AbortSignal):Promise<{status:string;pauseRequested:boolean}> => {
    let reply:unknown;
    try { reply=await api.request(`/v1/runs/${encodeURIComponent(runId)}/inputs`,{signal}); }
    catch(problem) {
      if(String(problem).includes('HTTP 409')) {
        const status=await readStatus(signal);
        if(isTerminalStatus(status))return {status,pauseRequested:false};
      }
      throw problem;
    }
    if(!mapping(reply)||typeof reply.status!=='string'||typeof reply.pause_requested!=='boolean'||!mapping(reply.watermarks))throw Error(copy('Input source progress is malformed.','输入源进度格式不正确。'));
    signal?.throwIfAborted();
    const observed=exactNanos(reply.watermarks.operator,copy('Operator source watermark','操作员输入源水位'),t);
    // Independent reads can complete out of order. Retain the newest frontier
    // actually read from this source, including a command's fresh preflight.
    const progress=operatorNs.current!==undefined&&operatorNs.current>observed?operatorNs.current:observed;
    operatorNs.current=progress;setHorizon(progress.toString());
    if(operatorEnd.current!==undefined)setScheduledOnly(progress>=operatorEnd.current);
    return {status:reply.status,pauseRequested:reply.pause_requested};
  };
  const operatorReady=operator&&operatorNs.current!==undefined;
  const injectionSourceReady=fields.stream_id!=='operator'||!operator||operatorReady;
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
  const records=useMemo(()=>store.messages.filter(message=>showInternal||message.schemaId==='aas.runtime.inject_event'||!['aas.runtime.','aas.behaviour.','aas.predicate.'].some(prefix=>message.schemaId.startsWith(prefix))),[store,store.messages.length,store.viewCursor?.knownAt,showInternal]);
  const recordPages=Math.max(1,Math.ceil(records.length/50)),shownPage=Math.min(recordPage,recordPages-1);
  const recordCuts=useMemo(()=>new Map(store.commits.flatMap(commit=>commit.messages.map(message=>[message.id,commit.commitIndex] as const))),[store,store.commits.length]);

  const messages=advertisedMessages(store,t),commands=messages.filter(row=>row.kind==='command');const chosen=commands.find(row=>row.id===fields.schema),injection=points.find(row=>row.id===point);
  const event=injection&&messages.find(row=>row.id===injection.emits);
  const definitions=mapping(store.header.runtimeRegistry)&&mapping(store.header.runtimeRegistry.schemas)?store.header.runtimeRegistry.schemas:{};
  useEffect(()=>{
    if(!injection)return;
    const defaults=schemaDefaults(event?.schema,definitions);
    setPayload(mapping(defaults)?{...defaults,...(point==='accident'?recordedAccidentPayload(store):{})}:point==='accident'?(recordedAccidentPayload(store)??{}):{});
  },[point,event?.schema]);
  useEffect(()=>{onInjections?.(canOperate&&points.some(row=>row.stream_id!=='operator'||!scheduledOnly&&(!operator||operatorReady)));},[points,scheduledOnly,canOperate,operator,operatorReady,onInjections]);
  useEffect(()=>{
    setOperator(false);setScheduledOnly(false);setHorizon(undefined);operatorNs.current=undefined;operatorEnd.current=undefined;
    setAuthoredMessages([]);
    const declared=store.header.behaviour?.injectionPoints??store.header.behaviour?.injection_points;if(declared)setPoints(declared);
    const abort=new AbortController();void awaitRunConfiguration(api, runId, abort.signal).then(value=>{
      if(!mapping(value)||!mapping(value.scenario))throw Error(t('Run configuration is malformed'));
      if(abort.signal.aborted)return;
      setPoints(injectionPointsFromScenario(value.scenario,t));
      const catalog=mapping(value.scenario.registry)?value.scenario.registry.messages:undefined;
      if(catalog!==undefined){if(!Array.isArray(catalog)||!catalog.every(mapping))throw Error(t('Run message catalog is malformed'));setAuthoredMessages(catalog);}
      const stream=Array.isArray(value.scenario.ingress_streams)?value.scenario.ingress_streams.find(row=>mapping(row)&&row.id==='operator'):undefined;
      if(!stream)return;
      const timing=operatorTiming(value.scenario,t),end=exactNanos(mapping(value.scenario.run)?value.scenario.run.until_ns:undefined,copy('Run limit','运行时限'),t);
      operatorEnd.current=end;
      setScheduledOnly(timing.initial>=end);setOperator(timing.initial<end);
    }).catch(problem=>{if(!abort.signal.aborted)setError(problemMessage(problem));});return()=>abort.abort();
  },[api,runId,store]);
  // Select the single authored point by default and prefill its transport from
  // the authored declaration; missing operator timing stays empty (no invention).
  useEffect(()=>{if(pointInitialized.current||!points.length)return;pointInitialized.current=true;const only=points.length===1?points[0]:points.find(row=>row.id==='accident');if(only)setPoint(String(only.id));},[points]);
  useEffect(()=>{if(!injection)return;setFields(transportForPoint(injection,{operator:operatorReady,operatorNs:operatorNs.current}));},[injection,operatorReady]);
  useEffect(()=>{
    if(point!=='accident'||!mapping(payload)||'incident' in payload)return;
    const recorded=recordedAccidentPayload(store);if(recorded)setPayload((previous:unknown)=>({...(mapping(previous)?previous:{}),...recorded}));
  },[point,payload,store,store.viewCursor?.knownAt]);
  useEffect(()=>{setEffective(undefined);setProgressError('');},[api,runId,store,mode]);
  // RUN-008: admissions are journaled server-side, so reload them on mount and
  // refresh, then merge live receipts by id — a refresh must not lose or
  // duplicate an admitted operation, and a journaled outcome updates the same row.
  useEffect(()=>{
    const abort=new AbortController();
    setAdmissions([]); setAdmissionError('');
    const load=()=>{void api.admissions(runId,abort.signal).then(rows=>{
      if(abort.signal.aborted)return;
      setAdmissionError('');
      setAdmissions(previous=>{
        // A live receipt's command_id is the journaled row's id: keep only the journaled copy.
        const journaled=new Set(rows.map(stored=>stored.id));
        const live=previous.filter(row=>(!mapping(row)||typeof row.id!=='string')&&!(mapping(row)&&typeof row.command_id==='string'&&journaled.has(row.command_id)));
        const merged=[...rows.map(stored=>({...stored,stored:true})),...live];
        const seen=new Set<string>(),unique=merged.filter(row=>{
          const key=typeof row.id==='string'?row.id:`live-${seen.size}`;
          if(typeof row.id==='string'&&seen.has(key))return false;
          seen.add(key);return true;
        });
        return unique;
      });
    }).catch(problem => { if(!abort.signal.aborted)setAdmissionError(String(problem).includes('HTTP 404')
      ? copy('Saved admission receipts are unavailable on this server.','此服务器不提供已保存的受理回执。')
      : problemMessage(problem)); });};
    load();
    const timer=setInterval(load,5000);
    return()=>{abort.abort();clearInterval(timer);};
  },[api,runId]);
  useEffect(()=>{
    const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{
      let terminal=false;
      try {
        // Reads must not occupy the admission/control queue: a slow projection
        // should never delay a top-bar pause, resume or stop request.
        const progress=operator?await readOperatorProgress(abort.signal):{status:await readStatus(abort.signal),pauseRequested:false};
        if(abort.signal.aborted)return;
        setEffective(progress.status);terminal=isTerminalStatus(progress.status);
        if(!abort.signal.aborted)setProgressError('');
      } catch(problem) {if(!abort.signal.aborted){setEffective(undefined);setHorizon(undefined);operatorNs.current=undefined;setProgressError(problemMessage(problem));}}
      if(!abort.signal.aborted&&!terminal&&mode==='live')timer=setTimeout(()=>void poll(),1000);
    };
    void poll();return()=>{abort.abort();clearTimeout(timer);};
  },[api,runId,store,mode,operator]); // eslint-disable-line react-hooks/exhaustive-deps
  const requestKey=()=>{try{return newIdempotencyKey();}catch{throw Error(t('Idempotency keys require a UUID-capable crypto runtime'));}};
  const submit=async()=>{
    if(!payloadValid||payloadProblem(injection?payloadSchema:chosen?.schema,payload,definitions)){setError(t('Correct the payload before submitting'));return;}
    setError('');
    try{
      if(!chosen)throw Error(t('Select a registered command'));
      if(operator&&fields.stream_id==='operator'){const progress=await readOperatorProgress();setEffective(progress.status);if(!['running','waiting_for_input','paused'].includes(progress.status))throw Error(t('This run is {status}; controls are unavailable.',{status:statusLabel(progress.status)}));if(operatorNs.current===undefined||operatorEnd.current===undefined||operatorNs.current>=operatorEnd.current)throw Error(copy('The operator input interval is already closed.','操作员输入区间已经关闭。'));}
      const automaticNs=operator&&fields.stream_id==='operator'&&operatorNs.current!==undefined?(operatorNs.current+1n).toString():undefined;
      const effective=automaticNs!==undefined?{...fields,at_ns:automaticNs,clock_id:'canonical',mapping_id:'canonical',numerator:automaticNs,denominator:'1'}:{...fields};
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
      case 'applied':return t('applied');
      case 'failed':return t('failed');
      case 'succeeded':return copy('succeeded','成功');
      case 'admitted':return t('admitted');
      default:return status;
    }
  };
  const setPoint_=(id:string)=>{const next=points.find(row=>row.id===id);setPoint(id);setPayload(id==='accident'?(recordedAccidentPayload(store)??{}):{});setFields(next?transportForPoint(next,{operator:operatorReady,operatorNs:operatorNs.current}):emptyTransport());};
  const ready=point!=='accident'||mapping(payload)&&mapping(payload.incident);
  const descriptor=typeof event?.schema==='string'?definitions[event.schema]:event?.schema;
  const payloadSchema=mapping(descriptor)&&mapping(descriptor.members)?{...descriptor,members:Object.fromEntries(Object.entries(descriptor.members).filter(([key])=>!['request_id','source_cut'].includes(key))),required:Array.isArray(descriptor.required)?descriptor.required.filter(key=>!['request_id','source_cut'].includes(String(key))):descriptor.required}:descriptor;
  // The portable runtime registry strips authoring subject annotations. Read
  // them from this run's pinned configuration rather than guessing from IDs.
  const formSchema=entityPayloadSchema(payloadSchema,authoredMessages.find(row=>row.id===event?.id)?.subjects??event?.subjects,definitions);
  const payloadReady=payloadValid&&!payloadProblem(injection?payloadSchema:chosen?.schema,payload,definitions);
  const choices=[...store.entities.values()].map(entity=>({label:displayName(entity,store.header,locale),value:{$ref:{run_id:store.header.kernelRunId,epoch:store.header.epoch,id:entity.key.id,generation:{$integer:String(entity.key.generation)},type_id:entity.typeId}},typeId:entity.typeId,ancestors:store.header.types.find(row=>row.typeId===entity.typeId)?.ancestors??[]}));
  useEffect(()=>{
    if(!injection||!selected||!mapping(formSchema)||!mapping(formSchema.members))return;
    const choice=choices.find(row=>mapping(row.value.$ref)&&row.value.$ref.id===selected.id&&String(row.value.$ref.generation.$integer)===String(selected.generation));
    if(!choice)return;
    const members=formSchema.members;
    setPayload((previous:unknown)=>{
      const row=mapping(previous)?previous:{},next={...row};
      for(const [key,member] of Object.entries(members)){
        if(row[key]!==undefined||!mapping(member)||typeof member.target_type!=='string')continue;
        if(choice.typeId===member.target_type||choice.ancestors.includes(member.target_type))next[key]=member.type==='string'?selected.id:choice.value;
      }
      return Object.keys(next).length===Object.keys(row).length?previous:next;
    });
  },[point,JSON.stringify(formSchema),selected?.id,selected?.generation,store.viewCursor?.knownAt]);
  const eventLabel=(row:Draft)=>readableLabel(String(row.name??row.id),locale).toLocaleLowerCase(locale);
  const targetSummary=(row:Draft)=>{
    if(row.id!=='accident')return;
    const incidents=[...store.entities.values()].filter(entity=>entity.typeId==='aas:TrafficIncident');
    return incidents.length?t('Trigger the accident response for {entities}. Choose an actor or add a reason if needed.',{entities:incidents.map(entity=>displayName(entity,store.header,locale)).join(locale.startsWith('zh')?'、':', ')}):t('Waiting for the run’s incident entity to load.');
  };
  const operationHint=mode==='replay'?t('Replay is read-only; switch to live mode to operate an active run.'):viewingHistory?copy('Viewing history. Return to live before submitting an operation.','正在查看历史时刻。请返回实时后再提交操作。'):!interactive?t('Run controls are unavailable in this view.'):!effective?t('Checking run status…'):!canOperate?t('This run is {status}; controls are unavailable.',{status:statusLabel(effective)}):undefined;
  const eventTarget=injection?targetSummary(injection):undefined;
  const admissionSummary=(receipt:unknown)=>{
    if(!mapping(receipt))return t('Receipt could not be interpreted; read the raw record below.');
    // Status precedence: a journaled execution outcome (succeeded/failed/
    // rejected) outranks a mere "admitted"; admitted is never shown as success.
    if(receipt.stored===true){
      const status=typeof receipt.execution_status==='string'?receipt.execution_status:String(receipt.status);
      if(status==='rejected')return t('Event rejected ({code}): it was not executed.',{code:String(receipt.code??t('no code'))});
      if(status==='failed')return t('Admitted but execution failed: {detail}',{detail:String(receipt.detail??t('no detail recorded'))});
      if(status==='succeeded'||status==='applied')return t('Executed. Its records appear in the timeline.');
      if(['accepted','admitted','delayed'].includes(status))return t('Admitted and awaiting execution. The execution receipts show what happened next.');
      return copy(`Unrecognised execution state: ${status}. Read the recorded details.`,`无法识别执行状态：${status}。请查看记录详情。`);
    }
    switch(receipt.disposition){
      case 'accepted':return t('Event accepted for processing. The execution receipts show what happened next.');
      case 'delayed':return t('Event scheduled for later. Check its execution receipt after it runs.');
      case 'rejected':return t('Event rejected ({code}): it was not executed.',{code:String(receipt.code??t('no code'))});
      default:return t('Receipt without an admission disposition; read the raw record below.');
    }
  };
  const admissionState=(receipt:unknown):'ok'|'bad'|'wait'|'info'=>{
    if(!mapping(receipt))return 'info';
    if(receipt.stored===true){
      const status=typeof receipt.execution_status==='string'?receipt.execution_status:String(receipt.status);
      if(status==='succeeded'||status==='applied')return 'ok';
      if(status==='failed'||status==='rejected')return 'bad';
      return ['accepted','admitted','delayed'].includes(status)?'wait':'info';
    }
    return receipt.disposition==='accepted'||receipt.disposition==='delayed'?'wait':receipt.disposition==='rejected'?'bad':'info';
  };
  const admissionTitle=(receipt:unknown)=>{
    const parts=[mapping(receipt)&&typeof receipt.id==='string'?String(receipt.id):''];
    if(mapping(receipt)&&receipt.submitted_at!==undefined){
      if(typeof receipt.submitted_at==='string')parts.push(receipt.submitted_at);
      else if(mapping(receipt.submitted_at)){
        try{
          const ns=exactNanos(receipt.submitted_at.ns,'submitted_at.ns');
          const microstep=receipt.submitted_at.microstep;
          if(typeof microstep!=='number'||!Number.isSafeInteger(microstep)||microstep<0)throw Error('submitted_at.microstep');
          const seconds=`${ns/1_000_000_000n}${ns%1_000_000_000n?'.'+(ns%1_000_000_000n).toString().padStart(9,'0').replace(/0+$/,''):''}`;
          parts.push(copy(`submitted at ${seconds} s · microstep ${microstep}`,`提交于 ${seconds} 秒 · 微步 ${microstep}`));
        }catch{parts.push(copy('Invalid recorded submission time','记录的提交时间无效'));}
      }else parts.push(copy('Invalid recorded submission time','记录的提交时间无效'));
    }
    if(mapping(receipt)&&typeof receipt.target_cut==='number')parts.push(t('target cut {cut}',{cut:receipt.target_cut}));
    return parts.filter(Boolean).join(' · ');
  };
  /** Journaled timeline commits that carry this admission's records. */
  const receiptCuts=(receipt:unknown):number[]=>{
    const id=mapping(receipt)&&typeof receipt.id==='string'?String(receipt.id):undefined;
    if(!id)return [];
    const recorded=mapping(receipt)?[receipt.execution_cut,receipt.target_cut].filter((cut):cut is number=>typeof cut==='number'&&Number.isSafeInteger(cut)&&cut>0):[];
    return [...new Set([...recorded,...store.commits.filter(commit=>commit.messages.some(message=>String(message.id).includes(id))).map(commit=>commit.commitIndex)])];
  };
  return <section className="run-operations" aria-label={t('Run operations')} aria-busy={busy}>
    {operationHint&&<p>{operationHint}</p>}
    {operator&&horizon!==undefined&&mode==='live'&&<Details title={t('Operator source progress')} buttonLabel={t('Source timing')}><p>{t('Operator source progress is closed through {time}. Events enter at the next unclosed simulation instant.',{time:displayTime(horizon)})}</p></Details>}
    <div className="injection-options" role="group" aria-label={t('Authored events')}>{points.map(row=><button type="button" className="console-btn" disabled={busy} aria-pressed={row.id===point} key={String(row.id)} onClick={()=>setPoint_(String(row.id))}>{t('Inject {event}',{event:eventLabel(row)})}</button>)}</div>
    {injection?<div className="injection-card" data-testid="injection-card">
      <h3>{t('Inject {event}',{event:eventLabel(injection)})}</h3>
      {eventTarget&&<p>{eventTarget}</p>}
      <TypedPayloadForm schema={formSchema} value={payload} onChange={setPayload} definitions={definitions} onValidityChange={setPayloadValid} entityChoices={choices}/>
      <button type="button" className="console-btn console-btn-primary" data-injection-submit data-testid={`inject-${point}`} aria-label={t('Submit typed ingress')} disabled={busy||!payloadReady||!canOperate||!ready||fields.stream_id==='operator'&&scheduledOnly||!injectionSourceReady||!chosen} onClick={()=>execute(submit)}>{t('Inject {event}',{event:eventLabel(injection)})}</button>
      <p className="injection-hint">{scheduledOnly&&fields.stream_id==='operator'?copy('This run’s operator input interval is closed. Start a run with live inputs to add events.','本次运行的操作员输入区间已经关闭。请启动实时输入运行以添加事件。'):!payloadReady?copy('Complete the required event details with values allowed by the event contract.','请填写事件契约要求的必填信息及允许的数值。'):operatorReady?t('Timing and request information are filled in automatically.'):t('Complete the required timing fields in Advanced before submitting.')}</p>
      <Details title={t('Injection transport')} buttonLabel={t('Advanced')}><div className="run-transport-fields">{Object.entries(fields).map(([key,value])=><label key={key}>{key}<input aria-label={t('Ingress {field}',{field:key})} disabled={busy} value={value} onChange={event=>setFields({...fields,[key]:event.target.value})}/></label>)}</div><pre>{exactValue({request_id:'generated when sent',source_cut:store.viewCursor?.knownAt,point:injection})}</pre></Details>
    </div>:<div className="injection-card"><h3>{t('Issue command')}</h3>
      <p className="injection-hint">{copy('Choose an authored event, or use a registered command with its declared input source and timing fields below.','请选择已定义的事件，或为已注册的命令填写声明的输入源和时序字段。')}</p>
      <label>{t('Command')} <select aria-label={t('Command schema')} disabled={busy} value={fields.schema} onChange={event=>{setFields({...fields,schema:event.target.value});setPayload({});}}><option value="">{t('Select registered command')}</option>{commands.filter(row=>row.id!=='aas.runtime.inject_event').map(row=><option key={String(row.id)} value={String(row.id)}>{readableLabel(String(row.id),locale)}</option>)}</select></label>
      {!commands.length&&<p>{t('No registered commands are available for this run.')}</p>}
      <TypedPayloadForm schema={chosen?.schema} value={payload} onChange={setPayload} definitions={definitions} onValidityChange={setPayloadValid} entityChoices={choices}/>
      <button type="button" className="console-btn" data-injection-submit disabled={busy||!payloadReady||!canOperate||!injectionSourceReady||!chosen} onClick={()=>execute(submit)}>{t('Submit typed ingress')}</button>
      <Details title={t('Command transport')} buttonLabel={t('Advanced')}><div className="run-transport-fields">{Object.entries(fields).filter(([key])=>key!=='schema').map(([key,value])=><label key={key}>{key}<input aria-label={t('Ingress {field}',{field:key})} disabled={busy} value={value} onChange={event=>setFields({...fields,[key]:event.target.value})}/></label>)}</div></Details>
    </div>}
    <Details title={t('Other commands')} buttonLabel={t('Direct typed command')}><label>{t('Injection point')} <select aria-label={t('Injection point')} disabled={busy} value={point} onChange={event=>setPoint_(event.target.value)}><option value="">{t('Direct typed command')}</option>{points.map(row=><option key={String(row.id)} value={String(row.id)}>{readableLabel(String(row.id),locale)}</option>)}</select></label><button type="button" disabled={busy} onClick={()=>setPoint_('')}>{t('Configure a direct command')}</button><p>{chosen?.cancel_support===true?t('This command supports cancellation.'):t('Send an authored cancellation event where available.')}</p></Details>
    {error&&<p role="alert">{error}</p>}
    {progressError&&<p role="alert">{progressError}</p>}
    <h3>{copy('Recorded input and execution','已记录的输入与执行')}</h3>
    <details open={admissions.length>0||!!admissionError}><summary>{t('Admission receipts · {count}',{count:admissions.length})}</summary>
      {admissionError&&<p className="wb-issues" role="alert">{admissionError}</p>}
      {!admissions.length&&!admissionError&&<p>{t('No admission receipts yet.')}</p>}
      {admissions.map((receipt,index)=>{
        const id=mapping(receipt)&&typeof receipt.id==='string'?String(receipt.id):undefined;
        const cuts=receiptCuts(receipt);
        return <article key={id??index} className="receipt-card" data-admission-id={id} data-receipt-state={admissionState(receipt)}>
          <header className="receipt-head">
            <strong>{t('Admission #{number}',{number:index+1})}</strong>
            {mapping(receipt)&&typeof receipt.status==='string'&&<span className="wb-tag" data-status={String(receipt.status)}>{statusLabel(receipt.status)}</span>}
          </header>
          {admissionTitle(receipt)&&<small className="wb-mono receipt-meta">{admissionTitle(receipt)}</small>}
          <p className="receipt-help">{admissionSummary(receipt)}</p>
          {mapping(receipt)&&typeof receipt.execution_status==='string'&&<p className="receipt-correlate">{copy('Execution','执行')} · {statusLabel(receipt.execution_status)}</p>}
          {mapping(receipt)&&receipt.execution_result!==undefined&&<section aria-label={copy('Execution result','执行结果')}><RecordFields record={receipt.execution_result} /></section>}
          {cuts.length>0&&<p className="receipt-correlate">{t('In the timeline:')} {cuts.slice(0,4).map(cut=><button type="button" key={cut} onClick={()=>onSeek(cut)}>{t('cut {cut}',{cut})}</button>)}</p>}
          <Details title={t('Admission receipt')} buttonLabel={t('Details')}><RecordFields record={receipt} /><details><summary>{copy('Raw record','原始记录')}</summary><pre className="wb-mono">{exactValue(receipt)}</pre></details></Details>
        </article>;
      })}
    </details>
    <details><summary>{t('Execution receipts at this cut · {count}',{count:store.receipts.length})}</summary>
      {!store.receipts.length&&<p>{t('No execution receipts at this cut.')}</p>}
      {store.receipts.length>100&&<p>{t('Latest 100 of {count} execution receipts. All records are retained in the journal.',{count:store.receipts.length})}</p>}
      {store.receipts.slice(-100).map((receipt,index)=>{
        // The typed journal shape is { commandId, status, result }; the raw
        // disclosure below still shows whatever extra fields the journal adds.
        const receiptId=typeof receipt.commandId==='string'?receipt.commandId:undefined;
        const outcome=typeof receipt.status==='string'?receipt.status:undefined;
        return <article key={index} className="receipt-card" data-receipt-state={outcome==='failed'||outcome==='rejected'?'bad':outcome==='succeeded'?'ok':'info'}>
          <header className="receipt-head">
            <strong>{t('Execution #{number}',{number:Math.max(0,store.receipts.length-100)+index+1})}</strong>
            {outcome&&<span className="wb-tag" data-status={outcome}>{statusLabel(outcome)}</span>}
          </header>
          {/* Precedence: the journaled execution outcome of the linked admission outranks "admitted". */}
          {receiptId&&admissions.some(row=>mapping(row)&&row.id===receiptId)&&<p className="receipt-correlate">{t('Response to admission {id}.',{id:receiptId})} {outcome==='succeeded'?t('Executed. Its records appear in the timeline.'):outcome?statusLabel(outcome):t('Admitted and awaiting execution. The execution receipts show what happened next.')}</p>}
          {receiptId&&<small className="wb-mono receipt-meta">{receiptId}</small>}
          <Details title={t('Execution receipt')} buttonLabel={t('Details')}><RecordFields record={receipt} /><details><summary>{copy('Raw record','原始记录')}</summary><pre className="wb-mono">{exactValue(receipt)}</pre></details></Details>
        </article>;
      })}
    </details>
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
