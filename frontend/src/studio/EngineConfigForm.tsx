import { Details } from '../console/Details';
import { Input, InputNumber, Select } from 'antd';
import { useEffect, useRef, useState } from 'react';
import './engine-config.css';

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
  default?: unknown;
  unit?: unknown;
  additionalProperties?: boolean;
}
interface Props {
  schema?: unknown;
  value: Config;
  onChange: (config: Config) => void;
  onValidityChange?: (valid: boolean) => void;
}
const object = (value: unknown): value is Config => typeof value === 'object' && value !== null && !Array.isArray(value);
const own = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const supportedKeys = new Set(['type', 'title', 'description', 'properties', 'required', 'enum', 'minimum', 'maximum', 'default', 'unit', 'additionalProperties']);

/** Known demo/physical parameters get genuine readable labels with their real units.
 * Descriptor title/unit always wins when the plugin declares them. */
const FRIENDLY: Record<string, string> = {
  max_speed_m_s: 'Max speed (m/s)',
  max_accel_m_s2: 'Max acceleration (m/s²)',
  reserve_j: 'Energy reserve (J)',
  capacity_j: 'Energy capacity (J)',
  idle_w: 'Idle power (W)',
  per_m_j: 'Energy per metre (J/m)',
  step_ns: 'Step (ns)',
  speed_mps: 'Speed (m/s)',
  offset_m: 'Offset (m)',
  altitude_m: 'Altitude (m)',
};

/** Descriptor `unit` may be a plain string or a symbol object ({symbol,name,...}); render it
 * only when a readable token is actually present. */
function unitText(child: Schema): string {
  const unit = child.unit;
  if (typeof unit === 'string') return unit;
  if (object(unit)) {
    const symbol = unit.symbol ?? unit.name ?? unit.label ?? unit.unit;
    if (typeof symbol === 'string' && symbol) return symbol;
  }
  return '';
}

function fieldTitle(key: string, child: Schema): string {
  const unit = unitText(child);
  if (child.title) return unit ? `${child.title} (${unit})` : child.title;
  const friendly = Object.prototype.hasOwnProperty.call(FRIENDLY,key) ? FRIENDLY[key] : undefined;
  if (friendly) return unit && !friendly.toLowerCase().includes(unit.toLowerCase()) ? `${friendly} (${unit})` : friendly;
  return unit ? `${key} (${unit})` : key;
}

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

/** SameValueZero comparison of an authored value against the explicitly declared default. */
function declared(value: unknown, current: unknown): boolean {
  if (value === current) return true;
  if (object(value) && object(current) && Object.keys(value).length === Object.keys(current).length) return Object.entries(value).every(([key, item]) => key in current && declared(item, current[key]));
  return false;
}

function ClearIcon() {
  return <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path d="M2.5 2.5l7 7m0-7l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" fill="none" /></svg>;
}

function Fields({ schema, value, path, onChange }: { schema: Schema; value: Config; path: string; onChange: (value: Config) => void }) {
  const set = (key: string, child: unknown, remove = false) => {
    // Object-literal spread keeps prototype-like member names (e.g. __proto__) ordinary own keys.
    const next = remove ? { ...value } : { ...value, [key]: child };
    if (remove) delete next[key];
    onChange(next);
  };
  /** Reset only ever restores a default the plugin author explicitly declared;
   * without a declared default the authored value is removed. */
  const resetTo = (key: string, child: Schema) => set(key, child.default, !own(child, 'default'));
  const scalar = Object.entries(schema.properties!).filter(([, child]) => child.type !== 'object');
  const nested = Object.entries(schema.properties!).filter(([, child]) => child.type === 'object');
  return <div className="studio-fields">{[...scalar, ...nested].map(([key, child]) => {
    const label = `${path}.${key}`, required = schema.required?.includes(key), current = value[key];
    const present = own(value, key);
    const differs = present && own(child, 'default') ? !declared(child.default, current) : false;
    const control = child.type === 'object'
      ? <fieldset><legend>{fieldTitle(key, child)}{required ? ' *' : ''}</legend><Fields schema={child} value={object(current) ? current : {}} path={label} onChange={next => set(key, next)} /></fieldset>
      : child.enum ? <Select aria-label={label} value={child.enum.findIndex(item => Object.is(item, current)) < 0 ? undefined : child.enum.findIndex(item => Object.is(item, current))} placeholder="Unset" onChange={index => index === undefined ? set(key, undefined, true) : set(key, child.enum![index])} options={child.enum.map((item, index) => ({ value: index, label: String(item) }))} />
        : child.type === 'boolean' ? <Select aria-label={label} value={typeof current === 'boolean' ? String(current) : undefined} placeholder="Unset" onChange={next => next === undefined ? set(key, undefined, true) : set(key, next === 'true')} options={[{ value: 'true', label: 'true' }, { value: 'false', label: 'false' }]} />
        : child.type === 'string' ? <Input aria-label={label} value={typeof current === 'string' ? current : ''} onChange={e => set(key, e.target.value)} />
        : <InputNumber aria-label={label} step={child.type === 'integer' ? 1 : 'any'} min={child.minimum} max={child.maximum} value={typeof current === 'number' ? current : null} onChange={next => next === null || typeof next !== 'number' || !Number.isFinite(next) ? set(key, undefined, true) : set(key, next)} />;
    return <div className={`studio-field${child.type === 'object' ? ' studio-field--object' : ''}`} key={key}>
      {child.type !== 'object' && <label>{fieldTitle(key, child)}{required ? ' *' : ''}</label>}
      {child.type !== 'object' && child.description && <small>{child.description}</small>}
      <div className="studio-field-control">
        {control}
        {present && child.type !== 'object' && (!own(child,'default') || differs) && (
          differs
            ? <button type="button" className="studio-reset" aria-label={`Reset ${label}`} title="Reset to declared default" onClick={() => resetTo(key, child)}><ClearIcon /></button>
            : <button type="button" className="studio-reset" aria-label={`Clear ${label}`} title="Clear value" onClick={() => set(key, undefined, true)}><ClearIcon /></button>
        )}
      </div>
    </div>;
  })}</div>;
}

/** No descriptor defaults are applied implicitly; only an author's input or explicit reset creates values. */
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
      <Details title="Plugin configuration JSON"><textarea aria-label="Plugin configuration JSON" rows={8} value={raw} onChange={e => {
        const text = e.target.value; setRaw(text);
        try {
          const next: unknown = JSON.parse(text);
          if (!object(next)) throw Error('Plugin configuration must be a JSON object');
          sent.current = next; setParseError(''); onChange(next);
        } catch (error) { setParseError(String(error)); }
      }} /></Details>
    </>}
    {errors.length > 0 && <div role="alert">{errors.map(error => <p key={error}>{error}</p>)}</div>}
  </div>;
}
