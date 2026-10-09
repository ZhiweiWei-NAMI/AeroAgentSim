import { mapping, type Draft } from './model';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { entityLabel } from '../pages/inspection-format';

/**
 * Friendly defaults for an authored behaviour injection point. Transport
 * fields are derived from the authored declaration and, for the operator
 * source, from the real acknowledged watermark progress. Nothing is invented:
 * when the run does not author operator timing, the transport fields stay
 * empty and the ingress validation reports them as explicitly required.
 */
export interface TransportFields extends Record<string,string> { schema:string;target:string;stream_id:string;at_ns:string;clock_id:string;mapping_id:string;numerator:string;denominator:string;idempotency_key:string }
export const emptyTransport = (schema = ''):TransportFields => ({schema,target:'',stream_id:'',at_ns:'',clock_id:'',mapping_id:'',numerator:'',denominator:'',idempotency_key:''});

export function transportForPoint(point:Draft, options:{operator:boolean;operatorNs:bigint}):TransportFields {
  const base:TransportFields = {...emptyTransport(String(point.command)),target:String(point.target),stream_id:String(point.stream_id)};
  if(!options.operator)return base;
  // Canonical source stamp at the next unclosed operator instant, from the
  // actual acknowledged watermark progress (never from wall-clock guessing).
  const at=(options.operatorNs+1n).toString();
  return {...base,at_ns:at,clock_id:'canonical',mapping_id:'canonical',numerator:at,denominator:'1'};
}

/** UUID idempotency key when the runtime supports it; otherwise reports absence. */
export function newIdempotencyKey():string {
  const uuid=(globalThis.crypto as Crypto|undefined)?.randomUUID?.();
  if(!uuid)throw Error('Idempotency keys require a UUID-capable crypto runtime');
  return uuid;
}

/**
 * Plain-language target summary from the run's actual recorded entities. The
 * current traffic schema only declares the incident reference, so only actual
 * incident identity is described — vehicle or lane details are not invented.
 */
export function actualTargetSummary(point:Draft, store:TemporalFeedStore):string {
  if(point.id==='accident') {
    const incidents=[...store.entities.values()].filter(row=>row.typeId==='aas:TrafficIncident');
    return incidents.length?`Trigger the accident response for ${incidents.map(entityLabel).join(', ')}. Choose an actor or add a reason if needed.`:'Waiting for the run’s incident entity to load.';
  }
  return `Send ${String(point.name??point.id)} to its configured plugin.`;
}

/**
 * Distinguishes what a receipt proves. Admission is stream acceptance, not
 * execution; execution is proven only by engine action receipts.
 */
export function receiptSummary(receipt:unknown):string {
  if(!mapping(receipt))return 'Receipt could not be interpreted; read the raw record below.';
  const disposition=typeof receipt.disposition==='string'?receipt.disposition:undefined;
  if(disposition==='accepted')return 'Event accepted for processing. The execution receipts show what happened next.';
  if(disposition==='delayed')return 'Event scheduled for later. Check its execution receipt after it runs.';
  if(disposition==='rejected')return `Event rejected (${String(receipt.code??'no code')}): it was not executed.`;
  return 'Receipt without an admission disposition; read the raw record below.';
}

/** Only authored schema defaults; absent values remain unset. */
export function schemaDefaults(schema:unknown, definitions:Draft={}, seen=new Set<string>()):unknown {
  const reference=typeof schema==='string'?schema:mapping(schema)&&typeof schema.schema_ref==='string'?schema.schema_ref:undefined;
  if(reference){if(seen.has(reference))return undefined;return schemaDefaults(definitions[reference],definitions,new Set([...seen,reference]));}
  if(!mapping(schema))return undefined;
  if('default' in schema)return structuredClone(schema.default);
  if(schema.type==='record'&&mapping(schema.members)){
    const entries=Object.entries(schema.members).map(([key,child])=>[key,schemaDefaults(child,definitions,seen)] as const).filter(([,value])=>value!==undefined);
    if(entries.length)return Object.fromEntries(entries);
  }
  return undefined;
}

/** The waiting banner fires the prepared authored event through its typed form. */
export function activateInjectionShortcut(controls: HTMLElement): void {
  const button=controls.querySelector<HTMLButtonElement>('[data-injection-submit]');
  if(button&&!button.disabled){button.click();return;}
  controls.querySelector<HTMLElement>('input, select')?.focus();
}
