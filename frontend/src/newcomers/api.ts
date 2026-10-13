import {decodeAlgorithms,decodeComparison,decodeKpis,decodeTemplates} from './api-decode';
import { parseAuthoringJson, stringifyLossless } from '../feeds/lossless-json';

export type Category = 'logistics'|'emergency'|'inspection'|'agriculture'|'mobility'|'security'|'airspace'|'weather'|'energy'|'mixed';
export type Severity = 'info'|'notice'|'warning'|'critical';
export type KnobValue = string|number|boolean;
export interface TemplateKnob {id:string;label:string;label_zh?:string;type:'integer'|'number'|'choice'|'boolean';default:KnobValue;min?:number;max?:number;options?:KnobValue[];options_zh?:string[];pointer?:string;apply?:string;description?:string;description_zh?:string}
export interface ScenarioTemplate {format:'aeroagentsim.template/v1';id:string;title:string;title_zh?:string;summary:string;summary_zh?:string;category:Category;difficulty:'beginner'|'intermediate'|'advanced';duration_s:number;scenario:string;thumbnail?:string;thumbnail_url?:string;observe:string[];observe_zh?:string[];try:string[];try_zh?:string[];knobs:TemplateKnob[];kpis:string[]}
export interface BusinessEvent {at_ns:string;category:Category;severity:Severity;text:string;text_zh?:string;entities:string[];position?:[number,number,number]}
export interface KpiValue {value:number|null;unit:string;label:string;label_zh?:string;direction?:'min'|'max'|'neutral'}
export type KpiValues = Record<string,KpiValue>;
export interface KpiSample {commitIndex?:number;at_ns:string;kpis:KpiValues}
export interface RunKpis {format?:'aeroagentsim.kpi/v1';run_id:string;final:KpiValues;series:KpiSample[]}
export type AlgorithmSlot = 'allocation'|'path_planning'|'conflict_resolution'|'charging';
export interface AlgorithmDescriptor {id:string;slot:AlgorithmSlot;label:string;label_zh?:string;description:string;description_zh?:string;/** Components that read this slot at runtime; absent on older servers. */consumers?:Array<{engine:string;plugin:string}>}
export interface ComparisonRun {id:string;title:string;title_zh?:string;template_id?:string;kpis:KpiValues;knobs:Record<string,KnobValue>;algorithms:Partial<Record<AlgorithmSlot,string>>}
export interface RunComparison {runs:ComparisonRun[]}
export class TemplateApi {
 constructor(readonly base:string=window.location.origin){}
 async request<T>(path:string,body?:unknown,signal?:AbortSignal):Promise<T>{
  const response=await fetch(`${this.base.replace(/\/$/,'')}${path}`,{signal,...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:stringifyLossless(body)})});
  if(!response.ok)throw Error(`HTTP ${response.status}: ${await response.text()}`);
  return parseAuthoringJson(await response.text()) as T;
 }
 async templates(signal?:AbortSignal){return decodeTemplates(await this.request<unknown>('/v1/templates',undefined,signal));}
 async template(id:string,signal?:AbortSignal){return decodeTemplates([await this.request<unknown>(`/v1/templates/${encodeURIComponent(id)}`,undefined,signal)])[0];}
 async kpis(id:string,signal?:AbortSignal){return decodeKpis(await this.request<unknown>(`/v1/runs/${encodeURIComponent(id)}/kpis`,undefined,signal));}
 async compare(ids:string[],signal?:AbortSignal){if(ids.length<2||ids.length>4)throw Error('Choose two to four runs to compare.');return decodeComparison(await this.request<unknown>(`/v1/runs/compare?ids=${ids.map(encodeURIComponent).join(',')}`,undefined,signal));}
 async algorithms(signal?:AbortSignal){return decodeAlgorithms(await this.request<unknown>('/v1/algorithms',undefined,signal));}
}
export function consoleApiBase(search:string){return new URLSearchParams(search).get('api')??window.location.origin;}
