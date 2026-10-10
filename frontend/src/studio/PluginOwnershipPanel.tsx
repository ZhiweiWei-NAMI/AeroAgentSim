import { useState } from 'react';
import { EngineConfigForm, engineDescriptorText, type Translate } from './EngineConfigForm';
import { Details } from '../console/Details';
import { useI18n } from '../i18n/I18nProvider';

export interface EngineDescriptor {
  id: string; available: boolean; description: string; error?: string; config_schema?: unknown;
  capability_descriptor?: { commands?: string[]; fields?: string[]; requires_plugins?: string[]; [key: string]: unknown };
}
type Row = Record<string, any>;
interface Props { scenario: Row; engines: EngineDescriptor[]; onChange: (scenario: Row) => void }
export interface ProfileDiagnostic { message: string; blocking: boolean }
const english: Translate = (text, vars = {}) => Object.entries(vars).reduce((result, [key, value]) => result.split(`{${key}}`).join(String(value)), text);

/** Severity is data, not a substring of a translated message. Missing metadata needs server validation. */
export function profileDiagnostics(descriptor: EngineDescriptor | undefined, fields: string[], commands: string[], configuredPlugins: string[], t: Translate = english): ProfileDiagnostic[] {
  if (!descriptor?.available) return [{ message: t('Select an available installed plugin'), blocking: true }];
  const capability = descriptor.capability_descriptor;
  if (!capability) return [{ message: t('Capability descriptor unavailable; compatibility requires server validation'), blocking: false }];
  const diagnostics: ProfileDiagnostic[] = [];
  if (capability.fields) {
    for (const field of fields) if (!capability.fields.includes(field)) diagnostics.push({ message: t('Plugin does not advertise writer capability for {field}', { field }), blocking: true });
  } else if (fields.length) diagnostics.push({ message: t('Writer capability descriptor unavailable'), blocking: false });
  if (capability.commands) {
    for (const command of commands) if (!capability.commands.includes(command)) diagnostics.push({ message: t('Plugin does not advertise {command}', { command }), blocking: true });
  } else if (commands.length) diagnostics.push({ message: t('Command capability descriptor unavailable'), blocking: false });
  for (const plugin of capability.requires_plugins ?? []) if (!configuredPlugins.includes(plugin)) diagnostics.push({ message: t('Required configured plugin: {plugin}', { plugin }), blocking: true });
  return diagnostics;
}

/** Preserve the readable English helper API used by existing tests and integrations. */
export function profileIssues(descriptor: EngineDescriptor | undefined, fields: string[], commands: string[], configuredPlugins: string[], t: Translate = english): string[] {
  return profileDiagnostics(descriptor, fields, commands, configuredPlugins, t).map(issue => issue.message);
}

export function PluginOwnershipPanel({ scenario, engines, onChange }: Props) {
  const { t } = useI18n();
  const partitions = Object.entries(scenario.engines ?? {}) as Array<[string, Row]>;
  const [partition, setPartition] = useState(''), [plugin, setPlugin] = useState(''), [config, setConfig] = useState<Record<string, unknown>>({}), [configValid, setConfigValid] = useState(true), [required, setRequired] = useState(''), [error, setError] = useState('');
  const bindings = [...(scenario.bindings?.exact ?? []), ...(scenario.bindings?.rules ?? [])] as Row[];
  const descriptor = engines.find(item => item.id === plugin);
  const partitionExists = partitions.some(([id]) => id === partition);
  const fields = bindings.filter(row => row.writer === partition).flatMap(row => row.field ? [row.field] : row.fields ?? []) as string[];
  // Check the configuration after replacement, not the plugin that is about to disappear.
  const configured = partitions.filter(([id]) => id !== partition).map(([, item]) => item.plugin).concat(plugin);
  const checks = profileDiagnostics(descriptor, fields, required.split(',').map(value => value.trim()).filter(Boolean), configured, t);
  const setWriter = (section: 'exact' | 'rules', index: number, writer: string) => onChange({ ...scenario, bindings: { ...scenario.bindings, [section]: (scenario.bindings?.[section] ?? []).map((row: Row, i: number) => i === index ? { ...row, writer } : row) } });
  return <section aria-label={t('Plugin profiles and field ownership')}>
    <h3>{t('Who controls each value')}</h3>
    <p>{t('Each field has one writer: the engine partition allowed to update it. Changed settings apply to the next run, after validation.')}</p>
    {bindings.length ? <table><thead><tr><th>{t('Entity / type selector')}</th><th>{t('Field')}</th><th>{t('Declared writer plugin')}</th></tr></thead><tbody>{(['exact', 'rules'] as const).flatMap(section => (scenario.bindings?.[section] ?? []).map((row: Row, index: number) => <tr key={`${section}/${index}`}><td>{row.entity ?? row.type}{row.ids ? ` / ${row.ids}` : ''}</td><td>{row.field ?? row.fields?.join(', ')}</td><td><select aria-label={t('Writer {section} {index}', { section, index })} value={row.writer ?? ''} disabled={!partitions.length} onChange={event => setWriter(section, index, event.target.value)}>{!partitions.some(([id]) => id === row.writer) && <option value={row.writer ?? ''} disabled>{row.writer ? t('Unknown writer: {writer}', { writer: row.writer }) : t('No writer selected')}</option>}{partitions.map(([id, item]) => <option key={id} value={id}>{id} · {item.plugin}</option>)}</select></td></tr>))}</tbody></table> : <p>{t('No field bindings declared in this scenario')}</p>}
    <p>{t('Advanced replacement changes the plugin for a whole engine partition. It does not transfer writers during a running simulation.')}</p>
    <Details title={t('Replace a partition plugin')} buttonLabel={t('Advanced plugin replacement')}>
      <p>{t('Edits invalidate validation and create a new run and epoch. Native readiness requires the configured service and actual runtime validation.')}</p>
      {!partitions.length && <p>{t('No engine partitions are configured in this draft.')}</p>}
      <label>{t('Engine partition')} <select aria-label={t('Profile partition')} value={partition} onChange={event => {
        const id = event.target.value, current = scenario.engines?.[id];
        setPartition(id); setPlugin(current?.plugin ?? ''); setConfig(current?.config ?? {}); setConfigValid(false); setRequired(''); setError('');
      }}><option value="">{t('Select partition')}</option>{partitions.map(([id]) => <option key={id}>{id}</option>)}</select></label>
      <label>{t('Plugin')} <select aria-label={t('Replacement profile')} value={plugin} disabled={!partitionExists} onChange={event => { setPlugin(event.target.value); setConfig({}); setConfigValid(false); setError(''); }}><option value="">{t('Select installed profile')}</option>{engines.map(item => <option key={item.id} value={item.id} disabled={!item.available}>{item.available ? item.id : t('{plugin} · unavailable', { plugin: item.id })}</option>)}</select></label>
      <label>{t('Required command IDs')} <input aria-label={t('Required capabilities')} value={required} onChange={event => setRequired(event.target.value)} placeholder={t('Comma-separated schema IDs')} /></label>
      <p>{t('Optional: list command schema IDs that this replacement must support. Leave blank if you only need the declared field writers.')}</p>
      {descriptor && <><p>{engineDescriptorText(descriptor.description, t)}</p>{descriptor.available ? <EngineConfigForm key={`${partition}/${plugin}`} schema={descriptor.config_schema} value={config} onChange={setConfig} onValidityChange={setConfigValid} /> : <><p>{t('This plugin is unavailable here; its configuration is read-only.')}</p><pre>{JSON.stringify(config, null, 2)}</pre></>}<Details title={t('Installed capability descriptor')} buttonLabel={t('Capability descriptor')}><pre>{descriptor.capability_descriptor ? JSON.stringify(descriptor.capability_descriptor, null, 2) : t('Not advertised by this plugin')}</pre></Details></>}
      {plugin && checks.map(issue => <p key={issue.message}>{issue.message}</p>)}
      {!engines.some(item => item.available) && <p>{t('No installed plugins are currently available; profiles cannot be applied.')}</p>}
      <button type="button" disabled={!partitionExists || !descriptor?.available || !configValid || checks.some(issue => issue.blocking)} onClick={() => { try { onChange({ ...scenario, engines: { ...scenario.engines, [partition]: { ...scenario.engines[partition], plugin, config } } }); setError(''); } catch (problem) { setError(String(problem)); } }}>{t('Apply profile to draft')}</button>
      {error && <p role="alert">{t('Could not apply plugin profile: {error}', { error })}</p>}
    </Details>
  </section>;
}
