import { useSyncExternalStore, type ReactNode } from 'react';

/** Viewport ticks update the time-dependent surfaces without rendering the shell. */
export function createPlaybackUpdates() {
  let revision = 0;
  const listeners = new Set<() => void>();
  return {
    snapshot: () => revision,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    notify: () => { revision++; for (const listener of listeners) listener(); },
  };
}

export function PlaybackRegion({ updates, children }: {
  updates: ReturnType<typeof createPlaybackUpdates>; children: () => ReactNode;
}) {
  useSyncExternalStore(updates.subscribe, updates.snapshot);
  return <>{children()}</>;
}
