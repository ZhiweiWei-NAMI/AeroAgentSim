import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { TemporalFeedStore } from '../feeds/temporal-store';
import type { RunHeader } from '../contracts/viewer-feed';
import { ObjectsPanel } from './ObjectsPanel';

const scroll = vi.fn(), saved = Element.prototype.scrollIntoView;
let resized: () => void;
beforeEach(() => {
  localStorage.clear(); Element.prototype.scrollIntoView = scroll;
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { resized = callback; } observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); Element.prototype.scrollIntoView = saved; vi.clearAllMocks(); vi.unstubAllGlobals(); });
function world() {
  const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'objects', registryDigest: 'd', start: { ns: '0', microstep: 0 }, fields: [],
    types: [{ typeId: 'UAV', displayName: 'UAV', ancestors: [] }, { typeId: 'Task', displayName: 'Task', ancestors: [] }],
    presentation: [{ typeId: 'UAV', positionField: 'pos', frame: 'enu', visual: { kind: 'marker' } }] };
  const store = new TemporalFeedStore(header), at = { ns: '0', microstep: 0 };
  store.ingest({ commitIndex: 1, at, created: [{ id: 'drone-1', generation: 0, typeId: 'UAV' }, { id: 'task-1', generation: 0, typeId: 'Task' }],
    removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [] }); store.seek('0'); return store;
}
it('reveals a graph-selected nonspatial object through collapsed groups and spatial/search filters', () => {
  const props = { store: world(), revision: 1, selectionOrigin:'manual' as const, onSelect: vi.fn(), onClose: vi.fn() };
  const view = render(<I18nProvider><ObjectsPanel {...props} /></I18nProvider>);
  expect(screen.queryByTitle('task-1')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Search objects'), { target: { value: 'drone' } });
  fireEvent.click(screen.getByLabelText('Only objects in the 3D scene'));
  view.rerender(<I18nProvider><ObjectsPanel {...props} selected={{ id: 'task-1', generation: 0 }} /></I18nProvider>);
  const selected = screen.getByTitle('task-1');
  expect(selected).toHaveAttribute('aria-current', 'true');
  expect(screen.getByLabelText('Search objects')).toHaveValue('');
  expect(screen.getByLabelText('Only objects in the 3D scene')).not.toBeChecked();
  expect(scroll).toHaveBeenCalledWith({ block: 'nearest' });
  fireEvent.click(selected); expect(props.onSelect).toHaveBeenCalledWith({ id: 'task-1', generation: 0 }, false);
});
it('keeps explicit group collapse usable after selecting an object', () => {
  const props = { store: world(), revision: 1, selectionOrigin:'manual' as const, selected: { id: 'drone-1', generation: 0 }, onSelect: vi.fn(), onClose: vi.fn() };
  render(<I18nProvider><ObjectsPanel {...props} /></I18nProvider>);
  expect(screen.getByTitle('drone-1')).toHaveAttribute('aria-current', 'true');
  fireEvent.click(within(screen.getByTestId('wb-objects')).getByRole('button', { name: 'UAV 1' }));
  expect(screen.queryByTitle('drone-1')).not.toBeInTheDocument();
});
it('reveals the selection when window resizing or an expanded dock reduces the list height', () => {
  render(<I18nProvider><ObjectsPanel store={world()} revision={1} selectionOrigin="manual" selected={{ id: 'drone-1', generation: 0 }} onSelect={vi.fn()} onClose={vi.fn()} /></I18nProvider>);
  scroll.mockClear(); resized();
  expect(scroll).toHaveBeenCalledWith({ block: 'nearest' });
  expect(scroll.mock.instances[0]).toBe(screen.getByTitle('drone-1'));
});

it('preserves a typed search through live director selections and reveals a later explicit selection', () => {
  const props = { store:world(), revision:1, selected:{ id:'task-1', generation:0 }, selectionOrigin:'director' as const, onSelect:vi.fn(), onClose:vi.fn() };
  const view = render(<I18nProvider><ObjectsPanel {...props}/></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Search objects'), { target:{ value:'task-1' } });
  scroll.mockClear();
  view.rerender(<I18nProvider><ObjectsPanel {...props} revision={2} selected={{ id:'drone-1', generation:0 }}/></I18nProvider>);
  expect(screen.getByLabelText('Search objects')).toHaveValue('task-1');
  fireEvent.click(screen.getByTitle('task-1'));
  expect(props.onSelect).toHaveBeenCalledWith({ id:'task-1', generation:0 }, false);
  resized(); expect(scroll).not.toHaveBeenCalled();
  view.rerender(<I18nProvider><ObjectsPanel {...props} revision={3} selected={{ id:'drone-1', generation:0 }} selectionOrigin="manual"/></I18nProvider>);
  expect(screen.getByLabelText('Search objects')).toHaveValue('');
  expect(screen.getByTitle('drone-1')).toHaveAttribute('aria-current','true');
  expect(scroll).toHaveBeenCalled();
});

it('does not move search results when a matching director selection or resize arrives', () => {
  const props = { store:world(), revision:1, selected:{ id:'task-1', generation:0 }, selectionOrigin:'director' as const, onSelect:vi.fn(), onClose:vi.fn() };
  const view = render(<I18nProvider><ObjectsPanel {...props}/></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Search objects'), { target:{ value:'drone' } }); scroll.mockClear();
  view.rerender(<I18nProvider><ObjectsPanel {...props} selected={{ id:'drone-1', generation:0 }}/></I18nProvider>);
  resized(); expect(scroll).not.toHaveBeenCalled(); expect(screen.getByLabelText('Search objects')).toHaveValue('drone');
  fireEvent.change(screen.getByLabelText('Search objects'), { target:{ value:'' } });
  expect(scroll).toHaveBeenCalledWith({ block:'nearest' });
});
