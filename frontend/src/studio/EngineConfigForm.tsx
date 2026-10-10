import { Details } from '../console/Details';
import { Input, InputNumber, Select } from 'antd';
import { useEffect, useId, useRef, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import './engine-config.css';

export type Translate = (text: string, vars?: Record<string, unknown>) => string;
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

/** Translate known backend wording explicitly; unfamiliar plugin-authored text remains intact. */
export function engineDescriptorText(text: string, t: Translate): string {
  switch (text) {
    case 'installed plugin': return t('Installed plugin');
    case 'Installed plugin': return t('Installed plugin');
    case 'Max speed': return t('Max speed');
    case 'Maximum speed': return t('Maximum speed');
    case 'Max speed (m/s)': return t('Max speed (m/s)');
    case 'Max acceleration': return t('Max acceleration');
    case 'Maximum acceleration': return t('Maximum acceleration');
    case 'Max acceleration (m/s²)': return t('Max acceleration (m/s²)');
    case 'Energy reserve': return t('Energy reserve');
    case 'Energy reserve (J)': return t('Energy reserve (J)');
    case 'Energy capacity': return t('Energy capacity');
    case 'Energy capacity (J)': return t('Energy capacity (J)');
    case 'Idle power': return t('Idle power');
    case 'Idle power (W)': return t('Idle power (W)');
    case 'Energy per metre': return t('Energy per metre');
    case 'Energy per metre (J/m)': return t('Energy per metre (J/m)');
    case 'Energy per meter': return t('Energy per meter');
    case 'Step': return t('Step');
    case 'Step (ns)': return t('Step (ns)');
    case 'Update interval (ns)': return t('Update interval (ns)');
    case 'One second is 1,000,000,000 nanoseconds.': return t('One second is 1,000,000,000 nanoseconds.');
    case 'Speed': return t('Speed');
    case 'Speed (m/s)': return t('Speed (m/s)');
    case 'Offset': return t('Offset');
    case 'Offset (m)': return t('Offset (m)');
    case 'Altitude': return t('Altitude');
    case 'Altitude (m)': return t('Altitude (m)');
    case 'Energy': return t('Energy');
    case 'Position field': return t('Position field');
    case 'Velocity field': return t('Velocity field');
    case 'Energy field': return t('Energy field');
    case 'Type ID': return t('Type ID');
    case 'Model': return t('Model');
    case 'Lifecycle': return t('Lifecycle');
    case 'Commands': return t('Commands');
    case 'Enabled': return t('Enabled');
    case 'Simulation step in nanoseconds.': return t('Simulation step in nanoseconds.');
    case 'Maximum speed in metres per second.': return t('Maximum speed in metres per second.');
    case 'Maximum acceleration in metres per second squared.': return t('Maximum acceleration in metres per second squared.');
    case 'Energy capacity in joules.': return t('Energy capacity in joules.');
    case 'Energy reserve in joules.': return t('Energy reserve in joules.');
    case 'Idle power consumption in watts.': return t('Idle power consumption in watts.');
    case 'Energy consumed per metre travelled.': return t('Energy consumed per metre travelled.');
    case 'true': return t('true');
    case 'false': return t('false');
    default: return text;
  }
}

/** Descriptor titles and units take precedence over familiar physical parameter labels. */
function friendlyTitle(key: string, t: Translate): string | undefined {
  switch (key) {
    case 'max_speed_m_s': return t('Max speed (m/s)');
    case 'max_accel_m_s2': return t('Max acceleration (m/s²)');
    case 'reserve_j': return t('Energy reserve (J)');
    case 'capacity_j': return t('Energy capacity (J)');
    case 'idle_w': return t('Idle power (W)');
    case 'per_m_j': return t('Energy per metre (J/m)');
    case 'step_ns': return t('Step (ns)');
    case 'speed_mps': return t('Speed (m/s)');
    case 'offset_m': return t('Offset (m)');
    case 'altitude_m': return t('Altitude (m)');
    case 'energy': return t('Energy');
    case 'position_field': return t('Position field');
    case 'velocity_field': return t('Velocity field');
    case 'energy_field': return t('Energy field');
    case 'type_id': return t('Type ID');
    case 'model': return t('Model');
    case 'lifecycle': return t('Lifecycle');
    case 'commands': return t('Commands');
    default: return undefined;
  }
}

/** Units may be strings or symbol objects. Never stringify an opaque unit as [object Object]. */
function unitText(child: Schema): string {
  const unit = child.unit;
  if (typeof unit === 'string') return unit;
  if (object(unit)) {
    const symbol = unit.symbol ?? unit.name ?? unit.label ?? unit.unit;
    if (typeof symbol === 'string' && symbol) return symbol;
  }
  return '';
}

function fieldTitle(key: string, child: Schema, t: Translate): string {
  const unit = unitText(child);
  const title = child.title ? engineDescriptorText(child.title, t) : friendlyTitle(key, t) ?? key;
  const hasUnit = title.toLowerCase().includes(`(${unit.toLowerCase()})`) || title.toLowerCase().includes(`（${unit.toLowerCase()}）`);
  return unit && !hasUnit ? t('{title} ({unit})', { title, unit }) : title;
}

function supported(value: unknown, root = true): value is Schema {
  if (!object(value) || Object.keys(value).some(key => !supportedKeys.has(key))) return false;
  for (const key of ['title', 'description']) if (value[key] !== undefined && typeof value[key] !== 'string') return false;
  if (value.required !== undefined && (!Array.isArray(value.required) || value.required.some(key => typeof key !== 'string'))) return false;
  if (value.additionalProperties !== undefined && typeof value.additionalProperties !== 'boolean') return false;
  for (const key of ['minimum', 'maximum']) if (value[key] !== undefined && (typeof value[key] !== 'number' || !Number.isFinite(value[key]))) return false;
  if (typeof value.minimum === 'number' && typeof value.maximum === 'number' && value.minimum > value.maximum) return false;
  if (value.enum !== undefined && (!Array.isArray(value.enum) || !value.enum.length || value.enum.some(item => !['string', 'number', 'boolean'].includes(typeof item)))) return false;
  if (value.type === 'object') return object(value.properties) && Object.values(value.properties).every(child => supported(child, false));
  return !root && ['string', 'number', 'integer', 'boolean'].includes(String(value.type));
}

function issues(schema: Schema, value: unknown, t: Translate, path = 'Config'): string[] {
  if (schema.type === 'object') {
    if (!object(value)) return [t('{path}: expected object', { path })];
    const errors = (schema.required ?? []).filter(key => !own(value, key)).map(key => t('{path}: required', { path: `${path}.${key}` }));
    if (schema.additionalProperties === false) for (const key of Object.keys(value)) if (!own(schema.properties!, key)) errors.push(t('{path}: unknown property', { path: `${path}.${key}` }));
    for (const [key, child] of Object.entries(schema.properties!)) if (own(value, key)) errors.push(...issues(child, value[key], t, `${path}.${key}`));
    return errors;
  }
  const valid = schema.type === 'integer' ? typeof value === 'number' && Number.isSafeInteger(value) : typeof value === schema.type && (typeof value !== 'number' || Number.isFinite(value));
  if (!valid) {
    switch (schema.type) {
      case 'integer': return [t('{path}: expected integer', { path })];
      case 'number': return [t('{path}: expected number', { path })];
      case 'boolean': return [t('{path}: expected boolean', { path })];
      default: return [t('{path}: expected string', { path })];
    }
  }
  if (schema.enum && !schema.enum.some(item => Object.is(item, value))) return [t('{path}: value excluded by enum', { path })];
  if (typeof value === 'number' && (schema.minimum !== undefined && value < schema.minimum || schema.maximum !== undefined && value > schema.maximum)) return [t('{path}: outside numeric bounds', { path })];
  return [];
}

/** Compare against the explicitly declared default; no defaults are applied implicitly. */
function declared(value: unknown, current: unknown): boolean {
  if (value === current) return true;
  if (object(value) && object(current) && Object.keys(value).length === Object.keys(current).length) return Object.entries(value).every(([key, item]) => own(current, key) && declared(item, current[key]));
  return false;
}

function ClearIcon() {
  return <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path d="M2.5 2.5l7 7m0-7l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" fill="none" /></svg>;
}

function Fields({ schema, value, path, onChange }: { schema: Schema; value: Config; path: string; onChange: (value: Config) => void }) {
  const { t } = useI18n();
  const id = useId();
  const set = (key: string, child: unknown, remove = false) => {
    const next = remove ? { ...value } : { ...value, [key]: child };
    if (remove) delete next[key];
    onChange(next);
  };
  const resetTo = (key: string, child: Schema) => set(key, child.default, !own(child, 'default'));
  const scalar = Object.entries(schema.properties!).filter(([, child]) => child.type !== 'object');
  const nested = Object.entries(schema.properties!).filter(([, child]) => child.type === 'object');
  return <div className="studio-fields">{[...scalar, ...nested].map(([key, child]) => {
    const field = path ? `${path}.${key}` : key;
    const label = t('Config.{field}', { field }), required = schema.required?.includes(key), current = own(value, key) ? value[key] : undefined;
    const controlId = `${id}-${encodeURIComponent(key)}`, hintId = `${controlId}-hint`;
    const present = own(value, key);
    const differs = present && own(child, 'default') ? !declared(child.default, current) : false;
    const accessibility = { id: controlId, 'aria-label': label, 'aria-describedby': child.description ? hintId : undefined, 'aria-required': required || undefined };
    const control = child.type === 'object'
      ? <fieldset><legend>{fieldTitle(key, child, t)}{required ? ' *' : ''}</legend>{child.description && <small>{engineDescriptorText(child.description, t)}</small>}<Fields schema={child} value={object(current) ? current : {}} path={field} onChange={next => set(key, next)} /></fieldset>
      : child.enum ? <Select {...accessibility} value={child.enum.findIndex(item => Object.is(item, current)) < 0 ? undefined : child.enum.findIndex(item => Object.is(item, current))} placeholder={t('Unset')} onChange={index => index === undefined ? set(key, undefined, true) : set(key, child.enum![index])} options={child.enum.map((item, index) => ({ value: index, label: engineDescriptorText(String(item), t) }))} />
        : child.type === 'boolean' ? <Select {...accessibility} value={typeof current === 'boolean' ? String(current) : undefined} placeholder={t('Unset')} onChange={next => next === undefined ? set(key, undefined, true) : set(key, next === 'true')} options={[{ value: 'true', label: t('true') }, { value: 'false', label: t('false') }]} />
        : child.type === 'string' ? <Input {...accessibility} value={typeof current === 'string' ? current : ''} onChange={e => set(key, e.target.value)} />
        : <InputNumber {...accessibility} step={child.type === 'integer' ? 1 : 'any'} min={child.minimum} max={child.maximum} value={typeof current === 'number' ? current : null} onChange={next => next === null || typeof next !== 'number' || !Number.isFinite(next) ? set(key, undefined, true) : set(key, next)} />;
    return <div className={`studio-field${child.type === 'object' ? ' studio-field--object' : ''}`} key={key}>
      {child.type !== 'object' && <label htmlFor={controlId}>{fieldTitle(key, child, t)}{required ? ' *' : ''}</label>}
      {child.type !== 'object' && child.description && <small id={hintId}>{engineDescriptorText(child.description, t)}</small>}
      <div className="studio-field-control">
        {control}
        {present && child.type !== 'object' && (!own(child, 'default') || differs) && (
          differs
            ? <button type="button" className="studio-reset" aria-label={t('Reset {field}', { field: label })} title={t('Reset to declared default')} onClick={() => resetTo(key, child)}><ClearIcon /></button>
            : <button type="button" className="studio-reset" aria-label={t('Clear {field}', { field: label })} title={t('Clear value')} onClick={() => set(key, undefined, true)}><ClearIcon /></button>
        )}
      </div>
    </div>;
  })}</div>;
}

export function EngineConfigForm({ schema, value, onChange, onValidityChange }: Props) {
  const { t } = useI18n();
  const [raw, setRaw] = useState(() => JSON.stringify(value, null, 2));
  const [parseError, setParseError] = useState<{ kind: 'json' | 'object'; detail?: string }>();
  const sent = useRef(value);
  useEffect(() => {
    if (value !== sent.current) { setRaw(JSON.stringify(value, null, 2)); setParseError(undefined); sent.current = value; }
  }, [value]);
  const form = supported(schema);
  const errors = form ? issues(schema, value, t) : parseError ? [parseError.kind === 'object' ? t('Plugin configuration must be a JSON object') : t('Invalid configuration JSON: {error}', { error: parseError.detail })] : [];
  const valid = errors.length === 0;
  useEffect(() => { onValidityChange?.(valid); }, [valid, onValidityChange]);
  return <div className="engine-config-form">
    {form ? <><p>{t('Set only the options you need. Required fields are marked *. Defaults are applied only when you explicitly reset a value.')}</p><Fields schema={schema} value={value} path="" onChange={onChange} /></> : <>
      <p>{schema === undefined ? t('This plugin has no configuration descriptor. Edit its configuration as JSON.') : t('This descriptor needs features outside the form editor. Edit its configuration as JSON.')}</p>
      <Details title={t('Plugin configuration JSON')} buttonLabel={t('Edit configuration JSON')}><textarea aria-label={t('Plugin configuration JSON')} rows={8} value={raw} onChange={e => {
        const text = e.target.value; setRaw(text);
        try {
          const next: unknown = JSON.parse(text);
          if (!object(next)) { setParseError({ kind: 'object' }); return; }
          sent.current = next; setParseError(undefined); onChange(next);
        } catch (error) { setParseError({ kind: 'json', detail: String(error) }); }
      }} /></Details>
    </>}
    {errors.length > 0 && <div role="alert">{errors.map(error => <p key={error}>{error}</p>)}</div>}
  </div>;
}
