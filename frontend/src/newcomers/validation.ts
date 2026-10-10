import type { BusinessEvent, Category, KpiValues } from './api';
const categories=['logistics','emergency','inspection','agriculture','mobility','security','airspace','weather','energy','mixed'];
function record(value:unknown):Record<string,unknown>{if(!value||typeof value!=='object'||Array.isArray(value))throw Error('Expected a business-summary object.');return value as Record<string,unknown>;}
function text(value:unknown):asserts value is string{if(typeof value!=='string')throw Error('Expected text in a business summary.');}
export function validateKpiValues(value:unknown):KpiValues{
 const values=record(value);
 for(const metric of Object.values(values)){
  const row=record(metric);text(row.unit);text(row.label);
  if(row.value!==null&&(typeof row.value!=='number'||!Number.isFinite(row.value)))throw Error(`Invalid result for ${row.label}.`);
  if(row.direction!==undefined&&!['min','max','neutral'].includes(String(row.direction)))throw Error('Invalid result direction.');
 }
 return values as unknown as KpiValues;
}
export function validateBusinessEvents(value:unknown):BusinessEvent[]{
 if(!Array.isArray(value))throw Error('Expected a business event list.');
 return value.map(raw=>{
  const event=record(raw);text(event.text);text(event.at_ns);
  if(!/^\d+$/.test(event.at_ns))throw Error('Invalid business event time.');
  if(!categories.includes(String(event.category)))throw Error('Unknown business event category.');
  if(!['info','notice','warning','critical'].includes(String(event.severity)))throw Error('Unknown business event severity.');
  if(!Array.isArray(event.entities)||event.entities.some(id=>typeof id!=='string'))throw Error('Invalid business event entities.');
  if(event.position!==undefined&&(!Array.isArray(event.position)||event.position.length!==3||event.position.some(value=>typeof value!=='number'||!Number.isFinite(value))))throw Error('Invalid business event position.');
  return event as unknown as BusinessEvent;
 });
}
