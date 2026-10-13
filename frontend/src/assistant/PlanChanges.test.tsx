import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import type { Diff, PlanVersion } from './api';
import { DiffList, PlanChanges } from './PlanChanges';

afterEach(()=>{cleanup();localStorage.clear();});

it.each(['en-US','zh-CN'])('T2-R3-005/D11: uses exact geographic display values and localized server labels in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const diff:Diff={added:[],removed:[],changed:[{kind:'scene',kind_label:'Scene settings',kind_label_zh:'场景设置',id:'region',label:'Map region',label_zh:'地图区域',fields:[
    {name:'bounds',before:[121.479,31.225,121.484,31.229],after:[121.4794284,31.2258728,121.4845353,31.2298517],
      label:'Region bounds (°)',label_zh:'区域范围（度）',before_text:'121.479, 31.225, 121.484, 31.229 °',before_text_zh:'121.479, 31.225, 121.484, 31.229 度',
      after_text:'121.4794284, 31.2258728, 121.4845353, 31.2298517 °',after_text_zh:'121.4794284, 31.2258728, 121.4845353, 31.2298517 度'},
    {name:'origin',before:null,after:{lat:31.22786225,lon:121.48198185,alt:0},label:'Geographic origin',label_zh:'地理原点',before_text:'—',before_text_zh:'—',
      after_text:'Latitude: 31.22786225 °; Longitude: 121.48198185 °; Altitude: 0 m',after_text_zh:'纬度: 31.22786225 度; 经度: 121.48198185 度; 海拔: 0 米'}]}]};
  render(<I18nProvider><DiffList diff={diff} empty=""/></I18nProvider>);
  const row=screen.getByTestId('as-delta');
  expect(row).toHaveTextContent('121.4794284, 31.2258728, 121.4845353, 31.2298517');
  expect(row).toHaveTextContent('31.22786225');
  expect(row).toHaveTextContent('121.48198185');
  expect(row).toHaveTextContent(locale==='en-US'?'Region bounds (°)':'区域范围（度）');
  expect(row).not.toHaveTextContent(locale==='en-US'?'地理原点':'Geographic origin');
  expect(row.querySelector('s')?.textContent).toContain('121.479, 31.225, 121.484, 31.229');
  expect(row.textContent?.match(/→/g)).toHaveLength(2);
});

it('T2-R3-005: preserves six decimal places in operation parameters and legacy geographic diffs',()=>{
  localStorage.setItem('aeroagentsim.locale','en-US');
  const bounds=[121.4794284,31.2258728,121.4845353,31.2298517];
  const plan:PlanVersion={version:1,created_at:'',note:'',base:{},title:'Area',summary:'',assumptions:[],open_questions:[],
    operations:[{id:'region-op',op:'set_region',params:{bounds}}],compile:{ok:true,errors:[],warnings:[]},validation:{state:'valid'},
    diff_base:{added:[],removed:[],changed:[{kind:'scene',id:'region',label:'Map region',fields:[
      {name:'bounds',before:[121.479,31.225,121.484,31.229],after:bounds},
      {name:'origin',before:null,after:{lat:31.22786225,lon:121.48198185,alt:0}}]}]}};
  render(<I18nProvider><PlanChanges plan={plan}/></I18nProvider>);
  const diff=screen.getByTestId('as-delta');
  expect(diff).toHaveTextContent('121.479428 °, 31.225873 °, 121.484535 °, 31.229852 °');
  expect(diff).toHaveTextContent('31.227862 °');
  expect(diff).toHaveTextContent('121.481982 °');
  const operation=screen.getByTestId('as-op');
  expect(operation.querySelector('.as-op-args')).toHaveTextContent('121.479428 °, 31.225873 °, 121.484535 °, 31.229852 °');
});

it.each(['en-US','zh-CN'])('D11: displays the server algorithm name with one change arrow in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const diff:Diff={added:[],removed:[],changed:[{kind:'scene',id:'algorithms',label:'Decision methods',label_zh:'决策方法',fields:[
    {name:'allocation',before:'other',after:'nearest',label:'Allocation algorithm',label_zh:'分配算法',before_text:'Other method',before_text_zh:'其他方法',after_text:'Nearest',after_text_zh:'最近优先'}]}]};
  render(<I18nProvider><DiffList diff={diff} empty=""/></I18nProvider>);
  const row=screen.getByTestId('as-delta');
  expect(row.querySelector('dt')).toHaveTextContent(locale==='en-US'?'Allocation algorithm:':'分配算法:');
  expect(row.querySelector('dd')).toHaveTextContent(locale==='en-US'?'Other method → Nearest':'其他方法 → 最近优先');
  expect(within(row).queryByText('nearest')).toBeNull();
  expect(row.textContent?.match(/→/g)).toHaveLength(1);
});
