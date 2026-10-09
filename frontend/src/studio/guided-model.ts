import type { Workspace } from './api';
export const studioSteps = [
 {id:'scene',label:'Scene',description:'Choose a region and place actors in the scene.'},
 {id:'entities',label:'Entities',description:'Browse typed entities, their initial fields and relationships.'},
 {id:'plugins',label:'Domain plugins',description:'Choose domain engines and assign one writer per field.'},
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
 return value.replace(/^.*:/,'').replace(/^traffic[._-]/,'').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/[._-]+/g,' ').replace(/\b\w/g,letter=>letter.toUpperCase());
}
