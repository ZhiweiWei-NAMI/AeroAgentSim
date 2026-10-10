import { createContext, useContext, useEffect, useId, useRef, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { mapping } from './model';

/** Invalid local buffers must block authoring even though the last valid model is retained. */
export const AuthoringValidity = createContext<(id: string, error: string) => void>(() => {});
const JSON_NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;

function BufferedNumber({ label, value, onChange, integer = false }: {
  label: string; value: number | string; onChange: (text: string | undefined) => void; integer?: boolean;
}) {
  const { t } = useI18n();
  const [text, setText] = useState(String(value));
  const sent = useRef(value);
  const report = useContext(AuthoringValidity);
  const id = useId();
  const invalid = text !== '' && (integer ? !/^-?(0|[1-9]\d*)$/.test(text) || text === '-0' : !JSON_NUMBER.test(text) || !Number.isFinite(Number(text)));
  useEffect(() => { report(id, invalid ? 'number' : ''); return () => report(id, ''); }, [report, id, invalid]);
  useEffect(() => { if (value !== sent.current) { setText(String(value)); sent.current = value; } }, [value]);
  return <label>{label}<input aria-label={label} aria-invalid={invalid || undefined} inputMode={integer ? 'numeric' : 'decimal'} value={text} onChange={event => {
    const next = event.target.value;
    setText(next);
    if (next === '') { onChange(undefined); return; }
    const valid = integer ? /^-?(0|[1-9]\d*)$/.test(next) && next !== '-0' : JSON_NUMBER.test(next) && Number.isFinite(Number(next));
    if (valid) { sent.current = typeof value === 'number' ? Number(next) : next; onChange(next); }
  }}/>{invalid && <span role="alert">{integer ? t('Enter an exact integer') : t('Enter a finite number using JSON syntax')}</span>}</label>;
}

/** Edit actual literals/references/records without replacing unsupported extension keys. */
export function ExpressionControl({ value, onChange, label }: { value: unknown; onChange: (value: unknown) => void; label: string }) {
  const { t } = useI18n();
  const [newKey, setNewKey] = useState('');
  const itemKeys = useRef<{ value: unknown; keys: number[]; sequence: number }>({ value: undefined, keys: [], sequence: 0 });
  if (Array.isArray(value) && itemKeys.current.value !== value) {
    const first = itemKeys.current.sequence;
    itemKeys.current = { value, keys: value.map((_, index) => first + index), sequence: first + value.length };
  }
  const changeArray = (next: unknown[], keys = itemKeys.current.keys) => {
    itemKeys.current = { ...itemKeys.current, value: next, keys };
    onChange(next);
  };
  const kind = value === undefined ? 'unset' : value === null ? 'null' : Array.isArray(value) ? 'array' : mapping(value) ? 'record' : typeof value;
  const typePicker = <label>{value === undefined || value === null ? label : t('Value type')}<select aria-label={value === undefined || value === null ? label : t('{label} value type', { label })} value={kind} onChange={event => {
    const selected = event.target.value;
    onChange(selected === 'unset' ? undefined : selected === 'null' ? null : selected === 'boolean' ? true : selected === 'number' ? 0 : selected === 'array' ? [] : selected === 'record' ? {} : '');
  }}>
    <option value="unset">{t('Unset')}</option><option value="null">{t('Explicit null')}</option><option value="string">{t('String')}</option><option value="number">{t('Number')}</option><option value="boolean">{t('Boolean')}</option><option value="array">{t('Array')}</option><option value="record">{t('Record')}</option>
  </select></label>;
  if (value === undefined || value === null) return typePicker;
  if (typeof value === 'boolean') return <div>{typePicker}<label>{label}<select aria-label={label} value={String(value)} onChange={event => onChange(event.target.value === 'true')}><option value="true">{t('true')}</option><option value="false">{t('false')}</option></select></label></div>;
  if (typeof value === 'number') return <div>{typePicker}<BufferedNumber label={label} value={value} onChange={text => onChange(text === undefined ? undefined : Number(text))}/></div>;
  if (typeof value === 'string') return <div>{typePicker}<label>{label}<input aria-label={label} value={value} onChange={event => onChange(event.target.value)}/></label></div>;
  if (Array.isArray(value)) return <fieldset><legend>{label}</legend>{typePicker}
    {!value.length && <p>{t('No items yet. Add an item to begin.')}</p>}
    {value.map((row, index) => <div key={itemKeys.current.keys[index]}><ExpressionControl value={row} onChange={next => changeArray(value.map((item, i) => index === i ? next : item))} label={`${label}[${index}]`}/><button type="button" aria-label={t('{label} remove item {index}', { label, index })} onClick={() => changeArray(value.filter((_, i) => i !== index), itemKeys.current.keys.filter((_, i) => i !== index))}>{t('Remove item')}</button></div>)}
    <button type="button" aria-label={t('{label} add item', { label })} onClick={() => { const key = itemKeys.current.sequence++; changeArray([...value, ''], [...itemKeys.current.keys, key]); }}>{t('Add item')}</button>
  </fieldset>;
  if (mapping(value)) return <fieldset><legend>{label}</legend>{typePicker}
    {Object.entries(value).map(([key, row]) => <div key={key}>{key === '$integer' || key === '$number' ? <BufferedNumber label={key === '$integer' ? t('{label} exact integer', { label }) : t('{label} number', { label })} value={String(row)} integer={key === '$integer'} onChange={text => onChange(text === undefined ? undefined : { ...value, [key]: text })}/> : <ExpressionControl value={row} onChange={next => onChange({ ...value, [key]: next })} label={`${label}.${key}`}/>}<button type="button" aria-label={t('{label} remove key {key}', { label, key })} onClick={() => { const next = { ...value }; delete next[key]; onChange(next); }}>{t('Remove key')}</button></div>)}
    <label>{t('New key')}<input aria-label={t('{label} new key', { label })} value={newKey} onChange={event => setNewKey(event.target.value)}/></label><button type="button" disabled={!newKey || newKey in value} onClick={() => { onChange({ ...value, [newKey]: '' }); setNewKey(''); }}>{t('Add key')}</button>
  </fieldset>;
  return <p>{t('Unsupported {label} remains in raw YAML.', { label })}</p>;
}
