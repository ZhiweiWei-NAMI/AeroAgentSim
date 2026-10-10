import React, { useEffect, useMemo, useRef, useState } from 'react';
import {Drawer} from 'antd';
import {useNavigate} from 'react-router-dom';
import {Details} from '../console/Details';
import { TemplateApi, templateRunUrl, type KnobValue, type ScenarioTemplate, type TemplateRunReceipt } from './api';
import { categoryMetadata, TEMPLATE_CATEGORIES, CategoryIcon } from './categories';
import CustomizeTemplate from './CustomizeTemplate';
import ExperimentPanel from './ExperimentPanel';
import { useI18n } from '../i18n/I18nProvider';
import { localized, translate } from '../i18n/translate';
import './gallery.css';

/* TemplateGallery — the "Pick a scenario" gallery on the newcomer home.
 * Data comes only from GET /v1/templates; loading, error and empty states are
 * truthful (no fake catalog, no invented counts). */

function hashOf(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

/** Deterministic inline-SVG placeholder used when a template has no thumbnail.png. */
export function TemplateThumb({ template, apiBase, height = 96 }: { template: ScenarioTemplate; apiBase?:string; height?: number }) {
  const { t, locale } = useI18n();
  if (template.thumbnail || template.thumbnail_url) {
    return <img className="template-card-thumb" src={new URL(template.thumbnail_url??`/v1/templates/${encodeURIComponent(template.id)}/thumbnail`,apiBase??window.location.origin).href} alt="" loading="lazy" />;
  }
  const h = hashOf(template.id);
  const w = 280;
  const color = categoryMetadata(template.category).color;
  const seed = h % 360;
  const buildings = Array.from({ length: 6 }, (_, i) => {
    const bh = 14 + ((h >>> (i * 3)) % 26);
    return { x: 14 + i * 42, y: height - bh, w: 24 + (i % 3) * 8, bh };
  });
  return (
    <svg className="template-card-thumb" viewBox={`0 0 ${w} ${height}`} width="100%" height={height} role="img" aria-label={t('{title} — schematic preview', { title: localized(template, 'title', locale) })}>
      <defs>
        <linearGradient id={`sky-${h}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={`hsl(${seed}, 32%, 92%)`} />
          <stop offset="100%" stopColor={`hsl(${(seed + 40) % 360}, 28%, 82%)`} />
        </linearGradient>
      </defs>
      <rect width={w} height={height} fill={`url(#sky-${h})`} />
      {buildings.map((b, i) => <rect key={i} x={b.x} y={b.y} width={b.w} height={b.bh} fill="#9aa7b2" opacity={0.55} rx={1} />)}
      <path d={`M8 ${height * 0.42} Q ${w * 0.35} ${height * 0.14} ${w * 0.62} ${height * 0.4} T ${w - 8} ${height * 0.3}`} fill="none" stroke={color} strokeWidth="2" strokeDasharray="5 4" opacity={0.9} />
      <circle cx={w * 0.62} cy={height * 0.4} r={5} fill={color} />
      <circle cx={w * 0.62} cy={height * 0.4} r={9} fill="none" stroke={color} strokeWidth="1.2" opacity={0.5} />
    </svg>
  );
}

export function difficultyLabel(difficulty: ScenarioTemplate['difficulty'], locale = 'en-US'): string {
  const t = (key: string) => translate(locale, key);
  return difficulty === 'beginner' ? t('Beginner friendly') : difficulty === 'intermediate' ? t('Some experience helps') : t('Advanced');
}

export function durationLabel(durationS: number, locale = 'en-US'): string {
  const t = (key: string, vars: Record<string, number>) => translate(locale, key, vars);
  if (durationS >= 60) {
    return t('about {minutes} min simulated', { minutes: Math.round(durationS / 60) });
  }
  return t('about {seconds} s simulated', { seconds: durationS });
}

interface GalleryState { status: 'loading' | 'ready' | 'error'; templates: ScenarioTemplate[]; error?: string }

export function TemplateCard({ template, api, apiBase, onCustomize }: {
  template: ScenarioTemplate; api: TemplateApi; apiBase: string; onCustomize: (t: ScenarioTemplate) => void;
}) {
  const { t, locale } = useI18n();
  const [running, setRunning] = useState(false);
  const [action, setAction] = useState<'run' | 'studio'>('run');
  const [error, setError] = useState<string>();
  const navigate=useNavigate();
  const meta = categoryMetadata(template.category, locale);

  const run = async () => {
    setAction('run');
    setRunning(true);
    setError(undefined);
    try {
      const defaults: Record<string, KnobValue> = {};
      for (const knob of template.knobs) defaults[knob.id] = knob.default;
      const receipt=await api.start(template.id,defaults);navigate(templateRunUrl(receipt,apiBase,template.id));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setRunning(false);
    }
  };

  const studio=async()=>{setAction('studio');setRunning(true);setError(undefined);try{const receipt=await api.workspace(template.id);navigate(`/studio?workspace=${encodeURIComponent(receipt.workspace_id)}&api=${encodeURIComponent(apiBase)}`);}catch(problem){setError(String(problem));}finally{setRunning(false);}};
  return (
    <li className="template-card" data-testid={`template-${template.id}`} style={{ '--category-color': meta.color } as React.CSSProperties}>
      <TemplateThumb template={template} apiBase={apiBase} />
      <h3>{localized(template, 'title', locale)}</h3>
      <p className="template-card-summary">{localized(template, 'summary', locale)}</p>
      <div className="template-card-meta">
        <span className="template-category-chip"><CategoryIcon category={template.category} size={13} /> {meta.label}</span>
        <span>{difficultyLabel(template.difficulty, locale)}</span>
        <span>{durationLabel(template.duration_s, locale)}</span>
      </div>
      <div className="template-card-actions">
        <button type="button" className="console-btn console-btn-primary" disabled={running} onClick={() => void run()} data-testid={`run-template-${template.id}`}>
          {running && action === 'run' ? t('Starting…') : t('Run now')}
        </button>
        <button type="button" className="console-btn" onClick={() => onCustomize(template)} data-testid={`customize-template-${template.id}`}>
          {t('Customize')}
        </button>
        <button className="console-btn" onClick={()=>void studio()} disabled={running} data-testid={`studio-template-${template.id}`}>{running && action === 'studio' ? t('Opening…') : t('Open in Studio')}</button>
      </div>
      {error && (
        <div className="template-error-card" role="alert">
          <strong>{action === 'studio' ? t('Could not open the scenario in Studio.') : t('Could not start the run.')}</strong>
          <Details title={action === 'studio' ? t('Workspace request') : t('Start request')}><p>{error}</p></Details>
          <button type="button" className="console-btn" disabled={running} onClick={() => void studio()}>{action === 'studio' ? t('Try again') : t('Try in Studio instead')}</button>
        </div>
      )}

    </li>
  );
}

export default function TemplateGallery({ apiBase }: { apiBase: string }) {
  const { t, locale } = useI18n();
  const api = useMemo(() => new TemplateApi(apiBase), [apiBase]);
  const [state, setState] = useState<GalleryState>({ status: 'loading', templates: [] });
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [customizing, setCustomizing] = useState<ScenarioTemplate>();
  const [experiment,setExperiment]=useState(false);
  const attempt=useRef(0);

  const load = () => {
    const current = ++attempt.current;
    setState({ status: 'loading', templates: [] });
    api.templates()
      .then(list => {
        if (attempt.current !== current) return;
        setState(Array.isArray(list) && list.every(t => t && typeof t.id === 'string' && typeof t.title === 'string')
          ? { status: 'ready', templates: list }
          : { status: 'error', templates: [], error: 'The templates service returned an unexpected shape.' });
      })
      .catch(error => {
        if (attempt.current !== current) return;
        setState({ status: 'error', templates: [], error: String(error instanceof Error ? error.message : error) });
      });
  };
  useEffect(() => {
    setCustomizing(undefined); setExperiment(false); load();
    return () => { ++attempt.current; };
  }, [api]); // eslint-disable-line react-hooks/exhaustive-deps

  const visible = state.status === 'ready'
    ? (selectedCategory === 'all' ? state.templates : state.templates.filter(t => t.category === selectedCategory))
    : [];
  const presentCategories = new Set(state.templates.map(t => t.category));

  return (
    <div data-testid="template-gallery">
      {state.status === 'loading' && <div className="console-loading">{t('Loading scenario templates…')}</div>}
      {state.status === 'error' && (
        <div className="template-error-card" role="alert">
          <strong>{t('Could not load scenario templates.')}</strong>
          <p className="template-card-summary">{t('The templates service is not reachable right now. You can still open the Studio and build or run scenarios there.')}</p>
          <Details title={t('Template request')}><p>{state.error}</p></Details>
          <button type="button" className="console-btn" onClick={load}>{t('Retry')}</button>
          <a className="console-btn console-btn-primary" href={`/studio?api=${encodeURIComponent(apiBase)}`}>{t('Go to Studio')}</a>
        </div>
      )}
      {state.status === 'ready' && state.templates.length === 0 && (
        <div className="console-empty">
          {t('No scenario templates are published on this service yet. Open the Studio to create your first scenario.')}
        </div>
      )}
      {state.status === 'ready' && state.templates.length > 0 && (
        <>
          <div className="template-filter-row" role="group" aria-label={t('Filter scenarios by category')}>
            <button className="console-btn" data-testid="gallery-experiment" onClick={()=>setExperiment(true)}>{t('Compare changes · Experiment')}</button>
            <button type="button" className="template-filter" aria-pressed={selectedCategory === 'all'} onClick={() => setSelectedCategory('all')} data-testid="filter-all">
              {t('All ({count})', { count: state.templates.length })}
            </button>
            {TEMPLATE_CATEGORIES.filter(c => presentCategories.has(c)).map(c => {
              const meta = categoryMetadata(c, locale);
              const count = state.templates.filter(t => t.category === c).length;
              return (
                <button key={c} type="button" className="template-filter" aria-pressed={selectedCategory === c}
                  onClick={() => setSelectedCategory(c)} data-testid={`filter-${c}`}>
                  <CategoryIcon category={c} size={14} /> {t('{category} ({count})', { category: meta.label, count })}
                </button>
              );
            })}
          </div>
          <ul className="template-gallery">
            {visible.map(template => (
              <TemplateCard key={template.id} template={template} api={api} apiBase={apiBase} onCustomize={setCustomizing} />
            ))}
          </ul>
          {visible.length === 0 && <div className="console-empty">{t('No templates in this category yet.')}</div>}
        </>
      )}
      <Drawer open={experiment} onClose={()=>setExperiment(false)} title={t('Compare changes')} width={920} destroyOnClose closable={false} extra={<button type="button" className="console-btn" aria-label={t('Close')} onClick={()=>setExperiment(false)}>×</button>}>{experiment&&<ExperimentPanel base={apiBase} templates={state.templates}/>}</Drawer>
      <Drawer open={!!customizing} onClose={()=>setCustomizing(undefined)} title={customizing?t('Customize · {title}', { title: localized(customizing, 'title', locale) }):t('Customize')} width={520} destroyOnClose closable={false} extra={<button type="button" className="console-btn" aria-label={t('Close')} onClick={()=>setCustomizing(undefined)}>×</button>}>
        {customizing&&<CustomizeTemplate key={customizing.id} template={customizing} api={api} apiBase={apiBase}/>}
      </Drawer>
    </div>
  );
}
