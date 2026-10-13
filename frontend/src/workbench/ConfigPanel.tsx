import { ModeSwitch, type EditMode, type Origin } from '../assistant/ModeSwitch';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { TemplateApi, type AlgorithmDescriptor, type KnobValue, type ScenarioTemplate } from '../newcomers/api';
import { TemplateKnobControl } from '../newcomers/TemplateKnobControl';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { ApiError, type Project, type SectionId, type ValidationIssue, type WorkbenchApi } from './api';
import { FormValidity, getAt, setAt } from './form';
import { changedSections, mergeSections, sectionSummary, type SectionChoice } from './merge-draft';
import { StudioApi } from '../studio/api';
import { SECTIONS, issuePointer, issueSection } from './sections/issues';
import type { SectionProps } from './sections/types';
import { SpaceSection } from './sections/SpaceSection';
import { ObjectsSection } from './sections/ObjectsSection';
import { RelationsSection } from './sections/RelationsSection';
import { TasksSection } from './sections/TasksSection';
import { ComponentsSection } from './sections/ComponentsSection';
import { AlgorithmsSection } from './sections/AlgorithmsSection';
import { RunSection } from './sections/RunSection';

const SECTION_BODIES: Record<SectionId, (props: SectionProps) => JSX.Element> = {
  space: SpaceSection, objects: ObjectsSection, relations: RelationsSection, tasks: TasksSection,
  components: ComponentsSection, algorithms: AlgorithmsSection, run: RunSection,
};

type Scenario = Record<string, unknown>;
type Section = SectionId | 'controls' | 'file';
export { SECTIONS, issueSection, issuePointer } from './sections/issues';

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

export function ConfigPanel({ api, project, onProject, onInitialize, initializing, section: requested, onSection, onClose, onInspectContract, onDraft, onSwitch, switching, morph, activeRunVersion }: {
  api: WorkbenchApi; project: Project; onProject: (project: Project) => void;
  onInitialize: () => void; initializing: boolean; section?: string; onSection: (section: string) => void; onClose: () => void;
  onSwitch?: (mode: EditMode, origin: Origin) => void; switching?: boolean; morph?: 'in' | 'out';
  onDraft?: (id: string, scenario: Scenario, dirty: boolean) => void;
  onInspectContract?: (typeId: string, field?: string) => void;
  /** Version of the run currently open in the workbench, if any; edits here never change it. */
  activeRunVersion?: number;
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
  // T4-01: compare-and-merge after a 409 — local edits stay; the user picks per section.
  const [merge, setMerge] = useState<{ server: Project; choices: Record<string, 'mine' | 'server'> }>();
  useEffect(() => { onDraft?.(project.id, draft, local); }, [project.id, draft, local, onDraft]);
  const [catalogError, setCatalogError] = useState<string>();
  const studioApi = useMemo(() => new StudioApi(api.base), [api.base]);
  const [asked, sectionPath] = (requested ?? (project.template ? 'controls' : 'space')).split(':') as [Section, string | undefined];
  // Projects without a template have no scenario controls; open their first stage instead of waiting forever.
  const section: Section = asked === 'controls' && !project.template ? 'space' : asked;
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
    const choices: SectionChoice[] = Object.entries(merge.choices).map(([section, take]) => ({ section, take }));
    if (choices.length !== conflict.length) { fail(L('Choose keep mine or take server for every changed section first.', '请先为每个变更分区选择“保留我的”或“采用服务器”。')); return; }
    setBusy('merge');
    try {
      const merged = mergeSections(draft, merge.server.scenario as Scenario, choices);
      const saved = await api.save(project.id, merged, merge.server.revision);
      localStorage.removeItem(localKey(project.id));
      setLocal(false); setRestored(false); setBaseRevision(saved.revision); setKnobs({}); setYaml(undefined); setYamlDirty(false);
      setMerge(undefined); onProject(saved); setDraft(saved.scenario as Scenario);
      setMessage({ kind: 'ok', text: `Merged draft saved as revision ${saved.revision}.`, textZh: `合并后的草稿已保存为修订 ${saved.revision}。` });
    } catch (problem) {
      // The server may have moved again; refresh the comparison against the new revision.
      if (problem instanceof ApiError && problem.status === 409) {
        try { const server = await api.project(project.id); setMerge({ server, choices: {} }); } catch { setMerge(undefined); }
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
    try { const updated = await api.knobs(saved.id, changes, saved.revision); setKnobs({}); localStorage.removeItem(localKey(project.id)); onProject(updated); setMessage({ kind: 'ok', text: 'Controls applied to the draft.', textZh: '控制项已应用到草稿。' }); }
    catch (problem) { fail(problem); } finally { setBusy(undefined); }
  };
  const jump = (issue: ValidationIssue) => { const target = issueSection(issue); onSection(`${target ?? 'file'}:${issuePointer(issue.path)}`); setFocus(issuePointer(issue.path)); };
  const discard = () => { localStorage.removeItem(localKey(project.id)); setLocal(false); setRestored(false); setKnobs({}); setYaml(undefined); setYamlDirty(false); setDraft(project.scenario as Scenario); setBaseRevision(project.revision); };
  // T1-010: an invalid saved draft must still be exportable and repairable in
  // the file editor. Loading the file with unsaved form edits keeps those
  // edits; the file editor shows the saved draft for export/repair only.
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

  const nav: Array<{ id: Section; label: string }> = [
    ...(project.template ? [{ id: 'controls' as Section, label: L('Scenario controls', '场景控制项') }] : []),
    ...SECTIONS.map(item => ({ id: item.id as Section, label: zh ? item.zh : item.en })),
    { id: 'file', label: L('File (YAML)', '文件（YAML）') },
  ];
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
        {local && <p className="hint" style={{ color: 'var(--wb-warn)' }}>{L('Unsaved form edits are kept; this file shows the last saved draft. You can still export or repair it here, and reload the saved draft over your form edits.', '未保存的表单修改仍保留；此文件显示最近一次保存的草稿。仍可在此导出或修复，并可用已保存草稿覆盖表单修改。')}</p>}
        {yamlDirty && yamlRevision !== project.revision && <p role="alert">{L('This file was edited from an older revision. Copy your changes and reload the current draft before importing.', '此文件基于旧修订修改。请复制修改并重新加载当前草稿后导入。')}</p>}
        {busy === 'export' && <p className="hint" role="status">{L('Loading the saved scenario file…', '正在加载已保存的场景文件…')}</p>}
        {yaml === undefined ? <button className="wb-btn" disabled={!!busy} onClick={() => void loadYaml()}>{busy === 'export' ? L('Loading scenario file…', '正在加载场景文件…') : L('Load scenario file', '加载场景文件')}</button>
          : <><textarea className="wb-json" style={{ minHeight: '52vh' }} spellCheck={false} aria-label={L('Scenario YAML', '场景 YAML')} value={yaml} onChange={event => { setYaml(event.target.value); setYamlDirty(true); }} />
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button className="wb-btn primary" disabled={!!busy || local || yamlRevision !== project.revision} title={local ? L('Save or discard your form edits first; importing here would replace them.', '请先保存或放弃表单修改；此处导入会覆盖它们。') : yamlRevision !== project.revision ? L('Reload the file at the current revision first.', '请先按当前修订重新加载文件。') : undefined} onClick={() => { setBusy('import'); void api.importYaml(project.id, yaml, project.revision).then(saved => { localStorage.removeItem(localKey(project.id)); setYamlDirty(false); setYaml(undefined); onProject(saved); setDraft(saved.scenario as Scenario); setMessage({ kind: 'ok', text: 'Imported into the draft.', textZh: '已导入草稿。' }); }).catch(fail).finally(() => setBusy(undefined)); }}>{L('Import into draft', '导入草稿')}</button>
              <button className="wb-btn" disabled={!!busy} onClick={() => void loadYaml(true)} title={local ? L('This replaces your unsaved form edits with the saved draft.', '这会用已保存草稿覆盖未保存的表单修改。') : undefined}>{L('Reload', '重新加载')}</button>
              <button className="wb-btn" disabled={!!busy} onClick={() => { const url = URL.createObjectURL(new Blob([yaml], { type: 'text/yaml' })); const link = document.createElement('a'); link.href = url; link.download = `${project.id}.yaml`; link.click(); URL.revokeObjectURL(url); }}>{L('Download YAML', '下载 YAML')}</button>
            </div></>}
      </>;
    }
    const props: SectionProps = { api, studioApi, project, draft, edit, replaceDraft: scenario => { setDraft(scenario); setLocal(true); }, focus, zh, locale, L, issues, onProject, saveDraft, reportValidity, onInspectContract, algorithms };
    const Body = SECTION_BODIES[section as SectionId];
    return <Body {...props} />;
  };

  return <FormValidity.Provider value={reportValidity}><aside className="wb-panel wb-right is-wide wb-glass" aria-label={L('Configure scenario', '配置场景')} data-testid="wb-config" data-morph={morph}>
    <div className="wb-panel-head as-rise"><h3>{L('Configure', '配置')} · {template && project.name === template.title ? localized(template, 'title', locale) : project.name}</h3>{onSwitch && <ModeSwitch mode="form" onChange={onSwitch} switching={switching} />}<button className="wb-x" aria-label={L('Close configuration', '关闭配置')} onClick={onClose}>✕</button></div>
    {restored && <div className="wb-restored">{L('Unsaved edits were restored from this browser.', '已从本浏览器恢复未保存的修改。')}<span className="wb-spacer" /><button className="wb-btn small" onClick={discard}>{L('Discard', '放弃')}</button></div>}
    {catalogError && <div className="wb-issues" role="alert">{catalogError}</div>}
    {issues.length > 0 && <div className="wb-issues" role="alert" data-testid="wb-issues">{issues.map((issue, index) => <button key={index} onClick={() => jump(issue)}>⚠ {[issue.entity_id, issue.field ?? issue.binding].filter(Boolean).join(' · ') || issue.path} — {issue.message}</button>)}</div>}
    {merge && <div className="wb-merge wb-glass" data-testid="wb-merge" role="region" aria-label={L('Compare and merge drafts', '对比并合并草稿')}>
      <h4>{L('Compare and merge', '对比并合并')}</h4>
      <p className="hint">{zh ? `服务器草稿在你编辑修订 ${baseRevision} 期间已推进到修订 ${merge.server.revision}。你的修改仍保留在下方；请逐个变更分区选择，然后保存合并。` : `The server draft moved to revision ${merge.server.revision} while you were editing revision ${baseRevision}. Your edits are kept below; choose per changed section, then save the merge.`}</p>
      {conflict.length === 0 && <p className="hint">{L('No top-level section differs; saving the merge will keep your draft as is.', '没有差异的顶层分区；保存合并将按原样保留你的草稿。')}</p>}
      {conflict.map(row => <div key={row.section} className="wb-merge-row" data-testid={`wb-merge-${row.section}`}>
        <div className="wb-merge-name"><b className="wb-mono">{row.section}</b><small>{L('mine', '我的')}: {sectionSummary(row.section, row.mine, locale)} · {L('server', '服务器')}: {sectionSummary(row.section, row.server, locale)}</small></div>
        <div className="wb-merge-choose" role="radiogroup" aria-label={row.section}>
          <button type="button" className="wb-btn small" aria-pressed={merge.choices[row.section] !== 'server'} onClick={() => chooseMerge(row.section, 'mine')}>{L('Keep mine', '保留我的')}</button>
          <button type="button" className="wb-btn small" aria-pressed={merge.choices[row.section] === 'server'} onClick={() => chooseMerge(row.section, 'server')}>{L('Take server', '采用服务器')}</button>
        </div>
      </div>)}
      <div className="wb-merge-actions">
        <button className="wb-btn primary" disabled={!!busy || conflict.length !== Object.keys(merge.choices).length} data-testid="wb-merge-save" onClick={() => void saveMerge()}>{busy === 'merge' ? L('Saving merged draft…', '正在保存合并草稿…') : L('Save merged draft', '保存合并草稿')}</button>
        <button className="wb-btn" disabled={!!busy} onClick={() => setMerge(undefined)}>{L('Close without merging', '不合并，直接关闭')}</button>
      </div>
    </div>}
    <div className="wb-config">
      <nav aria-label={L('Configuration sections', '配置分区')}>{nav.map(item => <button key={item.id} aria-current={section === item.id} onClick={() => { if (invalid.size) { fail(L('Correct the highlighted input before changing sections.', '请修正标记的输入后再切换分区。')); return; } onSection(item.id); setFocus(undefined); }}>{item.label}{issueSections.has(item.id as SectionId) && <i className="wb-issue-dot" aria-label={L('has issues', '有问题')} />}</button>)}</nav>
      <div className="wb-config-body">{sectionBody()}</div>
    </div>
    <div className="wb-config-foot">
      <div className="state" aria-live="polite">
        <div className="wb-config-state-copy">
        {local || controlsDirty || yamlDirty ? <b style={{ color: 'var(--wb-warn)' }}>{L('Unsaved edits', '有未保存的修改')}</b> : <b>{L(`Draft revision ${project.revision}`, `草稿修订 ${project.revision}`)}</b>}
        {' · '}{project.latest_version ? L(`latest version v${project.latest_version.version}${versioned ? ' (this draft)' : ' (earlier revision)'}`, `最新版本 v${project.latest_version.version}${versioned ? '（即当前草稿）' : '（较早修订）'}`) : L('no saved version yet', '尚无保存的版本')}
        {activeRunVersion !== undefined && <div className="hint">{L(`Edits here go into the draft and a new version; the open run of v${activeRunVersion} is not affected.`, `此处修改写入草稿和新版本，不会影响已打开的 v${activeRunVersion} 运行。`)}</div>}
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
