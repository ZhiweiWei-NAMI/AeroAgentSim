import { act, cleanup, fireEvent, render as renderComponent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import type { RunHeader } from '../contracts/viewer-feed';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from './clock';
import { ViewportView } from './ViewportView';

// The mocked class reads this only when instantiated, after module imports.
const renderer = { instances: [] as any[] };
vi.mock('./viewport', async () => {
  const { vi: mock } = await import('vitest');
  return { Viewport: class {
    camera = { fov: 48, aspect: 1, position: { x: 0, y: 0, z: 0 } };
    setCameraMode = mock.fn(); setDusk = mock.fn(); setSelection = mock.fn(); setHighlights = mock.fn();
    setSemantic = mock.fn(); setLocale = mock.fn(); setQuality = mock.fn(); focusPosition = mock.fn();
    render = mock.fn(); dispose = mock.fn(); setDirectorTarget = mock.fn(); fitRegion = mock.fn();
    constructor(public container: HTMLElement, _header: unknown, public options: any) {
      renderer.instances.push(this);
      options.onStatus('Models loaded · x500');
    }
  } };
});

beforeEach(() => {
  window.localStorage.clear(); window.history.replaceState({}, '/', '/');
  renderer.instances.length = 0;
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.localStorage.clear(); });

const at = (ns: string) => ({ ns, microstep: 0 });
const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'city-status', registryDigest: 'test',
  start: at('0'), end: at('2000000000'), types: [], fields: [], presentation: [],
  scene: { id: 'Huangpu', city: { kind: 'traffic-city', url: '/scene.json' }, hint: 'Detailed 3D city available: run `aeroagentsim assets fetch`', attribution: '© OpenStreetMap contributors' } };
function session() {
  const store = new TemporalFeedStore(header);
  store.ingest({ commitIndex: 1, at: at('0'), created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [] });
  store.seek('0', 1);
  return { store, clock: new PlaybackClock('0', '2000000000') };
}
const handlers = () => ({ onSelect: vi.fn(), onTick: vi.fn(), onError: vi.fn(), onQuality: vi.fn(), onSeekCommit: vi.fn() });
function View({ props }: { props: any }) {
  return <I18nProvider><ViewportView {...props} /></I18nProvider>;
}
const baseProps = () => ({ ...session(), ...handlers(), simple: true, bare: true, mode: 'orbit' as const, quality: 'med' as const, trails: true });

describe('persistent city status and scene hint', () => {
  it('DOC-014: a city load failure stays visible with Retry and Change source instead of a disappearing toast', () => {
    const onChangeSource = vi.fn();
    const props = { ...baseProps(), onChangeSource };
    renderComponent(<View props={props} />);
    const instance = renderer.instances[0];
    act(() => { instance.options.onCityIssue?.('HTTP 503: scene.json unavailable'); });
    expect(screen.getByTestId('city-load-alert')).toHaveTextContent('HTTP 503: scene.json unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Change source…' }));
    expect(onChangeSource).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeVisible();
  });

  it('clears the failure once the city actually loads, so success is never fabricated', () => {
    const props = { ...baseProps() };
    renderComponent(<View props={props} />);
    const instance = renderer.instances[0];
    act(() => { instance.options.onCityIssue?.('HTTP 503: scene.json unavailable'); });
    expect(screen.getByTestId('city-load-alert')).toBeVisible();    act(() => { instance.options.onCityIssue?.(undefined); });
    expect(screen.queryByTestId('city-load-alert')).toBeNull();
  });

  it('DOC-012: shows the header scene hint as a dismissible note', () => {
    const props = { ...baseProps() };
    renderComponent(<View props={props} />);
    expect(screen.getByTestId('scene-hint')).toHaveTextContent('aeroagentsim assets fetch');
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss hint' }));
    expect(screen.queryByTestId('scene-hint')).toBeNull();
  });

  it('GEO-014: exposes the viewport fit-region framing to the owner camera pad', () => {
    const props = { ...baseProps() };
    renderComponent(<View props={props} />);
    const instance = renderer.instances[0];
    expect(typeof instance.fitRegion).toBe('function');
  });
});
