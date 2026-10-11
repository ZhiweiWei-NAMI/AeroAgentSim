import type { FeedCommit } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { entityId } from './bindings';

class TimeIndex<T> {
  private rows: Array<{ at: bigint; value: T }> = [];
  private ordered = true;
  add(ns: string, value: T) {
    const at = BigInt(ns), previous = this.rows[this.rows.length - 1];
    if (previous && previous.at > at) this.ordered = false;
    this.rows.push({ at, value });
  }
  *between(start: bigint, end: bigint): Iterable<T> {
    if (!this.ordered) {
      this.rows.sort((a, b) => a.at < b.at ? -1 : a.at > b.at ? 1 : 0);
      this.ordered = true;
    }
    let low = 0, high = this.rows.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (this.rows[middle].at < start) low = middle + 1; else high = middle;
    }
    for (let i = low; i < this.rows.length && this.rows[i].at <= end; i++) yield this.rows[i].value;
  }
}

/** Index recorded timestamps once; scrubbing and fast playback query just the window. */
export class RecentActivity {
  private source?: FeedStore;
  private indexed = 0;
  private messageCount = 0;
  private messages = new TimeIndex<{ message: FeedCommit['messages'][number]; ordinal: number }>();
  private events = new TimeIndex<{ event: NonNullable<FeedCommit['events']>[number]; commitIndex: number }>();

  collect(store: FeedStore, ns: string, lookback: bigint, knownAt?: number) {
    if (store !== this.source) {
      this.source = store; this.indexed = this.messageCount = 0;
      this.messages = new TimeIndex(); this.events = new TimeIndex();
    }
    for (; this.indexed < store.commits.length; this.indexed++) {
      const commit = store.commits[this.indexed];
      for (const message of commit.messages) {
        const ordinal = this.messageCount++;
        if (message.kind === 'event') this.messages.add(message.at.ns, { message, ordinal });
      }
      for (const event of commit.events ?? []) this.events.add(event.at_ns, { event, commitIndex: commit.commitIndex });
    }
    const at = BigInt(ns), involved = new Set<string>(), alerts = new Set<string>();
    for (const { message, ordinal } of this.messages.between(at - lookback, at)) {
      // messages is the currently selected journal prefix. Display time can lag
      // that cut during live interpolation; it must not change availability.
      if (ordinal >= store.messages.length) continue;
      for (const subject of message.subjects ?? []) involved.add(entityId(subject));
    }
    let entities: Map<string, string[]> | undefined;
    for (const { event, commitIndex } of this.events.between(at - lookback, at)) {
      if (knownAt !== undefined && commitIndex > knownAt) continue;
      if (!entities) {
        entities = new Map();
        for (const [key, entity] of store.entities) {
          const keys = entities.get(entity.key.id);
          if (keys) keys.push(key); else entities.set(entity.key.id, [key]);
        }
      }
      for (const id of event.entities) for (const key of entities.get(id) ?? []) {
        involved.add(key);
        if (event.category === 'emergency' || event.severity === 'warning' || event.severity === 'critical') alerts.add(key);
      }
    }
    return { involved, alerts };
  }
}
