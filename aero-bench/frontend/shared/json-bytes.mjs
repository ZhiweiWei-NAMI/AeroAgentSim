/** Read an object from UTF-8 bytes without constructing a document-sized string. */
export function parseJsonObjectBytes(bytes, options = {}) {
  const decode = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  const parse = options.parseValue ?? (text => JSON.parse(text));
  let cursor = 0;
  const space = () => {
    while ([32, 9, 10, 13].includes(bytes[cursor])) cursor++;
  };
  const take = expected => {
    space();
    if (bytes[cursor++] !== expected) throw new Error("invalid JSON separator");
  };
  const endValue = (start, offset) => {
    const stack = [];
    let quoted = false;
    let escaped = false;
    const first = bytes[start];
    for (let i = start; i < bytes.length; i++) {
      const ch = bytes[i];
      if (quoted) {
        if (escaped) escaped = false;
        else if (ch === 92) escaped = true;
        else if (ch === 34) {
          quoted = false;
          if (stack.length === 0) return i + 1;
        }
        continue;
      }
      if (ch === 34) quoted = true;
      else if (ch === 123 || ch === 91) {
        stack.push(ch === 123 ? 125 : 93);
        if (stack.length + offset - 1 > 64) throw new Error("JSON nesting exceeds the evidence bound");
      } else if (ch === 125 || ch === 93) {
        if (!stack.length) {
          if (i === start) throw new Error("missing JSON value");
          return i;
        }
        if (stack.pop() !== ch) throw new Error("mismatched JSON container");
        if (!stack.length) return i + 1;
      } else if (!stack.length && (ch === 44 || ch === 32 || ch === 9 || ch === 10 || ch === 13)) {
        if (i === start) throw new Error("missing JSON value");
        return i;
      }
    }
    if (quoted || stack.length || first === undefined) throw new Error("incomplete JSON value");
    return bytes.length;
  };
  const value = depth => {
    space();
    const end = endValue(cursor, depth);
    const result = parse(decode.decode(bytes.subarray(cursor, end)), depth);
    cursor = end;
    return result;
  };
  const result = {};
  const keys = new Set();
  take(123);
  space();
  if (bytes[cursor] !== 125) {
    while (true) {
      space();
      if (bytes[cursor] !== 34) throw new Error("invalid JSON object key");
      const key = value(1);
      if (typeof key !== "string" || keys.has(key)) throw new Error("duplicate or invalid JSON object key");
      keys.add(key);
      take(58);
      space();
      const keep = options.keys === undefined || options.keys.has(key);
      let member;
      if (bytes[cursor] === 91) {
        cursor++;
        member = [];
        space();
        if (bytes[cursor] !== 93) {
          let index = 0;
          while (true) {
            const item = value(2);
            if (keep) member.push(item);
            options.onArrayItem?.(key, item, index++);
            space();
            if (bytes[cursor] === 93) break;
            take(44);
          }
        }
        take(93);
      } else member = value(1);
      if (keep) Object.defineProperty(result, key, { value: member, enumerable: true, writable: true, configurable: true });
      space();
      if (bytes[cursor] === 125) break;
      take(44);
    }
  }
  take(125);
  space();
  if (cursor !== bytes.length) throw new Error("trailing JSON content");
  return result;
}
