import type { ComparisonRun } from './api';
import { decodeComparison } from './api-decode';

/** What the service's result cursor/finality evidence allows us to claim. */
export type RunResultState = 'final' | 'provisional' | 'unknown';
export type MetricDirection = 'min' | 'max' | 'neutral';

export interface RecordedEnd { at_ns: string; commit_index: number; status: string }
export interface ConfigurationDifference { path: string; values: Record<string, unknown>; present: Record<string, boolean> }
export interface ComparisonConfiguration { alignment: 'recorded_run_end'; recorded_ends: Record<string, RecordedEnd>; config_diff: ConfigurationDifference[] }

/** One decoded run plus the finality/cursor evidence kept from the response. */
export interface ComparisonRunRead {
  run: ComparisonRun;
  status?: string;
  resultsFinal?: boolean;
  resultsCursor?: number;
  finalCommitIndex?: number;
  recordedEnd?: RecordedEnd;
  /** Result cut this run's KPIs describe, when a results checkpoint is known. */
  cursor?: number;
  state: RunResultState;
}

export interface ComparisonRead {
  runs: ComparisonRunRead[];
  configuration?: ComparisonConfiguration;
  configurationError?: string;
}

export interface MetricDirectionRead {
  direction: MetricDirection;
  /** At least one run declared a direction, or a known fallback exists. */
  defined: boolean;
  /** Every declared direction agrees (false when min/max are mixed). */
  consistent: boolean;
}

export interface ComparisonRefreshPolicy {
  active: boolean;
  intervalMs?: number;
  reason: 'provisional' | 'final' | 'unknown';
}

/** A provisional comparison is re-read at this cadence; it never auto-advances a final one. */
export const COMPARISON_REFRESH_MS = 3000;

// Same terminal states the service uses (app.py / feed_index.py).
const TERMINAL_STATUS = new Set(['completed', 'stopped', 'faulted', 'interrupted', 'input_timeout']);

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function cursor(value: unknown): number | undefined {
  return Number.isSafeInteger(value) && (value as number) >= 0 ? value as number : undefined;
}

function validRecordedEnd(value: unknown): value is RecordedEnd {
  const end = record(value);
  return !!end && typeof end.at_ns === 'string' && /^\d+$/.test(end.at_ns) &&
    Number.isSafeInteger(end.commit_index) && (end.commit_index as number) >= 1 && typeof end.status === 'string';
}

/** Strict reader for the pinned configuration half; unchanged from the original comparison contract. */
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

/**
 * Derive finality from the summary fields. Missing evidence (older servers)
 * stays provisional; evidence that contradicts itself is unknown, never final.
 */
export function deriveResultState(evidence: {
  status?: unknown; results_final?: unknown; results_cursor?: unknown;
  final_commit_index?: unknown; recorded_end?: unknown;
}): RunResultState {
  const final = evidence.results_final;
  const resultsCursor = evidence.results_cursor;
  const finalCommitIndex = evidence.final_commit_index;
  if (final !== undefined && typeof final !== 'boolean') return 'unknown';
  if (resultsCursor !== undefined && resultsCursor !== null && cursor(resultsCursor) === undefined) return 'unknown';
  if (finalCommitIndex !== undefined && finalCommitIndex !== null && cursor(finalCommitIndex) === undefined) return 'unknown';
  // Older services may supply only some of the finality fields. Absence is
  // not contradictory evidence, but it can never establish a final result.
  if (final !== true) return 'provisional';
  if (typeof resultsCursor !== 'number' || typeof finalCommitIndex !== 'number') return 'provisional';
  const recordedEnd = evidence.recorded_end;
  if (!validRecordedEnd(recordedEnd) || !TERMINAL_STATUS.has(recordedEnd.status)) return 'unknown';
  // `results_final` already implies a terminal status; when the summary also
  // carries one it must agree with the recorded end.
  if (evidence.status !== undefined) {
    if (typeof evidence.status !== 'string' || !TERMINAL_STATUS.has(evidence.status)) return 'unknown';
    if (recordedEnd.status !== evidence.status) return 'unknown';
  }
  // The KPI cut must be the recorded end, and the next checkpoint must follow it.
  if (recordedEnd.commit_index !== finalCommitIndex || resultsCursor !== finalCommitIndex + 1) return 'unknown';
  return 'final';
}

/**
 * Decode the whole comparison response once. Runs are validated exactly as
 * before (so the backend's unit rejection still surfaces); finality fields and
 * the configuration half are kept instead of being silently dropped.
 */
export function readComparison(value: unknown, ids?: string[]): ComparisonRead {
  const raw = record(value);
  const rawRuns = raw && Array.isArray(raw.runs) ? raw.runs : [];
  // include_kpis=false returns the same summaries without measurements. Keep
  // them usable as empty KPI maps, not fabricated zeroes; validate all supplied
  // measurements and settings with the existing decoder.
  const decoded = decodeComparison(raw && Array.isArray(raw.runs) ? {
    ...raw,
    runs: rawRuns.map(candidate => {
      const row = record(candidate);
      return row && row.kpis === undefined ? { ...row, kpis: {} } : candidate;
    }),
  } : value);
  let configuration: ComparisonConfiguration | undefined;
  let configurationError: string | undefined;
  try {
    configuration = comparisonConfiguration(value, ids);
  } catch (problem) {
    configurationError = String(problem);
  }
  const runs = decoded.runs.map((run, index) => {
    const row = record(rawRuns[index]) ?? {};
    const recordedEnd = configuration?.recorded_ends[run.id];
    const status = typeof row.status === 'string' ? row.status : undefined;
    const resultsFinal = typeof row.results_final === 'boolean' ? row.results_final : undefined;
    const resultsCursor = cursor(row.results_cursor);
    const finalCommitIndex = cursor(row.final_commit_index);
    const state = deriveResultState({
      status: row.status, results_final: row.results_final, results_cursor: row.results_cursor,
      final_commit_index: row.final_commit_index, recorded_end: recordedEnd,
    });
    // A terminal index/recorded end can be ahead of aggregation. Only the
    // results checkpoint establishes which cut these KPI values describe.
    const cut = resultsCursor !== undefined ? Math.max(0, resultsCursor - 1) : undefined;
    return { run, status, resultsFinal, resultsCursor, finalCommitIndex, recordedEnd, cursor: cut, state };
  });
  return { runs, configuration, configurationError };
}

/** Re-fetch only while at least one run is provisional; otherwise the comparison is frozen. */
export function comparisonRefreshPolicy(read: ComparisonRead): ComparisonRefreshPolicy {
  if (read.runs.some(entry => entry.state === 'provisional')) {
    return { active: true, intervalMs: COMPARISON_REFRESH_MS, reason: 'provisional' };
  }
  if (read.runs.length > 0 && read.runs.every(entry => entry.state === 'final')) {
    return { active: false, reason: 'final' };
  }
  return { active: false, reason: 'unknown' };
}

/** Cursors of the runs that are actually final (for the "frozen as of" label). */
export function finalCursors(read: ComparisonRead): number[] {
  return read.runs.flatMap(entry => entry.state === 'final' && entry.cursor !== undefined ? [entry.cursor] : []);
}

/**
 * Best-value emphasis needs one agreed direction. Mixed min/max declarations
 * are inconsistent; no declaration falls back to the known descriptive table.
 */
export function resolveMetricDirection(runs: ComparisonRun[], id: string, known: Record<string, MetricDirection> = {}): MetricDirectionRead {
  const declared = runs.flatMap(run => {
    const direction = run.kpis[id]?.direction;
    return direction ? [direction] : [];
  });
  const unique = [...new Set(declared)];
  if (unique.length > 1) return { direction: 'neutral', defined: false, consistent: false };
  if (unique.length === 1) return { direction: unique[0], defined: true, consistent: true };
  const fallback = known[id] ?? 'neutral';
  return { direction: fallback, defined: fallback !== 'neutral', consistent: true };
}
