import {describe,expect,it} from 'vitest';
import {Vector3} from 'three';
import {TemporalFeedStore} from '../feeds/temporal-store';
import {carryingPayload,chargingPadAssignments,PayloadLayer} from './operational-loads';
import {SemanticLayer} from './semantic-layer';
import {fixtureHeader,at,emptyCommit} from '../behaviours/extension-fixture';
const drone={id:'drone',generation:0},pad={id:'pad',generation:0};
function session(){
 const header={...fixtureHeader,types:[{typeId:'lowalt:UAV',displayName:'Drone',ancestors:[]},{typeId:'lowalt:ChargingPad',displayName:'Pad',ancestors:['lowalt:Site']}]};
 const store=new TemporalFeedStore(header);
 store.ingest({...emptyCommit(1),created:[{...drone,typeId:'lowalt:UAV'},{...pad,typeId:'lowalt:ChargingPad'}],facts:[{entity:drone,fieldId:'lowalt.payload_kg',value:1,producer:'tasks',validFrom:at('0')},{entity:drone,fieldId:'lowalt.charging',value:true,producer:'energy',validFrom:at('0')},{entity:pad,fieldId:'lowalt.site_position',value:[5,6,0],producer:'sites',validFrom:at('0')}],messages:[{id:'charging',kind:'event',schemaId:'lowalt.energy.charging_started',source:'energy',at:at('0'),payload:{vehicle:drone.id,pad:pad.id},subjects:[drone,pad]}]});store.seek('0');return store;
}
describe('visible committed operations',()=>{
 it('places a cargo marker beneath a loaded aircraft and removes it on drop-off or rewind',()=>{
  const store=session(),layer=new PayloadLayer(),positions=new Map([['drone:0',new Vector3(5,20,-6)]]);
  expect(carryingPayload(store.entities.get('drone:0')!)).toBe(true);layer.update(store,positions);expect(layer.markers.size).toBe(1);expect(layer.markers.get('drone:0')!.position.toArray()).toEqual([5,18.75,-6]);
  store.ingest({...emptyCommit(2,'1000000000'),facts:[{entity:drone,fieldId:'lowalt.payload_kg',value:0,producer:'tasks',validFrom:at('1000000000')}]});store.seek('1000000000');layer.update(store,positions);expect(layer.markers.size).toBe(0);
  store.seek('0',1);layer.update(store,positions);expect(layer.markers.size).toBe(1);layer.update(store,new Map());expect(layer.markers.size).toBe(0);layer.dispose();
 });
 it('glows only the pad named by charging_started while its aircraft is actually charging',()=>{
  const store=session(),layer=new SemanticLayer(store.header);
  expect(chargingPadAssignments(store).get('drone')).toBe('pad');layer.update(store,'0',new Vector3(1,0,1));expect(layer.chargingPadCount).toBe(1);
  const marker=layer.pickables[0];expect(marker.userData.charging).toBe(true);expect(marker.position.toArray()).toEqual([4,.6,-7]);
  store.ingest({...emptyCommit(2,'1000000000'),facts:[{entity:drone,fieldId:'lowalt.charging',value:false,producer:'energy',validFrom:at('1000000000')}]});store.seek('1000000000');layer.update(store,'1000000000',new Vector3());expect(chargingPadAssignments(store).size).toBe(0);expect(layer.chargingPadCount).toBe(0);
  store.seek('0',1);layer.update(store,'0',new Vector3());expect(layer.chargingPadCount).toBe(1);layer.dispose();
 });
 it('does not infer a charging pad from proximity or task assignment',()=>{
  const store=session();store.messages.length=0;expect(chargingPadAssignments(store).size).toBe(0);
  const entity=store.entities.get('drone:0')!;entity.fields.delete('lowalt.payload_kg');expect(carryingPayload(entity)).toBe(false);
 });
});
