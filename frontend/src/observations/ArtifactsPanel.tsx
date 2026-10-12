import { useEffect, useRef, useState } from 'react';
import { Modal } from 'antd';
import { RunsApi } from '../feeds/http';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { Details } from '../console/Details';
import { ConceptHelp } from '../console/ConceptHelp';
import { mapping, type Draft } from '../behaviours/model';
import { stateValue } from '../pages/inspection-format';
import { displayTime } from '../pages/display-time';
import { useI18n } from '../i18n/I18nProvider';
import './observations.css';
interface Props { api:RunsApi;runId:string;store:TemporalFeedStore;commitCut?:number;live?:boolean;onSeek:(index:number)=>void }
/** Disk storage is independent of publication and business acceptance in the journal. */
export function ArtifactsPanel({api,runId,store,live,onSeek}:Props) {
  const { t, locale } = useI18n();
  const [owners,setOwners]=useState<Array<{id:string;fields:Draft}>>(),[ownershipError,setOwnershipError]=useState('');
  const [records,setRecords]=useState<Draft[]>(),[error,setError]=useState(''),[image,setImage]=useState<{digest:string;url:string;record:Draft}>(),[opening,setOpening]=useState<string>();
  const [fullSize,setFullSize]=useState(false);
  const openRequest = useRef<AbortController>();
  const listSequence = useRef(0), photo = useRef<HTMLElement>(null);
  const observing = useRef(live);observing.current=live;
  const refresh=async(signal?:AbortSignal)=>{const sequence=++listSequence.current;try{const value=await api.request(`/v1/runs/${encodeURIComponent(runId)}/artifacts`,{signal});if(!Array.isArray(value)||!value.every(mapping))throw Error(t('Artifact list is malformed'));if(signal?.aborted||sequence!==listSequence.current)return;setRecords(value);setError('');}catch(problem){if(!signal?.aborted&&sequence===listSequence.current)setError(String(problem));}};
  useEffect(()=>{
    setRecords(undefined);setImage(undefined);setFullSize(false);setError('');setOpening(undefined);
    const abort=new AbortController();let loading=false,lastCommit=store.commits.at(-1)?.commitIndex;
    const load=async()=>{if(loading||abort.signal.aborted)return;loading=true;try{await refresh(abort.signal);}finally{loading=false;}};
    void load();
    // Storage can finish just after its feed commit. Follow both commits and
    // live publication while the tab is visible; replay needs no polling.
    const timer=setInterval(()=>{const next=store.commits.at(-1)?.commitIndex;if(observing.current||store.liveFollow||next!==lastCommit){lastCommit=next;void load();}},1000);
    return()=>{abort.abort();clearInterval(timer);++listSequence.current;openRequest.current?.abort();};
  },[api,runId,store]);
  useEffect(()=>{const abort=new AbortController();setOwners(undefined);setOwnershipError('');void api.request(`/v1/studio/runs/${encodeURIComponent(runId)}/configuration`,{signal:abort.signal}).then(value=>{if(!mapping(value)||!mapping(value.scenario)||!mapping(value.scenario.engines))throw Error(t('Capture owner configuration unavailable'));const configured=Object.entries(value.scenario.engines).flatMap(([id,engine])=>mapping(engine)&&['capture','traffic_camera_capture'].includes(String(engine.plugin))&&mapping(engine.config)&&mapping(engine.config.fields)?[{id,fields:engine.config.fields}]:[]);if(!abort.signal.aborted)setOwners(configured);}).catch(problem=>{if(!abort.signal.aborted)setOwnershipError(String(problem));});return()=>abort.abort();},[api,runId]);
  useEffect(()=>()=>{if(image)URL.revokeObjectURL(image.url);},[image]);
  useEffect(()=>{if(image)photo.current?.scrollIntoView({block:'start'});},[image]);
  const open=async(row:Draft)=>{
    openRequest.current?.abort();const abort=new AbortController();openRequest.current=abort;setOpening(String(row.digest));
    try {
      if(typeof row.digest!=='string'||!row.digest)throw Error(t('Recorded artifact ID is missing'));
      const result=await fetch(`${api.base}/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(row.digest)}/download`,{signal:abort.signal});if(!result.ok)throw Error(t('Artifact HTTP {status}: {error}', {status:result.status,error:await result.text()}));
      const bytes=await result.arrayBuffer();
      if(bytes.byteLength!==row.byte_count)throw Error(t('Stored artifact byte count mismatch'));
      if(abort.signal.aborted)return;
      setImage({digest:row.digest,url:URL.createObjectURL(new Blob([bytes],{type:'image/png'})),record:row});setFullSize(false);setError('');
    }catch(problem){if(!abort.signal.aborted)setError(String(problem));}
    finally {if(!abort.signal.aborted)setOpening(undefined);}
  };
  const rendererLabel=(value:unknown)=>value==='browser'?t('Browser'):value==='cpu'?t('CPU'):value==='gpu'?t('GPU'):String(value);
  const request=image&&mapping(image.record.request)?image.record.request:undefined;
  const actor=request&&mapping(request.actor)?request.actor:undefined,source=request&&mapping(request.source_cut)?request.source_cut:undefined;
  const caption=<>
    {request&&typeof request.request_id==='string'&&<strong>{request.request_id}</strong>}
    {actor&&typeof actor.id==='string'&&<span>{t('Actor: {actor}',{actor:actor.id})}</span>}
    {source&&typeof source.index==='number'&&<span>{t('Source cut #{cut}',{cut:source.index})}</span>}
    {source&&Array.isArray(source.instant)&&(typeof source.instant[0]==='string'||typeof source.instant[0]==='number')&&<span>{t('Source time: {time}',{time:displayTime(String(source.instant[0]),store.header.start.ns)})}</span>}
    <span>{t('Stored capture photo')}</span>
  </>;
  return <><section className={`capture-artifacts${image?' has-photo':''}`} aria-label={t('Stored capture artifacts')}>
    {image&&<figure ref={photo} tabIndex={-1} className="capture-photo">
      <button type="button" className="capture-photo-open" aria-label={t('View full-size photo')} title={t('View full-size photo')} aria-haspopup="dialog" onClick={()=>setFullSize(true)}>
        <img src={image.url} alt={t('Stored capture photo')} onLoad={()=>photo.current?.scrollIntoView({block:'start'})}/>
      </button>
      <figcaption>{request&&typeof request.request_id==='string'&&<strong>{request.request_id}</strong>}<span>{t('Click the photo to view full size.')}</span></figcaption>
    </figure>}
    <div className="capture-artifact-list"><h2>{t('Captured photos')}<ConceptHelp topic={t('Captured photos')} description={t('Photos retain their acquisition moment; seeking a photo moves both views to that moment.')} guide="observations.md"/></h2><button type="button" className="console-btn" onClick={()=>void refresh()}>{t('Refresh stored artifacts')}</button>{error&&<p role="alert">{error}</p>}{ownershipError&&<p>{t('Publication ownership unavailable: {error}', {error:ownershipError})}</p>}
    {records?.length===0&&<p>{t('No stored artifacts.')}</p>}{records===undefined&&!error&&<p>{t('Loading artifact storage…')}</p>}
    {records?.map((row,index)=>{const request=mapping(row.request)?row.request:undefined,source=request&&mapping(request.source_cut)?request.source_cut:undefined;
      const published=owners?.flatMap(owner=>[...store.entities.values()].filter(entity=>{const digest=typeof owner.fields.digest==='string'?entity.fields.get(owner.fields.digest):undefined;const requestId=typeof owner.fields.request_id==='string'?entity.fields.get(owner.fields.request_id):undefined;return digest?.producer===owner.id&&digest.value===row.digest&&requestId?.value===request?.request_id;}).map(entity=>{const status=typeof owner.fields.storage_status==='string'?entity.fields.get(owner.fields.storage_status):undefined;return status?stateValue(status.value,locale)??exactValue(status.value):t('status not recorded');}));
      const publication=published===undefined?t('owner metadata unavailable'):published.length?t('recorded metadata: {statuses}', {statuses:published.join(', ')}):t('not recorded at this cut');
      return <article key={`${row.digest}/${index}`}><strong>{request?String(request.request_id):t('Malformed request metadata')}</strong>
        <p>{t('Storage: actual bytes listed · renderer: {renderer} · publication at selected cut: {publication}. Task acceptance is determined by its recorded chain/receipts.', {renderer:rendererLabel(row.renderer_mode),publication})}</p>
        {source&&typeof source.index==='number'&&Number.isSafeInteger(source.index)&&source.index>=1&&<button type="button" className="console-btn" onClick={()=>onSeek(source.index as number)}>{t('Seek photo moment')}</button>}
        <button type="button" className="console-btn" disabled={opening===row.digest} onClick={()=>void open(row)}>{opening===row.digest?t('Opening photo…'):t('Open photo')}</button>
        <Details title={request?String(request.request_id):t('Artifact metadata')} buttonLabel={t('Camera / actor / acquisition / source cut')}>
          <pre>{exactValue(row)}</pre>
        </Details>
      </article>;
    })}
    </div>
  </section>
  <Modal className="capture-photo-dialog" title={t('Full-size photo')} open={fullSize&&!!image} onCancel={()=>setFullSize(false)} footer={null} width="min(1200px, calc(100vw - 48px))" centered closable={{'aria-label':t('Close')}}>
    {image&&<figure><img src={image.url} alt={t('Stored capture photo')}/><figcaption>{caption}</figcaption></figure>}
  </Modal></>;
}
