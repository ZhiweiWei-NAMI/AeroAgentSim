import {expect,it} from 'vitest';
import {TemporalFeedStore} from '../feeds/temporal-store';
import {emptyCommit,fixtureHeader} from '../behaviours/extension-fixture';
import {LiveFollowClock} from './live-interpolation';

it('smooths actual pose samples while exact state, cuts and discontinuities stay committed',()=>{
 const header={...fixtureHeader,presentation:[{typeId:'fixture:Generic',positionField:'position',frame:'enu' as const,visual:{kind:'marker' as const}}]};
 const store=new TemporalFeedStore(header),key={id:'actor',generation:0,typeId:'fixture:Generic'};
 // Real header registry identity, rather than synthesized missing type support.
 header.types=[{typeId:key.typeId,displayName:'Actor',ancestors:[]}];
 const first=emptyCommit(10);first.created=[key];first.facts=[{entity:key,fieldId:'position',value:[0,0,0],producer:'motion',validFrom:first.at}];store.ingest(first);store.seek('0');
 const display=new LiveFollowClock();display.observe(store,true,1000);expect(display.renderNs('0',1000)).toBe('0');
 const next=emptyCommit(20);next.at={ns:'1000000000',microstep:0};next.facts=[{...first.facts[0],value:[10,0,0],validFrom:next.at}];store.ingest(next);
 // A later event-only commit in the same poll must not hide the pose update.
 const event=emptyCommit(21);event.at={ns:'1000000000',microstep:1};store.ingest(event);store.seek(event.at.ns);
 display.observe(store,true,2000);expect(display.renderNs(event.at.ns,2000)).toBe('0');
 const halfway=display.renderNs(event.at.ns,2500)!;expect(halfway).toBe('500000000');expect(store.sample(key,'position',halfway)).toEqual([5,0,0]);
 expect(store.entities.get('actor:0')?.fields.get('position')?.value).toEqual([10,0,0]);expect(store.viewCursor?.knownAt).toBe(21);
 expect(display.renderNs(event.at.ns,2400)).toBe(halfway);expect(display.renderNs(event.at.ns,4000)).toBe(event.at.ns);
 display.observe(store,false,4000);expect(display.renderNs(event.at.ns,4000)).toBeUndefined();display.observe(store,true,5000);expect(display.renderNs(event.at.ns,5000)).toBe(event.at.ns);
 const retract=emptyCommit(30);retract.at={ns:'2000000000',microstep:0};retract.retracted=[{entity:key,fieldId:'position'}];store.ingest(retract);store.seek(retract.at.ns);
 expect(store.sample(key,'position',retract.at.ns)).toBeUndefined();
});
