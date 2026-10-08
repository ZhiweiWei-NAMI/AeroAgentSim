/**
 * Viewer feed contract (v1) between AeroAgentSim services and the frontend.
 *
 * The feed is a read-only projection of the aerokernel journal. Nothing here is
 * domain specific: entities are identified by registry type IDs, and the
 * scenario's presentation bindings say which fields (if any) carry spatial
 * pose for a type. Entities without a presentation binding still appear in
 * inspectors, timelines and relation views; they are simply not placed in 3D.
 *
 * Times are canonical kernel nanoseconds encoded as decimal strings, because
 * JavaScript numbers cannot hold every int64 losslessly.
 */

export type NanosString = string;

export interface Instant {
  ns: NanosString;
  microstep: number;
}

export interface EntityKey {
  id: string;
  generation: number | string;
}

export interface SourceStamp { clockId: string; mappingId: string; numerator: string; denominator: string }
export interface VersionKey { journalIndex: number; itemOrdinal: number }
export interface TemporalMetadata { validTo?: Instant | null; available?: Instant; version?: VersionKey; acquired?: SourceStamp; causes?: unknown[] }

/** Registry descriptors, delivered once per run (from the pinned snapshot). */
export interface TypeInfo {
  typeId: string;
  displayName: string;
  /** Ancestors via actual is-a, nearest first; used for visual fallback. */
  ancestors: string[];
  /** Browsing directory (one of AeroGraph's seven), for grouping only. */
  directory?: string;
}

export interface FieldInfo {
  fieldId: string;
  displayName: string;
  role?: string;
  unit?: string | null;
  frame?: string | null;
  valueType: string;
  schema?: unknown;
  metadata?: unknown;
}

/**
 * How a type is drawn. Chosen by the scenario, never inferred from type names.
 * `positionField` must hold a 3-vector in `frame`; `orientationField` (optional)
 * a quaternion [x, y, z, w] in the same frame.
 */
export interface PresentationBinding {
  typeId: string;
  positionField: string;
  orientationField?: string;
  frame: "enu" | "ned" | "wgs84";
  visual: { kind: "model" | "marker" | "label"; asset?: string; scale?: number; color?: string };
}

export interface RunHeader {
  contract: "aeroagentsim.viewer-feed/v1";
  runId: string;
  registryDigest: string;
  origin?: { lat: number; lon: number; alt: number };
  types: TypeInfo[];
  fields: FieldInfo[];
  presentation: PresentationBinding[];
  start: Instant;
  end?: Instant;
  runtimeRegistry?: unknown;
  messages?: unknown[];
}

/** One committed kernel transaction, projected for viewing. */
export interface FeedCommit {
  commitIndex: number;
  at: Instant;
  created: Array<EntityKey & { typeId: string }>;
  removed: EntityKey[];
  /** Latest committed value per (entity, field) in this commit. */
  facts: Array<TemporalMetadata & {
    entity: EntityKey; fieldId: string; value: unknown; producer: string; validFrom: Instant;
    /** Display-only barrier for an explicitly reported teleport/discontinuous update. */
    discontinuity?: boolean;
  }>;
  retracted: Array<TemporalMetadata & { entity: EntityKey; fieldId: string; validFrom?: Instant; reason?: string }>;
  edges: Array<TemporalMetadata & { edgeId: string; relationId: string; source: EntityKey; target: EntityKey; op: "assert" | "close" | "cancel"; validFrom?: Instant | null }>;
  messages: Array<{ id: string; kind: "command" | "event"; schemaId: string; source: string; target?: string; topic?: string; at: Instant; payload: unknown; subjects?: EntityKey[] }>;
  receipts: Array<{ commandId: string; status: string; result?: unknown }>;
}

/**
 * Transport-neutral source. Live runs stream commits (SSE/WS); replays page
 * them from storage. Consumers must not assume a fixed commit cadence.
 */
export interface ViewerFeed {
  header(): Promise<RunHeader>;
  /** Commits with index >= fromIndex, in order; resolves when the stream ends. */
  subscribe(fromIndex: number, onCommit: (commit: FeedCommit) => void, signal?: AbortSignal): Promise<void>;
}
