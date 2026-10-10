/** Structured authoring form for behaviour triggers: the compiler requires exactly one explicit category. */
import { Details } from '../console/Details';
import { AuthoringValidity, ExpressionControl } from './ExpressionControl';
import { useContext, useEffect, useId, useRef, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { mapping, type Draft } from './model';
import { parseLosslessJson } from '../feeds/lossless-json';

export interface TriggerFormProps {
  value: unknown;
  onChange: (next: unknown) => void;
  label: string;
  predicates: string[];
  initial?: boolean;
  events?: string[];
  actions?: string[];
}

const CATEGORIES = ['instance', 'lifecycle', 'predicate', 'event', 'timer', 'receipt', 'deadline'] as const;
type Category = (typeof CATEGORIES)[number];
const CATEGORY_KEYS = new Set<string>(CATEGORIES);
const COMPANION_KEYS = new Set<string>(['edge', 'status', 'policy', 'correlation', 'result_matches', 'after_ns']);
/** Known keys each explicit category may keep; unknown extension keys always survive edits. */
const COMPATIBLE: Record<Category, string[]> = {
  instance: ['instance'], lifecycle: ['lifecycle'], predicate: ['predicate', 'edge'], event: ['event', 'correlation'],
  timer: ['timer', 'after_ns'], receipt: ['receipt', 'status', 'policy', 'result_matches'], deadline: ['deadline'],
};
const RECEIPT_STATUSES = ['submitted', 'accepted', 'executing', 'canceling', 'succeeded', 'failed', 'rejected', 'canceled'];
const EDGES = ['entered', 'exited', 'while'];
const asText = (value: unknown): string => typeof value === 'string' ? value : '';

/** Category switch keeps exactly one explicit category: incompatible known keys are removed, unknown keys survive. */
function switchCategory(row: Draft, next: Category, initial: boolean): Draft {
  const keep = new Set(COMPATIBLE[next]);
  if (!initial) keep.delete('after_ns');
  const rebuilt: Draft = {};
  for (const [key, val] of Object.entries(row)) if ((!CATEGORY_KEYS.has(key) && !COMPANION_KEYS.has(key)) || keep.has(key)) rebuilt[key] = val;
  if (next === 'receipt' && !('receipt' in rebuilt)) rebuilt.receipt = [];
  else if (!(next in rebuilt)) rebuilt[next] = '';
  return rebuilt;
}

/** Safe integers stay numbers, larger nanosecond values travel as $integer wire tags. */
function exactInteger(text: string): unknown {
  if (!/^-?(0|[1-9]\d*)$/.test(text)) return undefined;
  const value = BigInt(text);
  return value >= BigInt(Number.MIN_SAFE_INTEGER) && value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(text) : { $integer: text };
}

function Json({ label, value, onChange }: { label: string; value: unknown; onChange: (next: unknown) => void }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(() => JSON.stringify(value, null, 2) ?? ''), [error, setError] = useState('');
  const sent = useRef(value);
  const report = useContext(AuthoringValidity);
  const id = useId();
  useEffect(() => { report(id, error); return () => report(id, ''); }, [report, id, error]);
  useEffect(() => { if (value !== sent.current) { setDraft(JSON.stringify(value, null, 2) ?? ''); setError(''); sent.current = value; } }, [value]);
  return <Details title={label} buttonLabel={t('Details')}><label className="behaviour-json">{label}<textarea aria-label={label} aria-invalid={!!error || undefined} rows={6} value={draft} onChange={event => {
    setDraft(event.target.value);
    try { const next = parseLosslessJson(event.target.value, true); onChange(next); sent.current = next; setError(''); } catch (problem) { setError(String(problem)); }
  }}/>{error && <span role="alert">{t('Invalid JSON: {error}', { error })}</span>}</label></Details>;
}

function ExactNs({ label, value, onChange }: { label: string; value: unknown; onChange: (next: unknown) => void }) {
  const { t } = useI18n();
  const shown = typeof value === 'number' ? String(value) : mapping(value) && typeof value.$integer === 'string' ? value.$integer : asText(value);
  const [draft, setDraft] = useState(shown);
  const sent = useRef(value);
  const report = useContext(AuthoringValidity);
  const id = useId();
  const invalid = draft !== '' && exactInteger(draft) === undefined;
  useEffect(() => { report(id, invalid ? 'nanoseconds' : ''); return () => report(id, ''); }, [report, id, invalid]);
  useEffect(() => { if (value !== sent.current) { setDraft(shown); sent.current = value; } }, [value, shown]);
  return <label>{label}<input aria-label={label} aria-invalid={invalid || undefined} inputMode="numeric" value={draft} onChange={event => {
    setDraft(event.target.value);
    if (event.target.value === '') { onChange(undefined); sent.current = undefined; return; }
    const exact = exactInteger(event.target.value);
    if (exact !== undefined) { onChange(exact); sent.current = exact; }
  }}/>{invalid && <span role="alert">{t('Exact integer nanoseconds required')}</span>}</label>;
}

function KeyCell({ label, name, names, onRename }: { label: string; name: string; names: string[]; onRename: (next: string) => void }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(name);
  const sent = useRef(name);
  const report = useContext(AuthoringValidity);
  const id = useId();
  const invalid = draft !== name && (!draft || names.includes(draft));
  useEffect(() => { report(id, invalid ? 'key' : ''); return () => report(id, ''); }, [report, id, invalid]);
  useEffect(() => { if (name !== sent.current) { setDraft(name); sent.current = name; } }, [name]);
  return <><input aria-label={label} aria-invalid={invalid || undefined} value={draft} onChange={event => setDraft(event.target.value)} onBlur={() => { if (draft !== name && !invalid) { onRename(draft); sent.current = draft; } }} onKeyDown={event => { if (event.key === 'Enter') (event.target as HTMLInputElement).blur(); }}/>{invalid && <span role="alert">{t('Enter a unique, non-empty key')}</span>}</>;
}

function KeyValueTable({ label, value, onChange }: { label: string; value: unknown; onChange: (next: unknown) => void }) {
  const { t } = useI18n();
  const [newKey, setNewKey] = useState('');
  const source = value === undefined ? {} : value;
  if (!mapping(source)) return <div><p>{t('Correlation must be a mapping; unsupported content is retained below.')}</p><Json label={t('{label} JSON', { label })} value={value} onChange={onChange}/></div>;
  const entries = Object.entries(source);
  const rename = (from: string, to: string) => { if (!to || to === from || to in source) return; onChange(Object.fromEntries(entries.map(([key, val]) => [key === from ? to : key, val]))); };
  return <fieldset><legend>{label}</legend><table><tbody>
    {entries.map(([key, val], index) => <tr key={key}>
      <td><KeyCell label={t('{label} key {index}', { label, index })} name={key} names={Object.keys(source)} onRename={next => rename(key, next)}/></td>
      <td><ExpressionControl label={t('{label} value {index}', { label, index })} value={val} onChange={next => onChange(Object.fromEntries(entries.map(([k, v]) => [k, k === key ? next : v])))}/></td>
      <td><button type="button" aria-label={t('{label} remove {key}', { label, key })} onClick={() => onChange(Object.fromEntries(entries.filter(([k]) => k !== key)))}>{t('Remove')}</button></td>
    </tr>)}
  </tbody></table>
    <label>{t('New key')} <input aria-label={t('{label} new key', { label })} value={newKey} onChange={event => setNewKey(event.target.value)}/></label>
    <button type="button" disabled={!newKey || newKey in source} onClick={() => { onChange({ ...source, [newKey]: '' }); setNewKey(''); }}>{t('Add key')}</button>
  </fieldset>;
}

function ReceiptEditor({ row, set, label, actions }: { row: Draft; set: (key: string, next: unknown) => void; label: string; actions: string[] }) {
  const { t } = useI18n();
  const statusLabels: Record<string, string> = { submitted: t('submitted'), accepted: t('accepted'), executing: t('executing'), canceling: t('canceling'), succeeded: t('succeeded'), failed: t('failed'), rejected: t('rejected'), canceled: t('canceled') };
  const raw = row.receipt, rawStatus = row.status;
  const ids = typeof raw === 'string' && raw ? [raw] : Array.isArray(raw) ? raw.filter((id): id is string => typeof id === 'string' && !!id) : [];
  const statuses = typeof rawStatus === 'string' && rawStatus ? [rawStatus] : Array.isArray(rawStatus) ? rawStatus.filter((item): item is string => typeof item === 'string' && !!item) : [];
  const toggle = (key: 'receipt' | 'status', current: string[], wasText: boolean, id: string) => {
    const next = current.includes(id) ? current.filter(item => item !== id) : [...current, id];
    set(key, wasText && next.length <= 1 ? next[0] ?? '' : next);
  };
  return <fieldset><legend>{t('Receipt')}</legend>
    <p>{t('Satisfied when the referenced command actions report the selected statuses.')}</p>
    {[...new Set([...actions, ...ids])].map(id => <label key={id}><input type="checkbox" aria-label={t('{label} receipt {id}', { label, id })} checked={ids.includes(id)} onChange={() => toggle('receipt', ids, typeof raw === 'string', id)}/>{id}</label>)}
    {!actions.length && !ids.length && <p>{t('No command actions known yet; reference their IDs once transition actions exist.')}</p>}
    <label>{t('Policy')} <select aria-label={t('{label} policy', { label })} value={row.policy === 'all' || row.policy === 'any' ? row.policy : ''} onChange={event => set('policy', event.target.value)}><option value="">{t('Unset (compiler default)')}</option><option value="all">{t('all')}</option><option value="any">{t('any')}</option></select></label>
    {[...new Set([...RECEIPT_STATUSES, ...statuses])].map(status => <label key={status}><input type="checkbox" aria-label={t('{label} status {status}', { label, status: statusLabels[status] ?? status })} checked={statuses.includes(status)} onChange={() => toggle('status', statuses, typeof rawStatus === 'string', status)}/>{statusLabels[status] ?? status}</label>)}
  </fieldset>;
}

export function TriggerForm({ value, onChange, label, predicates, initial = false, events = [], actions = [] }: TriggerFormProps) {
  const { t } = useI18n();
  const categoryLabels: Record<Category, string> = { instance: t('Instance'), lifecycle: t('Lifecycle'), predicate: t('Predicate'), event: t('Event'), timer: t('Timer'), receipt: t('Receipt'), deadline: t('Deadline') };
  const edgeLabels: Record<string, string> = { entered: t('entered'), exited: t('exited'), while: t('while') };
  const row = mapping(value) ? value : {};
  const present = CATEGORIES.filter(cat => cat in row);
  const category = present.length === 1 ? present[0] : '';
  const unsupported = !mapping(value) || present.length !== 1;
  const unknownKeys = mapping(value) ? Object.keys(value).filter(key => !CATEGORY_KEYS.has(key) && !COMPANION_KEYS.has(key)) : [];
  const set = (key: string, next: unknown) => onChange({ ...row, [key]: next });
  const remove = (key: string) => { const next = { ...row }; delete next[key]; onChange(next); };
  const actionOptions = [...new Set([...actions, asText(row.timer), asText(row.deadline)])].filter(Boolean);
  return <fieldset className="trigger-form"><legend>{label}</legend>
    <label>{t('Category')} <select aria-label={t('{label} category', { label })} value={category} onChange={event => { const next = event.target.value; if (next) onChange(switchCategory(row, next as Category, initial)); }}>
      <option value="">{unsupported ? t('Ambiguous / unset') : t('Unset')}</option>
      {CATEGORIES.map(name => <option key={name} value={name}>{categoryLabels[name]}</option>)}
    </select></label>
    {unsupported && <p>{t('Advanced or ambiguous trigger content is retained verbatim; choose exactly one category, or edit the complete JSON. The server compiler checks support.')}</p>}
    {unknownKeys.length > 0 && <p>{t('Unknown keys {keys} are preserved on every edit; the server compiler rejects them without an explicit compiled adapter.', { keys: unknownKeys.join(', ') })}</p>}
    {category === 'instance' && <label>{t('Instance')} <select aria-label={t('{label} instance', { label })} value={asText(row.instance)} onChange={event => set('instance', event.target.value)}><option value="">{t('Unset')}</option><option value="activated">{t('activated')}</option><option value="continued">{t('continued')}</option></select></label>}
    {category === 'lifecycle' && <label>{t('Lifecycle')} <select aria-label={t('{label} lifecycle', { label })} value={asText(row.lifecycle)} onChange={event => set('lifecycle', event.target.value)}><option value="">{t('Unset')}</option><option value="created">{t('created')}</option><option value="removed">{t('removed')}</option></select></label>}
    {category === 'predicate' && <>
      <label>{t('Predicate')} <input aria-label={t('{label} predicate', { label })} list={`${label}-predicates`} value={asText(row.predicate)} onChange={event => set('predicate', event.target.value)}/><datalist id={`${label}-predicates`}>{predicates.map(id => <option key={id}>{id}</option>)}</datalist></label>
      <label>{t('Edge')} <select aria-label={t('{label} edge', { label })} value={asText(row.edge)} onChange={event => set('edge', event.target.value)}><option value="">{t('Unset')}</option>{EDGES.map(name => <option key={name} value={name}>{edgeLabels[name]}</option>)}</select></label>
      <small>{t('Entered fires when the condition becomes true; exited when it becomes false; while when it remains true.')}</small>
    </>}
    {category === 'event' && <>
      <label>{t('Event ID')} <input aria-label={t('{label} event', { label })} list={`${label}-events`} value={asText(row.event)} onChange={event => set('event', event.target.value)}/><datalist id={`${label}-events`}>{events.map(id => <option key={id}>{id}</option>)}</datalist></label>
      <KeyValueTable label={t('{label} correlation', { label })} value={row.correlation} onChange={next => set('correlation', next)}/>
      {mapping(row.correlation) && 'requires_event' in row.correlation && <p>{t('correlation.requires_event is rejected by the compiler; declare durable event prerequisites as explicit chain states.')}</p>}
    </>}
    {(category === 'timer' || category === 'deadline') && <datalist id={`${label}-actions`}>{actionOptions.map(id => <option key={id}>{id}</option>)}</datalist>}
    {category === 'timer' && <>
      <label>{t('Timer ID (delay action)')} <input aria-label={t('{label} timer', { label })} list={`${label}-actions`} value={asText(row.timer)} onChange={event => set('timer', event.target.value)}/></label>
      {initial ? <ExactNs label={t('{label} after_ns (exact integer nanoseconds)', { label })} value={row.after_ns} onChange={next => { if (next === undefined) remove('after_ns'); else set('after_ns', next); }}/> : 'after_ns' in row && <p>{t('after_ns only applies to initial template timer triggers; it is retained but will be rejected here.')}</p>}
      {initial && !('after_ns' in row) && <p>{t('Template timer triggers require an explicit after_ns delay.')}</p>}
    </>}
    {category === 'deadline' && <label>{t('Deadline command ID')} <input aria-label={t('{label} deadline', { label })} list={`${label}-actions`} value={asText(row.deadline)} onChange={event => set('deadline', event.target.value)}/></label>}
    {category === 'receipt' && <ReceiptEditor row={row} set={set} label={label} actions={actions}/>}
    {initial && (category === 'receipt' || category === 'deadline' || row.instance === 'continued') && <p>{t('Initial triggers cannot wait on receipts/deadlines or start from instance continuation; the server compiler will reject this.')}</p>}
    {unsupported ? <Json label={t('{label} trigger JSON', { label })} value={value} onChange={onChange}/> : <details><summary>{t('Complete trigger JSON')}</summary><Json label={t('{label} complete JSON', { label })} value={value} onChange={onChange}/></details>}
  </fieldset>;
}
