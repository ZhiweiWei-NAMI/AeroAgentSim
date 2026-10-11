import { describe, it, expect } from 'vitest';
import { TemporalFeedStore as FeedStore } from './temporal-store';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { truth, instance } from '../behaviours/extension-fixture';
const at = (ns: string) => ({ ns, microstep: 0 });
const key = { id: 'restriction', generation: 0 };
const header: RunHeader = { contract: 'aeroagentsim.viewer-feed/v1', runId: 'run', registryDigest: 'digest', start: at('0'), types: [{typeId:'regulation',displayName:'regulation',ancestors:[]}], fields: [{fieldId:'status',displayName:'status',valueType:'string'}], presentation: [] };
const commit = (index: number, ns = '0'): FeedCommit => ({commitIndex:index,at:at(ns),created:[],removed:[],facts:[],retracted:[],edges:[],messages:[],receipts:[]});
describe('H2 temporal cut counterexamples', () => {
  it('does not display a future or expired regulatory fact', () => {
    const store = new FeedStore(header), first = commit(1); first.created = [{...key,typeId:'regulation'}]; first.facts=[{entity:key,fieldId:'status',value:'active',producer:'authority',validFrom:at('10000000'),validTo:at('15000000')}]; store.ingest(first);
    for(const [ns,present] of [['1000000',false],['10000000',true],['15000000',false],['20000000',false]] as const) {store.seek(ns);expect(store.entities.get('restriction:0')?.fields.has('status')).toBe(present);}
  });
  it('retractions shadow only their intervals and knowledge prefixes', () => {
    const store = new FeedStore(header), first=commit(1);first.created=[{...key,typeId:'regulation'}];first.facts=[{entity:key,fieldId:'status',value:'active',producer:'authority',validFrom:at('0'),validTo:null}];store.ingest(first);
    const second=commit(2,'1');second.retracted=[{entity:key,fieldId:'status',validFrom:at('10'),validTo:at('15')}];store.ingest(second);
    store.seek('12',1);expect(store.entities.get('restriction:0')?.fields.get('status')?.value).toBe('active');
    store.seek('12',2);expect(store.entities.get('restriction:0')?.fields.has('status')).toBe(false);
    store.seek('20',2);expect(store.entities.get('restriction:0')?.fields.get('status')?.value).toBe('active');
  });
});

it('backdated facts require their publication knowledge cut', () => {
  const store=new FeedStore(header), first=commit(1);first.created=[{...key,typeId:'regulation'}];store.ingest(first);
  const late=commit(2,'20');late.facts=[{entity:key,fieldId:'status',value:'active',producer:'authority',validFrom:at('10'),validTo:at('15')}];store.ingest(late);
  store.seek('12',1);expect(store.entities.get('restriction:0')?.fields.has('status')).toBe(false);
  store.seek('12',2);expect(store.entities.get('restriction:0')?.fields.get('status')?.value).toBe('active');
});
it('edge closure changes the recorded interval and cancellation removes it at its knowledge cut',()=>{
  const store=new FeedStore(header), first=commit(1);first.created=[{...key,typeId:'regulation'}];first.edges=[{edgeId:'restriction/self',relationId:'applies',source:key,target:key,op:'assert',validFrom:at('10'),validTo:at('15')}];store.ingest(first);
  const close=commit(2,'1');close.edges=[{...first.edges[0],op:'close',validTo:at('12')}];store.ingest(close);
  const cancel=commit(3,'2');cancel.edges=[{...first.edges[0],op:'cancel',validFrom:null,validTo:null}];store.ingest(cancel);
  store.seek('13',1);expect(store.edges.size).toBe(1);store.seek('13',2);expect(store.edges.size).toBe(0);
  store.seek('11',2);expect(store.edges.size).toBe(1);store.seek('11',3);expect(store.edges.size).toBe(0);
});

it('indexed fields match greatest-known interval precedence while scrubbing arbitrary knowledge cuts', () => {
  const store=new FeedStore(header),first=commit(1);first.created=[{...key,typeId:'regulation'}];store.ingest(first);
  let seed=17;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed;};
  for(let i=2;i<=180;i++){
    const row=commit(i,String(i)),start=String(random()%190),validFrom={ns:start,microstep:random()%3};
    const validity={validFrom,validTo:random()%3 ? null : at(String(Number(start)+1+random()%15))};
    const fieldId=`field-${random()%3}`;
    if(random()%4===0)row.retracted=[{entity:key,fieldId,...validity}];
    else row.facts=[{entity:key,fieldId,value:i,producer:'authority',...validity}];
    store.ingest(row);
  }
  for(let i=0;i<180;i++){
    const ns=String(1+random()%195),known=1+random()%180;store.seek(ns,known);
    const cursor=store.viewCursor!.validAt;
    const before=(a:typeof cursor,b:typeof cursor)=>BigInt(a.ns)<BigInt(b.ns)||a.ns===b.ns&&a.microstep<=b.microstep;
    for(let slot=0;slot<3;slot++){
      const fieldId=`field-${slot}`;
      const winner=[...store.fieldVersionsOf('restriction:0',fieldId)].reverse().find(row=>row.commit.commitIndex<=known&&before(row.value.validFrom??row.commit.at,cursor)&&(!row.value.validTo||!before(row.value.validTo,cursor)));
      expect(store.entities.get('restriction:0')!.fields.get(fieldId)).toEqual(winner&&!winner.retract?winner.value:undefined);
    }
  }
});

it('keeps monotone samples, same-time item precedence and discontinuities exact with future history retained',()=>{
  const store=new FeedStore(header);
  for(let i=0;i<300;i++){
    const row=commit(i+1,String(i*10));if(i===0)row.created=[{...key,typeId:'regulation'}];
    row.facts=[{entity:key,fieldId:'position',value:[i,0,0],producer:'authority',validFrom:row.at}];store.ingest(row);
  }
  for(const index of [100,2,250,1,299,10]){
    store.seek(String(index*10));expect(store.sample(key,'position',String(index*10+5))).toEqual([index===299?299:index+.5,0,0]);
    expect(store.entities.get('restriction:0')?.fields.get('position')?.value).toEqual([index,0,0]);
  }
  const correction=commit(301,'2990');correction.retracted=[{entity:key,fieldId:'position',validFrom:at('2990'),version:{journalIndex:301,itemOrdinal:0}}];
  correction.facts=[{entity:key,fieldId:'position',value:[999,0,0],producer:'authority',validFrom:at('2990'),version:{journalIndex:301,itemOrdinal:1}}];store.ingest(correction);
  store.seek('2990',300);expect(store.sample(key,'position','2990')).toEqual([299,0,0]);
  store.seek('2990',301);expect(store.sample(key,'position','2990')).toEqual([999,0,0]);
});

it('crosses relation and behaviour validity boundaries at a fixed knowledge cut, including rewinds',()=>{
  const store=new FeedStore(header),first=commit(1);
  first.created=[{...key,typeId:'regulation'}];
  first.facts=[{entity:key,fieldId:'position',value:[0,0,0],producer:'motion',validFrom:at('0')}];
  first.edges=[{edgeId:'self',relationId:'applies',source:key,target:key,op:'assert',validFrom:at('10'),validTo:at('20')}];
  first.predicateTruth=[{...truth,validFrom:at('30'),validTo:at('40')}];
  first.chainInstances=[{...instance,validFrom:at('50'),validTo:at('60')}];
  store.ingest(first);
  const next=commit(2,'100');next.facts=[{...first.facts[0],value:[100,0,0],validFrom:next.at}];store.ingest(next);
  for(const ns of ['1','2','9','10','11','20','30','31','40','50','51','60','59','49','39','29','19','9']){
    store.seek(ns,1);
    const n=Number(ns);
    expect(store.edges.has('self')).toBe(n>=10&&n<20);
    expect(store.predicateTruth.has(truth.contextId)).toBe(n>=30&&n<40);
    expect(store.chainInstances.has(instance.instanceId)).toBe(n>=50&&n<60);
    expect(store.entities.get('restriction:0')?.fields.get('position')?.value).toEqual([0,0,0]);
    const sample=store.sample(key,'position',ns)!;
    expect(sample[0]).toBeCloseTo(n,10);expect(sample.slice(1)).toEqual([0,0]);
    expect(store.viewCursor?.validAt.ns).toBe(ns);
  }
});
