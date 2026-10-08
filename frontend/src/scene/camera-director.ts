import * as T from 'three';
import type { EntityKey } from '../contracts/viewer-feed';
import type { FeedStore } from '../viewport/feed-store';
import { entityId } from '../viewport/bindings';
export type CameraMode = 'orbit' | 'follow' | 'chase' | 'cinematic';
/** Camera edits are display-only, event association uses typed subjects. */
export class CameraDirector {
  private shotSeconds = 0;
  private focus?: EntityKey;
  private phase = 0;
  choose(store: FeedStore, ns: string, delta: number, selected?: EntityKey): EntityKey | undefined {
    this.shotSeconds += delta;
    if (!this.focus || !store.entities.has(entityId(this.focus)) || this.shotSeconds > 7) {
      const now = BigInt(ns), recent = store.messages.filter(message => message.kind === 'event' && BigInt(message.at.ns) <= now && now - BigInt(message.at.ns) < 8_000_000_000n);
      const subjects = recent.flatMap(message => message.subjects ?? []).reverse();
      this.focus = subjects.find(key => store.entities.has(entityId(key))) ?? selected;
      this.shotSeconds = 0; this.phase++;
    }
    return this.focus;
  }
  offset() {
    const angle = this.phase * 1.8 + this.shotSeconds * 0.06;
    return new T.Vector3(Math.cos(angle) * 65, 35 + Math.sin(this.shotSeconds * 0.2) * 7, Math.sin(angle) * 65);
  }
}
