import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CompareRuns, comparisonConfiguration } from './CompareRuns';
import type { TemplateApi } from './api';
import { I18nProvider, STORAGE_KEY } from '../i18n/I18nProvider';

afterEach(()=>{cleanup();localStorage.removeItem(STORAGE_KEY);});
const runs = ['a','b'].map(id=>({id,title:id,kpis:{},knobs:{},algorithms:{}}));
const recorded_ends = {a:{at_ns:'10000000000',commit_index:2,status:'stopped'},b:{at_ns:'20000000000',commit_index:3,status:'completed'}};

/** Backend compare response: one payload carries runs, finality, ends and config. */
function response(list = runs, extra: Record<string, unknown> = {}) {
  const withFinality = list.map(run => {
    const end = recorded_ends[run.id as keyof typeof recorded_ends];
    return end ? { ...run, status: end.status, results_final: true, results_cursor: end.commit_index + 1, final_commit_index: end.commit_index } : run;
  });
  return { runs: withFinality, alignment: 'recorded_run_end', recorded_ends, config_diff: [], ...extra };
}
function apiFor(value: unknown) {
  return { request: vi.fn().mockResolvedValue(value), templates: vi.fn().mockResolvedValue([]), algorithms: vi.fn().mockResolvedValue([]) } as unknown as TemplateApi;
}

it.each(['en-US', 'zh-CN'])('T2-R5-002: shows the effective 5 m/s from the pinned run instead of the template default in %s', async locale => {
  localStorage.setItem(STORAGE_KEY, locale);
  const actual = [
    { ...runs[0], template_id: 'traffic', knobs: { wind_east_mps: 5 } },
    { ...runs[1], template_id: 'other', knobs: {} },
  ];
  const api = { request: vi.fn().mockResolvedValue(response(actual)),
    templates: vi.fn().mockResolvedValue([{ id: 'traffic', title: 'Traffic', kpis: [], knobs: [
      { id: 'wind_east_mps', label: 'Eastward wind (m/s)', label_zh: '向东风速（米/秒）', type: 'number', default: 3, apply: 'traffic.wind_east' },
    ] }]), algorithms: vi.fn().mockResolvedValue([]) } as unknown as TemplateApi;
  render(<I18nProvider><CompareRuns api={api} ids={['a', 'b']} /></I18nProvider>);
  const label = await screen.findByText(locale === 'zh-CN' ? '向东风速（米/秒）' : 'Eastward wind (m/s)');
  const row = within(label.closest('li')!);
  expect(row.getByText('5')).toBeVisible();
  expect(row.getByText('—')).toBeVisible();
  expect(row.queryByText('3')).toBeNull();
});

it('shows direct form differences, null versus missing, and recorded time alignment',async()=>{
  const request=vi.fn().mockResolvedValue(response(runs,{config_diff:[
    {path:'/run/seed',values:{a:42,b:43},present:{a:true,b:true}},
    {path:'/objects/x',values:{a:null},present:{a:true,b:false}},
  ]}));
  const api={request,templates:vi.fn().mockResolvedValue([])} as unknown as TemplateApi;
  render(<CompareRuns api={api} ids={['a','b']}/>);
  const section=await screen.findByTestId('comparison-configuration');
  await screen.findByText('/run/seed');
  expect(within(section).getByText(/simulation durations may differ/)).toBeVisible();
  expect(within(section).getByText(/00:10.0/)).toHaveTextContent('Stopped');
  expect(within(section).getByText(/00:20.0/)).toHaveTextContent('Completed');
  expect(within(section).getByText('42')).toBeVisible();expect(within(section).getByText('43')).toBeVisible();
  expect(within(section).getByText('null')).toBeVisible();expect(within(section).getByText('Not set')).toBeVisible();
  expect(request).toHaveBeenCalledWith('/v1/runs/compare?ids=a,b',undefined,expect.anything());
});

it('reports unavailable configuration metadata instead of declaring identical settings',async()=>{
  const api={request:vi.fn().mockResolvedValue({runs:response(runs).runs}),templates:vi.fn().mockResolvedValue([])} as unknown as TemplateApi;
  render(<CompareRuns api={api} ids={['a','b']}/>);
  expect(await screen.findByText(/Could not read pinned configuration differences/)).toHaveTextContent(/malformed/);
  expect(screen.queryByText(/configurations are identical/)).toBeNull();
  expect(()=>comparisonConfiguration({config_diff:[]})).toThrow('malformed');
  expect(()=>comparisonConfiguration({alignment:'recorded_run_end',recorded_ends,config_diff:[{path:'/run/seed',values:{a:42},present:{a:true}}]},['a','b'])).toThrow('malformed');
  expect(()=>comparisonConfiguration({alignment:'recorded_run_end',config_diff:[]},['a','b'])).toThrow('malformed');
});

it('keeps the configuration comparison readable in Chinese',async()=>{
  const api={request:vi.fn().mockResolvedValue(response()),templates:vi.fn().mockResolvedValue([]),algorithms:vi.fn().mockResolvedValue([])} as unknown as TemplateApi;
  localStorage.setItem(STORAGE_KEY,'zh-CN');
  render(<I18nProvider><CompareRuns api={api} ids={['a','b']}/></I18nProvider>);
  expect(await screen.findByText(/两次运行的固定配置相同/)).toBeVisible();
  expect(screen.getByText(/仿真时长可能不同/)).toBeVisible();
});

it.each(['en-US','zh-CN'])('keeps long source locations compact and exposes their exact recorded values in %s',async(locale)=>{
  localStorage.setItem(STORAGE_KEY,locale);
  const source='/tmp/aas-sol/polish/e2e-release/runs/.workspaces/studio-a841d6909e3c4413a2f50e153d6c598c/versions/1/files:$.behaviours[0]';
  const api={request:vi.fn().mockResolvedValue(response(runs,{config_diff:[
    {path:'/run/seed',values:{a:42,b:43},present:{a:true,b:true}},
    {path:'/behaviours/0/source',values:{a:source,b:source.replace('a841d','b841d')},present:{a:true,b:true}},
  ]})),templates:vi.fn().mockResolvedValue([]),algorithms:vi.fn().mockResolvedValue([])} as unknown as TemplateApi;
  render(<I18nProvider><CompareRuns api={api} ids={['a','b']}/></I18nProvider>);
  await screen.findByText('/run/seed');
  const full=screen.getByText(JSON.stringify(source));expect(full).not.toBeVisible();
  const detail=full.closest('details')!;expect(within(detail).getByText(locale==='zh-CN'?'源位置 · files:$.behaviours[0]':'Source location · files:$.behaviours[0]')).toBeVisible();
  fireEvent.click(detail.querySelector('summary')!);expect(full).toBeVisible();expect(screen.getByText('43')).toBeVisible();
});

it('leads with a summary naming the compared runs and collapses bulk JSON values',async()=>{
  const behaviours=[{id:'patrol',trigger:{at_ns:0}},{id:'climb',trigger:{at_ns:5}},{id:'descend',trigger:{at_ns:9}},{id:'hover',trigger:{at_ns:12}},{id:'land',trigger:{at_ns:20}}];
  const api={request:vi.fn().mockResolvedValue(response(runs,{config_diff:[
    {path:'/behaviours',values:{a:behaviours,b:[]},present:{a:true,b:true}},
  ]})),templates:vi.fn().mockResolvedValue([])} as unknown as TemplateApi;
  render(<I18nProvider><CompareRuns api={api} ids={['a','b']}/></I18nProvider>);
  // The summary names every compared run up front.
  expect(await screen.findByTestId('comparison-mixed')).toHaveTextContent('Comparing: Run 1 · a, Run 2 · b');
  const summary=screen.getByTestId('comparison-summary');
  // KPI summary exists before configuration differences.
  expect(within(summary).getByText(/Start here/)).toBeVisible();
  // Raw JSON wall stays collapsed behind a disclosure with a size summary.
  const raw=screen.getByText(JSON.stringify(behaviours));
  expect(raw).not.toBeVisible();
  const bulk=raw.closest('details')!;
  expect(within(bulk).getByText(/Show raw JSON · list · 5/)).toBeVisible();
  fireEvent.click(bulk.querySelector('summary')!);expect(raw).toBeVisible();
  expect(screen.queryByText(/metric definitions may not be directly comparable/)).toBeNull();
});

it('explains limited comparability when templates differ and keeps exact large integers',async()=>{
  const mixed=[{id:'a',title:'a',template_id:'food-delivery',kpis:{},knobs:{},algorithms:{}},{id:'b',title:'b',template_id:'city-day',kpis:{},knobs:{},algorithms:{}}];
  const api={request:vi.fn().mockResolvedValue(response(mixed,{config_diff:[
    {path:'/run/seed',values:{a:{'$integer':'9007199254740993'}},present:{a:true,b:false}},
  ]})),templates:vi.fn().mockResolvedValue([])} as unknown as TemplateApi;
  render(<I18nProvider><CompareRuns api={api} ids={['a','b']}/></I18nProvider>);
  expect(await screen.findByTestId('comparison-mixed')).toHaveTextContent(/metric definitions may not be directly comparable/);
  // Lossless integer survives; absent column stays visually distinct from null.
  expect(await screen.findByText('9007199254740993')).toBeVisible();
  expect(screen.getAllByText('Not set').length).toBeGreaterThan(0);
});

it('does not leave the summary stuck on Loading when recorded ends are unavailable',async()=>{
  const api={request:vi.fn().mockResolvedValue({runs:response(runs).runs}),templates:vi.fn().mockResolvedValue([])} as unknown as TemplateApi;
  render(<I18nProvider><CompareRuns api={api} ids={['a','b']}/></I18nProvider>);
  await screen.findByText(/Could not read pinned configuration differences/);
  const summary=screen.getByTestId('comparison-summary');
  expect(within(summary).getAllByText('Unavailable')).toHaveLength(2);
  expect(within(summary).queryByText(/Loading…/)).toBeNull();
});

it('shows final badges and freezes at the recorded cursors only when every run is final',async()=>{
  const list=[
    {...runs[0],kpis:{energy_used_kj:{value:900,unit:'kJ',label:'Energy used',direction:'min'}}},
    {...runs[1],kpis:{energy_used_kj:{value:700,unit:'kJ',label:'Energy used',direction:'min'}}},
  ];
  const request=vi.fn().mockResolvedValue(response(list));
  render(<CompareRuns api={{request,templates:vi.fn().mockResolvedValue([]),algorithms:vi.fn().mockResolvedValue([])} as unknown as TemplateApi} ids={['a','b']}/>);
  const summary=await screen.findByTestId('comparison-summary');
  expect(within(summary).getByTestId('comparison-state-a')).toHaveTextContent('Final');
  expect(within(summary).getByTestId('comparison-state-b')).toHaveTextContent('Final');
  expect(screen.getByTestId('comparison-refresh')).toHaveTextContent('frozen as of cursors 2, 3');
  // Results, ends and pinned configuration all came from one response.
  expect(request).toHaveBeenCalledTimes(1);
  expect(request).toHaveBeenCalledWith('/v1/runs/compare?ids=a,b',undefined,expect.anything());
  const cells=screen.getAllByText(/kJ/);
  expect(cells[1]).toHaveClass('is-best');
});

it('shows provisional runs, refreshes them, and suppresses best emphasis',async()=>{
  const list=[
    {...runs[0],kpis:{energy_used_kj:{value:900,unit:'kJ',label:'Energy used',direction:'min'}}},
    {...runs[1],kpis:{energy_used_kj:{value:700,unit:'kJ',label:'Energy used',direction:'min'}}},
  ];
  const api=apiFor({runs:list,alignment:'recorded_run_end',recorded_ends,config_diff:[]});
  render(<CompareRuns api={api} ids={['a','b']}/>);
  const summary=await screen.findByTestId('comparison-summary');
  expect(within(summary).getByTestId('comparison-state-a')).toHaveTextContent('Provisional');
  expect(screen.getByTestId('comparison-refresh')).toHaveTextContent('Some runs are provisional; refreshing every 3 seconds');
  expect(document.querySelector('.newc-kpi-cell.is-best')).toBeNull();
});

it('suppresses best emphasis when final runs disagree about a metric direction',async()=>{
  const list=[
    {...runs[0],kpis:{energy_used_kj:{value:900,unit:'kJ',label:'Energy used',direction:'min'}}},
    {...runs[1],kpis:{energy_used_kj:{value:700,unit:'kJ',label:'Energy used',direction:'max'}}},
  ];
  render(<CompareRuns api={apiFor(response(list))} ids={['a','b']}/>);
  await screen.findByTestId('comparison-summary');
  expect(screen.getByTestId('comparison-refresh')).toHaveTextContent('All results are final');
  expect(document.querySelector('.newc-kpi-cell.is-best')).toBeNull();
  expect(screen.queryByText('Results · preferred value highlighted')).toBeNull();
  expect(screen.getAllByText('Results · no measurements with an agreed ranking direction')).toHaveLength(2);
});
