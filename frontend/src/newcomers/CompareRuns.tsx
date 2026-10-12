import { useCallback, useEffect, useMemo, useState } from 'react';
import { PageState } from '../console/PageState';
import { TemplateApi, type AlgorithmDescriptor, type AlgorithmSlot, type ComparisonRun, type KnobValue, type ScenarioTemplate, type TemplateKnob } from './api';
import { metricHelp, metricLabel } from './metric-help';
import { useI18n } from '../i18n/I18nProvider';
import { localized, translate } from '../i18n/translate';
import { knobOptionLabel, runStatusLabel } from './presentation';
import { displayTime } from '../pages/display-time';
import './results.css';

export interface CompareRunsProps {
  api: TemplateApi;
  /** Two to four run ids; fewer/more shows an honest message (the API refuses too). */
  ids: string[];
  onBack?: () => void;
}

interface ConfigurationDifference { path: string; values: Record<string, unknown>; present: Record<string, boolean> }
interface RecordedEnd { at_ns: string; commit_index: number; status: string }
interface ComparisonConfiguration { alignment: 'recorded_run_end'; recorded_ends: Record<string, RecordedEnd>; config_diff: ConfigurationDifference[] }
export function comparisonConfiguration(value: unknown, ids?: string[]): ComparisonConfiguration {
  const data = value as Partial<ComparisonConfiguration> | null;
  if (!data || data.alignment !== 'recorded_run_end' || !data.recorded_ends || !Object.values(data.recorded_ends).every(end =>
    end && typeof end.at_ns === 'string' && /^\d+$/.test(end.at_ns) && Number.isSafeInteger(end.commit_index) && end.commit_index >= 1 && typeof end.status === 'string') ||
    (ids && !ids.every(id => Object.prototype.hasOwnProperty.call(data.recorded_ends, id))) ||
    !Array.isArray(data.config_diff) || !data.config_diff.every(row =>
    row && typeof row.path === 'string' && row.values && typeof row.values === 'object' && !Array.isArray(row.values) &&
    row.present && typeof row.present === 'object' && !Array.isArray(row.present) &&
    Object.entries(row.present).every(([id, present]) => typeof present === 'boolean' && (present ? Object.prototype.hasOwnProperty.call(row.values, id) : !Object.prototype.hasOwnProperty.call(row.values, id))) &&
    (!ids || ids.every(id => Object.prototype.hasOwnProperty.call(row.present, id))))) {
    throw Error('Pinned configuration comparison is unavailable or malformed.');
  }
  return data as ComparisonConfiguration;
}

/** Plain labels for known metric directions; anything unknown is treated as neutral. */
const DIRECTIONS: Record<string, 'min' | 'max' | 'neutral'> = {
  tasks_completed: 'max', tasks_created: 'neutral',
  mean_task_latency_s: 'min', p95_task_latency_s: 'min',
  energy_used_kj: 'min', distance_flown_km: 'neutral',
  separation_conflicts: 'min', geofence_violations: 'min', weather_holds: 'min',
};

/** Machine ids read badly for newcomers — split them into words. */
function humanize(id: string): string {
  return id.replace(/[_-]+/g, ' ').replace(/\b\w/g, character => character.toUpperCase());
}

function display(value: KnobValue | undefined): string {
  return value === undefined ? '—' : String(value);
}

function formatKpi(value: number | null, unit: string): string {
  if (value === null || !Number.isFinite(value)) return '—';
  if (unit === 'count') unit = '';
  const abs = Math.abs(value);
  const digits = Number.isInteger(value) ? 0 : abs >= 1000 ? 0 : abs >= 10 ? 1 : 2;
  return `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}`;
}

interface KpiRow {
  id: string;
  label: string;
  label_zh?: string;
  unit: string;
  direction: 'min' | 'max' | 'neutral';
  values: (number | null)[];
  best?: number | null;
}

/** Rows sorted best-first; best values are highlighted per direction, never by raw max alone. */
function kpiRows(runs: ComparisonRun[], order: string[] = []): KpiRow[] {
  const ids: string[] = [];
  order.forEach(id => { if (!ids.includes(id)) ids.push(id); });
  runs.forEach(run => Object.keys(run.kpis ?? {}).forEach(id => { if (!ids.includes(id)) ids.push(id); }));
  const rows = ids.map(id => {
    const values = runs.map(run => run.kpis[id]?.value ?? null);
    const known = values.filter((value): value is number => value !== null && Number.isFinite(value));
    const direction = runs.map(run => run.kpis[id]?.direction).find(direction => direction !== undefined) ?? DIRECTIONS[id] ?? 'neutral';
    const best = known.length && direction !== 'neutral'
      ? (direction === 'min' ? Math.min(...known) : Math.max(...known))
      : undefined;
    return { id, label: runs.flatMap(run => (run.kpis[id] ? [run.kpis[id]] : []))[0]?.label ?? humanize(id),
      label_zh: runs.flatMap(run => (run.kpis[id] ? [run.kpis[id]] : []))[0]?.label_zh,
      unit: runs.flatMap(run => (run.kpis[id] ? [run.kpis[id]] : []))[0]?.unit ?? '', direction, values, best };
  });
  const rank = (id: string) => {
    const index = order.indexOf(id);
    return index === -1 ? order.length + 1 : index;
  };
  return rows.sort((a, b) => rank(a.id) - rank(b.id) || a.direction.localeCompare(b.direction) || a.label.localeCompare(b.label));
}

/** Knob / algorithm columns where the compared runs actually differ. */
function differences(runs: ComparisonRun[], templates: Map<string, ScenarioTemplate>, locale: string, algorithms: AlgorithmDescriptor[]): { label: string; values: string[] }[] {
  const t = (key: string) => translate(locale, key);
  const keys: string[] = [];
  runs.forEach(run => Object.keys(run.knobs ?? {}).forEach(key => { if (!keys.includes(key)) keys.push(key); }));
  const relevant = [...templates.values()].filter(template => runs.some(run => run.template_id === template.id));
  const knobs = new Map<string, TemplateKnob>();
  const pointerSlot = new Map<string, string>();
  for (const template of relevant) for (const knob of template.knobs) {
    if (!knobs.has(knob.id)) knobs.set(knob.id, knob);
    const slot = knob.pointer?.match(/^\/algorithms\/([^/]+)$/)?.[1];
    if (slot) pointerSlot.set(knob.id, slot);
  }
  const algorithmSlots = ['allocation', 'path_planning', 'conflict_resolution', 'charging'] as const;
  const slotLabels: Record<AlgorithmSlot, string> = {
    allocation: t('Allocation'), path_planning: t('Path Planning'),
    conflict_resolution: t('Conflict Resolution'), charging: t('Charging strategy'),
  };
  const usedSlots = new Set<string>();
  interface Difference { label: string; raw: (KnobValue | undefined)[]; knob?: TemplateKnob; slot?: AlgorithmSlot }
  const knobColumns: Difference[] = keys.map(key => {
    const knob = knobs.get(key), slot = pointerSlot.get(key);
    const label = knob ? localized(knob, 'label', locale) : humanize(key);
    if (slot && algorithmSlots.some(candidate => candidate === slot)) {
      const typedSlot = slot as AlgorithmSlot;
      const agrees = runs.every(run => run.knobs[key] === undefined || run.algorithms[typedSlot] === undefined || run.knobs[key] === run.algorithms[typedSlot]);
      if (agrees) { usedSlots.add(slot); return {label, knob, slot: typedSlot, raw: runs.map(run => run.knobs[key] ?? run.algorithms[typedSlot])}; }
    }
    return {label, knob, raw: runs.map(run => run.knobs?.[key])};
  });
  const algorithmColumns: Difference[] = algorithmSlots.filter(slot => runs.some(run => run.algorithms?.[slot]) && !usedSlots.has(slot))
    .map(slot => ({label: slotLabels[slot], slot, raw: runs.map(run => run.algorithms?.[slot])}));
  // Compare retained typed values, not translations (different ids may share a label).
  return [...knobColumns, ...algorithmColumns].filter(column => new Set(column.raw).size > 1).map(column => ({
    label: column.label,
    values: column.raw.map(value => {
      if (value === undefined) return '—';
      if (locale !== 'zh-CN') return display(value);
      if (column.knob) return knobOptionLabel(column.knob, value, locale, algorithms.filter(row => row.slot === column.slot));
      const algorithm = algorithms.find(row => row.slot === column.slot && row.id === value);
      return algorithm ? localized(algorithm, 'label', locale) : typeof value === 'boolean' ? value ? t('yes') : t('no') : display(value);
    }),
  }));
}

function Column({ run, rows, index, more, template }: { run: ComparisonRun; rows: KpiRow[]; index: number; more: KpiRow[]; template?: ScenarioTemplate }) {
  const { t, locale } = useI18n();
  const label = (row: KpiRow) => metricLabel(row.id, row.label, locale, row.label_zh);
  return (
    <section className="newc-compare-col">
      <h3>{t('Run {number} · {title}', { number: index + 1, title: localized(run, 'title', locale) || humanize(run.id) })}</h3>
      {run.template_id && <p className="newc-compare-template">{locale === 'zh-CN' && template ? localized(template, 'title', locale) : humanize(run.template_id)}</p>}
      <table className="newc-compare-table">
        <caption>{t('Results · preferred value highlighted')}</caption>
        <tbody>
          {rows.map(row => {
            const value = row.values.length ? row.values[index] : null;
            const isBest = row.best !== undefined && value !== null && value === row.best;
            return (
              <tr key={row.id} className={isBest ? 'is-best' : undefined}>
                <th scope="row">
                  {label(row)}
                  {metricHelp(row.id, locale) !== undefined && (
                    <span className="newc-compare-help" title={metricHelp(row.id, locale)} aria-label={t('About {label}', { label: label(row) })} tabIndex={0}>?</span>
                  )}
                </th>
                <td className={`newc-kpi-cell${isBest ? ' is-best' : ''}`}>
                  {formatKpi(value, row.unit)}
                  <Bar value={value} row={row} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {more.length > 0 && (
        <details className="newc-kpi-more">
          <summary>{t('More metrics ({count})', { count: more.length })}</summary>
          <table className="newc-compare-table">
            <tbody>
              {more.map(row => {
                const value = row.values.length ? row.values[index] : null;
                const isBest = row.best !== undefined && value !== null && value === row.best;
                return (
                  <tr key={row.id} className={isBest ? 'is-best' : undefined}>
                    <th scope="row">{label(row)}{metricHelp(row.id, locale)&&<span className="newc-compare-help" title={metricHelp(row.id, locale)} aria-label={t('About {label}', { label: label(row) })} tabIndex={0}>?</span>}</th>
                    <td className={`newc-kpi-cell${isBest ? ' is-best' : ''}`}>{formatKpi(value, row.unit)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}

/** Small SVG bar scaled within the row; omitted for neutral or unknown metrics. */
function Bar({ value, row }: { value: number | null; row: KpiRow }) {
  const known = row.values.filter((entry): entry is number => entry !== null && Number.isFinite(entry));
  if (value === null || !Number.isFinite(value) || row.direction === 'neutral' || known.length < 2) return null;
  if(value<0||known.some(item=>item<0))return null;
  const max=Math.max(...known),width=max===0?0:value/max*100;
  return (
    <svg className="newc-bar" viewBox="0 0 100 5" preserveAspectRatio="none" aria-hidden="true">
      <rect x="0" y="0" width={width} height="5" rx="2" fill="currentColor" />
    </svg>
  );
}

/**
 * Side-by-side comparison fetched live from the API (no silent mocks).
 * Each run gets a column; the KPI table highlights the best value per metric
 * respecting its known direction, and differing knobs/algorithm choices get
 * their own rows. Template catalog metadata (knob labels, declared KPI order)
 * is fetched separately and best-effort: if the catalog is unavailable the
 * comparison still renders with machine ids and generic ordering.
 */
export function CompareRuns({ api, ids, onBack }: CompareRunsProps) {
  const { t, locale } = useI18n();
  const [algorithms, setAlgorithms] = useState<AlgorithmDescriptor[]>([]);
  const [runs, setRuns] = useState<ComparisonRun[]>();
  const [templates, setTemplates] = useState<Map<string, ScenarioTemplate>>(() => new Map());
  const [catalogNote, setCatalogNote] = useState<string>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [configuration, setConfiguration] = useState<ComparisonConfiguration>();
  const [configurationError, setConfigurationError] = useState<string>();

  useEffect(() => {
    setConfiguration(undefined); setConfigurationError(undefined);
    if (ids.length < 2 || ids.length > 4) return;
    const control = new AbortController();
    // The catalog decoder retains KPI/settings columns. Fetch pinned configuration
    // metadata without recomputing the KPIs for a second time.
    void Promise.resolve().then(() => api.request<unknown>(`/v1/runs/compare?ids=${ids.map(encodeURIComponent).join(',')}&include_kpis=false`, undefined, control.signal))
      .then(value => comparisonConfiguration(value, ids)).then(value => { if (!control.signal.aborted) setConfiguration(value); })
      .catch(problem => { if (!control.signal.aborted) setConfigurationError(String(problem)); });
    return () => control.abort();
  }, [api, ids.join(','), attempt]);

  useEffect(() => {
    if (ids.length < 2 || ids.length > 4) {
      setRuns(undefined); setError('Pick two to four runs to compare.'); setLoading(false);
      return;
    }
    const control = new AbortController();
    setLoading(true); setError(undefined);
    api.compare(ids, control.signal)
      .then(comparison => { if (!control.signal.aborted) { setRuns(comparison.runs); setLoading(false); } })
      .catch(problem => { if (!control.signal.aborted) { setError(String(problem)); setLoading(false); } });
    return () => control.abort();
  }, [api, ids.join(','), attempt]);

  // Best-effort catalog metadata: knob labels / pointers and template-declared
  // KPI ordering. Failures are surfaced as a small note, never as a dead end —
  // the comparison itself stays available.
  useEffect(() => {
    if (ids.length < 2 || ids.length > 4) return;
    const control = new AbortController();
    api.templates(control.signal)
      .then(catalog => {
        if (control.signal.aborted) return;
        const map = new Map<string, ScenarioTemplate>();
        for (const template of catalog) map.set(template.id, template);
        setTemplates(map); setCatalogNote(undefined);
      })
      .catch(problem => {
        if (control.signal.aborted) return;
        setTemplates(new Map());
        setCatalogNote(String(problem));
      });
    return () => control.abort();
  }, [api, ids.join(','), attempt]);

  useEffect(() => {
    setAlgorithms([]);
    if (locale !== 'zh-CN' || ids.length < 2 || ids.length > 4) return;
    const control = new AbortController();
    void api.algorithms(control.signal).then(rows => { if (!control.signal.aborted) setAlgorithms(rows); }).catch(() => { /* Catalog ids are still preserved when metadata is unavailable. */ });
    return () => control.abort();
  }, [api, locale, ids.join(','), attempt]);

  const retry = useCallback(() => setAttempt(attempt => attempt + 1), []);
  const declaredOrder = useMemo(() => {
    const order: string[] = [];
    runs?.forEach(run => {
      const template = run.template_id ? templates.get(run.template_id) : undefined;
      template?.kpis.forEach(id => { if (!order.includes(id)) order.push(id); });
    });
    return order;
  }, [runs, templates]);
  const rows = useMemo(() => (runs ? kpiRows(runs, declaredOrder) : []), [runs, declaredOrder]);
  const primary = useMemo(() => rows.filter(row => declaredOrder.length?declaredOrder.includes(row.id):row.values.some(value=>value!==null&&value!==0)), [rows, declaredOrder]);
  const more = useMemo(() => rows.filter(row=>!primary.includes(row)), [rows, primary]);
  const columns = useMemo(() => (runs ? differences(runs, templates, locale, algorithms) : []), [runs, templates, locale, algorithms]);

  if (loading && !runs) {
    return <PageState kind="loading" title={t('Loading the comparison')} description={t('Fetching the results of the runs you selected.')} />;
  }
  if (error) {
    return (
      <PageState kind="error" title={t('Could not compare the runs')} description={ids.length < 2 || ids.length > 4 ? t('Pick two to four runs to compare.') : error}
        action={(
          <span className="newc-compare-actions">
            {onBack && <button type="button" className="newc-button" onClick={onBack}>{t('Go back')}</button>}
            {ids.length >= 2 && ids.length <= 4 && <button type="button" className="newc-button" onClick={retry}>{t('Try again')}</button>}
          </span>
        )} />
    );
  }
  if (!runs) return null;
  return (
    <div className="newc-compare">
      <header className="newc-compare-header">
        <h2 className="newc-compare-title">{t('Comparing {count} runs', { count: runs.length })}</h2>
        {onBack && <button type="button" className="newc-button" onClick={onBack}>{t('Go back')}</button>}
      </header>
      {catalogNote && <p className="newc-catalog-note" role="note">{t('Metric names and settings come from machine ids — template details are unavailable ({error}).', { error: catalogNote })}</p>}
      <section className="newc-compare-section" data-testid="comparison-configuration">
        <h4>{locale === 'zh-CN' ? '固定配置差异' : 'Pinned configuration differences'}</h4>
        {configurationError ? <p role="status">{locale === 'zh-CN' ? '无法读取固定配置差异：' : 'Could not read pinned configuration differences: '}{configurationError}</p>
          : !configuration ? <p role="status">{locale === 'zh-CN' ? '正在读取运行的固定配置…' : 'Loading the runs’ pinned configurations…'}</p>
          : <>
            <p>{locale === 'zh-CN' ? '各列使用该次运行最后记录的结果，仿真时长可能不同；回放读取已有日志。' : 'Each column uses its run’s last recorded results; simulation durations may differ. Playback reads existing journals.'}</p>
            {runs.map((run, index) => { const end=configuration.recorded_ends[run.id];return <p key={run.id} style={{margin:'4px 0',fontSize:12}}>{t('Run {number} · {title}', {number:index+1,title:localized(run,'title',locale)||run.id})} · {displayTime(end.at_ns,'0')} · {runStatusLabel(end.status,locale)} · #{end.commit_index}</p>; })}
            {!configuration.config_diff.length ? <p>{locale === 'zh-CN' ? '两次运行的固定配置相同。注入事件与执行结果保留在各自日志中。' : 'The pinned configurations are identical. Injected events and results remain in each run’s journal.'}</p>
              : <div style={{ maxHeight: 280, overflow: 'auto' }}><table className="newc-compare-table" style={{ tableLayout: 'fixed', overflowWrap: 'anywhere' }}>
                <thead><tr><th style={{ width: '34%' }}>{locale === 'zh-CN' ? '配置字段' : 'Configuration field'}</th>{runs.map((run, index) => <th key={run.id} title={run.id}>{t('Run {number} · {title}', { number: index + 1, title: localized(run, 'title', locale) || run.id })}</th>)}</tr></thead>
                <tbody>{[...configuration.config_diff].sort((a, b) => Number(a.path.endsWith('/source')) - Number(b.path.endsWith('/source')) || a.path.localeCompare(b.path)).map(row => <tr key={row.path}><th scope="row"><code>{row.path}</code></th>{runs.map(run => {
                  const value = row.values[run.id];
                  return <td key={run.id}>{!row.present[run.id] ? locale === 'zh-CN' ? '未设置' : 'Not set'
                    : row.path.endsWith('/source') && typeof value === 'string' && value.length > 96 && value.includes('/')
                      ? <details className="newc-config-source"><summary title={value}>{locale === 'zh-CN' ? '源位置' : 'Source location'} · {value.slice(value.lastIndexOf('/') + 1)}</summary><code>{JSON.stringify(value)}</code></details>
                      : JSON.stringify(value)}</td>;
                })}</tr>)}</tbody>
              </table></div>}
          </>}
      </section>
      {columns.length > 0 && (
        <section className="newc-compare-section">
          <h4>{t('Settings that differ')}</h4>
          <ul className="newc-diff-list">
            {columns.map(column => (
              <li key={column.label}>
                <span>{column.label}</span>
                {column.values.map((value, index) => <span key={index} className="newc-diff-value">{value}</span>)}
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className={`newc-compare-grid n-${runs.length}`}>
        {runs.map((run, index) => (
          <Column key={run.id} run={run} rows={primary} more={more} index={index} template={run.template_id ? templates.get(run.template_id) : undefined} />
        ))}
      </div>
    </div>
  );
}
