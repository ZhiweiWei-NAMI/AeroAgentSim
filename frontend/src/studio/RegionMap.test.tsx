import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../i18n/I18nProvider';
import { RegionMap } from './RegionMap';

beforeEach(() => {
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  vi.stubGlobal('PointerEvent', MouseEvent);
});
afterEach(() => { vi.unstubAllGlobals(); });
const view = (onChange: (bounds: number[]) => void, bounds = [2, 2, 8, 8]) => <I18nProvider><RegionMap source={[0, 0, 10, 10]} bounds={bounds} onChange={onChange} /></I18nProvider>;

it('provides bounded keyboard movement, resize and whole-extract selection', () => {
  const change = vi.fn();
  render(view(change));
  const map = screen.getByRole('img', { name: 'Region selector map' });
  expect(map).toHaveAttribute('tabindex', '0');
  fireEvent.keyDown(map, { key: 'ArrowRight' });
  expect(change).toHaveBeenLastCalledWith([2.1, 2, 8.1, 8]);
  fireEvent.keyDown(map, { key: 'ArrowUp', shiftKey: true });
  expect(change).toHaveBeenLastCalledWith([2, 2, 8, 8.1]);
  fireEvent.keyDown(map, { key: 'Home' });
  expect(change).toHaveBeenLastCalledWith([0, 0, 10, 10]);
});

it('cancels a pointer drag instead of leaving a stale anchor for the next pointer-up', () => {
  const change = vi.fn();
  render(view(change));
  const map = screen.getByRole('img', { name: 'Region selector map' });
  vi.spyOn(map, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 600, bottom: 220, width: 600, height: 220, toJSON: () => ({}) });
  fireEvent.pointerDown(map, { button: 0, clientX: 60, clientY: 22 });
  fireEvent.pointerCancel(map);
  fireEvent.pointerUp(map, { clientX: 300, clientY: 110 });
  expect(change).not.toHaveBeenCalled();
  fireEvent.pointerDown(map, { button: 0, clientX: 60, clientY: 22 });
  fireEvent.pointerUp(map, { clientX: 300, clientY: 110 });
  expect(change).toHaveBeenLastCalledWith([1, 5, 5, 9]);
});

it('reports invalid bounds without emitting invalid geometry or changing the draft', () => {
  const change = vi.fn();
  render(view(change, [2, 2, 2, 8]));
  expect(screen.getByRole('alert')).toHaveTextContent('The region map needs valid west, south, east and north bounds.');
  expect(screen.queryByRole('img')).toBeNull();
  expect(change).not.toHaveBeenCalled();
});
