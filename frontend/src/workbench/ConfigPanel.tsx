import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { TemplateApi, type AlgorithmDescriptor, type KnobValue, type ScenarioTemplate } from '../newcomers/api';
import { TemplateKnobControl } from '../newcomers/CustomizeTemplate';
import { algorithmSlotLabel } from '../newcomers/presentation';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { ApiError, type Project, type SectionId, type ValidationIssue, type WorkbenchApi } from './api';
import { FormValidity, getAt, JsonEditor, Leaf, pointer, setAt, TreeForm } from './form';
import { BehavioursPanel } from '../studio/BehavioursPanel';
import { StudioApi, type TypeRow, type Workspace } from '../studio/api';

type Scenario = Record<string, unknown>;
type Section = SectionId | 'controls' | 'file';
export const SECTIONS: Array<{ id: SectionId; en: string; zh: string; keys: string[] }> = [
  { id: 'space', en: 'Scene & space', zh: '场景与空间', keys: ['city', 'presentation', 'region', 'airspace'] },
  { id: 'objects', en: 'Objects', zh: '对象', keys: ['entities', 'registry'] },
  { id: 'relations', en: 'Relations & resources', zh: '关系与资源', keys: ['relations', 'resources'] },
  { id: 'tasks', en: 'Tasks & behaviours', zh: '任务与行为', keys: ['generators', 'behaviours'] },
  { id: 'components', en: 'Execution components', zh: '执行组件', keys: ['engines', 'bindings'] },
  { id: 'algorithms', en: 'Algorithms & agents', zh: '算法与智能体', keys: ['algorithms', 'agents', 'decisions'] },
  { id: 'run', en: 'Run & recording', zh: '运行与记录', keys: ['run', 'outputs', 'ingress_streams'] },
];
const SECTION_OF: Record<string, SectionId> = Object.fromEntries(SECTIONS.flatMap(section => section.keys.map(key => [key, section.id])));
/** Section of a validation issue, from the server or from its authored path. */
export function issueSection(issue: ValidationIssue): SectionId | undefined {
  if (issue.section) return issue.section;
  const head = /^\/?\$?\.?([A-Za-z_]+)/.exec(issue.path ?? '')?.[1] ?? /^([A-Za-z_]+)[.[:]/.exec(issue.message)?.[1];
  return head ? SECTION_OF[head] : undefined;
}
/** Authored `$.entities[3].facts` → JSON pointer `/entities/3/facts`. */
export function issuePointer(path: string) {
  if (path.startsWith('/')) return path;
  return pointer(...path.replace(/^\$/, '').replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean));
}

const RESOURCE_FACT = /(capacity|open|power|queue|slots|preparation|stock|pads?_|_count)/;
const localKey = (id: string) => `aas.draft.${id}`;
/** Authoring preserves float kinds in rule parameters; knob widgets need their numeric value. */
export function controlValue(value: unknown): KnobValue | undefined {
  if (typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (value && typeof value === 'object') {
    const tag = value as Record<string, unknown>, token = tag.$number ?? tag.$integer;
    if (Object.keys(tag).length === 1 && typeof token === 'string' && token.trim() !== '' && Number.isFinite(Number(token))) return Number(token);
  }
}

/** Read the setting the draft will actually run, including declared compound controls. */
export function controlSource(knob: ScenarioTemplate['knobs'][number], draft: Scenario, authored: Record<string, KnobValue> | undefined): KnobValue | undefined {
  if (knob.pointer) return controlValue(getAt(draft, knob.pointer));
  if (knob.apply === 'traffic.physics_hz') {
    const road = controlValue(getAt(draft, '/engines/road_motion/config/step_ns'));
    const air = controlValue(getAt(draft, '/engines/air_motion/config/step_ns'));
    if (typeof road !== 'number' || !Number.isSafeInteger(road) || road <= 0 || air !== road) return undefined;
    const frequency = 1e9 / road, rounded = Math.round(frequency * 1e6) / 1e6;
    // Normalize only when applying that frequency reproduces the exact period.
    return Math.round(1e9 / rounded) === road ? rounded : frequency;
  }
  return controlValue(authored?.[knob.id]);
}

export function ConfigPanel({ api, project, onProject, onInitialize, initializing, section: requested, onSection, onClose, onInspectContract, onDraft }: {
  api: WorkbenchApi; project: Project; onProject: (project: Project) => void;
  onInitialize: () => void; initializing: boolean; section?: string; onSection: (section: string) => void; onClose: () => void;
  onDraft?: (id: string, scenario: Scenario, dirty: boolean) => void;
  onInspectContract?: (typeId: string, field?: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [draft, setDraft] = useState<Scenario>(project.scenario as Scenario);
  const [baseRevision, setBaseRevision] = useState(project.revision);
  const [local, setLocal] = useState(false), [restored, setRestored] = useState(false);
  const [busy, setBusy] = useState<string>(), [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string; textZh?: string }>();
  const [invalid, setInvalid] = useState<Set<string>>(() => new Set());
  const reportValidity = useCallback((path: string, valid: boolean) => setInvalid(previous => {
    if (previous.has(path) === !valid) return previous;
    const next = new Set(previous); if (valid) next.delete(path); else next.add(path); return next;
  }), []);
  const [focus, setFocus] = useState<string>();
  const [template, setTemplate] = useState<ScenarioTemplate>(), [algorithms, setAlgorithms] = useState<AlgorithmDescriptor[]>();
  const [knobs, setKnobs] = useState<Record<string, KnobValue>>({});
  const [yaml, setYaml] = useState<string>();
  const [yamlDirty, setYamlDirty] = useState(false), [yamlRevision, setYamlRevision] = useState(project.revision);
  useEffect(() => { onDraft?.(project.id, draft, local); }, [project.id, draft, local, onDraft]);
  const [catalogError, setCatalogError] = useState<string>();
  const [ruleEditor, setRuleEditor] = useState(false), [catalog, setCatalog] = useState<{ types: TypeRow[]; fields: Record<string, unknown>[]; relations: Record<string, unknown>[] }>();
  const ruleRoot = useRef<HTMLDivElement>(null), [ruleError, setRuleError] = useState<string>();
  const reportRuleValidity = useCallback((valid: boolean) => reportValidity('/behaviours', valid), [reportValidity]);
  const studioApi = useMemo(() => new StudioApi(api.base), [api.base]);
  useEffect(() => { if (!ruleEditor || catalog) return; setRuleError(undefined); void studioApi.request<{ types: TypeRow[]; fields: Record<string, unknown>[]; relations: Record<string, unknown>[] }>(`/v1/studio/types?workspace=${encodeURIComponent(project.id)}`).then(setCatalog).catch(problem => setRuleError(String(problem))); }, [ruleEditor, project.id]);
  useEffect(() => { if (ruleEditor && catalog) ruleRoot.current?.scrollIntoView({ block: 'start' }); }, [ruleEditor, catalog]);
  const [section, sectionPath] = (requested ?? (project.template ? 'controls' : 'objects')).split(':') as [Section, string | undefined];
  useEffect(() => () => reportValidity('/behaviours', true), [section, ruleEditor, reportValidity]);
  useEffect(() => { if (sectionPath) setFocus(sectionPath); }, [sectionPath]);

  // A newer server draft replaces ours unless we hold unsaved local edits.
  useEffect(() => {
    const stored = localStorage.getItem(localKey(project.id));
    if (stored) {
      try {
        const saved = JSON.parse(stored) as { revision: number; scenario: Scenario; knobs?: Record<string, KnobValue>; scenarioDirty?: boolean; yaml?: string; yamlRevision?: number };
        setDraft(saved.scenario); setLocal(saved.scenarioDirty ?? true); setRestored(true); setBaseRevision(saved.revision); setKnobs(saved.knobs ?? {});
        if (saved.yaml !== undefined) { setYaml(saved.yaml); setYamlDirty(true); setYamlRevision(saved.yamlRevision ?? saved.revision); }
        if (saved.revision !== project.revision) setMessage({ kind: 'error', text: L('The server draft changed. Your unsaved edits are kept; discard them to load the server draft, or copy them before resolving the revision conflict.', '服务器草稿已变化。未保存的修改仍保留；可放弃以加载服务器草稿，或先复制修改再处理修订冲突。') });
        return;
      } catch (problem) { setMessage({ kind: 'error', text: L(`Could not restore local edits: ${String(problem)}`, `无法恢复本地修改：${String(problem)}`) }); }
    }
    if (!local || project.revision !== baseRevision) { setDraft(project.scenario as Scenario); setBaseRevision(project.revision); setLocal(false); }
    if (!yamlDirty) setYaml(undefined);
  }, [project.id, project.revision]);
  useEffect(() => { if (local || Object.keys(knobs).length || yamlDirty) localStorage.setItem(localKey(project.id), JSON.stringify({ revision: baseRevision, scenario: draft, scenarioDirty: local, knobs, ...(yamlDirty ? { yaml, yamlRevision } : {}) })); }, [draft, local, knobs, yaml, yamlDirty, baseRevision]);
  useEffect(() => { if (!local && !Object.keys(knobs).length && !yamlDirty) return; const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; }; addEventListener('beforeunload', guard); return () => removeEventListener('beforeunload', guard); }, [local, knobs, yamlDirty]);
  useEffect(() => {
    const abort = new AbortController(), templates = new TemplateApi(api.base);
    setCatalogError(undefined);
    if (project.template) void templates.template(project.template.id, abort.signal).then(setTemplate).catch(problem => { if (!abort.signal.aborted) setCatalogError(String(problem)); });
    void templates.algorithms(abort.signal).then(setAlgorithms).catch(problem => { if (!abort.signal.aborted) setCatalogError(String(problem)); });
    return () => abort.abort();
  }, [api, project.template?.id]);

  const edit = (path: string, value: unknown) => { setDraft(previous => setAt(previous, path, value)); setLocal(true); };
  const issues: ValidationIssue[] = project.validation && project.validation.revision === project.revision && !project.validation.valid ? project.validation.issues ?? project.validation.errors.map(message => ({ source: 'scenario', path: '$', message })) : [];
  const issueSections = new Set(issues.map(issueSection));
  const fail = (problem: unknown) => setMessage({ kind: 'error', text: problem instanceof ApiError ? problem.detail : String(problem) });

  const saveDraft = async (): Promise<Project | undefined> => {
    if (invalid.size) { fail(L('Correct the highlighted input before saving.', '请修正标记的输入后再保存。')); return; }
    if (!local) return project;
    setBusy('save');
    try {
      const saved = await api.save(project.id, draft, baseRevision);
      localStorage.removeItem(localKey(project.id)); setLocal(false); setRestored(false); setBaseRevision(saved.revision); onProject(saved);
      return saved;
    } catch (problem) {
      fail(problem instanceof ApiError && problem.status === 409 ? L('The draft changed elsewhere. Your edits are kept locally; reload the project to compare before saving.', '草稿已在其他地方被修改。你的修改仍保存在本地；请重新加载项目对比后再保存。') : problem);
      return undefined;
    } finally { setBusy(undefined); }
  };
  const validate = async () => {
    const saved = await saveDraft(); if (!saved) return;
    setBusy('validate');
    try {
      const result = await api.validate(saved.id, saved.revision);
      onProject(await api.project(saved.id));
      if (result.valid) { setFocus(undefined); onSection(section); setMessage({ kind: 'ok', text: `Revision ${saved.revision} is valid. Save it as a version to run it.`, textZh: `修订 ${saved.revision} 校验通过。保存为版本后即可运行。` }); }
      else { setMessage({ kind: 'error', text: L(`${result.issues?.length ?? 1} issue(s) found. Click one to jump to it.`, `发现 ${result.issues?.length ?? 1} 个问题，点击可定位。`) }); const first = result.issues?.[0]; if (first) jump(first); }
    } catch (problem) { fail(problem); } finally { setBusy(undefined); }
  };
  const saveVersion = async () => {
    setBusy('version');
    try {
      const version = await api.saveVersion(project.id, project.revision);
      onProject(await api.project(project.id));
      setMessage({ kind: 'ok', text: `Saved version ${version.version}. Initialize it to see the world at t = 0.`, textZh: `已保存版本 ${version.version}。初始化后即可查看 t = 0 时的世界。` });
    } catch (problem) { fail(problem); } finally { setBusy(undefined); }
  };
  const applyKnobs = async () => {
    const changes = { ...knobs };
    const saved = await saveDraft(); if (!saved) return;
    setBusy('knobs');
    try { const updated = await api.knobs(saved.id, changes, saved.revision); setKnobs({}); localStorage.removeItem(localKey(project.id)); onProject(updated); setMessage({ kind: 'ok', text: 'Controls applied to the draft.', textZh: '控制项已应用到草稿。' }); }
    catch (problem) { fail(problem); } finally { setBusy(undefined); }
  };
  const jump = (issue: ValidationIssue) => { const target = issueSection(issue); setEntityFilter(''); onSection(`${target ?? 'file'}:${issuePointer(issue.path)}`); setFocus(issuePointer(issue.path)); };
  const discard = () => { localStorage.removeItem(localKey(project.id)); setLocal(false); setRestored(false); setKnobs({}); setYaml(undefined); setYamlDirty(false); setDraft(project.scenario as Scenario); setBaseRevision(project.revision); };
  const loadYaml = async (reload = false) => {
    setBusy('export');
    try {
      const text = await api.exportYaml(project.id); setYaml(text); setYamlRevision(project.revision);
      if (reload) { setYamlDirty(false); if (!local && !Object.keys(knobs).length) localStorage.removeItem(localKey(project.id)); }
    } catch (problem) { fail(problem); } finally { setBusy(undefined); }
  };
  const controlsDirty = Object.keys(knobs).length > 0;
  // A success describes the saved revision; new local or invalid input invalidates that notice.
  useEffect(() => { if (local || controlsDirty || yamlDirty || invalid.size) setMessage(previous => previous?.kind === 'ok' ? undefined : previous); }, [draft, knobs, yaml, local, controlsDirty, yamlDirty, invalid]);
  const valueFor = (knob: ScenarioTemplate['knobs'][number]) => knobs[knob.id] === undefined ? controlSource(knob, draft, project.template?.knobs) : controlValue(knobs[knob.id]);
  const controlsMissing = !!template?.knobs.some(knob => valueFor(knob) === undefined);
  const validNow = project.validation?.valid && project.validation.revision === project.revision && !local && !controlsDirty && !controlsMissing && !yamlDirty && !invalid.size;
  const versioned = project.latest_version && project.latest_version.revision === project.revision && !local;

  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const [entityFilter, setEntityFilter] = useState('');
  const shownEntities = useMemo(() => entities.map((entity, index) => ({ entity, index })).filter(({ entity }) => !entityFilter || `${entity.id} ${entity.label ?? ''} ${entity.type}`.toLowerCase().includes(entityFilter.toLowerCase())), [entities, entityFilter]);
  const [openEntity, setOpenEntity] = useState<number>();
  useEffect(() => { const match = /^\/entities\/(\d+)/.exec(focus ?? ''); if (match) setOpenEntity(Number(match[1])); }, [focus]);

  const nav: Array<{ id: Section; label: string }> = [
    ...(project.template ? [{ id: 'controls' as Section, label: L('Scenario controls', '场景控制项') }] : []),
    ...SECTIONS.map(item => ({ id: item.id as Section, label: zh ? item.zh : item.en })),
    { id: 'file', label: L('File (YAML)', '文件（YAML）') },
  ];
  const keysPresent = (keys: string[]) => keys.filter(key => key in draft);
  const sectionBody = () => {
    if (section === 'controls') {
      if (!template) return <p className="hint" role={catalogError ? 'alert' : 'status'}>{catalogError ?? L('Loading the scenario controls…', '正在加载场景控制项…')}</p>;
      return <>
        <p className="hint">{L('The controls the scenario was designed around. Applying writes them into this draft and keeps your other edits.', '该场景围绕这些控制项设计。应用后会写入当前草稿，并保留你的其他修改。')}</p>
        <div className="wb-knobs">{template.knobs.map(knob => {
          const value = valueFor(knob);
          return <div className="wb-knob" key={knob.id} data-path={knob.pointer}>{value === undefined ? <p role="alert">{L('Missing or invalid control source. Correct it in the form or file:', '控制项来源缺失或无效，请在表单或文件中修正：')} {knob.pointer ?? knob.id}</p>
            : <TemplateKnobControl disabled={!!busy} knob={knob} value={value} onChange={value => setKnobs(previous => ({ ...previous, [knob.id]: value }))} onValidityChange={valid => reportValidity(`/knobs/${knob.id}`, valid)} algorithms={algorithms} />}</div>;
        })}</div>
      </>;
    }
    if (section === 'file') {
      return <>
        <p className="hint">{L('The same draft as a scenario file. Importing replaces the draft after the server compiles it.', '与表单是同一份草稿的文件形式。导入时服务器先编译，通过后替换草稿。')}</p>
        {local && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{L('Save the form edits first; the file shows the saved draft.', '请先保存表单修改；文件显示的是已保存的草稿。')}</p>}
        {yamlDirty && yamlRevision !== project.revision && <p role="alert">{L('This file was edited from an older revision. Copy your changes and reload the current draft before importing.', '此文件基于旧修订修改。请复制修改并重新加载当前草稿后导入。')}</p>}
        {busy === 'export' && <p className="hint" role="status">{L('Loading the saved scenario file…', '正在加载已保存的场景文件…')}</p>}
        {yaml === undefined ? <button className="wb-btn" disabled={local || !!busy} onClick={() => void loadYaml()}>{busy === 'export' ? L('Loading scenario file…', '正在加载场景文件…') : L('Load scenario file', '加载场景文件')}</button>
          : <><textarea className="wb-json" style={{ minHeight: '52vh' }} spellCheck={false} aria-label={L('Scenario YAML', '场景 YAML')} value={yaml} onChange={event => { setYaml(event.target.value); setYamlDirty(true); }} />
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button className="wb-btn primary" disabled={!!busy || local || yamlRevision !== project.revision} onClick={() => { setBusy('import'); void api.importYaml(project.id, yaml, project.revision).then(saved => { localStorage.removeItem(localKey(project.id)); setYamlDirty(false); setYaml(undefined); onProject(saved); setMessage({ kind: 'ok', text: 'Imported into the draft.', textZh: '已导入草稿。' }); }).catch(fail).finally(() => setBusy(undefined)); }}>{L('Import into draft', '导入草稿')}</button>
              <button className="wb-btn" disabled={local || !!busy} onClick={() => void loadYaml(true)}>{L('Reload', '重新加载')}</button>
              <button className="wb-btn" disabled={local || yamlDirty || !!busy} onClick={() => { const url = URL.createObjectURL(new Blob([yaml], { type: 'text/yaml' })); const link = document.createElement('a'); link.href = url; link.download = `${project.id}.yaml`; link.click(); URL.revokeObjectURL(url); }}>{L('Download YAML', '下载 YAML')}</button>
            </div></>}
      </>;
    }
    const meta = SECTIONS.find(item => item.id === section)!;
    if (section === 'objects') return <>
      <p className="hint">{L('Concrete instances in this world. Each has an identity, a contract type, an initial state and a visual binding.', '世界中的具体实例：每个都有身份、契约类型、初始状态和视觉绑定。')}</p>
      {draft.generators && typeof draft.generators === 'object' && <><h4>{L('Generated fleets', '批量生成的机队')}</h4>
        {Object.entries(draft.generators as Record<string, any>).map(([name, generator]) => <div key={name}>
          <Leaf path={pointer('generators', name, 'count')} label={`${name} · ${L('count', '数量')}`} value={generator.count} onChange={edit} focus={focus} />
          <Leaf path={pointer('generators', name, 'type')} label={`${name} · ${L('type', '类型')}`} value={generator.type} onChange={edit} focus={focus} readOnly />
          <details open={focus?.startsWith(pointer('generators', name))}><summary>{L('Initial state & positions', '初始状态与位置')}</summary>
            <TreeForm value={Object.fromEntries(Object.entries(generator).filter(([key]) => key !== 'count' && key !== 'type'))} path={pointer('generators', name)} onChange={edit} focus={focus} />
          </details>
          {onInspectContract && <button className="wb-btn small ghost" onClick={() => onInspectContract(generator.type)}>{L('Inspect type contract', '查看类型契约')}</button>}
        </div>)}</>}
      <h4>{L('Instances', '实例')} <span className="wb-tag">{entities.length}</span></h4>
      <input className="wb-search" aria-label={L('Filter instances', '筛选实例')} placeholder={L('Filter by id, label or type', '按 id、名称或类型筛选')} value={entityFilter} onChange={event => setEntityFilter(event.target.value)} />
      <table className="wb-table" style={{ marginTop: 8 }}><thead><tr><th>{L('Instance', '实例')}</th><th>{L('Type', '类型')}</th><th /></tr></thead><tbody>
        {shownEntities.slice(0, 400).map(({ entity, index }) => <Fragment key={index}>
          <tr data-path={pointer('entities', index)} style={focus?.startsWith(pointer('entities', index)) ? { background: 'rgba(224,69,58,.08)' } : undefined}>
            <td><b>{entity.label ?? entity.id}</b><div className="wb-mono" style={{ fontSize: 11, color: 'var(--wb-muted)' }}>{entity.id}</div></td>
            <td className="wb-mono" style={{ fontSize: 11 }}>{entity.type}</td>
            <td><button className="wb-btn small" onClick={() => setOpenEntity(openEntity === index ? undefined : index)}>{openEntity === index ? L('Close', '收起') : L('Edit', '编辑')}</button></td>
          </tr>
          {openEntity === index && <tr><td colSpan={3}><TreeForm value={entity} path={pointer('entities', index)} onChange={edit} focus={focus} />
            {onInspectContract && <button className="wb-btn small ghost" onClick={() => onInspectContract(entity.type)}>{L('Inspect type contract', '查看类型契约')}</button>}</td></tr>}
        </Fragment>)}
      </tbody></table>
      {shownEntities.length > 400 && <p className="hint">{L(`Showing 400 of ${shownEntities.length}; filter to find others.`, `显示前 400 个（共 ${shownEntities.length} 个），请筛选查找其余。`)}</p>}
    </>;
    if (section === 'relations') {
      const resources = entities.map((entity, index) => ({ entity, index })).filter(({ entity }) => Object.keys(entity.facts ?? {}).some(key => RESOURCE_FACT.test(key)));
      return <>
        <p className="hint">{L('Facilities and their capacities, open state and service resources. Relations are facts on the instances below.', '设施及其容量、开放状态和服务资源。关系以下列实例上的事实表示。')}</p>
        {keysPresent(['relations', 'resources']).map(key => <div key={key}><h4>{key}</h4><TreeForm value={draft[key]} path={pointer(key)} label={key} onChange={edit} focus={focus} /></div>)}
        {!resources.length && <div className="wb-empty">{L('No resource facts in this scenario.', '该场景中没有资源类事实。')}</div>}
        {resources.map(({ entity, index }) => <details key={index} open={focus?.startsWith(pointer('entities', index))}><summary><b>{entity.label ?? entity.id}</b> <span className="wb-tag wb-mono">{entity.type}</span></summary>
          {Object.entries(entity.facts ?? {}).filter(([key]) => RESOURCE_FACT.test(key)).map(([key, value]) => <Leaf key={key} path={pointer('entities', index, 'facts', key)} label={key} value={value} onChange={edit} focus={focus} />)}
        </details>)}
      </>;
    }
    if (section === 'tasks') {
      const behaviours = (Array.isArray(draft.behaviours) ? draft.behaviours : []) as Array<Record<string, any>>;
      const taskConfig = (draft.engines as Record<string, any> | undefined)?.tasks?.config;
      return <>
        <p className="hint">{L('What work appears and the rules that decide what is allowed: predicates with parameters, and event chains.', '会出现哪些工作，以及决定允许做什么的规则：带参数的谓词与事件链。')}</p>
        {taskConfig?.generators && <><h4>{L('Task arrivals', '任务到达')}</h4><TreeForm value={taskConfig.generators} path={pointer('engines', 'tasks', 'config', 'generators')} onChange={edit} focus={focus} /></>}
        {behaviours.map((behaviour, b) => <div key={b}>
          <h4>{L('Rules', '规则')} · <span className="wb-mono">{behaviour.id ?? b}</span></h4>
          {Object.entries(behaviour.predicates ?? {}).map(([name, predicate]: [string, any]) => <details key={name} open><summary><b>{name}</b></summary>
            {predicate?.parameters ? Object.entries(predicate.parameters).map(([key, value]) => <Leaf key={key} path={pointer('behaviours', b, 'predicates', name, 'parameters', key)} label={key} value={value} onChange={edit} focus={focus} />) : <p className="hint">{L('No parameters.', '无参数。')}</p>}
          </details>)}
          {behaviour.chains && <><h4>{L('Event chains', '事件链')}</h4><div className="wb-card-meta">{Object.keys(behaviour.chains).map(chain => <span key={chain} className="wb-tag wb-mono">{chain}</span>)}</div></>}
        </div>)}
        <div style={{ marginTop: 14 }}><button className="wb-btn" aria-pressed={ruleEditor} onClick={() => setRuleEditor(value => !value)}>{ruleEditor ? L('Hide the rule editor', '收起规则编辑器') : L('Edit predicates, triggers and event chains…', '编辑谓词、触发条件和事件链…')}</button></div>
        {ruleEditor && (catalog ? <div className="wb-rule-editor" ref={ruleRoot}><BehavioursPanel api={studioApi} workspace={{ ...project, scenario: draft } as unknown as Workspace} scenario={draft} types={catalog.types} fields={catalog.fields} relations={catalog.relations}
            onChange={scenario => { setDraft(scenario); setLocal(true); }} onApply={workspace => onProject(workspace as unknown as Project)}
            save={async () => { const saved = await saveDraft(); if (!saved) throw Error(L('Save failed', '保存失败')); return saved as unknown as Workspace; }} onValidityChange={reportRuleValidity}
            issues={issues.filter(issue => issueSection(issue) === 'tasks')} /></div> : <p className="hint" role={ruleError ? 'alert' : 'status'}>{ruleError ?? L('Loading the contract catalog…', '正在加载契约目录…')}</p>)}
        {!behaviours.length && !taskConfig && <div className="wb-empty">{L('No task or behaviour definitions.', '没有任务或行为定义。')}</div>}
      </>;
    }
    if (section === 'algorithms') {
      const slots = (draft.algorithms ?? {}) as Record<string, string>;
      return <>
        <p className="hint">{L('Which method chooses plans. Each slot is used by the components listed with it. Changing a slot changes the next run, never a running world.', '用哪种方法选择方案。每个插槽作用于对应的组件。修改只影响下一次运行，不会改变正在运行的世界。')}</p>
        {Object.entries(slots).map(([slot, chosen]) => {
          const options = (algorithms ?? []).filter(item => item.slot === slot);
          const users = Object.entries((draft.engines ?? {}) as Record<string, any>).filter(([, engine]) => engine?.config?.algorithm_config !== undefined || JSON.stringify(engine?.config ?? {}).includes(slot)).map(([name]) => name);
          const current = options.find(item => item.id === chosen);
          return <div key={slot} style={{ marginBottom: 10 }}>
            <Leaf path={pointer('algorithms', slot)} label={algorithmSlotLabel(slot as never, locale)} value={chosen} options={options.map(item => item.id)} onChange={edit} focus={focus} />
            {current && <p className="hint">{(zh ? current.description_zh : undefined) ?? current.description}</p>}
            {users.length > 0 && <div className="wb-card-meta">{users.map(user => <span key={user} className="wb-tag">{L('used by', '作用于')} {user}</span>)}</div>}
          </div>;
        })}
        {!Object.keys(slots).length && <div className="wb-empty">{L('This scenario declares no algorithm slots.', '该场景未声明算法插槽。')}</div>}
        {keysPresent(['agents', 'decisions']).map(key => <div key={key}><h4>{key}</h4><TreeForm value={draft[key]} path={pointer(key)} label={key} onChange={edit} focus={focus} /></div>)}
      </>;
    }
    const keys = keysPresent(meta.keys);
    return <>
      <p className="hint">{section === 'space' ? L('Geographic scope, city assets and how types are drawn in the scene.', '地理范围、城市素材以及类型在场景中的呈现方式。')
        : section === 'components' ? L('Engines that compute state, and which engine owns (writes) which fields.', '计算状态的引擎，以及哪个引擎拥有（写入）哪些字段。')
        : L('Duration, stepping, random seed, inputs and what is recorded.', '时长、步进、随机种子、输入源以及记录内容。')}</p>
      {section === 'space' && <Leaf path="/city" label={L('City', '城市')} value={(draft.city as string | undefined) ?? 'none'} options={['huangpu', 'none']} onChange={(path, value) => edit(path, value === 'none' ? undefined : value)} focus={focus} />}
      {keys.filter(key => !(section === 'space' && key === 'city')).map(key => <div key={key}><h4 className="wb-mono">{key}</h4>
        {section === 'components' && key === 'engines' ? Object.entries(draft.engines as Record<string, any>).map(([name, engine]) => <details key={name} open={focus?.startsWith(pointer('engines', name) + '/')}><summary><b>{name}</b> <span className="wb-tag wb-mono">{engine.plugin}</span></summary>
          {JSON.stringify(engine.config ?? {}).length > 6000
            ? <JsonEditor label={`${name} config`} value={engine.config} path={pointer('engines', name, 'config')} focus={focus} onApply={value => edit(pointer('engines', name, 'config'), value)} />
            : <TreeForm value={engine.config ?? {}} path={pointer('engines', name, 'config')} onChange={edit} focus={focus} />}
        </details>)
          : (getAt(draft, '/' + key) && typeof getAt(draft, '/' + key) === 'object' && JSON.stringify(getAt(draft, '/' + key)).length > 6000)
            ? <JsonEditor label={key} value={getAt(draft, '/' + key)} path={'/' + key} focus={focus} onApply={value => edit('/' + key, value)} />
            : <TreeForm value={getAt(draft, '/' + key)} path={'/' + key} label={key} onChange={edit} focus={focus} />}
      </div>)}
    </>;
  };

  return <FormValidity.Provider value={reportValidity}><aside className="wb-panel wb-right is-wide wb-glass" aria-label={L('Configure scenario', '配置场景')} data-testid="wb-config">
    <div className="wb-panel-head"><h3>{L('Configure', '配置')} · {template && project.name === template.title ? localized(template, 'title', locale) : project.name}</h3><button className="wb-x" aria-label={L('Close configuration', '关闭配置')} onClick={onClose}>✕</button></div>
    {restored && <div className="wb-restored">{L('Unsaved edits were restored from this browser.', '已从本浏览器恢复未保存的修改。')}<span className="wb-spacer" /><button className="wb-btn small" onClick={discard}>{L('Discard', '放弃')}</button></div>}
    {catalogError && <div className="wb-issues" role="alert">{catalogError}</div>}
    {issues.length > 0 && <div className="wb-issues" role="alert" data-testid="wb-issues">{issues.map((issue, index) => <button key={index} onClick={() => jump(issue)}>⚠ {[issue.entity_id, issue.field ?? issue.binding].filter(Boolean).join(' · ') || issue.path} — {issue.message}</button>)}</div>}
    <div className="wb-config">
      <nav aria-label={L('Configuration sections', '配置分区')}>{nav.map(item => <button key={item.id} aria-current={section === item.id} onClick={() => { if (invalid.size) { fail(L('Correct the highlighted input before changing sections.', '请修正标记的输入后再切换分区。')); return; } onSection(item.id); setFocus(undefined); }}>{item.label}{issueSections.has(item.id as SectionId) && <i className="wb-issue-dot" aria-label={L('has issues', '有问题')} />}</button>)}</nav>
      <div className="wb-config-body">{sectionBody()}</div>
    </div>
    <div className="wb-config-foot">
      <div className="state" aria-live="polite">
        <div className="wb-config-state-copy">
        {local || controlsDirty || yamlDirty ? <b style={{ color: 'var(--wb-warn)' }}>{L('Unsaved edits', '有未保存的修改')}</b> : <b>{L(`Draft revision ${project.revision}`, `草稿修订 ${project.revision}`)}</b>}
        {' · '}{project.latest_version ? L(`latest version ${project.latest_version.version}${versioned ? ' (this draft)' : ''}`, `最新版本 ${project.latest_version.version}${versioned ? '（即当前草稿）' : ''}`) : L('no saved version yet', '尚无保存的版本')}
        {message && <div style={{ color: message.kind === 'error' ? 'var(--wb-bad)' : 'var(--wb-ok)' }}>{zh && message.textZh ? message.textZh : message.text}</div>}
        </div>
        {(section === 'controls' || controlsDirty) && <button className="wb-btn primary" disabled={!!busy || !controlsDirty || controlsMissing || !!invalid.size} onClick={() => void applyKnobs()}>{busy === 'knobs' ? L('Applying controls…', '正在应用控制项…') : L('Apply controls to draft', '应用到草稿')}</button>}
      </div>
      <button className="wb-btn" disabled={!local || !!busy || !!invalid.size} onClick={() => void saveDraft()}>{busy === 'save' ? L('Saving…', '保存中…') : L('Save draft', '保存草稿')}</button>
      <button className="wb-btn" disabled={!!busy || !!invalid.size || controlsDirty || controlsMissing || yamlDirty} title={controlsDirty ? L('Apply the controls first', '请先应用控制项') : controlsMissing ? L('Correct the missing control source first', '请先修正缺失的控制项来源') : yamlDirty ? L('Import the file first', '请先导入文件') : undefined} data-testid="wb-validate" onClick={() => void validate()}>{busy === 'validate' ? L('Validating…', '校验中…') : L('Validate', '校验')}</button>
      <button className="wb-btn primary" disabled={!validNow || !!versioned || !!busy} data-testid="wb-save-version" title={!validNow ? L('Validate this revision first', '请先校验当前修订') : undefined} onClick={() => void saveVersion()}>{L('Save version', '保存版本')}</button>
      <button className="wb-btn go" disabled={!project.latest_version || initializing || !!busy} data-testid="wb-initialize" onClick={onInitialize} title={L('Create a run of the latest saved version, held at t = 0', '以最新保存的版本创建运行，停在 t = 0')}>{initializing ? L('Initializing…', '初始化中…') : L(`Initialize v${project.latest_version?.version ?? '–'}`, `初始化 v${project.latest_version?.version ?? '–'}`)}</button>
    </div>
  </aside></FormValidity.Provider>;
}
