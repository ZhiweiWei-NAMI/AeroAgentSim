import type { EntityKey, FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { entityId } from './bindings';
import { SampleBuffer } from './samples';
import { compareInstant, nanos } from './time';

type Fact = FeedCommit['facts'][number];
export interface EntityState { key: EntityKey; typeId: string; fields: Map<string, Fact> }
interface Lifetime { created: FeedCommit; removed?: FeedCommit }

/** Retained replay journal with a separate committed cut and display sample index. */
export class FeedStore {
  readonly commits: FeedCommit[] = [];
  readonly entities = new Map<string, EntityState>();
  readonly edges = new Map<string, FeedCommit['edges'][number]>();
  readonly messages: FeedCommit['messages'] = [];
  readonly receipts: FeedCommit['receipts'] = [];
  private buffers = new Map<string, Map<string, SampleBuffer>>();
  private lifetimes = new Map<string, Lifetime>();
  private ingestedEntities = new Set<string>();
  private cursor = -1;
  private cutNs?: bigint;
  private cutIndex?: number;
  constructor(readonly header: RunHeader) { nanos(header.start.ns); }
  ingest(commit: FeedCommit) {
    const previous = this.commits[this.commits.length - 1];
    if (!Number.isSafeInteger(commit.commitIndex) || commit.commitIndex < 0 || !Number.isSafeInteger(commit.at.microstep) || commit.at.microstep < 0) throw Error('Invalid commit index or microstep');
    nanos(commit.at.ns);
    if (previous && (commit.commitIndex <= previous.commitIndex || compareInstant(commit.at, previous.at) < 0)) throw Error('Feed commits must arrive in journal order');
    // Validate the complete lifecycle projection before mutating retained indexes.
    const active = new Set(this.ingestedEntities);
    for (const key of commit.created) {
      const id = entityId(key);
      if (active.has(id) || this.lifetimes.has(id)) throw Error(`Duplicate entity generation: ${id}`);
      if (!this.header.types.some(type => type.typeId === key.typeId)) throw Error(`Unknown registry type: ${key.typeId}`);
      active.add(id);
    }
    for (const fact of [...commit.facts, ...commit.retracted]) if (!active.has(entityId(fact.entity))) throw Error(`Fact refers to absent entity: ${entityId(fact.entity)}`);
    for (const edge of commit.edges) if (!active.has(entityId(edge.source)) || !active.has(entityId(edge.target))) throw Error(`Relation refers to absent entity: ${edge.edgeId}`);
    for (const key of commit.removed) {
      if (!active.delete(entityId(key))) throw Error(`Removal refers to absent entity: ${entityId(key)}`);
    }
    this.ingestedEntities = active;
    this.commits.push(commit);
    for (const key of commit.created) this.lifetimes.set(entityId(key), { created: commit });
    // Retraction followed by a fact in one transaction denotes a discontinuity.
    for (const item of commit.retracted) this.buffer(item.entity, item.fieldId).retract(commit.at.ns, commit.commitIndex);
    for (const fact of commit.facts) {
      const buffer = this.buffer(fact.entity, fact.fieldId);
      if (fact.discontinuity) buffer.retract(commit.at.ns, commit.commitIndex);
      buffer.push(commit.at.ns, commit.commitIndex, fact.value);
    }
    for (const key of commit.removed) this.lifetimes.get(entityId(key))!.removed = commit;
  }
  private buffer(key: EntityKey, fieldId: string) {
    const id = entityId(key);
    let fields = this.buffers.get(id);
    if (!fields) { fields = new Map(); this.buffers.set(id, fields); }
    let buffer = fields.get(fieldId);
    if (!buffer) { buffer = new SampleBuffer(); fields.set(fieldId, buffer); }
    return buffer;
  }
  seek(ns: string, commitIndex?: number) {
    const at = nanos(ns);
    let low = 0, high = this.commits.length;
    while (low < high) {
      const mid = (low + high) >>> 1, commit = this.commits[mid];
      if (nanos(commit.at.ns) <= at && (commitIndex === undefined || commit.commitIndex <= commitIndex)) low = mid + 1;
      else high = mid;
    }
    const target = low - 1;
    if (target < this.cursor) {
      this.entities.clear(); this.edges.clear(); this.messages.length = this.receipts.length = 0; this.cursor = -1;
    }
    for (let i = this.cursor + 1; i <= target; i++) this.apply(this.commits[i]);
    this.cursor = target; this.cutNs = at; this.cutIndex = commitIndex;
  }
  private apply(commit: FeedCommit) {
    for (const key of commit.created) this.entities.set(entityId(key), { key: { id: key.id, generation: key.generation }, typeId: key.typeId, fields: new Map() });
    for (const fact of commit.retracted) this.entities.get(entityId(fact.entity))!.fields.delete(fact.fieldId);
    for (const fact of commit.facts) this.entities.get(entityId(fact.entity))!.fields.set(fact.fieldId, fact);
    for (const edge of commit.edges) {
      if (edge.op === 'assert') this.edges.set(edge.edgeId, edge); else this.edges.delete(edge.edgeId);
    }
    for (const key of commit.removed) {
      const id = entityId(key); this.entities.delete(id);
      for (const [edgeId, edge] of this.edges) if (entityId(edge.source) === id || entityId(edge.target) === id) this.edges.delete(edgeId);
    }
    this.messages.push(...commit.messages); this.receipts.push(...commit.receipts);
  }
  sample(key: EntityKey, fieldId: string, ns: string, quaternion = false): number[] | undefined {
    const at = nanos(ns), life = this.lifetimes.get(entityId(key));
    const equalCut = this.cutNs === at ? this.cutIndex : undefined;
    const available = (commit: FeedCommit) => nanos(commit.at.ns) < at || (nanos(commit.at.ns) === at && (equalCut === undefined || commit.commitIndex <= equalCut));
    if (!life || !available(life.created) || (life.removed && available(life.removed))) return undefined;
    return this.buffers.get(entityId(key))?.get(fieldId)?.sample(ns, quaternion, equalCut);
  }
}
