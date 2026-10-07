/**
 * P02 Run view index: typed, replay-cursor-scoped views over the authorized
 * public trace documents. Nothing here scans directories, provenance chains
 * or payloads at render time; every source document is read once per trace
 * load into flat arrays that are joined by prebuilt Maps (O(1) lookups in
 * the per-tick hot path).
 *
 * Authority boundary: every displayed value is copied from a named public
 * field. A field the public contract does not declare stays UNKNOWN
 * ("未知"); custody, cargo content, rule predicates and orders are only
 * ever echoed from explicit public records, never inferred from proximity
 * or plausibility.
 */
import type {
  ArtifactReference,
  PublicScenario,
  PublicMissionEvent,
  PublicRunEvent,
  PublicVerificationReport,
  StateAttribute,
  StateSample,
  SceneState,
  ResolvedCoordinate,
} from "./generated/aero-bench-contracts";

/** Public business inputs shared by a sealed replay and the authenticated live stream. */
export interface RunBusinessSource {
  readonly run_id: string;
  readonly scenario: PublicScenario;
  readonly events: readonly PublicRunEvent[];
  readonly mission_events: readonly PublicMissionEvent[];
  readonly verifier_public: PublicVerificationReport | null;
}

export const UNKNOWN_LABEL_ZH = "未知";

/** Same time convention as the rest of the viewer: seconds, non-negative. */
export type RunTimeSeconds = number;

/**
 * One record of the explicit business-identity sidecar
 * (p02.business-identities/v1). Every field is authored by the business
 * layer; the viewer never synthesizes any of them. `carrier_entity_id`
 * names a scene entity exactly or is null — it is never derived from
 * event.entity_id, proximity or timing.
 */
export interface BusinessIdentityRecord {
  readonly record_id: string;
  readonly at: { readonly tick: number; readonly sim_time_ns: number };
  readonly order_id: string;
  readonly parcel_id: string;
  readonly carrier_entity_id: string | null;
  readonly custody_holder: { readonly kind: "entity" | "facility" | "actor" | "unknown"; readonly id: string | null };
  readonly destination_id: string | null;
  readonly attempt: number | null;
  readonly status: string;
  readonly source_ref: string;
  readonly availability_reason: string | null;
  readonly parcel_position_enu?: { readonly east_m: number; readonly north_m: number; readonly up_m: number };
}

/** Sidecar envelope: binds the records to one exact PublicTrace run id. */
export interface BusinessIdentityEnvelope {
  readonly schema_version: string;
  readonly scene_run_id: string;
  readonly source_kind: "authored_business_fixture" | "business_provider" | string;
  readonly source_ref: string;
  readonly records: readonly BusinessIdentityRecord[];
}

/** Compiled, cursor-scoped view of one business identity record. */
export interface BusinessIdentityView {
  readonly orderId: string;
  readonly parcelId?: string;
  readonly carrierEntityId?: string;
  readonly custodyKind?: BusinessIdentityRecord["custody_holder"]["kind"];
  readonly custodyId?: string;
  readonly destinationId?: string;
  readonly attempt?: number;
  readonly status?: string;
  readonly sourceRef: string;
  readonly availabilityReason?: string;
  readonly tick: number;
  readonly timeSeconds: RunTimeSeconds;
  readonly parcelPositionEnu?: BusinessIdentityRecord["parcel_position_enu"];
}

/** Copy the native Provider's declared identities and parcel pose from public events. */
export function nativeParcelIdentities(trace: Pick<RunBusinessSource, "run_id" | "events">): BusinessIdentityEnvelope | null {
  const events = trace.events.filter(event => event.event_type === "public.parcel-projection");
  if (events.length === 0) return null;
  const records: BusinessIdentityRecord[] = events.map(event => {
    const fields = Object.fromEntries(event.public_payload.map(field => [field.name, field.value])) as {
      order_id: string; parcel_id: string; carrier_entity_id: string | null;
      custody_holder_id: string; custody_holder_kind: "carrier" | "facility";
      destination_id: string; parcel_state: string; frame_digest: string;
      x_m: number; y_m: number; z_m: number;
    };
    return { record_id: event.event_id, at: event.at, order_id: fields.order_id,
      parcel_id: fields.parcel_id, carrier_entity_id: fields.carrier_entity_id,
      custody_holder: { kind: fields.custody_holder_kind === "carrier" ? "actor" : "facility",
        id: fields.custody_holder_id }, destination_id: fields.destination_id,
      attempt: null, status: fields.parcel_state, source_ref: fields.frame_digest,
      availability_reason: null,
      parcel_position_enu: { east_m: fields.x_m, north_m: -fields.z_m, up_m: fields.y_m } };
  });
  return { schema_version: "p02.business-identities/v1", scene_run_id: trace.run_id,
    source_kind: "business_provider", source_ref: "public.parcel-projection", records };
}

/** One business work order as the public run actually declares it. */
export interface RunOrder {
  readonly orderId: string;
  /** The public label for the work-order family: always declared by source. */
  readonly sourceLabel: string;
  /** Declared carrier entity, or undefined when the public trace does not bind one. */
  readonly carrierEntityId?: string;
  /** Declared destination/custody target, or undefined when not published. */
  readonly targetEntityId?: string;
  /** Stage chain identifiers in declared dependency order. */
  readonly stageIds: readonly string[];
  /** Evidence artifact references that name this order explicitly. */
  readonly evidence: readonly ArtifactReference[];
  /** Stage index -> first tick that recorded a terminal state. */
  readonly stageFlips: ReadonlyMap<string, { readonly tick: number; readonly state: string }>;
}

/** One public record about an order stage at a tick ("delivered", "dropped", ...). */
export interface RunStageEvent {
  readonly orderId?: string;
  readonly state: string;
  readonly tick: number;
  readonly timeSeconds: RunTimeSeconds;
  readonly evidence: readonly ArtifactReference[];
  readonly eventId: string;
  readonly providerId: string;
  /** Second-based sim time of the flip, for the "last flip" column. */
  readonly flipTimeSeconds: RunTimeSeconds;
}

/** A spatial checkpoint of the run: observation targets and launch pads. */
export interface RunCheckPoint {
  readonly id: string;
  readonly kind: "target" | "launch" | "region";
  readonly label: string;
  readonly eastM: number;
  readonly northM: number;
  readonly upM: number;
  /** Optional declared parent entity (building the target is mounted on). */
  readonly parentEntityId?: string;
}

/** One row of the Run view's order list, already joined for one tick. */
export interface RunOrderView {
  readonly order: RunOrder;
  /** Latest stage event at or before the cursor, or undefined. */
  readonly latestStage?: RunStageEvent;
  /** Stage chain rendered as short status strings; UNKNOWN stays "未知". */
  readonly stageStates: readonly string[];
  /** Declared current phase of the carrier entity at the cursor, or undefined. */
  readonly carrierPhase?: string;
}

/** Evidence row for the selected order: state -> rule -> predicate -> record. */export interface RunOrderEvidence {
  readonly stageEvents: readonly RunStageEvent[];
  readonly verifierGoals: readonly {
    readonly goalId: string;
    readonly passed: boolean;
    readonly metrics: readonly {
      readonly metricId: string;
      readonly unit: string | null;
      readonly value: number;
      readonly evidence: readonly ArtifactReference[];
    }[];
  }[];
  /** Mission-requirement rows that name the stages of this order. */
  readonly requirements: readonly {
    readonly requirementId: string;
    readonly kind: string;
    readonly dependencies: readonly string[];
    readonly targetId?: string;
    readonly launchSiteId?: string;
  }[];
}

export interface RunIndexInput {
  readonly trace: RunBusinessSource;
  /**
   * Entity kinds by id from the scenario; the index uses it only to label
   * carriers, never to invent cargo truth.
   */
  readonly entityKinds: ReadonlyMap<string, string>;
  /**
   * Optional explicit business-identity sidecar (p02.business-identities/v1)
   * fetched once per trace load. It binds only the exact loaded run id;
   * anything else is ignored.
   */
  readonly identity?: BusinessIdentityEnvelope | null;
}

interface MutableOrder {
  orderId: string;
  sourceLabel: string;
  carrierEntityId?: string;
  targetEntityId?: string;
  stageIds: Set<string>;
  stageOrder: string[];
  evidence: ArtifactReference[];
  stageFlips: Map<string, { tick: number; state: string }>;
}

function seconds(ns: number): RunTimeSeconds {
  return ns / 1_000_000_000;
}

/**
 * Build the immutable Run index for one trace. Complexity is linear in the
 * number of mission events, requirements, targets and verifier metrics; the
 * per-tick hot path afterwards only reads Maps.
 */
export function buildRunIndex(input: RunIndexInput): RunIndex {
  const orders = new Map<string, MutableOrder>();
  const stageEvents: RunStageEvent[] = [];
  // Keyed by orderId for O(1) cursor queries; values sorted by tick ascending.
  const eventsByOrder = new Map<string, RunStageEvent[]>();
  const checkpoints: RunCheckPoint[] = [];

  // Mission events are the public, provider-signed state records. The
  // public document declares state + evidence + tick. event.entity_id
  // names a scene entity; it is NOT an order_id/parcel_id/carrier
  // binding, so events never mint orders here and always stay visible
  // under the explicit UNKNOWN bucket ("") — order identity arrives only
  // through the business-identity sidecar records.
  for (const event of input.trace.mission_events) {
    const stageEvent: RunStageEvent = {
      tick: event.at.tick,
      timeSeconds: seconds(event.at.sim_time_ns),
      flipTimeSeconds: seconds(event.at.sim_time_ns),
      state: event.state,
      evidence: event.evidence,
      eventId: event.event_id,
      providerId: event.provider_id,
    };
    stageEvents.push(stageEvent);
    // Every mission event is unbound business identity: keyed under "".
    const bucket = eventsByOrder.get("");
    if (bucket === undefined) eventsByOrder.set("", [stageEvent]);
    else bucket.push(stageEvent);
  }

  // Spatial checkpoints: semantic targets, launch sites and regions, all
  // declared with explicit ENU poses. The carrier binding below is copied
  // from requirement.target_id / launch_site allowed ids where present.
  for (const target of input.trace.scenario.semantic_targets) {
    const pose = target.pose.position.enu;
    checkpoints.push({
      id: target.target_id,
      kind: "target",
      label: target.target_id,
      eastM: pose.east_m,
      northM: pose.north_m,
      upM: pose.up_m,
      parentEntityId: target.parent_entity_id ?? undefined,
    });
  }
  for (const site of input.trace.scenario.launch_sites) {
    const pose = site.pose.position.enu;
    checkpoints.push({
      id: site.launch_site_id,
      kind: "launch",
      label: site.launch_site_id,
      eastM: pose.east_m,
      northM: pose.north_m,
      upM: pose.up_m,
    });
  }

  const requirements = input.trace.scenario.mission_requirements.map(requirement => ({
    requirementId: requirement.requirement_id,
    kind: requirement.kind,
    dependencies: requirement.dependencies,
    ...(requirement.target_id === null ? {} : { targetId: requirement.target_id }),
    ...(requirement.launch_site_id === null ? {} : { launchSiteId: requirement.launch_site_id }),
  }));

  const verifier = input.trace.verifier_public;

  return new RunIndex({
    orders,
    eventsByOrder,
    checkpoints,
    requirements,
    verifier,
    entityKinds: input.entityKinds,
    identity: buildBusinessIdentityIndex(input.identity ?? nativeParcelIdentities(input.trace), input.trace.run_id),
  });
}

export interface RunIndexSources {
  readonly orders: ReadonlyMap<string, MutableOrder>;
  readonly eventsByOrder: ReadonlyMap<string, RunStageEvent[]>;
  readonly checkpoints: readonly RunCheckPoint[];
  readonly requirements: readonly {
    readonly requirementId: string;
    readonly kind: string;
    readonly dependencies: readonly string[];
    readonly targetId?: string;
    readonly launchSiteId?: string;
  }[];
  readonly verifier: PublicVerificationReport | null;
  readonly entityKinds: ReadonlyMap<string, string>;
  readonly identity: BusinessIdentityIndex | null;
}

/**
 * Accept an explicit business-identity envelope for exactly one run. The
 * sidecar is authored by the business layer; the viewer only binds it when
 * `scene_run_id` equals the loaded PublicTrace.run_id. Anything else — a
 * foreign run, a different schema version, malformed records — binds
 * nothing: orders, custody and carriers stay UNKNOWN.
 */
export function buildBusinessIdentityIndex(
  envelope: BusinessIdentityEnvelope | null | undefined,
  traceRunId: string,
): BusinessIdentityIndex | null {
  if (envelope === null || envelope === undefined) return null;
  if (envelope.schema_version !== "p02.business-identities/v1") return null;
  if (envelope.scene_run_id !== traceRunId) return null;
  if (!Array.isArray(envelope.records)) return null;
  const byOrder = new Map<string, BusinessIdentityRecord[]>();
  for (const record of envelope.records) {
    if (record === null || typeof record !== "object") return null;
    if (typeof record.order_id !== "string" || record.order_id.length === 0) return null;
    if (typeof record.at?.tick !== "number" || !Number.isSafeInteger(record.at.tick)
      || typeof record.at.sim_time_ns !== "number" || !Number.isSafeInteger(record.at.sim_time_ns)) {
      return null;
    }
    const bucket = byOrder.get(record.order_id);
    if (bucket === undefined) byOrder.set(record.order_id, [record]);
    else bucket.push(record);
  }
  // Records must be cursor-comparable in declaration order.
  for (const bucket of byOrder.values()) {
    bucket.sort((left, right) => left.at.tick - right.at.tick
      || left.at.sim_time_ns - right.at.sim_time_ns);
  }
  return new BusinessIdentityIndex(envelope, byOrder);
}

/** Compiled business-identity sidecar; every read is a bounded slice join. */
export class BusinessIdentityIndex {
  constructor(
    readonly source: BusinessIdentityEnvelope,
    private readonly byOrder: ReadonlyMap<string, BusinessIdentityRecord[]>,
  ) {}

  /** Order ids the sidecar declares, sorted; no trace-derived ids appear. */
  orderIds(): readonly string[] {
    return [...this.byOrder.keys()].sort();
  }

  /** Latest record of one order at or before the cursor, or undefined. */
  latestAt(orderId: string, tick: number): BusinessIdentityView | undefined {
    const bucket = this.byOrder.get(orderId);
    if (bucket === undefined) return undefined;
    let latest: BusinessIdentityRecord | undefined;
    for (const record of bucket) {
      if (record.at.tick > tick) break;
      latest = record;
    }
    return latest === undefined ? undefined : viewOf(latest);
  }

  /** Business records that explicitly name the entity as carrier, up to the cursor. */
  ordersAt(entityId: string, tick: number): readonly BusinessIdentityView[] {
    const result: BusinessIdentityView[] = [];
    for (const orderId of this.byOrder.keys()) {
      const latest = this.latestAt(orderId, tick);
      if (latest !== undefined && latest.carrierEntityId === entityId) result.push(latest);
    }
    return result.sort((left, right) => left.orderId.localeCompare(right.orderId));
  }

  /** All order views at or before the cursor (for the Run dock list). */
  allAt(tick: number): readonly BusinessIdentityView[] {
    return this.orderIds()
      .map(orderId => this.latestAt(orderId, tick))
      .filter((view): view is BusinessIdentityView => view !== undefined);
  }
}

function viewOf(record: BusinessIdentityRecord): BusinessIdentityView {
  return {
    orderId: record.order_id,
    // Genuinely missing authored facts stay undefined (UNKNOWN); the
    // viewer never substitutes entity ids or placeholders for them.
    ...(record.parcel_id.length > 0 ? { parcelId: record.parcel_id } : {}),
    ...(record.carrier_entity_id !== null && record.carrier_entity_id.length > 0
      ? { carrierEntityId: record.carrier_entity_id } : {}),
    ...(record.custody_holder.kind !== "unknown" ? { custodyKind: record.custody_holder.kind } : {}),
    ...(record.custody_holder.id !== null && record.custody_holder.id.length > 0
      ? { custodyId: record.custody_holder.id } : {}),
    ...(record.destination_id !== null && record.destination_id.length > 0
      ? { destinationId: record.destination_id } : {}),
    ...(record.attempt !== null ? { attempt: record.attempt } : {}),
    ...(record.status.length > 0 ? { status: record.status } : {}),
    sourceRef: record.source_ref,
    ...(record.availability_reason !== null && record.availability_reason.length > 0
      ? { availabilityReason: record.availability_reason } : {}),
    tick: record.at.tick,
    timeSeconds: seconds(record.at.sim_time_ns),
    ...(record.parcel_position_enu === undefined ? {} : { parcelPositionEnu: record.parcel_position_enu }),
  };
}

/**
 * Selected-entity business panel: records that explicitly name the entity
 * as carrier (up to the cursor) plus the ids it carries custody for. No
 * trace-entity-id equality shortcut: an entity id shared with a mission
 * event never mints business identity.
 */
export function businessIdentityView(
  identity: BusinessIdentityIndex | null,
  entityId: string,
  tick: number,
): {
  readonly orders: readonly BusinessIdentityView[];
  readonly carrierFor: readonly string[];
} {
  if (identity === null) return { orders: [], carrierFor: [] };
  const orders = identity.ordersAt(entityId, tick);
  const carrierFor = identity.orderIds().filter(orderId => {
    const latest = identity.latestAt(orderId, tick);
    return latest !== undefined && latest.carrierEntityId === entityId;
  });
  return { orders, carrierFor };
}

/** Compiled per-trace view; every per-tick query is a Map join. */
export class RunIndex {
  constructor(private readonly sources: RunIndexSources) {}

  /** Compiled business-identity sidecar, or null when none binds this run. */
  get identity(): BusinessIdentityIndex | null {
    return this.sources.identity;
  }

  /**
   * The stage chain of the run in declared dependency order. Formal
   * inspection runs publish exactly one requirement DAG; the chain is the
   * topological order of that DAG, or ["未知"] when the trace declares none.
   */
  stageChain(): readonly string[] {
    const requirements = this.sources.requirements;
    if (requirements.length === 0) return [];
    const ids = new Map(requirements.map(r => [r.requirementId, r]));
    const done: string[] = [];
    const visiting = new Set<string>();
    const visit = (id: string): void => {
      if (done.includes(id) || visiting.has(id)) return;
      visiting.add(id);
      for (const dep of ids.get(id)?.dependencies ?? []) visit(dep);
      visiting.delete(id);
      done.push(id);
    };
    for (const requirement of requirements) visit(requirement.requirementId);
    return done;
  }

  orders(): readonly RunOrder[] {
    const chain = this.stageChain();
    const stageIndex = new Map(chain.map((id, index) => [id, index]));
    return [...this.sources.orders.values()]
      .map(order => ({
        orderId: order.orderId,
        sourceLabel: order.sourceLabel,
        stageIds: [...order.stageIds].sort((left, right) =>
          (stageIndex.get(left) ?? Number.MAX_SAFE_INTEGER)
          - (stageIndex.get(right) ?? Number.MAX_SAFE_INTEGER)
          || left.localeCompare(right)),
        evidence: order.evidence,
        stageFlips: order.stageFlips,
        ...(order.carrierEntityId === undefined ? {} : { carrierEntityId: order.carrierEntityId }),
        ...(order.targetEntityId === undefined ? {} : { targetEntityId: order.targetEntityId }),
      }))
      .sort((left, right) => left.orderId.localeCompare(right.orderId));
  }

  order(orderId: string): RunOrder | undefined {
    const order = this.sources.orders.get(orderId);
    if (order === undefined) return undefined;
    return {
      orderId: order.orderId,
      sourceLabel: order.sourceLabel,
      stageIds: [...order.stageIds],
      evidence: order.evidence,
      stageFlips: order.stageFlips,
      ...(order.carrierEntityId === undefined ? {} : { carrierEntityId: order.carrierEntityId }),
      ...(order.targetEntityId === undefined ? {} : { targetEntityId: order.targetEntityId }),
    };
  }

  /** Latest declared stage event for one order at or before the cursor tick. */
  latestStageAt(orderId: string, tick: number): RunStageEvent | undefined {
    const bucket = this.sources.eventsByOrder.get(orderId);
    if (bucket === undefined) return undefined;
    let latest: RunStageEvent | undefined;
    for (const event of bucket) {
      if (event.tick > tick) continue;
      if (latest === undefined || event.tick > latest.tick
        || (event.tick === latest.tick && event.eventId.localeCompare(latest.eventId) > 0)) {
        latest = event;
      }
    }
    return latest;
  }

  /** All declared stage events for one order up to the cursor, oldest first. */
  stagesUpTo(orderId: string, tick: number): readonly RunStageEvent[] {
    const bucket = this.sources.eventsByOrder.get(orderId);
    if (bucket === undefined) return [];
    return bucket.filter(event => event.tick <= tick);
  }

  /** Stage events that declared no order binding (visible, attributed to 未知). */
  unboundEventsUpTo(tick: number): readonly RunStageEvent[] {
    const bucket = this.sources.eventsByOrder.get("");
    if (bucket === undefined) return [];
    return bucket.filter(event => event.tick <= tick);
  }

  checkpoints(): readonly RunCheckPoint[] {
    return this.sources.checkpoints;
  }

  requirements(): RunIndexSources["requirements"] {
    return this.sources.requirements;
  }

  entityKind(entityId: string): string | undefined {
    return this.sources.entityKinds.get(entityId);
  }

  /** Verifier goals whose metric evidence references one of the order's artifacts. */
  verifierGoalsFor(order: RunOrder): RunOrderEvidence["verifierGoals"] {
    const verifier = this.sources.verifier;
    if (verifier === null) return [];
    const digests = new Set(order.evidence.map(reference => reference.digest));
    const artifactIds = new Set(order.evidence.map(reference => reference.artifact_id));
    const result: {
      goalId: string;
      passed: boolean;
      metrics: {
        metricId: string;
        unit: string | null;
        value: number;
        evidence: readonly ArtifactReference[];
      }[];
    }[] = [];
    for (const goal of verifier.goals) {
      const metrics = goal.metrics
        .filter(metric => metric.evidence.some(reference =>
          digests.has(reference.digest) || artifactIds.has(reference.artifact_id)))
        .map(metric => ({
          metricId: metric.metric_id,
          unit: metric.unit,
          value: metric.value,
          evidence: metric.evidence,
        }));
      if (metrics.length > 0) {
        result.push({ goalId: goal.goal_id, passed: goal.passed, metrics });
      }
    }
    return result;
  }

  /** Mission requirements that touch this order's stage names or evidence targets. */
  requirementsFor(order: RunOrder): RunOrderEvidence["requirements"] {
    const stageNames = new Set(order.stageIds);
    const targets = new Set(
      order.evidence.map(reference => reference.selector).filter(selector => selector.length > 0));
    const result = this.sources.requirements.filter(requirement =>
      stageNames.has(requirement.requirementId)
      || (requirement.targetId !== undefined && targets.has(requirement.targetId)));
    return result;
  }

  fullEvidence(order: RunOrder, tick: number): RunOrderEvidence {
    return {
      stageEvents: this.stagesUpTo(order.orderId, tick),
      verifierGoals: this.verifierGoalsFor(order),
      requirements: this.requirementsFor(order),
    };
  }
}

/** One joined row for the selected entity's business panel at the cursor. */
export interface EntityCargoView {
  readonly entityId: string;
  readonly entityKind?: string;
  /** Business records that explicitly name this entity as carrier. */
  readonly orders: readonly BusinessIdentityView[];
  /** Latest stage events the trace declared without an order binding. */
  readonly unboundEvents: readonly RunStageEvent[];
  /** The sample's own mode attribute (HOLD/TAKEOFF/LAND or SUMO lifecycle), if declared. */
  readonly phase?: string;
  /** Attributes copied verbatim from the current StateSample. */
  readonly attributes: readonly { readonly name: string; readonly value: StateAttribute["value"] }[];
}

/**
 * Build the cargo/business view of one entity at one tick from the compiled
 * indexes plus the current sample. O(orders + unbound events at tick).
 * Dynamic entity ids are NOT parcel ids: without an explicit business
 * record the panel says UNKNOWN.
 */
export function entityCargoView(
  index: RunIndex,
  trace: RunBusinessSource,
  entityId: string,
  tick: number,
  sample?: StateSample | null,
): EntityCargoView {
  void trace;
  const identity = index.identity;
  const orders = identity === null
    ? []
    : identity.ordersAt(entityId, tick);
  const unboundEvents = index.unboundEventsUpTo(tick).slice(-6);
  return {
    entityId,
    ...(index.entityKind(entityId) === undefined ? {} : { entityKind: index.entityKind(entityId) }),
    orders,
    unboundEvents,
    ...(sample?.mode === undefined || sample.mode === null ? {} : { phase: sample.mode }),
    attributes: (sample?.attributes ?? []).map(attribute => ({
      name: attribute.name, value: attribute.value,
    })),
  };
}

/** One selected-frame business result shared by the dock and entity inspector. */
export interface SelectedFrameBusinessView extends EntityCargoView {
  readonly cursorTick: number;
  readonly sourceKind?: string;
}

/** Short map copy only; the original identifier remains in the inspector and marker metadata. */
export function compactCargoLabel(id: string): string {
  return id.length <= 13 ? id : `${id.slice(0, 4)}…${id.slice(-7)}`;
}

export interface BusinessDestinationMarker {
  readonly destinationId: string;
  readonly position: ResolvedCoordinate;
}

/** Exact current custody position; an unresolved facility never inherits its former carrier's position. */
export function businessParcelAnchor(parcelId: string, business: SelectedFrameBusinessView | null,
  scenario: PublicScenario | null, frame: SceneState | null): { readonly holderId: string; readonly position: ResolvedCoordinate } | null {
  if (business === null || scenario === null || frame?.at.tick !== business.cursorTick) return null;
  const order = business.orders.find(item => item.parcelId === parcelId);
  if (order?.custodyId === undefined || (order.custodyKind !== "entity" && order.custodyKind !== "facility")) return null;
  const definition = scenario.entities.find(entity => entity.entity_id === order.custodyId);
  if (definition === undefined) return null;
  const sample = frame.samples.find(item => item.entity_id === order.custodyId && item.at.tick === frame.at.tick);
  const position = sample?.pose.position ?? (definition.state === "static" ? definition.initial_pose.position : undefined);
  if (position === undefined || ![position.enu.east_m, position.enu.north_m, position.enu.up_m,
    position.wgs84.latitude_deg, position.wgs84.longitude_deg].every(Number.isFinite)) return null;
  return { holderId: order.custodyId, position };
}

/** Exact authored entity identity and current public position only; proximity never binds a destination. */
export function businessDestinationMarkers(business: SelectedFrameBusinessView | null,
  scenario: PublicScenario | null, frame: SceneState | null): BusinessDestinationMarker[] {
  if (business === null || scenario === null || frame?.at.tick !== business.cursorTick) return [];
  const destinations = new Set(business.orders.map(order => order.destinationId).filter((id): id is string => id !== undefined));
  const result: BusinessDestinationMarker[] = [];
  for (const destinationId of destinations) {
    const definition = scenario.entities.find(entity => entity.entity_id === destinationId);
    if (definition === undefined) continue;
    const sample = frame.samples.find(item => item.entity_id === destinationId && item.at.tick === frame.at.tick);
    const position = sample?.pose.position ?? (definition.state === "static" ? definition.initial_pose.position : undefined);
    if (position === undefined || ![position.enu.east_m, position.enu.north_m, position.enu.up_m,
      position.wgs84.latitude_deg, position.wgs84.longitude_deg].every(Number.isFinite)) continue;
    result.push({ destinationId, position });
  }
  return result;
}

export function selectedFrameBusinessView(
  index: RunIndex,
  trace: RunBusinessSource,
  entityId: string,
  tick: number,
  sample?: StateSample | null,
): SelectedFrameBusinessView {
  return {
    ...entityCargoView(index, trace, entityId, tick, sample !== null && sample !== undefined
      && sample.at.tick <= tick ? sample : null),
    cursorTick: tick,
    ...(index.identity === null ? {} : { sourceKind: index.identity.source.source_kind }),
  };
}

/** Format a sim-time seconds value as mm:ss.s; UNKNOWN stays explicit. */
export function formatRunClock(timeSeconds: number | undefined): string {
  if (timeSeconds === undefined || !Number.isFinite(timeSeconds)) return UNKNOWN_LABEL_ZH;
  const minutes = Math.floor(timeSeconds / 60);
  const rest = timeSeconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

/** Human label for a declared stage/state token; unknown tokens pass through. */
export function stageLabel(state: string | undefined): string {
  return state ?? UNKNOWN_LABEL_ZH;
}

/**
 * Collapse a stage chain into short badges: declared stage ids in chain
 * order; states that are not chain stages appear after them in encounter
 * order. Pure display; the data stays the declared strings.
 */
export function stageChainBadges(chain: readonly string[], states: readonly (string | undefined)[]): string[] {
  const badges = [...chain];
  for (const state of states) {
    if (state === undefined || badges.includes(state)) continue;
    badges.push(state);
  }
  return badges;
}

/** Extract the scenario entity-kind map once per trace load. */
export function entityKindsOf(scenario: PublicScenario): ReadonlyMap<string, string> {
  return new Map(scenario.entities.map(entity => [entity.entity_id, entity.kind]));
}

/**
 * Selection decision for one clicked business order at the cursor: the only
 * selectable target is the record's explicit `carrier_entity_id`. Records
 * without a declared carrier select nothing — a dynamic scene entity id is
 * never substituted for a missing business identity.
 */
export function p02OrderSelection(
  record: Pick<BusinessIdentityView, "orderId" | "carrierEntityId"> | undefined,
): { kind: "entity"; id: string } | null {
  return record?.carrierEntityId === undefined ? null : { kind: "entity", id: record.carrierEntityId };
}

/** Elevated oblique camera pose in declared frame ENU metres (pure math). */
export interface OverviewPoseEnu {
  readonly position: { readonly east: number; readonly north: number; readonly up: number };
  readonly target: { readonly east: number; readonly north: number; readonly up: number };
}

/**
 * Task-region overview pose: fit the declared ENU points (valid carrier
 * positions plus, at the caller's option, the mapped city envelope corners)
 * into one elevated oblique view. The pitch stays well above facade height
 * so the frame reads as a region overview, not a close facade shot. Pure
 * coordinate math over the caller's own frame authority — no offsets are
 * invented here; callers convert through the map's existing ENU mapping.
 * Returns null when no finite point exists (the caller surfaces UNKNOWN and
 * never auto-follows an unresolved position).
 */
export function p02OverviewPose(
  points: readonly { readonly east: number; readonly north: number; readonly up: number }[],
  minSpanM = 260,
): OverviewPoseEnu | null {
  let minEast = Infinity;
  let maxEast = -Infinity;
  let minNorth = Infinity;
  let maxNorth = -Infinity;
  let maxUp = -Infinity;
  let finite = false;
  for (const point of points) {
    if (!Number.isFinite(point.east) || !Number.isFinite(point.north) || !Number.isFinite(point.up)) continue;
    finite = true;
    minEast = Math.min(minEast, point.east);
    maxEast = Math.max(maxEast, point.east);
    minNorth = Math.min(minNorth, point.north);
    maxNorth = Math.max(maxNorth, point.north);
    maxUp = Math.max(maxUp, point.up);
  }
  if (!finite) return null;
  const centerEast = (minEast + maxEast) / 2;
  const centerNorth = (minNorth + maxNorth) / 2;
  const span = Math.max(maxEast - minEast, maxNorth - minNorth, minSpanM);
  const target = { east: centerEast, north: centerNorth, up: Math.min(maxUp, 40) };
  // Oblique from the south-west at a steep elevation: region scale, not street level.
  const position = {
    east: centerEast - span * 0.42,
    north: centerNorth - span * 0.58,
    up: Math.max(maxUp, 24) + span * 0.85,
  };
  return { position, target };
}

export type { MutableOrder };
