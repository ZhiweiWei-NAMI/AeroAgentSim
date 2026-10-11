import type { RunHeader } from '../contracts/viewer-feed';
import { resolveBinding } from './bindings';
import type { EntityState } from './feed-store';
import { worldPosition } from './coordinates';

/** Frame the committed scenario actors, rather than a fixed city camera or background traffic.
 * Coordinates come from committed position fields; this is only a display camera.
 */
export function initialActionFraming(header: RunHeader, entities: Iterable<EntityState>) {
  const points: number[][] = [];
  const roles = new Set<string>();
  for (const entity of entities) {
    const role = entity.fields.get('traffic.actor.role')?.value;
    const binding = resolveBinding(header, entity.typeId);
    const point = binding && entity.fields.get(binding.positionField)?.value;
    if (!binding || !Array.isArray(point) || point.length !== 3 || !point.every(value => typeof value === 'number' && Number.isFinite(value))) continue;
    const position = worldPosition(point, binding.frame, header.origin), enu = [position.x, -position.z, position.y];
    if (role !== 'background') points.push(enu);
    if (['reporter', 'incident', 'candidate'].includes(String(role))) roles.add(String(role));
  }
  // Wait for the actual main actors. Do not aim at a guessed origin or partial bootstrap.
  if (roles.size) {
    if (!roles.has('reporter') || !roles.has('incident') || !roles.has('candidate')) return;
  }
  // Shared-city runs also contain delivery, inspection and passenger actors.
  // Keep their real locations in the overview alongside the incident actors.
  if (!points.length) return;
  const lower = [0, 1, 2].map(axis => Math.min(...points.map(point => point[axis])));
  const upper = [0, 1, 2].map(axis => Math.max(...points.map(point => point[axis])));
  const center = lower.map((value, axis) => (value + upper[axis]) / 2) as [number, number, number];
  const radius = Math.max(25, ...points.map(point => Math.hypot(...point.map((value, axis) => value - center[axis]))));
  return { center, radius };
}
