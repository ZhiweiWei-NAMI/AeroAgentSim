import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { SceneCards } from './SceneCards';
import type { SceneSummary } from './api';

afterEach(cleanup);

const base = {} as unknown as SceneSummary;

const renderCards = (scene: Partial<SceneSummary>) => render(
  <I18nProvider><SceneCards scene={{ ...base, ...scene } as SceneSummary} /></I18nProvider>,
);

it('shows a wind entry with a missing speed as “not set”, never 0 m/s', () => {
  renderCards({ environment: { wind: { gusts: [] } } });
  const env = screen.getByText(/wind/);
  expect(env.textContent).toMatch(/not set/);
  expect(env.textContent).not.toMatch(/0\s*m\/s|0 米\/秒/);
});

it('still renders an explicit numeric speed and keeps gusts untouched', () => {
  renderCards({ environment: { wind: { speed_mps: 14, gusts: [{ start_s: 0, end_s: 30, speed_mps: 14 }] } } });
  expect(screen.getByText(/14 m\/s wind/)).toBeTruthy();
  expect(screen.getByText(/gust 14 m\/s/)).toBeTruthy();
});

it('uses the Chinese wording for a missing speed', () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  renderCards({ environment: { wind: {} } });
  expect(screen.getByText(/风速未设置/)).toBeTruthy();
  expect(screen.queryByText(/米\/秒/)).toBeNull();
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
});

it.each(['en-US','zh-CN'])('T4-R2-015: shows wind limits as separate named values and retains the other rule values in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  renderCards({rules:[
    {id:'wind_limit',label:'Wind limit',label_zh:'风速上限',value:{parcel_limit_mps:12,medical_limit_mps:11,incident_response_limit_mps:14}},
    {id:'battery_reserve',label:'Battery reserve',label_zh:'保留电量',value:20,unit:'%'},
    {id:'safety_distance',label:'Safety distance',label_zh:'安全距离',value:30,unit:'m'},
  ],algorithms:{allocation:'nearest'}});
  const rules=screen.getByTestId('as-rules');
  const values=rules.querySelector('.as-rule-values')!;
  expect(values.children).toHaveLength(3);
  expect(values).toHaveTextContent('12');expect(values).toHaveTextContent('11');expect(values).toHaveTextContent('14');
  expect(values).toHaveTextContent(locale==='en-US'?'parcels limit (m/s)':'包裹风速上限（米/秒）');
  expect(rules).toHaveTextContent('20 %');expect(rules).toHaveTextContent('30 m');
  expect(rules).not.toHaveTextContent(/[{}]|parcel_limit_mps/);
  expect(within(rules).getByText(locale==='en-US'?'Nearest':'最近优先')).toBeVisible();
  localStorage.clear();
});
