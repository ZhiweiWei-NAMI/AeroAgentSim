/** Preserve JSON number spelling when checking Python-canonical evidence hashes. */
interface JsonNode {
  readonly canonical: string;
  readonly members?: ReadonlyMap<string, JsonNode>;
}

function scanJson(text: string, depthOffset = 0): JsonNode {
  let cursor = 0;
  const whitespace = (): void => { while (/\s/.test(text[cursor] ?? "") && cursor < text.length) cursor += 1; };
  const string = (): string => {
    const start = cursor++;
    while (cursor < text.length) {
      const character = text[cursor++];
      if (character === "\\") cursor += 1;
      else if (character === '"') return text.slice(start, cursor);
    }
    throw new Error("unterminated JSON string");
  };
  const value = (depth: number): JsonNode => {
    if (depth > 64) throw new Error("JSON nesting exceeds the evidence bound");
    whitespace();
    const start = cursor;
    const token = text[cursor];
    if (token === '"') return { canonical: JSON.stringify(JSON.parse(string())) };
    if (token === "{" || token === "[") {
      cursor += 1;
      const members = new Map<string, JsonNode>();
      const items: string[] = [];
      const closing = token === "{" ? "}" : "]";
      whitespace();
      while (text[cursor] !== closing) {
        if (token === "{") {
          if (text[cursor] !== '"') throw new Error("invalid JSON object key");
          const key = JSON.parse(string()) as string;
          if (members.has(key)) throw new Error(`duplicate JSON object key: ${key}`);
          whitespace();
          if (text[cursor++] !== ":") throw new Error("missing JSON member separator");
          members.set(key, value(depth + 1));
        } else {
          items.push(value(depth + 1).canonical);
        }
        whitespace();
        if (text[cursor] === closing) break;
        if (text[cursor++] !== ",") throw new Error("missing JSON separator");
        whitespace();
      }
      cursor += 1;
      if (token === "[") return { canonical: `[${items.join(",")}]` };
      return {
        canonical: `{${[...members.keys()].sort().map(key => `${JSON.stringify(key)}:${members.get(key)!.canonical}`).join(",")}}`,
        members,
      };
    }
    while (cursor < text.length && !/[\s,\]}]/.test(text[cursor]!)) cursor += 1;
    if (cursor === start) throw new Error("invalid JSON value");
    const raw = text.slice(start, cursor);
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed === "number" && (!Number.isFinite(parsed)
      || (!/[.eE]/.test(raw) && !Number.isSafeInteger(parsed)))) {
      throw new Error("JSON number is outside the finite exact range");
    }
    return { canonical: raw };
  };
  const result = value(depthOffset);
  whitespace();
  if (cursor !== text.length) throw new Error("trailing JSON content");
  return result;
}

export function parseStrictJson(text: string, depthOffset = 0): unknown {
  // Native parsing rejects invalid strings, trailing commas, and JSON whitespace.
  const value: unknown = JSON.parse(text);
  scanJson(text, depthOffset);
  return value;
}

export function canonicalJsonWithoutMember(text: string, omitted: string): string {
  const node = scanJson(text);
  if (node.canonical !== text || node.members === undefined || !node.members.has(omitted)) {
    throw new Error("evidence record is not canonical JSON");
  }
  return `{${[...node.members.keys()].filter(key => key !== omitted)
    .map(key => `${JSON.stringify(key)}:${node.members!.get(key)!.canonical}`).join(",")}}`;
}
