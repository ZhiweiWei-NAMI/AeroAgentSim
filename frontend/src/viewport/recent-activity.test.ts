import { expect, it } from 'vitest';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { fixtureHeader, at, emptyCommit } from '../behaviours/extension-fixture';
import { RecentActivity } from './recent-activity';
import { recentlyInvolved } from './semantic';

it('matches the full activity scan for rewinds, late timestamps, knowledge cuts and display interpolation', () => {
  const header={...fixtureHeader,types:[{typeId:'actor',displayName:'Actor',ancestors:[]}]};
  const store=new TemporalFeedStore(header),index=new RecentActivity();
  const actors=Array.from({length:8},(_,i)=>({id:`actor-${i%4}`,generation:Math.floor(i/4)}));
  const expectedAt=(display:string,known:number)=>{
    const involved=recentlyInvolved(store.messages,300,display),alerts=new Set<string>();
    for(const commit of store.commits){
      if(commit.commitIndex>known)break;
      for(const event of commit.events??[]){
        const age=BigInt(display)-BigInt(event.at_ns);if(age<0n||age>300n)continue;
        for(const [key,entity] of store.entities)if(event.entities.includes(entity.key.id)){
          involved.add(key);
          if(event.category==='emergency'||event.severity==='warning'||event.severity==='critical')alerts.add(key);
        }
      }
    }
    return {involved,alerts};
  };
  for(let i=1;i<=50;i++){
    const ns=String(i*100),eventNs=String(i%3===0?i*40:i*100);
    store.ingest({...emptyCommit(i,ns),created:i===1?actors.map(key=>({...key,typeId:'actor'})):[],removed:i===30?[actors[7]]:[],
      messages:[{id:String(i),kind:i%4===0?'command':'event',schemaId:'activity',source:'engine',at:at(eventNs),payload:{},subjects:[actors[i%8]]}],
      events:[{at_ns:eventNs,category:'emergency',severity:'warning',text:'Recorded incident',entities:[actors[(i+3)%8].id]}]});
    store.seek(ns);
    expect(index.collect(store,ns,300n,i)).toEqual(expectedAt(ns,i));
  }
  for(const [view,known,display] of [['3000',30,'2750'],['1000',10,'1000'],['4800',48,'4800'],['2000',5,'2000'],['2000',40,'1950']] as const){
    store.seek(view,known);
    expect(index.collect(store,display,300n,known)).toEqual(expectedAt(display,known));
  }
  const other=new TemporalFeedStore(header);other.seek('0');
  expect(index.collect(other,'0',300n)).toEqual({involved:new Set(),alerts:new Set()});
});
