import { Observable, type Unsubscribe } from "./observable";
import { sameTarget, type TraceTarget } from "./target";

/** Transient pointer hover target; never affects selection or replay state. */
export class HoverState {
  private readonly value = new Observable<TraceTarget | null>(null);

  get(): TraceTarget | null {
    return this.value.value;
  }

  hover(target: TraceTarget): void {
    if (!sameTarget(this.get(), target)) this.value.set(target);
  }

  clear(): void {
    if (this.get() !== null) this.value.set(null);
  }

  subscribe(listener: (target: TraceTarget | null) => void): Unsubscribe {
    return this.value.subscribe(listener);
  }
}
