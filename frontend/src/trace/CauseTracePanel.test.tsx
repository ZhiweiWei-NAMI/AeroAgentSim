import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { InspectionPanel } from '../pages/InspectionPanel';
import { CauseTracePanel } from './CauseTracePanel';
import { subscribeCauseTrace, openCauseTrace } from './bus';
import { explain } from './cause-trace';
import { duplicateEventTraceStore, incident, sameCutContextStore, traceStore } from './trace-fixture';
import { I18nProvider } from '../i18n/I18nProvider';

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

it('tells a selected step’s story and lets each recorded follow-up continue the walk', () => {
  const store = traceStore(), open = vi.fn();
  const model = explain(store, { kind: 'step', instanceId: 'chain:a', revision: 1 })!;
  render(<CauseTracePanel store={store} model={model} canGoBack={false} onOpen={open} onBack={() => {}} onClose={() => {}} onSelectEntity={() => {}} onSeek={() => {}} />);
  for (const heading of ['What set it off', 'What ran', 'What changed in the shared state', 'Recorded follow-ups']) expect(screen.getByText(heading)).toBeTruthy();
  expect(screen.getByText("Car 1's speed = 0")).toBeTruthy();
  expect(screen.getAllByText(/Only event chains may write this field/)).toHaveLength(1);
  expect(screen.getByText('false')).toBeTruthy(); expect(screen.getByText('true')).toBeTruthy();
  fireEvent.click(screen.getByText(/Demo Watch: Wait → Seen/));
  expect(open).toHaveBeenCalledWith({ kind: 'step', instanceId: 'chain:b', revision: '1' });
  expect(screen.queryByText(/receipt|invocation/i)).toBeNull();
  expect(screen.getByText('Involved objects:')).toBeTruthy();
  // Payload/message matches are shown as recorded links, not as causes.
  expect(screen.getAllByText(/recorded link/).length).toBeGreaterThan(0);
  expect(screen.queryByText(/same-cut context/)).toBeNull();
});

it('labels same-source facts and matched records as context rather than causation', () => {
  const store = traceStore(), open = vi.fn();
  const model = explain(store, { kind: 'message', messageId: 'm1', cut: 4 })!;
  render(<CauseTracePanel store={store} model={model} canGoBack={false} onOpen={open} onBack={() => {}} onClose={() => {}} onSelectEntity={() => {}} onSeek={() => {}} />);
  expect(screen.getAllByText(/recorded link/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/same-cut context/).length).toBeGreaterThan(0);
});

it('lets other pages open a trace through the bus and the entity panel opens moments and changes', () => {
  const seen = vi.fn(), off = subscribeCauseTrace(seen);
  openCauseTrace({ kind: 'cut', cut: 4 });
  off(); openCauseTrace({ kind: 'cut', cut: 5 });
  expect(seen).toHaveBeenCalledTimes(1);
  expect(seen).toHaveBeenCalledWith({ kind: 'cut', cut: 4 });
  const store = traceStore(), open = vi.fn();
  render(<InspectionPanel store={store} selected={incident} onSeek={() => {}} onOpenTrace={open} />);
  fireEvent.click(screen.getByText('Car stopped'));
  expect(open.mock.calls[0][0]).toMatchObject({ kind: 'event', text: 'Car stopped' });
  fireEvent.click(screen.getByText(/by Demo Alert/));
  expect(open.mock.calls[1][0]).toMatchObject({ kind: 'change', fieldId: 'demo.incident.active', cut: 4 });
});

it('localizes the complete cause walk while Back, close, entity focus and seek retain exact references', () => {
  localStorage.setItem('aeroagentsim.locale','zh-CN');
  const store=traceStore(), open=vi.fn(), back=vi.fn(), close=vi.fn(), seek=vi.fn(), select=vi.fn();
  const model=explain(store,{kind:'step',instanceId:'chain:a',revision:1},'zh-CN')!;
  const view=render(<I18nProvider><CauseTracePanel store={store} model={model} canGoBack onOpen={open} onBack={back} onClose={close} onSelectEntity={select} onSeek={seek}/></I18nProvider>);
  for(const heading of ['是什么触发了它','执行了什么','共享状态发生了哪些变化','已记录的后续活动']) expect(screen.getByText(heading)).toBeTruthy();
  expect(screen.getByText('车辆 1的速度 = 0')).toBeTruthy();
  expect(screen.getByText(/此字段仅允许事件链写入/)).toBeTruthy();
  expect(screen.getByText('涉及对象：')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'← 返回'})); expect(back).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button',{name:'查看此步骤之后的场景'})); expect(seek).toHaveBeenCalledWith(4);
  fireEvent.click(screen.getByRole('button',{name:/Demo Watch: 等待 → 已观察/})); expect(open).toHaveBeenCalledWith({kind:'step',instanceId:'chain:b',revision:'1'});
  fireEvent.click(screen.getByRole('button',{name:'关闭原因说明'})); expect(close).toHaveBeenCalledOnce();
  view.unmount(); localStorage.clear();
});

it('uses the Chinese recorded-link and same-cut wording without calling either a cause', () => {
  localStorage.setItem('aeroagentsim.locale','zh-CN');
  const store=traceStore(), model=explain(store,{kind:'message',messageId:'m1',cut:4},'zh-CN')!;
  const view=render(<I18nProvider><CauseTracePanel store={store} model={model} canGoBack={false} onOpen={()=>{}} onBack={()=>{}} onClose={()=>{}} onSelectEntity={()=>{}} onSeek={()=>{}}/></I18nProvider>);
  expect(screen.getAllByText(/记录关联/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/同一时刻上下文/).length).toBeGreaterThan(0);
  expect(screen.queryByText(/因果/)).toBeNull();
  view.unmount(); localStorage.clear();
});

it.each(['en-US', 'zh-CN'])('uses neutral headings and trigger wording for duplicate event occurrences (%s)', locale => {
  localStorage.setItem('aeroagentsim.locale', locale);
  const store = duplicateEventTraceStore();
  for (const messageId of ['m1', 'm2']) {
    const model = explain(store, { kind: 'message', messageId, cut: 4 }, locale)!;
    const view = render(<I18nProvider><CauseTracePanel store={store} model={model} canGoBack={false} onOpen={vi.fn()} onBack={vi.fn()} onClose={vi.fn()} onSelectEntity={vi.fn()} onSeek={vi.fn()} /></I18nProvider>);
    expect(screen.getByRole('heading', { name: locale === 'zh-CN' ? '已记录的后续活动' : 'Recorded follow-ups' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: locale === 'zh-CN' ? '同一时刻的状态记录' : 'Same-cut state records' })).toBeInTheDocument();
    expect(screen.getByText(locale === 'zh-CN' ? /匹配的已记录触发条件/ : /matching recorded trigger/)).toBeInTheDocument();
    expect(screen.getByTestId('cause-trace')).not.toHaveTextContent(/reacted|已响应|响应了该事件|Sent by the chain|Announced by the chain/);
    if (messageId === 'm1') expect(screen.getByRole('heading', { name: locale === 'zh-CN' ? '匹配的已记录步骤' : 'Matching recorded step' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: locale === 'zh-CN' ? '是什么触发了它' : 'What set it off' })).toBeNull();
    view.unmount();
  }
  const model = explain(store, { kind: 'step', instanceId: 'chain:b', revision: 1 }, locale)!;
  const view = render(<I18nProvider><CauseTracePanel store={store} model={model} canGoBack={false} onOpen={vi.fn()} onBack={vi.fn()} onClose={vi.fn()} onSelectEntity={vi.fn()} onSeek={vi.fn()} /></I18nProvider>);
  expect(screen.getByRole('heading', { name: locale === 'zh-CN' ? '记录的触发条件' : 'Recorded trigger' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: locale === 'zh-CN' ? '打开匹配的记录' : 'Open matching record' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: locale === 'zh-CN' ? '是什么触发了它' : 'What set it off' })).toBeNull();
  view.unmount();
});

it.each(['en-US', 'zh-CN'])('keeps the event as the focus and labels each contextual choice, even the first or only one (%s)', locale => {
  localStorage.setItem('aeroagentsim.locale', locale);
  const cases: Array<Array<'step' | 'event' | 'command'>> = [['step'], ['event'], ['command'], ['step', 'event', 'command']];
  for (const candidates of cases) {
    const store = sameCutContextStore(candidates), open = vi.fn();
    const model = explain(store, { kind: 'event', text: 'Car stopped' }, locale)!;
    const view = render(<I18nProvider><CauseTracePanel store={store} model={model} canGoBack={false} onOpen={open} onBack={vi.fn()} onClose={vi.fn()} onSelectEntity={vi.fn()} onSeek={vi.fn()} /></I18nProvider>);
    expect(screen.getByRole('heading', { name: locale === 'zh-CN' ? '车辆已停下' : 'Car stopped' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: locale === 'zh-CN' ? '执行了什么' : 'What ran' })).toBeNull();
    expect(screen.queryByRole('heading', { name: locale === 'zh-CN' ? '是什么触发了它' : 'What set it off' })).toBeNull();
    const choices = view.container.querySelector<HTMLElement>('.ct-choices')!;
    const buttons = within(choices).getAllByRole('button');
    expect(buttons).toHaveLength(candidates.length);
    expect(open).not.toHaveBeenCalled();
    for (const [index, button] of buttons.entries()) {
      expect(within(button).getByText(locale === 'zh-CN' ? '同一时刻上下文' : 'same-cut context')).toHaveAttribute('data-basis', 'same-cut');
      fireEvent.click(button);
      expect(open).toHaveBeenNthCalledWith(index + 1, model.choices[index].ref);
    }
    view.unmount();
  }
});

it('opens a new explanation at its heading and retains reading position as live follow-ups arrive', () => {
  const store = traceStore();
  const props = { store, canGoBack: false, onOpen: vi.fn(), onBack: vi.fn(), onClose: vi.fn(), onSelectEntity: vi.fn(), onSeek: vi.fn() };
  const view = render(<div data-testid="scrolling-dock"><CauseTracePanel {...props} /></div>);
  const dock = screen.getByTestId('scrolling-dock');
  Object.defineProperties(dock, { scrollHeight: { value: 1000 }, clientHeight: { value: 200 } });
  dock.scrollTop = 500;
  const model = explain(store, { kind: 'event', text: 'Car stopped' })!;
  view.rerender(<div data-testid="scrolling-dock"><CauseTracePanel {...props} model={model} /></div>);
  expect(dock.scrollTop).toBe(0);
  dock.scrollTop = 120;
  view.rerender(<div data-testid="scrolling-dock"><CauseTracePanel {...props} model={{ ...model, followUps: [...model.followUps] }} /></div>);
  expect(dock.scrollTop).toBe(120);
});
