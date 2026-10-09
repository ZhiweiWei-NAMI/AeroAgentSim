import {expect,it} from 'vitest';
import {ingestRecord,type AgentDecision} from './AgentConsole';
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
