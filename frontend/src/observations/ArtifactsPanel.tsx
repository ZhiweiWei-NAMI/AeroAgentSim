import { useEffect, useState } from 'react';
import { RunsApi } from '../feeds/http';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { Details } from '../console/Details';
import { ConceptHelp } from '../console/ConceptHelp';
import { mapping, type Draft } from '../behaviours/model';
interface Props { api:RunsApi;runId:string;store:TemporalFeedStore;commitCut?:number;onSeek:(index:number)=>void }
/** Disk storage is independent of publication and business acceptance in the journal. */
export function ArtifactsPanel({api,runId,store,onSeek}:Props) {
  const [owners,setOwners]=useState<Array<{id:string;fields:Draft}>>(),[ownershipError,setOwnershipError]=useState('');
  const [records,setRecords]=useState<Draft[]>(),[error,setError]=useState(''),[image,setImage]=useState<{digest:string;url:string}>();
  const refresh=async(signal?:AbortSignal)=>{try{const value=await api.request(`/v1/runs/${encodeURIComponent(runId)}/artifacts`,{signal});if(!Array.isArray(value)||!value.every(mapping))throw Error('Artifact list is malformed');if(signal?.aborted)return;setRecords(value);setError('');}catch(problem){if(!signal?.aborted)setError(String(problem));}};
  useEffect(()=>{setRecords(undefined);setImage(undefined);setError('');const abort=new AbortController();void refresh(abort.signal);return()=>abort.abort();},[api,runId]);
  useEffect(()=>{const abort=new AbortController();setOwners(undefined);setOwnershipError('');void api.request(`/v1/studio/runs/${encodeURIComponent(runId)}/configuration`,{signal:abort.signal}).then(value=>{if(!mapping(value)||!mapping(value.scenario)||!mapping(value.scenario.engines))throw Error('Capture owner configuration unavailable');const configured=Object.entries(value.scenario.engines).flatMap(([id,engine])=>mapping(engine)&&['capture','traffic_camera_capture'].includes(String(engine.plugin))&&mapping(engine.config)&&mapping(engine.config.fields)?[{id,fields:engine.config.fields}]:[]);if(!abort.signal.aborted)setOwners(configured);}).catch(problem=>{if(!abort.signal.aborted)setOwnershipError(String(problem));});return()=>abort.abort();},[api,runId]);
  useEffect(()=>()=>{if(image)URL.revokeObjectURL(image.url);},[image]);
  const open=async(row:Draft)=>{try{
    if(typeof row.digest!=='string'||!row.digest)throw Error('Recorded artifact ID is missing');
    const result=await fetch(`${api.base}/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(row.digest)}/download`);if(!result.ok)throw Error(`Artifact HTTP ${result.status}: ${await result.text()}`);
    const bytes=await result.arrayBuffer();
    if(bytes.byteLength!==row.byte_count)throw Error('Stored artifact byte count mismatch');
    const digest=row.digest;
    setImage({digest,url:URL.createObjectURL(new Blob([bytes],{type:'image/png'}))});setError('');
  }catch(problem){setError(String(problem));}};
  return <section aria-label="Stored capture artifacts"><h2>Captured photos<ConceptHelp topic="Captured photos" description="Photos retain their acquisition moment; seeking a photo moves both views to that moment." guide="observations.md"/></h2><button onClick={()=>void refresh()}>Refresh stored artifacts</button>{error&&<p role="alert">{error}</p>}{ownershipError&&<p>Publication ownership unavailable: {ownershipError}</p>}
    {records?.length===0&&<p>No stored artifacts.</p>}{records===undefined&&!error&&<p>Loading artifact storage…</p>}
    {records?.map((row,index)=>{const request=mapping(row.request)?row.request:undefined,source=request&&mapping(request.source_cut)?request.source_cut:undefined;
      const published=owners?.flatMap(owner=>[...store.entities.values()].filter(entity=>{const digest=typeof owner.fields.digest==='string'?entity.fields.get(owner.fields.digest):undefined;const requestId=typeof owner.fields.request_id==='string'?entity.fields.get(owner.fields.request_id):undefined;return digest?.producer===owner.id&&digest.value===row.digest&&requestId?.value===request?.request_id;}).map(entity=>{const status=typeof owner.fields.storage_status==='string'?entity.fields.get(owner.fields.storage_status):undefined;return status?exactValue(status.value):'status not recorded';}));
      return <article key={`${row.digest}/${index}`}><strong>{request?String(request.request_id):'Malformed request metadata'}</strong>
        <p>Storage: actual bytes listed · renderer: {String(row.renderer_mode)} · publication at selected cut: {published===undefined?'owner metadata unavailable':published.length?`recorded metadata: ${published.join(', ')}`:'not recorded at this cut'}. Task acceptance is determined by its recorded chain/receipts.</p>
        {source&&typeof source.index==='number'&&Number.isSafeInteger(source.index)&&<button onClick={()=>onSeek(source.index as number)}>Seek photo moment</button>}
        <button onClick={()=>void open(row)}>Open photo</button>
        <Details title={request?String(request.request_id):'Artifact metadata'} buttonLabel="Camera / actor / acquisition / source cut">
          <pre>{exactValue(row)}</pre>
        </Details>
      </article>;
    })}{image&&<figure><img src={image.url} alt="Stored capture photo" style={{maxWidth:'100%'}}/><figcaption>stored bytes, publication status shown above</figcaption></figure>}
  </section>;
}
