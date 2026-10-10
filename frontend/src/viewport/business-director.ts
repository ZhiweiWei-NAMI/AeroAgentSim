import type {EntityKey} from '../contracts/viewer-feed';
import type {RecordedBusinessEvent} from '../newcomers/projection';
import type {FeedStore} from './feed-store';
import {resolveBinding} from './bindings';

export const MIN_DIRECTOR_CUT_MS = 4000;
const SHOT_MS = 6000, MAX_PENDING = 12;
export interface DirectorTarget {eventKey:string;entity?:EntityKey;position?:[number,number,number]}
export function notableEvent(event: RecordedBusinessEvent): boolean {
  return event.severity !== 'info' || ['energy','airspace','weather','emergency'].includes(event.category);
}
/** Prefer an involved aircraft over a task/site; all associations are recorded. */
export function directorTarget(store:FeedStore,event:RecordedBusinessEvent):DirectorTarget|undefined {
  const involved=[...store.entities.values()].filter(entity=>event.entities.includes(entity.key.id));
  for(const task of involved){
    const vehicle=task.fields.get('lowalt.task_vehicle')?.value;
    const entity=[...store.entities.values()].find(entity=>entity.key.id===vehicle);
    if(entity&&!involved.includes(entity))involved.push(entity);
  }
  const spatial=involved.filter(entity=>{
    const binding=resolveBinding(store.header,entity.typeId);
    return binding&&store.sample(entity.key,binding.positionField,event.at_ns)!==undefined;
  });
  const entity=spatial.find(entity=>entity.typeId==='lowalt:UAV'||resolveBinding(store.header,entity.typeId)?.visual.catalog==='uav')??spatial[0];
  return entity||event.position?{eventKey:event.key,entity:entity?.key,position:event.position}:undefined;
}

/** Wall-clock scheduling, independent of playback speed. Never writes the feed. */
export class BusinessDirector {
  private seen=new Set<string>();
  private pending:RecordedBusinessEvent[]=[];
  private lastCut=-Infinity;
  private cursor?:{ns:bigint;cut:number};
  private paused=false;
  current?:RecordedBusinessEvent;
  pauseOnInteraction(){this.paused=true;}
  resume(){this.paused=false;}
  get interactionPaused(){return this.paused;}
  tick(events:RecordedBusinessEvent[],wallMs:number,active:boolean,atNs:string,knownAt:number):RecordedBusinessEvent|undefined {
    const ns=BigInt(atNs);
    if(this.cursor&&(ns<this.cursor.ns||knownAt<this.cursor.cut)){
      this.seen.clear();this.pending=[];this.current=undefined;
      // Keep lastCut: seeking/resuming cannot bypass the minimum cut interval.
    }
    this.cursor={ns,cut:knownAt};
    for(const event of events){
      if(event.cut>knownAt||BigInt(event.at_ns)>ns||this.seen.has(event.key))continue;
      this.seen.add(event.key);
      if(notableEvent(event))this.pending.push(event);
    }
    // A fast live feed may arrive in bursts. Bound latency instead of touring
    // minutes of stale history; retained event cards still show every event.
    if(this.pending.length>MAX_PENDING)this.pending=this.pending.slice(-MAX_PENDING);
    if(!active||this.paused||!this.pending.length)return;
    if(wallMs-this.lastCut<Math.max(MIN_DIRECTOR_CUT_MS,this.current?SHOT_MS:0))return;
    this.current=this.pending.shift();this.lastCut=wallMs;
    return this.current;
  }
}
