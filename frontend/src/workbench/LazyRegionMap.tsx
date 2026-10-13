import { lazy, Suspense } from 'react';
import type { RegionMapProps } from './sections/space/RegionMap';

const Map = lazy(() => import('./sections/space/RegionMap').then(module => ({ default: module.RegionMap })));

/** Keep Leaflet out of forms until a map is actually mounted. */
export function RegionMap(props: RegionMapProps) {
  return <Suspense fallback={<div style={{ height: props.height ?? 300 }} />}><Map {...props} /></Suspense>;
}
