import {it,expect} from 'vitest';
import {backgroundContext} from './BehaviourRunGraph';
import {TemporalFeedStore} from '../feeds/temporal-store';
it('filters real background task/route references while keeping shared foreground context',()=>{
 const store=new TemporalFeedStore({contract:'aeroagentsim.viewer-feed/v1',runId:'run',registryDigest:'actual',start:{ns:'0',microstep:0},types:[],fields:[],presentation:[]});
 const entity=(id:string,typeId:string,values:Record<string,unknown>)=>store.entities.set(`${id}:0`,{key:{id,generation:0},typeId,fields:new Map(Object.entries(values).map(([fieldId,value])=>[fieldId,{entity:{id,generation:0},fieldId,value,producer:'actual',validFrom:{ns:'0',microstep:0}}]))});
 entity('bg','aas:TrafficUAV',{'traffic.actor.role':'background',task:{$ref:{id:'bg-task',generation:0}},route:{$ref:{id:'shared-route',generation:0}}});
 entity('core','aas:TrafficUAV',{'traffic.actor.role':'candidate',route:{$ref:{id:'shared-route',generation:0}}});
 entity('bg-task','aas:TrafficTask',{route:{$ref:{id:'bg-route',generation:0}}});entity('bg-route','aas:TrafficRoute',{});entity('shared-route','aas:TrafficRoute',{});entity('edge','aas:TrafficCoordinator',{});
 expect([...backgroundContext(store)].sort()).toEqual(['bg-route:0','bg-task:0','bg:0']);
});
