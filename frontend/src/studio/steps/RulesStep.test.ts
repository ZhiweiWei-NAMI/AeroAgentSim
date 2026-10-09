import {test,expect} from 'vitest';
import {appendPredicateRule} from './RulesStep';
test('typed predicate rule appends one chain and binding while preserving unknown draft content',()=>{
 const scenario={extra:{retained:true},behaviours:[{id:'package',extension:'retain',predicates:{ready:{roles:{actor:'oo:UAV'},profile:'committed_reactive/v1',expression:{literal:true}}},chains:{},bindings:[]}]};
 const next=appendPredicateRule(scenario,0,'respond','ready',{actor:{entity:'alpha'}},{kind:'set',entity:{$role:'actor'},field:'status',value:'ready'});
 expect(next.extra).toEqual(scenario.extra);
 expect(next.behaviours[0].extension).toBe('retain');
 expect(next.behaviours[0].chains.respond.trigger).toEqual({predicate:'ready',edge:'entered'});
 expect(next.behaviours[0].bindings[0].match).toEqual({actor:{entity:'alpha'}});
 expect(next.behaviours[0].chains.respond.transitions[0].actions[0].value).toBe('ready');
});
