import type { FeedStore } from './feed-store';
import { nanos } from './time';
interface Arrival { ns: bigint; wall: number }

/** A display clock over actual pose arrivals; it never seeks or updates state. */
export class LiveFollowClock {
  private previous?: Arrival;
  private latest?: Arrival;
  private observed = 0;
  private display?: bigint;
  private active = false;
  private interval = 1;
  observe(store: FeedStore, active: boolean, wall = performance.now()) {
    if (!active) { this.active = false; return; }
    if (!this.active) {
      this.previous = this.latest = undefined; this.display = undefined;
      this.observed = 0; this.active = true;
    }
    // Polls can contain many event-only commits after the pose publication.
    // Record the last actual pose publication in the newly arrived batch.
    let pose: bigint | undefined;
    const fields = new Set(store.header.presentation.flatMap(row => [row.positionField, ...(row.orientationField ? [row.orientationField] : [])]));
    for (let index = this.observed; index < store.commits.length; index++) {
      const commit = store.commits[index];
      if (commit.facts.some(fact => fields.has(fact.fieldId)) || commit.retracted.some(fact => fields.has(fact.fieldId))) pose = nanos(commit.at.ns);
    }
    this.observed = store.commits.length;
    if (pose === undefined || pose === this.latest?.ns) return;
    if (this.latest && pose < this.latest.ns) { this.previous = this.latest = undefined; this.display = undefined; }
    this.previous = this.latest; this.latest = {ns:pose,wall};
    if (this.previous) this.interval = Math.max(1, wall - this.previous.wall);
  }
  renderNs(headNs: string, wall = performance.now()): string | undefined {
    if (!this.active || !this.latest) return undefined;
    let at = this.latest.ns;
    if (this.previous) {
      const alpha = Math.min(1, Math.max(0, (wall - this.latest.wall) / this.interval));
      at = this.previous.ns + (this.latest.ns - this.previous.ns) * BigInt(Math.round(alpha * 1_000_000)) / 1_000_000n;
    }
    if (this.display !== undefined && at < this.display) at = this.display;
    const head = nanos(headNs); if(at > head) at = head;
    this.display = at; return at.toString();
  }
}
