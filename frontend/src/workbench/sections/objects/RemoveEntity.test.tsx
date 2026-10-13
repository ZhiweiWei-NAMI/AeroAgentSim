import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
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
  render(<I18nProvider><RemoveEntity api={api as never} studioApi={{ request: studioRequest } as never}
    project={{ id: 'w', revision: 1 }} draft={{ entities: [{ id: 'uav-1', type: 'oo:UAV', facts: {} }] } as never}
    onProject={onProject} L={L} entityId="uav-1" onDone={() => undefined} /></I18nProvider>);
  return { api, onProject };
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
  expect(screen.getByText('Field binding')).toBeTruthy();
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
  const request = vi.fn()
    .mockResolvedValueOnce({ dependencies: [{ path: '/bindings/exact/0', kind: 'binding', detail: 'position' }] })
    .mockResolvedValueOnce({ id: 'w', revision: 3, scenario: { entities: [] } });
  const { onProject } = setup(request);
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Confirm removal' }));
  await waitFor(() => expect(onProject).toHaveBeenCalledTimes(2));
  expect(screen.getByText(/Removed uav-1/)).toBeTruthy();
});
