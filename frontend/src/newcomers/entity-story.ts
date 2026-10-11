import type {ChainInstance, EntityKey} from '../contracts/viewer-feed';
import type {TemporalFeedStore} from '../feeds/temporal-store';
import type {EntityState} from '../viewport/feed-store';
import {entityId} from '../viewport/bindings';
import {chargingPadAssignments} from '../viewport/operational-loads';
import {businessStyle,displayName,isLowaltTask,semanticVehicle,taskByVehicle} from '../viewport/semantic';
import {displayLabel} from '../pages/inspection-format';
import {translate} from '../i18n/translate';
import type {CauseRef} from '../trace/cause-trace';
import {eventNarrative} from './presentation';
import type {RecordedBusinessEvent} from './projection';

const number=(value:unknown):number|undefined=>typeof value==='number'&&Number.isFinite(value)?value:undefined;
const text=(value:unknown):string|undefined=>typeof value==='string'&&value?value:undefined;
const field=(entity:EntityState,id:string)=>entity.fields.get(`lowalt.${id}`)?.value;
const same=(a:EntityKey,b:EntityKey)=>entityId(a)===entityId(b);
const shortId=(id:string)=>id.split(/[./:]/).at(-1);
const revision=(value:ChainInstance['revision'])=>typeof value==='object'?value.$integer:value;

/** Only current, prefix-filtered chain records can describe an ongoing action. */
function ruleChain(store:TemporalFeedStore,entity:EntityState,name:string,state:string){
 return [...store.chainInstances.values()].filter(row=>shortId(row.templateId)===name&&row.state===state&&Object.values(row.roles).some(key=>same(key,entity.key)))
  .sort((a,b)=>BigInt(a.validFrom.ns)>BigInt(b.validFrom.ns)?-1:BigInt(a.validFrom.ns)<BigInt(b.validFrom.ns)?1:b.validFrom.microstep-a.validFrom.microstep)[0];
}
function parameter(store:TemporalFeedStore,predicate:string,name:string):number|undefined {
 const behaviour=store.header.behaviour as {packages?:Array<{predicates?:Record<string,{parameters?:Record<string,unknown>}>}>}|undefined;
 for(const pack of behaviour?.packages??[])for(const [id,definition] of Object.entries(pack.predicates??{}))if(shortId(id)===predicate)return number(definition.parameters?.[name]);
 return undefined;
}
export interface EntityStory {
 label:string;business:string;vehicleClass:string;battery?:number;task?:EntityState;nextStop?:string;
 status:string;cause?:CauseRef;latestEvent?:RecordedBusinessEvent;journey:RecordedBusinessEvent[];
}

/** Read-only explanation of the shared committed cut; no engine or future-state guesses. */
export function entityStory(store:TemporalFeedStore,entity:EntityState,events:RecordedBusinessEvent[],locale='en-US'):EntityStory {
 const t=(key:string,vars:Record<string,unknown>={})=>translate(locale,key,vars);
 const task=isLowaltTask(entity,store.header)?entity:taskByVehicle(store).get(entityId(entity.key));
 const known=store.viewCursor?.knownAt??-1,ns=BigInt(store.viewCursor?.validAt.ns??store.header.start.ns);
 const journey=events.filter(event=>event.cut<=known&&BigInt(event.at_ns)<=ns&&(event.entities.includes(entity.key.id)||!!task&&event.entities.includes(task.key.id))).slice(-6);
 const latestEvent=journey.at(-1);
 const names=new Map([...store.entities.values()].map(row=>[row.key.id,displayName(row,store.header,locale)]));
 const lookup=(id:string|undefined)=>id?names.get(id)??id:undefined;
 const phase=text(task&&field(task,'task_state'));
 const nextId=text(task&&field(task,['awaiting_preparation','preparing','queued','assigned','started'].includes(phase??'')?'task_origin':'task_destination'));
 const control=[...store.messages].reverse().find(row=>row.kind==='event'&&row.schemaId==='lowalt.control'&&row.subjects?.some(key=>same(key,entity.key)));
 const controlPayload=control?.payload as {action?:unknown;reason?:unknown;target?:unknown;until_ns?:unknown}|undefined;
 let nextStop=lookup(nextId);
 if(field(entity,'charging')===true){nextStop=lookup(chargingPadAssignments(store).get(entity.key.id));}
 else if(field(entity,'returning_to_charge')===true){
  const returnControl=[...store.messages].reverse().find(row=>row.kind==='event'&&row.schemaId==='lowalt.control'&&row.subjects?.some(key=>same(key,entity.key))&&(row.payload as {action?:unknown})?.action==='return_to_pad');
  const target=(returnControl?.payload as {target?:unknown}|undefined)?.target;
  // A return command records exact pad coordinates. Match that authored site,
  // never guess that the nearest pad is the destination.
  const pad=Array.isArray(target)&&target.length===3?[...store.entities.values()].find(row=>{const position=field(row,'site_position');return row.typeId==='lowalt:ChargingPad'&&Array.isArray(position)&&position.length===3&&position.every((value,index)=>typeof value==='number'&&typeof target[index]==='number'&&Math.abs(value-target[index])<1e-6);}):undefined;
  nextStop=pad?displayName(pad,store.header,locale):undefined;
 }
 const style=businessStyle(semanticVehicle(entity,task),locale),classStyle=businessStyle(semanticVehicle(entity),locale);
 const classId=text(field(entity,'vehicle_class'));
 const type=store.header.types.find(row=>row.typeId===entity.typeId);
 const story:EntityStory={label:displayName(entity,store.header,locale),business:style.label||t('Business not recorded'),vehicleClass:classStyle.label||(classId?displayLabel(undefined,classId,locale):displayLabel(type?.displayName,entity.typeId,locale)),battery:number(field(entity,'soc')),task,nextStop,status:t('No current activity recorded'),journey,latestEvent};
 const eventCause:CauseRef|undefined=latestEvent?{kind:'event',cut:latestEvent.cut,atNs:latestEvent.at_ns,text:latestEvent.text,entities:latestEvent.entities}:undefined;
 const fieldCause=(actor:EntityState,fieldName:string):CauseRef|undefined=>{
  const fact=actor.fields.get(`lowalt.${fieldName}`),rows=store.fieldVersionsOf(entityId(actor.key),`lowalt.${fieldName}`);
  const row=[...rows].reverse().find(row=>row.commit.commitIndex<=known&&!row.retract&&row.value===fact);
  return row?{kind:'change',entity:actor.key,fieldId:`lowalt.${fieldName}`,cut:row.commit.commitIndex}:undefined;
 };
 const cause=(chain:ChainInstance|undefined,fieldName?:string):CauseRef|undefined=>{
  if(chain?.transitionId)return {kind:'step',instanceId:chain.instanceId,revision:revision(chain.revision)};
  return (fieldName?fieldCause(entity,fieldName):undefined)??eventCause;
 };
 const set=(status:string,ref?:CauseRef)=>{story.status=status;story.cause=ref??eventCause;return story;};
 if(field(entity,'weather_held')===true){
  const wind=number(field(entity,'wind_mps'));
  const limit=(classId?parameter(store,'wind_above_limit',`${classId}_limit_mps`):undefined)??number(field(entity,'wind_limit_mps'));
  return set(wind!==undefined&&limit!==undefined&&wind>limit?t('Holding — wind {wind} m/s above limit {limit} m/s',{wind,limit}):t('Holding — weather rule active'),cause(ruleChain(store,entity,'wind_limit','held'),'weather_held'));
 }
 if(field(entity,'charging')===true)return set(t('Charging — replenishing battery'),cause(ruleChain(store,entity,'battery_reserve','returning'),'charging'));
 if(field(entity,'returning_to_charge')===true){
  const reserve=parameter(store,'battery_below_reserve','reserve_percent');
  // Explain the trigger using the last committed low-battery record, even if
  // battery has since changed. Do not invent a default reserve or round away <.
  const low=[...store.messages].reverse().find(row=>row.kind==='event'&&row.schemaId==='lowalt.energy.low'&&row.subjects?.some(key=>same(key,entity.key)));
  const payload=low?.payload as {soc?:unknown}|undefined;
  const soc=number(payload?.soc)??story.battery;
  const percent=soc===undefined?undefined:Number((soc*100).toFixed(2));
  const chain=ruleChain(store,entity,'battery_reserve','returning');
  return set(reserve!==undefined&&percent!==undefined&&percent<reserve?t('Returning to charge — battery {percent}% below reserve {reserve}%',{percent,reserve}):t('Returning to charge — battery reserve rule active'),cause(chain,'returning_to_charge'));
 }
 const untilRaw=controlPayload?.until_ns,until=untilRaw&&typeof untilRaw==='object'&&'$integer' in untilRaw?(untilRaw as {$integer:unknown}).$integer:untilRaw;
 if(controlPayload?.action==='hold'&&controlPayload.reason==='separation conflict'&&(typeof until==='string'&&/^\d+$/.test(until)||typeof until==='number'&&Number.isSafeInteger(until))&&BigInt(until)>ns){
  const cut=control&&[...store.commits].reverse().find(commit=>commit.commitIndex<=known&&commit.messages.includes(control))?.commitIndex;
  return set(t('Holding — maintaining safe separation'),cut===undefined?undefined:{kind:'message',messageId:control!.id,cut});
 }
 const reroute=ruleChain(store,entity,'no_fly_zone','rerouting');
 if(reroute){const zone=reroute.roles.zone;return set(zone?t('Re-routing around {zone}',{zone:lookup(zone.id)}):t('Re-routing — no-fly rule active'),cause(reroute));}
 if(task&&['assigned','started','picked_up'].includes(phase??'')){
  const replan=[...store.messages].reverse().find(row=>row.kind==='event'&&row.schemaId==='lowalt.task.replanned'&&row.subjects?.some(key=>same(key,task.key)));
  const payload=replan?.payload as {zone?:unknown;reason?:unknown}|undefined;
  const zone=typeof payload?.zone==='string'?[...store.entities.values()].find(row=>row.key.id===payload.zone&&field(row,'zone_active')===true):undefined;
  if(zone&&payload?.reason==='active no-fly zone'){
   const cut=[...store.commits].reverse().find(commit=>commit.commitIndex<=known&&replan&&commit.messages.includes(replan))?.commitIndex;
   return set(t('Re-routing around {zone}',{zone:displayName(zone,store.header,locale)}),cut===undefined?undefined:{kind:'message',messageId:replan!.id,cut});
  }
 }
 if(task){
  const ref=fieldCause(task,'task_state');
  if(field(task,'task_kind')==='food'){
   if(nextStop&&phase==='awaiting_preparation')return set(t('Waiting for kitchen capacity at {stop}',{stop:nextStop}),ref);
   if(nextStop&&phase==='preparing')return set(t('Preparing meal at {stop}',{stop:nextStop}),ref);
   if(phase==='queued')return set(t('Meal ready — waiting for a courier'),ref);
   if(nextStop&&phase==='picked_up')return set(t('Delivering meal to {stop}',{stop:nextStop}),ref);
   if(nextStop&&(phase==='assigned'||phase==='started'))return set(t('Heading to {stop} — collecting a prepared meal',{stop:nextStop}),ref);
  }
  if(phase==='paused')return set(t('Task paused — awaiting resumption'),ref);
  if(nextStop&&phase==='picked_up')return set(field(task,'task_kind')==='parcel'?t('Delivering parcel to {stop}',{stop:nextStop}):t('Performing {business} mission to {stop}',{business:style.label||t('Assigned'),stop:nextStop}),ref);
  if(nextStop&&(phase==='assigned'||phase==='started'))return set(t('Heading to {stop} — collecting assigned task',{stop:nextStop}),ref);
 }
 if(latestEvent)return set(eventNarrative(latestEvent,locale,names),eventCause);
 if(entity.typeId==='lowalt:UAV')return set(t('Ready — no active task recorded'));
 return story;
}
