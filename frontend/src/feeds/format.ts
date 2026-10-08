/** Format exact relative canonical seconds without rounding the nanosecond integer. */
export function seconds(ns: string, origin = '0'): string {
  const delta=BigInt(ns)-BigInt(origin),negative=delta<0n,value=negative?-delta:delta;
  const fractional=(value%1_000_000_000n).toString().padStart(9,'0').replace(/0+$/,'');
  return `${negative?'-':''}${value/1_000_000_000n}${fractional?'.'+fractional:''} s`;
}
export function exactValue(value: unknown): string {
  if(Array.isArray(value))return `[${value.map(exactValue).join(',')}]`;
  if(value && typeof value==='object') {
    const data=value as Record<string,unknown>;
    if(Object.keys(data).length===1 && typeof data.$integer==='string')return data.$integer;
    if(Object.keys(data).length===1 && typeof data.$number==='string')return `${data.$number} (float)`;
    if(Object.keys(data).length===1 && data.$record && typeof data.$record==='object')return `{${Object.entries(data.$record).map(([key,item])=>`${JSON.stringify(key)}:${exactValue(item)}`).join(',')}}`;
    return `{${Object.entries(data).map(([key,item])=>`${JSON.stringify(key)}:${exactValue(item)}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'absent';
}
