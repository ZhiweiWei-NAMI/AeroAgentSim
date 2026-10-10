import type { Workspace } from './api';
import type { AlgorithmSlot } from '../newcomers/api';

/** Shared algorithm plug-in contract (`aeroagentsim.algorithms`): one slot each, additive. */
export const algorithmSlots:AlgorithmSlot[]=['allocation','path_planning','conflict_resolution','charging'];
/** Built-in algorithm ids the shared contract expects per slot; actual availability comes only from the /v1/algorithms catalog. */
export const builtinAlgorithms:Record<AlgorithmSlot,string[]>={
 allocation:['nearest','auction','hungarian','llm'],
 path_planning:['straight','corridor_astar'],
 conflict_resolution:['altitude_layers','hold_and_go'],
 charging:['threshold_return'],
};
export const studioSteps = [
 {id:'scene',label:'Scene',description:'Choose a region and place actors in the scene.'},
 {id:'entities',label:'Entities',description:'Browse typed entities, their initial fields and relationships.'},
 {id:'plugins',label:'Domain plugins',description:'Choose the engines that control movement, traffic, communication and weather.'},
 {id:'predicates',label:'Predicates',description:'Define the conditions that drive your scenario.'},
 {id:'rules',label:'Event chains & rules',description:'Connect predicate transitions to actions and external events.'},
 {id:'agents',label:'Agents',description:'Configure recorded decisions or a live LangGraph model.'},
 {id:'validate',label:'Validate & run',description:'Check the complete scenario, then start a new run.'},
] as const;
export type StudioStep = typeof studioSteps[number]['id'];
export interface StudioIssue {path:string;message:string;source?:string}
export function issueStep(issue:StudioIssue):StudioStep {
 const text=`${issue.source ?? ''} ${issue.path}`;
 if(/predicate/i.test(text)) return 'predicates';
 if(/chains|bindings\[|conflicts|injection|behaviour/i.test(text)) return 'rules';
 if(/decisions|langgraph|agents|provider/i.test(text)) return 'agents';
 if(/engines|owners|writer|plugin|bindings\./i.test(text)) return 'plugins';
 if(/entities|relations|registry/i.test(text)) return 'entities';
 if(/scene|region|presentation|position/i.test(text)) return 'scene';
 return 'validate';
}
export function validationIssues(validation:Workspace['validation']):StudioIssue[] {
 if(!validation || validation.valid) return [];
 if(validation.issues?.length) return validation.issues;
 return validation.errors.map(message=>({path:'$',message}));
}
export function readableName(value:string):string {
 return value.replace(/^.*:/,'').replace(/^traffic[._-]/,'').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/[._-]+/g,' ').replace(/\b\w/g,letter=>letter.toUpperCase()).replace(/\bUav\b/g,'UAV').replace(/\bLlm\b/g,'LLM').replace(/\bPx4\b/g,'PX4');
}
