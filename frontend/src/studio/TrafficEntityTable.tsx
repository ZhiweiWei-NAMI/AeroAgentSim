import React, { useMemo, useState } from "react";

type Json = any;
interface TypeRowLike { id: string; parents: string[]; abstract?: boolean }
interface FieldDefLike { id: string; type?: string; schema?: Json; metadata?: Json }
export interface TrafficEntityTableProps { scenario: Record<string, any>; onChange: (scenario: Record<string, any>) => void; registryFields?:FieldDefLike[]; schemas?:Record<string,any> }

const INT = "$integer"; // lossless tag convention from frontend/src/feeds/lossless-json.ts
const BG = "background";

const ancestors = (type: string, types: TypeRowLike[]): string[] => {
  const byId = new Map(types.map(t => [t.id, t])); const out: string[] = [];
  const walk = (id: string): void => { if (out.indexOf(id) < 0) { out.push(id); (byId.get(id)?.parents ?? []).forEach(walk); } };
  walk(type); return out;
};
const writeInt = (raw: string): Json | undefined => {
  if (!/^-?\d+$/.test(raw)) return undefined;
  const b = BigInt(raw);
  return b >= BigInt(Number.MIN_SAFE_INTEGER) && b <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(raw) : { [INT]: raw };
};

function ValueEditor({ value, schema: supplied, schemas, onWrite }: { value: Json; schema?: Json; schemas:Record<string,any>; onWrite: (v: Json) => void }) {
  const schema=typeof supplied==='string'?schemas[supplied]:supplied?.schema_ref?schemas[supplied.schema_ref]:supplied;
  if(value===null||value===undefined)return <code>{value===null?'Explicit null':'Unset'}</code>;
  const ro = <code style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{JSON.stringify(value)}</code>;
  const num = (step: string, write: (s: string) => void) =>
    <input type="number" step={step} value={typeof value === "number" ? value : ""} onChange={e => write(e.target.value)} />;
  if (value && typeof value === "object" && !Array.isArray(value) && value[INT] !== undefined)
    return <input value={String(value[INT])} onChange={e => { const v = writeInt(e.target.value); if (v !== undefined) onWrite(typeof v === "number" ? { [INT]: e.target.value } : v); }} />;
  const st = schema?.type;
  if (st === "string" || (st === undefined && typeof value === "string")) {
    const en: string[] | undefined = schema?.enum;
    if (en) return <select value={value} onChange={e => onWrite(e.target.value)}>{en.map((o: string) => <option key={o} value={o}>{o}</option>)}</select>;
    return <input value={String(value ?? "")} onChange={e => onWrite(e.target.value)} />;
  }
  if (st === "boolean" || (st === undefined && typeof value === "boolean")) return <input type="checkbox" checked={!!value} onChange={e => onWrite(e.target.checked)} />;
  if (st === "integer" || (st === undefined && typeof value === "number" && Number.isInteger(value)))
    return num("1", s => { const v = writeInt(s); if (v !== undefined) onWrite(v); });
  if (st === "number" || (st === undefined && typeof value === "number"))
    return num("any", s => { const v = Number(s); if (s !== "" && Number.isFinite(v)) onWrite(v); });
  if (st === "vector") {
    if (!Array.isArray(value)) return <span>not set (no synthetic origin/zeros)</span>;
    const len = schema?.length ?? value.length;
    return <span>{Array.from({ length: len }, (_, i) =>
      <input key={i} type="number" step="any" style={{ width: 90 }} value={value[i] ?? ""} onChange={e => { const v = Number(e.target.value); if (e.target.value !== "" && Number.isFinite(v)) { const next = value.slice(); next[i] = v; onWrite(next); } }} />)}</span>;
  }
  if (st === "ref" || (value && typeof value === "object" && !Array.isArray(value) && value.$ref))
    return <span>ref → <code>{JSON.stringify(value?.$ref ?? value)}</code> (read-only; edit via raw scenario)</span>;
  return ro; // unknown schema / arrays / nested objects: complete value, read-only
}

export function TrafficEntityTable({ scenario, onChange,registryFields=[],schemas={} }: TrafficEntityTableProps) {
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [hideBg, setHideBg] = useState(false);
  const types: TypeRowLike[] = scenario?.registry?.types ?? [];
  const fields: FieldDefLike[] = [...registryFields,...(scenario?.registry?.fields ?? [])];
  const fieldById = useMemo(() => new Map(fields.map(f => [f.id, f])), [fields]);
  // Background = ONLY an explicitly present role field whose schema enumerates/holds the background value; missing role → not background.
  const roleFieldIds = useMemo(() => fields.filter(f => f.id === "traffic.actor.role" || (Array.isArray(f.schema?.enum) && f.schema.enum.includes(BG))).map(f => f.id), [fields]);
  const isBg = (e: any) => roleFieldIds.some(id => e?.facts?.[id] === BG);
  const genById = useMemo(() => {
    const m = new Map<string, Json>(); const scan = (v: Json) => {
      if (Array.isArray(v)) v.forEach(scan);
      else if (v && typeof v === "object" && v.$ref && v.$ref.id && v.$ref.generation !== undefined && !m.has(v.$ref.id)) m.set(String(v.$ref.id), v.$ref.generation);
    };
    (scenario?.entities ?? []).forEach((e: any) => Object.values(e?.facts ?? {}).forEach(scan));
    return m;
  }, [scenario]);
  const setFact = (eid: string, key: string, v: Json) =>
    onChange({ ...scenario, entities: (scenario.entities ?? []).map((e: any) => e.id === eid ? { ...e, facts: { ...e.facts, [key]: v } } : e) });
  const groups = useMemo(() => {
    const m = new Map<string, any[]>();
    (scenario?.entities ?? []).forEach((e: any) => {
      if (hideBg && isBg(e)) return;
      if (q && !String(e.id).includes(q) && !String(e.type ?? "").includes(q)) return;
      const root = String(e.type);
      const arr = m.get(root) ?? []; arr.push(e); m.set(root, arr);
    });
    return [...m.entries()];
  }, [scenario, q, hideBg, types]);
  return (
    <div>
      <div>
        <input placeholder="filter id/type…" value={q} onChange={e => setQ(e.target.value)} />
        <label><input type="checkbox" checked={hideBg} onChange={e => setHideBg(e.target.checked)} /> hide background actors (explicit role only)</label>
      </div>
      {groups.map(([root, ents]) => (
        <table key={root} style={{ borderCollapse: "collapse", marginBottom: 12 }}><caption style={{ textAlign: "left" }}>{root} ({ents.length})</caption>
          <thead><tr><th>id</th><th>type</th><th>generation</th><th>initial fields</th></tr></thead>
          <tbody>{ents.map((e: any) => {
            const facts = e.facts ?? {}; const expanded = open === e.id;
            return (
              <React.Fragment key={e.id}>
                <tr onClick={() => setOpen(expanded ? null : e.id)} style={{ cursor: "pointer" }}>
                  <td>{e.id}</td><td>{e.type}</td>
                  <td>{genById.has(e.id) ? String(genById.get(e.id)) : "—"}</td>
                  <td>{Object.keys(facts).length}</td>
                </tr>
                {expanded && (
                  <tr><td colSpan={4}>
                    {Object.keys(facts).map(k => {
                      const f = fieldById.get(k);
                      return (
                        <div key={k} style={{ borderTop: "1px solid #ddd" }}>
                          <code>{k}</code>{f ? <em> ({f.type ?? "?"}{f.metadata?.role ? `, role:${f.metadata.role}` : ""})</em> : <em> (unknown key)</em>}
                          <ValueEditor value={facts[k]} schema={f?.schema} schemas={schemas} onWrite={v => setFact(e.id, k, v)} />
                        </div>
                      );
                    })}
                  </td></tr>
                )}
              </React.Fragment>
            );
          })}</tbody>
        </table>
      ))}
      {groups.length === 0 && <p>no entities match</p>}
    </div>
  );
}
export default TrafficEntityTable;
