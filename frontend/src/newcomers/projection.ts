import type { FeedCommit } from '../contracts/viewer-feed';
import type { BusinessEvent, KpiSample, KpiValues, RunKpis } from './api';
export interface RecordedBusinessEvent extends BusinessEvent {cut:number;offset:number;key:string}
/** Read only the committed prefix visible at the shared cursor. */
export function businessProjection(commits:FeedCommit[],knownAt:number,atNs:string){
 const events:RecordedBusinessEvent[]=[],series:KpiSample[]=[];
 let values:KpiValues={};
 for(let offset=0;offset<commits.length;offset++){
  const commit=commits[offset];
  if(commit.commitIndex>knownAt||BigInt(commit.at.ns)>BigInt(atNs))break;
  if(commit.kpis){values={...values,...commit.kpis};series.push({commitIndex:commit.commitIndex,at_ns:commit.at.ns,kpis:{...values}});}
  for(const [index,event] of (commit.events??[]).entries())if(BigInt(event.at_ns)<=BigInt(atNs))events.push({...event,cut:commit.commitIndex,offset,key:`${commit.commitIndex}/${index}`});
 }
 return {events,values,series};
}
export function resultsAt(data:RunKpis|undefined,atNs:string,atEnd:boolean,knownAt?:number){
 if(!data)return {values:{} as KpiValues,series:[] as KpiSample[]};
 const series=data.series.filter(row=>BigInt(row.at_ns)<=BigInt(atNs)&&(knownAt===undefined||row.commitIndex===undefined||row.commitIndex<=knownAt));
 // Final values are usable at the end only; a replay must not show future results.
 return {values:atEnd?data.final:series.at(-1)?.kpis??{},series};
}
