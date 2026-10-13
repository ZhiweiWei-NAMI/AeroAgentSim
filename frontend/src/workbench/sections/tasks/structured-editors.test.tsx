import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { OrderStreams } from './OrderStreams';
import { CooperationEditor } from './Cooperation';
import { WeatherBlock } from './Weather';

const L = (en: string, _zh: string) => en;
const edit = vi.fn();
const replaceDraft = vi.fn();
beforeEach(() => {
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  edit.mockClear(); replaceDraft.mockClear();
});
const draft = {
  run: { until_ns: 360_000_000_000 },
  entities: [
    { id: 'kitchen-1', type: 'lowalt:Restaurant', facts: { 'lowalt.site_position': [0, 0, 0] } },
    { id: 'courier-1', type: 'lowalt:UAV', facts: { 'lowalt.soc': 90 } },
  ],
};
const config = { generators: [{ id: 'parcels', kind: 'parcel', tasks: ['parcels-1'], origins: ['depot'], destinations: ['customer-1'], arrivals: { mode: 'poisson', rate_per_s: 0.08, start_ns: 0, end_ns: 180_000_000_000 }, priority: 1, payload_kg: 1 }] };

function Streams() {
  return <I18nProvider><OrderStreams config={config} draft={draft} edit={edit} L={L} /></I18nProvider>;
}

it('lists existing arrivals as structured rows and adds a stream with the compiler shape', () => {
  render(<Streams />);
  fireEvent.click(screen.getByText('parcels'));
  expect(screen.getByText('0.08/s')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Remove' })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('New order stream ID'), { target: { value: 'rush' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add order stream' }));
  expect(edit).toHaveBeenLastCalledWith('/engines/tasks/config/generators/1', expect.objectContaining({ id: 'rush', kind: 'parcel', arrivals: expect.objectContaining({ mode: 'poisson' }) }));
});

it('blocks duplicate or empty order-stream IDs instead of fabricating a group', () => {
  render(<Streams />);
  expect(screen.getByRole('button', { name: 'Add order stream' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('New order stream ID'), { target: { value: 'parcels' } });
  expect(screen.getByRole('button', { name: 'Add order stream' })).toBeDisabled();
});

it('cooperation adds with the compiler skeleton and removes cleanly', () => {
  const empty = { ...config };
  const { rerender } = render(<I18nProvider><CooperationEditor config={empty} engineName="tasks" draft={draft} edit={edit} L={L} replaceDraft={replaceDraft} /></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Add cooperation' }));
  expect(replaceDraft).toHaveBeenCalledOnce();
  const added = replaceDraft.mock.calls[0][0];
  expect(added.engines.tasks.config.cooperation.responder.mode).toBe('prescribed');
  const withCoop = { ...config, cooperation: { watchers: { vehicles: [], notice_radius_m: 150 }, responder: { mode: 'agent' }, helpers: {}, incidents: [] } };
  rerender(<I18nProvider><CooperationEditor config={withCoop} engineName="tasks" draft={draft} edit={edit} L={L} replaceDraft={replaceDraft} /></I18nProvider>);
  expect(screen.getByText(/Agent mode needs a connected model/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Remove cooperation' }));
  expect(edit).toHaveBeenCalledWith('/engines/tasks/config/cooperation', undefined);
});

it('weather lists gusts with units and adds a gust into the environment profile', () => {
  const environment = { config: { profiles: { weather: { 'lowalt.wind': { mode: 'constant', value: [2, 0, 0] } } } } };
  const { rerender } = render(<I18nProvider><WeatherBlock environmentName="weather" environment={environment} draft={draft} edit={edit} L={L} /></I18nProvider>);
  expect(screen.getByLabelText('Background speed (m/s)')).toHaveValue('2');
  fireEvent.change(screen.getByLabelText('Gust start seconds'), { target: { value: '170' } });
  fireEvent.change(screen.getByLabelText('Gust end seconds'), { target: { value: '200' } });
  fireEvent.change(screen.getByLabelText('Gust speed m/s'), { target: { value: '14' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add gust' }));
  expect(edit).toHaveBeenCalledWith('/engines/weather/config/profiles/weather/lowalt.wind', expect.objectContaining({
    mode: 'gust',
    value: [2, 0, 0],
    gusts: [expect.objectContaining({ start_ns: 170_000_000_000, end_ns: 200_000_000_000, value: [0, 14, 0] })],
  }));
  const updated = { config: { profiles: { weather: { 'lowalt.wind': edit.mock.calls[0][1] } } } };
  rerender(<I18nProvider><WeatherBlock environmentName="weather" environment={updated} draft={draft} edit={edit} L={L} /></I18nProvider>);
  expect(screen.getByText('170–200 s')).toBeTruthy();
  expect(screen.getByText('14 m/s')).toBeTruthy();
  expect(screen.getByText('90°')).toBeTruthy();
});

it('refuses an inverted gust window instead of writing it', () => {
  const environment = { config: { profiles: { weather: { 'lowalt.wind': { mode: 'constant', value: [2, 0, 0] } } } } };
  render(<I18nProvider><WeatherBlock environmentName="weather" environment={environment} draft={draft} edit={edit} L={L} /></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Gust start seconds'), { target: { value: '200' } });
  fireEvent.change(screen.getByLabelText('Gust end seconds'), { target: { value: '170' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add gust' }));
  expect(edit).not.toHaveBeenCalled();
  expect(screen.getByText('End must be after start.')).toBeTruthy();
});
