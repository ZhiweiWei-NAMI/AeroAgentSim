import { useEffect, useState } from 'react';
import { Alert, Button, Card, Input, Modal, Popconfirm, Select, Space, Table, Tag } from 'antd';
import { useI18n } from '../../i18n/I18nProvider';
import { Details } from '../../console/Details';
import { ExpressionControl } from '../../behaviours/ExpressionControl';
import { AuthoringScope } from '../../behaviours/BehaviourEditor';
import { readableName } from '../guided-model';
import type { Workspace } from '../api';
import type { GuidedStepProps } from './PredicatesStep';
import { mapping } from '../../behaviours/model';
import { parseAuthoringJson } from '../../feeds/lossless-json';

const DECISION_PLUGINS = new Set(['decision', 'langgraph', 'traffic_decisions']);
function DecisionAgents({ scenario, onChange }: { scenario: Record<string, any>; onChange: (scenario: Record<string, any>) => void }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<{ existing?: string; id: string; plugin: string; text: string }>();
  const engines = scenario.engines ?? {};
  const rows = Object.entries(engines).flatMap(([id, engine]) => mapping(engine) && DECISION_PLUGINS.has(String(engine.plugin)) ? [{ id, engine }] : []);
  let config: unknown, problem = '';
  if (draft) {
    try { config = parseAuthoringJson(draft.text); if (!mapping(config)) problem = t('Agent configuration must be an object.'); }
    catch { problem = t('Invalid JSON. Check commas, quotes and brackets.'); }
  }
  const id = draft?.id.trim() ?? '';
  const duplicate = Boolean(id && id !== draft?.existing && id in engines);
  const apply = () => {
    if (!draft || !id || duplicate || problem || !mapping(config)) return;
    const next = { ...engines };
    const original = draft.existing ? next[draft.existing] : {};
    if (draft.existing) delete next[draft.existing];
    next[id] = { ...original, plugin: draft.plugin, config };
    onChange({ ...scenario, engines: next }); setDraft(undefined);
  };
  return <Card title={t('Decision agents')} extra={<Button onClick={() => setDraft({ id: '', plugin: 'langgraph', text: '{}' })}>{t('Add agent')}</Button>}>
    <p className="guided-muted">{t('Agents are optional. Add a configured decision engine, edit its settings, or remove it. Validate the draft before running; referenced routes and permissions are not changed automatically.')}</p>
    <Table size="small" rowKey="id" pagination={false} dataSource={rows} locale={{ emptyText: t('No decision agents configured.') }} columns={[
      { title: t('Agent engine ID'), dataIndex: 'id' },
      { title: t('Decision engine'), key: 'plugin', render: (_, row) => row.engine.plugin === 'langgraph' ? 'LangGraph' : row.engine.plugin === 'traffic_decisions' ? t('Scripted decisions') : t('Decision engine') },
      { title: t('Actions'), key: 'actions', render: (_, row) => <Space wrap><Button onClick={() => setDraft({ existing: row.id, id: row.id, plugin: String(row.engine.plugin), text: JSON.stringify(row.engine.config, null, 2) })}>{t('Edit agent')}</Button><Popconfirm title={t('Remove this decision agent?')} description={t('Other references are kept. Validate the draft after removal.')} okText={t('Remove')} cancelText={t('Cancel')} onConfirm={() => { const next = { ...engines }; delete next[row.id]; onChange({ ...scenario, engines: next }); }}><Button danger>{t('Remove agent')}</Button></Popconfirm></Space> },
    ]} />
    <Modal closable={{ 'aria-label': t('Close') }} title={t('Agent settings')} open={!!draft} onCancel={() => setDraft(undefined)} onOk={apply} okText={draft?.existing ? t('Apply agent changes') : t('Add agent')} cancelText={t('Cancel')} okButtonProps={{ disabled: !id || duplicate || !!problem || !mapping(config) }} destroyOnClose>
      {draft && <div className="guided-form"><label>{t('Agent engine ID')}<Input aria-label={t('Agent engine ID')} value={draft.id} disabled={!!draft.existing} onChange={event => setDraft({ ...draft, id: event.target.value })} /></label>
        <label>{t('Agent plugin')}<Select aria-label={t('Agent plugin')} value={draft.plugin} onChange={plugin => setDraft({ ...draft, plugin })} options={[{ value: 'langgraph', label: 'LangGraph' }, { value: 'decision', label: t('Decision engine') }, ...(scenario.id === 'traffic-accident' ? [{ value: 'traffic_decisions', label: t('Scripted decisions') }] : [])]} /></label>
        <p className="guided-muted">{t('Advanced configuration: paste the explicit settings for this plugin. No model, provider or API key is supplied automatically. Validation checks missing settings and permissions.')}</p>
        <label>{t('Agent configuration JSON')}<Input.TextArea aria-label={t('Agent configuration JSON')} aria-invalid={!!problem} rows={10} value={draft.text} onChange={event => setDraft({ ...draft, text: event.target.value })} /></label>
        {(problem || duplicate) && <Alert type="error" message={duplicate ? t('This engine ID already exists. Choose a different name.') : problem} />}
      </div>}
    </Modal>
  </Card>;
}
interface Props extends GuidedStepProps { profiles?: Record<string, Record<string, unknown>>; onValidityChange?: (valid: boolean) => void }
export function AgentsStep({ workspace, api, save, onApply, onChange, onValidityChange }: Props) {
  const { t } = useI18n();
  const decisions = workspace.scenario.engines?.decisions, config = decisions?.config ?? {};
  const [provider, setProvider] = useState({ profile: String(config?.provider?.profile ?? '') }), [error, setError] = useState(''), [busy, setBusy] = useState(false), [grantKind, setGrantKind] = useState('fields');
  useEffect(() => { setProvider({ profile: String(config?.provider?.profile ?? '') }); }, [workspace.id, config?.provider?.profile]);
  const live = decisions?.plugin === 'langgraph', trafficDemo = workspace.scenario.id === 'traffic-accident';
  const mode = !decisions ? t('No decision plugin') : live ? config?.provider?.mode === 'live' ? t('Live LangGraph') : config?.provider?.mode === 'stub' ? t('Recorded model responses') : 'LangGraph' : decisions.plugin === 'traffic_decisions' && config?.mode === 'stub' ? t('Scripted decisions') : readableName(decisions.plugin);
  const changeConfig = (next: Record<string, unknown>) => onChange({ ...workspace.scenario, engines: { ...workspace.scenario.engines, decisions: { ...decisions, config: next } } });
  const apply = async (body: unknown) => { setBusy(true); setError(''); try { await save(); onApply(await api.request<Workspace>(`/v1/studio/workspaces/${workspace.id}/decision-profile`, body)); } catch (problem) { setError(String(problem)); } finally { setBusy(false); } };
  const triggers = Array.isArray(config?.triggers) ? config.triggers : [];
  const scriptedPoints = config?.latency_ns ? Object.keys(config.latency_ns) : [];
  const grantLabels: Record<string, string> = { fields: t('Fields'), relations: t('Relations'), commands: t('Commands'), facts: t('Facts'), events: t('Events') };
  const grants = Object.entries(config?.grants ?? {}).flatMap(([kind, rows]) => Array.isArray(rows) ? rows.map((row: any, index) => ({ key: `${kind}/${index}`, kind, subject: row.entity ?? row.source ?? row.schema ?? row.relation, field: row.field ?? row.topic ?? row.target, row })) : []);
  return <AuthoringScope onValidityChange={onValidityChange}><div className="guided-root">
    <DecisionAgents scenario={workspace.scenario} onChange={onChange} />
    {trafficDemo && !decisions && <Button disabled={busy} onClick={() => void apply({ profile: 'stub' })}>{t('Restore scripted demo agents')}</Button>}
    <Card title={t('Decision profile')} extra={<Tag color={live ? 'blue' : 'default'}>{mode}</Tag>}>
      <p className="guided-muted">{t('Decision agents propose reports and bids. Behaviour chains admit the proposal and apply state updates through the assigned field writers.')}</p>
      {!decisions ? <Alert type="info" message={t('No decision plugin configured')} description={t('This scenario can run with its domain controllers. A decision agent is optional; advanced setup is available in the scenario source.')} /> : <>
        <div className="guided-agent-summary"><div><span className="guided-label">{t('Decision engine')}</span><strong>{readableName(decisions.plugin)}</strong></div><div><span className="guided-label">{t('Decision points')}</span><strong>{triggers.length ? triggers.map((row: any) => readableName(row.schema)).join(' · ') : scriptedPoints.length ? scriptedPoints.map(readableName).join(' · ') : t('No triggers configured')}</strong></div></div>
        {trafficDemo && <Button disabled={busy} onClick={() => void apply({ mode: 'scripted' })}>{t('Use scripted decisions')}</Button>}
        <Details title={t('Decision profile source')} buttonLabel={t('Profile source')}><pre>{JSON.stringify(decisions, null, 2)}</pre></Details>
      </>}
    </Card>
    {(trafficDemo || live) && <Card title={t('LangGraph model')}>
      <p className="guided-muted">{t('Select a provider profile configured by the server operator for the next run.')}</p>
      {trafficDemo && <p className="guided-muted">{t('Use a server-side profile name; do not paste an API key here.')}</p>}
      {trafficDemo ? <div className="guided-form"><label>{t('Provider profile')}<Input aria-label={t('Live provider profile')} value={provider.profile} placeholder={t('e.g. research')} onChange={event => setProvider({ profile: event.target.value })} /></label><Button loading={busy} disabled={!provider.profile.trim()} onClick={() => void apply({ provider: { profile: provider.profile.trim() } })}>{t('Use live LangGraph for next run')}</Button></div> : <p>{t('The quick provider switch is available for the traffic accident demo. Configure this scenario’s existing provider in Advanced agent settings.')}</p>}
      {error && <Alert type="error" showIcon message={t('Decision profile could not be applied')} description={error} />}
      {trafficDemo && <Details title={t('Decision profile endpoint')} buttonLabel={t('Technical details')}><code>{`POST /v1/studio/workspaces/${workspace.id}/decision-profile`}</code></Details>}
    </Card>}
    {live && <Card title={t('Graph & tool access')}>
      <p className="guided-muted">{t('Tool access determines which values the agent can observe and which commands it can request. Only grant the access your scenario needs.')}</p>
      <h4>{t('Granted tools and observations')}</h4>
      <Table size="small" rowKey="key" pagination={{ pageSize: 8 }} dataSource={grants} locale={{ emptyText: t('No tool access granted yet.') }} columns={[
        { title: t('Access'), dataIndex: 'kind', render: (kind: string) => grantLabels[kind] ?? readableName(kind) },
        { title: t('Entity / event'), dataIndex: 'subject', render: (id: string) => id ? readableName(id) : t('Not configured') },
        { title: t('Field / topic'), dataIndex: 'field', render: (id: string) => id ? readableName(id) : t('Not configured') },
        { title: t('Details'), key: 'details', render: (_, row) => <Details title={t('Tool grant')} buttonLabel={t('Details')}><pre>{JSON.stringify(row.row, null, 2)}</pre></Details> },
      ]} />
      <Details title={t('Edit tool access')} buttonLabel={t('Edit tool access')}><p>{t('Grants are lists of allowed observations and actions. Keep the IDs from your registry and validate after editing.')}</p><Select aria-label={t('Agent grant category')} value={grantKind} onChange={setGrantKind} options={Object.entries(grantLabels).map(([value, label]) => ({ value, label }))} /><ExpressionControl label={t('Agent tool grants')} value={config.grants?.[grantKind]} onChange={next => changeConfig({ ...config, grants: { ...config.grants, [grantKind]: next } })} /></Details>
      <Details title={t('Advanced agent settings')} buttonLabel={t('Advanced agent settings')}>
        <p>{t('These settings select the agent implementation, prompt, execution limits and model provider. Use the values supplied by your server operator.')}</p>
        <div className="guided-form"><label>{t('Graph factory')}<Input aria-label={t('Agent graph factory')} value={config.factory} onChange={event => changeConfig({ ...config, factory: event.target.value })} /></label>{config.options?.prompt_name !== undefined && <label>{t('Prompt name')}<Input aria-label={t('Prompt name')} value={config.options.prompt_name} onChange={event => changeConfig({ ...config, options: { ...config.options, prompt_name: event.target.value } })} /></label>}</div>
        <ExpressionControl label={t('Agent options')} value={config.options} onChange={next => changeConfig({ ...config, options: next })} /><ExpressionControl label={t('Agent budget')} value={config.budget} onChange={next => changeConfig({ ...config, budget: next })} /><ExpressionControl label={t('Agent provider')} value={config.provider} onChange={next => changeConfig({ ...config, provider: next })} />
      </Details>
    </Card>}
  </div></AuthoringScope>;
}
