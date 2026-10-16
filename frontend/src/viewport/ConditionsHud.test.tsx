import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { CALM_WIND_MPS, ConditionsHud } from './ConditionsHud';
import type { Conditions } from './conditions';

afterEach(cleanup);

const conditions = (override: Partial<Conditions> = {}): Conditions => ({
  wind: { east: -6, north: 0, speed: 6.2, fromDeg: 315, gust: false },
  precipitationMmH: 4, visibilityM: 500, light: 'day',
  incidents: [{ id: 'fire-1', kind: 'fire', label: 'Building fire', position: [0, 0, 0], radiusM: 30, phase: 'active', sinceS: 135, activeS: 135 }],
  ...override,
});

it('renders the wind card, gust line, intensity bar and active-condition chips in run mode', () => {
  render(<ConditionsHud conditions={conditions({ wind: { east: -6, north: 0, speed: 6.2, fromDeg: 315, gust: true, gustEndS: 90 } })} zh={false} mode="run" />);
  expect(screen.getByTestId('wb-conditions-wind')).toHaveTextContent('Wind 6.2 m/s · from NW');
  expect(screen.getByText('Gust until 01:30')).toBeTruthy();
  expect(screen.getByRole('img', { name: 'Wind intensity' }).querySelectorAll('i.on')).toHaveLength(3);
  expect(screen.getByText('Fire active')).toBeTruthy();
  expect(screen.getByText('Moderate rain · 4 mm/h')).toBeTruthy();
  expect(screen.getByText('Fog · 500 m')).toBeTruthy();
  expect(screen.queryByText(/Preview at/)).toBeNull();
  const needle = document.querySelector('.wb-cond-dial-needle') as SVGLineElement;
  expect(needle.style.transform).toBe('rotate(135deg)');
});

it('uses the Chinese compass wind line, mist copy and the plural fire copy', () => {
  render(<ConditionsHud conditions={conditions({
    visibilityM: 2000,
    incidents: [
      { id: 'fire-1', kind: 'fire', label: 'F1', position: [0, 0, 0], radiusM: 30, phase: 'active', sinceS: 10, activeS: 10 },
      { id: 'fire-2', kind: 'fire', label: 'F2', position: [5, 5, 0], radiusM: 30, phase: 'active', sinceS: 10, activeS: 10 },
    ],
  })} zh mode="run" />);
  expect(screen.getByTestId('wb-conditions-wind')).toHaveTextContent('西北风 6.2 m/s');
  expect(screen.getByText('2 处火情')).toBeTruthy();
  expect(screen.getByText('中雨 · 4 mm/h')).toBeTruthy();
  expect(screen.getByText('轻雾 · 2 km')).toBeTruthy();
});

it('shows the preview chip first and keeps the note behind the (i) toggle', () => {
  render(<ConditionsHud conditions={conditions()} zh={false} mode="preview" atS={110} />);
  const chips = [...screen.getByTestId('wb-conditions-hud').querySelectorAll('.wb-cond-chip')];
  expect(chips[0]).toHaveTextContent('Preview at 01:50');
  expect(screen.queryByText(/visual only/i)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'About the visual-only note' }));
  expect(screen.getByText(/Wind is simulated\./)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'About the visual-only note' })).toHaveAttribute('aria-expanded', 'true');
});

it('localises the Chinese copy and hides the wind card without a wind entity', () => {
  render(<ConditionsHud conditions={conditions({ wind: null, light: 'day', incidents: [], precipitationMmH: 0, visibilityM: null })} zh mode="run" />);
  expect(screen.queryByTestId('wb-conditions-wind')).toBeNull();
  expect(screen.getByTestId('wb-conditions-hud').querySelector('.wb-cond-chips')).toBeNull();
  render(<ConditionsHud conditions={conditions({ wind: null, light: 'dusk', incidents: [], precipitationMmH: 0 })} zh mode="run" />);
  expect(screen.getByText('黄昏')).toBeTruthy();
});

it('reads calm below the threshold: Calm/静风, no from-direction, no gust and a neutral dial dot', () => {
  const calm = { east: 0.1, north: 0.1, speed: CALM_WIND_MPS - 0.1, fromDeg: 315, gust: true, gustEndS: 90 };
  const { unmount } = render(<ConditionsHud conditions={conditions({ wind: calm })} zh={false} mode="run" />);
  const wind = screen.getByTestId('wb-conditions-wind');
  expect(wind).toHaveTextContent('Calm');
  expect(wind).not.toHaveTextContent('from');
  expect(wind.querySelector('.wb-cond-gust')).toBeNull();
  expect(wind.querySelector('.wb-cond-bar')).toBeNull();
  // The dial stays, but as a neutral dot instead of a needle.
  expect(wind.querySelector('.wb-cond-dial')).toBeTruthy();
  expect(wind.querySelector('.wb-cond-dial-dot')).toBeTruthy();
  expect(wind.querySelector('.wb-cond-dial-needle')).toBeNull();
  unmount();
  render(<ConditionsHud conditions={conditions({ wind: calm })} zh mode="run" />);
  const zhWind = screen.getByTestId('wb-conditions-wind');
  expect(zhWind).toHaveTextContent('静风');
  // No compass name and no speed: the Chinese line would otherwise read “西北风 0.2 m/s”.
  expect(zhWind).not.toHaveTextContent('西北风');
  expect(zhWind).not.toHaveTextContent('m/s');
  expect(zhWind.querySelector('.wb-cond-dial-needle')).toBeNull();
});

it('keeps the directional wind line and gust at or above the calm threshold', () => {
  render(<ConditionsHud conditions={conditions({ wind: { east: -0.2, north: 0.2, speed: CALM_WIND_MPS, fromDeg: 315, gust: false } })} zh={false} mode="run" />);
  const wind = screen.getByTestId('wb-conditions-wind');
  expect(wind).toHaveTextContent('Wind 0.3 m/s · from NW');
  expect(wind.querySelector('.wb-cond-dial-needle')).toBeTruthy();
  expect(wind.querySelector('.wb-cond-dial-dot')).toBeNull();
});
