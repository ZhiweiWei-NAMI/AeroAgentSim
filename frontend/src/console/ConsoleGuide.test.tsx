import { afterEach, beforeEach, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Link, MemoryRouter } from 'react-router-dom';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import ConsoleGuide, { loadGuideSource, resolveGuidePath } from './ConsoleGuide';

beforeEach(() => { window.localStorage.setItem('aeroagentsim.locale', 'en-US'); });
afterEach(() => { cleanup(); window.localStorage.clear(); });

function GuideControls() {
  const { setLocale } = useI18n();
  return <>
    <button onClick={() => setLocale('zh-CN')}>switch-zh</button>
    <button onClick={() => setLocale('en-US')}>switch-en</button>
    <Link to="/guide?guide=predicates.md">next-guide</Link>
  </>;
}

function renderGuide(route = '/guide?guide=behaviours.md') {
  return render(<MemoryRouter initialEntries={[route]}><I18nProvider><GuideControls /><ConsoleGuide /></I18nProvider></MemoryRouter>);
}

// One representative parameterized test: every legacy alias (retired
// docs/platform tree) and every new full directory path must resolve to an
// actual doc whose raw source is nonempty and carries a markdown heading.
it.each([
  ['console.md', 'concepts/views.md'],
  ['RUNTIME.md', 'concepts/architecture.md'],
  ['langgraph.md', 'guides/agents.md'],
  ['observations.md', 'guides/visualization.md'],
  ['predicates.md', 'guides/predicates.md'],
  ['behaviours.md', 'guides/behaviours.md'],
  ['studio.md', 'getting-started/first-scenario.md'],
  ['engines.md', 'concepts/plugins.md'],
  ['demo-traffic-accident.md', 'examples/traffic-accident.md'],
  ['docs/platform/guides/agents.md', 'guides/agents.md'],
  ['docs/getting-started/quickstart.md', 'getting-started/quickstart.md'],
  ['getting-started/first-scenario.md', 'getting-started/first-scenario.md'],
  ['concepts/plugins.md', 'concepts/plugins.md'],
  ['guides/visualization.md', 'guides/visualization.md'],
  ['reference/scenario.md', 'reference/scenario.md'],
  ['examples/traffic-accident.md', 'examples/traffic-accident.md'],
])('resolves %j to actual doc %j with nonempty heading', async (requested, expected) => {
  expect(resolveGuidePath(requested)).toBe(expected);
  const text = String(await loadGuideSource(expected));
  expect(text.trim().length).toBeGreaterThan(0);
  expect(text).toMatch(/^#\s+\S/);
});

it('errors instead of falling back for unknown guides and bare quickstart resolution', async () => {
  expect(resolveGuidePath('docs/platform/retired.md')).toBeNull();
  expect(resolveGuidePath('guides/nope.md')).toBeNull();
  expect(resolveGuidePath('docs/design-notes.md')).toBeNull();
  await expect(loadGuideSource('guides/nope.md')).rejects.toThrow('Unknown guide');
  // Bare names without a legacy alias resolve via the real docs tree.
  expect(resolveGuidePath('quickstart')).toBe('getting-started/quickstart.md');
  expect(resolveGuidePath('first-scenario')).toBe('getting-started/first-scenario.md');
  await expect(loadGuideSource('getting-started/quickstart.md')).resolves.toMatch(/^#\s+\S/);
});

it('renders the original English guide with a beginner summary and an explicit source-language explanation', async () => {
  renderGuide();
  expect(screen.getByText('Loading guide')).toBeTruthy();
  await screen.findByRole('heading', { name: 'In plain language' });
  expect(screen.getByRole('heading', { name: 'Guide: Behaviour packages', level: 1 })).toBeTruthy();
  expect(screen.getByText(/An event reports something that happened/)).toBeTruthy();
  const source = screen.getByRole('region', { name: 'Guide source (English)' });
  expect(within(source).getByText('This is the original English Markdown source. Commands, identifiers and configuration keys are kept unchanged.')).toBeTruthy();
  expect(source.querySelector('pre')?.textContent).toMatch(/^# Guide: Behaviour packages/);
  expect(screen.queryByRole('button', { name: 'Show English source' })).toBeNull();
  expect(screen.getByRole('link', { name: 'Back to Studio' }).getAttribute('href')).toBe('/studio');
});

it('uses a Chinese title and student summary instead of rendering English source by default, with an opt-in source toggle', async () => {
  renderGuide();
  await screen.findByRole('heading', { name: 'In plain language' });
  fireEvent.click(screen.getByRole('button', { name: 'switch-zh' }));
  expect(screen.getByRole('heading', { name: '指南：行为包', level: 1 })).toBeTruthy();
  expect(screen.getByRole('heading', { name: '通俗说明' })).toBeTruthy();
  expect(screen.getByText(/事件报告已经发生的事情/)).toBeTruthy();
  expect(screen.getByText(/一次只修改一项设置/)).toBeTruthy();
  expect(screen.getByText(/指南原文为英文/)).toBeTruthy();
  const source = screen.getByRole('region', { name: '指南原文（英文）' });
  expect(source.querySelector('pre')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '展开英文原文' }));
  expect(screen.getByRole('button', { name: '收起英文原文' }).getAttribute('aria-expanded')).toBe('true');
  expect(source.querySelector('pre')?.textContent).toMatch(/^# Guide: Behaviour packages/);
  fireEvent.click(screen.getByRole('button', { name: '收起英文原文' }));
  expect(screen.getByRole('button', { name: '展开英文原文' }).getAttribute('aria-expanded')).toBe('false');
  expect(source.querySelector('pre')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'switch-en' }));
  expect(screen.getByRole('heading', { name: 'Guide: Behaviour packages', level: 1 })).toBeTruthy();
  expect(screen.getByRole('region', { name: 'Guide source (English)' }).querySelector('pre')).toBeTruthy();
});

it('resets source disclosure when opening another guide and keeps its title and summary in Chinese', async () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  renderGuide();
  await screen.findByRole('heading', { name: '通俗说明' });
  fireEvent.click(screen.getByRole('button', { name: '展开英文原文' }));
  expect(screen.getByRole('region', { name: '指南原文（英文）' }).querySelector('pre')).toBeTruthy();
  fireEvent.click(screen.getByRole('link', { name: 'next-guide' }));
  await screen.findByText(/谓词对已记录的数值提出一个“是\/否”问题/);
  expect(screen.getByRole('heading', { name: '编写谓词', level: 1 })).toBeTruthy();
  expect(screen.getByRole('region', { name: '指南原文（英文）' }).querySelector('pre')).toBeNull();
  expect(screen.getByRole('button', { name: '展开英文原文' }).getAttribute('aria-expanded')).toBe('false');
});

it.each([
  ['getting-started/first-scenario.md', '创建第一个场景', '场景就像仿真的配方'],
  ['concepts/aerograph.md', 'AeroGraph 与场景注册表', '类型描述一类实体'],
  ['concepts/architecture.md', '系统架构', '所有插件共享一个内核'],
  ['concepts/runs.md', '运行、回放与来源追踪', '工作区是可编辑的配方'],
  ['guides/sumo.md', '指南：SUMO 道路交通', 'SUMO 通过适配器提供道路交通仿真'],
  ['guides/agents.md', '指南：智能体（决策引擎与实时大语言模型）', '智能体读取观测并提出决策'],
  ['reference/cli.md', '命令行参考', '命令行用于安装'],
  ['examples/traffic-accident.md', '交通事故实验', '可以从交通事故演示开始'],
])('localizes guide %s without displaying the English document by default', async (guide, title, summary) => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  renderGuide(`/guide?guide=${encodeURIComponent(guide)}`);
  await screen.findByRole('heading', { name: '通俗说明' });
  expect(screen.getByRole('heading', { name: title, level: 1 })).toBeTruthy();
  expect(screen.getByText(new RegExp(summary))).toBeTruthy();
  expect(screen.getByRole('region', { name: '指南原文（英文）' }).querySelector('pre')).toBeNull();
});

it('localizes unknown-guide errors and preserves explicit API and workspace context when returning to Studio', async () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  renderGuide('/guide?guide=missing.md&api=https%3A%2F%2Fapi.example.test&workspace=studio-1');
  await screen.findByRole('heading', { name: '无法打开指南' });
  expect(screen.getByRole('heading', { name: '控制台指南', level: 1 })).toBeTruthy();
  expect(screen.getByText('当前版本未包含此指南。')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '展开英文原文' })).toBeNull();
  const back = screen.getByRole('link', { name: '返回工作室' });
  const url = new URL(back.getAttribute('href')!, 'http://localhost');
  expect(url.pathname).toBe('/studio');
  expect(url.searchParams.get('api')).toBe('https://api.example.test');
  expect(url.searchParams.get('workspace')).toBe('studio-1');
});
