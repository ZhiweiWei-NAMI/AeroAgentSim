import { describe, expect, it } from 'vitest';
import { groupDependencies, parseFact, positionFieldOf, ID_PATTERN } from './model';

describe('parseFact', () => {
  it('keeps blank input as "omit this field"', () => {
    expect(parseFact({ type: 'number' }, '')).toEqual({});
    expect(parseFact({ type: 'boolean' }, '  ')).toEqual({});
  });
  it('rejects malformed numbers without inventing 0', () => {
    expect(parseFact({ type: 'number' }, 'abc').error).toBe('number');
    expect(parseFact({ type: 'number' }, '').value).toBeUndefined();
  });
  it('keeps integers whole', () => {
    expect(parseFact({ type: 'integer' }, '4.5').error).toBe('integer');
    expect(parseFact({ type: 'integer' }, '6')).toEqual({ value: 6 });
  });
  it('vectors must have the contract length', () => {
    expect(parseFact({ type: 'vector', length: 3 }, '1, 2').error).toBe('3 values');
    expect(parseFact({ type: 'vector', length: 3 }, '1, 2, 3')).toEqual({ value: [1, 2, 3] });
  });
  it('booleans accept only true/false', () => {
    expect(parseFact({ type: 'boolean' }, 'maybe').error).toBe('true | false');
    expect(parseFact({ type: 'boolean' }, 'false')).toEqual({ value: false });
  });
});

describe('ID_PATTERN', () => {
  it('matches the authoring identifier contract', () => {
    expect(ID_PATTERN.test('uav-1')).toBe(true);
    expect(ID_PATTERN.test('lowalt:Restaurant/pad_2.0')).toBe(true);
    expect(ID_PATTERN.test('bad id')).toBe(false);
    expect(ID_PATTERN.test('uav/*')).toBe(false);
  });
});

describe('groupDependencies', () => {
  it('groups by first-seen kind order and keeps server order within a kind', () => {
    const groups = groupDependencies([
      { path: '/a', kind: 'presentation', detail: 'x' },
      { path: '/b', kind: 'binding', detail: 'y' },
      { path: '/c', kind: 'presentation', detail: 'z' },
    ]);
    expect(groups.map(([kind]) => kind)).toEqual(['presentation', 'binding']);
    expect(groups[0][1].map(row => row.path)).toEqual(['/a', '/c']);
  });
});

describe('positionFieldOf', () => {
  it('uses the presentation position field when declared, else the studio default', () => {
    expect(positionFieldOf({ presentation: [{ typeId: 'oo:UAV', positionField: 'lowalt.position' }] } as never, 'oo:UAV')).toBe('lowalt.position');
    expect(positionFieldOf({} as never, 'oo:UAV')).toBe('aas.studio.position_enu_m');
  });
});
