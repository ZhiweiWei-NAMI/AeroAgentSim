import { useEffect, useState } from 'react';
import type { StudioApi, TypeRow } from '../../../studio/api';
import type { Scenario } from '../types';

export const STUDIO_POS = 'aas.studio.position_enu_m';
export const STUDIO_VEL = 'aas.studio.velocity_enu_m_s';
export const STUDIO_STATE = 'aas.studio.state';
export const STUDIO_AIRSPACE = 'aas:StudioAirspace';

/** Server defaults for a studio placement; real modeling assumptions, not UI decorations. */
export const KINEMATIC_DEFAULTS = { energy: 100000 };
export const WORKFLOW_DEFAULT_STATE = 'placed';

export type Card = { minimum: number; maximum: number | null };
export type RelationRow = {
  id: string; source_type: string; target_type: string;
  targets_per_source: Card; sources_per_target: Card; identity_policy?: string; metadata?: unknown;
};
export type Catalog = { types: TypeRow[]; fields?: Array<Record<string, any> & { id: string }>; relations?: RelationRow[] };

/** The server rejects anything outside this shape before touching the draft. */
export const ID_PATTERN = /^[A-Za-z0-9_.:/-]+$/;

/** Workspace type catalog; `relations` is absent on snapshot sources without relation rows. */
export function useCatalog(studioApi: StudioApi, workspaceId: string, enabled: boolean) {
  const [catalog, setCatalog] = useState<Catalog>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (!enabled || catalog) return;
    setError(undefined);
    void studioApi.request<Catalog>(`/v1/studio/types?workspace=${encodeURIComponent(workspaceId)}`)
      .then(setCatalog).catch(problem => setError(String(problem)));
  }, [enabled, catalog, studioApi, workspaceId]);
  return { catalog, catalogError: error };
}

/** The presentation position field for a type; the studio default as fallback. */
export function positionFieldOf(draft: Scenario, typeId: string): string {
  const presentation = Array.isArray(draft.presentation) ? draft.presentation as Array<Record<string, any>> : [];
  return presentation.find(item => item.typeId === typeId)?.positionField ?? STUDIO_POS;
}

export type Dependency = { path: string; kind: string; detail: string };
/** Group a C3 dependency preview by kind, keeping the server's order. */
export function groupDependencies(deps: Dependency[]): Array<[string, Dependency[]]> {
  const order: string[] = [], groups: Record<string, Dependency[]> = {};
  for (const dep of deps) {
    if (!groups[dep.kind]) { groups[dep.kind] = []; order.push(dep.kind); }
    groups[dep.kind].push(dep);
  }
  return order.map(kind => [kind, groups[kind]]);
}

export const num = (raw: string): number | undefined => {
  const text = raw.trim();
  return text !== '' && /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text) && Number.isFinite(Number(text)) ? Number(text) : undefined;
};

/** Parse one user input according to a contract schema; undefined means "leave the field out". */
export function parseFact(schema: any, raw: string): { value?: unknown; error?: string } {
  const text = raw.trim();
  if (text === '') return {};
  const kind = schema?.type;
  if (kind === 'number' || kind === 'integer') {
    const value = num(text);
    if (value === undefined) return { error: 'number' };
    if (kind === 'integer' && !Number.isInteger(value)) return { error: 'integer' };
    return { value };
  }
  if (kind === 'boolean') return text === 'true' ? { value: true } : text === 'false' ? { value: false } : { error: 'true | false' };
  if (kind === 'vector') {
    const parts = text.split(',').map(part => num(part));
    if (parts.some(part => part === undefined)) return { error: 'numbers' };
    const vector = parts as number[];
    if (typeof schema.length === 'number' && vector.length !== schema.length) return { error: `${schema.length} values` };
    return { value: vector };
  }
  return { value: raw };
}
