import {describe,expect,it} from 'vitest';
import {BusinessDirector,MIN_DIRECTOR_CUT_MS,directorTarget,notableEvent} from './business-director';
import type {RecordedBusinessEvent} from '../newcomers/projection';
import {TemporalFeedStore} from '../feeds/temporal-store';
import {emptyCommit,fixtureHeader,fixtureCommits,vehicle} from '../behaviours/extension-fixture';
const event=(key:string,changes:Partial<RecordedBusinessEvent>={}):RecordedBusinessEvent=>({key,cut:1,offset:0,at_ns:'0',text:key,entities:[],severity:'notice',category:'logistics',position:[1,2,3],...changes});
describe('business Director scheduling',()=>{
  it('tours notable events in order and holds shots for seconds regardless of simulation speed',()=>{
    const director=new BusinessDirector(),events=[event('a'),event('b'),event('c')];
    expect(director.tick(events,0,true,'0',1)?.key).toBe('a');
    expect(director.tick(events,MIN_DIRECTOR_CUT_MS-1,true,'8000000000',1)).toBeUndefined();
    expect(director.tick(events,4000,true,'16000000000',1)).toBeUndefined();
    expect(director.tick(events,6000,true,'48000000000',1)?.key).toBe('b');
    expect(director.tick(events,12000,true,'96000000000',1)?.key).toBe('c');
    expect(director.tick(events,18000,true,'96000000000',1)).toBeUndefined();
  });
  it('never cuts on interaction, waits for explicit resume and respects inactive playback',()=>{
    const director=new BusinessDirector(),events=[event('a'),event('b')];
    director.tick(events,0,true,'0',1);director.pauseOnInteraction();
    expect(director.interactionPaused).toBe(true);
    expect(director.tick(events,9000,true,'0',1)).toBeUndefined();
    director.resume();expect(director.tick(events,9000,false,'0',1)).toBeUndefined();
    expect(director.tick(events,9001,true,'0',1)?.key).toBe('b');
  });
  it('does not bypass rate limits on rewind or read events beyond an exact cut',()=>{
    const director=new BusinessDirector(),events=[event('a'),event('future',{cut:2})];
    expect(director.tick(events,0,true,'0',1)?.key).toBe('a');
    expect(director.tick(events,1000,true,'1000000000',2)).toBeUndefined();
    expect(director.tick(events,2000,true,'0',1)).toBeUndefined();
    expect(director.tick(events,4000,true,'0',1)?.key).toBe('a');
    expect(director.tick([event('later',{at_ns:'2000000000',cut:3})],12000,true,'0',3)).toBeUndefined();
  });
  it('includes all specified severities/categories, ignores routine info and bounds burst latency',()=>{
    expect(notableEvent(event('routine',{severity:'info'}))).toBe(false);
    for(const category of ['energy','airspace','weather','emergency'] as const)expect(notableEvent(event(category,{category,severity:'info'}))).toBe(true);
    for(const severity of ['notice','warning','critical'] as const)expect(notableEvent(event(severity,{severity}))).toBe(true);
    const director=new BusinessDirector();
    expect(director.tick(Array.from({length:20},(_,i)=>event(String(i))),0,true,'0',1)?.key).toBe('8');
  });
  it('resolves an involved spatial entity, falls back to the recorded position, and ignores nonspatial cues',()=>{
    const store=new TemporalFeedStore(fixtureHeader);store.ingest(fixtureCommits[0]);store.seek('0');
    expect(directorTarget(store,event('actor',{entities:[vehicle.id]}))?.entity).toEqual(vehicle);
    expect(directorTarget(store,event('position'))?.position).toEqual([1,2,3]);
    expect(directorTarget(store,event('none',{position:undefined}))).toBeUndefined();
    store.ingest(emptyCommit(2));
  });
});
