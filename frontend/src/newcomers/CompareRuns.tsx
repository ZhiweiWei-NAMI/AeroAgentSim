import { useCallback, useEffect, useMemo, useState } from 'react';
import { PageState } from '../console/PageState';
import { TemplateApi, type AlgorithmDescriptor, type AlgorithmSlot, type ComparisonRun, type KnobValue, type ScenarioTemplate, type TemplateKnob } from './api';
import { comparisonRefreshPolicy, COMPARISON_REFRESH_MS, finalCursors, readComparison, resolveMetricDirection, type ComparisonRead, type ComparisonRunRead, type ComparisonConfiguration, type RunResultState } from './comparison-read';
import { hasMetricDescriptor, LEGACY_METRIC_DIRECTIONS, metricHelp, metricLabel, metricUnit, metricValueText, type MetricMetadata } from './metric-help';
import { useI18n } from '../i18n/I18nProvider';
import { localized, translate } from '../i18n/translate';
import { knobOptionLabel, runStatusLabel } from './presentation';
import { displayTime } from '../pages/display-time';
import { stringifyLossless } from '../feeds/lossless-json';
import './results.css';
import './compare-runs.css';

export interface CompareRunsProps {
  api: TemplateApi;
  /** Two to four run ids; fewer/more shows an honest message (the API refuses too). */
  ids: string[];
  onBack?: () => void;
}

// Kept as a named export for callers/tests that used the original helper.
export { comparisonConfiguration } from './comparison-read';

function resultStateLabel(state: RunResultState, locale: string): string {
  const zh = locale === 'zh-CN';
  return state === 'final' ? (zh ? '已最终' : 'Final')
    : state === 'provisional' ? (zh ? '暂定' : 'Provisional')
    : (zh ? '最终性未知' : 'Finality unknown');
}

function resultStateTitle(state: RunResultState, locale: string): string {
  const zh = locale === 'zh-CN';
  return state === 'final'
    ? (zh ? '结果已最终确定，并与此次运行的记录终点一致。' : 'These results are final and match this run’s recorded end.')
    : state === 'provisional'
      ? (zh ? '结果仍在推进或汇总中，当前数值可能变化。' : 'Results are still advancing or aggregating; these values may change.')
      : (zh ? '服务返回的最终性或游标证据不一致，不能确认这些结果已最终。' : 'The service’s finality or cursor evidence is inconsistent; these results are not confirmed final.');
}

/** Explicit finality badge; a cursor is shown only when the service supplied one. */
function StateBadge({ entry, locale, showCursor = true, testId }: { entry: ComparisonRunRead; locale: string; showCursor?: boolean; testId?: string }) {
  const zh = locale === 'zh-CN';
  return <span className={`newc-compare-state is-${entry.state}`} data-testid={testId} title={resultStateTitle(entry.state, locale)}>
    {resultStateLabel(entry.state, locale)}
    {showCursor && entry.cursor !== undefined && <span className="newc-compare-state-cursor"> · {zh ? `游标 ${entry.cursor}` : `cursor ${entry.cursor}`}</span>}
  </span>;
}

/**
 * Lead with run identity, finality and recorded end. Settings, KPI values and
 * changes appear only in the detailed sections below.
 */
function SummaryTable({ runs, entries, configuration, configurationError, templates, locale }: {
  runs: ComparisonRun[]; entries: Map<string, ComparisonRunRead>; configuration?: ComparisonConfiguration; configurationError?: string;
  templates: Map<string, ScenarioTemplate>; locale: string;
}) {
  const { t } = useI18n();
  const zh = locale === 'zh-CN';
  const endOf = (id: string) => configuration?.recorded_ends[id];
  return <section className="newc-compare-summary" data-testid="comparison-summary" aria-label={zh ? '对比总览' : 'Comparison summary'}>
    <div style={{ maxHeight: 420, overflow: 'auto' }}>
      <table className="newc-compare-table newc-summary-table">
        <caption>{zh ? '先看这里：所选运行的场景模板、结果状态与记录终点' : 'Start here: the selected runs’ scenario templates, result state and recorded ends'}</caption>
        <thead><tr>
          <th scope="col" style={{ width: '32%' }}>{zh ? '项目' : 'Item'}</th>
          {runs.map((run, index) => <th key={run.id} scope="col" title={run.id}>{t('Run {number} · {title}', { number: index + 1, title: localized(run, 'title', locale) || humanize(run.id) })}</th>)}
        </tr></thead>
        <tbody>
          <tr>
            <th scope="row">{zh ? '场景模板' : 'Scenario template'}</th>
            {runs.map(run => {
              const template = run.template_id ? templates.get(run.template_id) : undefined;
              return <td key={run.id}>{run.template_id ? (zh && template ? localized(template, 'title', locale) : humanize(run.template_id)) : <span className="newc-config-tag">{zh ? '未知' : 'Unknown'}</span>}</td>;
            })}
          </tr>
          <tr>
            <th scope="row">{zh ? '记录终点 · 状态' : 'Recorded end · status'}</th>
            {runs.map(run => {
              const end = endOf(run.id);
              return <td key={run.id}>{end ? <>{displayTime(end.at_ns, '0')} · {runStatusLabel(end.status, locale)} · #{end.commit_index}</>
                : configurationError ? <span className="newc-config-absent" title={configurationError}>{zh ? '不可用' : 'Unavailable'}</span>
                : <span className="newc-config-absent">{zh ? '读取中…' : 'Loading…'}</span>}</td>;
            })}
          </tr>
          <tr>
            <th scope="row">{zh ? '结果状态' : 'Result state'}</th>
            {runs.map(run => {
              const entry = entries.get(run.id);
              return <td key={run.id}>{entry ? <StateBadge entry={entry} locale={locale} testId={`comparison-state-${run.id}`} /> : <span className="newc-config-absent">{zh ? '读取中…' : 'Loading…'}</span>}</td>;
            })}
          </tr>
        </tbody>
      </table>
    </div>
  </section>;
}

/** Names exactly which runs/versions (and templates) are being compared, and warns when metric definitions may not line up. */
function MixedNote({ runs }: { runs: ComparisonRun[] }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const names = runs.map((run, index) => `${zh ? `运行 ${index + 1}` : `Run ${index + 1}`} · ${localized(run, 'title', locale) || run.id}${run.template_id ? ` (${run.template_id})` : ''}`);
  const mixed = new Set(runs.map(run => run.template_id ?? '')).size > 1;
  return <div className="newc-compare-mixed" data-testid="comparison-mixed">
    <p>{zh ? `正在对比：${names.join('、')}` : `Comparing: ${names.join(', ')}`}</p>
    {mixed && <p className="newc-compare-mixed-warn" role="note">{zh
      ? '所选运行来自不同场景模板：各模板的指标定义可能不完全可比，缺失值显示为 “—”，绝不按最优值处理。原始数值原样保留。'
      : 'These runs come from different scenario templates: metric definitions may not be directly comparable. Missing values show “—” and are never marked best. Exact raw values are preserved.'}</p>}
  </div>;
}

/** States the refresh policy plainly: live refresh, frozen cursor, or no claim. */
function RefreshNote({ read, locale }: { read: ComparisonRead; locale: string }) {
  const zh = locale === 'zh-CN';
  const policy = comparisonRefreshPolicy(read);
  if (policy.active) {
    const seconds = Math.max(1, Math.round((policy.intervalMs ?? COMPARISON_REFRESH_MS) / 1000));
    return <p className="newc-compare-refresh is-live" data-testid="comparison-refresh" role="status" aria-live="polite">
      {zh ? `有运行尚未最终确定，正在每 ${seconds} 秒刷新。` : `Some runs are provisional; refreshing every ${seconds} seconds.`}
    </p>;
  }
  if (policy.reason === 'final') {
    const cursors = finalCursors(read);
    const joined = cursors.join(zh ? '、' : ', ');
    return <p className="newc-compare-refresh is-frozen" data-testid="comparison-refresh" role="status">
      {zh ? `全部结果已最终确定；已冻结于游标 ${joined}。`
        : `All results are final; frozen as of ${cursors.length === 1 ? 'cursor' : 'cursors'} ${joined}.`}
    </p>;
  }
  return <p className="newc-compare-refresh is-unknown" data-testid="comparison-refresh" role="status">
    {zh ? '无法确认全部结果是否已最终，未自动刷新。' : 'Finality could not be confirmed for every run; not refreshing automatically.'}
  </p>;
}

/** Machine ids read badly for newcomers — split them into words. */
function humanize(id: string): string {
  return id.replace(/[_-]+/g, ' ').replace(/\b\w/g, character => character.toUpperCase());
}

/** Exact lossless text of a recorded config value (integers above 2^53 included). */
function exactText(value: unknown): string {
  if (typeof value === 'bigint') return value.toString();
  if (typeof value === 'object' && value !== null && '$integer' in (value as Record<string, unknown>)) return String((value as Record<string, unknown>).$integer);
  try { return stringifyLossless(value); } catch { return JSON.stringify(value); }
}
function display(value: KnobValue | undefined): string {
  return value === undefined ? '—' : String(value);
}

/** True when a recorded value is a large object/array that should collapse behind a disclosure. */
function isBulk(value: unknown): value is Array<unknown> | Record<string, unknown> {
  if (Array.isArray(value)) return value.length > 4;
  if (!value || typeof value !== 'object') return false;
  const keys = Object.keys(value);
  if (keys.length === 1 && (keys[0] === '$integer' || keys[0] === '$number')) return false;
  return keys.length > 4;
}
/** Short human preview of a bulk value: kind and size, no content dump. */
function bulkSummary(value: Array<unknown> | Record<string, unknown>, locale: string): string {
  if (Array.isArray(value)) return `${translate(locale, 'list')} · ${value.length}`;
  const first = Object.keys(value).slice(0, 3).join(', ');
  return `${translate(locale, 'object')} · ${Object.keys(value).length}${first ? ` — ${first}` : ''}`;
}

function formatKpi(value: number | null, unit: string): string {
  return metricValueText('', value, unit);
}

interface KpiRow {
  id: string;
  label: string;
  metadata?: MetricMetadata;
  unit: string;
  direction: 'min' | 'max' | 'neutral';
  /** True only when every run is final and the metric direction is defined/consistent. */
  rankable: boolean;
  values: (number | null)[];
  best?: number | null;
}

/** Change from the previous run, judged only when ranking is actually allowed. */
function kpiDelta(row: KpiRow, index: number) {
  const before = row.values[index - 1], after = row.values[index];
  if (!row.rankable || index === 0 || row.direction === 'neutral' || before === null || after === null || !Number.isFinite(before) || !Number.isFinite(after)) return undefined;
  const span = after - before;
  if (!Number.isFinite(span) || span === 0) return undefined;
  const better = row.direction === 'min' ? span < 0 : span > 0;
  return { text: `${span > 0 ? '+' : ''}${formatKpi(span, row.unit)}${better ? ' ✓' : ' ✕'}`,
    className: better ? ' is-ok' : ' is-warn' };
}

/** Rows sorted best-first; best values need finality plus one agreed direction. */
function kpiRows(runs: ComparisonRun[], order: string[] = [], final: boolean): KpiRow[] {
  const ids: string[] = [];
  order.forEach(id => { if (!ids.includes(id)) ids.push(id); });
  runs.forEach(run => Object.keys(run.kpis ?? {}).forEach(id => { if (!ids.includes(id)) ids.push(id); }));
  const rows = ids.map(id => {
    const values = runs.map(run => run.kpis[id]?.value ?? null);
    const known = values.filter((value): value is number => value !== null && Number.isFinite(value));
    const declared = resolveMetricDirection(runs, id, LEGACY_METRIC_DIRECTIONS);
    const direction = declared.direction;
    const rankable = final && declared.defined && declared.consistent && direction !== 'neutral';
    const best = rankable && known.length
      ? (direction === 'min' ? Math.min(...known) : Math.max(...known))
      : undefined;
    const candidates = runs.flatMap(run => run.kpis[id] ? [run.kpis[id]] : []);
    const metadata = candidates.find(hasMetricDescriptor) ?? candidates[0];
    return { id, label: metadata?.label ?? humanize(id), metadata,
      unit: metricUnit(id, metadata) ?? '', direction, rankable, values, best };
  });
  const rank = (id: string) => {
    const index = order.indexOf(id);
    return index === -1 ? order.length + 1 : index;
  };
  return rows.sort((a, b) => rank(a.id) - rank(b.id) || a.direction.localeCompare(b.direction) || a.label.localeCompare(b.label));
}

/** Knob / algorithm columns where the compared runs actually differ. */
function differences(runs: ComparisonRun[], templates: Map<string, ScenarioTemplate>, locale: string, algorithms: AlgorithmDescriptor[]): { label: string; values: string[] }[] {
  // Run summaries resolve settings from the pinned configuration. Templates
  // supply labels/options only; their defaults are never experiment values.
  const t = (key: string) => translate(locale, key);
  if (!runs.length) return [];
  const keys: string[] = [];
  runs.forEach(run => Object.keys(run.knobs ?? {}).forEach(key => { if (!keys.includes(key)) keys.push(key); }));
  const relevant = [...templates.values()].filter(template => runs.some(run => run.template_id === template.id));
  if (relevant.length && !runs.some(run => run.template_id && templates.has(run.template_id))) return [];
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

function Column({ run, entry, final, rows, index, more, template }: {
  run: ComparisonRun; entry?: ComparisonRunRead; final: boolean;
  rows: KpiRow[]; index: number; more: KpiRow[]; template?: ScenarioTemplate;
}) {
  const { t, locale } = useI18n();
  // Each column keeps its own descriptor copy; a missing row can borrow only metadata.
  const metadata = (row: KpiRow) => run.kpis[row.id] ?? row.metadata;
  const label = (row: KpiRow) => metricLabel(row.id, metadata(row) ?? row.label, locale);
  return (
    <section className="newc-compare-col">
      <h3>{t('Run {number} · {title}', { number: index + 1, title: localized(run, 'title', locale) || humanize(run.id) })}</h3>
      {entry && <StateBadge entry={entry} locale={locale} showCursor={false} />}
      {run.template_id && <p className="newc-compare-template">{locale === 'zh-CN' && template ? localized(template, 'title', locale) : humanize(run.template_id)}</p>}
      <table className="newc-compare-table">
        <caption>{!final
          ? locale === 'zh-CN' ? '结果 · 全部运行最终确定前不评为最优' : 'Results · not ranked until every run is final'
          : [...rows, ...more].some(row => row.best !== undefined)
            ? t('Results · preferred value highlighted')
            : locale === 'zh-CN' ? '结果 · 无可按一致方向排名的测量值' : 'Results · no measurements with an agreed ranking direction'}</caption>
        <tbody>
          {rows.map(row => {
            const value = row.values.length ? row.values[index] : null;
            const isBest = row.rankable && row.best !== undefined && value !== null && value === row.best;
            const change = kpiDelta(row, index);
            const metric = metadata(row), help = metricHelp(row.id, locale, metric);
            return (
              <tr key={row.id} className={isBest ? 'is-best' : undefined}>
                <th scope="row">
                  {label(row)}
                  {help !== undefined && (
                    <span className="newc-compare-help" title={help} aria-label={t('About {label}', { label: label(row) })} tabIndex={0}>?</span>
                  )}
                </th>
                <td className={`newc-kpi-cell${isBest ? ' is-best' : ''}`}>
                  {metricValueText(row.id, value, metricUnit(row.id, metric) ?? '', locale, metric)}
                  {change && <span className={`newc-kpi-delta${change.className}`} title={locale === 'zh-CN' ? '相对左列的变化' : 'Change from the previous column'}>{change.text}</span>}
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
                const isBest = row.rankable && row.best !== undefined && value !== null && value === row.best;
                const metric = metadata(row), help = metricHelp(row.id, locale, metric);
                return (
                  <tr key={row.id} className={isBest ? 'is-best' : undefined}>
                    <th scope="row">{label(row)}{help !== undefined && <span className="newc-compare-help" title={help} aria-label={t('About {label}', { label: label(row) })} tabIndex={0}>?</span>}</th>
                    <td className={`newc-kpi-cell${isBest ? ' is-best' : ''}`}>{metricValueText(row.id, value, metricUnit(row.id, metric) ?? '', locale, metric)}</td>
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
 * One response supplies results, recorded ends and pinned configuration;
 * provisional runs are re-read on an interval. Best values are highlighted
 * only when every run is final and metrics agree on a direction, while
 * differing knobs/algorithm choices get their own rows. Template catalog
 * metadata (knob labels, declared KPI order) is fetched separately and
 * best-effort: if the catalog is unavailable the comparison still renders
 * with machine ids and generic ordering.
 */
export function CompareRuns({ api, ids, onBack }: CompareRunsProps) {
  const { t, locale } = useI18n();
  const [algorithms, setAlgorithms] = useState<AlgorithmDescriptor[]>([]);
  const [read, setRead] = useState<ComparisonRead>();
  const [templates, setTemplates] = useState<Map<string, ScenarioTemplate>>(() => new Map());
  const [catalogNote, setCatalogNote] = useState<string>();
  const [error, setError] = useState<string>();
  const [refreshNote, setRefreshNote] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const idsKey = ids.join(',');

  useEffect(() => {
    setRead(undefined); setError(undefined); setRefreshNote(undefined);
    if (ids.length < 2 || ids.length > 4) {
      setError('Pick two to four runs to compare.'); setLoading(false);
      return;
    }
    const control = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let haveData = false;
    setLoading(true);
    // One response carries results, recorded ends and pinned configuration
    // together. Re-read it only while at least one run is provisional.
    const load = async () => {
      try {
        const value = await api.request<unknown>(`/v1/runs/compare?ids=${ids.map(encodeURIComponent).join(',')}`, undefined, control.signal);
        if (disposed || control.signal.aborted) return;
        const next = readComparison(value, ids);
        haveData = true;
        setRead(next); setError(undefined); setRefreshNote(undefined); setLoading(false);
        const policy = comparisonRefreshPolicy(next);
        if (policy.active) timer = setTimeout(() => { void load(); }, policy.intervalMs ?? COMPARISON_REFRESH_MS);
      } catch (problem) {
        if (disposed || control.signal.aborted) return;
        setLoading(false);
        if (!haveData) setError(String(problem));
        else {
          // Keep the last coherent frame; report the refresh failure instead of
          // replacing results the user is already reading.
          setRefreshNote(String(problem));
          timer = setTimeout(() => { void load(); }, COMPARISON_REFRESH_MS);
        }
      }
    };
    void load();
    return () => { disposed = true; control.abort(); if (timer) clearTimeout(timer); };
  }, [api, idsKey, attempt]);

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
  const runs = useMemo(() => read?.runs.map(entry => entry.run), [read]);
  const entries = useMemo(() => new Map<string, ComparisonRunRead>((read?.runs ?? []).map(entry => [entry.run.id, entry] as const)), [read]);
  const configuration = read?.configuration;
  const configurationError = read?.configurationError;
  const allFinal = !!read && read.runs.length > 0 && read.runs.every(entry => entry.state === 'final');
  const declaredOrder = useMemo(() => {
    const order: string[] = [];
    runs?.forEach(run => {
      const template = run.template_id ? templates.get(run.template_id) : undefined;
      template?.kpis.forEach(id => { if (!order.includes(id)) order.push(id); });
    });
    return order;
  }, [runs, templates]);
  const rows = useMemo(() => (runs ? kpiRows(runs, declaredOrder, allFinal) : []), [runs, declaredOrder, allFinal]);
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
      <MixedNote runs={runs} />
      {read && <RefreshNote read={read} locale={locale} />}
      {refreshNote && <p className="newc-catalog-note" role="status">{locale === 'zh-CN' ? '最新一次刷新失败，仍显示上一次一致的结果：' : 'The latest refresh failed; showing the last coherent results: '}{refreshNote}</p>}
      {catalogNote && <p className="newc-catalog-note" role="note">{t('Metric names and settings come from machine ids — template details are unavailable ({error}).', { error: catalogNote })}</p>}
      <SummaryTable runs={runs} entries={entries} configuration={configuration} configurationError={configurationError} templates={templates} locale={locale} />
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
                  if (!row.present[run.id]) return <td key={run.id} className="newc-config-absent">{locale === 'zh-CN' ? '未设置' : 'Not set'}</td>;
                  // Exact lossless text is always available; bulk structures stay collapsed so the
                  // first screen keeps showing key settings and results instead of raw JSON walls.
                  const text = exactText(value);
                  if (isBulk(value)) return <td key={run.id} className="newc-config-bulk"><details className="newc-config-details">
                    <summary>{locale === 'zh-CN' ? '显示原始 JSON' : 'Show raw JSON'} · {bulkSummary(value, locale)}</summary>
                    <pre className="wb-mono newc-config-raw" tabIndex={0}>{text}</pre>
                  </details></td>;
                  if (value === null) return <td key={run.id} className="newc-config-null"><code className="newc-config-tag" title={t('null')}>{t('null')}</code></td>;
                  if (row.path.endsWith('/source') && typeof value === 'string' && text.length > 96 && value.includes('/'))
                    return <td key={run.id}><details className="newc-config-details"><summary title={value}>{locale === 'zh-CN' ? '源位置' : 'Source location'} · {value.slice(value.lastIndexOf('/') + 1)}</summary><code>{text}</code></details></td>;
                  return <td key={run.id}><code className={typeof value === 'object' ? 'newc-config-raw' : undefined}>{text}</code></td>;
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
          <Column key={run.id} run={run} entry={entries.get(run.id)} final={allFinal} rows={primary} more={more} index={index} template={run.template_id ? templates.get(run.template_id) : undefined} />
        ))}
      </div>
    </div>
  );
}
