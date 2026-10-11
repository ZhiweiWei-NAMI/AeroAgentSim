import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { InspectionPanel } from '../pages/InspectionPanel';
import { CauseTracePanel } from './CauseTracePanel';
import { subscribeCauseTrace, openCauseTrace } from './bus';
import { explain } from './cause-trace';
import { incident, traceStore } from './trace-fixture';
import { I18nProvider } from '../i18n/I18nProvider';

it('tells the story in plain words and lets each follow-up continue the walk', () => {
  const store = traceStore(), open = vi.fn();
  const model = explain(store, { kind: 'event', text: 'Car stopped' })!;
  render(<CauseTracePanel store={store} model={model} canGoBack={false} onOpen={open} onBack={() => {}} onClose={() => {}} onSelectEntity={() => {}} onSeek={() => {}} />);
  for (const heading of ['What set it off', 'What ran', 'What changed in the shared state', 'What happened next']) expect(screen.getByText(heading)).toBeTruthy();
  expect(screen.getByText("Car 1's speed = 0")).toBeTruthy();
  expect(screen.getAllByText(/Only event chains may write this field/)).toHaveLength(1);
  expect(screen.getByText('false')).toBeTruthy(); expect(screen.getByText('true')).toBeTruthy();
  fireEvent.click(screen.getByText(/Demo Watch: Wait → Seen/));
  expect(open).toHaveBeenCalledWith({ kind: 'step', instanceId: 'chain:b', revision: '1' });
  expect(screen.queryByText(/receipt|invocation/i)).toBeNull();
  expect(screen.getByText('Involved objects:')).toBeTruthy();
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
  const model=explain(store,{kind:'event',text:'Car stopped'},'zh-CN')!;
  const view=render(<I18nProvider><CauseTracePanel store={store} model={model} canGoBack onOpen={open} onBack={back} onClose={close} onSelectEntity={select} onSeek={seek}/></I18nProvider>);
  for(const heading of ['是什么触发了它','执行了什么','共享状态发生了哪些变化','接下来发生了什么']) expect(screen.getByText(heading)).toBeTruthy();
  expect(screen.getByText('车辆 1的速度 = 0')).toBeTruthy();
  expect(screen.getByText(/此字段仅允许事件链写入/)).toBeTruthy();
  expect(screen.getByText('涉及对象：')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'← 返回'})); expect(back).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button',{name:'查看此步骤之后的场景'})); expect(seek).toHaveBeenCalledWith(4);
  fireEvent.click(screen.getByRole('button',{name:/Demo Watch: 等待 → 已观察/})); expect(open).toHaveBeenCalledWith({kind:'step',instanceId:'chain:b',revision:'1'});
  fireEvent.click(screen.getByRole('button',{name:'关闭原因说明'})); expect(close).toHaveBeenCalledOnce();
  view.unmount(); localStorage.clear();
});
