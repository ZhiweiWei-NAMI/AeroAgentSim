import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import { EntityGraph } from './EntityGraph';
import { BehaviourRunGraph } from './BehaviourRunGraph';
import { DualRunViews } from './DualRunViews';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from '../viewport/clock';
import { emptyCommit, fixtureCommits, fixtureHeader, task } from './extension-fixture';
import type { ReactNode } from 'react';

vi.mock('../viewport/ViewerPresentation', () => ({ ViewerPresentation: () => <div data-testid="spatial-view" /> }));
class ResizeObserverStub { observe() {} disconnect() {} }

beforeEach(() => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  vi.spyOn(SVGElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 600, height: 400, x: 0, y: 0, top: 0, left: 0, right: 600, bottom: 400, toJSON: () => ({}) } as DOMRect);
});
afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function LocaleSwitch() {
  const { setLocale } = useI18n();
  return <button type="button" onClick={() => setLocale('en-US')}>Change locale</button>;
}
function Bilingual({ children }: { children: ReactNode }) {
  return <I18nProvider><MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><LocaleSwitch />{children}</MemoryRouter></I18nProvider>;
}

it('localizes grouped graph controls, search results and keyboard expansion without changing entity keys', () => {
  const store = new TemporalFeedStore({ ...fixtureHeader, types: [{ typeId: 'task', displayName: 'Task / 任务', ancestors: [] }] });
  const tasks = Array.from({ length: 9 }, (_, index) => ({ id: `task-${index + 1}`, generation: 1 }));
  store.ingest({ ...emptyCommit(1), created: tasks.map(key => ({ ...key, typeId: 'task' })) });
  store.seek('0', 1);
  const onSelect = vi.fn();
  const view = render(<Bilingual><EntityGraph store={store} selected={tasks[8]} highlights={[tasks[7]]} onSelect={onSelect} /></Bilingual>);
  const graph = screen.getByTestId('entity-graph');
  expect(within(graph).getByText('9 个实体，1 个分组 · 0 条连线')).toBeVisible();
  expect(graph.querySelectorAll('.eg-node')).toHaveLength(2);
  expect(within(graph).getByRole('group', { name: '实体关系图；拖动平移，滚轮缩放' })).toBeVisible();
  const chip = within(graph).getByRole('button', { name: '显示任务（9）中的全部 9 个实体' });
  expect(chip).toHaveAttribute('tabindex', '0');
  fireEvent.keyDown(chip, { key: ' ' });
  expect(graph.querySelectorAll('.eg-node')).toHaveLength(9);
  fireEvent.keyDown(within(graph).getByRole('button', { name: '折叠任务（9）' }), { key: 'Enter' });
  expect(graph.querySelectorAll('.eg-node')).toHaveLength(2);
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索图中实体' }), { target: { value: 'task-3' } });
  expect(within(graph).getByRole('status')).toHaveTextContent('找到 1 个实体');
  expect(graph.querySelectorAll('.eg-node')).toHaveLength(3);
  fireEvent.change(screen.getByLabelText('图中实体'), { target: { value: 'task-3:1' } });
  expect(onSelect).toHaveBeenCalledWith(tasks[2]);
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索图中实体' }), { target: { value: 'absent' } });
  expect(within(graph).getByRole('status')).toHaveTextContent('没有匹配的实体。');
  expect(screen.getByLabelText('图中实体')).toHaveValue('task-9:1'); // selection survives a nonmatching search
  const canvas = graph.querySelector('svg > g')!;
  const fitted = canvas.getAttribute('transform');
  fireEvent.click(within(graph).getByRole('button', { name: '放大关系图' }));
  expect(canvas.getAttribute('transform')).not.toBe(fitted);
  fireEvent.click(within(graph).getByRole('button', { name: '适应画布' }));
  expect(canvas.getAttribute('transform')).toBe(fitted);
  fireEvent.click(screen.getByRole('button', { name: 'Change locale' }));
  expect(within(graph).getByRole('button', { name: 'Expand Tasks (9)' })).toBeVisible();
  expect(screen.getByLabelText('Search graph entities')).toHaveValue('absent');
  expect(screen.getByLabelText('Graph entity')).toHaveValue('task-9:1');
  expect(within(graph).getByText('9 entities in 1 groups · 0 links')).toBeVisible();
  expect(view.container.textContent).not.toMatch(/\{(?:entities|groups|count|pinned)\}/);
});

it('prefers recorded Chinese names, truncates wide labels and preserves the full authored identity', () => {
  const store = new TemporalFeedStore(fixtureHeader);
  const key = { id: 'authored/record', generation: '9007199254740993' };
  const chinese = '这是一条保留原始值且不应溢出图中节点的中文名称';
  const english = 'Authored craft identity';
  store.ingest({ ...emptyCommit(1), created: [{ ...key, typeId: 'task' }], facts: [
    { entity: key, fieldId: 'name_zh', value: chinese, producer: 'actual', validFrom: { ns: '0', microstep: 0 } },
    { entity: key, fieldId: 'name', value: english, producer: 'actual', validFrom: { ns: '0', microstep: 0 } },
  ] });
  store.seek('0', 1);
  const select = vi.fn();
  render(<Bilingual><EntityGraph store={store} selected={key} onSelect={select} /></Bilingual>);
  const graph = screen.getByTestId('entity-graph');
  const node = within(graph).getByRole('button', { name: `${chinese}，task，代次 9007199254740993` });
  expect(node.querySelector('.eg-label')?.textContent).toMatch(/…$/);
  expect(node.querySelector('.eg-label')?.textContent?.length).toBeLessThanOrEqual(13);
  expect(node.querySelector('title')).toHaveTextContent(chinese);
  fireEvent.keyDown(node, { key: ' ' });
  expect(select).toHaveBeenCalledWith(key);
  fireEvent.click(screen.getByRole('button', { name: 'Change locale' }));
  expect(within(graph).getByRole('button', { name: `${english}, task, generation 9007199254740993` })).toBeVisible();
  expect(store.entities.get('authored/record:9007199254740993')?.fields.get('name')?.value).toBe(english);
  expect(store.entities.get('authored/record:9007199254740993')?.fields.get('name_zh')?.value).toBe(chinese);
});

it('shows bilingual graph empty states and preserves selected runtime entities through filters', () => {
  const store = new TemporalFeedStore({ ...fixtureHeader, types: [...fixtureHeader.types, { typeId: 'aas:BehaviourInstance', displayName: 'Behaviour instance / 行为实例', ancestors: [], directory: 'runtime' }] });
  const select = vi.fn(), seek = vi.fn();
  const view = render(<Bilingual><BehaviourRunGraph store={store} onSelect={select} onSeek={seek} /></Bilingual>);
  expect(screen.getByText('当前时刻没有活动实体。')).toBeVisible();
  expect(screen.getByLabelText('聚焦选中实体及邻居')).toBeDisabled();
  fireEvent.click(screen.getByText('条件与事件链 · 0 个条件 · 0 条事件链'));
  expect(screen.getByText('此数据流没有条件或事件链记录。')).toBeVisible();
  const runtime = { id: 'runtime.actual', generation: '9007199254740993' };
  store.ingest({ ...emptyCommit(1), created: [{ ...runtime, typeId: 'aas:BehaviourInstance' }] });
  store.seek('0', 1);
  view.rerender(<Bilingual><BehaviourRunGraph store={store} selected={runtime} onSelect={select} onSeek={seek} /></Bilingual>);
  fireEvent.change(screen.getByLabelText('关系图类型'), { target: { value: 'task' } });
  const graph = screen.getByTestId('entity-graph');
  expect(graph).toHaveAttribute('data-node-count', '1');
  fireEvent.keyDown(within(graph).getByRole('button', { name: /runtime\.actual|Runtime Actual/ }), { key: 'Enter' });
  expect(select).toHaveBeenCalledWith(runtime);
  expect(screen.getByLabelText('图中实体')).toHaveValue('runtime.actual:9007199254740993');
  fireEvent.click(screen.getByRole('button', { name: 'Change locale' }));
  expect(screen.getByText('Conditions and event chains · 0 conditions · 0 chains')).toBeVisible();
  expect(screen.getByText('No recorded predicate/chain data in this feed.')).toBeVisible();
});

it('localizes synchronized headings and predicate/chain navigation while retaining exact journal cuts', () => {
  const store = new TemporalFeedStore(fixtureHeader);
  fixtureCommits.forEach(commit => store.ingest(commit));
  store.seek('10', 2);
  const select = vi.fn(), seek = vi.fn();
  const viewer = { clock: new PlaybackClock('0', '10'), mode: 'orbit' as const, quality: 'med' as const, trails: true, onError: vi.fn(), onQuality: vi.fn(), onTick: vi.fn() };
  const view = render(<Bilingual><DualRunViews {...viewer} store={store} selected={task} onSelect={select} onSeek={seek} /></Bilingual>);
  expect(screen.getByRole('heading', { name: '城市视图' })).toBeVisible();
  expect(screen.getByText('同一时间轴时刻')).toBeVisible();
  fireEvent.click(screen.getByText('条件与事件链 · 1 个条件 · 1 条事件链'));
  expect(screen.getByText('未知 · 缺少所需输入')).toBeVisible();
  expect(screen.getByText('缺少所需的来源历史记录')).toBeVisible();
  expect(within(screen.getByLabelText('关系图目录')).getByRole('option', { name: '环境' })).toHaveValue('environment');
  expect(within(screen.getByLabelText('关系图目录')).getByRole('option', { name: '任务' })).toHaveValue('tasks');
  fireEvent.click(screen.getByRole('button', { name: /跳(?:转)?到求值时刻/ }));
  expect(seek).toHaveBeenCalledWith(1);
  store.seek('10', 3);
  view.rerender(<Bilingual><DualRunViews {...viewer} store={store} selected={task} onSelect={select} onSeek={seek} /></Bilingual>);
  fireEvent.click(screen.getByRole('button', { name: /跳(?:转|到)? launch →/ }));
  expect(select).toHaveBeenCalledWith(task);
  expect(seek).toHaveBeenCalledWith(3);
  expect(screen.getByTestId('synchronized-city')).toHaveAttribute('data-cut', '3');
  expect(screen.getByTestId('dual-run-views')).toHaveAttribute('data-cut', '3');
  fireEvent.click(screen.getByRole('button', { name: 'Change locale' }));
  expect(screen.getByRole('heading', { name: 'City view' })).toBeVisible();
  expect(screen.getByText('Same timeline moment')).toBeVisible();
  expect(screen.getByRole('button', { name: /Seek launch → moving/ })).toBeVisible();
});
