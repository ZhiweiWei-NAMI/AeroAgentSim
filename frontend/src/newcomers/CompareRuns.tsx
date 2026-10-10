import { useCallback, useEffect, useMemo, useState } from 'react';
import { PageState } from '../console/PageState';
import { TemplateApi, type ComparisonRun, type KnobValue } from './api';
import './results.css';

export interface CompareRunsProps {
  api: TemplateApi;
  /** Two to four run ids; fewer/more shows an honest message (the API refuses too). */
  ids: string[];
  onBack?: () => void;
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
  const abs = Math.abs(value);
  const digits = Number.isInteger(value) ? 0 : abs >= 1000 ? 0 : abs >= 10 ? 1 : 2;
  return `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}`;
}

interface KpiRow {
  id: string;
  label: string;
  unit: string;
  direction: 'min' | 'max' | 'neutral';
  values: (number | null)[];
  best?: number | null;
}

/** Rows sorted best-first; best values are highlighted per direction, never by raw max alone. */
function kpiRows(runs: ComparisonRun[]): KpiRow[] {
  const ids: string[] = [];
  runs.forEach(run => Object.keys(run.kpis ?? {}).forEach(id => { if (!ids.includes(id)) ids.push(id); }));
  const rows = ids.map(id => {
    const values = runs.map(run => run.kpis[id]?.value ?? null);
    const known = values.filter((value): value is number => value !== null && Number.isFinite(value));
    const direction = runs.map(run => run.kpis[id]?.direction).find(direction => direction !== undefined) ?? DIRECTIONS[id] ?? 'neutral';
    const best = known.length && direction !== 'neutral'
      ? (direction === 'min' ? Math.min(...known) : Math.max(...known))
      : undefined;
    return { id, label: runs.flatMap(run => (run.kpis[id] ? [run.kpis[id]] : []))[0]?.label ?? humanize(id),
      unit: runs.flatMap(run => (run.kpis[id] ? [run.kpis[id]] : []))[0]?.unit ?? '', direction, values, best };
  });
  return rows.sort((a, b) => a.direction.localeCompare(b.direction) || a.label.localeCompare(b.label));
}

/** Knob / algorithm columns where the compared runs actually differ. */
function differences(runs: ComparisonRun[]): { label: string; values: string[] }[] {
  const keys: string[] = [];
  runs.forEach(run => Object.keys(run.knobs ?? {}).forEach(key => { if (!keys.includes(key)) keys.push(key); }));
  const algorithmSlots = ['allocation', 'path_planning', 'conflict_resolution', 'charging'] as const;
  return [
    ...keys.map(key => ({ label: humanize(key), values: runs.map(run => display(run.knobs?.[key])) })),
    ...algorithmSlots
      .filter(slot => runs.some(run => run.algorithms?.[slot]))
      .map(slot => ({ label: humanize(slot), values: runs.map(run => display(run.algorithms?.[slot])) })),
  ].filter(column => new Set(column.values).size > 1);
}

function Column({ run, rows,index }: { run: ComparisonRun; rows: KpiRow[];index:number }) {
  return (
    <section className="newc-compare-col">
      <h3>{run.title || humanize(run.id)}</h3>
      {run.template_id && <p className="newc-compare-template">{humanize(run.template_id)}</p>}
      <table className="newc-compare-table">
        <caption>Results · preferred value highlighted</caption>
        <tbody>
          {rows.map(row => {
            const value = row.values.length ? row.values[index] : null;
            const isBest = row.best !== undefined && value !== null && value === row.best;
            return (
              <tr key={row.id} className={isBest ? 'is-best' : undefined}>
                <th scope="row">{row.label}</th>
                <td className={`newc-kpi-cell${isBest ? ' is-best' : ''}`}>
                  {formatKpi(value, row.unit)}
                  <Bar value={value} row={row} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
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
 * their own rows.
 */
export function CompareRuns({ api, ids, onBack }: CompareRunsProps) {
  const [runs, setRuns] = useState<ComparisonRun[]>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

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

  const retry = useCallback(() => setAttempt(attempt => attempt + 1), []);
  const rows = useMemo(() => (runs ? kpiRows(runs) : []), [runs]);
  const columns = useMemo(() => (runs ? differences(runs) : []), [runs]);

  if (loading && !runs) {
    return <PageState kind="loading" title="Loading the comparison" description="Fetching the results of the runs you selected." />;
  }
  if (error) {
    return (
      <PageState kind="error" title="Could not compare the runs" description={error}
        action={(
          <span className="newc-compare-actions">
            {onBack && <button type="button" className="newc-button" onClick={onBack}>Go back</button>}
            {ids.length >= 2 && ids.length <= 4 && <button type="button" className="newc-button" onClick={retry}>Try again</button>}
          </span>
        )} />
    );
  }
  if (!runs) return null;
  return (
    <div className="newc-compare">
      <header className="newc-compare-header">
        <h2 className="newc-compare-title">Comparing {runs.length} runs</h2>
        {onBack && <button type="button" className="newc-button" onClick={onBack}>Go back</button>}
      </header>
      {columns.length > 0 && (
        <section className="newc-compare-section">
          <h4>Settings that differ</h4>
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
          <Column key={run.id} run={run} rows={rows} index={index} />
        ))}
      </div>
    </div>
  );
}
