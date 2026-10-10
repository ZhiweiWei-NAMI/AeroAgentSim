import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react';
import { Details } from '../console/Details';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { parseLosslessJson } from '../feeds/lossless-json';
import { mapping, type Draft } from './model';
interface EntityChoice { label: string; value: unknown; typeId: string; ancestors: string[] }
interface Props { entityChoices?: EntityChoice[]; schema: unknown; value: unknown; onChange: (value: unknown) => void; definitions?: Draft; path?: string; onValidityChange?: (valid: boolean) => void }
const PayloadValidity = createContext<(id: string, error: string) => void>(() => {});
const JSON_NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;
function scalarKey(value: unknown): string {
  const numeric = mapping(value) ? value.$integer ?? value.$number : value;
  if (typeof numeric === 'number') return `number:${numeric}`;
  if (mapping(value) && typeof numeric === 'string') {
    if (/^-?\d+$/.test(numeric)) return `number:${BigInt(numeric)}`;
    if (JSON_NUMBER.test(numeric) && Number.isFinite(Number(numeric))) return `number:${Number(numeric)}`;
  }
  return JSON.stringify(value) ?? 'unset';
}

export function TypedPayloadForm(props: Props) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const report = useCallback((id: string, error: string) => setErrors(previous => {
    if (previous[id] === error || (!error && !(id in previous))) return previous;
    const next = { ...previous };
    if (error) next[id] = error; else delete next[id];
    return next;
  }), []);
  const valid = Object.keys(errors).length === 0;
  useEffect(() => props.onValidityChange?.(valid), [valid, props.onValidityChange]);
  return <PayloadValidity.Provider value={report}><PayloadFields {...props}/></PayloadValidity.Provider>;
}

function PayloadFields({ schema, value, onChange, definitions = {}, path = 'Payload', entityChoices }: Props) {
  const { t, locale } = useI18n();
  const descriptor = typeof schema === 'string' ? definitions[schema] : mapping(schema) && typeof schema.schema_ref === 'string' ? definitions[schema.schema_ref] : schema;
  const schemaKey = JSON.stringify(descriptor);
  const numeric = mapping(value) ? value.$integer ?? value.$number : value;
  const shown = typeof numeric === 'number' || typeof numeric === 'string' ? String(numeric) : '';
  const [raw, setRaw] = useState(() => JSON.stringify(value, null, 2) ?? '');
  const [text, setText] = useState(shown), [error, setError] = useState('');
  const sent = useRef({ value, schemaKey, path });
  const report = useContext(PayloadValidity);
  const id = useId();
  useEffect(() => { report(id, error); return () => report(id, ''); }, [report, id, error]);
  useEffect(() => {
    if (value !== sent.current.value || schemaKey !== sent.current.schemaKey || path !== sent.current.path) {
      setRaw(JSON.stringify(value, null, 2) ?? ''); setText(shown); setError('');
      sent.current = { value, schemaKey, path };
    }
  }, [value, schemaKey, path, shown]);
  const commit = (next: unknown) => { onChange(next); sent.current = { value: next, schemaKey, path }; setError(''); };
  const displayPath = path === 'Payload' ? t('Payload') : path.startsWith('Payload.') ? `${t('Payload')}${path.slice('Payload'.length)}` : path;
  const title = localized(descriptor, 'title', locale) ?? localized(descriptor, 'label', locale);
  const description = localized(descriptor, 'description', locale);
  const label = typeof title === 'string' ? title : path === 'Payload' ? t('Payload') : path.split('.').at(-1)?.replace(/_/g, ' ').replace(/^./, letter => letter.toUpperCase());
  const help = typeof description === 'string' && description ? <small>{description}</small> : null;
  const errorMessage = error === 'integer' ? t('Enter an exact integer') : error === 'number' ? t('Enter a finite number using JSON syntax') : error === 'record' ? t('Record payload must be an object') : error ? t('Invalid JSON: {error}', { error }) : '';
  const jsonChange = (source: string, record = false) => {
    setRaw(source);
    try {
      const next = parseLosslessJson(source, true);
      if (record && !mapping(next)) { setError('record'); return; }
      commit(next);
    } catch (problem) { setError(String(problem)); }
  };
  if (mapping(descriptor) && descriptor.type === 'record' && mapping(descriptor.members)) {
    const row = mapping(value) ? value : {};
    return <fieldset><legend>{typeof title === 'string' ? title : path === 'Payload' ? t('Event details') : label}</legend>{help}{Object.entries(descriptor.members).map(([key, child]) => <PayloadFields key={key} schema={child} value={row[key]} onChange={next => onChange({ ...row, [key]: next })} definitions={definitions} entityChoices={entityChoices} path={`${path}.${key}`}/>)}
      <Details title={t('Complete payload / extra members')} buttonLabel={t('Extra payload fields')}><textarea aria-label={t('{path} JSON', { path: displayPath })} aria-invalid={!!error || undefined} value={raw} onChange={event => jsonChange(event.target.value, true)}/>{error && <p role="alert">{errorMessage}</p>}</Details>
    </fieldset>;
  }
  if (mapping(descriptor) && descriptor.type === 'ref' && entityChoices) {
    const compatible = entityChoices.filter(choice => choice.typeId === descriptor.target_type || choice.ancestors.includes(String(descriptor.target_type)));
    const reference = mapping(value) && mapping(value.$ref) ? value.$ref : undefined;
    return <label>{label}<select aria-label={displayPath} value={reference ? `${reference.id}:${mapping(reference.generation) ? reference.generation.$integer : reference.generation}` : ''} onChange={event => commit(compatible.find(choice => mapping(choice.value) && mapping(choice.value.$ref) && `${choice.value.$ref.id}:${mapping(choice.value.$ref.generation) ? choice.value.$ref.generation.$integer : choice.value.$ref.generation}` === event.target.value)?.value)}>
      <option value="">{t('Choose an entity')}</option>{compatible.map(choice => { const ref = mapping(choice.value) && mapping(choice.value.$ref) ? choice.value.$ref : undefined; return ref ? <option key={`${ref.id}:${mapping(ref.generation) ? ref.generation.$integer : ref.generation}`} value={`${ref.id}:${mapping(ref.generation) ? ref.generation.$integer : ref.generation}`}>{choice.label}</option> : null; })}
    </select>{help}</label>;
  }
  if (mapping(descriptor) && ['string', 'boolean', 'number', 'integer'].includes(String(descriptor.type))) {
    const kind = String(descriptor.type);
    const enumValues = Array.isArray(descriptor.enum) ? descriptor.enum : undefined;
    if (enumValues) {
      const selected = enumValues.findIndex(item => scalarKey(item) === scalarKey(value));
      const hasCurrent = value !== undefined && selected < 0;
      return <label>{label}<select aria-label={displayPath} value={value === undefined ? '' : selected >= 0 ? String(selected) : 'current'} onChange={event => {
        if (event.target.value === 'current') return;
        const next = event.target.value === '' ? undefined : enumValues[Number(event.target.value)];
        commit(typeof next === 'number' && kind === 'integer' ? { $integer: String(next) } : typeof next === 'number' && kind === 'number' ? { $number: String(next) } : next);
      }}><option value="">{t('Unset')}</option>{hasCurrent && <option value="current">{value === null ? t('Explicit null') : t('Current value: {value}', { value: JSON.stringify(value) })}</option>}{enumValues.map((item, index) => <option key={index} value={String(index)}>{item === true ? t('true') : item === false ? t('false') : item === null ? t('Explicit null') : String(item)}</option>)}</select>{help}</label>;
    }
    const booleanValue = value === undefined ? '' : typeof value === 'boolean' ? String(value) : 'current';
    return <div><label>{label}{kind === 'boolean' ? <select aria-label={displayPath} value={booleanValue} onChange={event => { if (event.target.value !== 'current') commit(event.target.value === '' ? undefined : event.target.value === 'true'); }}><option value="">{t('Unset')}</option>{booleanValue === 'current' && <option value="current">{value === null ? t('Explicit null') : t('Current value: {value}', { value: JSON.stringify(value) })}</option>}<option value="true">{t('true')}</option><option value="false">{t('false')}</option></select> : <input aria-label={displayPath} aria-invalid={!!error || undefined} inputMode={kind === 'integer' ? 'numeric' : kind === 'number' ? 'decimal' : undefined} value={text} onChange={event => {
      const next = event.target.value; setText(next);
      if (kind === 'string') { commit(next); return; }
      if (!next) { commit(undefined); return; }
      if (kind === 'integer' && /^-?(0|[1-9]\d*)$/.test(next) && next !== '-0') commit({ $integer: next });
      else if (kind === 'number' && JSON_NUMBER.test(next) && Number.isFinite(Number(next))) commit({ $number: next });
      else setError(kind);
    }}/>} {help}{error && <span role="alert">{errorMessage}</span>}</label>
      {value === null && kind !== 'boolean' && <small>{t('Current value is explicit null; edit to replace it.')}</small>}
      {kind !== 'boolean' && <button type="button" aria-label={t('{path} clear value', { path: displayPath })} onClick={() => { setText(''); commit(undefined); }}>{t('Clear value')}</button>}
      {(descriptor.nullable === true || value === null) && <button type="button" aria-label={t('{path} set null', { path: displayPath })} onClick={() => { setText(''); commit(null); }}>{t('Explicit null')}</button>}
    </div>;
  }
  const schemaLabels: Record<string, string> = { ref: t('Entity reference'), record: t('Record'), array: t('Array'), string: t('String'), boolean: t('Boolean'), number: t('Number'), integer: t('Integer') };
  const schemaLabel = mapping(descriptor) ? schemaLabels[String(descriptor.type)] ?? String(descriptor.type) : t('unsupported schema');
  return <Details title={t('{path} JSON', { path: displayPath })} buttonLabel={t('{name} details', { name: label })}><label>{t('{path} · {schema} JSON', { path: displayPath, schema: schemaLabel })}<textarea aria-label={t('{path} JSON', { path: displayPath })} aria-invalid={!!error || undefined} rows={3} value={raw} onChange={event => jsonChange(event.target.value)}/>{help}{error && <span role="alert">{errorMessage}</span>}</label></Details>;
}
