import { Observable, type Unsubscribe } from "./observable";
import type { TraceTarget } from "./target";

/**
 * Camera intent. `focus` is a one-shot smooth fly-to; `follow` keeps framing
 * the target whenever a new authoritative snapshot changes its recorded
 * position. Only the camera moves smoothly; entities never interpolate.
 */
export class CameraState {
  private readonly focusRequest = new Observable<TraceTarget | null>(null);
  private readonly followTarget = new Observable<TraceTarget | null>(null);

  focus(target: TraceTarget): void {
    this.focusRequest.set(target);
  }

  subscribeFocus(listener: (target: TraceTarget | null) => void): Unsubscribe {
    return this.focusRequest.subscribe(listener);
  }

  follow(target: TraceTarget): void {
    this.followTarget.set(target);
    this.focusRequest.set(target);
  }

  releaseFollow(): void {
    this.followTarget.set(null);
  }

  followed(): TraceTarget | null {
    return this.followTarget.value;
  }

  isFollowing(target: TraceTarget): boolean {
    const followed = this.followTarget.value;
    return (
      followed !== null &&
      followed.kind === target.kind &&
      followed.id === target.id
    );
  }

  subscribeFollow(listener: (target: TraceTarget | null) => void): Unsubscribe {
    return this.followTarget.subscribe(listener);
  }
}
