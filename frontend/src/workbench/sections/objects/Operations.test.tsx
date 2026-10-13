import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { LowaltStart } from './LowaltStart';
import { AddPlace } from './AddPlace';
import { AddFleet } from './AddFleet';
import { OperationDiff, operationChanges, operationKey, operationText, useOperationsReview } from './Operations';
import { operation, type Operation } from '../operations';
import type { WorkbenchApi } from '../../api';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; cleanup(); });

const L = (en: string, _zh?: string) => en;
const project = { id: 'w', revision: 7 };
const draft = { entities: [{ id: 'depot', type: 'lowalt:Depot', facts: { 'lowalt.site_position': [1, 2, 0] } }] } as never;
const emptyDiff = { diff: { added: [{ kind: 'fleet', id: 'couriers-1', label: 'Parcel drone 1' }], changed: [], removed: [] }, notes: [] };

function operationsApi(overrides: Record<string, unknown> = {}) {
  return {
    request: vi.fn().mockImplementation(async (_path: string, init?: { body?: any }) => {
      if (init?.body?.dry_run) return emptyDiff;
      return { ...project, revision: 8, capabilities: { lowalt: true, region: false, network: false, city: false } };
    }),
    project: vi.fn().mockResolvedValue({ ...project, revision: 8 }),
    ...overrides,
  };
}

it('LowaltStart previews the C11 dry-run diff and disables Apply until previewed again', async () => {
  const api = operationsApi();
  const onProject = vi.fn();
  render(<I18nProvider><LowaltStart api={api as unknown as WorkbenchApi} project={project as never} draft={{}} onProject={onProject} L={L} /></I18nProvider>);
  expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull(); // nothing without a shown diff
  fireEvent.click(screen.getByRole('button', { name: /Add standard low-altitude components/ }));
  await waitFor(() => expect(api.request).toHaveBeenCalled());
  const [path, init] = (api.request as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(path).toBe('/v1/studio/workspaces/w/operations');
  expect(init.body.dry_run).toBe(true);
  expect(init.body.operations[0].op).toBe('add_lowalt_components');
  expect(await screen.findByRole('group', { name: 'Review changes' })).toBeTruthy();
  expect(await screen.findByText('Parcel drone 1')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(onProject).toHaveBeenCalled());
  expect(onProject.mock.calls[0][0].revision).toBe(8); // state comes from the server, not the form
});

it('AddPlace sends an add_place operation with role-specific fields only, after a dry run', async () => {
  const api = operationsApi();
  const onProject = vi.fn();
  render(<I18nProvider><AddPlace api={api as unknown as WorkbenchApi} project={project as never} draft={draft} onProject={onProject} L={L} onClose={() => undefined} /></I18nProvider>);
  const preview = screen.getByRole('button', { name: 'Preview' });
  expect(preview).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Charging pad' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.change(screen.getByLabelText(/^id$/), { target: { value: 'pad-1' } });
  expect(preview).toBeEnabled();
  fireEvent.change(screen.getByLabelText('charging power (W)'), { target: { value: 'abc' } });
  expect(preview).toBeDisabled(); // invalid input blocks the operation visibly
  expect(screen.getByText(/Enter a positive number/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('charging power (W)'), { target: { value: '7000' } });
  fireEvent.click(preview);
  await waitFor(() => expect(api.request).toHaveBeenCalled());
  const [, init] = (api.request as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(init.body.dry_run).toBe(true);
  expect(init.body.operations[0]).toMatchObject({ op: 'add_place', params: { id: 'pad-1', role: 'charging_pad', at: 'centre', power_w: 7000 } });
  expect(init.body.operations[0].params).not.toHaveProperty('preparation_s');
  fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(onProject).toHaveBeenCalled());
  const [applyPath, applyInit] = (api.request as ReturnType<typeof vi.fn>).mock.calls[1];
  expect(applyPath).toBe('/v1/studio/workspaces/w/operations');
  expect(applyInit.body.dry_run).toBeUndefined();
});

it('AddFleet previews a set_fleet shrink and names exactly the members to be removed', async () => {
  const api = operationsApi({ request: vi.fn().mockResolvedValue({ diff: { added: [], changed: [], removed: [
    { kind: 'entity', id: 'couriers-2', label: 'Courier 2' }, { kind: 'entity', id: 'couriers-3', label: 'Courier 3' },
  ] }, notes: [] }) });
  const onProject = vi.fn();
  const fleetDraft = { entities: [
    { id: 'couriers-1', type: 'lowalt:UAV', facts: {} },
    { id: 'couriers-2', type: 'lowalt:UAV', facts: {} },
    { id: 'couriers-3', type: 'lowalt:UAV', facts: {} },
  ] } as never;
  render(<I18nProvider><AddFleet api={api as unknown as WorkbenchApi} project={project as never} draft={fleetDraft}
    onProject={onProject} L={L} onClose={() => undefined}
    editing={{ id: 'couriers', count: 3 }} /></I18nProvider>);
  expect(screen.getByText(/Members: couriers-1, couriers-2, couriers-3/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('count (drones)'), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await waitFor(() => expect(api.request).toHaveBeenCalled());
  const [, init] = (api.request as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(init.body.operations[0]).toMatchObject({ op: 'set_fleet', params: { fleet: 'couriers', count: 1 } });
  const removed = within(await screen.findByRole('region', { name: 'Removed' }));
  expect(removed.getByText('couriers-2')).toBeTruthy();
  expect(removed.getByText('couriers-3')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Confirm resize' })).toBeEnabled();
  expect(init.body.operations[0].params).toEqual({ fleet: 'couriers', count: 1 });
});

it('keeps a preview confirmable through rerenders, transient ids and equivalent parameter key order', async () => {
  const api = operationsApi();
  const first = [{ id: 'render-1', op: 'add_place', params: { id: 'p', role: 'depot', at: [100, 0, 0] } }];
  const { result, rerender } = renderHook(({ operations, revision }) => useOperationsReview(api as unknown as WorkbenchApi, { ...project, revision } as never, operations),
    { initialProps: { operations: first, revision: 7 } });
  await act(() => result.current.load());
  rerender({ operations: [{ id: 'render-2', op: 'add_place', params: { at: [100, 0, 0], role: 'depot', id: 'p' } }], revision: 7 });
  expect(result.current.stale).toBe(false);
  let applied;
  await act(async () => { applied = await result.current.apply(); });
  expect(applied).toMatchObject({ revision: 8 });
  expect(api.request.mock.calls[1][1].body.operations).toEqual(first);
  expect(api.request.mock.calls[1][1].body.revision).toBe(7);
});

it('invalidates a preview only for operation names, params or base revision and recognises restored inputs', async () => {
  const api = operationsApi();
  const ops = [operation('set_fleet', { fleet: 'couriers', count: 3 })];
  const { result, rerender } = renderHook(({ operations, revision }) => useOperationsReview(api as unknown as WorkbenchApi, { ...project, revision } as never, operations),
    { initialProps: { operations: ops, revision: 7 } });
  await act(() => result.current.load());
  rerender({ operations: [operation('set_fleet', { fleet: 'couriers', count: 4 })], revision: 7 });
  expect(result.current.stale).toBe(true);
  await act(() => result.current.apply());
  expect(api.request).toHaveBeenCalledTimes(1);
  rerender({ operations: [operation('set_fleet', { fleet: 'couriers', count: 3 })], revision: 7 });
  expect(result.current.stale).toBe(false);
  rerender({ operations: ops, revision: 8 });
  expect(result.current.stale).toBe(true);
  rerender({ operations: [operation('set_vehicle', { fleet: 'couriers', count: 3 })], revision: 7 });
  expect(result.current.stale).toBe(true);
});

it('makes an in-flight preview stale when real inputs change while the request is pending', async () => {
  let respond!: (value: typeof emptyDiff) => void;
  const api = operationsApi({ request: vi.fn(() => new Promise(resolve => { respond = resolve; })) });
  const { result, rerender } = renderHook(({ operations }: { operations: Operation[] }) => useOperationsReview(api as unknown as WorkbenchApi, project as never, operations),
    { initialProps: { operations: [operation('set_fleet', { fleet: 'couriers', count: 3 })] } });
  let pending!: Promise<void>;
  act(() => { pending = result.current.load(); });
  rerender({ operations: [operation('set_fleet', { fleet: 'couriers', count: 4 })] });
  await act(async () => { respond(emptyDiff); await pending; });
  expect(result.current.stale).toBe(true);
});

it('groups every review by Added/Changed/Removed counts, with kind, id and active-locale label (D4)', () => {
  const L = (_en: string, zh: string) => zh;
  render(<OperationDiff L={L} diff={{ added: [{ kind: 'place', id: 'p', label: 'Charging pad', label_zh: '充电坪' }],
    changed: [{ kind: 'fleet', id: 'f', label: 'Couriers', label_zh: '送餐机群', fields: [{ name: 'count', before: 2, after: 3 }] }],
    removed: [{ kind: 'entity', id: 'old', label: 'Old object', label_zh: '旧对象' }] }} notes={[{ en: 'Review before applying', zh: '查看后再应用' }, 'Kitchen ready / 厨房已就绪']} />);
  for (const name of ['新增', '修改', '移除']) expect(within(screen.getByRole('region', { name })).getByText('1')).toBeTruthy();
  expect(within(screen.getByRole('region', { name: '新增' })).getByText('p')).toBeTruthy();
  expect(screen.getByText('充电坪')).toBeTruthy();
  expect(screen.queryByText('Charging pad')).toBeNull();
  expect(screen.getByText('查看后再应用')).toBeTruthy();
  expect(screen.getByText('厨房已就绪')).toBeTruthy();
  expect(screen.queryByText(/Kitchen ready/)).toBeNull();
  expect(screen.getByText('count: 2 → 3')).toBeTruthy();
});

it('does not split coordinate-unit prose when choosing a bilingual backend message', () => {
  expect(operationText('Speed m / s', L)).toBe('Speed m / s');
  expect(operationText('The draft changed. / 草稿已变化。', (_en, zh) => zh)).toBe('草稿已变化。');
  expect(operationKey(project as never, [operation('set_fleet', { count: 3, fleet: 'c' })]))
    .toBe(operationKey({ ...project } as never, [operation('set_fleet', { fleet: 'c', count: 3 })]));
});

it('includes the exact removed members from C11 entity deltas and member-field changes (D4)', () => {
  const diff = { added: [], removed: [], changed: [{ kind: 'fleet', id: 'c', fields: [{ name: 'members', before: ['c-1', 'c-2', 'c-3'], after: ['c-1'] }] }],
    entities: { added: [], changed: [], removed: ['c-2', 'c-3'] } };
  const changes = operationChanges(diff, [{ id: 'c-2', label: 'Courier 2', label_zh: '送餐机 2' }]);
  expect(changes.removed).toEqual([{ kind: 'entity', id: 'c-2', label: 'Courier 2', label_zh: '送餐机 2' }, { kind: 'entity', id: 'c-3', label: undefined, label_zh: undefined }]);
  render(<OperationDiff diff={diff} L={L} />);
  expect(within(screen.getByRole('region', { name: 'Removed' })).getByText('2')).toBeTruthy();
});
