import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { AddObjects } from './AddObjects';
import { forceSvgRenderer } from '../space/leafletTestSetup';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { sessionStorage.clear(); Element.prototype.scrollIntoView = vi.fn(); forceSvgRenderer(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; cleanup(); });

const L = (en: string, _zh?: string) => en;
const catalog = { types: [{ id: 'oo:UAV', parents: [], abstract: false }], relations: [] };

function setup(detail?: Record<string, unknown>) {
  const api = {
    save: vi.fn().mockResolvedValue({ id: 'w', revision: 2, scenario: { entities: [] } }),
    request: vi.fn().mockResolvedValue({ id: 'w', revision: 3, scenario: { entities: [] } }),
  };
  const studioApi = {
    request: vi.fn().mockImplementation(async (path: string) => {
      if (path.startsWith('/v1/studio/types?')) return catalog;
      return { id: 'oo:UAV', parents: [], abstract: false, fields: [] } as never;
    }),
  };
  render(<I18nProvider><AddObjects api={api as never} studioApi={studioApi as never} project={{ id: 'w', revision: 1 }}
    draft={{ entities: [] } as never} project_detail={detail as never} onProject={vi.fn()} L={L} onClose={() => undefined} /></I18nProvider>);
  return { api };
}

const chooseUav = async () => {
  fireEvent.click(await screen.findByRole('option', { name: /^oo:UAV(?:\s|$)/ }));
  await waitFor(() => expect(screen.getByLabelText('east (m)')).toBeTruthy());
};

it('disables "Pick on map" without an applied region and explains why', async () => {
  setup();
  await chooseUav();
  const button = screen.getByRole('button', { name: 'Pick on map' });
  expect(button).toBeDisabled();
  expect(button.getAttribute('title')).toBe('Apply a region first (Scene & space › Region)');
});

it('opens the inline pick map when a region is applied', async () => {
  const detail = {
    region: { extract: 'wujiaochang', bounds: [121.5, 31.3, 121.52, 31.31], alt: 0, level_height_m: 3.5 },
    scene: { origin: { lat: 31.305, lon: 121.51, alt: 4 } },
  };
  setup(detail);
  await chooseUav();
  const button = screen.getByRole('button', { name: 'Pick on map' });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  expect(await screen.findByText('Click to place — east/north fill in automatically')).toBeTruthy();
  expect(button.getAttribute('aria-pressed')).toBe('true');
});

it('keeps creation contract unchanged when picking', async () => {
  setup();
  await chooseUav();
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '12.3' } });
  fireEvent.change(screen.getByLabelText('north (m)'), { target: { value: '34.4' } });
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'uav-1' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create 1 objects' })).toBeEnabled());
});
