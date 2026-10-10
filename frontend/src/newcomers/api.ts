import {decodeAlgorithms,decodeComparison,decodeKpis,decodeTemplates} from './api-decode';
import { parseAuthoringJson, stringifyLossless } from '../feeds/lossless-json';

export type Category = 'logistics'|'emergency'|'inspection'|'agriculture'|'mobility'|'security'|'airspace'|'weather'|'energy'|'mixed';
export type Severity = 'info'|'notice'|'warning'|'critical';
export type KnobValue = string|number|boolean;
export interface TemplateKnob {id:string;label:string;type:'integer'|'number'|'choice'|'boolean';default:KnobValue;min?:number;max?:number;options?:KnobValue[];pointer?:string;apply?:string;description?:string}
export interface ScenarioTemplate {format:'aeroagentsim.template/v1';id:string;title:string;summary:string;category:Category;difficulty:'beginner'|'intermediate'|'advanced';duration_s:number;scenario:string;thumbnail?:string;thumbnail_url?:string;observe:string[];try:string[];knobs:TemplateKnob[];kpis:string[]}
export interface BusinessEvent {at_ns:string;category:Category;severity:Severity;text:string;entities:string[];position?:[number,number,number]}
export interface KpiValue {value:number|null;unit:string;label:string;direction?:'min'|'max'|'neutral'}
export type KpiValues = Record<string,KpiValue>;
export interface KpiSample {commitIndex?:number;at_ns:string;kpis:KpiValues}
export interface RunKpis {format?:'aeroagentsim.kpi/v1';run_id:string;final:KpiValues;series:KpiSample[]}
export type AlgorithmSlot = 'allocation'|'path_planning'|'conflict_resolution'|'charging';
export interface AlgorithmDescriptor {id:string;slot:AlgorithmSlot;label:string;description:string}
export interface ComparisonRun {id:string;title:string;template_id?:string;kpis:KpiValues;knobs:Record<string,KnobValue>;algorithms:Partial<Record<AlgorithmSlot,string>>}
export interface RunComparison {runs:ComparisonRun[]}
export interface TemplateRunReceipt {run_id:string;workspace_id?:string}
export class TemplateApi {
 constructor(readonly base:string=window.location.origin){}
 async request<T>(path:string,body?:unknown,signal?:AbortSignal):Promise<T>{
  const response=await fetch(`${this.base.replace(/\/$/,'')}${path}`,{signal,...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:stringifyLossless(body)})});
  if(!response.ok)throw Error(`HTTP ${response.status}: ${await response.text()}`);
  return parseAuthoringJson(await response.text()) as T;
 }
 async templates(signal?:AbortSignal){return decodeTemplates(await this.request<unknown>('/v1/templates',undefined,signal));}
 async template(id:string,signal?:AbortSignal){return decodeTemplates([await this.request<unknown>(`/v1/templates/${encodeURIComponent(id)}`,undefined,signal)])[0];}
 async start(id:string,knobs:Record<string,KnobValue>={},workspace=false){const receipt=await this.request<TemplateRunReceipt>(`/v1/templates/${encodeURIComponent(id)}/runs`,{knobs,...(workspace?{workspace:true}:{})});if(!receipt||typeof receipt.run_id!=='string'||!receipt.run_id)throw Error('The service did not return a run.');return receipt;}
 async workspace(id:string,knobs:Record<string,KnobValue>={}){const receipt=await this.request<{workspace_id:string}>(`/v1/templates/${encodeURIComponent(id)}/workspace`,{knobs});if(!receipt||typeof receipt.workspace_id!=='string'||!receipt.workspace_id)throw Error('The service did not return a workspace.');return receipt;}
 async kpis(id:string,signal?:AbortSignal){return decodeKpis(await this.request<unknown>(`/v1/runs/${encodeURIComponent(id)}/kpis`,undefined,signal));}
 async compare(ids:string[],signal?:AbortSignal){if(ids.length<2||ids.length>4)throw Error('Choose two to four runs to compare.');return decodeComparison(await this.request<unknown>(`/v1/runs/compare?ids=${ids.map(encodeURIComponent).join(',')}`,undefined,signal));}
 async algorithms(signal?:AbortSignal){return decodeAlgorithms(await this.request<unknown>('/v1/algorithms',undefined,signal));}
}
export function consoleApiBase(search:string){return new URLSearchParams(search).get('api')??window.location.origin;}
export function templateRunUrl(receipt:TemplateRunReceipt,base:string,templateId:string){return `/runs/${encodeURIComponent(receipt.run_id)}?mode=live&view=simple&template=${encodeURIComponent(templateId)}&api=${encodeURIComponent(base)}`;}
