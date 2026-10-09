/** Structured authoring form for behaviour triggers: the compiler requires exactly one explicit category. */
import { ExpressionControl } from './ExpressionControl';
import { useEffect, useRef, useState } from 'react';
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
  instance: ['instance'],
  lifecycle: ['lifecycle'],
  predicate: ['predicate', 'edge'],
  event: ['event', 'correlation'],
  timer: ['timer', 'after_ns'],
  receipt: ['receipt', 'status', 'policy', 'result_matches'],
  deadline: ['deadline'],
};
const RECEIPT_STATUSES = ['submitted', 'accepted', 'executing', 'canceling', 'succeeded', 'failed', 'rejected', 'canceled'];
const EDGES = ['entered', 'exited', 'while'];
const asText = (value: unknown): string => typeof value === 'string' ? value : '';

/** Category switch keeps exactly one explicit category: incompatible known keys are removed, unknown keys survive. */
function switchCategory(row: Draft, next: Category, initial: boolean): Draft {
  const keep = new Set(COMPATIBLE[next]);
  if (!initial) keep.delete('after_ns'); // only template (initial) timer triggers may schedule an initial delay
  const rebuilt: Draft = {};
  for (const [key, val] of Object.entries(row)) if ((!CATEGORY_KEYS.has(key) && !COMPANION_KEYS.has(key)) || keep.has(key)) rebuilt[key] = val;
  if (next === 'receipt' && !('receipt' in rebuilt)) rebuilt.receipt = [];
  else if (!(next in rebuilt)) rebuilt[next] = '';
  return rebuilt;
}

/** Exact integer tokens: safe integers stay numbers, larger nanosecond values travel as $integer wire tags. */
function exactInteger(text: string): unknown {
  if (!/^-?(0|[1-9]\d*)$/.test(text)) return undefined;
  const value = BigInt(text);
  return value >= BigInt(Number.MIN_SAFE_INTEGER) && value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(text) : {$integer: text};
}

function Json({label, value, onChange}: {label: string; value: unknown; onChange: (next: unknown) => void}) {
  const [draft, setDraft] = useState(() => JSON.stringify(value, null, 2) ?? ''), [error, setError] = useState('');
  const sent = useRef(value);
  useEffect(() => { if (value !== sent.current) { setDraft(JSON.stringify(value, null, 2) ?? ''); setError(''); sent.current = value; } }, [value]);
  return <label className="behaviour-json">{label}<textarea aria-label={label} rows={6} value={draft} onChange={event => {
    setDraft(event.target.value);
    try { const next = parseLosslessJson(event.target.value, true); sent.current = next; onChange(next); setError(''); } catch (problem) { setError(String(problem)); }
  }}/>{error && <span role="alert">{error}</span>}</label>;
}

function ExactNs({label, value, onChange}: {label: string; value: unknown; onChange: (next: unknown) => void}) {
  const shown = typeof value === 'number' ? String(value) : mapping(value) && typeof value.$integer === 'string' ? value.$integer : asText(value);
  const [draft, setDraft] = useState(shown);
  const sent = useRef(value);
  useEffect(() => { if (value !== sent.current) { setDraft(shown); sent.current = value; } }, [value, shown]);
  return <label>{label}<input aria-label={label} inputMode="numeric" value={draft} onChange={event => {
    setDraft(event.target.value);
    if (event.target.value === '') { sent.current = undefined; onChange(undefined); return; }
    const exact = exactInteger(event.target.value);
    if (exact !== undefined) { sent.current = exact; onChange(exact); }
  }}/>{draft !== '' && exactInteger(draft) === undefined && <span role="alert">Exact integer nanoseconds required</span>}</label>;
}

function KeyCell({label, name, onRename}: {label: string; name: string; onRename: (next: string) => void}) {
  const [draft, setDraft] = useState(name);
  const sent = useRef(name);
  useEffect(() => { if (name !== sent.current) { setDraft(name); sent.current = name; } }, [name]);
  return <input aria-label={label} value={draft} onChange={event => setDraft(event.target.value)} onBlur={() => { if (draft !== name) { sent.current = draft; onRename(draft); } }} onKeyDown={event => { if (event.key === 'Enter') (event.target as HTMLInputElement).blur(); }}/>;
}

function CorrelationValue({label,value,onChange}:{label:string;value:unknown;onChange:(next:unknown)=>void}) {return <ExpressionControl label={label} value={value} onChange={onChange}/>;}

function KeyValueTable({label, value, onChange}: {label: string; value: unknown; onChange: (next: unknown) => void}) {
  const [newKey, setNewKey] = useState('');
  const source = value === undefined ? {} : value;
  if (!mapping(source)) return <div><p>Correlation must be a mapping; unsupported content is retained below.</p><Json label={`${label} JSON`} value={value} onChange={onChange}/></div>;
  const entries = Object.entries(source);
  const rename = (from: string, to: string) => { if (!to || to === from || to in source) return; onChange(Object.fromEntries(entries.map(([key, val]) => [key === from ? to : key, val]))); };
  return <fieldset><legend>{label}</legend><table><tbody>
    {entries.map(([key, val], index) => <tr key={index}>
      <td><KeyCell label={`${label} key ${index}`} name={key} onRename={next => rename(key, next)}/></td>
      <td><CorrelationValue label={`${label} value ${index}`} value={val} onChange={next => onChange(Object.fromEntries(entries.map(([k, v]) => [k, k === key ? next : v])))}/></td>
      <td><button aria-label={`${label} remove ${key}`} onClick={() => onChange(Object.fromEntries(entries.filter(([k]) => k !== key)))}>Remove</button></td>
    </tr>)}
  </tbody></table>
  <label>New key <input aria-label={`${label} new key`} value={newKey} onChange={event => setNewKey(event.target.value)}/></label>
  <button disabled={!newKey || newKey in source} onClick={() => {onChange({...source, [newKey]: ''}); setNewKey('');}}>Add key</button>
  </fieldset>;
}

function ReceiptEditor({row, set, label, actions}: {row: Draft; set: (key: string, next: unknown) => void; label: string; actions: string[]}) {
  const raw = row.receipt, rawStatus = row.status;
  const ids = typeof raw === 'string' && raw ? [raw] : Array.isArray(raw) ? raw.filter((id): id is string => typeof id === 'string' && !!id) : [];
  const statuses = typeof rawStatus === 'string' && rawStatus ? [rawStatus] : Array.isArray(rawStatus) ? rawStatus.filter((item): item is string => typeof item === 'string' && !!item) : [];
  const toggle = (key: 'receipt' | 'status', current: string[], wasText: boolean, id: string) => {
    const next = current.includes(id) ? current.filter(item => item !== id) : [...current, id];
    set(key, wasText && next.length <= 1 ? next[0] ?? '' : next);
  };
  return <fieldset><legend>Receipt</legend>
    <p>Satisfied when the referenced command actions report the selected statuses.</p>
    {[...new Set([...actions, ...ids])].map(id => <label key={id}><input type="checkbox" aria-label={`${label} receipt ${id}`} checked={ids.includes(id)} onChange={() => toggle('receipt', ids, typeof raw === 'string', id)}/>{id}</label>)}
    {!actions.length && !ids.length && <p>No command actions known yet; reference their IDs once transition actions exist.</p>}
    <label>Policy <select aria-label={`${label} policy`} value={row.policy === 'all' || row.policy === 'any' ? row.policy : ''} onChange={event => set('policy', event.target.value)}><option value="">Unset (compiler default)</option><option>all</option><option>any</option></select></label>
    {[...new Set([...RECEIPT_STATUSES, ...statuses])].map(status => <label key={status}><input type="checkbox" aria-label={`${label} status ${status}`} checked={statuses.includes(status)} onChange={() => toggle('status', statuses, typeof rawStatus === 'string', status)}/>{status}</label>)}
  </fieldset>;
}

export function TriggerForm({value, onChange, label, predicates, initial = false, events = [], actions = []}: TriggerFormProps) {
  const row = mapping(value) ? value : {};
  const present = CATEGORIES.filter(cat => cat in row);
  const category = present.length === 1 ? present[0] : '';
  const unsupported = !mapping(value) || present.length !== 1;
  const unknownKeys = mapping(value) ? Object.keys(value).filter(key => !CATEGORY_KEYS.has(key) && !COMPANION_KEYS.has(key)) : [];
  const set = (key: string, next: unknown) => onChange({...row, [key]: next});
  const remove = (key: string) => { const next = {...row}; delete next[key]; onChange(next); };
  const actionOptions = [...new Set([...actions, asText(row.timer), asText(row.deadline)])].filter(Boolean);
  return <fieldset className="trigger-form"><legend>{label}</legend>
    <label>Category <select aria-label={`${label} category`} value={category} onChange={event => { const next = event.target.value; if (next) onChange(switchCategory(row, next as Category, initial)); }}>
      <option value="">{unsupported ? 'Ambiguous / unset' : 'Unset'}</option>
      {CATEGORIES.map(name => <option key={name} value={name}>{name}</option>)}
    </select></label>
    {unsupported && <p>Advanced or ambiguous trigger content is retained verbatim; the server compiler requires exactly one explicit category and rejects unknown keys. Pick a category to rebuild it, or edit the complete JSON.</p>}
    {unknownKeys.length > 0 && <p>Unknown keys {unknownKeys.join(', ')} are preserved on every edit; the server compiler rejects them without an explicit compiled adapter.</p>}
    {category === 'instance' && <label>Instance <select aria-label={`${label} instance`} value={asText(row.instance)} onChange={event => set('instance', event.target.value)}><option value="">Unset</option>{['activated', 'continued'].map(name => <option key={name}>{name}</option>)}</select></label>}
    {category === 'lifecycle' && <label>Lifecycle <select aria-label={`${label} lifecycle`} value={asText(row.lifecycle)} onChange={event => set('lifecycle', event.target.value)}><option value="">Unset</option>{['created', 'removed'].map(name => <option key={name}>{name}</option>)}</select></label>}
    {category === 'predicate' && <>
      <label>Predicate <input aria-label={`${label} predicate`} list={`${label}-predicates`} value={asText(row.predicate)} onChange={event => set('predicate', event.target.value)}/><datalist id={`${label}-predicates`}>{predicates.map(id => <option key={id}>{id}</option>)}</datalist></label>
      <label>Edge <select aria-label={`${label} edge`} value={asText(row.edge)} onChange={event => set('edge', event.target.value)}><option value="">Unset</option>{EDGES.map(name => <option key={name}>{name}</option>)}</select></label>
    </>}
    {category === 'event' && <>
      <label>Event ID <input aria-label={`${label} event`} list={`${label}-events`} value={asText(row.event)} onChange={event => set('event', event.target.value)}/><datalist id={`${label}-events`}>{events.map(id => <option key={id}>{id}</option>)}</datalist></label>
      <KeyValueTable label={`${label} correlation`} value={row.correlation} onChange={next => set('correlation', next)}/>
      {mapping(row.correlation) && 'requires_event' in row.correlation && <p>correlation.requires_event is rejected by the compiler; declare durable event prerequisites as explicit chain states.</p>}
    </>}
    {(category === 'timer' || category === 'deadline') && <datalist id={`${label}-actions`}>{actionOptions.map(id => <option key={id}>{id}</option>)}</datalist>}
    {category === 'timer' && <>
      <label>Timer ID (delay action) <input aria-label={`${label} timer`} list={`${label}-actions`} value={asText(row.timer)} onChange={event => set('timer', event.target.value)}/></label>
      {initial ? <ExactNs label={`${label} after_ns (exact integer nanoseconds)`} value={row.after_ns} onChange={next => { if (next === undefined) remove('after_ns'); else set('after_ns', next); }}/> : 'after_ns' in row && <p>after_ns only applies to initial template timer triggers; it is retained but will be rejected here.</p>}
      {initial && !('after_ns' in row) && <p>Template timer triggers require an explicit after_ns delay.</p>}
    </>}
    {category === 'deadline' && <label>Deadline command ID <input aria-label={`${label} deadline`} list={`${label}-actions`} value={asText(row.deadline)} onChange={event => set('deadline', event.target.value)}/></label>}
    {category === 'receipt' && <ReceiptEditor row={row} set={set} label={label} actions={actions}/>}
    {initial && (category === 'receipt' || category === 'deadline' || row.instance === 'continued') && <p>Initial triggers cannot wait on receipts/deadlines or start from instance continuation; the server compiler will reject this.</p>}
    {unsupported ? <Json label={`${label} trigger JSON`} value={value} onChange={onChange}/> : <details><summary>Complete trigger JSON</summary><Json label={`${label} complete JSON`} value={value} onChange={onChange}/></details>}
  </fieldset>;
}
