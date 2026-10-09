import {test,expect} from 'vitest';
import {appendPredicateRule,entityLabel,commandRulePlan} from './RulesStep';
const scenario={extra:{retained:true},registry:{messages:[{id:'ops.telemetry',kind:'event',schema:'telemetry'}],schemas:{telemetry:{type:'record',members:{level:{type:'string'}}}}},behaviours:[{id:'package',extension:'retain',predicates:{ready:{roles:{actor:'oo:UAV'},profile:'committed_reactive/v1',expression:{literal:true}}},chains:{},bindings:[]}]};

test('typed predicate rule appends one chain and binding while preserving unknown draft content',()=>{
 const next=appendPredicateRule(scenario,0,'respond','ready',{actor:{entity:'alpha'}},{kind:'set',entity:{$role:'actor'},field:'status',value:'ready'});
 expect(next.extra).toEqual(scenario.extra);
 expect(next.behaviours[0].extension).toBe('retain');
 expect(next.behaviours[0].chains.respond.trigger).toEqual({predicate:'ready',edge:'entered'});
 expect(next.behaviours[0].bindings[0].match).toEqual({actor:{entity:'alpha'}});
 expect(next.behaviours[0].chains.respond.transitions[0].actions[0].value).toBe('ready');
});

test('emit plan authors a real compiler emit action with topic and payload',()=>{
 const next=appendPredicateRule(scenario,0,'shout','ready',{actor:{is_a:'oo:UAV'}},{},{actions:[{id:'action',kind:'emit',schema:'ops.telemetry',topic:'ops.events',payload:{level:'high'}},{id:'finish',kind:'complete',status:'completed'}]});
 const action=next.behaviours[0].chains.shout.transitions[0].actions[0];
 expect(action).toEqual({id:'action',kind:'emit',schema:'ops.telemetry',topic:'ops.events',payload:{level:'high'}});
});

test('delay plan schedules a timer and completes on a separate timer transition, not in the same one',()=>{
 const next=appendPredicateRule(scenario,0,'linger','ready',{actor:{entity:'alpha'}},{},{actions:[{id:'wait',kind:'delay',duration_ns:5000}],transitions:[
  {id:'wait-timer',from:'waiting',on:{instance:'activated'},to:'waiting',actions:[{id:'wait',kind:'delay',duration_ns:5000}]},
  {id:'finish',from:'waiting',on:{timer:'wait'},to:'done',actions:[{id:'finish',kind:'complete',status:'completed'}]}]});
 const transitions=next.behaviours[0].chains.linger.transitions;
 expect(transitions).toHaveLength(2);
 expect(transitions[1].on).toEqual({timer:'wait'});
 expect(transitions[1].actions.every((action:any)=>action.kind!=='delay')).toBe(true);
 expect(transitions[0].actions[0].kind).toBe('delay');
});

test('command and assert_relation plans use compiler-required shapes with role refs',()=>{
 const command=appendPredicateRule(scenario,0,'ask','ready',{actor:{entity:'alpha'}},{},{actions:[{id:'action',kind:'command',capability:'relocate',payload:{goal:'grid'}},{id:'finish',kind:'complete',status:'completed'}]});
 expect(command.behaviours[0].chains.ask.transitions[0].actions[0]).toMatchObject({kind:'command',capability:'relocate'});
 const relation=appendPredicateRule(scenario,0,'link','ready',{actor:{entity:'alpha'}},{},{actions:[{id:'action',kind:'assert_relation',edge_id:'edge-1',relation:'oo:assigned_to',source:{$role:'actor'},target:{$role:'actor'}},{id:'finish',kind:'complete',status:'completed'}]});
 expect(relation.behaviours[0].chains.link.transitions[0].actions[0]).toEqual({id:'action',kind:'assert_relation',edge_id:'edge-1',relation:'oo:assigned_to',source:{$role:'actor'},target:{$role:'actor'}});
});

test('entity labels come from actual facts and fall back to the humanized id',()=>{
 expect(entityLabel({id:'uav.bravo',type:'oo:UAV',facts:{name:'UAV Bravo'}})).toBe('UAV Bravo');
 expect(entityLabel({id:'uav.bravo',type:'oo:UAV',facts:{}})).toBe('UAV Bravo');
});

test('issued commands keep the chain alive until the actual terminal receipt',()=>{
 const plan=commandRulePlan({id:'action',kind:'command',schema:'move',target:'motion',payload:{}});
 expect(plan.transitions![0].actions).toHaveLength(1);
 expect(plan.transitions![0].to).toBe('waiting');
 expect(plan.transitions![1].on).toEqual({receipt:'action',status:'succeeded'});
 expect(plan.transitions![2].on).toEqual({receipt:'action',status:['failed','rejected','canceled']});
});
