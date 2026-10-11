import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Collapse, Empty, Input, Select, Space, Table, Tag } from 'antd';
import { ExpressionControl } from '../../behaviours/ExpressionControl';
import { Details } from '../../console/Details';
import { EngineConfigForm, engineDescriptorText, type Translate } from '../EngineConfigForm';
import { profileDiagnostics, type EngineDescriptor, type ProfileDiagnostic } from '../PluginOwnershipPanel';
import { useI18n } from '../../i18n/I18nProvider';

type Row = Record<string, any>;
export interface DomainPluginsStepProps {
  scenario: Row;
  onChange: (scenario: Row) => void;
  engines: EngineDescriptor[];
  onValidityChange?: (valid: boolean) => void;
}
const DOMAINS = [
  { key: 'air', match: /air|uav|kinematic|px4|gazebo/i },
  { key: 'road', match: /road|vehicle_motion|sumo/i },
  { key: 'network', match: /network|ns3|ns-3|ideal/i },
  { key: 'weather', match: /weather|environment|wind|climate/i },
];
const domainOf = (id: string, item: Row): string =>
  (DOMAINS.find(domain => domain.match.test(id)) ?? DOMAINS.find(domain => domain.match.test(String(item?.plugin ?? ''))))?.key ?? 'other';
function domainTitle(key: string, t: Translate): string {
  switch (key) {
    case 'air': return t('Air motion & energy');
    case 'road': return t('Road traffic');
    case 'network': return t('Network');
    case 'weather': return t('Weather & environment');
    default: return t('Other configured domains');
  }
}
function partitionName(id: string, t: Translate): string {
  switch (id) {
    case 'air': case 'air_motion': return t('Air motion');
    case 'motion': return t('Motion');
    case 'road': case 'road_motion': return t('Road motion');
    case 'network': return t('Network');
    case 'weather': return t('Weather');
    case 'operations': return t('Operations');
    case 'decisions': return t('Decisions');
    case 'behaviour': return t('Behaviour');
    case 'capture': return t('Capture');
    case 'capture_bridge': return t('Capture bridge');
    default: return id;
  }
}
const matchesDomain = (key: string, id: string) => {
  switch (key) {
    case 'air': return /kinematic|px4|gazebo/i.test(id);
    case 'road': return /sumo|road_motion/i.test(id);
    case 'network': return /network|ns3|ns-3|ideal/i.test(id);
    case 'weather': return /weather|environment/i.test(id);
    default: return true;
  }
};

/** Domain grouping is advisory. Unknown content survives; server validation remains authoritative. */
export function DomainPluginsStep({ scenario, onChange, engines, onValidityChange }: DomainPluginsStepProps) {
  const { t } = useI18n();
  const partitions = useMemo(() => Object.entries(scenario?.engines ?? {}) as Array<[string, Row]>, [scenario?.engines]);
  const engineById = useMemo(() => new Map(engines.map(engine => [engine.id, engine])), [engines]);
  const profileConfigs = useRef(new Map<string, Record<string, unknown>>());
  const [openDomains, setOpenDomains] = useState<string[]>(['air']);
  const [configValidity, setConfigValidity] = useState<Record<string, { plugin: string; valid: boolean }>>({});
  const [replacePartition, setReplacePartition] = useState(''), [replacePlugin, setReplacePlugin] = useState('');
  const [replaceConfig, setReplaceConfig] = useState<Record<string, unknown>>({}), [replaceConfigValid, setReplaceConfigValid] = useState(true);
  const [requiredCommands, setRequiredCommands] = useState('');
  const [applyError, setApplyError] = useState<{ kind: 'selection' | 'availability' | 'config' | 'checks' | 'runtime'; detail?: string }>();
  const readName = (id: string, item: Row): string => {
    const name = partitionName(id, t), label = name === id ? id : t('{name} ({id})', { name, id });
    return item?.plugin ? `${label} · ${item.plugin}` : label;
  };
  const switchEngine = (partition: string, next: string) => {
    if (!engineById.get(next)?.available) return;
    const current = scenario.engines[partition];
    profileConfigs.current.set(`${partition}/${current.plugin}`, current.config);
    const config = profileConfigs.current.get(`${partition}/${next}`) ?? {};
    setConfigValidity(previous => { const nextValidity = { ...previous }; delete nextValidity[partition]; return nextValidity; });
    onChange({ ...scenario, engines: { ...scenario.engines, [partition]: { ...current, plugin: next, config } } });
  };
  const bindings = useMemo(() => [
    ...((scenario?.bindings?.exact ?? []) as Row[]).map((row, index) => ({ section: 'exact' as const, index, row })),
    ...((scenario?.bindings?.rules ?? []) as Row[]).map((row, index) => ({ section: 'rules' as const, index, row })),
  ], [scenario?.bindings?.exact, scenario?.bindings?.rules]);
  const configuredPlugins = partitions.map(([, item]) => item?.plugin).filter(plugin => typeof plugin === 'string');
  const rowChecks = (row: Row): ProfileDiagnostic[] => {
    const item = scenario.engines?.[String(row?.writer ?? '')];
    const fields = row.field ? [row.field] : Array.isArray(row.fields) ? row.fields : [];
    return profileDiagnostics(engineById.get(item?.plugin), fields, row.command ? [String(row.command)] : [], configuredPlugins, t);
  };
  // An unused replacement draft is not part of the scenario. Removed partitions and old plugins
  // must not leave stale invalid buffers blocking the current draft either.
  const currentConfigValid = partitions.every(([id, item]) => {
    const record = configValidity[id];
    return !engineById.get(item?.plugin)?.available || !record || record.plugin !== item?.plugin || record.valid;
  });
  useEffect(() => { onValidityChange?.(currentConfigValid); }, [currentConfigValid, onValidityChange]);
  const setWriter = (section: 'exact' | 'rules', index: number, writer: string) => onChange({ ...scenario, bindings: { ...scenario.bindings, [section]: (scenario.bindings?.[section] ?? []).map((row: Row, i: number) => i === index ? { ...row, writer } : row) } });
  const descriptor = engineById.get(replacePlugin);
  const partitionExists = partitions.some(([id]) => id === replacePartition);
  const replacementFields = bindings.filter(entry => entry.row.writer === replacePartition).flatMap(entry => entry.row.field ? [entry.row.field] : entry.row.fields ?? []);
  const replacementChecks = profileDiagnostics(descriptor, replacementFields, requiredCommands.split(',').map(value => value.trim()).filter(Boolean), partitions.filter(([id]) => id !== replacePartition).map(([, item]) => item.plugin).concat(replacePlugin), t);
  const blockingChecks = replacementChecks.filter(issue => issue.blocking);
  const applyProfile = () => {
    setApplyError(undefined);
    if (!partitionExists || !replacePlugin) { setApplyError({ kind: 'selection' }); return; }
    if (!descriptor?.available) { setApplyError({ kind: 'availability' }); return; }
    if (!replaceConfigValid) { setApplyError({ kind: 'config' }); return; }
    if (blockingChecks.length) { setApplyError({ kind: 'checks' }); return; }
    try { onChange({ ...scenario, engines: { ...scenario.engines, [replacePartition]: { ...scenario.engines[replacePartition], plugin: replacePlugin, config: replaceConfig } } }); }
    catch (problem) { setApplyError({ kind: 'runtime', detail: String(problem) }); }
  };
  const applyErrorText = !applyError ? '' : applyError.kind === 'selection' ? t('Select a partition and an installed plugin first')
    : applyError.kind === 'availability' ? t('Selected plugin is not available in this deployment')
      : applyError.kind === 'config' ? t('Correct the plugin configuration before applying')
        : applyError.kind === 'checks' ? blockingChecks.map(issue => issue.message).join('; ')
          : t('Could not apply plugin profile: {error}', { error: applyError.detail });
  const domains = useMemo(() => {
    const map = new Map<string, Array<[string, Row]>>(DOMAINS.map(domain => [domain.key, []]));
    partitions.forEach(([id, item]) => { const key = domainOf(id, item), rows = map.get(key) ?? []; rows.push([id, item]); map.set(key, rows); });
    return [...map.entries()];
  }, [partitions]);
  const unavailableCatalog = engines.filter(engine => !engine.available);
  const columns = [
    { title: t('Entity / type'), key: 'selector', render: (_: unknown, entry: { row: Row }) => <span>{entry.row?.entity ?? entry.row?.type ?? t('Unspecified')}{entry.row?.ids ? <code className="guided-ownership-ids"> / {String(entry.row.ids)}</code> : null}</span> },
    { title: t('Fields'), key: 'fields', render: (_: unknown, entry: { row: Row }) => <span>{entry.row?.field ?? (Array.isArray(entry.row?.fields) ? entry.row.fields.join(', ') : t('Unspecified'))}</span> },
    { title: t('Who controls this value'), key: 'writer', render: (_: unknown, entry: { section: 'exact' | 'rules'; index: number; row: Row }) => <Select size="small" aria-label={t('Writer {section} {index}', { section: entry.section, index: entry.index })} value={String(entry.row?.writer ?? '')} placeholder={t('No writer selected')} disabled={!partitions.length} className="guided-writer-select" options={partitions.map(([id, item]) => ({ value: id, label: readName(id, item) }))} onChange={value => setWriter(entry.section, entry.index, value)} /> },
    { title: t('Status'), key: 'status', render: (_: unknown, entry: { row: Row }) => {
      const checks = rowChecks(entry.row);
      if (!checks.length) return <Tag color="success">{t('Compatible')}</Tag>;
      return <Space size="small" wrap><Tag color={checks.some(issue => issue.blocking) ? 'red' : undefined}>{checks.some(issue => issue.blocking) ? t('Conflict') : t('Needs server validation')}</Tag><Details title={t('Ownership check')} buttonLabel={t('Checks')}><ul className="guided-ownership-issues">{checks.map(issue => <li key={issue.message}>{issue.message}</li>)}</ul></Details></Space>;
    } },
  ];
  return <div className="guided-domain-plugins">
    <p>{t('An engine partition is a named group of fields controlled by one plugin. Start with the configured engines; changes apply only to the next validated run.')}</p>
    <Collapse className="guided-domain-cards" activeKey={openDomains} onChange={keys => setOpenDomains(Array.isArray(keys) ? keys : [keys])} items={domains.map(([key, items]) => ({
      key,
      label: <span className="guided-domain-label">{domainTitle(key, t)} <Tag>{items.length}</Tag></span>,
      children: <div className="guided-domain-body">
        {!items.length && <Card title={t('No engine configured')}><p className="guided-muted">{t('Add a domain plugin to this draft, then configure its fields and validate before running.')}</p><Select aria-label={t('Add {domain} engine', { domain: domainTitle(key, t) })} placeholder={t('Choose a domain engine')} options={engines.filter(engine => matchesDomain(key, engine.id)).map(engine => ({ value: engine.id, label: engine.available ? engine.id : t('{plugin} · unavailable', { plugin: engine.id }), disabled: !engine.available }))} notFoundContent={t('No matching installed engines')} onChange={plugin => onChange({ ...scenario, engines: { ...scenario.engines, [key]: { plugin, config: {} } } })} /></Card>}
        {items.map(([partitionId, item]) => {
          const plugin = engineById.get(String(item?.plugin)), available = plugin?.available;
          return <Card key={partitionId} size="small" className="guided-domain-partition" title={readName(partitionId, item)} extra={available === undefined ? <Tag>{t('Plugin not in catalog')}</Tag> : available ? <Tag color="green">{t('Installed')}</Tag> : <Tag color="red">{t('Unavailable')}</Tag>}>
            <label className="guided-domain-engine-label">{t('Engine')}<Select aria-label={t('Engine {partition}', { partition: partitionId })} value={item.plugin} options={engines.filter(engine => engine.id === item.plugin || matchesDomain(key, engine.id)).map(engine => ({ value: engine.id, label: engine.available ? engine.id : t('{plugin} · unavailable', { plugin: engine.id }), disabled: !engine.available }))} notFoundContent={t('No matching installed engines')} onChange={next => switchEngine(partitionId, next)} /></label>
            {plugin?.description && <p>{engineDescriptorText(plugin.description, t)}</p>}
            {plugin?.error && <Alert type="error" showIcon message={t('Plugin unavailable: {error}', { error: plugin.error })} />}
            {available === false && <Alert type="warning" showIcon message={t('This plugin is not available in the current deployment; its configuration is shown read-only.')} />}
            {available ? <EngineConfigForm key={`${partitionId}/${item?.plugin}`} schema={plugin!.config_schema ?? parameterSchema(item.config)} value={item?.config ?? {}} onChange={config => onChange({ ...scenario, engines: { ...scenario.engines, [partitionId]: { ...item, config } } })} onValidityChange={valid => setConfigValidity(current => current[partitionId]?.plugin === item.plugin && current[partitionId]?.valid === valid ? current : { ...current, [partitionId]: { plugin: item.plugin, valid } })} /> : <>
              {!plugin && <Alert type="info" showIcon message={t('Plugin "{plugin}" is not in the installed catalog; edit via scenario JSON to keep unknown content.', { plugin: String(item?.plugin ?? '') })} />}
              <Details title={t('Read-only partition configuration')} buttonLabel={t('View configuration JSON')}><pre>{JSON.stringify(item, null, 2)}</pre></Details>
            </>}
            {available && key === 'weather' && item.config?.profiles && <ExpressionControl label={t('Weather profiles')} value={item.config.profiles} onChange={profiles => onChange({ ...scenario, engines: { ...scenario.engines, [partitionId]: { ...item, config: { ...item.config, profiles } } } })} />}
          </Card>;
        })}
      </div>,
    }))} />
    <section className="guided-ownership" aria-label={t('Field writers and replacement profiles')}>
      <h4>{t('Who controls each value')}</h4>
      <p className="guided-ownership-note">{t('Each field has one writer: the engine partition allowed to update it. Changed settings apply to the next run, after validation.')}</p>
      <Table size="small" className="guided-ownership-table" rowKey={(entry: any) => `${entry.section}/${entry.index}/${entry.row?.entity ?? entry.row?.type ?? ''}`} pagination={bindings.length > 8 ? { pageSize: 8, showSizeChanger: false } : false} dataSource={bindings} columns={columns} locale={{ emptyText: <Empty description={t('No field bindings declared in this scenario')} /> }} />
      <p>{t('Advanced replacement changes the plugin for a whole engine partition. It does not transfer writers during a running simulation.')}</p>
      <Details title={t('Replace a partition plugin')} buttonLabel={t('Advanced plugin replacement')}>
        <Card size="small" className="guided-replace-profile" title={t("Replace a partition's plugin profile")}>
          <p>{t('Choose a partition, review the installed plugin and its configuration, then apply. This replacement draft does not affect validation until you apply it.')}</p>
          <Space wrap direction="vertical" className="guided-replace-controls">
            <Space wrap>
              <Select aria-label={t('Profile partition')} placeholder={t('Select partition')} className="guided-partition-select" value={replacePartition || undefined} allowClear options={partitions.map(([id, item]) => ({ value: id, label: readName(id, item) }))} onChange={value => {
                const id = value ?? '', current = scenario.engines?.[id];
                setReplacePartition(id); setReplacePlugin(current?.plugin ?? ''); setReplaceConfig(current?.config ?? {}); setReplaceConfigValid(false); setRequiredCommands(''); setApplyError(undefined);
              }} />
              <Select aria-label={t('Replacement profile')} placeholder={t('Select installed profile')} className="guided-plugin-select" value={replacePlugin || undefined} allowClear disabled={!partitionExists} options={engines.map(item => ({ value: item.id, label: item.available ? item.id : t('{plugin} · unavailable', { plugin: item.id }), disabled: !item.available }))} onChange={value => { setReplacePlugin(value ?? ''); setReplaceConfig({}); setReplaceConfigValid(false); setApplyError(undefined); }} />
              <Input aria-label={t('Required capabilities')} placeholder={t('Required command schema IDs (comma-separated)')} value={requiredCommands} onChange={event => setRequiredCommands(event.target.value)} className="guided-replace-commands" />
            </Space>
            <p>{t('Optional: list command schema IDs that this replacement must support. Leave blank if you only need the declared field writers.')}</p>
            {descriptor && <>
              <p className="guided-replace-description">{engineDescriptorText(descriptor.description, t)}</p>
              {descriptor.available ? <EngineConfigForm key={`${replacePartition}/${replacePlugin}`} schema={descriptor.config_schema} value={replaceConfig} onChange={setReplaceConfig} onValidityChange={setReplaceConfigValid} /> : <pre>{JSON.stringify(replaceConfig, null, 2)}</pre>}
              <Details title={t('Installed capability descriptor')} buttonLabel={t('Capability descriptor')}><pre>{descriptor.capability_descriptor ? JSON.stringify(descriptor.capability_descriptor, null, 2) : t('Not advertised by this plugin')}</pre></Details>
            </>}
            {descriptor && !descriptor.available && <Alert type="warning" showIcon message={t('This plugin is unavailable here; applying it is blocked until the service is installed.')} />}
            {descriptor && replacementChecks.map(issue => <Alert key={issue.message} type={issue.blocking ? 'error' : 'info'} showIcon message={issue.message} />)}
            {unavailableCatalog.length > 0 && <Collapse ghost className="guided-unavailable" items={[{ key: 'unavailable', label: t('Unavailable alternatives ({count})', { count: unavailableCatalog.length }), children: <ul className="guided-unavailable-list">{unavailableCatalog.map(item => <li key={item.id}><code>{item.id}</code> — {item.error ?? t('Not installed or service unreachable')}</li>)}</ul> }]} />}
            {!engines.some(engine => engine.available) && <Alert type="warning" showIcon message={t('No installed plugins are currently available; profiles cannot be applied.')} />}
            <Button type="primary" htmlType="button" disabled={!partitionExists || !descriptor?.available || !replaceConfigValid || blockingChecks.length > 0} onClick={applyProfile}>{t('Apply profile to draft')}</Button>
            {applyErrorText && <Alert role="alert" type="error" showIcon message={applyErrorText} />}
          </Space>
        </Card>
      </Details>
    </section>
  </div>;
}
export default DomainPluginsStep;

const parameterTitles: Record<string, string> = {
  max_speed_m_s: 'Max speed (m/s)', max_accel_m_s2: 'Max acceleration (m/s²)',
  reserve_j: 'Energy reserve (J)', step_ns: 'Update interval (ns)',
  'energy.idle_w': 'Idle power (W)', 'energy.per_m_j': 'Energy per metre (J/m)',
  'energy.capacity_j': 'Energy capacity (J)',
};
/** Expose only existing physical parameters; never invent descriptor defaults. */
export function parameterSchema(config: Row): unknown {
  if (!config) return undefined;
  const keys = ['max_speed_m_s', 'max_accel_m_s2', 'reserve_j', 'energy', 'step_ns'];
  const properties: Record<string, unknown> = {};
  for (const key of keys) {
    const value = config[key];
    if (typeof value === 'number') properties[key] = { type: key === 'step_ns' ? 'integer' : 'number', title: parameterTitles[key], ...(key === 'step_ns' ? { description: 'One second is 1,000,000,000 nanoseconds.' } : {}) };
    else if (value && typeof value === 'object' && !Array.isArray(value)) {
      const children = Object.fromEntries(Object.entries(value).filter(([, child]) => typeof child === 'number').map(([name]) => [name, { type: 'number', title: parameterTitles[`${key}.${name}`] }]));
      if (Object.keys(children).length) properties[key] = { type: 'object', properties: children };
    }
  }
  return Object.keys(properties).length ? { type: 'object', properties } : undefined;
}
