import type { PresentationBinding, RunHeader } from '../contracts/viewer-feed';
import { resolveBinding } from '../viewport/bindings';
import { enuPosition } from '../viewport/position';
import type { Scenario } from './api';

/** A presentation frame the viewer understands; mirrors the viewer-feed contract. */
export type PreviewFrame = PresentationBinding['frame'];

export interface PreviewPlacement {
  /** Entity id, or `<generator>:start:<n>` for a configured fleet start. */
  id: string;
  typeId?: string;
  frame: PreviewFrame;
  /** Runtime metres: east, north, up. */
  enu: [number, number, number];
  /** Renderer axes: east, up, south. */
  world: [number, number, number];
  color?: string;
  source: 'entity' | 'generator';
}

export interface PreviewDiagnostic {
  entityId?: string;
  typeId?: string;
  field?: string;
  code: 'missing-origin' | 'invalid-frame' | 'invalid-position';
  message: string;
}

export interface PreviewPositions {
  placements: PreviewPlacement[];
  diagnostics: PreviewDiagnostic[];
  /** Any entity with a resolved presentation contract, an area, or a configured fleet. */
  hasSpatialEntities: boolean;
}

const isRecord = (value: unknown): value is Record<string, any> => typeof value === 'object' && value !== null && !Array.isArray(value);
const isVector = (value: unknown): value is [number, number, number] => Array.isArray(value) && value.length === 3
  && value.every(part => typeof part === 'number' && Number.isFinite(part));

/** Nearest parent first, breadth-first: the order the projector gives RunHeader.types[].ancestors. */
function ancestorsOf(typeId: string, parents: Map<string, string[]>): string[] {
  const result: string[] = [], pending = [...(parents.get(typeId) ?? [])];
  while (pending.length) {
    const parent = pending.shift()!;
    if (result.includes(parent)) continue;
    result.push(parent);
    pending.push(...(parents.get(parent) ?? []));
  }
  return result;
}

/** Adapt a draft's registry/presentation to the run header shape and use the runtime's resolver. */
function draftHeader(scenario: Scenario): Pick<RunHeader, 'presentation' | 'types'> {
  const types: any[] = Array.isArray(scenario.registry?.types) ? scenario.registry.types : [];
  const parents = new Map<string, string[]>();
  for (const type of types) {
    if (typeof type?.id !== 'string') continue;
    parents.set(type.id, Array.isArray(type.parents) ? type.parents.filter((parent: unknown) => typeof parent === 'string') : []);
  }
  const presentation: PresentationBinding[] = Array.isArray(scenario.presentation) ? scenario.presentation : [];
  return {
    presentation,
    types: types.filter((type: any) => typeof type?.id === 'string').map((type: any) => ({
      typeId: type.id, displayName: type.name ?? type.id, ancestors: ancestorsOf(type.id, parents),
    })),
  };
}

/** Resolve a draft type's presentation contract with the runtime's inheritance rule. */
export function draftBinding(scenario: Scenario, typeId: string): PresentationBinding | undefined {
  return resolveBinding(draftHeader(scenario) as RunHeader, typeId);
}

function originOf(value: unknown): NonNullable<RunHeader['origin']> | undefined {
  if (!isRecord(value)) return undefined;
  const { lat, lon, alt } = value;
  if (typeof lat !== 'number' || !Number.isFinite(lat) || lat < -90 || lat > 90) return undefined;
  if (typeof lon !== 'number' || !Number.isFinite(lon) || lon < -180 || lon > 180) return undefined;
  if (typeof alt !== 'number' || !Number.isFinite(alt)) return undefined;
  return { lat, lon, alt };
}

/** The Studio's authored position field is ENU; tolerate drafts that predate the explicit frame key. */
function frameOf(value: unknown): PreviewFrame | undefined {
  if (value === undefined || value === null) return 'enu';
  return value === 'enu' || value === 'ned' || value === 'wgs84' ? value : undefined;
}

/**
 * Convert draft declarations with the same frame/origin semantics the run viewer uses.
 * A declaration that cannot be converted yields a diagnostic; no fallback coordinate is invented.
 */
export function previewPositions(scenario: Scenario): PreviewPositions {
  const placements: PreviewPlacement[] = [], diagnostics: PreviewDiagnostic[] = [];
  let hasSpatialEntities = false;
  const origin = originOf(scenario.origin);
  const header = draftHeader(scenario) as RunHeader;

  for (const entity of Array.isArray(scenario.entities) ? scenario.entities : []) {
    const facts: Record<string, any> = isRecord(entity?.facts) ? entity.facts : {};
    const airspace = facts['aas.studio.airspace'];
    // Area geometry is authoritative; it is spatial, but its legacy anchor is not a point.
    if (airspace) { hasSpatialEntities = true; continue; }
    const typeId = typeof entity?.type === 'string' ? entity.type : '';
    const binding = resolveBinding(header, typeId);
    if (!binding) continue;
    hasSpatialEntities = true;
    const field = binding.positionField;
    const entityId = String(entity?.id ?? '');
    const context = { entityId, typeId, field };
    const frame = frameOf(binding.frame);
    if (!frame) {
      diagnostics.push({ ...context, code: 'invalid-frame', message: `Unsupported presentation frame: ${String(binding.frame)}` });
      continue;
    }
    const value = facts[field];
    if (value === undefined) continue;
    if (!isVector(value)) {
      diagnostics.push({ ...context, code: 'invalid-position', message: 'Position must be a finite 3-vector' });
      continue;
    }
    if (frame === 'wgs84' && !origin) {
      diagnostics.push({ ...context, code: 'missing-origin', message: 'WGS84 presentation requires an origin' });
      continue;
    }
    try {
      const enu = enuPosition(value, frame, origin);
      placements.push({ id: entityId, typeId, frame, enu, world: [enu[0], enu[2], -enu[1]], color: binding.visual?.color, source: 'entity' });
    } catch (problem) {
      diagnostics.push({ ...context, code: 'invalid-position', message: problem instanceof Error ? problem.message : String(problem) });
    }
  }

  // Generated fleet starts are authored ENU points, independent of entity presentation bindings.
  const generators: Record<string, any> = isRecord(scenario.generators) ? scenario.generators : {};
  for (const [key, generator] of Object.entries(generators)) {
    if (!isRecord(generator)) continue;
    const starts = Array.isArray(generator.starts) ? generator.starts : [];
    const count = typeof generator.count === 'number' && Number.isFinite(generator.count) ? generator.count : 0;
    if (starts.length || count > 0) hasSpatialEntities = true;
    const typeId = typeof generator.type === 'string' ? generator.type : undefined;
    const binding = typeId ? resolveBinding(header, typeId) : undefined;
    for (const [index, start] of starts.entries()) {
      const id = `${key}:start:${index + 1}`;
      if (!isVector(start)) {
        diagnostics.push({ entityId: id, typeId, field: 'starts', code: 'invalid-position', message: 'Fleet start must be a finite 3-vector' });
        continue;
      }
      placements.push({ id, typeId, frame: 'enu', enu: start, world: [start[0], start[2], -start[1]], color: binding?.visual?.color, source: 'generator' });
    }
  }

  return { placements, diagnostics, hasSpatialEntities };
}
