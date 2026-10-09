/** Preserve integer wire tokens before JSON.parse can round them. */
export function parseLosslessJson(source: string, preserveFloatKind = false): unknown {
  let result = '', index = 0;
  while (index < source.length) {
    const char = source[index];
    if (char === '"') {
      const start = index++;
      while (index < source.length) { const next = source[index++]; if (next === '\\') index++; else if (next === '"') break; }
      result += source.slice(start, index); continue;
    }
    if (char === '-' || char >= '0' && char <= '9') {
      const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(source.slice(index));
      if (match) {
        const token = match[0]; index += token.length;
        result += preserveFloatKind && /[.eE]/.test(token) ? JSON.stringify({$number:token}) : /^-?\d+$/.test(token) && (BigInt(token) > BigInt(Number.MAX_SAFE_INTEGER) || BigInt(token) < BigInt(Number.MIN_SAFE_INTEGER)) ? JSON.stringify({ $integer: token }) : token;
        continue;
      }
    }
    result += char; index++;
  }
  return JSON.parse(result) as unknown;
}

/** Send tagged viewer/draft quantities as real JSON numeric tokens to Python. */
export function stringifyLossless(value: unknown): string {
  const encode = (item: unknown, tags = true): string => {
    if (item === null || typeof item === 'string' || typeof item === 'boolean') return JSON.stringify(item);
    if (typeof item === 'number') { if (!Number.isFinite(item) || Number.isInteger(item) && !Number.isSafeInteger(item)) throw Error('Unsafe numeric value; use a lossless integer/number tag'); return JSON.stringify(item); }
    if (Array.isArray(item)) return `[${item.map(child => child === undefined ? 'null' : encode(child)).join(',')}]`;
    if (item && typeof item === 'object') {
      const row = item as Record<string, unknown>, keys = Object.keys(row);
      if (tags && keys.length === 1 && '$integer' in row) {
        if (typeof row.$integer !== 'string' || !/^-?(0|[1-9]\d*)$/.test(row.$integer) || row.$integer === '-0') throw Error('Invalid integer tag');
        return row.$integer;
      }
      if (tags && keys.length === 1 && '$number' in row) {
        if (typeof row.$number !== 'string' || !/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(row.$number) || !Number.isFinite(Number(row.$number))) throw Error('Invalid number tag');
        return /[.eE]/.test(row.$number) ? row.$number : row.$number + '.0';
      }
      if (tags && keys.length === 1 && '$record' in row) return encode(row.$record, false);
      return `{${keys.filter(key => row[key] !== undefined).map(key => `${JSON.stringify(key)}:${encode(row[key])}`).join(',')}}`;
    }
    throw Error('Value is not portable JSON');
  };
  return encode(value);
}

/** Preserve behaviour numeric kinds while keeping geometry/config widgets numeric. */
export function parseAuthoringJson(source:string):unknown {
  const value=parseLosslessJson(source), typed=parseLosslessJson(source,true);
  const merge=(plain:unknown,exact:unknown):unknown=>{
    if(Array.isArray(plain)&&Array.isArray(exact))return plain.map((child,index)=>merge(child,exact[index]));
    if(plain&&exact&&typeof plain==='object'&&typeof exact==='object') {
      const row=plain as Record<string,unknown>, numeric=exact as Record<string,unknown>;
      return Object.fromEntries(Object.entries(row).map(([key,child])=>[key,key==='behaviours'||key==='package'?numeric[key]:merge(child,numeric[key])]));
    }
    return plain;
  };
  return merge(value,typed);
}
