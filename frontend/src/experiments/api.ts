import type { KnobValue } from '../newcomers/api';

/**
 * The template experiment contract from `aeroagentsim.experiment/v1`.
 * Only fields that the POST and GET routes actually return are decoded here.
 */
export const EXPERIMENT_CONTRACT = 'aeroagentsim.experiment/v1';

export interface ExperimentMetric {
  mean: number | null;
  std: number | null;
  n: number;
  unit: string | null;
  label: string | null;
  best: boolean;
}

export interface ExperimentTableRow {
  config_id: string;
  knobs: Record<string, KnobValue>;
  metrics: Record<string, ExperimentMetric>;
}

export interface ExperimentKpiMeta {
  unit: string | null;
  label: string | null;
}

export interface ExperimentRun {
  config_id: string;
  seed: number;
  /** Absolute run directory, or null when no run directory was created. */
  run: string | null;
  /** Run id, or null when the cell produced no run. */
  run_id: string | null;
  status: string;
  error: string | null;
  kpis: Record<string, number | null>;
  kpi_meta: Record<string, ExperimentKpiMeta>;
}

export interface ExperimentReport {
  id: string;
  contract: string;
  template_id: string;
  template_title: string;
  template_title_zh?: string;
  status: string;
  seeds: number;
  jobs: number;
  horizon_s: number | null;
  knobs: Record<string, KnobValue>;
  vary: Record<string, KnobValue[]>;
  total: number;
  completed: number;
  failed: number;
  error: string | null;
  runs: ExperimentRun[];
  table: ExperimentTableRow[];
}

export interface ExperimentPlan {
  vary: Record<string, KnobValue[]>;
  seeds: number;
  jobs: number;
  horizon_s?: number;
}

export class ExperimentDecodeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ExperimentDecodeError';
  }
}

export interface ExperimentTransport {
  request<T>(path: string, init?: { method?: string; body?: unknown; signal?: AbortSignal }): Promise<T>;
}

type ObjectValue = Record<string, unknown>;

function object(value: unknown, what: string): ObjectValue {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new ExperimentDecodeError(`${what}: JSON object required`);
  }
  return value as ObjectValue;
}

function array(value: unknown, what: string): unknown[] {
  if (!Array.isArray(value)) throw new ExperimentDecodeError(`${what}: list required`);
  return value;
}

function text(value: unknown, what: string): string {
  if (typeof value !== 'string') throw new ExperimentDecodeError(`${what}: text required`);
  return value;
}

function nullableText(value: unknown, what: string): string | null {
  if (value === null) return null;
  return text(value, what);
}

function count(value: unknown, what: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw new ExperimentDecodeError(`${what}: non-negative integer required`);
  }
  return value;
}

function positiveCount(value: unknown, what: string): number {
  const parsed = count(value, what);
  if (parsed < 1) throw new ExperimentDecodeError(`${what}: positive integer required`);
  return parsed;
}

function nullableNumber(value: unknown, what: string): number | null {
  if (value === null) return null;
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new ExperimentDecodeError(`${what}: finite number or null required`);
  }
  return value;
}

function bool(value: unknown, what: string): boolean {
  if (typeof value !== 'boolean') throw new ExperimentDecodeError(`${what}: boolean required`);
  return value;
}

function knobs(value: unknown, what: string): Record<string, KnobValue> {
  const source = object(value, what);
  const result: Record<string, KnobValue> = {};
  for (const [name, candidate] of Object.entries(source)) {
    if (typeof candidate !== 'string' && typeof candidate !== 'number' && typeof candidate !== 'boolean') {
      throw new ExperimentDecodeError(`${what}.${name}: scalar required`);
    }
    result[name] = candidate;
  }
  return result;
}

function vary(value: unknown, what: string): Record<string, KnobValue[]> {
  const source = object(value, what);
  const result: Record<string, KnobValue[]> = {};
  for (const [name, candidate] of Object.entries(source)) {
    const values = array(candidate, `${what}.${name}`);
    if (!values.length) throw new ExperimentDecodeError(`${what}.${name}: nonempty list required`);
    result[name] = values.map((item, index) => {
      if (typeof item !== 'string' && typeof item !== 'number' && typeof item !== 'boolean') {
        throw new ExperimentDecodeError(`${what}.${name}[${index}]: scalar required`);
      }
      return item;
    });
  }
  return result;
}

function kpis(value: unknown, what: string): Record<string, number | null> {
  const source = object(value, what);
  const result: Record<string, number | null> = {};
  for (const [name, candidate] of Object.entries(source)) {
    result[name] = nullableNumber(candidate, `${what}.${name}`);
  }
  return result;
}

function kpiMeta(value: unknown, what: string): Record<string, ExperimentKpiMeta> {
  const source = object(value, what);
  const result: Record<string, ExperimentKpiMeta> = {};
  for (const [name, candidate] of Object.entries(source)) {
    const row = object(candidate, `${what}.${name}`);
    result[name] = {
      unit: row.unit === undefined ? null : nullableText(row.unit, `${what}.${name}.unit`),
      label: row.label === undefined ? null : nullableText(row.label, `${what}.${name}.label`),
    };
  }
  return result;
}

function decodeRun(value: unknown, what: string): ExperimentRun {
  const row = object(value, what);
  return {
    config_id: text(row.config_id, `${what}.config_id`),
    seed: count(row.seed, `${what}.seed`),
    run: nullableText(row.run, `${what}.run`),
    run_id: nullableText(row.run_id, `${what}.run_id`),
    status: text(row.status, `${what}.status`),
    error: nullableText(row.error, `${what}.error`),
    kpis: kpis(row.kpis, `${what}.kpis`),
    kpi_meta: kpiMeta(row.kpi_meta, `${what}.kpi_meta`),
  };
}

function decodeMetric(value: unknown, what: string): ExperimentMetric {
  const row = object(value, what);
  return {
    mean: nullableNumber(row.mean, `${what}.mean`),
    std: nullableNumber(row.std, `${what}.std`),
    n: count(row.n, `${what}.n`),
    unit: nullableText(row.unit, `${what}.unit`),
    label: nullableText(row.label, `${what}.label`),
    best: bool(row.best, `${what}.best`),
  };
}

function decodeTableRow(value: unknown, what: string): ExperimentTableRow {
  const row = object(value, what);
  const metrics = object(row.metrics, `${what}.metrics`);
  const decoded: Record<string, ExperimentMetric> = {};
  for (const [name, candidate] of Object.entries(metrics)) {
    decoded[name] = decodeMetric(candidate, `${what}.metrics.${name}`);
  }
  return {
    config_id: text(row.config_id, `${what}.config_id`),
    knobs: knobs(row.knobs, `${what}.knobs`),
    metrics: decoded,
  };
}

export function decodeExperiment(value: unknown): ExperimentReport {
  const report = object(value, 'experiment');
  const contract = text(report.contract, 'experiment.contract');
  if (contract !== EXPERIMENT_CONTRACT) {
    throw new ExperimentDecodeError(`experiment.contract: expected ${EXPERIMENT_CONTRACT}`);
  }
  const optionalTitleZh = report.template_title_zh;
  return {
    id: text(report.id, 'experiment.id'),
    contract,
    template_id: text(report.template_id, 'experiment.template_id'),
    template_title: text(report.template_title, 'experiment.template_title'),
    ...(optionalTitleZh === undefined ? {} : { template_title_zh: text(optionalTitleZh, 'experiment.template_title_zh') }),
    status: text(report.status, 'experiment.status'),
    seeds: positiveCount(report.seeds, 'experiment.seeds'),
    jobs: positiveCount(report.jobs, 'experiment.jobs'),
    horizon_s: nullableNumber(report.horizon_s, 'experiment.horizon_s'),
    knobs: knobs(report.knobs, 'experiment.knobs'),
    vary: vary(report.vary, 'experiment.vary'),
    total: count(report.total, 'experiment.total'),
    completed: count(report.completed, 'experiment.completed'),
    failed: count(report.failed, 'experiment.failed'),
    error: nullableText(report.error, 'experiment.error'),
    runs: array(report.runs, 'experiment.runs').map((run, index) => decodeRun(run, `experiment.runs[${index}]`)),
    table: array(report.table, 'experiment.table').map((row, index) => decodeTableRow(row, `experiment.table[${index}]`)),
  };
}

/**
 * The only experiment routes the server exposes: create on a template and read by id.
 * There is deliberately no list, resume or cancel method.
 */
export class ExperimentApi {
  constructor(private readonly transport: ExperimentTransport) {}

  async create(templateId: string, plan: ExperimentPlan, signal?: AbortSignal): Promise<ExperimentReport> {
    return decodeExperiment(await this.transport.request<unknown>(
      `/v1/templates/${encodeURIComponent(templateId)}/experiments`,
      { method: 'POST', body: plan, signal },
    ));
  }

  async get(experimentId: string, signal?: AbortSignal): Promise<ExperimentReport> {
    return decodeExperiment(await this.transport.request<unknown>(
      `/v1/experiments/${encodeURIComponent(experimentId)}`,
      { signal },
    ));
  }
}
