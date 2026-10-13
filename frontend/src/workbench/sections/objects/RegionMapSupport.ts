/** Shared support for the region map: the ObjectsPanel fallback palette and
 *  instance-dot colouring, kept in one place so both sections agree on colours. */
import type { Scenario } from '../types';
import type { RegionMapInstance } from '../space/RegionMap';

/** The ObjectsPanel.tsx type-colour palette, mirrored here (spec 5 fallback palette). */
export const FALLBACK_PALETTE = ['#1f7ae0', '#e8a317', '#1fae6b', '#b26bff', '#e0453a', '#38c6f4', '#ff7a45', '#5b6b7d'];

/** Resolve an instance's colour from the draft presentation binding, else the palette fallback. */
export const paletteColorFor = (typeId: string, typeIndex: Map<string, number>): string => {
  const index = typeIndex.get(typeId);
  return index === undefined ? FALLBACK_PALETTE[0] : FALLBACK_PALETTE[index % FALLBACK_PALETTE.length];
};

const PLACE_MARKERS: Record<string, { color: string; en: string; zh: string }> = {
  'lowalt:Vertiport': { color: '#1f7ae0', en: 'V', zh: '港' },
  'lowalt:ChargingPad': { color: '#1fae6b', en: 'C', zh: '充' },
  'lowalt:Depot': { color: '#e8a317', en: 'D', zh: '仓' },
  'lowalt:Restaurant': { color: '#e0453a', en: 'K', zh: '餐' },
  'lowalt:Customer': { color: '#b26bff', en: 'C', zh: '客' },
  'lowalt:HandoffPoint': { color: '#38c6f4', en: 'H', zh: '交' },
  'lowalt:IncidentSite': { color: '#ff7a45', en: '!', zh: '!' },
  'lowalt:Site': { color: '#5b6b7d', en: 'S', zh: '点' },
};
const VEHICLE_MARKERS: Record<string, { color: string; en: string; zh: string }> = {
  parcel: { color: '#e8a317', en: 'P', zh: '配' },
  food: { color: '#e8a317', en: 'F', zh: '餐' },
  medical: { color: '#e0453a', en: 'M', zh: '医' },
  inspection: { color: '#38c6f4', en: 'I', zh: '检' },
  patrol: { color: '#b26bff', en: 'P', zh: '巡' },
  passenger: { color: '#1f7ae0', en: 'T', zh: '客' },
  spraying: { color: '#1fae6b', en: 'S', zh: '植' },
};

/** Authoritative site positions, explicit UAV starts and configured generator start points.
 * Labels use the active locale; generic spatial instances retain their presentation colour.
 * No task or positionless entity is assigned a made-up map coordinate.
 */
export function regionMapObjects(draft: Scenario, L: (en: string, zh: string) => string): { instances: RegionMapInstance[]; invalidPositions: string[] } {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const presentation = (Array.isArray(draft.presentation) ? draft.presentation : []) as Array<Record<string, any>>;
  const typeOrder = new Map<string, number>();
  const invalidPositions: string[] = [];
  const engines = draft.engines as Record<string, { plugin?: string; config?: Record<string, any> }> | undefined;
  const tasks = Object.values(engines ?? {}).find(engine => engine.plugin === 'lowalt_tasks')?.config;
  for (const entity of entities) if (!typeOrder.has(entity.type)) typeOrder.set(entity.type, typeOrder.size);
  const vector = (value: unknown): value is [number, number, number] => Array.isArray(value)
    && value.length === 3 && value.every(Number.isFinite);
  const instances: RegionMapInstance[] = entities.flatMap(entity => {
    const binding = presentation.find(row => row.typeId === entity.type);
    const facts = entity.facts ?? {};
    const field = ['lowalt.site_position', 'lowalt.position', binding?.positionField ?? 'aas.studio.position_enu_m'].find(key => key in facts);
    if (!field) return [];
    const position = facts[field];
    if (!vector(position)) { invalidPositions.push(String(entity.id)); return []; }
    const vehicleClass = facts['lowalt.vehicle_class'] ?? entity.vehicle_class;
    const kinds = tasks?.vehicle_task_kinds?.[entity.id] ?? tasks?.vehicle_kinds?.[vehicleClass];
    const vehicleRole = Array.isArray(kinds) && kinds.length === 1 && (kinds[0] === 'food' || kinds[0] === 'medical') ? kinds[0] : vehicleClass;
    const marker = PLACE_MARKERS[entity.type] ?? VEHICLE_MARKERS[vehicleRole];
    const label = L(entity.label ?? entity.id, entity.label_zh ?? entity.label ?? entity.id);
    return [{ east: position[0], north: position[1],
      color: marker?.color ?? binding?.visual?.color ?? paletteColorFor(entity.type, typeOrder),
      letter: marker ? L(marker.en, marker.zh) : L('O', '物'),
      label: label && label !== entity.id ? `${label} · ${entity.id}` : String(entity.id),
    }];
  });
  const generators = draft.generators as { fleet?: { prefix?: string; starts?: unknown[]; facts?: Record<string, unknown> } } | undefined;
  const fleet = generators?.fleet;
  if (fleet && !Array.isArray(fleet.starts)) invalidPositions.push(L('Fleet starts', '机群起点'));
  if (Array.isArray(fleet?.starts)) {
    const marker = VEHICLE_MARKERS[String(fleet.facts?.['lowalt.vehicle_class'])];
    for (const [index, start] of fleet.starts.entries()) {
      if (!vector(start)) { invalidPositions.push(L(`Fleet start ${index + 1}`, `机群起点 ${index + 1}`)); continue; }
      instances.push({ east: start[0], north: start[1], color: marker?.color ?? FALLBACK_PALETTE[0],
        letter: marker ? L(marker.en, marker.zh) : L('F', '群'),
        label: L(`${fleet.prefix ?? ''} start ${index + 1}`, `${fleet.prefix ?? ''} 起点 ${index + 1}`) });
    }
  }
  return { instances, invalidPositions };
}
