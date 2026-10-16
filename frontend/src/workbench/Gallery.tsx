import { metricLabel } from '../newcomers/metric-help';
import { useEffect, useRef, useState } from 'react';
import type { ScenarioTemplate } from '../newcomers/api';
import { categoryMetadata } from '../newcomers/categories';
import { difficultyLabel, durationLabel } from '../newcomers/presentation';
import { localized } from '../i18n/translate';
import { useI18n } from '../i18n/I18nProvider';
import { ApiError, type Project, type WorkbenchApi } from './api';
import { Markdown } from './Markdown';
import { linkedGuide, loadGuideSource } from '../console/guides';
import { DescribeCard } from '../assistant/motion/DescribeCard';
import './gallery.css';

const relative = (iso: string, zh: boolean) => {
  const minutes = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(minutes)) return '';
  if (minutes < 1) return zh ? '刚刚' : 'just now';
  if (minutes < 60) return zh ? `${minutes} 分钟前` : `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return zh ? `${hours} 小时前` : `${hours} h ago`;
  return new Date(iso).toLocaleDateString(zh ? 'zh-CN' : 'en-US');
};

const NUMBER_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];

/** S8: the heading count is derived from the template list. English spells the number as a
 *  word up to twelve, otherwise numerals; Chinese always uses numerals. Sentence case keeps
 *  the heading's leading capital ("Nine ready-made worlds"). */
export function readyWorldsHeading(count: number): { en: string; zh: string } {
  const word = count <= 12 ? NUMBER_WORDS[count] ?? String(count) : String(count);
  const en = `${word} ready-made worlds`;
  return { en: en.charAt(0).toUpperCase() + en.slice(1), zh: `${count} 个现成的低空世界` };
}

/** Both translations live in the template's one guide; ordinary Markdown stays intact. */
export function localizedGuide(source: string, locale: string): string {
  const sections = [...source.matchAll(/^<!-- guide-language: ([\w-]+) -->\s*$/gm)];
  const index = sections.findIndex(section => section[1] === locale);
  if (index < 0) return source;
  const section = sections[index];
  return source.slice(section.index! + section[0].length, sections[index + 1]?.index ?? source.length).trim();
}

/** Gallery dialogs cover the shell, so its language setting must remain reachable here. */
function GalleryLanguage() {
  const { locale, setLocale } = useI18n(), zh = locale === 'zh-CN';
  return <button className="wb-btn" aria-label={zh ? '切换到英文' : 'Switch to Chinese'} onClick={() => setLocale(zh ? 'en-US' : 'zh-CN')}>{zh ? 'EN' : '中文'}</button>;
}

/** The scenario library and the user's projects. */
export function Gallery({ api, current, onOpenProject, onCreate, onCreateBlank, onClose, initialGuide, onDescribe }: {
  api: WorkbenchApi; current?: string; initialGuide?: string;
  onOpenProject: (id: string) => void; onCreate: (template: ScenarioTemplate) => Promise<void>; onCreateBlank: () => Promise<void>; onClose?: () => void; onDescribe?: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [templates, setTemplates] = useState<ScenarioTemplate[]>(), [projects, setProjects] = useState<Project[]>();
  const [error, setError] = useState<string>(), [guide, setGuide] = useState<ScenarioTemplate>();
  const [creating, setCreating] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  useEffect(() => { dialog.current?.querySelector<HTMLElement>('button:not(:disabled)')?.focus(); }, [guide]);
  const createBlank = async () => {
    setCreating(true); setError(undefined);
    try { await onCreateBlank(); }
    catch (problem) { setError(String(problem)); }
    finally { setCreating(false); }
  };
  useEffect(() => {
    const abort = new AbortController();
    void api.templates(abort.signal).then(rows => { setTemplates(rows); if (initialGuide) setGuide(rows.find(row => row.id === initialGuide)); }).catch(problem => { if (!abort.signal.aborted) setError(String(problem)); });
    void api.projects(abort.signal).then(setProjects).catch(problem => { if (!abort.signal.aborted) setError(String(problem)); });
    return () => abort.abort();
  }, [api]);
  const titleOf = (template: ScenarioTemplate) => localized(template, 'title', locale) ?? template.title;
  // Only derived once the list has loaded; before that the count would read "Zero worlds".
  const heading = templates ? readyWorldsHeading(templates.length) : undefined;
  return <div ref={dialog} className="wb-overlay wb-gallery-overlay" role="dialog" aria-modal="true" aria-label={L('Scenarios and projects', '场景与项目')}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (guide) setGuide(undefined); else onClose?.(); }
      if (event.key !== 'Tab') return;
      const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled)')];
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }} onClick={event => { if (event.target === event.currentTarget) onClose?.(); }}>
    <div className="wb-overlay-card wb-glass" data-testid="wb-gallery">
      {guide ? <ScenarioGuide api={api} template={guide} onBack={() => setGuide(undefined)} onCreate={onCreate} /> : <>
        <div className="wb-hero">
          <div style={{ flex: 1 }}>
            <h1>{heading ? L(heading.en, heading.zh) : L('Ready-made worlds', '现成的低空世界')}</h1>
            <p>{L('Start with a ready-made scenario, or skip this step and configure a blank project yourself. Manual setup does not require a model.', '可以从现成场景开始，也可以跳过这一步，从空白项目手动配置。手动操作无需连接模型。')}</p>
          </div>
          <button className="wb-btn primary" disabled={creating} onClick={() => void createBlank()} data-testid="wb-create-blank">{creating ? L('Creating…', '正在创建…') : L('Skip, create manually', '跳过，手动创建')}</button>
          <GalleryLanguage />
          {onClose && <button className="wb-btn" onClick={onClose}>{L('Back to the scene', '返回场景')} <span className="wb-kbd">Esc</span></button>}
        </div>
        {error && <div className="wb-issues" role="alert">{error}</div>}
        {onDescribe && <DescribeCard onStart={onDescribe} imageSrc={`${import.meta.env.BASE_URL}assistant/describe-card.webp`} />}
        <div className="wb-cards">
          {(templates ?? []).map(template => {
            const category = categoryMetadata(template.category, locale);
            return <button key={template.id} className="wb-card" data-testid={`wb-template-${template.id}`} onClick={() => setGuide(template)} aria-label={L(`Open the guide for ${titleOf(template)}`, `打开“${titleOf(template)}”指南`)}>
              {template.thumbnail_url ? <img alt="" src={new URL(template.thumbnail_url, api.base + '/').href} loading="lazy" /> : <img alt="" />}
              <div className="wb-card-body">
                <h4>{titleOf(template)}</h4>
                <p>{localized(template, 'summary', locale) ?? template.summary}</p>
                <div className="wb-card-meta"><span className="wb-tag" style={{ background: category.color + '22', color: category.color }}>{category.label}</span><span className="wb-tag">{difficultyLabel(template.difficulty, locale)}</span><span className="wb-tag">{durationLabel(template.duration_s, locale)}</span><span className="wb-tag wb-mono">{template.id}</span></div>
              </div>
            </button>;
          })}
          {!templates && !error && <div className="wb-empty">{L('Loading scenarios…', '正在加载场景…')}</div>}
        </div>
        <div className="wb-section-title">{L('Your projects', '我的项目')}</div>
        {projects && !projects.length && <div className="wb-empty"><strong>{L('No projects yet', '还没有项目')}</strong>{L('Create a blank project or start from a scenario above.', '可以创建空白项目，也可以从上面的场景开始。')}</div>}
        <div className="wb-projects">
          {(projects ?? []).map(project => <button key={project.id} className="wb-row wb-glass" style={{ padding: 12 }} title={project.id} aria-current={project.id === current} onClick={() => onOpenProject(project.id)} data-testid="wb-project">
            <span className="wb-dot" style={{ background: project.dirty ? 'var(--wb-warn)' : 'var(--wb-ok)' }} />
            <span className="wb-row-main"><div>{project.name} <span className="wb-tag wb-mono">{project.id.slice(-8)}</span></div><small>{project.latest_version ? L(`Version ${project.latest_version.version}`, `版本 ${project.latest_version.version}`) : L('No saved version', '尚无保存的版本')}{project.dirty ? L(' · draft changes', ' · 草稿有改动') : ''} · {relative(project.updated_at, zh)}</small></span>
            {project.template && <span className="wb-tag">{project.template.id}</span>}
          </button>)}
        </div>
      </>}
    </div>
  </div>;
}

function ScenarioGuide({ api, template, onBack, onCreate }: { api: WorkbenchApi; template: ScenarioTemplate; onBack: () => void; onCreate: (template: ScenarioTemplate) => Promise<void> }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [markdown, setMarkdown] = useState<string>(), [problem, setProblem] = useState<string>(), [busy, setBusy] = useState(false);
  useEffect(() => { const abort = new AbortController(); setMarkdown(undefined); setProblem(undefined); void api.guide(template.id, abort.signal).then(guide => setMarkdown(guide.markdown)).catch(error => { if (!abort.signal.aborted) setProblem(error instanceof ApiError && error.status === 404 ? L('This scenario has no written guide.', '该场景没有书面指南。') : String(error)); }); return () => abort.abort(); }, [api, template.id]);
  // Referenced platform guides open in place; Back walks the trail to the scenario's own guide.
  const [trail, setTrail] = useState<{ path: string; text?: string; problem?: string }[]>([]);
  useEffect(() => setTrail([]), [template.id]);
  const reading = trail.at(-1);
  const opener = (from: string) => (href: string) => {
    const path = linkedGuide(href, from);
    if (!path) return undefined;
    return () => {
      setTrail(current => [...current, { path }]);
      void loadGuideSource(path).then(text => setTrail(current => current.map(entry => entry.path === path && entry.text === undefined ? { ...entry, text: String(text) } : entry)))
        .catch(error => setTrail(current => current.map(entry => entry.path === path ? { ...entry, problem: String(error) } : entry)));
    };
  };
  const observe = (zh ? template.observe_zh : undefined) ?? template.observe, attempt = (zh ? template.try_zh : undefined) ?? template.try;
  return <div data-testid="wb-guide">
    <div className="wb-hero">
      <button className="wb-btn" onClick={onBack}>← {L('All scenarios', '全部场景')}</button>
      <div style={{ flex: 1 }}><h1>{localized(template, 'title', locale) ?? template.title}</h1><p>{localized(template, 'summary', locale) ?? template.summary}</p></div>
      <GalleryLanguage />
      <button className="wb-btn primary" disabled={busy} data-testid="wb-create-project" onClick={() => { setBusy(true); void onCreate(template).catch(error => setProblem(String(error))).finally(() => setBusy(false)); }}>{busy ? L('Creating…', '正在创建…') : L('Create a project from this scenario', '以此场景创建项目')}</button>
    </div>
    <div className="wb-guide">
      {reading ? <div key={`${trail.length}:${reading.path}`} data-testid="wb-guide-reference" ref={node => node?.scrollIntoView?.({ block: 'start' })}>
        <button className="wb-btn" onClick={() => setTrail(current => current.slice(0, -1))}>← {trail.length > 1 ? L('Back', '返回') : L('Back to the scenario guide', '返回场景指南')}</button>
        <p className="hint">{L(`Platform guide · docs/${reading.path}`, `平台文档（英文原文）· docs/${reading.path}`)}</p>
        {reading.problem ? <p className="hint" role="alert">{reading.problem}</p>
          : reading.text === undefined ? <p className="hint" role="status">{L('Loading the guide…', '正在加载指南…')}</p>
          : <Markdown key={reading.path} source={reading.text} openLink={opener(`docs/${reading.path}`)} />}
      </div>
      : <div>{problem && <p className="hint" role="alert">{problem}</p>}{markdown ? <Markdown source={localizedGuide(markdown, locale)} openLink={opener(`scenarios/templates/${template.id}/README.md`)} /> : !problem && <p className="hint" role="status">{L('Loading the guide…', '正在加载指南…')}</p>}</div>}
      <aside>
        {template.thumbnail_url && <img className="hero" alt="" src={new URL(template.thumbnail_url, api.base + '/').href} />}
        <div className="wb-section-title">{L('What to watch', '观察要点')}</div><ul>{observe.map(item => <li key={item}>{item}</li>)}</ul>
        <div className="wb-section-title">{L('Things to try', '可以尝试')}</div><ul>{attempt.map(item => <li key={item}>{item}</li>)}</ul>
        <div className="wb-section-title">{L('Controls you can change', '可调整的控制项')}</div>
        <div className="wb-card-meta">{template.knobs.map(knob => <span key={knob.id} className="wb-tag" title={knob.id}>{(zh ? knob.label_zh : undefined) ?? knob.label}</span>)}</div>
        <div className="wb-section-title">{L('KPIs measured', '衡量指标')}</div>
        <div className="wb-card-meta">{template.kpis.map(kpi => <span key={kpi} className="wb-tag info" title={kpi}>{metricLabel(kpi, kpi, locale)}</span>)}</div>
        <p className="hint" style={{ marginTop: 14 }}>{L('Creating a project copies this scenario into an editable draft. Nothing runs until you save a version, initialize it and press Start.', '创建项目会把此场景复制为可编辑的草稿。保存版本、初始化并按下“启动”之前，不会运行任何仿真。')}</p>
      </aside>
    </div>
  </div>;
}
