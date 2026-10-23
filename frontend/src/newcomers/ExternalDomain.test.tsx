import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider, STORAGE_KEY, useI18n } from '../i18n/I18nProvider';
import type { KpiValue, KpiValues, TemplateApi } from './api';
import { decodeKpis } from './api-decode';
import { CompareRuns } from './CompareRuns';
import { KpiPanel } from './KpiPanel';

// An arbitrary installed metric: there is no corresponding browser ID/copy table.
const id = 'orchard.moisture';
const descriptor: KpiValue = {
  value: null, label: 'Observed soil moisture', label_zh: '已观测土壤湿度', unit: '%',
  direction: 'higher', aggregation: 'Latest moisture sample from orchard.sample events.',
  missing_data_policy: 'None before a sample; a recorded zero is measured zero.',
};

beforeEach(() => localStorage.setItem(STORAGE_KEY, 'en-US'));
afterEach(() => { cleanup(); localStorage.removeItem(STORAGE_KEY); vi.restoreAllMocks(); });

function LocaleToggle() {
  const { locale, setLocale } = useI18n();
  return <button onClick={() => setLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN')}>
    {locale === 'zh-CN' ? '切换语言' : 'Switch language'}
  </button>;
}

function panel(value: number | null) {
  const data = decodeKpis({
    format: 'aeroagentsim.kpi/v1', run_id: 'orchard', series: [],
    final: { [id]: { ...descriptor, value } },
  });
  return <I18nProvider><KpiPanel values={data.final} series={data.series} ids={[id]} /><LocaleToggle /></I18nProvider>;
}

function comparisonApi(left: KpiValues, right: KpiValues): TemplateApi {
  const recorded_ends = {
    a: { at_ns: '1000000000', commit_index: 2, status: 'completed' },
    b: { at_ns: '1000000000', commit_index: 2, status: 'completed' },
  };
  return {
    request: vi.fn().mockResolvedValue({
      runs: [left, right].map((kpis, index) => ({
        id: index === 0 ? 'a' : 'b', title: index === 0 ? 'A' : 'B', kpis, knobs: {}, algorithms: {},
        status: 'completed', results_final: true, results_cursor: 3, final_commit_index: 2,
      })),
      alignment: 'recorded_run_end', recorded_ends, config_diff: [],
    }),
    templates: vi.fn().mockResolvedValue([]), algorithms: vi.fn().mockResolvedValue([]),
  } as unknown as TemplateApi;
}

it('renders an external metric’s bilingual labels, unit and help from decoded rows, keeping null and zero distinct', () => {
  const view = render(panel(null));
  expect(screen.getByText(descriptor.label)).toBeVisible();
  expect(screen.getByTitle('Not measured')).toHaveTextContent('—');
  expect(screen.getByTitle('Not measured')).toHaveClass('is-null');
  expect(screen.queryByText('0 %')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: `About ${descriptor.label}` }));
  const help = document.querySelector('.newc-kpi-help-text')!;
  expect(help).toHaveTextContent(`Aggregation: ${descriptor.aggregation}`);
  expect(help).toHaveTextContent(`Missing data: ${descriptor.missing_data_policy}`);

  fireEvent.click(screen.getByRole('button', { name: 'Switch language' }));
  expect(screen.getByText(descriptor.label_zh!)).toBeVisible();
  expect(screen.queryByText(descriptor.label)).toBeNull();
  expect(screen.getByTitle('未测量')).toHaveTextContent('—');
  expect(help).toHaveTextContent(`汇总方式：${descriptor.aggregation}`);
  expect(help).toHaveTextContent(`缺失数据：${descriptor.missing_data_policy}`);

  view.rerender(panel(0));
  expect(screen.getByText('0 %')).toBeVisible();
  expect(screen.queryByText('—')).toBeNull();
  expect(document.querySelector('.newc-kpi-value.is-null')).toBeNull();
  expect(descriptor.value).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '切换语言' }));
  expect(screen.getByText(descriptor.label)).toBeVisible();
  expect(screen.getByText('0 %')).toBeVisible();
});

it.each([
  ['en-US', 'higher'], ['zh-CN', 'higher'], ['en-US', 'lower'], ['zh-CN', 'lower'],
] as const)('compares an external metric using only its row direction and copy in %s (%s)', async (locale, direction) => {
  localStorage.setItem(STORAGE_KEY, locale);
  const api = comparisonApi(
    { [id]: { ...descriptor, direction, value: 2 } },
    { [id]: { ...descriptor, direction, value: 8 } },
  );
  const { container } = render(<I18nProvider><CompareRuns api={api} ids={['a', 'b']} /></I18nProvider>);
  const label = locale === 'zh-CN' ? descriptor.label_zh! : descriptor.label;
  await screen.findAllByText(label);
  const columns = container.querySelectorAll('.newc-compare-col');
  const rows = [...columns].map(column => within(column as HTMLElement).getByText(label).closest('tr')!);
  expect(within(rows[0]).getByText('2 %')).toBeVisible();
  expect(within(rows[1]).getByText('8 %')).toBeVisible();
  expect(rows[direction === 'higher' ? 1 : 0]).toHaveClass('is-best');
  expect(rows[direction === 'higher' ? 0 : 1]).not.toHaveClass('is-best');
  expect(rows[1].querySelector('.newc-kpi-delta')).toHaveClass(direction === 'higher' ? 'is-ok' : 'is-warn');
  for (const row of rows) {
    const help = row.querySelector('.newc-compare-help')!;
    expect(help.getAttribute('title')).toContain(descriptor.aggregation);
    expect(help.getAttribute('title')).toContain(descriptor.missing_data_policy);
    expect(help.getAttribute('title')).toContain(locale === 'zh-CN' ? '汇总方式：' : 'Aggregation: ');
    expect(help.getAttribute('title')).toContain(locale === 'zh-CN' ? '缺失数据：' : 'Missing data: ');
  }
});

it.each(['en-US', 'zh-CN'])('keeps a missing comparison value distinct from measured zero, including More metrics, in %s', async locale => {
  localStorage.setItem(STORAGE_KEY, locale);
  const api = comparisonApi({ [id]: { ...descriptor, direction: 'lower', value: null } },
    { [id]: { ...descriptor, direction: 'lower', value: 0 } });
  const { container } = render(<I18nProvider><CompareRuns api={api} ids={['a', 'b']} /></I18nProvider>);
  const label = locale === 'zh-CN' ? descriptor.label_zh! : descriptor.label;
  await screen.findAllByText(label);
  const disclosures = container.querySelectorAll('.newc-kpi-more summary');
  expect(disclosures).toHaveLength(2);
  disclosures.forEach(summary => fireEvent.click(summary));
  const rows = screen.getAllByText(label).map(element => element.closest('tr')!);
  expect(within(rows[0]).getByText('—')).toBeVisible();
  expect(rows[0]).not.toHaveClass('is-best');
  expect(within(rows[1]).getByText('0 %')).toBeVisible();
  expect(rows[1]).toHaveClass('is-best');
  expect(container.querySelector('.newc-kpi-delta')).toBeNull();
  rows.forEach(row => expect(row.querySelector('.newc-compare-help')?.getAttribute('title')).toContain(descriptor.missing_data_policy));
});

it.each([
  [id, null], [id, undefined], ['energy_used_kj', null], ['energy_used_kj', undefined],
] as const)('does not colour a metric without a direction as better or worse (%s, %s)', async (metricId, direction) => {
  const api = comparisonApi({ [metricId]: { ...descriptor, direction, value: 2 } },
    { [metricId]: { ...descriptor, direction, value: 8 } });
  const { container } = render(<I18nProvider><CompareRuns api={api} ids={['a', 'b']} /></I18nProvider>);
  await screen.findAllByText(descriptor.label);
  expect(screen.getByText('2 %')).toBeVisible();
  expect(screen.getByText('8 %')).toBeVisible();
  expect(container.querySelector('.newc-compare-grid .is-best')).toBeNull();
  expect(container.querySelector('.newc-compare-grid .is-ok, .newc-compare-grid .is-warn')).toBeNull();
  expect(container.querySelector('.newc-kpi-delta, .newc-bar')).toBeNull();
});

it('uses descriptor metadata instead of legacy builtin copy and boolean formatting', () => {
  localStorage.setItem(STORAGE_KEY, 'zh-CN');
  render(<I18nProvider><KpiPanel values={{ rescue_success: { ...descriptor, value: 0 } }}
    ids={['rescue_success']} series={[]} /></I18nProvider>);
  expect(screen.getByText(descriptor.label_zh!)).toBeVisible();
  expect(screen.queryByText('救援成功')).toBeNull();
  expect(screen.queryByText('否')).toBeNull();
  expect(screen.getByText('0 %')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: `了解“${descriptor.label_zh}”` }));
  expect(document.querySelector('.newc-kpi-help-text')).toHaveTextContent(descriptor.aggregation!);
  expect(document.querySelector('.newc-kpi-help-text')).toHaveTextContent(descriptor.missing_data_policy!);
});

it('keeps each comparison column’s own descriptor text rather than copying the first run', async () => {
  const other = { ...descriptor, label: 'Later moisture label', label_zh: '后续湿度标签',
    aggregation: 'Latest calibrated sample.', missing_data_policy: 'None before calibration.', value: 8 };
  const api = comparisonApi({ [id]: { ...descriptor, value: 2 } }, { [id]: other });
  render(<I18nProvider><CompareRuns api={api} ids={['a', 'b']} /></I18nProvider>);
  const row = (await screen.findByText(other.label)).closest('tr')!;
  const help = row.querySelector('.newc-compare-help')!.getAttribute('title');
  expect(help).toContain(other.aggregation);
  expect(help).toContain(other.missing_data_policy);
  expect(help).not.toContain(descriptor.aggregation);
});
