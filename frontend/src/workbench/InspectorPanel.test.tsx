import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { RunsApi } from '../feeds/http';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureCommits, fixtureHeader, vehicle } from '../behaviours/extension-fixture';
import { InspectorPanel } from './InspectorPanel';
vi.mock('../behaviours/RunOperations', () => ({ RunOperations: () => <div data-testid="operations" className="injection-card"><input aria-label="Event details" /></div> }));
const scroll = vi.fn(), savedScroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = scroll; });
afterEach(() => { cleanup(); localStorage.clear(); Element.prototype.scrollIntoView = savedScroll; vi.clearAllMocks(); vi.unstubAllGlobals(); });
function props() {
 const store = new TemporalFeedStore(fixtureHeader); fixtureCommits.forEach(row => store.ingest(row)); store.seek('10', 3);
 return { api: new RunsApi('http://api'), runId: 'run', store, revision: 1, mode: 'live' as const, canOperate: true, onSelect: vi.fn(), onSeekCut: vi.fn(), onTrace: vi.fn(), onFocus: vi.fn(), onFollow: vi.fn(), following: false, onShowGraph: vi.fn(), onClose: vi.fn() };
}
it('opens the actual operation panel from a waiting request even before choosing an object', () => {
 render(<I18nProvider><InspectorPanel {...props()} operateRequest={1} /></I18nProvider>);
 expect(screen.getByRole('tab', { name: 'Operate' })).toHaveAttribute('aria-selected', 'true');
 expect(screen.getByRole('tab', { name: 'Story' })).toBeDisabled();
 expect(screen.getByTestId('operations')).toBeInTheDocument();
 expect(screen.getByText('Scene events')).toBeInTheDocument();
 expect(scroll).toHaveBeenCalledWith({ block: 'start' });
 expect(screen.getByLabelText('Event details')).toHaveFocus();
});
it('reopens Operate for repeated requests and preserves manual tab switching between requests', () => {
 localStorage.setItem('aeroagentsim.locale', 'zh-CN'); const parameters = { ...props(), selected: vehicle };
 const view = render(<I18nProvider><InspectorPanel {...parameters} operateRequest={1} /></I18nProvider>);
 expect(screen.getByRole('tab', { name: '操作' })).toHaveAttribute('aria-selected', 'true');
 fireEvent.click(screen.getByRole('tab', { name: '状态' }));
 expect(screen.getByRole('tab', { name: '状态' })).toHaveAttribute('aria-selected', 'true');
 view.rerender(<I18nProvider><InspectorPanel {...parameters} operateRequest={2} /></I18nProvider>);
 expect(screen.getByRole('tab', { name: '操作' })).toHaveAttribute('aria-selected', 'true');
});
it('shows the object identity once, with recorded fields under All fields on the State tab', () => {
 render(<I18nProvider><InspectorPanel {...props()} selected={vehicle} /></I18nProvider>);
 expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(1);
 expect(screen.getByTestId('wb-all-fields')).not.toHaveAttribute('open');
 fireEvent.click(screen.getByText('All fields', { selector: 'summary' }));
 expect(screen.getByRole('region', { name: 'Selected entity state' })).toBeVisible();
 expect(screen.queryByText('Selected entity')).not.toBeInTheDocument();
});
it('explains that history must return to live before an event can be introduced', () => {
 render(<I18nProvider><InspectorPanel {...props()} canOperate={false} viewingHistory operateRequest={1} /></I18nProvider>);
 expect(screen.getByText('You are viewing history. Back to live before introducing an event.')).toBeVisible();
});
it('compacts the empty hint and restores the inspector for selection or scene operations', () => {
 const compact = vi.fn(), parameters = props();
 const view = render(<I18nProvider><InspectorPanel {...parameters} onCompactChange={compact} /></I18nProvider>);
 expect(screen.getByTestId('wb-inspector')).toHaveClass('is-compact'); expect(compact).toHaveBeenLastCalledWith(true);
 view.rerender(<I18nProvider><InspectorPanel {...parameters} selected={vehicle} onCompactChange={compact} /></I18nProvider>);
 expect(screen.getByTestId('wb-inspector')).not.toHaveClass('is-compact'); expect(compact).toHaveBeenLastCalledWith(false);
 view.rerender(<I18nProvider><InspectorPanel {...parameters} operateRequest={1} onCompactChange={compact} /></I18nProvider>);
 expect(screen.getByTestId('wb-inspector')).not.toHaveClass('is-compact');
});
it('offers scene operations from the compact hint without requiring an arbitrary object selection', () => {
 render(<I18nProvider><InspectorPanel {...props()} /></I18nProvider>);
 fireEvent.click(screen.getByRole('button', { name: 'Introduce an event' }));
 expect(screen.getByRole('tab', { name: 'Operate' })).toHaveAttribute('aria-selected', 'true');
 expect(screen.getByTestId('operations')).toBeVisible();
});

it('restores an independent scroll position for State, Story and Operate', () => {
 render(<I18nProvider><InspectorPanel {...props()} selected={vehicle}/></I18nProvider>);
 const body = screen.getByTestId('wb-inspector').querySelector<HTMLDivElement>('.wb-panel-body')!;
 const scrollTo = (top: number) => { body.scrollTop = top; fireEvent.scroll(body); };
 fireEvent.click(screen.getByRole('tab', { name: 'Operate' }));
 scrollTo(420);
 fireEvent.click(screen.getByRole('tab', { name: 'State' }));
 expect(body.scrollTop).toBe(0);
 scrollTo(180);
 fireEvent.click(screen.getByRole('tab', { name: 'Story' }));
 expect(body.scrollTop).toBe(0);
 scrollTo(90);
 fireEvent.click(screen.getByRole('tab', { name: 'Operate' }));
 expect(body.scrollTop).toBe(420);
 fireEvent.click(screen.getByRole('tab', { name: 'State' }));
 expect(body.scrollTop).toBe(180);
 fireEvent.click(screen.getByRole('tab', { name: 'Story' }));
 expect(body.scrollTop).toBe(90);
});

it('restores a tab after its asynchronous form grows and lets a manual scroll cancel that restoration', () => {
 let resized!: () => void;
 const disconnect = vi.fn();
 vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { resized = callback; } observe() {} disconnect() { disconnect(); } });
 render(<I18nProvider><InspectorPanel {...props()} selected={vehicle}/></I18nProvider>);
 const body = screen.getByTestId('wb-inspector').querySelector<HTMLDivElement>('.wb-panel-body')!;
 let position = 0, capacity = 600;
 Object.defineProperty(body, 'scrollTop', { configurable: true, get: () => position, set: value => { position = Math.min(value, capacity); } });
 const scrollTo = (top: number) => { body.scrollTop = top; fireEvent.scroll(body); };
 fireEvent.click(screen.getByRole('tab', { name: 'Operate' })); scrollTo(420);
 fireEvent.click(screen.getByRole('tab', { name: 'State' })); capacity = 100;
 fireEvent.click(screen.getByRole('tab', { name: 'Operate' }));
 expect(body.scrollTop).toBe(100); fireEvent.scroll(body);
 capacity = 600; act(() => resized());
 expect(body.scrollTop).toBe(420); expect(disconnect).toHaveBeenCalled();
 fireEvent.click(screen.getByRole('tab', { name: 'State' })); capacity = 100;
 fireEvent.click(screen.getByRole('tab', { name: 'Operate' }));
 fireEvent.wheel(body); scrollTo(50); capacity = 600; act(() => resized());
 expect(body.scrollTop).toBe(50);
 fireEvent.click(screen.getByRole('tab', { name: 'State' }));
 fireEvent.click(screen.getByRole('tab', { name: 'Operate' }));
 expect(body.scrollTop).toBe(50);
});
