/**
 * In-memory cache for AeroGraph explorer payloads.
 *
 * Keyed by API base + requested workspace id, so payloads are never mixed
 * across backends or workspaces. Only successfully fetched payloads are
 * stored — errors must always surface to the operator, never be masked by a
 * cached "success". Entries live for the SPA session (surviving route
 * remounts) and are intentionally not persisted to storage, so a page reload
 * re-reads the backend instead of showing stale catalog data forever.
 */

export type AeroGraphCacheKey = string;

const store = new Map<AeroGraphCacheKey, unknown>();

export function aeroGraphCacheKey(base: string, workspaceId?: string): AeroGraphCacheKey {
  return `${base}::${workspaceId ?? ''}`;
}

/** Returns the cached payload for the key, or undefined on a miss. */
export function readAeroGraphCache(key: AeroGraphCacheKey): unknown {
  return store.get(key);
}

export function writeAeroGraphCache(key: AeroGraphCacheKey, payload: unknown): void {
  store.delete(key);
  if(store.size >= 16) store.delete(store.keys().next().value!);
  store.set(key, payload);
}

/** Test hook: the module instance is shared across a vitest file. */
export function clearAeroGraphCacheForTests(): void {
  store.clear();
}
