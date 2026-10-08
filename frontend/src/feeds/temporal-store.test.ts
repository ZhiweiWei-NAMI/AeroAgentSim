import { describe, it, expect } from 'vitest';
import { TemporalFeedStore as FeedStore } from './temporal-store';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
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
