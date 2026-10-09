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

export interface SelectionKey extends EntityKey { runId: string; epoch: string }
export interface RecordedCut { index: number; at: Instant }
export interface PredicateTruth extends Omit<TemporalMetadata, 'acquired'> {
  contextId: string; predicateId: string; roles: Record<string, EntityKey>;
  profile: string; status: 'known' | 'required_input' | 'invalid_input'; value: boolean | null;
  diagnostics: unknown[]; evaluatedAt: Instant; readCut: RecordedCut;
  validFrom: Instant; op: 'assert' | 'close';
  acquired?: Record<string, { clockId: string; mappingId: string; numerator?: string; denominator?: string }>;
}
export interface ChainInstance extends TemporalMetadata {
  instanceId: string; templateId: string; bindingId: string; packageDigest: string;
  roles: Record<string, EntityKey>; lifecycle: 'created' | 'transitioned' | 'completed' | 'failed' | 'canceled';
  state: string; revision: number | string | { $integer: string }; variables: Record<string, unknown>;
  children: Record<string, unknown>; transitionId?: string | null;
  trigger?: unknown; evaluation?: unknown; validFrom: Instant; op?: 'assert' | 'close';
}
export interface BehaviourHeader {
  packageDigests?: string[]; irDigests?: string[]; evaluatorDigests?: string[];
  predicates?: unknown; chains?: unknown; bindings?: unknown;
  injectionPoints?: Array<Record<string, unknown>>; injection_points?: Array<Record<string, unknown>>;
  extensions: string[];
  [key: string]: unknown;
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
  epoch?: string;
  kernelRunId?: string;
  behaviour?: BehaviourHeader;
  registryDigest: string;
  origin?: { lat: number; lon: number; alt: number };
  types: TypeInfo[];
  fields: FieldInfo[];
  presentation: PresentationBinding[];
  start: Instant;
  end?: Instant;
  runtimeRegistry?: unknown;
  messages?: unknown[];
  /** Authored platform bindings for string/ref payload paths, indexed by schema ID. */
  messageSubjects?: Record<string, Array<{ path: Array<string | number>; type_id: string; generation_path?: Array<string | number> }>>;
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
  /** Subjects come from typed $ref values or declared payload paths, never arbitrary strings. */
  messages: Array<{ id: string; kind: "command" | "event"; schemaId: string; source: string; target?: string; topic?: string; at: Instant; payload: unknown; subjects?: EntityKey[] }>;
  receipts: Array<{ commandId: string; status: string; result?: unknown }>;
  predicateTruth?: PredicateTruth[];
  chainInstances?: ChainInstance[];
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
