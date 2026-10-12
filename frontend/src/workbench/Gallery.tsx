import { useEffect, useState } from 'react';
import type { ScenarioTemplate } from '../newcomers/api';
import { categoryMetadata } from '../newcomers/categories';
import { difficultyLabel, durationLabel } from '../newcomers/TemplateGallery';
import { localized } from '../i18n/translate';
import { useI18n } from '../i18n/I18nProvider';
import { ApiError, type Project, type WorkbenchApi } from './api';
import { Markdown } from './Markdown';

const relative = (iso: string, zh: boolean) => {
  const minutes = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(minutes)) return '';
  if (minutes < 1) return zh ? '刚刚' : 'just now';
  if (minutes < 60) return zh ? `${minutes} 分钟前` : `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return zh ? `${hours} 小时前` : `${hours} h ago`;
  return new Date(iso).toLocaleDateString(zh ? 'zh-CN' : 'en-US');
};

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

/** The scenario library (nine ready-made worlds) and the user's projects. */
export function Gallery({ api, current, onOpenProject, onCreate, onClose, initialGuide, onDescribe }: {
  api: WorkbenchApi; current?: string; initialGuide?: string;
  onOpenProject: (id: string) => void; onCreate: (template: ScenarioTemplate) => Promise<void>; onClose?: () => void; onDescribe?: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [templates, setTemplates] = useState<ScenarioTemplate[]>(), [projects, setProjects] = useState<Project[]>();
  const [error, setError] = useState<string>(), [guide, setGuide] = useState<ScenarioTemplate>();
  useEffect(() => {
    const abort = new AbortController();
    void api.templates(abort.signal).then(rows => { setTemplates(rows); if (initialGuide) setGuide(rows.find(row => row.id === initialGuide)); }).catch(problem => { if (!abort.signal.aborted) setError(String(problem)); });
    void api.projects(abort.signal).then(setProjects).catch(problem => { if (!abort.signal.aborted) setError(String(problem)); });
    return () => abort.abort();
  }, [api]);
  useEffect(() => { const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { if (guide) setGuide(undefined); else onClose?.(); } }; addEventListener('keydown', key); return () => removeEventListener('keydown', key); }, [guide, onClose]);
  const titleOf = (template: ScenarioTemplate) => localized(template, 'title', locale) ?? template.title;
  return <div className="wb-overlay" role="dialog" aria-label={L('Scenarios and projects', '场景与项目')} onClick={event => { if (event.target === event.currentTarget) onClose?.(); }}>
    <div className="wb-overlay-card wb-glass" data-testid="wb-gallery">
      {guide ? <ScenarioGuide api={api} template={guide} onBack={() => setGuide(undefined)} onCreate={onCreate} /> : <>
        <div className="wb-hero">
          <div style={{ flex: 1 }}>
            <h1>{L('Nine ready-made worlds', '九个现成的低空世界')}</h1>
            <p>{L('Each scenario has controls to change and KPIs to measure the result. Open one to read its guide and create your own project from it.', '每个场景都有可调的控制项和衡量结果的指标。点开场景阅读指南，并以它为起点创建自己的项目。')}</p>
          </div>
          <GalleryLanguage />
          {onClose && <button className="wb-btn" onClick={onClose}>{L('Back to the scene', '返回场景')} <span className="wb-kbd">Esc</span></button>}
        </div>
        {error && <div className="wb-issues" role="alert">{error}</div>}
        {onDescribe && <button className="as-describe" onClick={onDescribe} data-testid="wb-describe">
          <img alt="" src={`${import.meta.env.BASE_URL}assistant/describe-card.webp`} />
          <span className="as-describe-body">
            <small>{L('Assistant', '助手')}</small>
            <strong>{L('Describe a scenario in your own words', '用你自己的话描述一个场景')}</strong>
            <span>{L('“A logistics drone notices someone has fallen, hands its parcel to a nearby courier and goes to help.” You get a concrete plan to review, then confirm once.', '“物流无人机发现有人跌倒，把包裹交给附近的配送员，然后前去查看。”你会先看到具体方案，确认一次即可应用。')}</span>
            <em>{L('Start a conversation', '开始对话')} →</em>
          </span>
        </button>}
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
        {projects && !projects.length && <div className="wb-empty"><strong>{L('No projects yet', '还没有项目')}</strong>{L('Create one from a scenario above.', '从上面的场景创建一个。')}</div>}
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
  const observe = (zh ? template.observe_zh : undefined) ?? template.observe, attempt = (zh ? template.try_zh : undefined) ?? template.try;
  return <div data-testid="wb-guide">
    <div className="wb-hero">
      <button className="wb-btn" onClick={onBack}>← {L('All scenarios', '全部场景')}</button>
      <div style={{ flex: 1 }}><h1>{localized(template, 'title', locale) ?? template.title}</h1><p>{localized(template, 'summary', locale) ?? template.summary}</p></div>
      <GalleryLanguage />
      <button className="wb-btn primary" disabled={busy} data-testid="wb-create-project" onClick={() => { setBusy(true); void onCreate(template).catch(error => setProblem(String(error))).finally(() => setBusy(false)); }}>{busy ? L('Creating…', '正在创建…') : L('Create a project from this scenario', '以此场景创建项目')}</button>
    </div>
    <div className="wb-guide">
      <div>{problem && <p className="hint" role="alert">{problem}</p>}{markdown ? <Markdown source={localizedGuide(markdown, locale)} /> : !problem && <p className="hint" role="status">{L('Loading the guide…', '正在加载指南…')}</p>}</div>
      <aside>
        {template.thumbnail_url && <img className="hero" alt="" src={new URL(template.thumbnail_url, api.base + '/').href} />}
        <div className="wb-section-title">{L('What to watch', '观察要点')}</div><ul>{observe.map(item => <li key={item}>{item}</li>)}</ul>
        <div className="wb-section-title">{L('Things to try', '可以尝试')}</div><ul>{attempt.map(item => <li key={item}>{item}</li>)}</ul>
        <div className="wb-section-title">{L('Controls you can change', '可调整的控制项')}</div>
        <div className="wb-card-meta">{template.knobs.map(knob => <span key={knob.id} className="wb-tag" title={knob.id}>{(zh ? knob.label_zh : undefined) ?? knob.label}</span>)}</div>
        <div className="wb-section-title">{L('KPIs measured', '衡量指标')}</div>
        <div className="wb-card-meta">{template.kpis.map(kpi => <span key={kpi} className="wb-tag info wb-mono">{kpi}</span>)}</div>
        <p className="hint" style={{ marginTop: 14 }}>{L('Creating a project copies this scenario into an editable draft. Nothing runs until you save a version, initialize it and press Start.', '创建项目会把此场景复制为可编辑的草稿。保存版本、初始化并按下“启动”之前，不会运行任何仿真。')}</p>
      </aside>
    </div>
  </div>;
}
