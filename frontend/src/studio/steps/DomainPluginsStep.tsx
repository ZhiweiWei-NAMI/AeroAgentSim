import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Collapse, Empty, Input, Select, Space, Spin, Table, Tag } from 'antd';
import { ExpressionControl } from '../../behaviours/ExpressionControl';
import { readableName } from '../guided-model';
import { Details } from '../../console/Details';
import { EngineConfigForm } from '../EngineConfigForm';
import { profileIssues, type EngineDescriptor } from '../PluginOwnershipPanel';

/**
 * DomainPluginsStep — Studio step that groups real scenario engine partitions into readable domain
 * cards (air / road / network / weather / other), reuses EngineConfigForm for plugin configuration,
 * and presents field ownership bindings as a compact paginated antd table with inline conflicts.
 *
 * Honesty rules:
 * - Domain grouping is derived from actual partition IDs and plugin capabilities; unknown partitions
 *   land in "Other domains" and survive edits untouched.
 * - Unavailable plugins are shown as unavailable; capability gaps are reported, never masked as
 *   success, and unknown values are never rendered as false / zero.
 * - All edits preserve unknown scenario sections (spread of the original document).
 */

type Row = Record<string, any>;

export interface DomainPluginsStepProps {
  scenario: Row;
  onChange: (scenario: Row) => void;
  engines: EngineDescriptor[];
  onValidityChange?: (valid: boolean) => void;
}

interface DomainSpec { key: string; title: string; match: RegExp }

/** Readable domain cards; match on actual partition keys present in the scenario. */
const DOMAINS: DomainSpec[] = [
  { key: 'air', title: 'Air motion & energy', match: /air|uav|kinematic/i },
  { key: 'road', title: 'Road traffic', match: /road|vehicle_motion|sumo/i },
  { key: 'network', title: 'Network', match: /network|ns3|ns-3/i },
  { key: 'weather', title: 'Weather & environment', match: /weather|environment|wind|climate/i },
];

const domainOf = (partitionId: string): string => {
  const hit = DOMAINS.find(d => d.match.test(partitionId));
  return hit?.key ?? 'other';
};

const readName = (partitionId: string, item: Row): string => {
  const plugin = typeof item?.plugin === 'string' ? item.plugin : '';
  return plugin ? `${readableName(partitionId)} · ${readableName(plugin)}` : readableName(partitionId);
};

export function DomainPluginsStep({ scenario, onChange, engines, onValidityChange }: DomainPluginsStepProps) {
  const partitions = Object.entries(scenario?.engines ?? {}) as Array<[string, Row]>;
  const engineById = useMemo(() => new Map(engines.map(e => [e.id, e])), [engines]);

  const [openDomains, setOpenDomains] = useState<string[]>(['air']);
  const [configValidity,setConfigValidity]=useState<Record<string,boolean>>({});
  const [replacePartition, setReplacePartition] = useState('');
  const [replacePlugin, setReplacePlugin] = useState('');
  const [replaceConfig, setReplaceConfig] = useState<Record<string, unknown>>({});
  const [replaceConfigValid, setReplaceConfigValid] = useState(true);
  const [requiredCommands, setRequiredCommands] = useState('');
  const [applyError, setApplyError] = useState('');

  // Compact ownership table (replaces the old enormous native table).
  const bindings = useMemo(() => ([
    ...((scenario?.bindings?.exact ?? []) as Row[]).map((row, index) => ({ section: 'exact' as const, index, row })),
    ...((scenario?.bindings?.rules ?? []) as Row[]).map((row, index) => ({ section: 'rules' as const, index, row })),
  ]), [scenario?.bindings?.exact, scenario?.bindings?.rules]);

  const configuredPlugins = useMemo(() => partitions.map(([, item]) => item?.plugin).filter(p => typeof p === 'string'), [partitions]);

  /** Per-row inline conflicts from real capability descriptors — unavailable is reported, not suppressed. */
  const rowConflicts = (row: Row): string[] => {
    const writer = String(row?.writer ?? '');
    const index = partitions.findIndex(([id]) => id === writer);
    const plugin = index >= 0 ? partitions[index][1]?.plugin : undefined;
    const descriptor = typeof plugin === 'string' ? engineById.get(plugin) : undefined;
    const fields: string[] = row.field ? [row.field] : Array.isArray(row.fields) ? row.fields : [];
    const commands: string[] = [];
    if (row.command) commands.push(String(row.command));
    return profileIssues(descriptor, fields, commands, configuredPlugins);
  };

  useEffect(() => { onValidityChange?.(Object.values(configValidity).every(Boolean) && replaceConfigValid); }, [configValidity,replaceConfigValid,onValidityChange]);

  const setWriter = (section: 'exact' | 'rules', index: number, writer: string) =>
    onChange({
      ...scenario,
      bindings: {
        ...scenario.bindings,
        [section]: scenario.bindings[section].map((row: Row, i: number) => i === index ? { ...row, writer } : row),
      },
    });

  const applyProfile = () => {
    setApplyError('');
    if (!replacePartition || !replacePlugin) { setApplyError('Select a partition and an installed plugin first'); return; }
    const descriptor = engineById.get(replacePlugin);
    if (!descriptor?.available) { setApplyError('Selected plugin is not available in this deployment'); return; }
    if (!replaceConfigValid) { setApplyError('Correct the plugin configuration before applying'); return; }
    if (blockingChecks.length) { setApplyError(blockingChecks.join('; ')); return; }
    try {
      onChange({
        ...scenario,
        engines: {
          ...scenario.engines,
          [replacePartition]: { ...scenario.engines[replacePartition], plugin: replacePlugin, config: replaceConfig },
        },
      });
    } catch (problem) { setApplyError(String(problem)); }
  };

  const descriptor = engineById.get(replacePlugin);
  const replacementFields = bindings.filter(entry => entry.row.writer === replacePartition).flatMap(entry => entry.row.field ? [entry.row.field] : entry.row.fields ?? []);
  const replacementChecks = profileIssues(descriptor, replacementFields, requiredCommands.split(',').map(value=>value.trim()).filter(Boolean), partitions.filter(([id])=>id!==replacePartition).map(([,item])=>item.plugin).concat(replacePlugin));
  const blockingChecks = replacementChecks.filter(issue=>!issue.includes('unavailable') && !issue.includes('requires server validation'));
  const availableCatalog = engines.filter(e => e.available);
  const unavailableCatalog = engines.filter(e => !e.available);

  const domains = useMemo(() => {
    const map = new Map<string, Array<[string, Row]>>(DOMAINS.map(domain=>[domain.key,[]]));
    partitions.forEach(([id, item]) => {
      const key = domainOf(id);
      const arr = map.get(key) ?? []; arr.push([id, item]); map.set(key, arr);
    });
    return [...map.entries()].sort((a, b) => (a[0] === 'other' ? 1 : 0) - (b[0] === 'other' ? 1 : 0));
  }, [partitions]);

  const title = (key: string): string => key === 'other' ? 'Other configured domains' : (DOMAINS.find(d => d.key === key)?.title ?? key);

  const columns = [
    { title: 'Entity / type', key: 'selector', render: (_: unknown, entry: { section: 'exact' | 'rules'; index: number; row: Row }) =>
      <span>{readableName(String(entry.row?.entity ?? entry.row?.type ?? 'Unspecified'))}{entry.row?.ids ? <code className="guided-ownership-ids"> / {String(entry.row.ids)}</code> : null}</span> },
    { title: 'Fields', key: 'fields', render: (_: unknown, entry: { row: Row }) =>
      <span>{entry.row?.field ? readableName(entry.row.field) : Array.isArray(entry.row?.fields) ? entry.row.fields.map(readableName).join(', ') : 'Unspecified'}</span> },
    { title: 'Writer', key: 'writer', render: (_: unknown, entry: { section: 'exact' | 'rules'; index: number; row: Row }) =>
      <Select
        size="small"
        aria-label={`Writer ${entry.section} ${entry.index}`}
        value={String(entry.row?.writer ?? '')}
        className="guided-writer-select"
        options={partitions.map(([id, item]) => ({ value: id, label: readName(id, item) }))}
        onChange={value => setWriter(entry.section, entry.index, value)}
      /> },
    { title: 'Status', key: 'status', render: (_: unknown, entry: { row: Row }) => {
      const conflicts = rowConflicts(entry.row);
      if (!conflicts.length) return <Tag color="success">Compatible</Tag>;
      const blocking = conflicts.filter(issue => !issue.includes('unavailable') && !issue.includes('requires server validation'));
      return <Space size="small" wrap>
        {blocking.length === 0 ? <Tag>needs server validation</Tag> : <Tag color="red">conflict</Tag>}
        <Details title="Ownership check" buttonLabel="Checks">
          <ul className="guided-ownership-issues">{conflicts.map(issue => <li key={issue}>{issue}</li>)}</ul>
        </Details>
      </Space>;
    } },
  ];

  return <div className="guided-domain-plugins">
    <Collapse
      className="guided-domain-cards"
      activeKey={openDomains}
      onChange={keys => setOpenDomains(Array.isArray(keys) ? keys : [keys])}
      items={domains.map(([key, items]) => ({
        key,
        label: <span className="guided-domain-label">{title(key)} <Tag>{items.length}</Tag></span>,
        children: <div className="guided-domain-body">
          {!items.length && <Card title="No engine configured"><p className="guided-muted">Add a domain plugin to this draft, then configure its fields and validate before running.</p><Select aria-label={`Add ${key} engine`} placeholder="Choose a domain engine" options={engines.filter(engine=>key==='network'?/ns3|ns-3|network|ideal/.test(engine.id):key==='weather'?/weather|environment/.test(engine.id):key==='air'?/kinematic|px4|gazebo/.test(engine.id):/sumo|road_motion/.test(engine.id)).map(engine=>({value:engine.id,label:`${readableName(engine.id)}${engine.available?'':' · unavailable'}`,disabled:!engine.available}))} onChange={plugin=>onChange({...scenario,engines:{...scenario.engines,[key]:{plugin,config:{}}}})}/></Card>}
          {items.map(([partitionId, item]) => {
            const plugin = engineById.get(String(item?.plugin));
            const available = plugin?.available;
            return <Card key={partitionId} size="small" className="guided-domain-partition" title={readName(partitionId, item)}
              extra={available === undefined ? <Tag>plugin not in catalog</Tag> : available ? <Tag color="green">available</Tag> : <Tag color="red">unavailable</Tag>}>
              <label className="guided-domain-engine-label">Engine<Select aria-label={`Engine ${partitionId}`} value={item.plugin} options={engines.filter(engine=>engine.id===item.plugin || (key==='air' ? /kinematic|px4|gazebo/.test(engine.id) : key==='road' ? /sumo|road_motion/.test(engine.id) : key==='network' ? /network|ns3|ns-3|ideal/.test(engine.id) : key==='weather' ? /weather|environment/.test(engine.id) : true)).map(engine=>({value:engine.id,label:`${readableName(engine.id)}${engine.available?'':' · unavailable'}`,disabled:!engine.available}))} onChange={next=>onChange({...scenario,engines:{...scenario.engines,[partitionId]:{...item,plugin:next,config:{}}}})}/></label>
              {plugin?.error && <Alert type="error" showIcon message={plugin.error} />}
              {available === false && <Alert type="warning" showIcon message="This plugin is not available in the current deployment; its configuration is shown read-only." />}
              {plugin
                ? <EngineConfigForm
                    key={`${partitionId}/${item?.plugin}`}
                    schema={plugin.config_schema ?? parameterSchema(item.config)}
                    value={(item?.config ?? {}) as Record<string, unknown>}
                    onChange={config => onChange({
                      ...scenario,
                      engines: { ...scenario.engines, [partitionId]: { ...item, config } },
                    })}
                    onValidityChange={valid=>setConfigValidity(current=>current[partitionId]===valid?current:{...current,[partitionId]:valid})}
                  />
                : <Alert type="info" showIcon message={`Plugin "${String(item?.plugin ?? '')}" is not in the installed catalog; edit via scenario JSON to keep unknown content.`} />}
              {key==='weather' && item.config?.profiles && <ExpressionControl label="Weather profiles" value={item.config.profiles} onChange={profiles=>onChange({...scenario,engines:{...scenario.engines,[partitionId]:{...item,config:{...item.config,profiles}}}})}/>}
              {!plugin && <Details title="Partition raw JSON"><pre>{JSON.stringify(item, null, 2)}</pre></Details>}
            </Card>;
          })}
        </div>,
      }))}
    />

    <section className="guided-ownership" aria-label="Field writers and replacement profiles">
      <h4>Field writers / replacement profiles</h4>
      <p className="guided-ownership-note">
        Each field has one writer. Profile changes apply to a new run after validation.
      </p>
      <Table
        size="small"
        className="guided-ownership-table"
        rowKey={(entry: any) => `${entry.section}/${entry.index}/${entry.row?.entity ?? entry.row?.type ?? ''}`}
        pagination={bindings.length > 8 ? { pageSize: 8 } : false}
        dataSource={bindings}
        columns={columns}
        locale={{ emptyText: <Empty description="No field bindings declared in this scenario" /> }}
      />

      <Card size="small" className="guided-replace-profile" title="Replace a partition's plugin profile">
        <Space wrap direction="vertical" className="guided-replace-controls">
          <Space wrap>
            <Select
              aria-label="Profile partition"
              placeholder="Select partition"
              className="guided-partition-select"
              value={replacePartition || undefined}
              allowClear
              options={partitions.map(([id]) => ({ value: id, label: readName(id, scenario.engines?.[id]) }))}
              onChange={value => { setReplacePartition(value ?? ''); setReplacePlugin(''); setReplaceConfig({}); setApplyError(''); }}
            />
            <Select
              aria-label="Replacement profile"
              placeholder="Select installed profile"
              className="guided-plugin-select"
              value={replacePlugin || undefined}
              allowClear
              options={engines.map(item => ({ value: item.id, label: `${item.id}${item.available ? '' : ' · unavailable'}`, disabled: !item.available }))}
              onChange={value => { setReplacePlugin(value ?? ''); setReplaceConfig({}); }}
            />
            <Input
              aria-label="Required capabilities"
              placeholder="Required command schema IDs (comma-separated)"
              value={requiredCommands}
              onChange={e => setRequiredCommands(e.target.value)}
              className="guided-replace-commands"
            />
          </Space>
          {descriptor && <>
            <p className="guided-replace-description">{descriptor.description}</p>
            <EngineConfigForm
              key={`${replacePartition}/${replacePlugin}`}
              schema={descriptor.config_schema}
              value={replaceConfig}
              onChange={setReplaceConfig}
              onValidityChange={setReplaceConfigValid}
            />
            <Details title="Installed capability descriptor" buttonLabel="Capability descriptor">
              <pre>{descriptor.capability_descriptor ? JSON.stringify(descriptor.capability_descriptor, null, 2) : 'Not advertised by this plugin'}</pre>
            </Details>
          </>}
          {descriptor && !descriptor.available && <Alert type="warning" showIcon message="This plugin is unavailable here; applying it is blocked until the service is installed." />}
          {descriptor && replacementChecks.map(issue=><Alert key={issue} type={blockingChecks.includes(issue)?'error':'info'} showIcon message={issue}/>)}
          {unavailableCatalog.length > 0 && (
            <Collapse ghost className="guided-unavailable" items={[{
              key: 'unavailable',
              label: `Unavailable alternatives (${unavailableCatalog.length})`,
              children: <ul className="guided-unavailable-list">
                {unavailableCatalog.map(item => <li key={item.id}><code>{item.id}</code> — {item.error ?? 'not installed or service unreachable'}</li>)}
              </ul>,
            }]} />
          )}
          {availableCatalog.length === 0 && <Alert type="warning" showIcon message="No installed plugins are currently available; profiles cannot be applied." />}
          <Space wrap>
            <Button
              type="primary"
              disabled={!replacePartition || !descriptor?.available || !replaceConfigValid || blockingChecks.length > 0}
              onClick={applyProfile}
            >Apply profile to draft</Button>
          </Space>
          {applyError && <Alert role="alert" type="error" showIcon message={applyError} />}
        </Space>
      </Card>
    </section>
  </div>;
}

export default DomainPluginsStep;

/** Expose existing motion parameters when a plugin has no advertised form schema.
 * These are the actual configured values; compiler validation remains authoritative.
 */
function parameterSchema(config:Row):unknown {
 if(!config) return undefined;
 const keys=['max_speed_m_s','max_accel_m_s2','reserve_j','energy','step_ns'];
 const properties:Record<string,unknown>={};
 for(const key of keys) {
  const value=config[key];
  if(typeof value==='number') properties[key]={type:key==='step_ns'?'integer':'number',title:readableName(key)};
  else if(value&&typeof value==='object'&&!Array.isArray(value)) {
   const children=Object.fromEntries(Object.entries(value).filter(([,child])=>typeof child==='number').map(([name])=>[name,{type:'number',title:readableName(name)}]));
   if(Object.keys(children).length)properties[key]={type:'object',properties:children,title:readableName(key)};
  }
 }
 return Object.keys(properties).length?{type:'object',properties}:undefined;
}
