import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import { translate } from '../i18n/translate';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureCommits, fixtureHeader, task } from '../behaviours/extension-fixture';
import { PlaybackClock } from '../viewport/clock';
import { InspectionPanel } from './InspectionPanel';
import { WaitingBanner } from './WaitingBanner';
import { displayLabel, localizedLabel, entityLabel, stateValue } from './inspection-format';

beforeEach(() => { localStorage.clear(); window.history.replaceState(null, '', '/'); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });
function SwitchLocale() { const { setLocale } = useI18n(); return <button onClick={() => setLocale('zh-CN')}>switch-zh</button>; }

it('uses Chinese descriptor parts and localizes compact values without changing IDs or precision', () => {
  expect(displayLabel('计算节点 / Compute node', 'oo:ComputeNode', 'zh-CN')).toBe('计算节点');
  expect(displayLabel('he.aircraft.position_enu_m', 'he.aircraft.position_enu_m', 'zh-CN')).toBe('位置（东 / 北 / 上）');
  expect(displayLabel('Position (east / north / up)', 'position', 'en-US')).toBe('Position (east / north / up)');
  expect(displayLabel('Position (east / north / up)', 'position', 'zh-CN')).toBe('位置（东 / 北 / 上）');
  expect(stateValue(false, 'zh-CN')).toBe('假');
  expect(stateValue(null, 'zh-CN')).toBe('空值（已记录）');
  expect(stateValue({$number:'84.73465076268933'}, 'zh-CN')).toBe('84.73');
  expect(stateValue('external.user.identifier', 'zh-CN')).toBe('external.user.identifier');
  expect(localizedLabel('Route vehicle 001','zh-CN')).toBe('路线车辆 001');
  expect(localizedLabel('Incident drone Alpha','zh-CN')).toBe('应急无人机 阿尔法');
  expect(localizedLabel('Drone 1','zh-CN')).toBe('无人机 1');
  const key={id:'uav.custom',generation:0};
  const entity={key,typeId:'lowalt:UAV',fields:new Map([['label',{...fixtureCommits[0].facts[0],entity:key,fieldId:'label',value:'An authored name'}],['label_zh',{...fixtureCommits[0].facts[0],entity:key,fieldId:'label_zh',value:'自定义名称'}]])};
  expect(entityLabel(entity,'zh-CN')).toBe('自定义名称');
  expect(entityLabel(entity,'en-US')).toBe('An authored name');
});

it('interpolates waiting guidance and uses a drawer callback instead of changing the hash', () => {
  localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  const inject = vi.fn();
  const view = render(<I18nProvider><WaitingBanner waiting={{stream_ids:['operator'],at_ns:'2500000000'}} injectionHref="#run-operations" onInject={inject}/></I18nProvider>);
  expect(screen.getByText('场景在 00:02.5 等待实时输入。你可以引入一个事件。')).toBeTruthy();
  const action = screen.getByRole('button', {name:translate('zh-CN','Introduce an event')});
  expect(action.tagName).toBe('BUTTON');
  fireEvent.click(action); expect(inject).toHaveBeenCalledOnce();
  view.rerender(<I18nProvider><WaitingBanner waiting={{stream_ids:['operator'],at_ns:'2500000000'}} terminal/></I18nProvider>);
  expect(screen.getByText('等待输入超时')).toBeTruthy();
  expect(screen.queryByRole('button', {name:translate('zh-CN','Introduce an event')})).toBeNull();
});

