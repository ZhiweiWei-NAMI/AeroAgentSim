import {Details} from '../console/Details';
import { PageState } from '../console/PageState';
import type { KpiSample, KpiValues } from './api';
import './results.css';

export interface KpiPanelProps {
  values: KpiValues;
  ids?:string[];
  series: KpiSample[];
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
}

/** Reads a KPI value in a friendly fixed precision, unit included, never faking null as 0. */
function formatKpi(value: number | null, unit: string): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const abs = Math.abs(value);
  const digits = Number.isInteger(value) ? 0 : abs >= 1000 ? 0 : abs >= 10 ? 1 : 2;
  return `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}`;
}

/** Small inline SVG sparkline (no chart dependency). Null samples become gaps. */
function Sparkline({ series, kpiId }: { series: KpiSample[]; kpiId: string }) {
  const points = series.map(sample => sample.kpis[kpiId]?.value ?? null);
  const known = points.filter((point): point is number => point !== null && Number.isFinite(point));
  if (known.length < 2) return null;
  const min = Math.min(...known), max = Math.max(...known);
  const span = max - min || 1;
  const coords = points.map((point, index) => {
    if (point === null || !Number.isFinite(point)) return null;
    const x = (index / (points.length - 1)) * 100;
    const y = 26 - ((point - min) / span) * 24;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const paths: string[] = [];
  let current: string[] = [];
  coords.forEach(coordinate => {
    if (coordinate) current.push(coordinate);
    else if (current.length > 1) { paths.push(`M${current.join(' L')}`); current = []; }
    else current = [];
  });
  if (current.length > 1) paths.push(`M${current.join(' L')}`);
  if (!paths.length) return null;
  return (
    <svg className="newc-kpi-spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true">
      {paths.map((path, index) => <path key={index} d={path} fill="none" stroke="currentColor" strokeWidth="1.6" />)}
    </svg>
  );
}

/**
 * Read-only result cards: the actual values produced by the run, each with its
 * unit and a mini trend line. While the first result has not landed yet the
 * panel says so instead of showing zeros.
 */
export function KpiPanel({ids, values, series, loading, error, onRetry }: KpiPanelProps) {
  if (error) {
    return (
      <PageState kind="error" title="Could not load the results"
        description="Results could not be read from the simulation service."
        action={<>{onRetry&&<button type="button" className="newc-button" onClick={onRetry}>Try again</button>} <Details title="Results request"><p>{error}</p></Details></>} />
    );
  }
  if (loading && Object.keys(values).length === 0) {
    return <PageState kind="loading" title="Waiting for the first results" description="Metrics appear here as soon as the simulation produces them." />;
  }
  const entries = ids?ids.flatMap(id=>values[id]?[[id,values[id]] as const]:[]):Object.entries(values);
  if (entries.length === 0) {
    return (
      <PageState kind="empty" title="No results yet"
        description="This run has not published results for this moment. Follow live or move along the timeline." />
    );
  }
  return (
    <div className="newc-kpis" aria-busy={loading || undefined}>
      <div className="newc-kpi-grid">
        {entries.map(([id, kpi]) => (
          <div key={id} className="newc-kpi-card">
            <span className="newc-kpi-label">{kpi.label}</span>
            <span className={`newc-kpi-value${kpi.value === null ? ' is-null' : ''}`}>
              {formatKpi(kpi.value, kpi.unit)}
            </span>
            <Sparkline series={series} kpiId={id} />
          </div>
        ))}
      </div>
    </div>
  );
}
