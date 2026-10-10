import * as T from 'three';
import type {EntityState,FeedStore} from './feed-store';
import {entityId} from './bindings';

/** The committed onboard mass, not merely an assigned task, proves a load. */
export function carryingPayload(entity:EntityState):boolean {
 const mass=entity.fields.get('lowalt.payload_kg')?.value;
 return entity.typeId==='lowalt:UAV'&&typeof mass==='number'&&Number.isFinite(mass)&&mass>0;
}
/** The charging event names the pad; current charging state prevents stale glows. */
export function chargingPadAssignments(store:FeedStore):Map<string,string> {
 const assignments=new Map<string,string>();
 for(const message of store.messages){
  if(message.kind!=='event'||!['lowalt.energy.charging_started','lowalt.energy.charging_finished'].includes(message.schemaId))continue;
  const payload=message.payload as {vehicle?:unknown;pad?:unknown}|null;
  if(typeof payload?.vehicle!=='string')continue;
  if(message.schemaId.endsWith('finished'))assignments.delete(payload.vehicle);
  else if(typeof payload.pad==='string')assignments.set(payload.vehicle,payload.pad);
 }
 for(const [vehicle,pad] of assignments){
  const actor=[...store.entities.values()].find(entity=>entity.key.id===vehicle);
  const site=[...store.entities.values()].find(entity=>entity.key.id===pad&&entity.typeId==='lowalt:ChargingPad');
  if(actor?.fields.get('lowalt.charging')?.value!==true||!site)assignments.delete(vehicle);
 }
 return assignments;
}

/** Small suspended cargo glyphs in the same rebased display frame as aircraft. */
export class PayloadLayer {
 readonly root=new T.Group();
 readonly markers=new Map<string,T.Group>();
 private box=new T.BoxGeometry(.95,.65,.85);
 private band=new T.BoxGeometry(.13,.68,.88);
 private cord=new T.CylinderGeometry(.025,.025,.5,6);
 private cargo=new T.MeshStandardMaterial({color:0xe69f00,roughness:.65,emissive:0x794000,emissiveIntensity:.3});
 private tape=new T.MeshStandardMaterial({color:0xffebc1,roughness:.7});
 update(store:FeedStore,positions:ReadonlyMap<string,T.Vector3>){
  const shown=new Set<string>();
  for(const entity of store.entities.values()){
   const id=entityId(entity.key),position=positions.get(id);
   if(!position||!carryingPayload(entity))continue;
   let marker=this.markers.get(id);
   if(!marker){marker=new T.Group();marker.userData.entity=entity.key;marker.add(new T.Mesh(this.box,this.cargo),new T.Mesh(this.band,this.tape));const cord=new T.Mesh(this.cord,this.tape);cord.position.y=.56;marker.add(cord);this.root.add(marker);this.markers.set(id,marker);}
   marker.position.copy(position);marker.position.y-=1.25;marker.visible=true;shown.add(id);
  }
  for(const [id,marker] of this.markers)if(!shown.has(id)){marker.removeFromParent();this.markers.delete(id);}
 }
 dispose(){this.root.clear();this.markers.clear();this.box.dispose();this.band.dispose();this.cord.dispose();this.cargo.dispose();this.tape.dispose();}
}
