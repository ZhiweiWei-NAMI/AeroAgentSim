export type Unsubscribe = () => void;

/** Minimal observable value: subscribers are replayed the current value on subscribe. */
export class Observable<T> {
  private current: T;
  private readonly listeners = new Set<(value: T) => void>();

  constructor(initial: T) {
    this.current = initial;
  }

  get value(): T {
    return this.current;
  }

  set(value: T): void {
    this.current = value;
    for (const listener of [...this.listeners]) {
      listener(value);
    }
  }

  subscribe(listener: (value: T) => void): Unsubscribe {
    this.listeners.add(listener);
    listener(this.current);
    return () => {
      this.listeners.delete(listener);
    };
  }
}
