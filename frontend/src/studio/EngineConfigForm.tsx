import { useEffect, useRef, useState } from 'react';

type Config = Record<string, unknown>;
interface Schema {
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, Schema>;
  required?: string[];
  enum?: Array<string | number | boolean>;
  minimum?: number;
  maximum?: number;
  additionalProperties?: boolean;
}
interface Props {
  schema?: unknown;
  value: Config;
  onChange: (config: Config) => void;
  onValidityChange?: (valid: boolean) => void;
}
const object = (value: unknown): value is Config => typeof value === 'object' && value !== null && !Array.isArray(value);
const own = (value: Config, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const supportedKeys = new Set(['type', 'title', 'description', 'properties', 'required', 'enum', 'minimum', 'maximum', 'additionalProperties']);

function supported(value: unknown, root = true): value is Schema {
  if (!object(value) || Object.keys(value).some(key => !supportedKeys.has(key))) return false;
  if (value.required !== undefined && (!Array.isArray(value.required) || value.required.some(key => typeof key !== 'string'))) return false;
  if (value.additionalProperties !== undefined && typeof value.additionalProperties !== 'boolean') return false;
  for (const key of ['minimum', 'maximum']) if (value[key] !== undefined && (typeof value[key] !== 'number' || !Number.isFinite(value[key]))) return false;
  if (value.enum !== undefined && (!Array.isArray(value.enum) || !value.enum.length || value.enum.some(item => !['string', 'number', 'boolean'].includes(typeof item)))) return false;
  if (value.type === 'object') return object(value.properties) && Object.values(value.properties).every(child => supported(child, false));
  return !root && ['string', 'number', 'integer', 'boolean'].includes(String(value.type));
}

function issues(schema: Schema, value: unknown, path = 'Config'): string[] {
  if (schema.type === 'object') {
    if (!object(value)) return [`${path}: expected object`];
    const errors = (schema.required ?? []).filter(key => !own(value, key)).map(key => `${path}.${key}: required`);
    if (schema.additionalProperties === false) for (const key of Object.keys(value)) if (!(key in schema.properties!)) errors.push(`${path}.${key}: unknown property`);
    for (const [key, child] of Object.entries(schema.properties!)) if (own(value, key)) errors.push(...issues(child, value[key], `${path}.${key}`));
    return errors;
  }
  const valid = schema.type === 'integer' ? typeof value === 'number' && Number.isSafeInteger(value) : typeof value === schema.type && (typeof value !== 'number' || Number.isFinite(value));
  if (!valid) return [`${path}: expected ${schema.type}`];
  if (schema.enum && !schema.enum.some(item => Object.is(item, value))) return [`${path}: value excluded by enum`];
  if (typeof value === 'number' && (schema.minimum !== undefined && value < schema.minimum || schema.maximum !== undefined && value > schema.maximum)) return [`${path}: outside numeric bounds`];
  return [];
}

function Fields({ schema, value, path, onChange }: { schema: Schema; value: Config; path: string; onChange: (value: Config) => void }) {
  const set = (key: string, child: unknown, remove = false) => {
    const next = remove ? { ...value } : { ...value, [key]: child };
    if (remove) delete next[key];
    onChange(next);
  };
  return <>{Object.entries(schema.properties!).map(([key, child]) => {
    const label = `${path}.${key}`, required = schema.required?.includes(key), current = value[key];
    return <div className="studio-field" key={key}>
      <label>{child.title ?? key}{required ? ' *' : ''}</label>
      {child.description && <small>{child.description}</small>}
      {child.type === 'object' ? <fieldset><legend>{child.title ?? key}</legend><Fields schema={child} value={object(current) ? current : {}} path={label} onChange={next => set(key, next)} /></fieldset>
        : child.enum ? <select aria-label={label} value={child.enum.findIndex(item => Object.is(item, current)) < 0 ? '' : String(child.enum.findIndex(item => Object.is(item, current)))} onChange={e => e.target.value === '' ? set(key, undefined, true) : set(key, child.enum![Number(e.target.value)])}>
          <option value="">Unset</option>{child.enum.map((item, index) => <option value={index} key={index}>{String(item)}</option>)}
        </select>
        : child.type === 'boolean' ? <select aria-label={label} value={typeof current === 'boolean' ? String(current) : ''} onChange={e => e.target.value === '' ? set(key, undefined, true) : set(key, e.target.value === 'true')}><option value="">Unset</option><option value="true">true</option><option value="false">false</option></select>
        : child.type === 'string' ? <input aria-label={label} value={typeof current === 'string' ? current : ''} onChange={e => set(key, e.target.value)} />
        : <input aria-label={label} type="number" step={child.type === 'integer' ? '1' : 'any'} min={child.minimum} max={child.maximum} value={typeof current === 'number' ? current : ''} onChange={e => e.target.value === '' ? set(key, undefined, true) : set(key, e.target.valueAsNumber)} />}
      {own(value, key) && <button type="button" aria-label={`Clear ${label}`} onClick={() => set(key, undefined, true)}>Clear</button>}
    </div>;
  })}</>;
}

/** No descriptor defaults are applied; only an author's input creates values. */
export function EngineConfigForm({ schema, value, onChange, onValidityChange }: Props) {
  const [raw, setRaw] = useState(() => JSON.stringify(value, null, 2));
  const [parseError, setParseError] = useState('');
  const sent = useRef(value);
  useEffect(() => {
    if (value !== sent.current) { setRaw(JSON.stringify(value, null, 2)); setParseError(''); sent.current = value; }
  }, [value]);
  const form = supported(schema);
  const errors = form ? issues(schema, value) : parseError ? [parseError] : [];
  const valid = errors.length === 0;
  useEffect(() => { onValidityChange?.(valid); }, [valid, onValidityChange]);
  return <div className="engine-config-form">
    {form ? <Fields schema={schema} value={value} path="Config" onChange={onChange} /> : <>
      <p>{schema === undefined ? 'This plugin has no configuration descriptor. Edit its configuration as JSON.' : 'This descriptor needs features outside the form editor. Edit its configuration as JSON.'}</p>
      <textarea aria-label="Plugin configuration JSON" rows={8} value={raw} onChange={e => {
        const text = e.target.value; setRaw(text);
        try {
          const next: unknown = JSON.parse(text);
          if (!object(next)) throw Error('Plugin configuration must be a JSON object');
          sent.current = next; setParseError(''); onChange(next);
        } catch (error) { setParseError(String(error)); }
      }} />
    </>}
    {errors.length > 0 && <div role="alert">{errors.map(error => <p key={error}>{error}</p>)}</div>}
  </div>;
}
