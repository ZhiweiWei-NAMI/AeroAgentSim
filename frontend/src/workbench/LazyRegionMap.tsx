import { Suspense } from 'react';
import { lazyWithRetry } from '../lazyRetry';
import type { RegionMapProps } from './sections/space/RegionMap';

const Map = lazyWithRetry(() => import('./sections/space/RegionMap'), module => module.RegionMap);

/** Keep Leaflet out of forms until a map is actually mounted. */
export function RegionMap(props: RegionMapProps) {
  return <Suspense fallback={<div style={{ height: props.height ?? 300 }} />}><Map {...props} /></Suspense>;
}
