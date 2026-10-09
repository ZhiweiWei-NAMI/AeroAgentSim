import type { EntityKey, FeedCommit, Instant, RunHeader, PredicateTruth, ChainInstance, SelectionKey } from '../contracts/viewer-feed';
import { FeedStore } from '../viewport/feed-store';
import { entityId } from '../viewport/bindings';
type Fact = FeedCommit['facts'][number];
type Retraction = FeedCommit['retracted'][number];
type Edge = FeedCommit['edges'][number];
interface FieldVersion { commit: FeedCommit; value: Fact | Retraction; retract: boolean }
interface EdgeVersion { commit: FeedCommit; value: Edge }
interface BehaviourVersion<T> { commit: FeedCommit; value: T }
const before = (a: Instant, ns: bigint, microstep: number) => BigInt(a.ns) < ns || BigInt(a.ns) === ns && a.microstep <= microstep;
const contains = (start: Instant, end: Instant | null | undefined, ns: bigint, microstep: number) => before(start,ns,microstep) && (!end || !before(end,ns,microstep));

/** Exact physical-time views at a retained knowledge prefix; no simulation writes. */
export class TemporalFeedStore extends FeedStore {
  readonly predicateTruth = new Map<string, PredicateTruth>();
  readonly chainInstances = new Map<string, ChainInstance>();
  readonly chainHistory: Array<BehaviourVersion<ChainInstance>> = [];
  hasPredicateRecords = false;
  hasChainRecords = false;
  selection?: SelectionKey;
  liveFollow = false;
  viewCursor?: { validAt: Instant; knownAt: number };
  private predicateVersions = new Map<string, Array<BehaviourVersion<PredicateTruth>>>();
  private chainVersions = new Map<string, Array<BehaviourVersion<ChainInstance>>>();
  private versions = new Map<string, Map<string, FieldVersion[]>>();
  private edgeVersions = new Map<string, EdgeVersion[]>();
  private lives = new Map<string, {created:FeedCommit;removed?:FeedCommit}>();
  private indexedCommits = new Map<number, FeedCommit>();
  private selectedCut?: number;
  private previousSelection?: string;
  constructor(header: RunHeader) { super(header); }
  predicateRecords(contextId:string):ReadonlyArray<BehaviourVersion<PredicateTruth>> {return this.predicateVersions.get(contextId)??[];}
  chainRecords(instanceId: string): ReadonlyArray<BehaviourVersion<ChainInstance>> { return this.chainVersions.get(instanceId) ?? []; }
  select(key?: EntityKey) {
    if (!key) { this.selection = undefined; return; }
    if (this.header.epoch === undefined) throw Error('Selection epoch is unavailable; load the run manifest/header epoch');
    this.selection = { ...key, runId: this.header.runId, epoch: this.header.epoch };
  }
  override ingest(commit: FeedCommit) {
    super.ingest(commit);
    this.indexedCommits.set(commit.commitIndex,commit);
    for (const key of commit.created) this.lives.set(entityId(key),{created:commit});
    for (const key of commit.removed) this.lives.get(entityId(key))!.removed=commit;
    const items: FieldVersion[] = [...commit.retracted.map(value=>({commit,value,retract:true})),...commit.facts.map(value=>({commit,value,retract:false}))];
    items.sort((a,b)=>(a.value.version?.itemOrdinal ?? (a.retract?0:1))-(b.value.version?.itemOrdinal ?? (b.retract?0:1)));
    for(const version of items) {
      const id=entityId(version.value.entity); let fields=this.versions.get(id);
      if(!fields) {fields=new Map();this.versions.set(id,fields);}
      let rows=fields.get(version.value.fieldId);if(!rows){rows=[];fields.set(version.value.fieldId,rows);} rows.push(version);
    }
    for(const value of commit.edges) { let rows=this.edgeVersions.get(value.edgeId);if(!rows){rows=[];this.edgeVersions.set(value.edgeId,rows);}rows.push({commit,value}); }
    for (const value of commit.predicateTruth ?? []) {
      let rows = this.predicateVersions.get(value.contextId);
      if (!rows) { rows = []; this.predicateVersions.set(value.contextId, rows); }
      rows.push({ commit, value });
    }
    if (commit.predicateTruth?.length) this.hasPredicateRecords = true;
    if (commit.chainInstances?.length) this.hasChainRecords = true;
    for (const value of commit.chainInstances ?? []) {
      let rows = this.chainVersions.get(value.instanceId);
      if (!rows) { rows = []; this.chainVersions.set(value.instanceId, rows); }
      rows.push({ commit, value }); this.chainHistory.push({ commit, value });
    }
    this.previousSelection=undefined;
  }
  private cut(ns: bigint, requested?: number) {
    if(requested!==undefined) return requested;
    let low=0,high=this.commits.length;
    while(low<high){const middle=(low+high)>>>1;if(BigInt(this.commits[middle].at.ns)<=ns)low=middle+1;else high=middle;}
    return low ? this.commits[low-1].commitIndex : -1;
  }
  private microstep(ns: bigint, cut: number) {
    const last=this.indexedCommits.get(cut);
    return last && BigInt(last.at.ns)===ns ? last.at.microstep : Number.MAX_SAFE_INTEGER;
  }
  private effective(rows: FieldVersion[], ns: bigint, cut: number, microstep: number) {
    for(let index=rows.length-1;index>=0;index--){const row=rows[index];
      if(row.commit.commitIndex<=cut && contains(row.value.validFrom ?? row.commit.at,row.value.validTo,ns,microstep)) return row;
    }
    return undefined;
  }
  private behaviourAt<T extends { validFrom: Instant; validTo?: Instant | null }>(rows: Array<BehaviourVersion<T>>, ns: bigint, cut: number, microstep: number): T | undefined {
    // A close is a new knowledge version of an existing interval, never a retroactive mutation.
    const seen = new Set<string>();
    for (let i = rows.length - 1; i >= 0; i--) {
      const row = rows[i]; if (row.commit.commitIndex > cut) continue;
      const interval = `${row.value.validFrom.ns}/${row.value.validFrom.microstep}`;
      if (seen.has(interval)) continue; seen.add(interval);
      if (contains(row.value.validFrom, row.value.validTo, ns, microstep)) return row.value;
    }
    return undefined;
  }
  override seek(ns: string, commitIndex?: number) {
    super.seek(ns,commitIndex);this.selectedCut=commitIndex;
    const at=BigInt(ns),cut=this.cut(at,commitIndex),microstep=this.microstep(at,cut),selection=`${ns}/${cut}/${microstep}`;
    this.viewCursor = { validAt: { ns, microstep }, knownAt: cut };
    if(selection===this.previousSelection)return;
    for(const [id,entity] of this.entities){entity.fields.clear();for(const [slot,rows] of this.versions.get(id) ?? []){
      const row=this.effective(rows,at,cut,microstep);if(row && !row.retract)entity.fields.set(slot,row.value as Fact);
    }}
    this.edges.clear();
    for(const [id,rows] of this.edgeVersions){
      const row=[...rows].reverse().find(row=>row.commit.commitIndex<=cut);
      if(!row || row.value.op==='cancel' || !this.entities.has(entityId(row.value.source)) || !this.entities.has(entityId(row.value.target)))continue;
      if(row.value.validFrom===undefined && row.value.op==='close')continue; // legacy close has no interval row
      if(row.value.validFrom===null)continue;
      if(contains(row.value.validFrom ?? row.commit.at,row.value.validTo,at,microstep))this.edges.set(id,row.value);
    }
    this.predicateTruth.clear(); this.chainInstances.clear();
    for (const [id, rows] of this.predicateVersions) { const value = this.behaviourAt(rows, at, cut, microstep); if (value) this.predicateTruth.set(id, value); }
    for (const [id, rows] of this.chainVersions) { const value = this.behaviourAt(rows, at, cut, microstep); if (value) this.chainInstances.set(id, value); }
    this.previousSelection=selection;
  }
  override sample(key: EntityKey, fieldId: string, ns: string, quaternion=false): number[] | undefined {
    const at=BigInt(ns),cut=this.cut(at,this.selectedCut),microstep=this.microstep(at,cut),life=this.lives.get(entityId(key));
    if(!life || life.created.commitIndex>cut || !before(life.created.at,at,microstep) || life.removed && life.removed.commitIndex<=cut && before(life.removed.at,at,microstep))return undefined;
    const rows=this.versions.get(entityId(key))?.get(fieldId);if(!rows)return undefined;
    const row=this.effective(rows,at,cut,microstep);if(!row || row.retract)return undefined;
    const fact=row.value as Fact;
    if(!Array.isArray(fact.value) || !fact.value.every(value=>typeof value==='number' && Number.isFinite(value)))return undefined;
    // Ordinary continuously published motion can use existing display interpolation.
    // Finite/future/backdated versions remain exact and never bridge a validity gap.
    const ordinary=(version:FieldVersion)=>!version.retract && version.value.validTo==null && version.value.validFrom?.ns===version.commit.at.ns && version.value.validFrom.microstep===version.commit.at.microstep;
    const next=rows[rows.indexOf(row)+1];
    if(ordinary(row) && (!next || ordinary(next)))return super.sample(key,fieldId,ns,quaternion);
    return [...fact.value] as number[];
  }
}
