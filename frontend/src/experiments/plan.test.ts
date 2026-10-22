import { describe, expect, it } from 'vitest';
import type { TemplateKnob } from '../newcomers/api';
import {
  MAX_AXIS_VALUES,
  blockedAxis,
  configurationCount,
  horizonSeconds,
  parseAxisValues,
  positiveInteger,
  runCount,
} from './plan';

const numberKnob: TemplateKnob = { id: 'fleet_size', label: 'Number of drones', type: 'integer', default: 4, min: 1, max: 10 };
const decimalKnob: TemplateKnob = { id: 'rate', label: 'Rate', type: 'number', default: 0.5, min: 0.1, max: 2 };

describe('runCount', () => {
  it('multiplies every axis by the matched seeds and treats no axis as one configuration', () => {
    expect(runCount({}, 3)).toBe(3);
    expect(runCount({ allocator: ['nearest', 'auction'], fleet_size: [4, 8] }, 3)).toBe(12);
    expect(configurationCount({ allocator: ['nearest', 'auction'] })).toBe(2);
  });

  it('is unknown for an empty axis or invalid seed count', () => {
    expect(runCount({ allocator: [] }, 3)).toBeUndefined();
    expect(runCount({}, 0)).toBeUndefined();
    expect(runCount({}, 1.5)).toBeUndefined();
  });
});

describe('typed axis values', () => {
  it('keeps the declared integer/number type, deduplicates and checks bounds', () => {
    expect(parseAxisValues(numberKnob, '4, 8, 8')).toEqual({ values: [4, 8] });
    expect(parseAxisValues(decimalKnob, '0.25, 1.5')).toEqual({ values: [0.25, 1.5] });
    expect(parseAxisValues(numberKnob, '4.5')).toMatchObject({ problem: { code: 'not-integer' } });
    expect(parseAxisValues(decimalKnob, 'abc')).toMatchObject({ problem: { code: 'not-finite' } });
    expect(parseAxisValues(numberKnob, '11')).toMatchObject({ problem: { code: 'out-of-range' } });
  });

  it('refuses an empty or oversized axis rather than truncating it', () => {
    expect(parseAxisValues(numberKnob, '')).toMatchObject({ problem: { code: 'empty' } });
    const many = Array.from({ length: MAX_AXIS_VALUES + 1 }, (_, index) => String(index + 1)).join(',');
    expect(parseAxisValues(numberKnob, many)).toMatchObject({ problem: { code: 'too-many' } });
  });

  it('accepts only bounded positive integers and positive horizons', () => {
    expect(positiveInteger('3', 10)).toBe(3);
    expect(positiveInteger('0', 10)).toBeUndefined();
    expect(positiveInteger('11', 10)).toBeUndefined();
    expect(horizonSeconds('')).toBeNull();
    expect(horizonSeconds('300')).toBe(300);
    expect(horizonSeconds('0')).toBeUndefined();
    expect(horizonSeconds('soon')).toBeUndefined();
  });

  it('blocks seed and overridden duration knobs from the axis list', () => {
    expect(blockedAxis({ id: 'seed', label: 'Seed', type: 'integer', default: 1, pointer: '/run/seed' }, false)).toBe('seed');
    expect(blockedAxis({ id: 'duration', label: 'Duration', type: 'integer', default: 300, pointer: '/run/until_ns' }, true)).toBe('horizon');
    expect(blockedAxis({ id: 'duration', label: 'Duration', type: 'integer', default: 300, pointer: '/run/until_ns' }, false)).toBeUndefined();
  });
});
