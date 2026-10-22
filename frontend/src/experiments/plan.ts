import type { KnobValue, TemplateKnob } from '../newcomers/api';

/** A browser guard, not a backend limit. Larger grids remain available through the CLI. */
export const MAX_EXPERIMENT_RUNS = 24;
export const MAX_EXPERIMENT_SEEDS = 10;
export const MAX_EXPERIMENT_JOBS = 8;
export const MAX_AXIS_VALUES = 12;

export type AxisProblemCode =
  | 'empty'
  | 'too-many'
  | 'not-integer'
  | 'not-finite'
  | 'out-of-range';

export interface AxisProblem {
  code: AxisProblemCode;
  token?: string;
}

export type AxisParse = { values: KnobValue[] } | { problem: AxisProblem };

/** Product of the axis value counts, times the matched seeds. One empty vary is one configuration. */
export function runCount(vary: Record<string, readonly KnobValue[]>, seeds: number): number | undefined {
  if (!Number.isSafeInteger(seeds) || seeds < 1) return undefined;
  let configurations = 1;
  for (const values of Object.values(vary)) {
    if (!values.length) return undefined;
    configurations *= values.length;
    if (!Number.isSafeInteger(configurations)) return undefined;
  }
  const total = configurations * seeds;
  return Number.isSafeInteger(total) ? total : undefined;
}

/** The configuration count for a preview, before multiplying by seeds. */
export function configurationCount(vary: Record<string, readonly KnobValue[]>): number | undefined {
  let configurations = 1;
  for (const values of Object.values(vary)) {
    if (!values.length) return undefined;
    configurations *= values.length;
    if (!Number.isSafeInteger(configurations)) return undefined;
  }
  return configurations;
}

function sameValue(left: KnobValue, right: KnobValue): boolean {
  return typeof left === typeof right && left === right;
}

/**
 * Parse comma-separated numeric axis values with the knob's declared type and bounds.
 * Values are de-duplicated so the preview matches the submitted `vary` array.
 */
export function parseAxisValues(knob: TemplateKnob, text: string): AxisParse {
  const tokens = text.split(',').map(token => token.trim()).filter(Boolean);
  if (!tokens.length) return { problem: { code: 'empty' } };
  if (tokens.length > MAX_AXIS_VALUES) return { problem: { code: 'too-many' } };
  const values: KnobValue[] = [];
  for (const token of tokens) {
    let value: number;
    if (knob.type === 'integer') {
      if (!/^[+-]?\d+$/.test(token)) return { problem: { code: 'not-integer', token } };
      value = Number(token);
      if (!Number.isSafeInteger(value)) return { problem: { code: 'not-integer', token } };
    } else if (knob.type === 'number') {
      value = Number(token);
      if (!token || !Number.isFinite(value)) return { problem: { code: 'not-finite', token } };
    } else {
      return { problem: { code: 'not-finite', token } };
    }
    if (
      (knob.min !== undefined && value < knob.min)
      || (knob.max !== undefined && value > knob.max)
    ) {
      return { problem: { code: 'out-of-range', token } };
    }
    if (!values.some(existing => sameValue(existing, value))) values.push(value);
  }
  return { values };
}

export function positiveInteger(text: string, maximum: number): number | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  const value = Number(trimmed);
  return Number.isSafeInteger(value) && value >= 1 && value <= maximum ? value : undefined;
}

/** Blank means the template's own horizon; undefined means the typed override is invalid. */
export function horizonSeconds(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value > 0 ? value : undefined;
}

/** A seed knob cannot be an axis; a duration knob cannot be varied while an override is set. */
export function blockedAxis(knob: TemplateKnob, horizonOverride: boolean): 'seed' | 'horizon' | undefined {
  if (knob.pointer === '/run/seed') return 'seed';
  if (knob.pointer === '/run/until_ns' && horizonOverride) return 'horizon';
  return undefined;
}

/** Stable identity for typed choice/boolean options. */
export function valueKey(value: KnobValue): string {
  return `${typeof value}:${String(value)}`;
}
