import { act, cleanup, fireEvent, render as renderComponent, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from './clock';
import { ViewportView } from './ViewportView';

// The mocked class reads this only when instantiated, after module imports.
const renderer = { instances: [] as any[] };
vi.mock('./viewport', async () => {
  const { vi: mock } = await import('vitest');
  return { Viewport: class {
    camera = { fov: 48, aspect: 1, position: { x: 0, y: 0, z: 0 } };
    setCameraMode = mock.fn(); setDusk = mock.fn(); setLightingOverride = mock.fn(); setSelection = mock.fn(); setHighlights = mock.fn();
    onConditions = mock.fn(() => () => {});
    setSemantic = mock.fn(); setLocale = mock.fn(); setQuality = mock.fn(); focusPosition = mock.fn();
    render = mock.fn(); dispose = mock.fn(); setDirectorTarget = mock.fn();
    constructor(public container: HTMLElement, _header: unknown, public options: any) {
      renderer.instances.push(this);
      options.onStatus('Models loaded · x500');
    }
  } };
});

let nextFrame: FrameRequestCallback;
beforeEach(() => {
  window.localStorage.clear(); window.history.replaceState({}, '', '/'); renderer.instances.length = 0;
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { nextFrame = callback; return 1; }));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.localStorage.clear(); });

const key = { id: 'unit-1', generation: 0 };
const at = (ns: string, microstep = 0) => ({ ns, microstep });
const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'viewport-controls', registryDigest: 'test',
  start: at('0'), end: at('2000000000'), types: [{ typeId: 'Unit', displayName: 'Unit', ancestors: [] }], fields: [], presentation: [] };
function session() {
  const store = new TemporalFeedStore(header);
  const commit = (index: number, ns: string, changes: Partial<FeedCommit> = {}): FeedCommit => ({ commitIndex: index, at: at(ns, index),
    created: [], removed: [], facts: [], retracted: [], edges: [], messages: [], receipts: [], ...changes });
  const fact = (value: string, ns: string) => ({ entity: key, fieldId: 'state', value, producer: 'engine', validFrom: at(ns) });
  store.ingest(commit(1, '0', { created: [{ ...key, typeId: 'Unit' }], facts: [fact('waiting', '0')] }));
  store.ingest(commit(2, '1000000000', { facts: [fact('running', '1000000000')], messages: [{ id: 'event-1', kind: 'event', schemaId: 'unit.started', source: 'engine', at: at('1000000000'), subjects: [key], payload: {} }] }));
  store.ingest(commit(3, '1000000000', { facts: [fact('after-event', '1000000000')] }));
  store.seek('0', 1);
  return { store, clock: new PlaybackClock('0', '2000000000') };
}
const handlers = () => ({ onSelect: vi.fn(), onTick: vi.fn(), onError: vi.fn(), onQuality: vi.fn(), onSeekCommit: vi.fn() });
function LocaleButton() { const { locale, setLocale } = useI18n(); return <button onClick={() => setLocale(locale === 'en-US' ? 'zh-CN' : 'en-US')}>locale</button>; }
function View({ props }: { props: ComponentProps<typeof ViewportView> }) {
  return <I18nProvider><LocaleButton /><main className="viewer-main"><section><ViewportView {...props} /></section><aside className="viewer-sidebar">sidebar</aside></main></I18nProvider>;
}

describe('viewport bilingual controls', () => {
  it('keeps a manual follow choice when the event director pauses, and reports an orbit gesture to the owner', () => {
    const { store, clock } = session();
    const props = { store, clock, ...handlers(), simple: true, mode: 'orbit' as const, quality: 'med' as const, trails: true,
      director: { enabled: true, active: true, paused: false, events: [] }, onCameraMode: vi.fn() };
    const view = renderComponent(<View props={props} />), instance = renderer.instances[0], now = performance.now();
    act(() => nextFrame(now));
    expect(instance.setCameraMode).toHaveBeenLastCalledWith('cinematic');
    view.rerender(<View props={{ ...props, mode: 'follow', director: { ...props.director, paused: true } }} />);
    act(() => nextFrame(now + 100));
    expect(instance.setCameraMode).toHaveBeenLastCalledWith('follow');
    act(() => instance.options.onCameraInteraction());
    expect(props.onCameraMode).toHaveBeenCalledWith('orbit');
  });
  it('routes notable cues to smooth Director targets and hands control to camera interaction until resumed',()=>{
    const {store,clock}=session();
    const cue={key:'2/0',cut:2,offset:1,at_ns:'1000000000',category:'energy' as const,severity:'notice' as const,text:'Low battery',entities:[],position:[1,2,3] as [number,number,number]};
    clock.seek('1000000000');
    const config={enabled:true,active:true,paused:false,events:[cue]};
    const props={store,clock,...handlers(),simple:true,mode:'orbit' as const,quality:'med' as const,trails:true,director:config,onDirectorPause:vi.fn()};
    const view=renderComponent(<View props={props}/>),instance=renderer.instances[0],now=performance.now()+200;
    act(()=>nextFrame(now));expect(instance.setDirectorTarget).toHaveBeenLastCalledWith({eventKey:cue.key,entity:undefined,position:[1,2,3]});expect(instance.setCameraMode).toHaveBeenLastCalledWith('cinematic');
    act(()=>instance.options.onCameraInteraction());expect(props.onDirectorPause).toHaveBeenCalledOnce();act(()=>nextFrame(now+100));expect(instance.setDirectorTarget).toHaveBeenLastCalledWith(undefined);expect(instance.setCameraMode).toHaveBeenLastCalledWith('orbit');
    view.rerender(<View props={{...props,director:{...config,paused:true}}}/>);act(()=>nextFrame(now+200));
    view.rerender(<View props={props}/>);act(()=>nextFrame(now+4000));expect(instance.setDirectorTarget).toHaveBeenLastCalledWith(expect.objectContaining({eventKey:cue.key}));expect(instance.setCameraMode).toHaveBeenLastCalledWith('cinematic');expect(props.onError).not.toHaveBeenCalled();
  });
  it('localizes camera, quality, lighting and aria labels reactively without recreating the renderer', () => {
    const props = { ...session(), ...handlers(), mode: 'orbit' as const, quality: 'med' as const, trails: true };
    const view = renderComponent(<View props={props} />), instance = renderer.instances[0];
    expect(screen.getByLabelText('Simulation viewport')).toBeInTheDocument();
    expect(screen.queryByText('WebGL2')).not.toBeInTheDocument();
    expect(screen.getByText('Models loaded · x500').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('Buildings, lighting and symbols are display effects.')).toBeVisible();
    fireEvent.change(screen.getByLabelText('View camera'), { target: { value: 'chase' } });
    expect(instance.setCameraMode).toHaveBeenLastCalledWith('chase');
    fireEvent.change(screen.getByLabelText('View quality'), { target: { value: 'high' } });
    expect(instance.setQuality).toHaveBeenLastCalledWith('high', true);
    fireEvent.change(screen.getByLabelText('Display lighting'), { target: { value: 'dusk' } });
    expect(instance.setLightingOverride).toHaveBeenLastCalledWith('dusk');
    fireEvent.click(screen.getByRole('button', { name: 'locale' }));
    expect(within(screen.getByLabelText('显示光照')).getByRole('option', { name: '场景' })).toHaveValue('scene');
    expect(renderer.instances).toHaveLength(1);
    expect(instance.setLocale).toHaveBeenLastCalledWith('zh-CN');
    const camera = screen.getByLabelText('视图镜头');
    expect(camera).toHaveValue('chase');
    expect(within(camera).getByRole('option', { name: '事件导播' })).toHaveValue('cinematic');
    expect(within(screen.getByLabelText('视图画质')).getByRole('option', { name: '均衡' })).toHaveValue('med');
    expect(within(screen.getByLabelText('显示光照')).getByRole('option', { name: '黄昏' })).toHaveValue('dusk');
    expect(instance.onConditions).toHaveBeenCalled();
    const toggle = screen.getByLabelText('展开或收起检查器');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(view.container.querySelector('.viewer-main')).toHaveAttribute('data-inspector-collapsed', 'true');
    const main = view.container.querySelector('.viewer-main')!;
    view.unmount();
    expect(instance.dispose).toHaveBeenCalledOnce();
    expect(main).not.toHaveAttribute('data-inspector-collapsed');
  });

  it('respects an external camera change after a local override and synchronizes quality', () => {
    window.history.replaceState({}, '', '/?camera=cinematic');
    const props = { ...session(), ...handlers(), mode: 'orbit' as const, quality: 'med' as const, trails: false };
    const view = renderComponent(<View props={props} />), instance = renderer.instances[0];
    expect(screen.getByLabelText('View camera')).toHaveValue('cinematic');
    fireEvent.change(screen.getByLabelText('View camera'), { target: { value: 'chase' } });
    view.rerender(<View props={{ ...props, mode: 'follow', quality: 'low' }} />);
    expect(screen.getByLabelText('View camera')).toHaveValue('follow');
    expect(instance.setCameraMode).toHaveBeenLastCalledWith('follow');
    expect(screen.getByLabelText('View quality')).toHaveValue('low');
    expect(instance.setQuality).toHaveBeenLastCalledWith('low');
  });

  it('seeks event markers to their exact commit, preserving same-nanosecond cuts and string generations', () => {
    const props = { ...session(), ...handlers(), selected: { ...key, generation: '0' }, commitCut: 1, mode: 'orbit' as const, quality: 'med' as const, trails: false };
    renderComponent(<View props={props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Seek unit.started' }));
    expect(props.clock.ns).toBe('1000000000');
    expect(props.clock.playing).toBe(false);
    expect(props.store.entities.get('unit-1:0')!.fields.get('state')!.value).toBe('running');
    expect(props.onSeekCommit).toHaveBeenCalledWith('1000000000', 2);
    act(() => { nextFrame(performance.now() + 200); });
    expect(props.store.entities.get('unit-1:0')!.fields.get('state')!.value).toBe('running');
    props.clock.play();
    act(() => { nextFrame(performance.now() + 400); });
    expect(props.store.entities.get('unit-1:0')!.fields.get('state')!.value).toBe('after-event');
    expect(props.onError).not.toHaveBeenCalled();
  });

  it('localizes simple-mode legends/counts and forwards the show-all control', () => {
    window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
    const props = { ...session(), ...handlers(), simple: true, mode: 'orbit' as const, quality: 'med' as const, trails: true };
    renderComponent(<View props={props} />);
    expect(screen.getByLabelText('地图图例')).toHaveTextContent('空域与运行');
    expect(screen.getByText('加载场景数据后显示图例')).toBeInTheDocument();
    expect(screen.getByTestId('semantic-zone-count')).toHaveTextContent('0 个有效空域');
    fireEvent.click(screen.getByLabelText('显示所有标签'));
    expect(renderer.instances[0].setSemantic).toHaveBeenLastCalledWith({ semantic: true, showAllLabels: true });
    act(() => { renderer.instances[0].options.onLegend([{ label: '禁飞区', color: '#e4572e' }], 1); });
    expect(screen.getByText('禁飞区')).toBeInTheDocument();
    expect(screen.getByTestId('semantic-zone-count')).toHaveTextContent('1 个有效空域');
  });
});
