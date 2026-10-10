import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { ChangeList } from './EntityStory';
import { changesFor, explain, momentsFor } from './cause-trace';
import { car, incident, traceStore } from './trace-fixture';
import { I18nProvider } from '../i18n/I18nProvider';

afterEach(() => { cleanup(); localStorage.clear(); });

it('recalculates visible changes when seeking backwards without exposing later continuous updates', () => {
  const store=traceStore();
  const view=render(<ChangeList store={store} entity={incident} onOpen={()=>{}}/>);
  expect(screen.getByText(/by Demo Alert/)).toBeTruthy();
  store.seek('0',1);
  view.rerender(<ChangeList store={store} entity={incident} onOpen={()=>{}}/>);
  expect(screen.queryByText(/by Demo Alert/)).toBeNull();
  expect(screen.getByText('false')).toBeTruthy();
  const future=store.commits[1];
  for(let i=6;i<=32;i++) store.ingest({...future,commitIndex:i,at:{ns:String(i*1e9),microstep:0},facts:[{...future.facts[0],value:i,validFrom:{ns:String(i*1e9),microstep:0}}]});
  store.seek('0',1);
  const atStart=changesFor(store,car);
  expect(atStart.continuous).toEqual([]);
  expect(atStart.rows).toHaveLength(1);
  store.seek('32000000000',32);
  expect(changesFor(store,car).continuous[0].updates).toBe(29);
});

it('shows Chinese backend event text while its cause pointer preserves original text and exact cut', () => {
  const store=traceStore();
  const event=store.commits[3].events![0];
  Object.assign(event,{text_zh:'车辆已停止'});
  const moment=momentsFor(store,incident,'zh-CN').find(row=>row.kind==='event')!;
  expect(moment.label).toBe('车辆已停止');
  expect(moment.detail).toBe('事件');
  expect(moment.ref).toMatchObject({kind:'event',text:'Car stopped',cut:4});
  expect(explain(store,moment.ref,'zh-CN')?.cut).toBe(4);
  delete (event as typeof event & {text_zh?:string}).text_zh;
});

it('localizes changes and writer attribution in the selected locale', () => {
  localStorage.setItem('aeroagentsim.locale','zh-CN');
  render(<I18nProvider><ChangeList store={traceStore()} entity={incident} onOpen={()=>{}}/></I18nProvider>);
  expect(screen.getAllByText('活动状态')).toHaveLength(2);
  expect(screen.getByText('由事件链写入')).toBeTruthy();
  expect(screen.getByText(/由Demo Alert（步骤“raise”）写入/)).toBeTruthy();
});
