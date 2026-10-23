import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import type { Operation, OperationDescriptors, PlanVersion } from './api';
import { operationName, PlanSummary } from './OperationSummary';
import { PlanChanges } from './PlanChanges';
import summarySource from './OperationSummary.tsx?raw';
import changesSource from './PlanChanges.tsx?raw';

afterEach(()=>{cleanup();localStorage.clear();});

const descriptors: OperationDescriptors={api_version:1,families:[
  {name:'garden',operations:[{
    id:'garden.irrigate',title:{en:'Propose irrigation',zh:'提议灌溉'},
    summary:{en:'Configure a watering proposal, not an executed task.',zh:'配置灌溉提议，而非已执行的任务。'},
    parameters:{type:'object',properties:{}},prerequisites:[],
  }]},
  {name:'lowalt',operations:[{
    id:'lowalt.add_fleet',aliases:['add_fleet'],title:{en:'Provider-defined group title',zh:'操作族定义的机群标题'},
    summary:{en:'Provider-defined group summary.',zh:'操作族定义的机群说明。'},
    parameters:{type:'object',properties:{}},prerequisites:[],
  }]},
]};

function plan(operations: Operation[]=[{id:'watering',op:'garden.irrigate',params:{count:2}}]): PlanVersion {
  return {version:1,created_at:'',note:'',base:{},title:'Proposal',
    summary:operations.map(op=>`${op.op}: ${JSON.stringify(op.params)}`).join('; '),operations,
    assumptions:[],open_questions:[],compile:{ok:true,errors:[],warnings:[]},validation:{state:'valid'},
    diff_base:{added:[],changed:[],removed:[]},operationCopy:{descriptors}};
}

it.each(['en-US','zh-CN'])('renders a novel family title and summary in both summary and changes in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const proposed=plan(),copy=descriptors.families[0].operations[0],language=locale==='zh-CN'?'zh':'en';
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  for(const row of [screen.getByTestId('as-arrangement'),screen.getByTestId('as-op')]) {
    expect(row).toHaveTextContent(copy.title[language]);
    expect(row).toHaveTextContent(copy.summary![language]);
    expect(row).not.toHaveTextContent(copy.title[language==='en'?'zh':'en']);
  }
});

it.each(['en-US','zh-CN'])('uses supplied copy even for builtin IDs and their explicit legacy aliases in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const proposed=plan([{id:'canonical',op:'lowalt.add_fleet',params:{}},{id:'legacy',op:'add_fleet',params:{}}]);
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  const language=locale==='zh-CN'?'zh':'en',copy=descriptors.families[1].operations[0];
  expect(within(screen.getByTestId('as-arrangement')).getAllByText(copy.title[language])).toHaveLength(2);
  for(const row of screen.getAllByTestId('as-op')) {
    expect(row.querySelector('.as-op-title')).toHaveTextContent(copy.title[language]);
    expect(row).toHaveTextContent(copy.summary![language]);
  }
});

it.each(['en-US','zh-CN'])('keeps an unknown namespaced ID and an undeclared short name unknown in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const proposed=plan([{id:'future',op:'future.add_fleet',params:{}},{id:'short',op:'irrigate',params:{}}]);
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  const unknown=locale==='zh-CN'?'未知操作':'Unknown operation';
  for(const name of ['future.add_fleet','irrigate']) {
    expect(screen.getAllByText(`${unknown} · ${name}`)).toHaveLength(2);
  }
  expect(screen.queryByText('Provider-defined group title')).toBeNull();
  expect(screen.queryByText('操作族定义的机群标题')).toBeNull();
  expect(screen.queryByText('Propose irrigation')).toBeNull();
  expect(screen.queryByText('提议灌溉')).toBeNull();
});

it('has no builtin copy fallback when descriptors are missing',()=>{
  expect(operationName('add_fleet',(en)=>en)).toBe('Unknown operation · add_fleet');
  expect(operationName('add_fleet',(_en,zh)=>zh)).toBe('未知操作 · add_fleet');
});

function LocaleSwitch() {
  const {locale,setLocale}=useI18n();
  return <button onClick={()=>setLocale(locale==='zh-CN'?'en-US':'zh-CN')}>Switch locale / 切换语言</button>;
}

it('switches copy from the same descriptor snapshot when the active locale changes',()=>{
  localStorage.setItem('aeroagentsim.locale','en-US');
  const proposed=plan();
  render(<I18nProvider><LocaleSwitch/><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent('Propose irrigation');
  fireEvent.click(screen.getByRole('button',{name:'Switch locale / 切换语言'}));
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent('提议灌溉');
  expect(screen.getByTestId('as-op')).toHaveTextContent('配置灌溉提议，而非已执行的任务。');
  expect(proposed.operationCopy?.descriptors).toBe(descriptors);
});

it.each(['en-US','zh-CN'])('shows descriptor failures without hiding the proposal or inventing copy in %s',locale=>{
  localStorage.setItem('aeroagentsim.locale',locale);
  const proposed={...plan(),operationCopy:{error:'Family unavailable / 操作族不可用'}};
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  for(const alert of screen.getAllByRole('alert')) {
    expect(alert).toHaveTextContent(locale==='zh-CN'?'操作说明不可用':'Operation descriptions are unavailable');
    expect(alert).toHaveTextContent(locale==='zh-CN'?'操作族不可用':'Family unavailable');
  }
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent(`${locale==='zh-CN'?'未知操作':'Unknown operation'} · garden.irrigate`);
  expect(screen.getByTestId('as-op')).toHaveTextContent('garden.irrigate');
});

it('renders optional summaries as absent and descriptor strings as text, not markup',()=>{
  localStorage.setItem('aeroagentsim.locale','en-US');
  const proposed=plan(),copy=descriptors.families[0].operations[0];
  const {summary,...titleOnly}=copy;
  proposed.operationCopy={descriptors:{api_version:1,families:[{name:'garden',operations:[{
    ...titleOnly,title:{en:'<img src=x onerror=alert(1)>',zh:'<script>说明</script>'},
  }]}]}};
  const view=render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  expect(screen.getAllByText('<img src=x onerror=alert(1)>')).toHaveLength(2);
  expect(view.container.querySelector('img, script')).toBeNull();
  expect(screen.queryByText(copy.summary!.en)).toBeNull();
});

it('labels effective prerequisite operations without changing the authored recipes or approval state',()=>{
  localStorage.setItem('aeroagentsim.locale','en-US');
  const proposed=plan([{id:'group',op:'add_fleet',params:{count:2}}]);
  proposed.compile.operations=[{id:'auto',op:'add_lowalt_components',params:{}},...proposed.operations];
  proposed.operationCopy={descriptors:{...descriptors,families:descriptors.families.map(family=>family.name==='lowalt'
    ? {...family,operations:[...family.operations,{
      id:'lowalt.add_lowalt_components',aliases:['add_lowalt_components'],title:{en:'Descriptor setup title',zh:'说明中的准备标题'},
      summary:{en:'Proposed setup only.',zh:'仅提议准备。'},parameters:{type:'object'},prerequisites:[],
    }]} : family)}};
  render(<I18nProvider><PlanSummary plan={proposed}/><PlanChanges plan={proposed}/></I18nProvider>);
  expect(screen.getByTestId('as-arrangement')).toHaveTextContent('Descriptor setup title');
  expect(screen.getAllByTestId('as-op')[0]).toHaveTextContent('Proposed setup only.');
  expect(proposed.operations).toEqual([{id:'group',op:'add_fleet',params:{count:2}}]);
  expect(proposed.compile.ok).toBe(true);
  expect(proposed.validation.state).toBe('valid');
});

it('does not contain a hardcoded operation-label map in either operation-copy consumer',()=>{
  for(const source of [summarySource,changesSource]) {
    expect(source).not.toMatch(/\b(?:const|let)\s+OPS\b/);
    expect(source).not.toMatch(/\b(?:add|set|remove|update|schedule)_[a-z_]+\s*:\s*\[/);
    expect(source).not.toContain('Add a group of UAVs');
    expect(source).not.toContain('添加无人机编队');
  }
});
