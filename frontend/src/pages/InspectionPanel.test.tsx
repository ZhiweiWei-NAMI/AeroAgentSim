import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureHeader, fixtureCommits, task } from '../behaviours/extension-fixture';
import { InspectionPanel } from './InspectionPanel';
import { timelineEvents } from './RunTimeline';
it('shows only the selected generation at the current cut, preserving missing truth and chain state',()=>{
 const store=new TemporalFeedStore(fixtureHeader);fixtureCommits.forEach(row=>store.ingest(row));store.seek('10',2);
 const view=render(<InspectionPanel store={store} selected={task} onSeek={()=>{}}/>);
 expect(screen.getByText('Missing input')).toBeTruthy();expect(screen.getByText('Waiting')).toBeTruthy();expect(screen.getByText('queued')).toBeTruthy();
 store.seek('10',3);view.rerender(<InspectionPanel store={store} selected={task} onSeek={()=>{}}/>);
 expect(screen.getByText('True')).toBeTruthy();expect(screen.getByText('Moving')).toBeTruthy();
 view.rerender(<InspectionPanel store={store} selected={{...task,generation:2}} onSeek={()=>{}}/>);
 expect(screen.getByText('Entity absent at this moment')).toBeTruthy();
});
it('marks committed event emissions and retains their exact microstep cut',()=>{
 const base=fixtureCommits[2];
 const message={id:'recorded',kind:'event' as const,schemaId:'traffic.capture.stored',source:'capture',at:base.at,payload:{}};
 expect(timelineEvents([{...base,messages:[message,{...message,id:'command',kind:'command'}]}])).toEqual([{label:'Capture',kind:'capture',offset:0,cut:3,ns:'10'}]);
});
