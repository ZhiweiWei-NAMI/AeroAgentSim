import { useState } from 'react';
import { EngineConfigForm } from './EngineConfigForm';

export interface EngineDescriptor {
  id: string; available: boolean; description: string; error?: string; config_schema?: unknown;
  capability_descriptor?: { commands?: string[]; fields?: string[]; requires_plugins?: string[]; [key: string]: unknown };
}
type Row = Record<string, any>;
interface Props { scenario: Row; engines: EngineDescriptor[]; onChange: (scenario: Row) => void }
export function profileIssues(descriptor: EngineDescriptor | undefined, fields: string[], commands: string[], configuredPlugins: string[]): string[] {
  if (!descriptor?.available) return ['Select an available installed plugin'];
  const capability = descriptor.capability_descriptor;
  if (!capability) return ['Capability descriptor unavailable; compatibility requires server validation'];
  const issues: string[] = [];
  if (capability.fields) { for (const field of fields) if (!capability.fields.includes(field)) issues.push(`Plugin does not advertise writer capability for ${field}`); }
  else if (fields.length) issues.push('Writer capability descriptor unavailable');
  if (capability.commands) { for (const command of commands) if (!capability.commands.includes(command)) issues.push(`Plugin does not advertise ${command}`); }
  else if (commands.length) issues.push('Command capability descriptor unavailable');
  for (const plugin of capability.requires_plugins ?? []) if (!configuredPlugins.includes(plugin)) issues.push(`Required configured plugin: ${plugin}`);
  return issues;
}
export function PluginOwnershipPanel({ scenario, engines, onChange }: Props) {
  const partitions = Object.entries(scenario.engines ?? {}) as Array<[string, Row]>;
  const [partition, setPartition] = useState(''), [plugin, setPlugin] = useState(''), [config, setConfig] = useState<Record<string, unknown>>({}), [configValid, setConfigValid] = useState(true), [required, setRequired] = useState(''), [error, setError] = useState('');
  const bindings = [...(scenario.bindings?.exact ?? []), ...(scenario.bindings?.rules ?? [])] as Row[];
  const descriptor = engines.find(item => item.id === plugin);
  const fields = bindings.filter(row => row.writer === partition).flatMap(row => row.field ? [row.field] : row.fields ?? []) as string[];
  const checks = profileIssues(descriptor, fields, required.split(',').map(value => value.trim()).filter(Boolean), partitions.map(([, item]) => item.plugin));
  const contradictory = checks.filter(issue => !issue.includes('unavailable') && !issue.includes('requires server validation'));
  const setWriter = (section: 'exact' | 'rules', index: number, writer: string) => onChange({ ...scenario, bindings: { ...scenario.bindings, [section]: scenario.bindings[section].map((row: Row, i: number) => i === index ? { ...row, writer } : row) } });
  return <section aria-label="Plugin profiles and field ownership">
    <h3>Field writers / replacement profiles</h3>
    <p>Edits invalidate validation and create a new run and epoch. Native readiness requires the configured service and actual runtime validation.</p>
    <table><thead><tr><th>Entity / type selector</th><th>Field</th><th>Declared writer plugin</th></tr></thead><tbody>{(['exact', 'rules'] as const).flatMap(section => (scenario.bindings?.[section] ?? []).map((row: Row, index: number) => <tr key={`${section}/${index}`}><td>{row.entity ?? row.type}{row.ids ? ` / ${row.ids}` : ''}</td><td>{row.field ?? row.fields?.join(', ')}</td><td><select aria-label={`Writer ${section} ${index}`} value={row.writer} onChange={event => setWriter(section, index, event.target.value)}>{partitions.map(([id, item]) => <option key={id} value={id}>{id} · {item.plugin}</option>)}</select></td></tr>))}</tbody></table>
    <label>Replace partition <select aria-label="Profile partition" value={partition} onChange={event => { setPartition(event.target.value); setPlugin(''); setConfig({}); setError(''); }}><option value="">Select</option>{partitions.map(([id]) => <option key={id}>{id}</option>)}</select></label>
    <label>Plugin <select aria-label="Replacement profile" value={plugin} onChange={event => { setPlugin(event.target.value); setConfig({}); }}><option value="">Select installed profile</option>{engines.map(item => <option key={item.id} value={item.id} disabled={!item.available}>{item.id} {item.available ? '' : '· unavailable'}</option>)}</select></label>
    <label>Required command IDs <input aria-label="Required capabilities" value={required} onChange={event => setRequired(event.target.value)} placeholder="Comma-separated schema IDs" /></label>
    {descriptor && <><p>{descriptor.description}</p><EngineConfigForm key={`${partition}/${plugin}`} schema={descriptor.config_schema} value={config} onChange={setConfig} onValidityChange={setConfigValid} /><details><summary>Installed capability descriptor</summary><pre>{descriptor.capability_descriptor ? JSON.stringify(descriptor.capability_descriptor, null, 2) : 'Not advertised by this plugin'}</pre></details></>}
    {checks.map(issue => <p key={issue}>{issue}</p>)}
    <button disabled={!partition || !descriptor?.available || !configValid || contradictory.length > 0} onClick={() => { try { onChange({ ...scenario, engines: { ...scenario.engines, [partition]: { ...scenario.engines[partition], plugin, config } } }); setError(''); } catch (problem) { setError(String(problem)); } }}>Apply profile to draft</button>
    {error && <p role="alert">{error}</p>}
  </section>;
}
