import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { RemoveEntity } from './RemoveEntity';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; });

const L = (en: string, _zh?: string) => en;

function setup(studioRequest: ReturnType<typeof vi.fn>, apiOverrides: Record<string, unknown> = {}) {
  const api = {
    save: vi.fn().mockResolvedValue({ id: 'w', revision: 2, scenario: { entities: [{ id: 'uav-1', type: 'oo:UAV', facts: {} }] } }),
    ...apiOverrides,
  };
  const onProject = vi.fn();
  const props = { api, studioApi: { request: studioRequest }, project: { id: 'w', revision: 1 }, draft: { entities: [{ id: 'uav-1', type: 'oo:UAV', facts: {} }] }, onProject, L, entityId: 'uav-1', onDone: () => undefined };
  const view = render(<I18nProvider><RemoveEntity api={api as never} studioApi={{ request: studioRequest } as never}
    project={{ id: 'w', revision: 1 }} draft={{ entities: [{ id: 'uav-1', type: 'oo:UAV', facts: {} }] } as never}
    onProject={onProject} L={L} entityId="uav-1" onDone={() => undefined} /></I18nProvider>);
  return { api, onProject, props, ...view };
}

it('previews dependencies grouped by kind before any removal', async () => {
  const request = vi.fn().mockResolvedValue({ dependencies: [
    { path: '/bindings/exact/0', kind: 'binding', detail: 'position' },
    { path: '/bindings/exact/1', kind: 'binding', detail: 'energy' },
    { path: '/presentation/0', kind: 'presentation', detail: 'marker' },
  ] });
  setup(request);
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  expect(await screen.findByText(/also removes 3 reference/)).toBeTruthy();
  expect(screen.getAllByText('Binding')).toHaveLength(2);
  expect(screen.getByText('Visual binding')).toBeTruthy();
  expect((request.mock.calls[0][1] as Record<string, unknown>).dry_run).toBe(true);
});

it('explains rather than deleting locally when the server lacks removal', async () => {
  const request = vi.fn().mockRejectedValue(Error('HTTP 404: not found'));
  const { onProject } = setup(request);
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  expect(await screen.findByText(/does not offer removal yet/)).toBeTruthy();
  expect(onProject).not.toHaveBeenCalledWith(expect.objectContaining({ scenario: { entities: [] } }));
});

it('removes atomically on confirmation', async () => {
  const updated = { id: 'w', revision: 4, scenario: { entities: [] } };
  const request = vi.fn()
    .mockResolvedValueOnce({ dependencies: [{ path: '/bindings/exact/0', kind: 'binding', detail: 'position' }] })
    .mockResolvedValueOnce(updated);
  const { api, onProject } = setup(request);
  api.save.mockResolvedValueOnce({ id: 'w', revision: 2, scenario: { entities: [{ id: 'uav-1', type: 'oo:UAV', facts: {} }] } })
    .mockResolvedValueOnce({ id: 'w', revision: 3, scenario: { entities: [{ id: 'uav-1', type: 'oo:UAV', facts: {} }] } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm removal' }));
  await waitFor(() => expect(onProject).toHaveBeenLastCalledWith(updated));
  expect(api.save).toHaveBeenCalledTimes(1);
  expect(request.mock.calls).toEqual([
    ['/v1/studio/workspaces/w/remove', { id: 'uav-1', revision: 2, dry_run: true }],
    ['/v1/studio/workspaces/w/remove', { id: 'uav-1', revision: 2 }],
  ]);
  expect(api.save.mock.invocationCallOrder[0]).toBeLessThan(request.mock.invocationCallOrder[0]);
  expect(screen.getByText(/Removed uav-1/)).toBeTruthy();
});

it('blocks confirmation if the dependency review does not include the required list', async () => {
  const request = vi.fn().mockResolvedValue({});
  setup(request);
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('did not include a dependency list');
  expect(screen.queryByRole('button', { name: 'Confirm removal' })).toBeNull();
  expect(request).toHaveBeenCalledTimes(1);
});

it('invalidates removal when real draft inputs change after the review', async () => {
  const request = vi.fn().mockResolvedValue({ dependencies: [] });
  const { rerender, props } = setup(request);
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  await screen.findByRole('button', { name: 'Confirm removal' });
  rerender(<I18nProvider><RemoveEntity {...props as unknown as ComponentProps<typeof RemoveEntity>} draft={{ entities: [{ id: 'uav-1', type: 'oo:UAV', facts: { 'aas.studio.position_enu_m': [1, 2, 3] } }] }} /></I18nProvider>);
  expect(screen.getByRole('button', { name: 'Confirm removal' })).toBeDisabled();
  expect(screen.getByText(/Inputs or draft changed/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm removal' }));
  expect(request).toHaveBeenCalledTimes(1);
});

it('removes lowalt places through C11 after showing the same shared review', async () => {
  const updated = { id: 'w', revision: 2, scenario: { entities: [] } };
  const api = { request: vi.fn().mockResolvedValue({ diff: { added: [], changed: [], removed: [{ kind: 'place', id: 'pad', label: 'Pad' }] }, notes: [] }), project: vi.fn().mockResolvedValue(updated) };
  const studioApi = { request: vi.fn() }, onProject = vi.fn();
  render(<RemoveEntity api={api as never} studioApi={studioApi as never} project={{ id: 'w', revision: 1 }}
    draft={{ entities: [{ id: 'pad', type: 'lowalt:ChargingPad', facts: { 'lowalt.site_position': [1, 2, 3] } }] }} onProject={onProject} L={L} entityId="pad" onDone={() => undefined} />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview removal' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm removal' }));
  await waitFor(() => expect(onProject).toHaveBeenCalledWith(updated));
  expect(api.request.mock.calls[0][1].body.operations[0]).toMatchObject({ op: 'remove_place', params: { id: 'pad' } });
  expect(studioApi.request).not.toHaveBeenCalled();
});
