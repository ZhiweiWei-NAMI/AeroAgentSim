import type { Diff, SceneSummary } from './api';
import type { OverlayMarker, OverlayRoute, PreviewOverlay, Vec3 } from '../studio/plan-overlay';

export type { OverlayMarker, OverlayRoute, PreviewOverlay, Vec3 };
type MarkerKind = OverlayMarker['kind'];
type ItemState = OverlayMarker['state'];

const vec = (value: unknown): Vec3 | undefined => Array.isArray(value) && value.length === 3 && value.every(item => typeof item === 'number' && Number.isFinite(item)) ? value as Vec3 : undefined;
const PLACE_KIND: Record<string, MarkerKind> = { kitchen: 'kitchen', restaurant: 'kitchen', customer: 'customer', vertiport: 'vertiport', depot: 'vertiport', charging_pad: 'charging', handoff_point: 'handoff', incident_site: 'incident' };

/** Positions the compiled draft really authors: site facts, UAV starts and fleet generator starts. */
export function scenarioPositions(scenario: Record<string, any> | undefined): Map<string, Vec3> {
  const out = new Map<string, Vec3>();
  for (const entity of scenario?.entities ?? []) {
    const facts = entity?.facts ?? {};
    const position = vec(facts['lowalt.site_position']) ?? vec(facts['lowalt.position']) ?? vec(facts['lowalt.incident_position']) ?? vec(facts['aas.studio.position_enu_m']);
    if (position && typeof entity.id === 'string') out.set(entity.id, position);
  }
  for (const generator of Object.values(scenario?.generators ?? {}) as any[]) {
    const starts = (generator?.starts ?? []).map(vec).filter(Boolean) as Vec3[];
    for (let index = 0; index < (generator?.count ?? 0) && starts.length; index++) out.set(`${generator.prefix}${index + 1}`, starts[index % starts.length]);
  }
  return out;
}

function states(diff: Diff | undefined) {
  const added = new Set([...(diff?.added ?? []).map(item => item.id), ...(diff?.entities?.added ?? [])]);
  const changed = new Set([...(diff?.changed ?? []).map(item => item.id), ...(diff?.entities?.changed ?? [])]);
  return (id: string, members: string[] = []): ItemState => added.has(id) || members.some(member => added.has(member)) ? 'new' : changed.has(id) || members.some(member => changed.has(member)) ? 'changed' : 'existing';
}

/** Everything the plan names that has a place in the city, plus the flows between them. */
export function planOverlay(scene: SceneSummary | undefined, scenario: Record<string, any> | undefined, diff: Diff | undefined, tone: PreviewOverlay['tone'], zh: boolean): PreviewOverlay {
  const positions = scenarioPositions(scenario), state = states(diff);
  const markers: OverlayMarker[] = [], routes: OverlayRoute[] = [], at = new Map<string, Vec3>();
  const label = (row: { label?: string; label_zh?: string }, fallback: string) => (zh ? row.label_zh : undefined) ?? row.label ?? fallback;
  for (const place of scene?.places ?? []) {
    const position = vec(place.position) ?? positions.get(place.id);
    if (!position) continue;
    at.set(place.id, position);
    markers.push({ id: place.id, position, label: label(place, place.id), kind: PLACE_KIND[place.role] ?? 'place', state: state(place.id) });
  }
  const cooperation = scene?.cooperation ?? undefined;
  for (const point of cooperation?.handoff_points ?? []) {
    const position = vec(point.position) ?? positions.get(point.id);
    if (!position || at.has(point.id)) continue;
    at.set(point.id, position);
    markers.push({ id: point.id, position, label: label(point, point.id), kind: 'handoff', state: state(point.id) });
  }
  for (const incident of cooperation?.incidents ?? []) {
    const position = vec(incident.position) ?? positions.get(incident.id);
    if (!position) continue;
    at.set(incident.id, position);
    markers.push({ id: incident.id, position, label: label(incident, incident.id), kind: 'incident', state: state(incident.id) });
  }
  const fleetOf = new Map<string, string>();
  for (const fleet of scene?.fleets ?? []) {
    for (const member of fleet.members ?? []) fleetOf.set(member, fleet.id);
    const position = (fleet.start?.place ? at.get(fleet.start.place) ?? positions.get(fleet.start.place) : undefined) ?? fleet.members.map(member => positions.get(member)).find(Boolean);
    if (!position) continue;
    at.set(fleet.id, position);
    const name = label(fleet, fleet.id);
    markers.push({ id: fleet.id, position, label: fleet.count > 1 ? `${fleet.count} × ${name}` : name, kind: 'fleet', state: state(fleet.id, fleet.members) });
  }
  const seen = new Set<string>();
  const route = (id: string, from: string | undefined, to: string | undefined, kind: OverlayRoute['kind'], status: OverlayRoute['state']) => {
    const a = from ? at.get(from) : undefined, b = to ? at.get(to) : undefined;
    if (!a || !b || from === to || seen.has(`${from}>${to}>${kind}`)) return;
    if (Math.hypot(a[0] - b[0], a[1] - b[1]) < 1) return;
    seen.add(`${from}>${to}>${kind}`); routes.push({ id, from: a, to: b, kind, state: status });
  };
  for (const workload of scene?.workloads ?? []) {
    const status = state(workload.id, workload.tasks) === 'existing' ? 'existing' : state(workload.id, workload.tasks) as OverlayRoute['state'];
    let count = 0;
    for (const origin of workload.from) for (const destination of workload.to) if (count++ < 6) route(`${workload.id}:${origin}>${destination}`, origin, destination, 'task', status);
  }
  if (cooperation) {
    const fleetsOf = (vehicles: string[] | undefined) => [...new Set((vehicles ?? []).map(vehicle => fleetOf.get(vehicle) ?? vehicle))];
    const watchers = fleetsOf(cooperation.watchers?.vehicles), helpers = fleetsOf(cooperation.helpers?.vehicles);
    const status: OverlayRoute['state'] = state('cooperation') === 'existing' ? 'existing' : state('cooperation') as OverlayRoute['state'];
    for (const incident of cooperation.incidents ?? []) for (const watcher of watchers) route(`notice:${watcher}>${incident.id}`, watcher, incident.id, 'response', status);
    for (const point of cooperation.handoff_points ?? []) {
      for (const watcher of watchers) route(`drop:${watcher}>${point.id}`, watcher, point.id, 'handoff', status);
      for (const helper of helpers) route(`pick:${helper}>${point.id}`, helper, point.id, 'handoff', status);
    }
  }
  return { tone, markers, routes };
}

/** Marker ids an element of the plan corresponds to (hover a card → light it in 3D). */
export function markerIds(scene: SceneSummary | undefined, id: string): string[] {
  const fleet = scene?.fleets?.find(row => row.id === id || row.members.includes(id));
  return fleet ? [fleet.id] : [id];
}

/** Reveal order after Confirm: places first, then fleets, then incidents — routes follow their ends. */
export function revealOrder(overlay: PreviewOverlay): string[] {
  const rank: Record<MarkerKind, number> = { vertiport: 0, kitchen: 1, customer: 2, charging: 3, place: 3, handoff: 4, fleet: 5, vehicle: 5, incident: 6 };
  return overlay.markers.filter(marker => marker.state === 'new' || marker.state === 'changed').sort((a, b) => rank[a.kind] - rank[b.kind]).map(marker => marker.id)
    .concat(overlay.routes.filter(route => route.state !== 'existing').map(route => route.id));
}
