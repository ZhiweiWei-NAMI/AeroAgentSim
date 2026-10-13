import { describe, expect, it } from 'vitest';
import { changedSections, mergeSections, sectionSummary } from './merge-draft';

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
