import { describe, expect, it } from 'vitest';
import { changedFields, changedSections, mergeSections, rebaseDraft, sameDraftValue, sectionSummary } from './merge-draft';

const mine = { run: { until_ns: 100 }, entities: [{ id: 'a' }, { id: 'b' }], engines: { motion: { step_ns: 5 } } };
const server = { run: { until_ns: 200 }, entities: [{ id: 'a' }], engines: { motion: { step_ns: 5 } } };

describe('changedSections', () => {
  it('lists only the top-level sections that actually differ, in stable order', () => {
    expect(changedSections(mine, server).map(row => row.section)).toEqual(['run', 'entities']);
  });
  it('reports an absent section on either side as changed', () => {
    const local = { entities: [{ id: 'a' }] };
    const remote = { entities: [{ id: 'a' }], behaviours: { packages: [] } };
    expect(changedSections(local, remote).map(row => row.section)).toEqual(['behaviours']);
    expect(changedSections(remote, local).map(row => row.section)).toEqual(['behaviours']);
  });
});

describe('mergeSections', () => {
  it('keeps my sections and takes only the chosen server sections', () => {
    const merged = mergeSections(mine, server, [{ section: 'run', take: 'server' }, { section: 'entities', take: 'mine' }]);
    expect(merged).toEqual({ run: { until_ns: 200 }, entities: [{ id: 'a' }, { id: 'b' }], engines: { motion: { step_ns: 5 } } });
  });
  it('taking the server for every conflict reproduces the server draft', () => {
    const merged = mergeSections(mine, server, [{ section: 'run', take: 'server' }, { section: 'entities', take: 'server' }]);
    expect(merged).toEqual(server);
  });
  it('rejects choices for sections that are not in conflict — never silently defaults', () => {
    expect(() => mergeSections(mine, server, [{ section: 'engines', take: 'server' }])).toThrow(/not in conflict/);
  });
  it('rejects duplicate choices for the same section', () => {
    expect(() => mergeSections(mine, server, [{ section: 'run', take: 'mine' }, { section: 'run', take: 'server' }])).toThrow(/Duplicate/);
  });
  it('T3-R2-005: taking a deleted server section removes it while keeping my seed', () => {
    const merged = mergeSections({ city: 'huangpu', run: { seed: 43 } }, { run: { seed: 42 } }, [{ section: 'city', take: 'server' }, { section: 'run', take: 'mine' }]);
    expect(merged).toEqual({ run: { seed: 43 } });
    expect(merged).not.toHaveProperty('city');
  });
  it('preserves server field deletions and explicit nulls without substituting a value', () => {
    const merged = mergeSections({ run: { seed: 43, output: 'events' }, city: 'huangpu' }, { run: { seed: null }, city: null }, [{ section: 'run', take: 'server' }, { section: 'city', take: 'server' }]);
    expect(merged).toEqual({ run: { seed: null }, city: null });
    expect(merged.run).not.toHaveProperty('output');
  });
  it('requires an explicit valid choice for every changed section', () => {
    expect(() => mergeSections(mine, server, [{ section: 'run', take: 'mine' }])).toThrow(/every changed section/);
    expect(() => mergeSections(mine, server, [{ section: 'run', take: 'default' as never }])).toThrow(/Invalid choice/);
  });
});

it('T4-R2-010: compares seed values and identifies deleted fields distinctly from null', () => {
  expect(changedFields({ seed: 45, output: 'events', retained: null }, { seed: 44, output: null }, '/run')).toEqual([
    { path: '/run/seed', mine: 45, server: 44 },
    { path: '/run/output', mine: 'events', server: null },
    { path: '/run/retained', mine: null, server: undefined },
  ]);
});

describe('rebasing unsaved edits after mutations (T1-007)', () => {
  const base = { city: 'huangpu', run: { seed: 42 }, entities: [{ id: 'pad', facts: { capacity: 1 } }] };
  it('keeps local deletions and edits alongside server-created objects', () => {
    const own = { run: { seed: 43 }, entities: [{ id: 'pad', facts: { capacity: 2 } }] };
    const remote = { ...base, entities: [...base.entities, { id: 'qa-point', facts: {} }] };
    expect(rebaseDraft(base, own, remote)).toEqual({ scenario: { run: { seed: 43 }, entities: [{ id: 'pad', facts: { capacity: 2 } }, { id: 'qa-point', facts: {} }] }, conflicts: [] });
  });
  it('adopts changes already saved by the object flow, without keeping a dirty old revision', () => {
    const own = { ...base, run: { seed: 43 } };
    const remote = { ...own, entities: [...own.entities, { id: 'qa-point', facts: {} }] };
    expect(rebaseDraft(base, own, remote)).toEqual({ scenario: remote, conflicts: [] });
  });
  it('combines disjoint fields of one section, including a server deletion', () => {
    expect(rebaseDraft({ run: { seed: 42, step: 1 } }, { run: { seed: 43, step: 1 } }, { run: { seed: 42 } })).toEqual({ scenario: { run: { seed: 43 } }, conflicts: [] });
  });
  it('reports overlapping changes instead of silently saving a selected value', () => {
    expect(rebaseDraft({ run: { seed: 42 } }, { run: { seed: 43 } }, { run: { seed: 44 } })).toEqual({ scenario: { run: { seed: 43 } }, conflicts: ['/run/seed'] });
  });
});
it('compares draft content independent of object key order and treats authored undefined as deletion', () => {
  expect(sameDraftValue({ a: 1, b: [2, null], city: undefined }, { b: [2, null], a: 1 })).toBe(true);
  expect(sameDraftValue({ city: null }, {})).toBe(false);
});

describe('sectionSummary', both_locales());
function both_locales() {
  return () => {
    it('describes arrays and records without inventing content', () => {
      expect(sectionSummary('entities', mine.entities, 'en-US')).toBe('2 item(s)');
      expect(sectionSummary('entities', mine.entities, 'zh-CN')).toBe('2 项');
      expect(sectionSummary('run', mine.run, 'en-US')).toBe('1 field(s)');
      expect(sectionSummary('run', undefined, 'zh-CN')).toBe('（无）');
    });
  };
}
