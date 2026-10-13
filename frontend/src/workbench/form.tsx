import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { parseAuthoringJson, stringifyLossless } from '../feeds/lossless-json';

export const FormValidity = createContext<(path: string, valid: boolean) => void>(() => undefined);
function useFieldValidity(path: string, valid: boolean) {
  const report = useContext(FormValidity);
  useEffect(() => { report(path, valid); return () => report(path, true); }, [report, path, valid]);
}

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
const escape = (part: string) => part.replace(/~/g, '~0').replace(/\//g, '~1');
const unescape = (part: string) => part.replace(/~1/g, '/').replace(/~0/g, '~');
export const pointer = (...parts: Array<string | number>) => parts.map(part => '/' + escape(String(part))).join('');
/** Extend an existing JSON pointer with unescaped parts. */
export const under = (base: string, ...parts: Array<string | number>) => base + pointer(...parts);
export function getAt(root: unknown, path: string): unknown {
  if (!path) return root;
  return path.split('/').slice(1).map(unescape).reduce<unknown>((node, key) => node == null ? undefined : (node as Record<string, unknown>)[key], root);
}
/** Immutable set: returns a new root with only the path's ancestors copied. */
export function setAt<T>(root: T, path: string, value: unknown): T {
  const parts = path.split('/').slice(1).map(unescape);
  const write = (node: unknown, index: number): unknown => {
    if (index === parts.length) return value;
    const key = parts[index];
    if (Array.isArray(node)) { const copy = node.slice(); copy[Number(key)] = write(node[Number(key)], index + 1); return copy; }
    const record = (node && typeof node === 'object' ? node : {}) as Record<string, unknown>;
    return { ...record, [key]: write(record[key], index + 1) };
  };
  return write(root, 0) as T;
}

/** Lossless numeric tags from the authoring wire format. */
type Tagged = { $number: string } | { $integer: string };
const isTagged = (value: unknown): value is Tagged => !!value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 1 && ('$number' in value || '$integer' in value);
const tagText = (value: Tagged) => '$number' in value ? value.$number : value.$integer;
const scalar = (value: unknown) => value === null || ['string', 'number', 'boolean'].includes(typeof value) || isTagged(value);

/** Human unit hint from authored key names. */
export function unitHint(key: string): string | undefined {
  if (/rate_per_s/.test(key)) return '1/s';
  if (/_ns$/.test(key)) return 'ns';
  if (/_mps$|_m_s$/.test(key)) return 'm/s';
  if (/_m_s2$/.test(key)) return 'm/s²';
  if (/_s$|_seconds?$/.test(key)) return 's';
  if (/_m$|_meters?$|radius_m|spacing_m/.test(key)) return 'm';
  if (/_w$|_power_w/.test(key)) return 'W';
  if (/_j$/.test(key)) return 'J';
  if (/_kg$/.test(key)) return 'kg';
  if (/percent/.test(key)) return '%';
  return undefined;
}
const nsSeconds = (value: unknown) => { const n = typeof value === 'number' ? value : isTagged(value) ? Number(tagText(value)) : NaN; return Number.isFinite(n) ? `${(n / 1e9).toLocaleString(undefined, { maximumFractionDigits: 3 })} s` : undefined; };

/** One editable leaf; keeps its numeric kind and lossless tags. */
export function Leaf({ path, label, value, onChange, focus, readOnly, options }: { path: string; label: string; value: unknown; onChange: (path: string, value: unknown) => void; focus?: string; readOnly?: boolean; options?: string[] }) {
  const id = useId(), { locale } = useI18n();
  const unit = unitHint(label);
  const [text, setText] = useState(() => isTagged(value) ? tagText(value) : value == null ? '' : String(value));
  const row = useRef<HTMLDivElement>(null);
  const focused = focus !== undefined && focus !== '' && (focus === path || focus.startsWith(path + '/'));
  useEffect(() => { if (focused) row.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }); }, [focused]);
  // Numeric and tagged values parse on blur/Enter only: while typing, the raw text (including '-' or a
  // trailing '.') stays in the field, is never coerced to 0 and never reverts mid-edit (T4-02/T1-019).
  const numeric = typeof value === 'number' || isTagged(value);
  const [bad, setBadState] = useState(false);
  const setBad = (next: boolean) => setBadState(current => current === next ? current : next);
  useFieldValidity(path, !bad);
  // Reset only when the committed value itself changes; parents often hand a fresh array/object with
  // equal contents, and resetting on every render would clear an error the user is correcting.
  const committed = useRef(value);
  useEffect(() => {
    if (committed.current === value) return;
    committed.current = value;
    setText(isTagged(value) ? tagText(value) : value == null ? '' : String(value));
    setBad(false);
  }, [value]);
  const commit = (raw: string) => {
    if (numeric) {
      const integer = isTagged(value) && '$integer' in value;
      if (!(integer ? /^-?(?:0|[1-9]\d*)$/ : /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/).test(raw.trim()) || (!integer && !Number.isFinite(Number(raw)))) { setBad(true); return; }
      setBad(false);
      const trimmed = raw.trim();
      if (isTagged(value)) onChange(path, '$number' in value ? { $number: trimmed } : { $integer: trimmed });
      else { const n = Number(trimmed); onChange(path, Number.isSafeInteger(n) || !Number.isInteger(n) ? n : /^-?\d+$/.test(trimmed) ? { $integer: trimmed } : { $number: trimmed }); }
    } else if (value === null) onChange(path, raw === '' ? null : raw);
    else onChange(path, raw);
  };
  return <div className={`wb-field${focused ? ' is-focus' : ''}`} ref={row} data-path={path}>
    <label htmlFor={id}>{label}{unit && <small>{unit}{unit === 'ns' && nsSeconds(value) ? ` · ${nsSeconds(value)}` : ''}</small>}</label>
    {typeof value === 'boolean'
      ? <select id={id} value={String(value)} disabled={readOnly} onChange={event => onChange(path, event.target.value === 'true')}><option value="true">true</option><option value="false">false</option></select>
      : options ? <select id={id} value={String(value)} disabled={readOnly} onChange={event => onChange(path, event.target.value)}>{[...new Set([String(value), ...options])].map(option => <option key={option}>{option}</option>)}</select>
      : <input id={id} value={text} readOnly={readOnly} aria-invalid={bad} aria-describedby={bad ? `${id}-error` : undefined} style={bad ? { borderColor: 'var(--wb-bad)' } : undefined} inputMode={numeric ? 'decimal' : undefined}
          onChange={event => { setText(event.target.value); if (numeric) setBad(false); else commit(event.target.value); }}
          onBlur={event => { if (numeric) commit(event.target.value); }}
          onKeyDown={event => { if (event.key === 'Enter') { const target = event.target as HTMLInputElement; if (numeric) commit(target.value); target.blur(); } }} />}
    {bad && <span id={`${id}-error`} className="wb-field-error" role="alert">{locale === 'zh-CN' ? '请输入有效数值；整数不能含小数。' : 'Enter a valid number; integers cannot contain decimals.'}</span>}
  </div>;
}

function VectorField({ value, path, label, onChange, focus }: { value: number[]; path: string; label?: string; onChange: (path: string, value: unknown) => void; focus?: string }) {
  const id = useId(), { locale } = useI18n(), row = useRef<HTMLDivElement>(null);
  const [text, setText] = useState(value.join(', ')), [bad, setBad] = useState(false);
  const focused = focus === path || focus?.startsWith(path + '/');
  useFieldValidity(path, !bad);
  // Same committed-value guard as Leaf: equal contents in a new array must not reset the field.
  const committed = useRef(value);
  useEffect(() => {
    if (committed.current === value) return;
    committed.current = value;
    setText(value.join(', '));
    setBad(false);
  }, [value]);
  useEffect(() => { if (focused) row.current?.scrollIntoView({ block: 'center' }); }, [focused]);
  // Like Leaf: the raw text survives typing ('-' and trailing separators included); the vector is
  // parsed and written only on blur/Enter, and an invalid value shows a field error (T4-02/T1-019).
  const parse = (raw: string) => {
    const tokens = raw.split(',').map(part => part.trim()), parts = tokens.map(Number);
    return parts.length === value.length && tokens.every(token => /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(token)) && parts.every(Number.isFinite)
      ? parts : undefined;
  };
  const commit = (raw: string) => {
    const parts = parse(raw);
    if (!parts) { setBad(true); return; }
    setBad(false); onChange(path, parts);
  };
  return <div ref={row} className={`wb-field${focused ? ' is-focus' : ''}`} data-path={path}>
    <label htmlFor={id}>{label ?? path}<small>[{value.length}]</small></label>
    <input id={id} value={text} aria-invalid={bad} aria-describedby={bad ? `${id}-error` : undefined} inputMode="decimal"
      onChange={event => { setText(event.target.value); setBad(false); }}
      onBlur={event => commit(event.target.value)}
      onKeyDown={event => { if (event.key === 'Enter') { const target = event.target as HTMLInputElement; commit(target.value); target.blur(); } }} />
    {bad && <span id={`${id}-error`} className="wb-field-error" role="alert">{locale === 'zh-CN' ? `请输入 ${value.length} 个以逗号分隔的数值。` : `Enter ${value.length} comma-separated numbers.`}</span>}
  </div>;
}

/** A form generated from any JSON subtree: leaves become typed inputs, objects nest, arrays expand. */
function TreeBranch({ label, count, initiallyOpen, focused, children }: { label?: string; count?: number; initiallyOpen: boolean; focused: boolean; children: () => ReactNode }) {
  const [open, setOpen] = useState(initiallyOpen || focused), [visited, setVisited] = useState(initiallyOpen || focused);
  useEffect(() => { if (focused) { setOpen(true); setVisited(true); } }, [focused]);
  return <details open={open || focused} className="wb-tree" onToggle={event => { const expanded = event.currentTarget.open; setOpen(expanded); if (expanded) setVisited(true); }}>
    <summary><b>{label}</b>{count !== undefined && <> <span className="wb-tag">{count}</span></>}</summary>
    {(visited || focused) && <div style={{ paddingLeft: 12 }}>{children()}</div>}
  </details>;
}

export function TreeForm({ value, path, onChange, focus, depth = 0, label }: { value: unknown; path: string; onChange: (path: string, value: unknown) => void; focus?: string; depth?: number; label?: string }) {
  const focused = focus === path || !!focus?.startsWith(path + '/');
  if (scalar(value)) return <Leaf path={path} label={label ?? path} value={value} onChange={onChange} focus={focus} />;
  if (Array.isArray(value)) {
    if (!value.length) return <div className="wb-field" data-path={path}><label>{label}</label><span className="hint">[ ]</span></div>;
    if (value.every(item => typeof item === 'number') && value.length <= 4) {
      return <VectorField value={value} path={path} label={label} onChange={onChange} focus={focus} />;
    }
    return <TreeBranch label={label} count={value.length} initiallyOpen={depth < 1} focused={focused}>{() => value.map((item, index) => {
        const name = item && typeof item === 'object' && !Array.isArray(item) ? String((item as Record<string, unknown>).id ?? (item as Record<string, unknown>).name ?? index) : String(index);
        return <TreeForm key={index} value={item} path={`${path}/${index}`} label={name} onChange={onChange} focus={focus} depth={depth + 1} />;
      })}</TreeBranch>;
  }
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    const body = () => entries.map(([key, child]) => <TreeForm key={key} value={child} path={`${path}/${escape(key)}`} label={key} onChange={onChange} focus={focus} depth={depth + 1} />);
    if (depth === 0) return <>{body()}</>;
    return <TreeBranch label={label} initiallyOpen={depth < 2} focused={focused}>{body}</TreeBranch>;
  }
  return null;
}

/** Edit a subtree as JSON text; only valid JSON is applied. */
export function JsonEditor({ value, onApply, label, path, focus }: { value: unknown; onApply: (value: Json) => void; label: string; path?: string; focus?: string }) {
  const { locale } = useI18n();
  const root = useRef<HTMLDivElement>(null), focused = !!path && (focus === path || !!focus?.startsWith(path + '/'));
  const format = (source: unknown) => JSON.stringify(source, null, 2).replace(/\{\s*"\$(number|integer)": "([^"\\]+)"\s*\}/g, (_, kind, token) => stringifyLossless({ [`$${kind}`]: token }));
  const [text, setText] = useState(() => format(value));
  const [error, setError] = useState<string>();
  useEffect(() => { setText(format(value)); setError(undefined); }, [value]);
  useEffect(() => { if (focused) { root.current?.scrollIntoView({ block: 'center' }); root.current?.querySelector('textarea')?.focus(); } }, [focused]);
  return <div ref={root} data-path={path} className={focused ? 'wb-json-focus' : undefined}>
    <textarea aria-label={label} className={`wb-json${error ? ' is-bad' : ''}`} value={text} spellCheck={false} onChange={event => setText(event.target.value)} />
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6 }}>
      <button className="wb-btn small" onClick={() => { try { const document = parseAuthoringJson(`{${JSON.stringify(label)}:${text}}`) as Record<string, Json>; onApply(document[label]); setError(undefined); } catch (problem) { setError(String(problem)); } }}>{locale === 'zh-CN' ? '应用 JSON' : 'Apply JSON'}</button>
      {error && <span className="hint" style={{ color: 'var(--wb-bad)' }}>{error}</span>}
    </div>
  </div>;
}
