import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { EntityGraph } from './EntityGraph';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureHeader, fixtureCommits, vehicle } from './extension-fixture';

// jsdom has no ResizeObserver; the graph observes its svg for refits.
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', ResizeObserverStub);
// jsdom getBoundingClientRect is all zeros; give the svg a real box so Fit works.
vi.spyOn(SVGElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 600, height: 400, x: 0, y: 0, top: 0, left: 0, right: 600, bottom: 400, toJSON: () => ({}) } as DOMRect);

/** The graph renders real, human-labeled entity nodes, actual relation arrows and selection through existing props. */
it('draws the active entity node-link graph with human labels, relations and selection', () => {
  const store = new TemporalFeedStore(fixtureHeader);
  fixtureCommits.forEach(commit => store.ingest(commit));
  store.seek('10', 2);
  const select = vi.fn();
  const view = render(<EntityGraph store={store} selected={vehicle} onSelect={select} />);
  const graph = screen.getByTestId('entity-graph');
  // Human labels: id words humanized, never raw registry type ids as headings.
  expect(within(graph).getAllByText('Task 1').length).toBeGreaterThan(0);
  expect(within(graph).getAllByText('Vehicle 1').length).toBeGreaterThan(0);
  // Real recorded relation between the task and vehicle, drawn once.
  expect(graph.querySelectorAll('.eg-edge')).toHaveLength(1);
  // Selecting a node or using the accessible select reports the exact recorded key.
  fireEvent.click(within(graph).getByRole('button', { name: /Task 1,.*generation 1/ }));
  expect(select).toHaveBeenCalledWith({ id: 'task-1', generation: 1 });
  // Selection prop syncs: after the parent re-renders with the clicked key, the graph reflects it.
  view.rerender(<EntityGraph store={store} selected={{ id: 'task-1', generation: 1 }} onSelect={select} />);
  expect(screen.getByLabelText('Graph entity')).toHaveValue('task-1:1');
  fireEvent.change(screen.getByLabelText('Graph entity'), { target: { value: 'vehicle-1:2' } });
  expect(select).toHaveBeenCalledWith(vehicle);
});
