import type {AlgorithmDescriptor,AlgorithmSlot,RunComparison,RunKpis,ScenarioTemplate} from './api';
import {validateKpiValues} from './validation';
const object=(value:unknown):Record<string,unknown>=>{if(!value||typeof value!=='object'||Array.isArray(value))throw Error('The service returned an invalid object.');return value as Record<string,unknown>;};
export function decodeTemplates(value:unknown):ScenarioTemplate[]{
 if(!Array.isArray(value))throw Error('The service returned an invalid template list.');
 for(const candidate of value){const t=object(candidate);if(t.format!=='aeroagentsim.template/v1'||!['id','title','summary','scenario'].every(key=>typeof t[key]==='string')||!['logistics','emergency','inspection','agriculture','mobility','security','airspace','weather','energy','mixed'].includes(String(t.category))||!['beginner','intermediate','advanced'].includes(String(t.difficulty))||typeof t.duration_s!=='number'||!Number.isFinite(t.duration_s)||t.duration_s<0)throw Error('A template is missing its title, category or duration.');
  for(const key of ['observe','try','kpis'])if(!Array.isArray(t[key])||!(t[key] as unknown[]).every(item=>typeof item==='string'))throw Error(`Template ${t.id}: ${key} must be a list.`);
  if(!Array.isArray(t.knobs))throw Error(`Template ${t.id}: settings are missing.`);
  for(const candidate of t.knobs){const knob=object(candidate);if(typeof knob.id!=='string'||typeof knob.label!=='string'||!['integer','number','boolean','choice'].includes(String(knob.type)))throw Error(`Template ${t.id}: invalid setting.`);
   if(knob.type==='boolean'&&typeof knob.default!=='boolean'||['integer','number'].includes(String(knob.type))&&(typeof knob.default!=='number'||!Number.isFinite(knob.default)||knob.type==='integer'&&!Number.isSafeInteger(knob.default)))throw Error(`Template ${t.id}: invalid default for ${knob.label}.`);
   for(const key of ['min','max'])if(knob[key]!==undefined&&(typeof knob[key]!=='number'||!Number.isFinite(knob[key])))throw Error(`Template ${t.id}: invalid bound for ${knob.label}.`);
   if(typeof knob.default==='number'&&(typeof knob.min==='number'&&knob.default<knob.min||typeof knob.max==='number'&&knob.default>knob.max))throw Error(`Template ${t.id}: default outside the bounds for ${knob.label}.`);
   if(knob.type==='choice'&&(!Array.isArray(knob.options)||!knob.options.length||!knob.options.every(value=>['string','number','boolean'].includes(typeof value))||!knob.options.includes(knob.default)))throw Error(`Template ${t.id}: invalid choices for ${knob.label}.`);
  }
 }
 return value as ScenarioTemplate[];
}
export function decodeKpis(value:unknown):RunKpis{const data=object(value);if(typeof data.run_id!=='string'||!Array.isArray(data.series))throw Error('Results are missing their run or timeline.');const final=validateKpiValues(data.final);const series=data.series.map(candidate=>{const sample=object(candidate);if(typeof sample.at_ns!=='string'||!/^\d+$/.test(sample.at_ns))throw Error('Results contain an invalid time.');if(sample.commitIndex!==undefined&&(!Number.isSafeInteger(sample.commitIndex)||(sample.commitIndex as number)<1))throw Error('Results contain an invalid cut.');return{...(sample.commitIndex!==undefined?{commitIndex:sample.commitIndex as number}:{}),at_ns:sample.at_ns,kpis:validateKpiValues(sample.kpis)};});return{run_id:data.run_id,final,series};}
export function decodeComparison(value:unknown):RunComparison{const data=object(value);if(!Array.isArray(data.runs))throw Error('Comparison is missing its runs.');return{runs:data.runs.map(candidate=>{const run=object(candidate);if(typeof run.id!=='string'||typeof run.title!=='string')throw Error('Comparison has an unnamed run.');const knobs=object(run.knobs),algorithms=object(run.algorithms);if(!Object.values(knobs).every(value=>['string','number','boolean'].includes(typeof value))||!Object.values(algorithms).every(value=>typeof value==='string'))throw Error('Comparison settings are invalid.');return{id:run.id,title:run.title,template_id:typeof run.template_id==='string'?run.template_id:undefined,kpis:validateKpiValues(run.kpis),knobs:knobs as import('./api').ComparisonRun['knobs'],algorithms:algorithms as import('./api').ComparisonRun['algorithms']};})};}
export function decodeAlgorithms(value:unknown):AlgorithmDescriptor[]{
 const grouped=object(value),rows:AlgorithmDescriptor[]=[];
 for(const [slot,entries] of Object.entries(grouped)){
  if(!(['allocation','path_planning','conflict_resolution','charging'] as string[]).includes(slot)||!Array.isArray(entries))throw Error('Algorithm catalog has an invalid slot.');
  for(const candidate of entries){const row=object(candidate);if(!['id','label','description'].every(key=>typeof row[key]==='string'))throw Error('Algorithm catalog has an invalid entry.');rows.push({id:row.id as string,label:row.label as string,description:row.description as string,slot:slot as AlgorithmSlot});}
 }
 return rows;
}
