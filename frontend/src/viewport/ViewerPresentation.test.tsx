import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ViewerPresentation } from './ViewerPresentation';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from './clock';
import type { RunHeader } from '../contracts/viewer-feed';

vi.mock('./ViewportView', () => ({ ViewportView: () => <div data-testid="spatial-renderer" /> }));
vi.mock('./NonspatialViews', () => ({ NonspatialViews: ({ view }: { view: string }) => <div data-testid="exact-panel">{view}</div> }));
vi.mock('./MissionTimeline', () => ({ MissionTimeline: () => <div>Recorded timeline</div> }));
afterEach(() => { vi.unstubAllGlobals(); });

function session(spatial: boolean) {
  const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'presentation-test', registryDigest: 'test',
    start: { ns: '0', microstep: 0 }, end: { ns: '2000000000', microstep: 0 },
    types: [{ typeId: 'Base', displayName: 'Base', ancestors: [] }, { typeId: 'Job', displayName: 'Job', ancestors: [] }], fields: [],
    presentation: spatial ? [{ typeId: 'Base', positionField: 'position', frame: 'enu', visual: { kind: 'marker' } }] : [] };
  const store = new TemporalFeedStore(header), key = { id: 'job', generation: 0 };
  store.ingest({ commitIndex: 1, at: { ns: '0', microstep: 0 }, created: [{ ...key, typeId: 'Job' },
    ...(spatial ? [{ id: 'object', generation: 0, typeId: 'Base' }] : [])], removed: [], retracted: [], edges: [], messages: [], receipts: [],
    facts: [{ entity: key, fieldId: 'status', value: 'waiting', producer: 'recorded', validFrom: { ns: '0', microstep: 0 } }] });
  store.ingest({ commitIndex: 2, at: { ns: '1000000000', microstep: 0 }, created: [], removed: [], retracted: [], edges: [], messages: [], receipts: [],
    facts: [{ entity: key, fieldId: 'status', value: 'running', producer: 'recorded', validFrom: { ns: '1000000000', microstep: 0 } }] });
  store.seek('0');
  return { store, clock: new PlaybackClock('0', '2000000000'), key };
}
const handlers = () => ({ onSelect: vi.fn(), onTick: vi.fn(), onError: vi.fn(), onQuality: vi.fn() });

describe('viewer presentation routing', () => {
  it('starts a nonspatial run in the graph and keeps its playback cursor advancing', () => {
    let nextFrame: FrameRequestCallback | undefined;
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { nextFrame = callback; return 1; }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    const { store, clock, key } = session(false), callbacks = handlers();
    render(<ViewerPresentation store={store} clock={clock} selected={key} mode="orbit" quality="med" trails={false} {...callbacks} />);
    expect(screen.queryByTestId('spatial-renderer')).not.toBeInTheDocument();
    expect(screen.getByTestId('exact-panel')).toHaveTextContent('graph');
    clock.play();
    act(() => { nextFrame!(1000); });
    act(() => { nextFrame!(2000); });
    expect(store.entities.get('job:0')?.fields.get('status')?.value).toBe('running');
    expect(callbacks.onTick).toHaveBeenCalled();
    expect(callbacks.onError).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Queue / state' }));
    expect(screen.getByTestId('exact-panel')).toHaveTextContent('state');
  });

  it('retains the spatial renderer while either exact panel is open on a mixed run', () => {
    const { store, clock, key } = session(true);
    render(<ViewerPresentation store={store} clock={clock} selected={key} mode="orbit" quality="med" trails={true} {...handlers()} />);
    expect(screen.getByTestId('spatial-renderer')).toBeInTheDocument();
    expect(screen.queryByTestId('exact-panel')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Relations / topology' }));
    expect(screen.getByTestId('exact-panel')).toHaveTextContent('graph');
    expect(screen.getByTestId('spatial-renderer')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Queue / state' }));
    expect(screen.getByTestId('exact-panel')).toHaveTextContent('state');
    fireEvent.click(screen.getByRole('button', { name: '3D only' }));
    expect(screen.queryByTestId('exact-panel')).not.toBeInTheDocument();
  });
});
