import type { EntityState } from '../viewport/feed-store';

/** Display labels only; retained identities and recorded values stay intact. */
export function readableLabel(value: string): string {
  const words = value.slice(value.lastIndexOf(':')+1).replace(/^traffic\.p\./, '').replace(/^traffic\./, '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[._/-]+/g, ' ').trim();
  return words ? words[0].toUpperCase() + words.slice(1) : value;
}
export function entityLabel(entity: EntityState): string {
  for (const id of ['name', 'label', 'display_name', 'traffic.actor.label']) {
    const value = entity.fields.get(id)?.value;
    if (typeof value === 'string' && value.trim()) return value;
  }
  return readableLabel(entity.key.id);
}
export function stateValue(value: unknown): string | undefined {
  if (value === null) return 'Null (recorded)';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value) && value.every(item => typeof item === 'number')) return value.map(item => String(item)).join(' · ');
  return undefined;
}
