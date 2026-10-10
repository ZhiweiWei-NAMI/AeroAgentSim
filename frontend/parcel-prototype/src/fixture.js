// Independently authored fixture. This is sampled replay data, not a simulator,
// a BENCH provider, measured telemetry, or an Atlas implementation.
export const FIXTURE = Object.freeze({
  runId: 'fixture.smartpark.parcel.001', epoch: 'fixture.epoch.1', revision: 'parcel-script.v1',
  stepSeconds: 0.1, startSeconds: 0.1, durationSeconds: 84,
  provenance: 'hypothetical', authority: 'authored-fixture',
});
export const STATIONS = Object.freeze([
  {id:'warehouse', kind:'warehouse', labelKey:'warehouse', position:[13,17,0]},
  {id:'pad', kind:'pad', labelKey:'pad', position:[56,26,0]},
  {id:'tray', kind:'tray', labelKey:'staging', position:[55,26,1]},
  {id:'locker', kind:'locker', labelKey:'locker', position:[111,42,0]},
  {id:'relay', kind:'relay', labelKey:'relay', position:[82,57,0]},
]);
export const ROUTES = Object.freeze({
  ground:[[18,18,0],[27,18,0],[27,24,0],[52,24,0]],
  air:[[59,27,14],[74,38,14],[109,42,14]],
  alternate:[[74,38,14],[75,57,14],[97,57,14],[109,42,14]],
});
const event=(id,time,labelKey,kind,entities,extra={})=>({id,time,labelKey,kind,entities,provenance:'hypothetical',...extra});
export const SCRIPT = Object.freeze([
  event('script.pickup',3,'script_pickup','script',['parcel.p1042','ugv.01','warehouse']),
  event('receipt.ugv',6,'receipt_ugv','custody',['parcel.p1042','ugv.01'],{from:'warehouse',to:'ugv.01'}),
  event('arrival.pad',19,'arrival_pad','motion',['ugv.01','pad']),
  event('script.unload-pad',20,'unload_pad','script',['parcel.p1042','ugv.01','tray']),
  event('receipt.pad',24,'receipt_pad','custody',['parcel.p1042','tray'],{from:'ugv.01',to:'tray'}),
  event('script.load-uav',26,'load_uav','script',['parcel.p1042','uav.01','tray']),
  event('receipt.uav',29,'receipt_uav','custody',['parcel.p1042','uav.01'],{from:'tray',to:'uav.01'}),
  event('script.departure',29.1,'departure','motion',['uav.01','parcel.p1042']),
  event('script.comm-degraded',38,'degradation','schedule',['uav.01','link.delivery'],{phaseGate:'delivery',endTime:54}),
  event('predicate.comm-degraded',38,'detection','predicate',['uav.01','link.delivery'],{dependsOn:['script.comm-degraded'],predicateValue:true}),
  event('receipt.hold',39,'wait_receipt','response',['uav.01'],{dependsOn:['predicate.comm-degraded'],action:'hold',status:'executed'}),
  event('receipt.retry',43,'retry_receipt','response',['uav.01','link.delivery'],{dependsOn:['receipt.hold'],action:'retry',status:'failed'}),
  event('receipt.replan',48,'replan_receipt','response',['uav.01'],{dependsOn:['receipt.retry'],action:'replan',status:'accepted'}),
  event('receipt.alternate-route',49,'route_receipt','response',['uav.01'],{dependsOn:['receipt.replan'],action:'replan',status:'executed'}),
  event('script.comm-recovery',54,'recovery','schedule',['uav.01','link.delivery'],{dependsOn:['script.comm-degraded']}),
  event('arrival.locker',68,'descend','motion',['uav.01','locker']),
  event('script.unload-locker',73,'unload_locker','script',['parcel.p1042','uav.01','locker']),
  event('receipt.delivered',78,'delivered_receipt','custody',['parcel.p1042','locker'],{from:'uav.01',to:'locker'}),
]);
const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
const lerp=(a,b,f)=>a.map((v,i)=>v+(b[i]-v)*f);
export function pointOnPath(points,progress) {
  const distances=points.slice(1).map((p,i)=>Math.hypot(...p.map((n,j)=>n-points[i][j])));
  const total=distances.reduce((a,b)=>a+b,0);
  if(!total) return [...points[0]];
  let distance=clamp(progress,0,1)*total;
  for(let i=0;i<distances.length;i++) {
    if(distance<=distances[i] || i===distances.length-1) return lerp(points[i],points[i+1],distances[i] ? distance/distances[i] : 0);
    distance-=distances[i];
  }
}
function ugvPosition(t) { return pointOnPath(ROUTES.ground,(t-6)/13); }
function uavPosition(t) {
  if(t<29) return [59,27,2];
  if(t<34) return [59,27,2+(t-29)/5*12];
  if(t<39) return lerp([59,27,14],[74,38,14],(t-34)/5);
  if(t<49) return [74,38,14];
  if(t<68) return pointOnPath(ROUTES.alternate,(t-49)/19);
  if(t<73) return [109,42,14-(t-68)/5*12];
  return [109,42,2];
}
const plus=(p,o)=>p.map((v,i)=>v+o[i]);
function source(tick,pointer) {return {authority:FIXTURE.authority,provenance:'hypothetical',frameId:`fixture.frame.${tick}`,pointer};}
function primaryParcel(t,ugv,uav,tick) {
  let state='queued',custodian='warehouse',position=[14,17,1.2],attachment=null,transfer=null;
  const ugvCargo=plus(ugv,[0,0,1.4]),uavCargo=plus(uav,[0,0,-0.8]);
  if(t>=3 && t<6){state='loading_ugv';position=lerp([14,17,1.2],ugvCargo,(t-3)/3);transfer={from:'warehouse',to:'ugv.01',progress:(t-3)/3};}
  else if(t>=6 && t<20){state='on_ugv';custodian='ugv.01';attachment={carrierId:'ugv.01',offsetEnu:[0,0,1.4]};position=ugvCargo;}
  else if(t>=20 && t<24){state='unloading_ugv';custodian='ugv.01';position=lerp(ugvCargo,[55,26,1.3],(t-20)/4);transfer={from:'ugv.01',to:'tray',progress:(t-20)/4};}
  else if(t>=24 && t<26){state='at_pad';custodian='tray';position=[55,26,1.3];}
  else if(t>=26 && t<29){state='loading_uav';custodian='tray';position=lerp([55,26,1.3],uavCargo,(t-26)/3);transfer={from:'tray',to:'uav.01',progress:(t-26)/3};}
  else if(t>=29 && t<73){state=t>=39&&t<49?'holding':t>=49?'replanned':'on_uav';custodian='uav.01';attachment={carrierId:'uav.01',offsetEnu:[0,0,-0.8]};position=uavCargo;}
  else if(t>=73 && t<78){state='unloading_locker';custodian='uav.01';position=lerp(uavCargo,[111,42,1],(t-73)/5);transfer={from:'uav.01',to:'locker',progress:(t-73)/5};}
  else if(t>=78){state='delivered';custodian='locker';position=[111,42,1];}
  return {id:'parcel.p1042',label:'P-1042',kind:'parcel',state,custodian,position,attachment,transfer,
    dimensionsM:[0.45,0.32,0.26],massKg:1.8,target:'locker',source:source(tick,'fixture.parcels.parcel.p1042')};
}
export function sampleFixtureFrame(requestedSeconds) {
  if(!Number.isFinite(requestedSeconds)) throw new TypeError('Replay time must be finite');
  const tick=Math.round(clamp(requestedSeconds,FIXTURE.startSeconds,FIXTURE.durationSeconds)/FIXTURE.stepSeconds);
  const t=tick/10, ugv=ugvPosition(t), uav=uavPosition(t);
  // Right-hand derivative matches the active authored segment at a boundary.
  const velocity=(fn)=>t>=84?[0,0,0]:fn(Math.min(84,t+.01)).map((v,i)=>(v-fn(t)[i])/(Math.min(84,t+.01)-t));
  const deliveryPhase=t>=29 && t<78;
  const degraded=deliveryPhase && t>=38 && t<54;
  const rssi=degraded?-98:-62;
  const entities=[
    {...STATIONS[0],velocity:[0,0,0],source:source(tick,'fixture.stations.warehouse')},
    ...STATIONS.slice(1).map(s=>({...s,velocity:[0,0,0],source:source(tick,`fixture.stations.${s.id}`)})),
    {id:'ugv.01',label:'UGV-01',kind:'ugv',labelKey:'ugv',position:ugv,velocity:velocity(ugvPosition),state:t>=6&&t<19?'travelling':'idle',source:source(tick,'fixture.entities.ugv.01')},
    {id:'uav.01',label:'UAV-01',kind:'uav',labelKey:'uav',position:uav,velocity:velocity(uavPosition),state:t>=39&&t<49?'holding':t>=68&&t<73?'descending':t>=29&&t<73?'flying':'idle',source:source(tick,'fixture.entities.uav.01')},
  ];
  const parcels=[primaryParcel(t,ugv,uav,tick),
    {id:'parcel.p1043',label:'P-1043',kind:'parcel',state:'queued',custodian:'warehouse',position:[11.5,18,1.2],attachment:null,transfer:null,dimensionsM:[.4,.3,.25],massKg:1.2,target:'locker',source:source(tick,'fixture.parcels.parcel.p1043')},
    {id:'parcel.p1044',label:'P-1044',kind:'parcel',state:'delivered',custodian:'locker',position:[112,43,1],attachment:null,transfer:null,dimensionsM:[.3,.25,.2],massKg:.9,target:'locker',source:source(tick,'fixture.parcels.parcel.p1044')},
  ];
  return {
    schemaVersion:'p02.parcel-view-frame/v1', runId:FIXTURE.runId,epoch:FIXTURE.epoch,manifestRevision:FIXTURE.revision,
    frameKey:`${FIXTURE.runId}:${FIXTURE.epoch}:${FIXTURE.revision}:${tick}`,tick,simTimeNs:String(tick*100000000),timeSeconds:t,
    provenance:'hypothetical',authority:FIXTURE.authority,readiness:{motion:'fixture',network:'fixture',business:'fixture'},
    entities:entities.map(e=>({...e,generation:'fixture.g1'})),parcels:parcels.map(p=>({...p,generation:'fixture.g1'})),routes:ROUTES,activeRoute:t>=49?'alternate':'air',
    events:SCRIPT.filter(e=>e.time<=t).map(e=>({...e,frameKey:`fixture.frame.${Math.round(e.time*10)}`})),
    script:SCRIPT,network:{id:'link.delivery',degraded,rssiDbm:rssi,source:source(tick,'fixture.network.link.delivery')},
    rule:{id:'demo.delivery-link-degraded',value:deliveryPhase && rssi < -90,inputs:{deliveryPhase,rssiDbm:rssi,thresholdDbm:-90},provenance:'hypothetical',engine:'fixture-only',atlasValue:'unknown'},
  };
}
export function assertFrame(frame) {
  if(!frame || frame.schemaVersion!=='p02.parcel-view-frame/v1') throw new TypeError('Unsupported view-frame schema');
  if(typeof frame.frameKey!=='string'||!frame.frameKey||!Number.isInteger(frame.tick)||frame.tick<1||!Number.isFinite(frame.timeSeconds))throw new TypeError('Invalid frame identity/time');
  if(!Array.isArray(frame.entities)||!Array.isArray(frame.parcels))throw new TypeError('Entities and parcels must be explicit arrays');
  const ids=new Set();
  for(const item of [...frame.entities,...frame.parcels]) {
    if(typeof item.id!=='string'||!item.id.trim()||ids.has(item.id))throw new TypeError('Entity IDs must be nonempty and unique');
    ids.add(item.id);
    if(!Array.isArray(item.position)||item.position.length!==3||item.position.some(v=>!Number.isFinite(v)||Math.abs(v)>1e6))throw new TypeError('Invalid bounded ENU position');
  }
  for(const parcel of frame.parcels){
    if(!frame.entities.some(e=>e.id===parcel.custodian))throw new TypeError('Custodian must be an explicit non-parcel entity');
    if(parcel.attachment){
      const carrier=frame.entities.find(e=>e.id===parcel.attachment.carrierId);
      if(!carrier || !Array.isArray(parcel.attachment.offsetEnu) || parcel.attachment.offsetEnu.length!==3 || parcel.attachment.offsetEnu.some(v=>!Number.isFinite(v))) throw new TypeError('Invalid attachment');
      if(parcel.custodian!==carrier.id)throw new TypeError('Attachment/custody mismatch');
      if(parcel.position.some((v,i)=>Math.abs(v-carrier.position[i]-parcel.attachment.offsetEnu[i])>1e-6))throw new TypeError('Attached parcel detached from carrier');
    }
  }
  return frame;
}
export function selectionFor(frame,entityId) {
  const entity=[...frame.parcels,...frame.entities].find(e=>e.id===entityId);
  if(!entity)return null;
  return {runId:frame.runId,epoch:frame.epoch,manifestRevision:frame.manifestRevision,frameKey:frame.frameKey,generation:entity.generation??null,entityId,entity};
}
export function normalizeBenchMotion(scene) {
  // Field paths only; intentionally no private BENCH implementation or schema copy.
  if(!scene||scene.schema_version!=='aero-bench.scene-state/v1'||!Number.isInteger(scene.at?.tick)||scene.at.tick<1||!Array.isArray(scene.samples)||!Array.isArray(scene.declared_entity_ids))throw new TypeError('Invalid BENCH motion envelope');
  const declared=new Set(scene.declared_entity_ids),seen=new Set();
  if(declared.size!==scene.declared_entity_ids.length || declared.size!==scene.samples.length)throw new TypeError('Declared/sample identity mismatch');
  const entities=scene.samples.map(sample=>{
    if(!declared.has(sample.entity_id)||seen.has(sample.entity_id))throw new TypeError('Invalid exact entity join');
    seen.add(sample.entity_id);
    const p=sample.pose?.position?.enu,v=sample.linear_velocity_enu;
    const position=[p?.east_m,p?.north_m,p?.up_m],velocity=[v?.east_mps,v?.north_mps,v?.up_mps];
    if([...position,...velocity].some(n=>!Number.isFinite(n)))throw new TypeError('Missing finite pose/velocity');
    return {id:sample.entity_id,position,velocity,bodyDimensionsM:null,source:{pointer:`SceneState.samples[entity_id=${sample.entity_id}]`,authority:sample.provider_id,provenance:'reported'}};
  });
  return {entities,parcelSupport:'not-supplied',parcels:null,networkSupport:'not-supplied',motionReadiness:'unverified',tick:scene.at.tick,simTimeNs:String(scene.at.sim_time_ns)};
}
