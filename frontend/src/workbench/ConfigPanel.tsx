import { ModeSwitch, type EditMode, type Origin } from '../assistant/ModeSwitch';
import { Component, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from 'react';
import { lazyWithRetry, retryFailedLazyImports } from '../lazyRetry';
import { TemplateApi, type AlgorithmDescriptor, type KnobValue, type ScenarioTemplate } from '../newcomers/api';
import { TemplateKnobControl } from '../newcomers/TemplateKnobControl';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { ApiError, type Project, type SectionId, type ValidationIssue, type WorkbenchApi } from './api';
import { FormValidity, getAt, setAt } from './form';
import { changedFields, changedSections, mergeSections, rebaseDraft, sameDraftValue, type SectionChoice } from './merge-draft';
import { StudioApi } from '../studio/api';
import { SECTIONS, issuePointer, issueSection } from './sections/issues';
import type { SectionProps } from './sections/types';
const SpaceSection = lazyWithRetry(() => import('./sections/SpaceSection'), module => module.SpaceSection);
const ObjectsSection = lazyWithRetry(() => import('./sections/ObjectsSection'), module => module.ObjectsSection);
const RelationsSection = lazyWithRetry(() => import('./sections/RelationsSection'), module => module.RelationsSection);
const TasksSection = lazyWithRetry(() => import('./sections/TasksSection'), module => module.TasksSection);
const ComponentsSection = lazyWithRetry(() => import('./sections/ComponentsSection'), module => module.ComponentsSection);
const AlgorithmsSection = lazyWithRetry(() => import('./sections/AlgorithmsSection'), module => module.AlgorithmsSection);
const RunSection = lazyWithRetry(() => import('./sections/RunSection'), module => module.RunSection);
import { assertOperationsReady, currentOperationsProject, mutateWorkspace, operationText, useWorkspaceOperations } from './sections/operations';
import { StagedFormWorkspace, useStagedFormStatus } from './sections/useStagedForm';

const SECTION_BODIES: Record<SectionId, ComponentType<SectionProps>> = {
  space: SpaceSection, objects: ObjectsSection, relations: RelationsSection, tasks: TasksSection,
  components: ComponentsSection, algorithms: AlgorithmsSection, run: RunSection,
};

type Scenario = Record<string, unknown>;
type Section = SectionId | 'controls' | 'file';
export { SECTIONS, issueSection, issuePointer } from './sections/issues';

const localKey = (id: string) => `aas.draft.${id}`;
type Recovery = { revision: number; scenario: Scenario; base?: Scenario; knobs?: Record<string, KnobValue>; scenarioDirty?: boolean; yaml?: string; yamlRevision?: number };
function readRecovery(id: string): { saved?: Recovery; error?: string } {
  try {
    const stored = sessionStorage.getItem(localKey(id));
    if (!stored) return {};
    const saved = JSON.parse(stored) as Recovery;
    if (!Number.isSafeInteger(saved.revision) || !saved.scenario || typeof saved.scenario !== 'object' || Array.isArray(saved.scenario)) throw Error('Invalid draft recovery record');
    return { saved };
  } catch (problem) { return { error: String(problem) }; }
}

/** Each section owns its failure and retry; sibling forms and the workbench stay mounted. */
export class ConfigureBoundary extends Component<{ children: ReactNode; label: string; L: (en: string, zh: string) => string }, { error?: Error }> {
  state: { error?: Error } = {};
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    const { L, label } = this.props;
    return this.state.error ? <div className="wb-issues" role="alert">
      <p>{L(`Could not display ${label}. Your other sections and draft are kept.`, `无法显示“${label}”。其他分区和草稿仍保留。`)}</p>
      <p className="hint wb-mono">{this.state.error.message}</p>
      <button type="button" className="wb-btn" onClick={() => { retryFailedLazyImports(); this.setState({ error: undefined }); }}>{L('Retry', '重试')}</button>
    </div> : this.props.children;
  }
}
function ConfigureBody({ render }: { render: () => ReactNode }) { return <>{render()}</>; }
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
  if (knob.apply === 'traffic.wind_east' || knob.apply === 'traffic.wind_north') {
    const motion = getAt(draft, '/engines/air_motion') as Record<string, any> | undefined;
    const consumer = motion?.config?.consumption_model;
    if (motion?.plugin !== 'kinematic' || consumer?.plugin !== 'wind') return undefined;
    const source = consumer.config;
    if (typeof source?.entity !== 'string' || typeof source.field !== 'string') return undefined;
    const profiles = Object.values((draft.engines ?? {}) as Record<string, Record<string, any>>)
      .filter(engine => engine.plugin === 'environment')
      .map(engine => engine.config?.profiles?.[source.entity]?.[source.field]).filter(profile => profile !== undefined);
    if (profiles.length !== 1) return undefined;
    const profile = profiles[0], row = source.vector_index;
    if (!['calm', 'constant', 'gust'].includes(profile?.mode) || row !== undefined && (!Number.isSafeInteger(row) || row < 0)) return undefined;
    const vector = row === undefined ? profile.value : profile.value?.[row];
    if (!Array.isArray(vector) || ![2, 3].includes(vector.length) || vector.some(value => typeof controlValue(value) !== 'number')) return undefined;
    return controlValue(vector[knob.apply === 'traffic.wind_east' ? 0 : 1]);
  }
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

export function ConfigPanel({ api, project: suppliedProject, onProject, onInitialize, initializing, section: requested, onSection, onClose, onInspectContract, onDraft, onSwitch, switching, morph, activeRunVersion }: {
  api: WorkbenchApi; project: Project; onProject: (project: Project) => void;
  onInitialize: (version: number) => void; initializing: boolean; section?: string; onSection: (section: string) => void; onClose: () => void;
  onSwitch?: (mode: EditMode, origin: Origin) => void; switching?: boolean; morph?: 'in' | 'out';
  onDraft?: (id: string, scenario: Scenario, dirty: boolean) => void;
  onInspectContract?: (typeId: string, field?: string) => void;
  /** Version of the run currently open in the workbench, if any; edits here never change it. */
  activeRunVersion?: number;
}) {
  const { locale } = useI18n();
  const workspaceOperations = useWorkspaceOperations(suppliedProject), project = workspaceOperations.project;
  const stagedInputs = useStagedFormStatus(project.id);
  const zh = locale === 'zh-CN';
  const L = useCallback((en: string, cn: string) => zh ? cn : en, [zh]);
  // sessionStorage survives refresh but a different tab cannot overwrite or clear this buffer.
  const [recovery] = useState(() => readRecovery(project.id));
  const [draft, setDraft] = useState<Scenario>(() => recovery.saved?.scenario ?? project.scenario as Scenario);
  const [baseRevision, setBaseRevision] = useState(() => recovery.saved?.revision ?? project.revision);
  const [baseScenario, setBaseScenario] = useState<Scenario>(() => recovery.saved?.base ?? project.scenario as Scenario);
  const [local, setLocal] = useState(() => recovery.saved ? recovery.saved.scenarioDirty !== false : false), [restored, setRestored] = useState(!!recovery.saved);
  const [busy, setBusy] = useState<string>(), [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string; textZh?: string } | undefined>(() => recovery.error
    ? { kind: 'error', text: `Could not restore local edits: ${recovery.error}`, textZh: `无法恢复本地修改：${recovery.error}` }
    : recovery.saved && recovery.saved.revision !== project.revision ? { kind: 'error', text: 'The server draft changed. Your unsaved edits are kept; save to compare and merge them.', textZh: '服务器草稿已变化。未保存的修改仍保留；保存时可对比并合并。' } : undefined);
  const [invalid, setInvalid] = useState<Set<string>>(() => new Set());
  const reportValidity = useCallback((path: string, valid: boolean) => setInvalid(previous => {
    if (previous.has(path) === !valid) return previous;
    const next = new Set(previous); if (valid) next.delete(path); else next.add(path); return next;
  }), []);
  const [focus, setFocus] = useState<string>();
  const [template, setTemplate] = useState<ScenarioTemplate>(), [algorithms, setAlgorithms] = useState<AlgorithmDescriptor[]>();
  const [knobs, setKnobs] = useState<Record<string, KnobValue>>(() => recovery.saved?.knobs ?? {});
  const [yaml, setYaml] = useState<string | undefined>(() => recovery.saved?.yaml);
  const [yamlDirty, setYamlDirty] = useState(recovery.saved?.yaml !== undefined), [yamlRevision, setYamlRevision] = useState(() => recovery.saved?.yamlRevision ?? recovery.saved?.revision ?? project.revision);
  // T4-01: compare-and-merge after a 409 — local edits stay; the user picks per section.
  const [merge, setMerge] = useState<{ server: Project; choices: Record<string, 'mine' | 'server'> }>();
  useEffect(() => { onDraft?.(project.id, draft, local); }, [project.id, draft, local, onDraft]);
  const [catalogError, setCatalogError] = useState<string>();
  // Generic removal uses the studio transport too; route its writes through
  // the same authoritative project read as placement and scenario controls.
  const studioApi = useMemo(() => new class extends StudioApi {
    override request<T>(path: string, body?: unknown): Promise<T> {
      return body === undefined ? super.request<T>(path) : api.request<T>(path, { body });
    }
  }(api.base), [api]);
  const destination = requested ?? (project.template ? 'controls' : 'space');
  const separator = destination.indexOf(':');
  const asked = (separator < 0 ? destination : destination.slice(0, separator)) as Section;
  const sectionPath = separator < 0 ? undefined : destination.slice(separator + 1);
  // Projects without a template have no scenario controls; open their first stage instead of waiting forever.
  const section: Section = asked === 'controls' && !project.template ? 'space' : asked;
  const [visited, setVisited] = useState<Section[]>([section]);
  const mounted = visited.includes(section) ? visited : [...visited, section];
  useEffect(() => { setVisited(previous => previous.includes(section) ? previous : [...previous, section]); }, [section]);
  useEffect(() => { if (sectionPath) setFocus(sectionPath); }, [sectionPath]);

  const authoring = useRef({ draft, baseScenario, baseRevision });
  authoring.current = { draft, baseScenario, baseRevision };
  const adoptProject = (saved: Project) => {
    const current = authoring.current, fresh = saved.scenario as Scenario;
    const result = rebaseDraft(current.baseScenario, current.draft, fresh);
    authoring.current = { draft: result.scenario, baseScenario: fresh, baseRevision: saved.revision };
    setDraft(result.scenario); setBaseScenario(fresh); setBaseRevision(saved.revision);
    setLocal(!sameDraftValue(result.scenario, fresh)); setRestored(false);
    if (result.conflicts.length) {
      setMerge({ server: saved, choices: {} });
      setMessage({ kind: 'error', text: 'The updated draft overlaps your edits. Compare the values below and choose what to keep.', textZh: '更新的草稿与本地修改有重叠。请对比下方的值并选择保留内容。' });
    }
  };
  const replaceProject = (saved: Project) => {
    const fresh = saved.scenario as Scenario;
    authoring.current = { draft: fresh, baseScenario: fresh, baseRevision: saved.revision };
    setDraft(fresh); setBaseScenario(fresh); setBaseRevision(saved.revision); setLocal(false); setRestored(false);
  };
  const sectionProject = (saved: Project) => { adoptProject(saved); onProject(saved); };
  // Restore once, on mount. Mutation responses must never reload an older recovery buffer.
  const seenProject = useRef(project);
  useEffect(() => {
    if (seenProject.current === project) return;
    seenProject.current = project;
    if (authoring.current.baseRevision !== project.revision || !sameDraftValue(authoring.current.baseScenario, project.scenario)) adoptProject(project);
    if (!yamlDirty) setYaml(undefined);
  }, [project]);
  useEffect(() => {
    try {
      if (local || Object.keys(knobs).length || yamlDirty) sessionStorage.setItem(localKey(project.id), JSON.stringify({ revision: baseRevision, base: baseScenario, scenario: draft, scenarioDirty: local, knobs, ...(yamlDirty ? { yaml, yamlRevision } : {}) }));
      else if (!recovery.error) sessionStorage.removeItem(localKey(project.id));
    } catch (problem) { setMessage({ kind: 'error', text: `Could not keep edits for refresh: ${String(problem)}`, textZh: `无法保留修改用于刷新恢复：${String(problem)}` }); }
  }, [draft, local, knobs, yaml, yamlDirty, baseRevision, baseScenario, project.id, recovery.error]);
  useEffect(() => { if (!local && !Object.keys(knobs).length && !yamlDirty) return; const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; }; addEventListener('beforeunload', guard); return () => removeEventListener('beforeunload', guard); }, [local, knobs, yamlDirty]);
  useEffect(() => {
    const abort = new AbortController(), templates = new TemplateApi(api.base);
    setCatalogError(undefined);
    if (project.template) void templates.template(project.template.id, abort.signal).then(setTemplate).catch(problem => { if (!abort.signal.aborted) setCatalogError(String(problem)); });
    void templates.algorithms(abort.signal).then(setAlgorithms).catch(problem => { if (!abort.signal.aborted) setCatalogError(String(problem)); });
    return () => abort.abort();
  }, [api, project.template?.id]);

  const edit = (path: string, value: unknown) => { setDraft(previous => setAt(previous, path, value)); setLocal(true); setMessage(previous => previous?.kind === 'ok' ? undefined : previous); };
  const issues: ValidationIssue[] = project.validation && project.validation.revision === project.revision && !project.validation.valid ? project.validation.issues ?? project.validation.errors.map(message => ({ source: 'scenario', path: '$', message })) : [];
  const issueSections = new Set(issues.map(issueSection));
  const fail = (problem: unknown) => {
    const text = problem instanceof ApiError ? problem.detail : String(problem);
    setMessage({ kind: 'error', text: operationText(text, false), textZh: operationText(text, true) });
  };

  const saveDraft = async (): Promise<Project | undefined> => {
    try { assertOperationsReady(currentOperationsProject(project)); } catch (problem) { fail(problem); return; }
    if (invalid.size) { fail(L('Correct the highlighted input before saving.', '请修正标记的输入后再保存。')); return; }
    if (merge) { fail(L('Choose the values in Compare and merge before saving.', '请先在“对比并合并”中选择要保留的值再保存。')); return; }
    if (!local) return currentOperationsProject(project);
    setBusy('save');
    try {
      const saved = await mutateWorkspace(project, () => api.save(project.id, draft, baseRevision));
      sectionProject(saved);
      return saved;
    } catch (problem) {
      // T4-01: a 409 opens "Compare and merge". Local edits are never discarded;
      // the user sees each changed section and chooses per section.
      if (problem instanceof ApiError && problem.status === 409) {
        try {
          const server = await api.project(project.id);
          setMerge({ server, choices: {} });
          setMessage({ kind: 'error', text: L('The draft changed elsewhere. Compare the changed sections and choose per section; your edits are kept until you save the merge.', '草稿已在其他地方被修改。请逐个分区对比并选择；合并保存之前你的修改会保留。') });
        } catch (fetchProblem) { fail(fetchProblem); }
      } else fail(problem);
      return undefined;
    } finally { setBusy(undefined); }
  };
  const conflict = merge ? changedSections(draft, merge.server.scenario as Scenario) : [];
  const chooseMerge = (section: string, take: 'mine' | 'server') => setMerge(previous => previous ? { ...previous, choices: { ...previous.choices, [section]: take } } : previous);
  const saveMerge = async () => {
    if (!merge) return;
    if (invalid.size) { fail(L('Correct the highlighted input before saving.', '请修正标记的输入后再保存。')); return; }
    const choices: SectionChoice[] = Object.entries(merge.choices).map(([section, take]) => ({ section, take }));
    if (choices.length !== conflict.length) { fail(L('Choose keep mine or take server for every changed section first.', '请先为每个变更分区选择“保留我的”或“采用服务器”。')); return; }
    setBusy('merge');
    try {
      const merged = mergeSections(draft, merge.server.scenario as Scenario, choices);
      const saved = await mutateWorkspace(merge.server, () => api.save(project.id, merged, merge.server.revision));
      replaceProject(saved);
      setMerge(undefined); onProject(saved);
      setMessage({ kind: 'ok', text: `Merged draft saved as revision ${saved.revision}.`, textZh: `合并后的草稿已保存为修订 ${saved.revision}。` });
    } catch (problem) {
      // The server may have moved again; refresh the comparison against the new revision.
      if (problem instanceof ApiError && problem.status === 409) {
        try { const server = await api.project(project.id); setMerge({ server, choices: {} }); }
        catch (fetchProblem) { fail(fetchProblem); return; }
      }
      fail(problem);
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
    try { const updated = await mutateWorkspace(saved, () => api.knobs(saved.id, changes, saved.revision)); setKnobs({}); sectionProject(updated); setMessage({ kind: 'ok', text: 'Controls applied to the draft.', textZh: '控制项已应用到草稿。' }); }
    catch (problem) { fail(problem); } finally { setBusy(undefined); }
  };
  const jump = (issue: ValidationIssue) => { const target = issueSection(issue); onSection(`${target ?? 'file'}:${issuePointer(issue.path)}`); setFocus(issuePointer(issue.path)); };
  const discard = () => { sessionStorage.removeItem(localKey(project.id)); replaceProject(project); setKnobs({}); setYaml(undefined); setYamlDirty(false); setMerge(undefined); setMessage(undefined); };
  // T1-010: an invalid saved draft must still be exportable and repairable in
  // the file editor. Loading the file with unsaved form edits keeps those
  // edits; the file editor shows the saved draft for export/repair only.
  const loadYaml = async (reload = false) => {
    setBusy('export');
    try {
      const text = await api.exportYaml(project.id); setYaml(text); setYamlRevision(project.revision);
      if (reload) setYamlDirty(false);
    } catch (problem) { fail(problem); } finally { setBusy(undefined); }
  };
  const controlsDirty = Object.keys(knobs).length > 0;
  // A success describes the saved revision; new local or invalid input invalidates that notice.
  useEffect(() => { if (local || controlsDirty || yamlDirty || invalid.size) setMessage(previous => previous?.kind === 'ok' ? undefined : previous); }, [draft, knobs, yaml, local, controlsDirty, yamlDirty, invalid]);
  const valueFor = (knob: ScenarioTemplate['knobs'][number]) => knobs[knob.id] === undefined ? controlSource(knob, draft, project.template?.knobs) : controlValue(knobs[knob.id]);
  const validNow = project.validation?.valid && project.validation.revision === project.revision && !local && !controlsDirty && !yamlDirty && !invalid.size;
  const versioned = project.latest_version && project.latest_version.revision === project.revision && !local;

  const nav: Array<{ id: Section; label: string }> = [
    ...(project.template ? [{ id: 'controls' as Section, label: L('Scenario controls', '场景控制项') }] : []),
    ...SECTIONS.map(item => ({ id: item.id as Section, label: L(item.en, item.zh) })),
    { id: 'file', label: L('File (YAML)', '文件（YAML）') },
  ];
  const goTo: NonNullable<SectionProps['goTo']> = (target, targetFocus) => { setFocus(targetFocus); onSection(targetFocus ? `${target}:${targetFocus}` : target); };
  const sectionBody = (target: Section) => {
    if (target === 'controls') {
      if (!template) return <p className="hint" role={catalogError ? 'alert' : 'status'}>{catalogError ?? L('Loading the scenario controls…', '正在加载场景控制项…')}</p>;
      return <>
        <p className="hint">{L('The controls the scenario was designed around. Applying writes them into this draft and keeps your other edits.', '该场景围绕这些控制项设计。应用后会写入当前草稿，并保留你的其他修改。')}</p>
        <div className="wb-knobs">{template.knobs.map(knob => {
          const value = valueFor(knob);
          return <div className="wb-knob" key={knob.id} data-path={knob.pointer}>{value === undefined ? <div className="wb-form-prerequisite">
            <p>{L('This setting cannot be read from the current configuration. Validate the draft, or edit its source:', '无法从当前配置读取此控制项。可校验草稿，或编辑其来源：')} {knob.pointer ?? knob.id}</p>
            <button className="wb-btn small" onClick={() => {
              const source = knob.pointer ? issueSection({ source: 'scenario', path: knob.pointer, message: '' }) : 'components';
              if (source) goTo(source, knob.pointer); else { setFocus(knob.pointer); onSection('file'); }
            }}>{L('Edit source', '编辑来源')}</button>
          </div>
            : <TemplateKnobControl disabled={workspaceOperations.applying || !!busy} knob={knob} value={value} onChange={value => setKnobs(previous => ({ ...previous, [knob.id]: value }))} onValidityChange={valid => reportValidity(`/knobs/${knob.id}`, valid)} algorithms={algorithms} />}</div>;
        })}</div>
      </>;
    }
    if (target === 'file') {
      return <>
        <p className="hint">{L('The same draft as a scenario file. Importing replaces the draft after the server compiles it.', '与表单是同一份草稿的文件形式。导入时服务器先编译，通过后替换草稿。')}</p>
        {local && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{L('Unsaved form edits are kept; this file shows the last saved draft. You can still export or repair it here, and reload the saved draft over your form edits.', '未保存的表单修改仍保留；此文件显示最近一次保存的草稿。仍可在此导出或修复，并可用已保存草稿覆盖表单修改。')}</p>}
        {yamlDirty && yamlRevision !== project.revision && <p role="alert">{L('This file was edited from an older revision. Copy your changes and reload the current draft before importing.', '此文件基于旧修订修改。请复制修改并重新加载当前草稿后导入。')}</p>}
        {busy === 'export' && <p className="hint" role="status">{L('Loading the saved scenario file…', '正在加载已保存的场景文件…')}</p>}
        {yaml === undefined ? <button className="wb-btn" disabled={workspaceOperations.applying || !!busy} onClick={() => void loadYaml()}>{busy === 'export' ? L('Loading scenario file…', '正在加载场景文件…') : L('Load scenario file', '加载场景文件')}</button>
          : <><textarea className="wb-json" style={{ minHeight: '52vh' }} spellCheck={false} aria-label={L('Scenario YAML', '场景 YAML')} value={yaml} onChange={event => { setYaml(event.target.value); setYamlDirty(true); }} />
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button className="wb-btn primary" disabled={workspaceOperations.applying || !!busy || local || yamlRevision !== project.revision} title={local ? L('Save or discard your form edits first; importing here would replace them.', '请先保存或放弃表单修改；此处导入会覆盖它们。') : yamlRevision !== project.revision ? L('Reload the file at the current revision first.', '请先按当前修订重新加载文件。') : undefined} onClick={() => { setBusy('import'); void mutateWorkspace(project, () => api.importYaml(project.id, yaml, project.revision)).then(saved => { setYamlDirty(false); setYaml(undefined); sectionProject(saved); setMessage({ kind: 'ok', text: 'Imported into the draft.', textZh: '已导入草稿。' }); }).catch(fail).finally(() => setBusy(undefined)); }}>{L('Import into draft', '导入草稿')}</button>
              <button className="wb-btn" disabled={workspaceOperations.applying || !!busy} onClick={() => void loadYaml(true)} title={local ? L('This replaces your unsaved form edits with the saved draft.', '这会用已保存草稿覆盖未保存的表单修改。') : undefined}>{L('Reload', '重新加载')}</button>
              <button className="wb-btn" disabled={workspaceOperations.applying || !!busy} onClick={() => { const url = URL.createObjectURL(new Blob([yaml], { type: 'text/yaml' })); const link = document.createElement('a'); link.href = url; link.download = `${project.id}.yaml`; link.click(); URL.revokeObjectURL(url); }}>{L('Download YAML', '下载 YAML')}</button>
            </div></>}
      </>;
    }
    const props: SectionProps = { api, studioApi, project, draft, edit, replaceDraft: scenario => { setDraft(scenario); setLocal(true); }, focus: target === section ? focus : undefined, zh, locale, L, issues, onProject: sectionProject, saveDraft, reportValidity, onInspectContract, algorithms, goTo };
    const Body = SECTION_BODIES[target as SectionId];
    if (!Body) throw Error(L(`Unknown configuration section: ${target}`, `未知配置分区：${target}`));
    return <Suspense fallback={<p className="hint" role="status">{L('Loading this section…', '正在加载此分区…')}</p>}><Body {...props} /></Suspense>;
  };

  return <StagedFormWorkspace.Provider value={{ id: project.id, revision: project.revision }}><FormValidity.Provider value={reportValidity}><aside className="wb-panel wb-right is-wide wb-glass" aria-label={L('Configure scenario', '配置场景')} data-testid="wb-config" data-morph={morph}>
    <div className="wb-panel-head as-rise"><h3>{L('Configure', '配置')} · {template && project.name === template.title ? localized(template, 'title', locale) : project.name}</h3>{onSwitch && <ModeSwitch mode="form" onChange={onSwitch} switching={switching} />}<button className="wb-x" aria-label={L('Close configuration', '关闭配置')} onClick={onClose}>✕</button></div>
    {restored && <div className="wb-restored">{L('Unsaved edits were restored from this browser.', '已从本浏览器恢复未保存的修改。')}<span className="wb-spacer" /><button className="wb-btn small" onClick={discard}>{L('Discard', '放弃')}</button></div>}
    {catalogError && <div className="wb-issues" role="alert">{catalogError}</div>}
    {issues.length > 0 && <div className="wb-issues" role="alert" data-testid="wb-issues">{issues.map((issue, index) => <button key={index} onClick={() => jump(issue)}>⚠ {[issue.entity_id, issue.field ?? issue.binding].filter(Boolean).join(' · ') || issue.path} — {issue.message}</button>)}</div>}
    {merge && <div className="wb-merge wb-glass" data-testid="wb-merge" role="region" aria-label={L('Compare and merge drafts', '对比并合并草稿')}>
      <h4>{L('Compare and merge', '对比并合并')}</h4>
      <p className="hint">{L(`The server draft moved to revision ${merge.server.revision} while you were editing revision ${baseRevision}. Your edits are kept below; choose per changed section, then save the merge.`, `服务器草稿在你编辑修订 ${baseRevision} 期间已推进到修订 ${merge.server.revision}。你的修改仍保留在下方；请逐个变更分区选择，然后保存合并。`)}</p>
      {conflict.length === 0 && <p className="hint">{L('No top-level section differs; saving the merge will keep your draft as is.', '没有差异的顶层分区；保存合并将按原样保留你的草稿。')}</p>}
      {conflict.map(row => <div key={row.section} className="wb-merge-row" data-testid={`wb-merge-${row.section}`}>
        <div className="wb-merge-name"><b className="wb-mono">{row.section}</b></div>
        <table className="wb-table">
          <thead><tr><th>{L('Field', '字段')}</th><th>{L('Mine', '我的')}</th><th>{L('Server', '服务器')}</th></tr></thead>
          <tbody>{changedFields(row.mine, row.server, `/${row.section}`).map(field => <tr key={field.path}>
            <td className="wb-mono">{field.path}</td>
            {[field.mine, field.server].map((value, index) => <td key={index}>{value === undefined ? <span className="wb-tag warn">{L('Deleted', '已删除')}</span> : <code className="wb-mono">{JSON.stringify(value)}</code>}</td>)}
          </tr>)}</tbody>
        </table>
        <div className="wb-merge-choose" role="radiogroup" aria-label={row.section}>
          <button type="button" className="wb-btn small" disabled={workspaceOperations.applying || !!busy} aria-pressed={merge.choices[row.section] === 'mine'} onClick={() => chooseMerge(row.section, 'mine')}>{L('Keep mine', '保留我的')}</button>
          <button type="button" className="wb-btn small" disabled={workspaceOperations.applying || !!busy} aria-pressed={merge.choices[row.section] === 'server'} onClick={() => chooseMerge(row.section, 'server')}>{L('Take server', '采用服务器')}</button>
        </div>
      </div>)}
      <div className="wb-merge-actions">
        <button className="wb-btn primary" disabled={workspaceOperations.applying || !!busy || !!invalid.size || conflict.length !== Object.keys(merge.choices).length} data-testid="wb-merge-save" onClick={() => void saveMerge()}>{busy === 'merge' ? L('Saving merged draft…', '正在保存合并草稿…') : L('Save merged draft', '保存合并草稿')}</button>
        <button className="wb-btn" disabled={workspaceOperations.applying || !!busy} onClick={() => setMerge(undefined)}>{L('Close without merging', '不合并，直接关闭')}</button>
      </div>
    </div>}
    <div className="wb-config">
      <nav aria-label={L('Configuration sections', '配置分区')}>{nav.map(item => <button key={item.id} aria-current={section === item.id} onClick={() => { onSection(item.id); setFocus(undefined); }}>{item.label}{issueSections.has(item.id as SectionId) && <i className="wb-issue-dot" aria-label={L('has issues', '有问题')} />}</button>)}</nav>
      <fieldset className="wb-config-body" disabled={workspaceOperations.applying} style={{ border: 0, margin: 0, minWidth: 0 }}>{mounted.map(target => <div key={target} hidden={section !== target} data-section={target}>
        <ConfigureBoundary label={nav.find(item => item.id === target)?.label ?? target} L={L}>
          <ConfigureBody render={() => sectionBody(target)} />
        </ConfigureBoundary>
      </div>)}</fieldset>
    </div>
    <div className="wb-config-foot">
      <div className="state" aria-live="polite">
        <div className="wb-config-state-copy">
        {local || controlsDirty || yamlDirty ? <b style={{ color: 'var(--wb-warn)' }}>{L('Unsaved edits', '有未保存的修改')}</b> : stagedInputs ? <b style={{ color: 'var(--wb-warn)' }}>{L('Unconfirmed form inputs', '有未确认的表单输入')}</b> : <b>{L(`Draft revision ${project.revision}`, `草稿修订 ${project.revision}`)}</b>}
        {' · '}{project.latest_version ? L(`latest version v${project.latest_version.version}${versioned ? ' (this draft)' : ' (earlier revision)'}`, `最新版本 v${project.latest_version.version}${versioned ? '（即当前草稿）' : '（较早修订）'}`) : L('no saved version yet', '尚无保存的版本')}
        {activeRunVersion !== undefined && <div className="hint">{L(`Edits here go into the draft and a new version; the open run of v${activeRunVersion} is not affected.`, `此处修改写入草稿和新版本，不会影响已打开的 v${activeRunVersion} 运行。`)}</div>}
        {message && <div style={{ color: message.kind === 'error' ? 'var(--wb-bad)' : 'var(--wb-ok)' }}>{L(message.text, message.textZh ?? message.text)}</div>}
        {workspaceOperations.applying && <div className="hint" role="status">{L('Applying a change…', '正在应用变更…')}</div>}
        </div>
        {(section === 'controls' || controlsDirty) && <button className="wb-btn primary" disabled={workspaceOperations.applying || !!busy || !controlsDirty || !!invalid.size} onClick={() => void applyKnobs()}>{busy === 'knobs' ? L('Applying controls…', '正在应用控制项…') : L('Apply controls to draft', '应用到草稿')}</button>}
      </div>
      <button className="wb-btn" disabled={workspaceOperations.applying || !local || !!busy || !!invalid.size || !!merge} onClick={() => void saveDraft()}>{busy === 'save' ? L('Saving…', '保存中…') : L('Save draft', '保存草稿')}</button>
      <button className="wb-btn" disabled={workspaceOperations.applying || !!busy || !!invalid.size || controlsDirty || yamlDirty} title={controlsDirty ? L('Apply the controls first', '请先应用控制项') : yamlDirty ? L('Import the file first', '请先导入文件') : undefined} data-testid="wb-validate" onClick={() => void validate()}>{busy === 'validate' ? L('Validating…', '校验中…') : L('Validate', '校验')}</button>
      <button className="wb-btn primary" disabled={workspaceOperations.applying || !validNow || !!versioned || !!busy} data-testid="wb-save-version" title={!validNow ? L('Validate this revision first', '请先校验当前修订') : undefined} onClick={() => void saveVersion()}>{L('Save version', '保存版本')}</button>
      <button className="wb-btn go" disabled={workspaceOperations.applying || !project.latest_version || initializing || !!busy} data-testid="wb-initialize" onClick={() => { if (project.latest_version) onInitialize(project.latest_version.version); }} title={L('Create a run of the latest saved version, held at t = 0', '以最新保存的版本创建运行，停在 t = 0')}>{initializing ? L('Initializing…', '初始化中…') : L(`Initialize v${project.latest_version?.version ?? '–'}`, `初始化 v${project.latest_version?.version ?? '–'}`)}</button>
    </div>
  </aside></FormValidity.Provider></StagedFormWorkspace.Provider>;
}
