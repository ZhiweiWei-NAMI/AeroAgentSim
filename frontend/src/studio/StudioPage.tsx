import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Dropdown, Input, InputNumber, Modal, Select, Space, Tag } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { useConsoleNotice } from '../console/Notifications';
import { PageHeader, PageState } from '../console/PageState';
import { Details } from '../console/Details';
import { ConceptHelp } from '../console/ConceptHelp';
import { StudioApi, type Workspace, type TypeRow } from './api';
import { ScenePreview } from './ScenePreview';
import { RegionMap } from './RegionMap';
import type { EngineDescriptor } from './PluginOwnershipPanel';
import { EntitiesStep } from './steps/EntitiesStep';
import { DomainPluginsStep } from './steps/DomainPluginsStep';
import { PredicatesStep } from './steps/PredicatesStep';
import { RulesStep } from './steps/RulesStep';
import { AgentsStep } from './steps/AgentsStep';
import { parseAuthoringJson } from '../feeds/lossless-json';
import { recentWorkspaces, workspaceUpdated } from './workspace-list';
import { issueStep, readableName, registryLabel, studioSteps, validationIssues, type StudioStep, type StudioIssue } from './guided-model';
import { AlgorithmsPanel } from '../newcomers/AlgorithmsPanel';
import type { AlgorithmSlot } from '../newcomers/api';
import './studio.css';
import './guided.css';

/** Only the authored low-altitude task pack consumes the algorithm slots. */
export function scenarioUsesLowaltPack(scenario: Record<string, any> | undefined): boolean {
  return Boolean(scenario && scenario.engines && typeof scenario.engines === 'object' && !Array.isArray(scenario.engines) && Object.values(scenario.engines).some((engine: any) => engine?.plugin === 'lowalt_tasks'));
}
interface Catalog { demo_profiles?: Record<string, Record<string, unknown>>; extracts: Array<{ id: string; name: string; name_zh?: string; bounds: number[] }>; engines: EngineDescriptor[] }

export function workspaceUrl(search: string, id: string): string {
  const query = new URLSearchParams(search);
  query.set('workspace', id);
  for (const key of ['template', 'entity', 'guide']) query.delete(key);
  return `/studio?${query}`;
}

export default function StudioPage() {
  const { t, locale } = useI18n();
  const location = useLocation(), navigate = useNavigate(), notify = useConsoleNotice();
  const base = new URLSearchParams(location.search).get('api') ?? window.location.origin;
  const api = useMemo(() => new StudioApi(base), [base]);
  const [catalog, setCatalog] = useState<Catalog>(), [workspaces, setWorkspaces] = useState<Workspace[]>([]), [workspace, setWorkspace] = useState<Workspace>();
  const [types, setTypes] = useState<TypeRow[]>([]), [fields, setFields] = useState<Record<string, unknown>[]>([]), [relations, setRelations] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [step, setStep] = useState<StudioStep>('scene'), [visited, setVisited] = useState<Set<StudioStep>>(new Set(['scene']));
  const [validity, setValidity] = useState<Record<string, boolean>>({}), [dirty, setDirty] = useState(false), [yaml, setYaml] = useState(''), [raw, setRaw] = useState(''), [rawError, setRawError] = useState('');
  const [issuesOpen, setIssuesOpen] = useState(false), [focusIssue, setFocusIssue] = useState<StudioIssue>();
  const [extract, setExtract] = useState<string>(), [bounds, setBounds] = useState<number[]>([]), [alt, setAlt] = useState(0), [levelHeight, setLevelHeight] = useState(3), [layers, setLayers] = useState(['ground', 'buildings', 'roads', 'entities', 'airspace']);
  const [placeId, setPlaceId] = useState(''), [placeType, setPlaceType] = useState<string>(), [position, setPosition] = useState([0, 0, 10]);
  const [placementOverride, setPlacementOverride] = useState<string>();
  const launch = useRef<string>();
  const scenario = workspace?.scenario;
  const labels: Record<StudioStep, string> = {
    scene: t('Scene'), entities: t('Entities'), plugins: t('Domain plugins'), predicates: t('Predicates'),
    rules: t('Event chains & rules'), agents: t('Agents'), validate: t('Validate & run'),
  };
  const descriptions: Record<StudioStep, string> = {
    scene: t('Choose a region and place actors in the scene.'),
    entities: t('Browse typed entities, their initial fields and relationships.'),
    plugins: t('Choose the engines that control movement, traffic, communication and weather.'),
    predicates: t('Define the conditions that drive your scenario.'),
    rules: t('Connect predicate transitions to actions and external events.'),
    agents: t('Configure recorded decisions or a live LangGraph model.'),
    validate: t('Check the complete scenario, then start a new run.'),
  };
  const stepHelp: Record<StudioStep, { description: string; guide: string }> = {
    scene: { description: t('The scene is the map and starting positions of the actors in your simulation.'), guide: 'console.md' },
    entities: { description: t('Entities are the actors in your scenario. Their type determines which values and relationships they can have.'), guide: 'console.md' },
    plugins: { description: t('Domain plugins control parts of the simulation. Each changing value must have exactly one controller.'), guide: 'RUNTIME.md' },
    predicates: { description: t('Predicates are true-or-false conditions. A change in a condition can start an event chain.'), guide: 'predicates.md' },
    rules: { description: t('Event chains describe what happens next: wait for a condition or event, then run actions.'), guide: 'behaviours.md' },
    agents: { description: t('Decision agents propose actions. The same safety checks and controllers apply to their proposals.'), guide: 'langgraph.md' },
    validate: { description: t('Validation checks your complete draft. A new run records its own timeline without changing previous runs.'), guide: 'console.md' },
  };
  const placementTypes = [...types, ...(workspace?.registry_catalog?.types ?? []), ...(scenario?.registry?.types ?? [])];
  const ancestry = new Set<string>(), pending = placeType ? [placeType] : [];
  while (pending.length) { const id = pending.pop()!; if (ancestry.has(id)) continue; ancestry.add(id); const row = placementTypes.find(row => row.id === id); pending.push(...(row?.parents ?? []), ...(row?.ancestors ?? [])); }
  const moving = ['oo:UAV', 'oo:Aircraft', 'oo:GroundVehicle', 'oo:Vehicle'].some(id => ancestry.has(id));
  const automaticPlacement = moving ? 'kinematic' : 'workflow';
  const effectivePlacement = placementOverride ?? automaticPlacement;
  const duplicatePlaceId = Boolean(scenario?.entities?.some((row: { id: string }) => row.id === placeId.trim()));
  const action = async (fn: () => Promise<void>) => { setBusy(true); setError(''); try { await fn(); } catch (problem) { const message = String(problem); setError(message); notify({ kind: 'error', message: t('Studio request failed: {error}', { error: message }) }); } finally { setBusy(false); } };
  const apply = (next: Workspace) => {
    setWorkspace(next); setRaw(JSON.stringify(next.scenario, null, 2)); setRawError(''); setDirty(false);
    if (next.region) { setExtract(next.region.extract); setBounds(next.region.bounds); setAlt(next.region.alt); setLevelHeight(next.region.level_height_m); }
    else { setExtract(undefined); setBounds([]); setAlt(0); setLevelHeight(3); }
    if (workspace?.id !== next.id) { setPlaceId(''); setPlaceType(undefined); setPlacementOverride(undefined); setVisited(new Set(['scene'])); setStep('scene'); setFocusIssue(undefined); setIssuesOpen(false); setYaml(''); }
    setWorkspaces(previous => recentWorkspaces([...previous.filter(row => row.id !== next.id), next]));
  };
  const onChange = (scenario: Record<string, any>) => { setWorkspace(current => current ? { ...current, scenario, validation: undefined } : current); setRaw(JSON.stringify(scenario, null, 2)); setRawError(''); setDirty(true); };
  const behaviourValidity = useCallback((valid: boolean) => setValidity(current => ({ ...current, rules: valid })), []);
  const predicateValidity = useCallback((valid: boolean) => setValidity(current => ({ ...current, predicates: valid })), []);
  const pluginValidity = useCallback((valid: boolean) => setValidity(current => ({ ...current, plugins: valid })), []);
  const entityValidity = useCallback((valid: boolean) => setValidity(current => ({ ...current, entities: valid })), []);
  const agentValidity = useCallback((valid: boolean) => setValidity(current => ({ ...current, agents: valid })), []);
  const chooseStep = (id: StudioStep) => { setStep(id); setVisited(current => new Set([...current, id])); setFocusIssue(undefined); };
  const openIssue = (issue: StudioIssue) => { chooseStep(issueStep(issue)); setFocusIssue(issue); setIssuesOpen(false); };
  const buffersValid = Object.values(validity).every(Boolean) && !rawError;
  const save = async (): Promise<Workspace> => {
    if (!workspace) throw Error(t('Open a workspace first'));
    if (!buffersValid) throw Error(t('Correct the invalid editor content before saving'));
    const next = await api.request<Workspace>(`/v1/studio/workspaces/${workspace.id}`, { name: workspace.name, scenario: workspace.scenario }); apply(next); return next;
  };
  const openDemo = async () => { const created = await api.request<Workspace>('/v1/studio/workspaces', { name: t('Traffic accident (demo)') }); apply(await api.request<Workspace>(`/v1/studio/workspaces/${created.id}/templates/traffic-accident`, { console: true, capture_mode: 'city' })); setValidity({}); chooseStep('scene'); };
  useEffect(() => {
    let active = true;
    void Promise.all([api.request<Catalog>('/v1/studio/catalog'), api.request<Workspace[]>('/v1/studio/workspaces'), api.request<{ types: TypeRow[]; fields: Record<string, unknown>[]; relations: Record<string, unknown>[] }>('/v1/studio/types?q=')]).then(([c, w, catalog]) => { if (!active) return; setCatalog(c); setWorkspaces(previous => recentWorkspaces([...w, ...previous])); setTypes(catalog.types); setFields(catalog.fields); setRelations(catalog.relations); }).catch(problem => { if (active) setError(String(problem)); });
    return () => { active = false; };
  }, [api]);
  useEffect(() => {
    const query = new URLSearchParams(location.search), target = query.get('workspace'), key = `${base}/${location.search}`;
    if (launch.current === key || (!target && query.get('template') !== 'traffic-accident')) return;
    launch.current = key;
    void action(async () => { if (target) { const next = await api.request<Workspace>(`/v1/studio/workspaces/${encodeURIComponent(target)}`), entity = query.get('entity'); if (entity && !next.scenario.entities?.some((row: { id: string }) => row.id === entity)) throw Error(t('The linked entity is absent from this workspace')); apply(next); if (entity) chooseStep('entities'); } else await openDemo(); setValidity({}); });
  }, [api, location.search]);
  const mergeRows = <T extends { id: string }>(...rows: T[][]): T[] => [...new Map(rows.flat().map(row => [row.id, row])).values()];
  const allTypes = mergeRows(types, workspace?.registry_catalog?.types ?? [], scenario?.registry?.types ?? []);
  const allFields = mergeRows(fields as Array<Record<string, unknown> & { id: string }>, workspace?.registry_catalog?.fields ?? [], scenario?.registry?.fields ?? []);
  const allRelations = mergeRows(relations as Array<Record<string, unknown> & { id: string }>, scenario?.registry?.relations ?? []);
  const issues = validationIssues(workspace?.validation);
  const validate = async () => {
    if (!workspace) throw Error(t('Open a workspace first'));
    if (!buffersValid) throw Error(t('Correct the invalid editor content before validation'));
    const result = await api.request<NonNullable<Workspace['validation']>>(`/v1/studio/workspaces/${workspace.id}/validate`, { scenario: workspace.scenario });
    const next = await api.request<Workspace>(`/v1/studio/workspaces/${workspace.id}`); apply({ ...next, validation: result });
    notify({ kind: result.valid ? 'success' : 'error', message: result.valid ? t('Scenario valid. Ready to run.') : t('Validation found authoring errors.') });
    if (!result.valid) chooseStep('validate');
  };
  const run = async () => {
    if (!workspace?.validation?.valid || !buffersValid || dirty) throw Error(t('Validate this draft before starting a run'));
    const receipt = await api.request<{ id: string }>('/v1/runs', { scenario: workspace.scenario, studio_workspace: workspace.id });
    notify({ kind: 'success', message: t('Run started.') }); navigate(`/runs/${encodeURIComponent(receipt.id)}?api=${encodeURIComponent(base)}&mode=live`);
  };
  const switchWorkspace = (id: string) => {
    if (id === workspace?.id) return;
    const open = () => navigate(workspaceUrl(location.search, id));
    if (dirty) Modal.confirm({ title: t('Switch without saving?'), content: t('Your current edits will be discarded. Cancel and save first if you want to keep them.'), okText: t('Discard edits and switch'), cancelText: t('Keep editing'), onOk: open });
    else open();
  };
  const selectedExtract = catalog?.extracts.find(item => item.id === extract);
  const bindingsValid = workspace?.validation?.valid === true && !dirty && buffersValid;
  const runHint = !buffersValid ? t('Correct invalid editor content first') : dirty ? t('Validate your changes first') : t('Validate first');
  const issueCount = (count: number) => count === 1 ? t('1 authoring issue') : t('{count} authoring issues', { count });
  const issueList = (group: StudioStep) => <div className="guided-issues">{issues.filter(issue => issueStep(issue) === group).map((issue, index) => <button key={index} className="guided-issue" onClick={() => openIssue(issue)}><strong>{issue.message}</strong><span>{t('Edit {step}', { step: labels[group] })}</span></button>)}</div>;
  const common = workspace ? { workspace, api, save, onApply: apply, onChange } : undefined;
  const displayType = (row: TypeRow) => {
    const names = row.name?.split('/').map(name => name.trim());
    const preferred = names?.find(name => locale === 'zh-CN' ? /[\u3400-\u9fff]/.test(name) : !/[\u3400-\u9fff]/.test(name));
    return registryLabel(preferred || (locale === 'zh-CN' ? names?.[0] : undefined) || row.id, locale);
  };
  const coordinates = [t('East'), t('North'), t('Up')], regionCoordinates = [t('West'), t('South'), t('East bound'), t('North bound')];
  return <div className="console-page guided-studio" data-testid="guided-studio">
    <PageHeader eyebrow={t('Studio · configuration')} title={scenario ? (localized(scenario, 'title', locale) ?? (scenario.id === 'traffic-accident' ? t('Traffic accident') : readableName(String(scenario.id ?? '')))) : t('Build a scenario')} description={t('Choose the actors, their controllers and the rules that make things happen.')} />
    {error && <Alert type="error" showIcon message={t('Studio could not complete the request')} description={error} closable onClose={() => setError('')} />}
    <div className="guided-workspace-header">
      <div className="guided-workspace-name"><span className="guided-label">{t('Workspace')}</span>{workspace ? <Input aria-label={t('Workspace name')} value={workspace.name} onChange={event => { setWorkspace({ ...workspace, name: event.target.value }); setDirty(true); }} /> : <strong>{t('Select or create a workspace')}</strong>}<span className="guided-muted">{workspace ? `${workspaceUpdated(workspace, locale)}${dirty ? t(' · Unsaved changes') : ''}` : t('A saved draft holds the complete scenario.')}</span></div>
      <Space wrap>
        <Dropdown trigger={['click']} menu={{ items: recentWorkspaces(workspaces).map(item => ({ key: item.id, label: <span className="guided-picker-item"><strong>{item.name}</strong><small>{workspaceUpdated(item, locale)}</small></span> })), onClick: ({ key }) => switchWorkspace(key) }}><Button disabled={!workspaces.length || busy} data-testid="workspace-picker">{t('Switch workspace ▾')}</Button></Dropdown>
        {!workspace && <Button disabled={busy} onClick={() => void action(openDemo)}>{t('Open traffic demo')}</Button>}
        <Button disabled={!workspace || busy || !buffersValid} onClick={() => void action(async () => { await save(); notify({ kind: 'success', message: t('Workspace saved.') }); })}>{t('Save workspace')}</Button>
        <Button disabled={!workspace || busy || !buffersValid} onClick={() => void action(validate)}>{t('Validate')}</Button>
        <span className="guided-run-now-wrap"><Button type="primary" disabled={busy || !bindingsValid} onClick={() => void action(run)}>{t('Run now')}</Button>{!bindingsValid && !busy && <small className="guided-run-hint" data-testid="run-now-hint">{runHint}</small>}</span>
      </Space>
    </div>
    {!workspace && catalog && <PageState kind="empty" title={t('Start with a scenario')} description={t('Open the traffic accident demo, choose a saved workspace or create an empty draft.')} action={<Button disabled={busy} onClick={() => void action(async () => { apply(await api.request<Workspace>('/v1/studio/workspaces', { name: t('New research scenario') })); setValidity({}); chooseStep('scene'); })}>{t('Create workspace')}</Button>} />}
    {!catalog && !error && <PageState kind="loading" title={t('Loading configuration tools')} description={t('Reading the AeroGraph catalog and installed domain engines.')} />}
    {workspace && catalog && scenario && common && <>
      <div className="guided-studio-layout" key={workspace.id}>
        <nav className="guided-step-rail" aria-label={t('Configuration steps')}>
          {studioSteps.map((item, index) => {
            const hasIssues = issues.some(issue => issueStep(issue) === item.id), status = hasIssues ? 'issues' : bindingsValid ? 'valid' : visited.has(item.id) ? 'opened' : 'pending';
            const statusLabel = hasIssues ? t('Has issues') : bindingsValid ? t('Valid') : visited.has(item.id) ? t('Opened') : t('Not opened');
            return <button key={item.id} data-testid={`studio-step-${item.id}`} aria-current={step === item.id ? 'step' : undefined} onClick={() => chooseStep(item.id)}><span className={`guided-status-dot guided-status-${status}`} aria-label={statusLabel} /><span><small>0{index + 1}</small>{labels[item.id]}</span>{hasIssues && <Tag color="error">{issues.filter(issue => issueStep(issue) === item.id).length}</Tag>}</button>;
          })}
          <div className="guided-rail-footer">
            <button className="guided-validation-badge" data-testid="validation-status" onClick={() => setIssuesOpen(true)}><Tag color={bindingsValid ? 'success' : issues.length ? 'error' : 'default'}>{bindingsValid ? t('Validated') : issues.length ? issueCount(issues.length) : t('Not validated')}</Tag><span>{t('Review scenario checks →')}</span></button>
            <Details title={t('Scenario source')} buttonLabel={t('YAML & details')}>
              <p>{t('Advanced: edit or exchange the complete scenario. Invalid JSON must be corrected before saving or validating.')}</p>
              <Space wrap><Button disabled={busy || !buffersValid} onClick={() => void action(async () => { const saved = await save(); setYaml(await api.export(saved.id)); })}>{t('Export YAML')}</Button><Button disabled={!yaml || busy} onClick={() => void action(async () => { apply(await api.request<Workspace>(`/v1/studio/workspaces/${workspace.id}/import`, { yaml })); })}>{t('Import YAML')}</Button></Space>
              <Input.TextArea aria-label={t('Scenario YAML')} rows={18} value={yaml} onChange={event => setYaml(event.target.value)} />
              <h3>{t('Scenario JSON')}</h3><Input.TextArea aria-label={t('Scenario editor')} rows={16} value={raw} onChange={event => {
                setRaw(event.target.value); setDirty(true); setWorkspace({ ...workspace, validation: undefined });
                try { const value = parseAuthoringJson(event.target.value); if (!value || Array.isArray(value) || typeof value !== 'object') throw Error(t('Scenario must be an object')); setWorkspace({ ...workspace, scenario: value as Record<string, any>, validation: undefined }); setRawError(''); }
                catch { setRawError(t('Invalid JSON. Check commas, quotes and brackets.')); }
              }} />{rawError && <Alert type="error" message={rawError} />}
              <pre>{JSON.stringify({ id: workspace.id, region: workspace.region, registry: scenario.registry }, null, 2)}</pre>
            </Details>
          </div>
        </nav>
        <main className="guided-step-content" aria-busy={busy}>
          <header className="guided-step-heading"><div><span className="guided-label">{t('Step {number} of 7', { number: studioSteps.findIndex(item => item.id === step) + 1 })}</span><h2>{labels[step]}<ConceptHelp topic={labels[step]} description={stepHelp[step].description} guide={stepHelp[step].guide} /></h2><p>{descriptions[step]}</p></div><Tag>{t('{count} entities', { count: scenario.entities?.length ?? 0 })}</Tag></header>
          {focusIssue && <Alert type="error" message={focusIssue.message} description={<Details title={t('Authored diagnostic')} buttonLabel={t('Diagnostic details')}><pre>{focusIssue.path}</pre></Details>} />}
          {visited.has('scene') && <section hidden={step !== 'scene'} data-testid="studio-panel-scene">
            <div className="guided-scene-layout">
              <Card title={t('Scene preview')}>{step === 'scene' && <ScenePreview workspace={workspace} api={base} layers={layers} />}<Checkbox.Group aria-label={t('Visible scene layers')} value={layers} options={['ground', 'buildings', 'roads', 'entities', 'airspace'].map(value => ({ value, label: registryLabel(value, locale) }))} onChange={values => setLayers(values as string[])} /><p className="guided-muted">{workspace.scene ? t('Selected geographic region') : workspace.demo_console?.city_available ? t('Demo scene · original city geometry') : t('Draft scene · local east / north / up coordinates')}</p></Card>
              <Card title={t('Place an entity')}><div className="guided-form">
                <label>{t('Entity name')}<Input aria-label={t('Entity ID')} value={placeId} onChange={event => setPlaceId(event.target.value)} placeholder={t('e.g. survey-uav')} /></label>
                <label>{t('AeroGraph type')}<Select aria-label={t('Placement type')} showSearch optionFilterProp="label" value={placeType} onChange={setPlaceType} options={allTypes.map(row => ({ value: row.id, label: displayType(row), disabled: row.abstract }))} /></label>
                <div className="guided-coordinate-row">{coordinates.map((label, index) => <label key={index}>{t('{direction} (m)', { direction: label })}<InputNumber aria-label={label} value={position[index]} onChange={value => { if (value !== null) setPosition(position.map((old, i) => i === index ? value : old)); }} /></label>)}</div>
                <Button disabled={!placeId.trim() || !placeType || duplicatePlaceId || busy || !buffersValid} onClick={() => void action(async () => { await save(); apply(await api.request<Workspace>(`/v1/studio/workspaces/${workspace.id}/place`, { id: placeId.trim(), type: placeType, kind: 'entity', engine: effectivePlacement, position, facts: {} })); setPlaceId(''); })}>{t('Place entity')}</Button>
                {duplicatePlaceId && <p role="alert">{t('This entity ID already exists. Choose a different name.')}</p>}
                <Details title={t('Placement settings')} buttonLabel={t('Advanced')}><p>{t('Who controls this value? Moving vehicles use basic motion; stationary actors keep their placed position. The Studio creates the required controller and field ownership when you place the actor.')}</p><label>{t('Movement controller')}<Select aria-label={t('Placement engine')} value={effectivePlacement} options={[{ value: 'kinematic', label: t('Basic motion') }, { value: 'workflow', label: t('Keep the placed position') }]} onChange={setPlacementOverride} /></label>{placementOverride && <Button onClick={() => setPlacementOverride(undefined)}>{t('Use automatic movement settings')}</Button>}<p>{t('Advanced domain engines can be configured in Domain plugins.')}</p></Details>
                <p className="guided-muted">{t('Movement settings are filled in for you. Review the new actor in Entities before running.')}</p>
              </div></Card>
            </div>
            <Card title={t('Region map')}><Space wrap><Select aria-label={t('Local extract')} placeholder={t('Choose a geographic extract')} value={extract} options={catalog.extracts.map(row => ({ value: row.id, label: localized(row, 'name', locale) }))} onChange={id => { setExtract(id); setBounds(catalog.extracts.find(row => row.id === id)!.bounds); }} /><Button disabled={!selectedExtract || busy || !buffersValid} onClick={() => void action(async () => { await save(); apply(await api.request<Workspace>(`/v1/studio/workspaces/${workspace.id}/region`, { extract, bounds, alt, level_height_m: levelHeight })); })}>{t('Select region')}</Button></Space>
              {selectedExtract && bounds.length === 4 ? <><RegionMap source={selectedExtract.bounds} bounds={bounds} scene={workspace.scene} onChange={setBounds} /><div className="guided-coordinate-row">{regionCoordinates.map((label, index) => <label key={index}>{label}<InputNumber aria-label={label} value={bounds[index]} step={0.0001} onChange={value => { if (value !== null) setBounds(bounds.map((old, i) => i === index ? value : old)); }} /></label>)}<label>{t('Altitude (m)')}<InputNumber aria-label={t('Altitude (m)')} value={alt} onChange={value => { if (value !== null) setAlt(value); }} /></label><label>{t('Metres per level')}<InputNumber aria-label={t('Metres per level')} min={0.1} value={levelHeight} onChange={value => { if (value !== null) setLevelHeight(value); }} /></label></div></> : <p className="guided-muted">{t('No geographic extract selected. This scenario uses its authored local scene.')}</p>}
              {workspace.region && <><p className="guided-muted">{t('For road traffic, generate a SUMO road network from the selected region before running.')}</p><Button disabled={busy || !buffersValid} onClick={() => void action(async () => { await save(); await api.request(`/v1/studio/workspaces/${workspace.id}/network`, {}); apply(await api.request<Workspace>(`/v1/studio/workspaces/${workspace.id}`)); })}>{t('Generate SUMO road network')}</Button></>}
            </Card>
          </section>}
          {visited.has('entities') && <section hidden={step !== 'entities'} data-testid="studio-panel-entities"><EntitiesStep key={`${workspace.id}/${new URLSearchParams(location.search).get('entity') ?? ''}`} initialEntityId={new URLSearchParams(location.search).get('entity') ?? undefined} scenario={scenario} onChange={onChange} api={api} types={allTypes} fields={allFields} schemas={workspace.registry_catalog?.schemas} onValidityChange={entityValidity} /></section>}
          {visited.has('plugins') && <section hidden={step !== 'plugins'} data-testid="studio-panel-plugins"><Card title={t('Algorithms')} data-testid="studio-algorithms-card"><p className="guided-muted">{t('Choose how drones share tasks, find routes, avoid conflicts and decide when to recharge.')}</p><AlgorithmsPanel base={base} applicable={scenarioUsesLowaltPack(scenario)} algorithms={(scenario.algorithms ?? {}) as Partial<Record<AlgorithmSlot, string>>} onChange={next => onChange({ ...scenario, algorithms: { ...next } })} /></Card><DomainPluginsStep key={workspace.id} scenario={scenario} onChange={onChange} engines={catalog.engines} onValidityChange={pluginValidity} /></section>}
          {visited.has('predicates') && <section hidden={step !== 'predicates'} data-testid="studio-panel-predicates"><PredicatesStep {...common} scenario={scenario} types={allTypes} fields={allFields} relations={allRelations} onValidityChange={predicateValidity} /></section>}
          {visited.has('rules') && <section hidden={step !== 'rules'} data-testid="studio-panel-rules"><RulesStep {...common} scenario={scenario} types={allTypes} fields={allFields} relations={allRelations} onValidityChange={behaviourValidity} /></section>}
          {visited.has('agents') && <section hidden={step !== 'agents'} data-testid="studio-panel-agents"><AgentsStep {...common} profiles={catalog.demo_profiles} onValidityChange={agentValidity} /></section>}
          {step === 'validate' && <section data-testid="studio-panel-validate"><Card title={t('Scenario checks')}><p>{t('The compiler checks entity types and fields, controllers, conditions, event chains and plugin capabilities.')}</p>{!workspace.validation ? <PageState kind="empty" title={t('Ready for validation')} description={t('Validate the entire draft to see authored errors grouped by configuration step.')} /> : bindingsValid ? <Alert type="success" showIcon message={t('Scenario valid')} description={t('The current saved draft passed the real runtime compiler. You can start a new run.')} /> : <><Alert type="error" showIcon message={issueCount(issues.length)} />{studioSteps.filter(item => issues.some(issue => issueStep(issue) === item.id)).map(item => <div key={item.id}><h3>{labels[item.id]}</h3>{issueList(item.id)}</div>)}</>}<div className="guided-run-actions"><Button disabled={busy || !buffersValid} onClick={() => void action(validate)}>{t('Validate draft')}</Button><Button type="primary" disabled={busy || !bindingsValid} onClick={() => void action(run)}>{t('Start run')}</Button></div></Card></section>}
          <footer className="guided-step-footer"><Button disabled={step === 'scene'} onClick={() => chooseStep(studioSteps[studioSteps.findIndex(item => item.id === step) - 1].id)}>{t('Previous step')}</Button><span className="guided-muted">{t('Changes apply to the next run.')}</span><Button disabled={step === 'validate'} onClick={() => chooseStep(studioSteps[studioSteps.findIndex(item => item.id === step) + 1].id)}>{t('Next step')}</Button></footer>
        </main>
      </div>
      <Modal closable={{ 'aria-label': t('Close') }} title={t('Scenario validation')} open={issuesOpen} onCancel={() => setIssuesOpen(false)} footer={<Button onClick={() => { chooseStep('validate'); setIssuesOpen(false); }}>{t('Open validation step')}</Button>}>{bindingsValid ? <Alert type="success" message={t('No authoring issues')} /> : issues.length ? studioSteps.filter(item => issues.some(issue => issueStep(issue) === item.id)).map(item => <section key={item.id}><h3>{labels[item.id]}</h3>{issueList(item.id)}</section>) : <p>{t('Validate the draft to check its authored fields, plugin ownership and event rules.')}</p>}</Modal>
    </>}
    <Link className="guided-bottom-link" to={`/runs?api=${encodeURIComponent(base)}`}>{t('Browse runs →')}</Link>
  </div>;
}
