import { useState } from 'react';
import {Details} from '../console/Details';
import { PageState } from '../console/PageState';
import type { KpiSample, KpiValues } from './api';
import { metricHelp } from './metric-help';
import './results.css';

export interface KpiPanelProps {
  values: KpiValues;
  ids?:string[];
  series: KpiSample[];
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
}

/** Reads a KPI value in a friendly fixed precision, unit included, never faking null as 0. The meaningless "count" unit is dropped. */
function formatKpi(value: number | null, unit: string): string {
  if (value === null || !Number.isFinite(value)) return '—';
  if (unit === 'count') unit = '';
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

function KpiCard({ id, kpi, series }: { id: string; kpi: KpiValues[string]; series: KpiSample[] }) {
  const [showHelp, setShowHelp] = useState(false);
  const help = metricHelp(id);
  return (
    <div className="newc-kpi-card">
      <span className="newc-kpi-label">
        <span>{kpi.label}</span>
        {help !== undefined && (
          <button
            type="button"
            className="newc-kpi-help"
            aria-label={`About ${kpi.label}`}
            aria-expanded={showHelp}
            title="What does this mean?"
            onClick={() => setShowHelp(open => !open)}
          >?</button>
        )}
      </span>
      {showHelp && help !== undefined && <p className="newc-kpi-help-text">{help}</p>}
      <span className={`newc-kpi-value${kpi.value === null ? ' is-null' : ''}`}>
        {formatKpi(kpi.value, kpi.unit)}
      </span>
      <Sparkline series={series} kpiId={id} />
    </div>
  );
}

/**
 * Read-only result cards: the actual values produced by the run, each with its
 * unit and a mini trend line. While the first result has not landed yet the
 * panel says so instead of showing zeros. Metrics declared by the template
 * (`ids`) come first; any other metric present in the feed stays available
 * under "More metrics" instead of silently disappearing.
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
  const declared = ids ? ids.filter(id => values[id]) : Object.keys(values).filter(id=>values[id].value!==0);
  const rest = Object.keys(values).filter(id => !declared.includes(id));
  if (declared.length === 0 && rest.length === 0) {
    return (
      <PageState kind="empty" title="No results yet"
        description="This run has not published results for this moment. Follow live or move along the timeline." />
    );
  }
  return (
    <div className="newc-kpis" aria-busy={loading || undefined}>
      {declared.length > 0 && (
        <div className="newc-kpi-grid">
          {declared.map(id => <KpiCard key={id} id={id} kpi={values[id]} series={series} />)}
        </div>
      )}
      {rest.length > 0 && (
        <details className="newc-kpi-more">
          <summary>More metrics ({rest.length})</summary>
          <div className="newc-kpi-grid">
            {rest.map(id => <KpiCard key={id} id={id} kpi={values[id]} series={series} />)}
          </div>
        </details>
      )}
    </div>
  );
}
