import { Details } from '../console/Details';
import React, { useMemo, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';

type Json = any;
interface FieldDefLike { id: string; type?: string; schema?: Json; metadata?: Json }
export interface TrafficEntityTableProps { scenario: Record<string, any>; onChange: (scenario: Record<string, any>) => void; registryFields?: FieldDefLike[]; schemas?: Record<string, any> }
const INT = '$integer';
const BG = 'background';

const writeInt = (raw: string): Json | undefined => {
  if (!/^-?\d+$/.test(raw)) return undefined;
  const b = BigInt(raw);
  return b >= BigInt(Number.MIN_SAFE_INTEGER) && b <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(raw) : { [INT]: raw };
};
function resolveSchema(supplied: Json, schemas: Record<string, any>): Json {
  const seen = new Set<string>();
  let current = supplied;
  while (typeof current === 'string' || typeof current?.schema_ref === 'string') {
    const ref = typeof current === 'string' ? current : current.schema_ref;
    if (seen.has(ref)) return undefined;
    seen.add(ref); current = schemas[ref];
  }
  return current;
}

function ValueEditor({ value, schema: supplied, schemas, onWrite, label }: { value: Json; schema?: Json; schemas: Record<string, any>; onWrite: (v: Json) => void; label: string }) {
  const { t } = useI18n();
  const schema = resolveSchema(supplied, schemas);
  if (value === null || value === undefined) return <code>{value === null ? t('Explicit null') : t('Unset')}</code>;
  const readonly = <Details title={t('Field value')} buttonLabel={t('View value')}><p>{t('Complex values are read-only here. Use the Scenario JSON editor to change them without losing their structure.')}</p><code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{JSON.stringify(value)}</code></Details>;
  const num = (step: string, write: (s: string) => void) =>
    <input aria-label={label} type="number" step={step} value={typeof value === 'number' ? value : ''} onChange={e => write(e.target.value)} />;
  if (value && typeof value === 'object' && !Array.isArray(value) && value[INT] !== undefined)
    return <input aria-label={label} inputMode="numeric" value={String(value[INT])} onChange={e => { const v = writeInt(e.target.value); if (v !== undefined) onWrite(typeof v === 'number' ? { [INT]: e.target.value } : v); }} />;
  const st = schema?.type;
  // Store the selected enum member, not its stringified display value.
  if (Array.isArray(schema?.enum) && schema.enum.every((item: Json) => ['string', 'number', 'boolean'].includes(typeof item)))
    return <select aria-label={label} value={schema.enum.findIndex((item: Json) => Object.is(item, value))} onChange={e => onWrite(schema.enum[Number(e.target.value)])}>
      {!schema.enum.some((item: Json) => Object.is(item, value)) && <option value={-1} disabled>{t('Current value is outside the allowed choices')}</option>}
      {schema.enum.map((item: Json, i: number) => <option key={i} value={i}>{item === true ? t('true') : item === false ? t('false') : String(item)}</option>)}
    </select>;
  // A mismatched authored scalar is not an unchecked boolean or an empty/zero number.
  if ((st === 'string' || st === 'boolean') && typeof value !== st || (st === 'integer' || st === 'number') && typeof value !== 'number') return readonly;
  if (st === 'string' || (st === undefined && typeof value === 'string')) return <input aria-label={label} value={String(value)} onChange={e => onWrite(e.target.value)} />;
  if (st === 'boolean' || (st === undefined && typeof value === 'boolean')) return <input aria-label={label} type="checkbox" checked={value === true} onChange={e => onWrite(e.target.checked)} />;
  if (st === 'integer' || (st === undefined && typeof value === 'number' && Number.isInteger(value))) return num('1', s => { const v = writeInt(s); if (v !== undefined) onWrite(v); });
  if (st === 'number' || (st === undefined && typeof value === 'number')) return num('any', s => { const v = Number(s); if (s !== '' && Number.isFinite(v)) onWrite(v); });
  if (st === 'vector') {
    if (!Array.isArray(value)) return <span>{t('Not set; no origin or zero values are invented.')}</span>;
    const len = Number.isSafeInteger(schema?.length) && schema.length >= 0 ? schema.length : value.length;
    return <span>{Array.from({ length: len }, (_, i) =>
      <input aria-label={t('Component {index} of {field}', { index: i + 1, field: label })} key={i} type="number" step="any" style={{ width: 90 }} value={value[i] ?? ''} onChange={e => { const v = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(v)) { const next = value.slice(); next[i] = v; onWrite(next); } }} />)}</span>;
  }
  if (st === 'ref' || (value && typeof value === 'object' && !Array.isArray(value) && value.$ref))
    return <span>{t('Reference to {entity}', { entity: value?.$ref?.id ?? t('Unknown reference') })} <Details title={t('Entity reference')} buttonLabel={t('Reference details')}><pre>{JSON.stringify(value?.$ref ?? value, null, 2)}</pre></Details></span>;
  return readonly;
}

export function TrafficEntityTable({ scenario, onChange, registryFields = [], schemas = {} }: TrafficEntityTableProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [hideBg, setHideBg] = useState(false);
  const fields: FieldDefLike[] = useMemo(() => [...registryFields, ...(scenario?.registry?.fields ?? [])], [registryFields, scenario?.registry?.fields]);
  const fieldById = useMemo(() => new Map(fields.map(f => [f.id, f])), [fields]);
  // Missing roles never imply background. Resolve catalog schema references before checking enums.
  const roleFieldIds = useMemo(() => fields.filter(field => {
    const allowed = resolveSchema(field.schema, schemas)?.enum;
    return field.id === 'traffic.actor.role' || Array.isArray(allowed) && allowed.includes(BG);
  }).map(field => field.id), [fields, schemas]);
  const genById = useMemo(() => {
    const m = new Map<string, Json>();
    const scan = (value: Json): void => {
      if (Array.isArray(value)) value.forEach(scan);
      else if (value && typeof value === 'object') {
        if (value.$ref?.id && value.$ref.generation !== undefined && !m.has(String(value.$ref.id))) m.set(String(value.$ref.id), value.$ref.generation);
        Object.values(value).forEach(scan);
      }
    };
    (scenario?.entities ?? []).forEach((e: any) => Object.values(e?.facts ?? {}).forEach(scan));
    return m;
  }, [scenario]);
  const setFact = (eid: string, key: string, value: Json) =>
    onChange({ ...scenario, entities: (scenario.entities ?? []).map((e: any) => e.id === eid ? { ...e, facts: { ...e.facts, [key]: value } } : e) });
  const groups = useMemo(() => {
    const m = new Map<string, any[]>(), query = q.trim().toLowerCase();
    (scenario?.entities ?? []).forEach((e: any) => {
      if (hideBg && roleFieldIds.some(id => e?.facts?.[id] === BG)) return;
      if (query && !String(e.id).toLowerCase().includes(query) && !String(e.type ?? '').toLowerCase().includes(query)) return;
      const root = String(e.type ?? '');
      const arr = m.get(root) ?? []; arr.push(e); m.set(root, arr);
    });
    return [...m.entries()];
  }, [scenario, q, hideBg, roleFieldIds]);
  return <div>
    <p>{t('Select an entity ID to inspect its initial fields. These are starting values, not live simulation results.')}</p>
    <div>
      <input aria-label={t('Filter entities by ID or type')} placeholder={t('Filter by ID or type…')} value={q} onChange={e => setQ(e.target.value)} />
      <label title={t('Only entities with an explicit background role are hidden. Entities without a role remain visible.')}><input type="checkbox" checked={hideBg} onChange={e => setHideBg(e.target.checked)} /> {t('Hide background actors')}</label>
    </div>
    <p>{t('Generation distinguishes reused entity IDs. It is shown only when an authored reference declares it; a dash means unknown.')}</p>
    {groups.map(([root, entities]) => <table key={root} style={{ borderCollapse: 'collapse', marginBottom: 12 }}><caption style={{ textAlign: 'left' }}>{root || t('Type not declared')} ({entities.length})</caption>
      <thead><tr><th>{t('ID')}</th><th>{t('Type')}</th><th>{t('Generation')}</th><th>{t('Initial fields')}</th></tr></thead>
      <tbody>{entities.map((entity: any) => {
        const facts = entity.facts ?? {}, expanded = open === entity.id;
        return <React.Fragment key={entity.id}>
          <tr>
            <td><button type="button" aria-expanded={expanded} aria-label={expanded ? t('Hide initial fields for {entity}', { entity: entity.id }) : t('Show initial fields for {entity}', { entity: entity.id })} onClick={() => setOpen(expanded ? null : entity.id)}>{entity.id}</button></td><td>{entity.type}</td>
            <td>{genById.has(String(entity.id)) ? String(genById.get(String(entity.id))) : '—'}</td><td>{Object.keys(facts).length}</td>
          </tr>
          {expanded && <tr><td colSpan={4}>
            {Object.keys(facts).map(key => {
              const field = fieldById.get(key);
              return <div key={key} style={{ borderTop: '1px solid #ddd' }}>
                <code>{key}</code>{field ? <em> ({field.type ?? t('Unknown type')}{field.metadata?.role ? t(', role: {role}', { role: field.metadata.role }) : ''})</em> : <em> ({t('unknown key')})</em>}
                <ValueEditor label={t('Field {field} for entity {entity}', { field: key, entity: entity.id })} value={facts[key]} schema={field?.schema} schemas={schemas} onWrite={value => setFact(entity.id, key, value)} />
              </div>;
            })}
            {!Object.keys(facts).length && <p>{t('This entity has no initial fields.')}</p>}
          </td></tr>}
        </React.Fragment>;
      })}</tbody>
    </table>)}
    {groups.length === 0 && <p>{(scenario?.entities ?? []).length ? t('No entities match. Clear the filter or show background actors.') : t('No entities have been added to this scenario.')}</p>}
  </div>;
}
export default TrafficEntityTable;
