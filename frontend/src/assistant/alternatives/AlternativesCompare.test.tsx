import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AlternativesCompare } from './AlternativesCompare';
import type { AssistantApi, PlanVersion } from '../api';
import { I18nProvider, STORAGE_KEY, useI18n } from '../../i18n/I18nProvider';

afterEach(() => { cleanup(); localStorage.removeItem(STORAGE_KEY); });

const plan = (version: number, placeLabel: string, placeLabelZh: string): PlanVersion => ({
  version, created_at: '', note: '', variant: version === 5 ? 'A' : 'B', parent: null,
  base: {}, base_label: 'base', title: `Plan ${version}`, summary: `Summary ${version}`,
  operations: [], assumptions: [], open_questions: [],
  compile: { ok: true, errors: [], warnings: [] }, validation: { state: 'valid' },
  scene: { places: [{ id: 'pad-west', role: 'origin', label: placeLabel, label_zh: placeLabelZh }], fleets: [], workloads: [], events: [] },
  diff_previous: null,
}) as unknown as PlanVersion;

const api = {
  plan: vi.fn(async (_id: string, version: number) => version === 5 ? plan(5, 'West pad', '西发射台') : plan(6, 'West pad', '西发射台')),
} as unknown as AssistantApi;

it('marks which option is under review', async () => {
  render(<I18nProvider><AlternativesCompare api={api} sessionId="s1" versions={[5, 6]} current={5} onSelect={() => {}} onClose={() => {}} /></I18nProvider>);
  expect(await screen.findByTestId('as-alt-active-5')).toHaveTextContent(/under review/);
  expect(screen.queryByTestId('as-alt-active-6')).toBeNull();
  expect(screen.getByTestId('as-alt-5')).toHaveClass('is-current');
});

it('T2-AS-014: switches every generated shared/difference label while the comparison stays open',async()=>{
  const alternative=(version:number):PlanVersion=>({...plan(version,'West kitchen / 西侧厨房','西侧厨房'),
    scene:{places:[{id:'kitchen',role:'kitchen',label:'West kitchen',label_zh:'西侧厨房'}],
      fleets:[{id:'fleet',role:'meal_courier',label:'Meal courier',label_zh:'送餐无人机',count:2,members:[]}],
      workloads:[{id:'meal',kind:'food',label:'Meal order',label_zh:'外卖订单',count:6,from:['kitchen'],to:[]}],
      events:[{id:'gust',kind:'wind_gust',label:'Wind gust: 14 m/s',label_zh:'阵风：风速 14 米/秒',at_s:170}]},
    diff_previous:{added:[],changed:[{id:'fleet',kind:'fleet',label:'送餐无人机',label_zh:'送餐无人机'}],removed:[]},
  });
  const currentApi={plan:vi.fn(async(_id:string,version:number)=>alternative(version))} as unknown as AssistantApi;
  function Switch(){const {locale,setLocale}=useI18n();return <button onClick={()=>setLocale(locale==='en-US'?'zh-CN':'en-US')}>Switch locale</button>;}
  localStorage.setItem(STORAGE_KEY,'zh-CN');
  render(<I18nProvider><Switch/><AlternativesCompare api={currentApi} sessionId="s1" versions={[5,6]} current={5} onSelect={vi.fn()} onClose={vi.fn()}/></I18nProvider>);
  await screen.findByTestId('as-alt-5');
  const panel=screen.getByTestId('as-alternatives');
  expect(panel).toHaveTextContent('送餐无人机');expect(panel).toHaveTextContent('外卖订单');
  fireEvent.click(screen.getByText('Switch locale'));
  expect(panel).toHaveTextContent('Meal courier');expect(panel).toHaveTextContent('Meal order');expect(panel).toHaveTextContent('Wind gust: 14 m/s');
  expect(panel).not.toHaveTextContent(/送餐无人机|外卖订单|西侧厨房|阵风/);
  expect(currentApi.plan).toHaveBeenCalledTimes(2);
});

it('shows shared items in the current locale, not always in Chinese', async () => {
  const renderIn = (locale: string) => {
    localStorage.setItem(STORAGE_KEY, locale);
    return render(<I18nProvider><AlternativesCompare api={api} sessionId="s1" versions={[5, 6]} current={5} onSelect={() => {}} onClose={() => {}} /></I18nProvider>);
  };
  const en = renderIn('en-US');
  const shared = await en.findAllByText('West pad'); // shared list (1) + place labels come only via scenes; summary + common list
  expect(shared.length).toBeGreaterThanOrEqual(1);
  expect(en.queryAllByText('西发射台').length).toBe(0);
  en.unmount();
  const zh = renderIn('zh-CN');
  expect((await zh.findAllByText('西发射台')).length).toBeGreaterThanOrEqual(1);
  expect(zh.queryAllByText('West pad').length).toBe(0);
  const panel = zh.getByTestId('as-alternatives');
  expect(within(panel).getAllByText(/查看中|under review/).length).toBe(1);
});
