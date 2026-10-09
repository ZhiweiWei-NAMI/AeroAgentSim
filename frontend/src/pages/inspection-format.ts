import type { EntityState } from '../viewport/feed-store';
import type { FieldInfo } from '../contracts/viewer-feed';

/** Display labels only; retained identities and recorded values stay intact. */
export function readableLabel(value: string): string {
  const words = value.slice(value.lastIndexOf(':')+1).replace(/^traffic\.p\./, '').replace(/^traffic\./, '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[._/-]+/g, ' ').trim();
  return words ? words.replace(/\b\w/g, letter => letter.toUpperCase()).replace(/\bUav\b/g, 'UAV').replace(/\bLlm\b/g, 'LLM').replace(/\bPx4\b/g, 'PX4') : value;
}
export function displayLabel(label: string | undefined, id: string): string {
  const english = label?.split('/').map(part => part.trim()).find(part => /[a-z]/i.test(part) && !/[\u3400-\u9fff]/.test(part));
  if (english && english !== id) return english;
  const field = id.replace(/^(?:he\.aircraft|traffic\.(?:uav|actor))\./, '');
  const labels: Record<string, string> = {position_enu_m:'Position (east / north / up)', velocity_enu_mps:'Velocity (east / north / up)', energy_j:'Energy', reserve_j:'Energy reserve', max_speed_mps:'Max speed', max_accel_mps2:'Max acceleration'};
  return Object.prototype.hasOwnProperty.call(labels, field) ? labels[field] : readableLabel(field);
}
export function displayUnit(field: FieldInfo | undefined): string | undefined {
  if (field?.unit) return field.unit === '1' ? undefined : field.unit;
  const schema = field?.schema;
  if (!schema || typeof schema !== 'object') return;
  const unit = (schema as {unit?:unknown}).unit;
  if (typeof unit === 'string') return unit === '1' ? undefined : unit;
  if (unit && typeof unit === 'object' && 'symbol' in unit && typeof unit.symbol === 'string') return unit.symbol === '1' ? undefined : unit.symbol;
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
  if (typeof value === 'boolean') return String(value);
  const number = (item: unknown): string | undefined => {
    const tagged = item !== null && typeof item === 'object' && '$number' in item;
    const raw = tagged ? (item as {$number:unknown}).$number : item;
    const numeric = typeof raw === 'number' ? raw : tagged && typeof raw === 'string' && raw.trim() ? Number(raw) : undefined;
    return numeric !== undefined && Number.isFinite(numeric) ? new Intl.NumberFormat('en-US', {maximumFractionDigits:2}).format(numeric) : undefined;
  };
  if (typeof value === 'number' || value && typeof value === 'object' && '$number' in value) return number(value);
  if (Array.isArray(value) && value.every(item => number(item) !== undefined)) return value.map(number).join(' · ');
  return undefined;
}
