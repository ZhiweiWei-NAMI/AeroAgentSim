import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { NonspatialViews } from './NonspatialViews';
import { ancestryLabel, layoutGraph, pickNode, recordedFields } from './nonspatial';
import cases from './nonspatial-cases.json';

const at = (ns: string) => ({ ns, microstep: 0 });
const a = { id: 'a', generation: 0 }, b = { id: 'b', generation: 0 };
const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'nonspatial-test', registryDigest: 'fixture', start: at('0'),
  types: [{ typeId: 'Job', displayName: 'Job', ancestors: ['ParentA', 'ParentB'], directory: 'Recorded directory' },
    { typeId: 'ParentA', displayName: 'Parent A', ancestors: [] }, { typeId: 'ParentB', displayName: 'Parent B', ancestors: [] }],
  fields: [{ fieldId: 'state', displayName: 'State', valueType: 'any', role: 'state' }], presentation: [] };
const commit = (index: number, ns: string, changes: Partial<FeedCommit> = {}): FeedCommit => ({ commitIndex: index, at: at(ns), created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [], ...changes });
function fixture() {
  const store = new TemporalFeedStore(header), edge = { edgeId: 'e', relationId: 'linked', source: a, target: b, validFrom: at('0') };
  store.ingest(commit(1, '0', { created: [a, b].map(key => ({ ...key, typeId: 'Job' })), facts: [{ entity: a, fieldId: 'state', value: 0, producer: 'recorded', validFrom: at('0') }], edges: [{ ...edge, op: 'assert' }] }));
  store.ingest(commit(2, '1', { facts: [{ entity: a, fieldId: 'state', value: false, producer: 'recorded', validFrom: at('1') }] }));
  store.ingest(commit(3, '2', { facts: [{ entity: a, fieldId: 'state', value: null, producer: 'recorded', validFrom: at('2') }] }));
  store.ingest(commit(4, '3', { retracted: [{ entity: a, fieldId: 'state', validFrom: at('3') }] }));
  store.ingest(commit(5, '5', { edges: [{ ...edge, op: 'close', validTo: at('5') }] }));
  store.ingest(commit(6, '7', { edges: [{ ...edge, op: 'cancel', validFrom: null }] }));
  return store;
}

describe('exact nonspatial replay', () => {
  it.each(cases)('$case', row => {
    const store = fixture(); store.seek(row.ns, row.commitCut);
    const value = store.entities.get('a:0')!.fields.get('state');
    if (typeof row.expectedValue === 'object' && row.expectedValue !== null) expect(value).toBeUndefined();
    else expect(value!.value).toEqual(row.expectedValue);
    expect(store.edges.has('e')).toBe(row.edgeActive);
    expect(recordedFields(store, 'Job')).toEqual(['state']);
  });
  it('updates table values and selected entity using the same cursor as the inspector', () => {
    const store = fixture(); store.seek('0', 1); const select = vi.fn();
    const view = render(<NonspatialViews store={store} onSelect={select} view="state" />);
    fireEvent.click(screen.getByRole('button', { name: 'a · g0' })); expect(select).toHaveBeenCalledWith(a);
    const row = screen.getByRole('button', { name: 'a · g0' }).closest('tr')!;
    expect(within(row).getByText('0', { selector: 'code' })).toBeInTheDocument();
    for (const [ns, text] of [['1', 'false'], ['2', 'null'], ['3', 'absent']]) {
      store.seek(ns); view.rerender(<NonspatialViews store={store} onSelect={select} selected={a} view="state" />);
      expect(within(row).getByText(text, { selector: 'code' })).toBeInTheDocument();
      expect(row).toHaveAttribute('aria-selected', 'true');
    }
  });
  it('keeps a DAG ancestors list instead of inventing a chain between parents', () => {
    expect(ancestryLabel(header.types[0], new Map(header.types.map(type => [type.typeId, type])))).toBe('Ancestors: Parent A (ParentA), Parent B (ParentB)');
  });
  it('distinguishes same-nanosecond knowledge cuts and canceled historical edges', () => {
    const store = fixture(); store.ingest(commit(7, '7', { facts: [{ entity: a, fieldId: 'state', value: 'later', producer: 'recorded', validFrom: at('7') }] }));
    store.seek('7', 6); expect(store.entities.get('a:0')!.fields.has('state')).toBe(false);
    store.seek('7', 7); expect(store.entities.get('a:0')!.fields.get('state')!.value).toBe('later');
    store.seek('0', 6); expect(store.edges.has('e')).toBe(false);
    store.seek('0', 1); expect(store.edges.has('e')).toBe(true);
  });
  it('lays out 2000 distinct entity nodes and keeps 5000 recorded edges with bounded table DOM', () => {
    const store = new TemporalFeedStore(header), keys = Array.from({ length: 2000 }, (_, index) => ({ id: `entity-${String(index).padStart(4, '0')}`, generation: 0 }));
    store.ingest(commit(1, '0', { created: keys.map(key => ({ ...key, typeId: 'Job' })), facts: keys.map((entity, value) => ({ entity, fieldId: 'state', value, producer: 'fixture', validFrom: at('0') })),
      edges: Array.from({ length: 5000 }, (_, index) => ({ edgeId: `edge-${index}`, relationId: 'linked', source: keys[index % keys.length], target: keys[(index + 1) % keys.length], op: 'assert', validFrom: at('0') })) }));
    store.seek('0'); const layout = layoutGraph(store);
    expect(layout.nodes).toHaveLength(2000); expect(store.edges.size).toBe(5000);
    expect(new Set(layout.nodes.map(node => `${node.x}/${node.y}`)).size).toBe(2000);
    expect(pickNode(layout, layout.nodes[100].x, layout.nodes[100].y)).toEqual(layout.nodes[100].entity.key);
    const select = vi.fn(), view = render(<NonspatialViews store={store} onSelect={select} view="state" />);
    expect(view.container.querySelectorAll('tbody tr')).toHaveLength(50);
    fireEvent.click(screen.getByLabelText('Next state page')); expect(screen.getByRole('button', { name: 'entity-0050 · g0' })).toBeInTheDocument();
  });
});
