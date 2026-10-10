import type {KnobValue} from './api';
import {parseAuthoringJson, stringifyLossless} from '../feeds/lossless-json';

/* Experiment API — typed client + decoders for the full-factorial experiment
 * endpoints (contract `aeroagentsim.experiment/v1`):
 *   POST /v1/templates/{id}/experiments  -> full report (status created)
 *   GET  /v1/experiments/{id}            -> report with live progress
 * Every number in a report comes from a real run; unmeasured values are nulls
 * and are never rewritten to zero here. */

export type ExperimentVary = Record<string, KnobValue[]>;

export interface ExperimentRequest {
  vary: ExperimentVary;
  seeds: number;
  jobs: number;
  horizon_s?: number;
  knobs?: Record<string, KnobValue>;
}

export interface ExperimentMetric {
  mean: number;
  /** Sample std; null when fewer than two observed samples (never zero). */
  std: number | null;
  n: number;
  unit: string | null;
  label: string;
  /** Descriptive per-metric min/max highlight set by the backend only. */
  best: boolean;
}

export interface ExperimentTableRow {
  config_id: string;
  knobs: Record<string, KnobValue>;
  metrics: Record<string, ExperimentMetric>;
}

export interface ExperimentRunRecord {
  config_id: string;
  seed: number;
  run: string | null;
  run_id: string | null;
  status: string | null;
  error: string | null;
  kpis: Record<string, number>;
  kpi_meta: Record<string, {unit?: string | null; label?: string | null}>;
}

export interface ExperimentReport {
  id: string;
  contract: string;
  template_id: string;
  template_title: string;
  status: 'created' | 'running' | 'completed' | 'faulted' | 'interrupted' | string;
  seeds: number;
  jobs: number;
  horizon_s: number | null;
  knobs: Record<string, KnobValue>;
  vary: ExperimentVary;
  total: number;
  completed: number;
  failed: number;
  error: string | null;
  runs: ExperimentRunRecord[];
  table: ExperimentTableRow[];
}

const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('The experiment service returned an invalid object.');
  return value as Record<string, unknown>;
};

/** Decodes typed scalar knob values (string/number/boolean) as declared by the
 * template registry; anything else is rejected rather than coerced. */
const typedKnobs = (value: unknown): Record<string, KnobValue> => {
  const knobs = object(value);
  for (const [key, item] of Object.entries(knobs)) {
    if (item === null || item === undefined || !['string', 'number', 'boolean'].includes(typeof item)) throw Error(`Experiment setting ${key} has an unsupported value.`);
  }
  return knobs as Record<string, KnobValue>;
};

const metric = (value: unknown, name: string): ExperimentMetric => {
  const data = object(value);
  if (typeof data.mean !== 'number' || !Number.isFinite(data.mean) || typeof data.n !== 'number' || !Number.isSafeInteger(data.n) || data.n < 1) {
    throw Error(`Experiment metric ${name} has no valid mean or sample count.`);
  }
  if (data.std !== null && (typeof data.std !== 'number' || !Number.isFinite(data.std))) throw Error(`Experiment metric ${name} has an invalid spread.`);
  if (typeof data.label !== 'string') throw Error(`Experiment metric ${name} has no label.`);
  return {
    mean: data.mean,
    std: data.std as number | null,
    n: data.n,
    unit: typeof data.unit === 'string' ? data.unit : null,
    label: data.label,
    best: (()=>{if(typeof data.best!=='boolean')throw Error(`Experiment metric ${name} has no comparison flag.`);return data.best;})(),
  };
};

const runRecord = (value: unknown): ExperimentRunRecord => {
  const data = object(value);
  if (typeof data.config_id !== 'string' || typeof data.seed !== 'number' || !Number.isSafeInteger(data.seed)) {
    throw Error('Experiment has an invalid run record.');
  }
  const kpis: Record<string, number> = {};
  if (data.kpis !== undefined && data.kpis !== null) {
    for (const [key, item] of Object.entries(object(data.kpis))) {
      if (typeof item !== 'number' || !Number.isFinite(item)) throw Error(`Experiment result ${key} is not a finite number.`);
      kpis[key] = item;
    }
  }
  const meta: ExperimentRunRecord['kpi_meta'] = {};
  if (data.kpi_meta !== undefined && data.kpi_meta !== null) {
    for (const [key, item] of Object.entries(object(data.kpi_meta))) {
      const description = object(item);
      meta[key] = {
        unit: typeof description.unit === 'string' ? description.unit : null,
        label: typeof description.label === 'string' ? description.label : null,
      };
    }
  }
  return {
    config_id: data.config_id,
    seed: data.seed,
    run: typeof data.run === 'string' ? data.run : null,
    run_id: typeof data.run_id === 'string' ? data.run_id : null,
    status: typeof data.status === 'string' ? data.status : null,
    error: typeof data.error === 'string' ? data.error : null,
    kpis,
    kpi_meta: meta,
  };
};

/** Typed decode of an experiment report; throws with a readable message on any shape drift. */
export function decodeExperimentReport(value: unknown): ExperimentReport {
  const data = object(value);
  if (data.contract !== 'aeroagentsim.experiment/v1') throw Error('The service returned an unknown experiment contract.');
  if (typeof data.id !== 'string' || typeof data.template_id !== 'string' || typeof data.status !== 'string') {
    throw Error('The experiment report is missing its identity.');
  }
  for (const key of ['seeds', 'jobs', 'total', 'completed', 'failed'] as const) {
    if (typeof data[key] !== 'number' || !Number.isSafeInteger(data[key]) || (data[key] as number) < 0) {
      throw Error(`The experiment report has an invalid ${key}.`);
    }
  }
  if (data.horizon_s !== null && (typeof data.horizon_s !== 'number' || !Number.isFinite(data.horizon_s))) {
    throw Error('The experiment report has an invalid horizon.');
  }
  if (!Array.isArray(data.runs) || !Array.isArray(data.table)) throw Error('The experiment report is missing its runs or table.');
  return {
    id: data.id,
    contract: data.contract,
    template_id: data.template_id,
    template_title: typeof data.template_title === 'string' ? data.template_title : data.template_id,
    status: data.status,
    seeds: data.seeds as number,
    jobs: data.jobs as number,
    horizon_s: data.horizon_s as number | null,
    knobs: typedKnobs(data.knobs ?? {}),
    vary: Object.fromEntries(Object.entries(object(data.vary)).map(([key, values]) => {
      if (!Array.isArray(values)) throw Error(`Experiment setting ${key} has no value list.`);
      return [key, values.map(value => typedKnobs({value}).value)];
    })),
    total: data.total as number,
    completed: data.completed as number,
    failed: data.failed as number,
    error: typeof data.error === 'string' ? data.error : null,
    runs: data.runs.map(runRecord),
    table: data.table.map(candidate => {
      const row = object(candidate);
      if (typeof row.config_id !== 'string') throw Error('The experiment table has an unnamed configuration.');
      const metrics: Record<string, ExperimentMetric> = {};
      if (row.metrics !== undefined && row.metrics !== null) {
        for (const [name, item] of Object.entries(object(row.metrics))) metrics[name] = metric(item, name);
      }
      return {config_id: row.config_id, knobs: typedKnobs(row.knobs ?? {}), metrics};
    }),
  };
}

export const EXPERIMENT_TERMINAL_STATUSES = ['completed', 'faulted', 'interrupted'] as const;
export const experimentIsTerminal = (status: string): boolean =>
  (EXPERIMENT_TERMINAL_STATUSES as readonly string[]).includes(status);

/** Open a finished child run on the Simple replay route (never a fake URL). */
export function experimentRunUrl(runId: string, base: string, templateId: string): string {
  return `/runs/${encodeURIComponent(runId)}?mode=replay&view=simple&template=${encodeURIComponent(templateId)}&api=${encodeURIComponent(base)}`;
}

/** Standalone experiment client (kept beside TemplateApi, same conventions). */
export class ExperimentApi {
  constructor(readonly base: string = window.location.origin) {}

  private async request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`${this.base.replace(/\/$/, '')}${path}`, {
      signal,
      ...(body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: stringifyLossless(body)}),
    });
    if (!response.ok) throw Error(`HTTP ${response.status}: ${await response.text()}`);
    return parseAuthoringJson(await response.text()) as T;
  }

  async create(templateId: string, body: ExperimentRequest, signal?: AbortSignal): Promise<ExperimentReport> {
    return decodeExperimentReport(await this.request<unknown>(`/v1/templates/${encodeURIComponent(templateId)}/experiments`, body, signal));
  }

  async get(experimentId: string, signal?: AbortSignal): Promise<ExperimentReport> {
    return decodeExperimentReport(await this.request<unknown>(`/v1/experiments/${encodeURIComponent(experimentId)}`, undefined, signal));
  }
}
