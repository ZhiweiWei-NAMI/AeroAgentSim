import type { PublicRunEvent } from "./trace";
import { channelOfEvent, MAX_LINES_PER_CHANNEL } from "./event-channels";

/** Bounded views of recorded events, indexed once instead of rescanning every tick. */
export class ReplayEvents {
  private readonly groups = new Map<string, PublicRunEvent[]>();
  constructor(events: readonly PublicRunEvent[]) {
    for (const event of events) {
      const channel = channelOfEvent(event);
      const group = this.groups.get(channel) ?? [];
      group.push(event);
      this.groups.set(channel, group);
    }
  }
  at(tick: number): PublicRunEvent[] {
    const result: PublicRunEvent[] = [];
    for (const events of this.groups.values()) {
      let low = 0, high = events.length;
      while (low < high) {
        const mid = (low + high) >>> 1;
        if (events[mid]!.at.tick <= tick) low = mid + 1;
        else high = mid;
      }
      result.push(...events.slice(Math.max(0, low - MAX_LINES_PER_CHANNEL), low));
    }
    return result.sort((left, right) => left.sequence - right.sequence);
  }
}
