import {expect,it} from 'vitest';
import {ingestRecord,ingestRecords,type AgentDecision} from './AgentConsole';
import type {AgentRecordInput} from '../agents/records';

it('reads actual LangGraph observation, model call and event output records without fabricating command receipts',()=>{
 const decisions=new Map<string,AgentDecision>(),entities=new Set(['uav.bravo']);
 const record=(phase:string,data:unknown)=>ingestRecord({decision_id:'decision-1',phase,data_json:JSON.stringify(data)},'3000000000',decisions,entities,'aas.langgraph.record');
 record('observation',{initial:{observation:{fields:[{entity:{id:'uav.bravo'},field:'traffic.actor.role',status:'known',value:'candidate'}]}}});
 record('model_call',{node:'uav_bravo',response:{message:{content:'{"accept":true}'}}});
 record('output',{kind:'event',schema:'traffic.proposal.bid',topic:'traffic.proposal.bid',payload:{accept:true}});
 record('finished',{result:{outputs:[]}});
 const decision=decisions.get('decision-1')!;
 expect(decision.fields[0]).toMatchObject({entity:'uav.bravo',value:'"candidate"'});
 expect(decision.outputs[0].schema).toBe('traffic.proposal.bid');
 expect(decision.phases.map(row=>row.phase)).toEqual(['observation','model_call','output','finished']);
 expect(decision.calls).toHaveLength(0);expect(decision.receipts).toHaveLength(0);
 expect(()=>record('invented',{})).toThrow('unknown phase');
});

it('folds projector aliases into originals instead of appending duplicate phases or model calls',()=>{
 const entities=new Set(['uav.bravo']),decisions=new Map<string,AgentDecision>();
 const fields=[{entity:{id:'uav.bravo'},field:'traffic.actor.role',status:'known',value:'candidate'}];
 const call={identity:[0,'uav_bravo','accept',0],request:{messages:[]},response:{message:{content:'{"accept":true}'}}};
 const record=(id:string,schemaId:string,phase:string,data:unknown,sequence:number):AgentRecordInput=>({
  id,schemaId,atNs:'3000000000',cut:1,sequence,
  payload:{decision_id:'decision-alias',phase,data_json:JSON.stringify(data)},
 });
 const derived=(id:string,phase:string,data:object,source:string,sequence:number)=>record(id,'aas.agent.record',phase,{...(data as Record<string,unknown>),source_schema:'aas.langgraph.record',source_message_id:source},sequence);
 const problems=ingestRecords([
  record('observation.raw','aas.langgraph.record','observation',{initial:{observation:{fields}}},0),
  derived('observation.raw/console/0','observation',{fields,model:'fixture-model',pin:{factory:'fixture'}},'observation.raw',1),
  record('call.raw','aas.langgraph.record','model_call',call,2),
  derived('call.raw/console/0','prompt',{model_call:call.identity,request:call.request},'call.raw',3),
  derived('call.raw/console/1','response',call,'call.raw',4),
  record('finished.raw','aas.langgraph.record','finished',{result:{outputs:[]}},5),
  derived('finished.raw/console/0','finished',{result:{outputs:[]}},'finished.raw',6),
 ],decisions,entities);

 expect(problems).toEqual([]);
 const decision=decisions.get('decision-alias')!;
 expect(decision.phases.map(row=>row.phase)).toEqual(['observation','model_call','finished']);
 expect(decision.counts).toMatchObject({records:3,modelCalls:1,derived:4});
 expect(decision.fields).toHaveLength(1);
 expect(decision.modelCalls[0].recordId).toBe('call.raw');
 expect(decision.modelCalls[0].aliasIds).toEqual(['call.raw/console/0','call.raw/console/1']);
 expect(decision.modelCalls[0].rawIds).toEqual(['call.raw','call.raw/console/0','call.raw/console/1']);
 expect(decision.phases[0].views[0].recordId).toBe('observation.raw/console/0');
});
