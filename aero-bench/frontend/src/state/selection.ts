import { Observable, type Unsubscribe } from "./observable";
import { sameTarget, type TraceTarget } from "./target";

/** The single selected trace target, or null when the canvas selection is empty. */
export class SelectionState {
  private readonly value = new Observable<TraceTarget | null>(null);

  get(): TraceTarget | null {
    return this.value.value;
  }

  isSelected(target: TraceTarget): boolean {
    return sameTarget(this.value.value, target);
  }

  select(target: TraceTarget): void {
    this.value.set(target);
  }

  clear(): void {
    this.value.set(null);
  }

  subscribe(listener: (target: TraceTarget | null) => void): Unsubscribe {
    return this.value.subscribe(listener);
  }
}
