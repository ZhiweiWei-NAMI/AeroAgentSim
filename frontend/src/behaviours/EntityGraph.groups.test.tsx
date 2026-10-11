import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { COLLAPSE_ABOVE, EntityGraph, computeLayout } from './EntityGraph';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { emptyCommit, fixtureHeader } from './extension-fixture';

class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', ResizeObserverStub);
Object.defineProperty(SVGElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
vi.spyOn(SVGElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 600, height: 400, x: 0, y: 0, top: 0, left: 0, right: 600, bottom: 400, toJSON: () => ({}) } as DOMRect);

const tasks = Array.from({ length: 25 }, (_, i) => ({ id: `task-${i + 1}`, generation: 1 }));
const vehicles = Array.from({ length: 3 }, (_, i) => ({ id: `vehicle-${i + 1}`, generation: 1 }));
function manyStore() {
  const store = new TemporalFeedStore(fixtureHeader);
  store.ingest({
    ...emptyCommit(1), created: [...tasks.map(key => ({ ...key, typeId: 'task' })), ...vehicles.map(key => ({ ...key, typeId: 'vehicle' }))],
    edges: tasks.map((task, i) => ({ edgeId: `e${i}`, relationId: 'assigned_to', source: task, target: vehicles[i % 3], op: 'assert' as const, validFrom: { ns: '0', microstep: 0 } })),
  });
  store.seek('1', 1);
  return store;
}

it('collapses groups larger than the threshold, keeps pinned members visible and never overlaps nodes', () => {
  const store = manyStore();
  const collapsed = computeLayout(store, new Set(), new Set(['task-7:1']));
  const group = collapsed.clusters.find(cluster => cluster.typeId === 'task')!;
  expect(25).toBeGreaterThan(COLLAPSE_ABOVE);
  expect(group.title).toBe('Tasks (25)');
  expect(group.expanded).toBe(false);
  expect(group.visible.map(node => node.id)).toEqual(['task-7:1']);
  expect(collapsed.clusters.find(cluster => cluster.typeId === 'vehicle')!.expanded).toBe(true);
  // 25 relations collapse into per-pair links to the pinned task or the group chip.
  expect(collapsed.edges.length).toBeLessThan(25);
  const open = computeLayout(store, new Set(['task']), new Set());
  expect(open.clusters.find(cluster => cluster.typeId === 'task')!.visible).toHaveLength(25);
  const spots = new Set(open.clusters.flatMap(cluster => cluster.visible).map(node => `${node.x}/${node.y}`));
  expect(spots.size).toBe(28);
  for (const a of open.clusters) for (const b of open.clusters) if (a !== b) expect(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y).toBe(true);
});

it('uses the authoritative authored name shared with the 3D labels and object inspector', () => {
  const store = manyStore();
  store.header.entities = [{ id: 'vehicle-1', type: 'vehicle', label: 'Meal courier 1', label_zh: '外卖配送机 1' }];
  expect(computeLayout(store, new Set(), new Set()).byId.get('vehicle-1:1')?.label).toBe('Meal courier 1');
  expect(computeLayout(store, new Set(), new Set(), 760, 'zh-CN').byId.get('vehicle-1:1')?.label).toBe('外卖配送机 1');
});

it('expands a collapsed group on click and always shows highlighted entities', () => {
  const store = manyStore();
  const view = render(<EntityGraph store={store} onSelect={() => {}} highlights={[{ id: 'task-9', generation: 1 }]} />);
  const graph = screen.getByTestId('entity-graph');
  expect(graph.querySelectorAll('.eg-node')).toHaveLength(4); // 3 vehicles + the highlighted task
  expect(graph.querySelectorAll('.eg-node-highlight')).toHaveLength(1);
  fireEvent.click(within(graph).getByRole('button', { name: 'Expand Tasks (25)' }));
  expect(graph.querySelectorAll('.eg-node')).toHaveLength(28);
  view.unmount();
});
