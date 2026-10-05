// Owned host adapter — PR10 structured Ref / typed-state projection.
//
// Attribution: adapted from the P02 parcel prototype
// (validation/p02-parcel-host/source @ 15f473a4dc0ed4f80e3000b0acef6e875c617393,
// files src/fixture.js and src/scene.js, Apache-2.0 per the checkout's
// LICENSE) and from the PR10 predicate-binding contracts
// (validation/p02-parcel-host/binding-source/validation/predicate-binding-prototype/contracts.py,
// Apache-2.0): the exact Ref shape {run_id, epoch, id, generation, ref_type},
// integer-vs-string generation distinctness and per-record availability
// fields follow those contracts without importing them.
//
// BENCH interface authority (read-only):
// - frontend/src/generated/aero-bench-contracts.ts  SceneState/StateSample
//   (aero-bench.scene-state/v1: SimulationTime.tick minimum 0, sim_time_ns >= 0)
// - frontend/src/state/selection.ts, replay.ts, target.ts
//
// Ingestion/projection split (the hot-path requirement):
// - ingestSceneState / ingestSceneContext / ingestFrameEvidence /
//   ingestRuleEvidence run ONCE per supplied record: full shape validation,
//   duplicate-Ref rejection and exact-key index construction happen here.
// - projectHostViewFrame runs per presented tick and performs ONLY state and
//   availability projection over the prebuilt immutable indexes: map lookups,
//   availability comparison and attachment arithmetic. No schema re-checks,
//   no regex, no duplicate detection, no throws on the presentation path.
//
// Identity rules (no ontology invented):
// - Exact opaque ids. Dotted ids never split; the id regex is the BENCH
//   contract pattern, checked at ingestion only.
// - generation is an integer >= 1 or a nonempty string, and the two are
//   DISTINCT identities (Ref key encoding is type-tagged: "i:1" vs "s:1").
// - epoch/generation/availability absent from a source stay UNKNOWN — visible
//   in the projected frame, never synthesized from digests, run prefixes or
//   ticks. Host-declared scene context (epoch/generation/revision for a run)
//   is an explicit mapper input, exactly like the PR10 provider mapper.
// - Evidence is joined by exact run/epoch/revision/typed-Ref context. A Ref
//   whose epoch/generation is UNKNOWN cannot join an actual-identity Ref.

/** View-frame schema this host speaks, per INTEGRATION.md. */
export const VIEW_FRAME_SCHEMA = 'p02.parcel-view-frame/v1';
/** BENCH SceneState schema version the adapter accepts, per the contract. */
export const SCENE_STATE_SCHEMA = 'aero-bench.scene-state/v1';
const STATE_SAMPLE_SCHEMA = 'aero-bench.state-sample/v1';
/** Host evidence envelope schema versions (typed, separately supplied). */
export const HOST_FRAME_EVIDENCE_SCHEMA = 'p02.parcel-host.frame-evidence/v2';
export const HOST_RULE_EVIDENCE_SCHEMA = 'p02.parcel-host.rule-evidence/v2';
/** Demo-only marker written on every parcel/geometry value this host invents. */
export const HOST_DEMO_AUTHORITY = 'host-demo-evidence';
/**
 * Visible sentinel for identity a source does not carry. Display-only: it
 * never participates in a Ref join (a Ref carrying it cannot match a Ref with
 * an actual identity because join readiness checks the sentinel explicitly).
 */
export const UNKNOWN_IDENTITY = 'UNKNOWN';

/**
 * Typed feed-identity markers for the scene-state feed (PR12 truth-in-source
 * pass). 'demo' marks an authored feed (demo.motion samples); 'bench' is set
 * only by an actual sealed BENCH replay loader. The view renders the fed
 * identity verbatim; nothing infers it from ids, digests or provider names.
 */
export const FEED_MOTION_DEMO = 'demo';
export const FEED_MOTION_BENCH = 'bench';

/**
 * Explicit causal response reference, carried on an event record when — and
 * only when — the source declares one. Fields (all optional, all typed):
 * - response_rule_id: the rule this event claims to respond to (exact string)
 * - response_flip_time_seconds: the truth-flip instant it claims to answer
 * Ingestion validates the shape and preserves both fields verbatim. Nothing
 * infers causality from event kind, timing or proximity; the view may present
 * an event as a rule response only when it declares a matching explicit
 * reference (see view.js). Absent fields render as an unbound response.
 */
export const EVENT_RESPONSE_REF_FIELDS = ['response_rule_id', 'response_flip_time_seconds'];

function optionalTimeString(value, what) {
  if (value === undefined || value === null) return null;
  if (!Number.isFinite(value)) {
    throw new TypeError(`${what} must be a finite number of seconds when supplied`);
  }
  return value;
}

const isPlainObject = value =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const finiteTuple3 = (value, what) => {
  if (!Array.isArray(value) || value.length !== 3 ||
      value.some(v => !Number.isFinite(v))) {
    throw new TypeError(`${what} must be three finite numbers`);
  }
  return value;
};

/** Same contract value check the SceneState schema enforces on entity ids. */
const contractEntityId = value => {
  if (typeof value !== 'string' || !/^[a-z][a-z0-9_.-]*$/.test(value)) {
    throw new TypeError(`entity_id must match ^[a-z][a-z0-9_.-]*$, got: ${String(value)}`);
  }
  return value;
};

// ---------------------------------------------------------------------------
// Ref — exact typed identity (mirrors contracts.py Ref; no ontology added)
// ---------------------------------------------------------------------------

/** Validate and freeze one Ref. Throws on contract violations (ingestion). */
export function assertRef(candidate) {
  if (!isPlainObject(candidate)) throw new TypeError('Ref must be an object');
  for (const name of ['run_id', 'epoch', 'id']) {
    if (typeof candidate[name] !== 'string' || !candidate[name].trim()) {
      throw new TypeError(`Ref.${name} must be a nonempty string`);
    }
  }
  const g = candidate.generation;
  const generationOk = (typeof g === 'number' && Number.isSafeInteger(g) && g >= 1) ||
    (typeof g === 'string' && g.trim().length > 0);
  if (!generationOk) {
    throw new TypeError('Ref.generation must be a positive integer or nonempty string');
  }
  const refType = candidate.ref_type ?? 'object';
  if (refType !== 'object' && refType !== 'relation') {
    throw new TypeError('Ref.ref_type must be object or relation');
  }
  return Object.freeze({
    run_id: candidate.run_id,
    epoch: candidate.epoch,
    id: candidate.id,
    // Integer 1 and string "1" are different generations and different keys.
    generation: g,
    generationIsInteger: typeof g === 'number',
    ref_type: refType,
  });
}

/**
 * Exact join key for a Ref. The generation is type-tagged so integer 1 and
 * string "1" never collide. A Ref with an UNKNOWN component produces a key
 * that cannot equal any actual-identity key of a real record.
 */
export function makeRefKey(ref) {
  const checked = assertRef(ref);
  const generation = checked.generationIsInteger
    ? `i:${checked.generation}`
    : `s:${checked.generation}`;
  return JSON.stringify([checked.ref_type, checked.run_id, checked.epoch, checked.id, generation]);
}

/** True when two Refs are the exact same typed identity. */
export function sameRef(a, b) {
  return makeRefKey(a) === makeRefKey(b);
}

// ---------------------------------------------------------------------------
// SceneState ingestion — validated and indexed ONCE
// ---------------------------------------------------------------------------

/** Optional explicit scene context (the PR10 provider-mapper role). */
export function ingestSceneContext(decl) {
  if (!isPlainObject(decl)) throw new TypeError('scene context must be an object');
  if (typeof decl.run_id !== 'string' || !/^[0-9a-f]{64}$/.test(decl.run_id)) {
    throw new TypeError('scene context run_id must be a 64-hex run id');
  }
  if (typeof decl.scenario_digest !== 'string' || !/^[0-9a-f]{64}$/.test(decl.scenario_digest)) {
    throw new TypeError('scene context scenario_digest must be 64-hex');
  }
  const out = { run_id: decl.run_id, scenario_digest: decl.scenario_digest };
  if (decl.epoch !== undefined && decl.epoch !== null) {
    if (typeof decl.epoch !== 'string' || !decl.epoch.trim()) {
      throw new TypeError('scene context epoch must be a nonempty string when supplied');
    }
    out.epoch = decl.epoch;
  }
  if (decl.generation !== undefined && decl.generation !== null) {
    const g = decl.generation;
    if (!(typeof g === 'number' && Number.isSafeInteger(g) && g >= 1) &&
        !(typeof g === 'string' && g.trim())) {
      throw new TypeError('scene context generation must be a positive integer or nonempty string');
    }
    out.generation = g;
  }
  if (decl.revision !== undefined && decl.revision !== null) {
    if (typeof decl.revision !== 'string' || !decl.revision.trim()) {
      throw new TypeError('scene context revision must be a nonempty string when supplied');
    }
    out.revision = decl.revision;
  }
  return Object.freeze(out);
}

function assertBenchSceneState(scene) {
  if (!isPlainObject(scene) || scene.schema_version !== SCENE_STATE_SCHEMA) {
    throw new TypeError(`SceneState must declare ${SCENE_STATE_SCHEMA}`);
  }
  // Contract: SimulationTime.tick has minimum 0. Tick 0 is a legal record.
  if (!isPlainObject(scene.at) || !Number.isSafeInteger(scene.at.tick) || scene.at.tick < 0 ||
      !Number.isSafeInteger(scene.at.sim_time_ns) || scene.at.sim_time_ns < 0) {
    throw new TypeError('SceneState.at must carry an integer tick >= 0 and sim_time_ns >= 0');
  }
  if (typeof scene.run_id !== 'string' || !/^[0-9a-f]{64}$/.test(scene.run_id)) {
    throw new TypeError('SceneState.run_id must be a 64-hex run id');
  }
  if (typeof scene.scenario_digest !== 'string' || !/^[0-9a-f]{64}$/.test(scene.scenario_digest)) {
    throw new TypeError('SceneState.scenario_digest must be 64-hex');
  }
  if (typeof scene.scene_state_digest !== 'string' || !/^[0-9a-f]{64}$/.test(scene.scene_state_digest)) {
    throw new TypeError('SceneState.scene_state_digest must be 64-hex');
  }
  if (!Array.isArray(scene.declared_entity_ids) || !scene.declared_entity_ids.length ||
      !Array.isArray(scene.samples) || scene.samples.length !== scene.declared_entity_ids.length) {
    throw new TypeError('SceneState declared_entity_ids and samples must be nonempty and equal length');
  }
  if (new Set(scene.declared_entity_ids).size !== scene.declared_entity_ids.length) {
    throw new TypeError('SceneState declared entity ids must be unique');
  }
  return scene;
}

/**
 * Ingest one SceneState: validate once, build the immutable per-entity index.
 * The returned record is frozen and reused by every presentation tick.
 */
export function ingestSceneState(scene, sceneContext = null) {
  const validated = assertBenchSceneState(scene);
  const context = sceneContext ? ingestSceneContext({
    run_id: validated.run_id,
    scenario_digest: validated.scenario_digest,
    ...sceneContext,
  }) : null;
  if (context && (context.run_id !== validated.run_id ||
      context.scenario_digest !== validated.scenario_digest)) {
    throw new TypeError('scene context run/scenario identity differs from the SceneState');
  }
  const epoch = context?.epoch ?? null;       // null renders UNKNOWN
  const generation = context?.generation ?? null;
  const byId = new Map();
  const refByEntityId = new Map();
  const seen = new Set();
  for (const sample of validated.samples) {
    if (!isPlainObject(sample) || sample.schema_version !== STATE_SAMPLE_SCHEMA) {
      throw new TypeError('each SceneState sample must declare aero-bench.state-sample/v1');
    }
    const id = contractEntityId(sample.entity_id);
    if (!validated.declared_entity_ids.includes(id) || seen.has(id)) {
      throw new TypeError(`sample entity_id ${id} must match declared_entity_ids exactly once`);
    }
    seen.add(id);
    if (sample.run_id !== validated.run_id || sample.scenario_digest !== validated.scenario_digest) {
      throw new TypeError(`sample ${id} run/scenario identity differs from the SceneState`);
    }
    const enu = sample.pose?.position?.enu;
    const velocity = sample.linear_velocity_enu;
    const position = [enu?.east_m, enu?.north_m, enu?.up_m];
    const v = [velocity?.east_mps, velocity?.north_mps, velocity?.up_mps];
    if ([...position, ...v].some(n => !Number.isFinite(n))) {
      throw new TypeError(`sample ${id} lacks a finite ENU pose/velocity`);
    }
    byId.set(id, Object.freeze({
      id,
      position,
      velocity: v,
      providerId: sample.provider_id,
      sampleKind: sample.sample_kind,
      stage: sample.stage,
      mode: sample.mode ?? null,
      contacts: Array.isArray(sample.contacts) ? Object.freeze([...sample.contacts]) : [],
    }));
    // Exact typed Ref for the sample. epoch/generation carry the actual
    // declared context when supplied; otherwise null (rendered UNKNOWN) —
    // never synthesized from digests, run prefixes or ticks.
    refByEntityId.set(id, makeRefKey({
      run_id: validated.run_id,
      epoch: epoch ?? UNKNOWN_IDENTITY,
      id,
      generation: generation ?? UNKNOWN_IDENTITY,
      ref_type: 'object',
    }));
  }
  if (seen.size !== validated.declared_entity_ids.length) {
    throw new TypeError('SceneState samples must cover every declared entity exactly once');
  }
  return Object.freeze({
    runId: validated.run_id,
    scenarioDigest: validated.scenario_digest,
    sceneStateDigest: validated.scene_state_digest,
    tick: validated.at.tick,
    simTimeNs: validated.at.sim_time_ns,
    declaredEntityIds: Object.freeze([...validated.declared_entity_ids]),
    contextDeclared: context !== null,
    epoch,
    generation,
    revision: context?.revision ?? null,
    // Exact context key the mount uses to join evidence: actual declared
    // identity only. With no declared context the UNKNOWN components produce
    // a key that cannot equal any real evidence context key.
    contextKey: makeRefKey({
      run_id: validated.run_id,
      epoch: epoch ?? UNKNOWN_IDENTITY,
      id: 'context',
      generation: generation ?? UNKNOWN_IDENTITY,
      ref_type: 'object',
    }),
    byId,
    refByEntityId,
  });
}

// ---------------------------------------------------------------------------
// Evidence ingestion — validated and indexed ONCE, keyed by exact Ref context
// ---------------------------------------------------------------------------

function assertAvailability(record, what) {
  const out = { availableAfterCommit: 0, availableNs: null };
  if (record.available_after_commit !== undefined && record.available_after_commit !== null) {
    if (!Number.isSafeInteger(record.available_after_commit) || record.available_after_commit < 0) {
      throw new TypeError(`${what} available_after_commit must be an integer >= 0`);
    }
    out.availableAfterCommit = record.available_after_commit;
  }
  if (record.available_ns !== undefined && record.available_ns !== null) {
    if (!Number.isSafeInteger(record.available_ns) || record.available_ns < 0) {
      throw new TypeError(`${what} available_ns must be an integer >= 0 (evidence availability, never wall clock)`);
    }
    out.availableNs = record.available_ns;
  }
  return out;
}

function assertEvidenceContext(envelope, schema) {
  const context = envelope.context;
  if (!isPlainObject(context)) {
    throw new TypeError(`${schema} requires context: { run_id, epoch, generation, revision }`);
  }
  for (const name of ['run_id', 'epoch', 'revision']) {
    if (typeof context[name] !== 'string' || !context[name].trim()) {
      throw new TypeError(`evidence context.${name} must be a nonempty string`);
    }
  }
  const g = context.generation;
  if (!(typeof g === 'number' && Number.isSafeInteger(g) && g >= 1) &&
      !(typeof g === 'string' && g.trim())) {
    throw new TypeError('evidence context.generation must be a positive integer or nonempty string');
  }
  return Object.freeze({
    runId: context.run_id,
    epoch: context.epoch,
    generation: g,
    generationIsInteger: typeof g === 'number',
    revision: context.revision,
  });
}

function assertParcelEvidence(parcel, context) {
  if (!isPlainObject(parcel)) throw new TypeError('parcel evidence must be an object');
  contractEntityId(parcel.id);
  if (!isPlainObject(parcel.source) || typeof parcel.source.pointer !== 'string' ||
      !parcel.source.pointer) {
    throw new TypeError(`parcel ${parcel.id} evidence needs source.pointer`);
  }
  const availability = assertAvailability(parcel, `parcel ${parcel.id}`);
  const generation = parcel.generation !== undefined && parcel.generation !== null
    ? parcel.generation
    : context.generation;
  const ref = assertRef({
    run_id: context.runId,
    epoch: context.epoch,
    id: parcel.id,
    generation,
    ref_type: 'object',
  });
  const out = {
    ref,
    refKey: makeRefKey(ref),
    authority: parcel.source.authority ?? HOST_DEMO_AUTHORITY,
    provenance: parcel.source.provenance ?? 'demo',
    sourcePointer: parcel.source.pointer,
    sourceFrameId: parcel.source.frameId ?? null,
    dimensionsM: null,
    massKg: null,
    state: null,
    custodianId: null,
    attachment: null,
    transfer: null,
    target: null,
    ...availability,
  };
  if (parcel.dimensions_m !== undefined && parcel.dimensions_m !== null) {
    if (!Array.isArray(parcel.dimensions_m) || parcel.dimensions_m.length !== 3 ||
        parcel.dimensions_m.some(v => !Number.isFinite(v) || v <= 0)) {
      throw new TypeError(`parcel ${parcel.id} dimensions_m must be three positive finite numbers`);
    }
    out.dimensionsM = Object.freeze([...parcel.dimensions_m]);
  }
  if (parcel.mass_kg !== undefined && parcel.mass_kg !== null) {
    if (!Number.isFinite(parcel.mass_kg) || parcel.mass_kg <= 0) {
      throw new TypeError(`parcel ${parcel.id} mass_kg must be a positive finite number`);
    }
    out.massKg = parcel.mass_kg;
  }
  if (parcel.state !== undefined && parcel.state !== null) {
    if (typeof parcel.state !== 'string' || !parcel.state) {
      throw new TypeError(`parcel ${parcel.id} state must be a nonempty string`);
    }
    out.state = parcel.state;
  }
  if (parcel.custodian_id !== undefined && parcel.custodian_id !== null) {
    contractEntityId(parcel.custodian_id);
    out.custodianId = parcel.custodian_id;
  }
  const attachment = parcel.attachment;
  if (attachment !== undefined && attachment !== null) {
    if (!isPlainObject(attachment)) throw new TypeError(`parcel ${parcel.id} attachment must be an object`);
    contractEntityId(attachment.carrier_id);
    if (!Array.isArray(attachment.offset_enu) || attachment.offset_enu.length !== 3 ||
        attachment.offset_enu.some(v => !Number.isFinite(v))) {
      throw new TypeError(`parcel ${parcel.id} attachment.offset_enu must be three finite numbers`);
    }
    out.attachment = Object.freeze({ carrierId: attachment.carrier_id, offsetEnu: Object.freeze([...attachment.offset_enu]) });
  }
  const transfer = parcel.transfer;
  if (transfer !== undefined && transfer !== null) {
    if (!isPlainObject(transfer)) throw new TypeError(`parcel ${parcel.id} transfer must be an object`);
    contractEntityId(transfer.from_id);
    contractEntityId(transfer.to_id);
    if (!Number.isFinite(transfer.progress) || transfer.progress < 0 || transfer.progress > 1) {
      throw new TypeError(`parcel ${parcel.id} transfer.progress must be within [0,1]`);
    }
    out.transfer = Object.freeze({ fromId: transfer.from_id, toId: transfer.to_id, progress: transfer.progress });
  }
  if (parcel.target !== undefined && parcel.target !== null) {
    if (typeof parcel.target !== 'string' || !parcel.target) {
      throw new TypeError(`parcel ${parcel.id} target must be a nonempty string`);
    }
    out.target = parcel.target;
  }
  return Object.freeze(out);
}

function assertRuleEvidence(rule) {
  if (!isPlainObject(rule)) throw new TypeError('rule evidence must be an object');
  if (typeof rule.rule_id !== 'string' || !rule.rule_id) {
    throw new TypeError('rule evidence needs a nonempty rule_id');
  }
  const availability = assertAvailability(rule, `rule ${rule.rule_id}`);
  const out = {
    id: rule.rule_id,
    truth: null,
    inputs: null,
    inputSource: rule.input_source ?? null,
    engine: rule.engine ?? null,
    atlasValue: 'unknown', // Atlas is not bound anywhere in this patch
    provenance: rule.provenance ?? 'demo',
    authority: rule.authority ?? HOST_DEMO_AUTHORITY,
    sourcePointer: rule.source_pointer ?? null,
    lastFlip: null,
    ...availability,
  };
  if (rule.truth !== undefined && rule.truth !== null) {
    if (typeof rule.truth !== 'boolean') throw new TypeError(`rule ${rule.rule_id} truth must be a boolean`);
    out.truth = rule.truth;
  }
  if (rule.inputs !== undefined && rule.inputs !== null) {
    if (!isPlainObject(rule.inputs)) throw new TypeError(`rule ${rule.rule_id} inputs must be an object`);
    out.inputs = Object.freeze({ ...rule.inputs });
  }
  if (rule.last_flip_time_seconds !== undefined && rule.last_flip_time_seconds !== null) {
    if (!Number.isFinite(rule.last_flip_time_seconds)) {
      throw new TypeError(`rule ${rule.rule_id} last_flip_time_seconds must be finite`);
    }
    out.lastFlip = rule.last_flip_time_seconds;
  }
  return Object.freeze(out);
}

function assertNetworkEvidence(network) {
  if (!isPlainObject(network)) throw new TypeError('network evidence must be an object');
  if (typeof network.link_id !== 'string' || !network.link_id) {
    throw new TypeError('network evidence needs a nonempty link_id');
  }
  const availability = assertAvailability(network, `link ${network.link_id}`);
  const out = {
    id: network.link_id,
    rssiDbm: null,
    degraded: null,
    provenance: network.provenance ?? 'demo',
    authority: network.authority ?? HOST_DEMO_AUTHORITY,
    sourcePointer: network.source_pointer ?? null,
    ...availability,
  };
  if (network.rssi_dbm !== undefined && network.rssi_dbm !== null) {
    if (!Number.isFinite(network.rssi_dbm)) {
      throw new TypeError(`link ${network.link_id} rssi_dbm must be finite`);
    }
    out.rssiDbm = network.rssi_dbm;
  }
  if (network.degraded !== undefined && network.degraded !== null) {
    if (typeof network.degraded !== 'boolean') {
      throw new TypeError(`link ${network.link_id} degraded must be a boolean`);
    }
    out.degraded = network.degraded;
  }
  return Object.freeze(out);
}

function assertEventEvidence(event) {
  if (!isPlainObject(event)) throw new TypeError('event evidence must be an object');
  if (typeof event.event_id !== 'string' || !event.event_id) {
    throw new TypeError('event evidence needs a nonempty event_id');
  }
  if (!Number.isFinite(event.time_seconds)) {
    throw new TypeError(`event ${event.event_id} time_seconds must be finite`);
  }
  if (typeof event.label_key !== 'string' || !event.label_key) {
    throw new TypeError(`event ${event.event_id} label_key must be a nonempty string`);
  }
  if (!Array.isArray(event.entity_ids)) {
    throw new TypeError(`event ${event.event_id} entity_ids must be an array`);
  }
  for (const id of event.entity_ids) contractEntityId(id);
  // Explicit causal reference (typed, optional, preserved verbatim). An event
  // without one is never presented as a rule response; see EVENT_RESPONSE_REF_FIELDS.
  const responseRuleId = event.response_rule_id;
  if (responseRuleId !== undefined && responseRuleId !== null &&
      (typeof responseRuleId !== 'string' || !responseRuleId.trim())) {
    throw new TypeError(`event ${event.event_id} response_rule_id must be a nonempty string when supplied`);
  }
  const responseFlip = optionalTimeString(
    event.response_flip_time_seconds, `event ${event.event_id} response_flip_time_seconds`);
  return Object.freeze({
    id: event.event_id,
    time: event.time_seconds,
    labelKey: event.label_key,
    kind: event.kind ?? 'script',
    entities: Object.freeze([...event.entity_ids]),
    from: event.from_id ?? null,
    to: event.to_id ?? null,
    dependsOn: Array.isArray(event.depends_on) ? Object.freeze([...event.depends_on]) : null,
    responseRuleId: responseRuleId ?? null,
    responseFlipTime: responseFlip,
    provenance: event.provenance ?? 'demo',
    authority: event.authority ?? HOST_DEMO_AUTHORITY,
  });
}

/**
 * Ingest one frame-evidence envelope (schema v2): validate once and build the
 * exact-key index. Records are keyed by their complete typed Ref, so evidence
 * for one run/epoch/generation can never join another, regardless of tick.
 */
export function ingestFrameEvidence(envelope) {
  if (!isPlainObject(envelope) || envelope.schema_version !== HOST_FRAME_EVIDENCE_SCHEMA) {
    throw new TypeError(`frame evidence must use ${HOST_FRAME_EVIDENCE_SCHEMA}`);
  }
  const context = assertEvidenceContext(envelope, HOST_FRAME_EVIDENCE_SCHEMA);
  const evidence = envelope.evidence;
  if (!isPlainObject(evidence)) throw new TypeError('evidence must be an object');
  const parcels = evidence.parcels ?? [];
  const events = evidence.events ?? [];
  const stations = evidence.stations ?? [];
  const routes = evidence.routes ?? null;
  if (!Array.isArray(parcels)) throw new TypeError('evidence.parcels must be an array');
  if (!Array.isArray(events)) throw new TypeError('evidence.events must be an array');
  if (!Array.isArray(stations)) throw new TypeError('evidence.stations must be an array');
  if (routes !== null && routes !== undefined && !isPlainObject(routes)) {
    throw new TypeError('evidence.routes must be an object or null');
  }
  const parcelIndex = new Map();
  for (const parcel of parcels) {
    const record = assertParcelEvidence(parcel, context);
    if (parcelIndex.has(record.refKey)) {
      throw new TypeError(`duplicate parcel Ref for ${record.ref.id} (${record.refKey})`);
    }
    parcelIndex.set(record.refKey, record);
  }
  const eventIndex = new Map();
  for (const event of events) {
    const record = assertEventEvidence(event);
    if (eventIndex.has(record.id)) throw new TypeError(`duplicate event evidence for ${record.id}`);
    eventIndex.set(record.id, record);
  }
  const stationIndex = new Map();
  for (const station of stations) {
    if (!isPlainObject(station)) throw new TypeError('station evidence must be an object');
    contractEntityId(station.id);
    finiteTuple3(station.position_enu, `station ${station.id} position_enu`);
    if (station.id === 'floor' || station.id === 'compass') {
      throw new TypeError(`station id ${station.id} is reserved by the host scene`);
    }
    if (stationIndex.has(station.id)) {
      throw new TypeError(`duplicate station evidence for ${station.id}`);
    }
    stationIndex.set(station.id, Object.freeze({
      id: station.id,
      label: station.label ?? station.id,
      labelKey: station.label_key ?? null,
      position: Object.freeze([...station.position_enu]),
      authority: station.authority ?? HOST_DEMO_AUTHORITY,
      sourcePointer: station.source_pointer ?? `evidence.stations[id=${station.id}]`,
    }));
  }
  let routeIndex = null;
  if (routes !== null && routes !== undefined) {
    routeIndex = new Map();
    for (const key of Object.keys(routes)) {
      const points = routes[key];
      if (!Array.isArray(points) || points.length < 2 ||
          points.some(p => !Array.isArray(p) || p.length !== 3 || p.some(v => !Number.isFinite(v)))) {
        throw new TypeError(`route ${key} must be at least two [east,north,up] points`);
      }
      routeIndex.set(key, Object.freeze(points.map(p => Object.freeze([...p]))));
    }
    if (!routeIndex.size) throw new TypeError('evidence.routes must carry at least one named route');
  }
  return Object.freeze({
    context,
    contextKey: makeRefKey({ run_id: context.runId, epoch: context.epoch, id: 'context', generation: context.generation }),
    parcels: parcelIndex,
    events: eventIndex,
    stations: stationIndex,
    routes: routeIndex,
    network: evidence.network ? assertNetworkEvidence(evidence.network) : null,
  });
}

export function ingestRuleEvidence(envelope) {
  if (envelope === null || envelope === undefined) {
    return Object.freeze({ context: null, rule: null });
  }
  if (!isPlainObject(envelope) || envelope.schema_version !== HOST_RULE_EVIDENCE_SCHEMA) {
    throw new TypeError(`rule evidence must use ${HOST_RULE_EVIDENCE_SCHEMA}`);
  }
  const context = assertEvidenceContext(envelope, HOST_RULE_EVIDENCE_SCHEMA);
  const evidence = envelope.evidence;
  if (!isPlainObject(evidence)) throw new TypeError('rule evidence must carry an evidence object');
  const rule = Object.keys(evidence).filter(key => key !== 'at_tick').length
    ? assertRuleEvidence(evidence)
    : null; // cleared envelope: rule truth explicitly absent
  return Object.freeze({
    context,
    contextKey: makeRefKey({ run_id: context.runId, epoch: context.epoch, id: 'context', generation: context.generation }),
    rule,
  });
}

/** Validate an arbitrary projected frame (tests and simple callers). */
export function assertViewFrame(frame) {
  if (!frame || frame.schemaVersion !== VIEW_FRAME_SCHEMA) {
    throw new TypeError('Unsupported view-frame schema');
  }
  if (typeof frame.frameKey !== 'string' || !frame.frameKey ||
      !Number.isInteger(frame.tick) || frame.tick < 0 || !Number.isFinite(frame.timeSeconds)) {
    throw new TypeError('Invalid frame identity/time');
  }
  if (!Array.isArray(frame.entities) || !Array.isArray(frame.parcels)) {
    throw new TypeError('Entities and parcels must be explicit arrays');
  }
  const ids = new Set();
  for (const item of [...frame.entities, ...frame.parcels]) {
    if (typeof item.id !== 'string' || !item.id.trim() || ids.has(item.id)) {
      throw new TypeError('Entity IDs must be nonempty and unique');
    }
    ids.add(item.id);
    if (item.position !== null || item.kind !== 'parcel') {
      if (!Array.isArray(item.position) || item.position.length !== 3 ||
          item.position.some(v => !Number.isFinite(v) || Math.abs(v) > 1e9)) {
        throw new TypeError(`Invalid bounded ENU position on ${item.id}`);
      }
    }
  }
  return frame;
}

// ---------------------------------------------------------------------------
// Projection — per presented tick, over prebuilt indexes only.
// No validation, no regex, no duplicate detection, no throws here.
// ---------------------------------------------------------------------------

const isAvailable = (record, scene) =>
  record.availableAfterCommit <= scene.tick &&
  (record.availableNs === null || record.availableNs <= scene.simTimeNs);

const unknownRule = Object.freeze({
  id: null, value: null, inputs: null, inputsKnown: false, inputSource: null,
  provenance: null, engine: null, atlasValue: 'unknown', lastFlip: null,
});

/**
 * Project one view frame from ingested scene + ingested evidence.
 * Identity joins are exact Ref-key comparisons; anything that does not join
 * or is not yet available renders as visible UNKNOWN/absent — never as
 * another tick's or another run's values.
 */
export function projectHostViewFrame(scene, frameEvidence, ruleEvidence, options = {}) {
  const epochShown = scene.epoch ?? UNKNOWN_IDENTITY;
  const generationShown = scene.generation ?? UNKNOWN_IDENTITY;
  const entities = [];
  const parcels = [];
  const unresolvedParcelIds = [];

  // Scene entities: real BENCH motion records from the prebuilt index.
  // An id that evidence declares as a parcel is projected in the parcels
  // list only — never as both an entity and a parcel (frame ids are unique).
  const parcelEvidenceIds = new Set();
  for (const parcel of (frameEvidence?.parcels ?? new Map()).values()) {
    parcelEvidenceIds.add(parcel.ref.id);
  }
  for (const id of scene.declaredEntityIds) {
    if (parcelEvidenceIds.has(id)) continue;
    const record = scene.byId.get(id);
    entities.push(Object.freeze({
      id,
      label: options.entityLabel?.(id) ?? id,
      kind: options.entityKind?.(id) ?? 'facility',
      labelKey: options.entityLabelKey?.(id) ?? null,
      position: record.position,
      velocity: record.velocity,
      state: options.entityState?.(id, record) ?? null,
      generation: scene.generation, // actual declared value or null (UNKNOWN)
      refKey: scene.refByEntityId.get(id),
      source: {
        pointer: `SceneState.samples[entity_id=${id}]`,
        frameId: `${scene.runId.slice(0, 8)}#${scene.tick}`,
        authority: record.providerId,
        provenance: 'reported',
      },
      providerId: record.providerId,
      sampleKind: record.sampleKind,
      sourceKind: 'scene-state',
    }));
  }
  const entityById = new Map(entities.map(e => [e.id, e]));

  // Host stations: display geometry. A station id matching a scene entity
  // merges display labels only (a replaced record — projected entities are
  // frozen); the SceneState position stays authoritative.
  for (const station of (frameEvidence?.stations ?? new Map()).values()) {
    const entity = entityById.get(station.id);
    if (entity) {
      const merged = {
        ...entity,
        label: station.label || entity.label,
        labelKey: station.labelKey ?? entity.labelKey,
        kind: 'station',
      };
      entities.splice(entities.indexOf(entity), 1, Object.freeze(merged));
      entityById.set(station.id, merged);
      continue;
    }
    const stationEntity = {
      id: station.id,
      label: station.label,
      labelKey: station.labelKey,
      kind: 'station',
      position: station.position,
      velocity: [0, 0, 0],
      state: null,
      generation: null,
      refKey: null,
      source: { pointer: station.sourcePointer, frameId: null, authority: station.authority, provenance: 'demo' },
      sourceKind: 'host-evidence',
    };
    entities.push(stationEntity);
    entityById.set(station.id, stationEntity);
  }

  // Parcel join by exact typed Ref key. Evidence context whose epoch or
  // generation is UNKNOWN never joins an actual-identity scene Ref: missing
  // identity stays visible instead of being fabricated. Late availability
  // (available_after_commit / available_ns) keeps the record listed and
  // unknown until its own availability gate passes at the presented tick.
  for (const [refKey, parcel] of (frameEvidence?.parcels ?? new Map())) {
    const sceneRefKey = scene.refByEntityId.get(parcel.ref.id);
    const inScene = scene.byId.has(parcel.ref.id);
    const joins = sceneRefKey !== undefined && sceneRefKey === refKey && inScene;
    if (!joins) {
      unresolvedParcelIds.push(parcel.ref.id);
      parcels.push(Object.freeze({
        id: parcel.ref.id,
        label: options.parcelLabel?.(parcel.ref.id, parcel) ?? parcel.ref.id,
        kind: 'parcel',
        generation: null,
        state: 'unknown',
        custodian: null,
        custodianDeclared: parcel.custodianId,
        position: null,
        velocity: null,
        attachment: null,
        attachmentInconsistent: false,
        transfer: parcel.transfer ?? null,
        dimensionsM: parcel.dimensionsM ?? [0.45, 0.32, 0.25],
        massKg: parcel.massKg ?? null,
        target: parcel.target ?? null,
        ref: parcel.ref,
        refKey,
        joinReason: sceneRefKey === undefined ? 'no-scene-sample' : 'identity-mismatch',
        available: isAvailable(parcel, scene),
        source: {
          pointer: parcel.sourcePointer,
          frameId: parcel.sourceFrameId,
          authority: parcel.authority,
          provenance: parcel.provenance,
        },
        sourceKind: 'host-evidence',
        demoEvidence: true,
        custodyKnown: false,
      }));
      continue;
    }
    if (!isAvailable(parcel, scene)) {
      unresolvedParcelIds.push(parcel.ref.id);
      parcels.push(Object.freeze({
        id: parcel.ref.id,
        label: options.parcelLabel?.(parcel.ref.id, parcel) ?? parcel.ref.id,
        kind: 'parcel',
        generation: null,
        state: 'unknown',
        custodian: null,
        custodianDeclared: parcel.custodianId,
        position: null,
        velocity: null,
        attachment: null,
        attachmentInconsistent: false,
        transfer: null,
        dimensionsM: parcel.dimensionsM ?? [0.45, 0.32, 0.25],
        massKg: parcel.massKg ?? null,
        target: parcel.target ?? null,
        ref: parcel.ref,
        refKey,
        joinReason: 'not-yet-available',
        availableAfterCommit: parcel.availableAfterCommit,
        availableNs: parcel.availableNs,
        available: false,
        source: {
          pointer: parcel.sourcePointer,
          frameId: parcel.sourceFrameId,
          authority: parcel.authority,
          provenance: parcel.provenance,
        },
        sourceKind: 'host-evidence',
        demoEvidence: true,
        custodyKnown: false,
      }));
      continue;
    }
    const motion = scene.byId.get(parcel.ref.id);
    let attachment = parcel.attachment ?? null;
    let attachmentInconsistent = false;
    if (attachment) {
      const carrier = entityById.get(attachment.carrierId);
      // Projection-time rigidity arithmetic (state projection, not validation):
      // an inconsistent combination is shown as detached-with-flag, never
      // drawn attached and never thrown.
      attachmentInconsistent = !carrier || parcel.custodianId !== attachment.carrierId ||
        motion.position.some((v, i) => Math.abs(v - carrier.position[i] - attachment.offsetEnu[i]) > 1e-6);
      if (attachmentInconsistent) attachment = null;
    }
    const custodian = parcel.custodianId !== null && entityById.has(parcel.custodianId)
      ? parcel.custodianId
      : null;
    parcels.push(Object.freeze({
      id: parcel.ref.id,
      label: options.parcelLabel?.(parcel.ref.id, parcel) ?? parcel.ref.id,
      kind: 'parcel',
      generation: scene.generation,
      state: parcel.state ?? 'unknown',
      custodian,
      custodianDeclared: parcel.custodianId,
      position: motion.position,
      velocity: motion.velocity,
      attachment,
      attachmentInconsistent,
      transfer: parcel.transfer ?? null,
      dimensionsM: parcel.dimensionsM ?? [0.45, 0.32, 0.26],
      massKg: parcel.massKg ?? null,
      target: parcel.target ?? null,
      ref: parcel.ref,
      refKey,
      available: true,
      source: {
        pointer: `SceneState.samples[entity_id=${parcel.ref.id}]`,
        frameId: `${scene.runId.slice(0, 8)}#${scene.tick}`,
        authority: motion.providerId,
        provenance: 'reported',
        evidencePointer: parcel.sourcePointer,
        evidenceAuthority: parcel.authority,
      },
      sourceKind: 'scene-state+host-evidence',
      demoEvidence: true,
      custodyKnown: custodian !== null,
    }));
  }

  // Rule evidence: exact context join (run/epoch/typed generation) + availability.
  const ruleRecord = ruleEvidence?.rule ?? null;
  const ruleContext = ruleEvidence?.context ?? null;
  const ruleUsable = ruleRecord !== null && ruleContext !== null &&
    ruleContext.runId === scene.runId &&
    ruleContext.epoch === epochShown &&
    ruleContext.generationIsInteger === (typeof scene.generation === 'number') &&
    String(ruleContext.generation) === String(generationShown) &&
    isAvailable(ruleRecord, scene);
  const networkRecord = frameEvidence?.network ?? null;
  const networkUsable = networkRecord !== null && frameEvidence.context.runId === scene.runId &&
    isAvailable(networkRecord, scene);

  // Events of this frame's evidence commit, projected once for both the
  // explicit-response lookup and the frame's event list.
  const frameEvents = [...(frameEvidence?.events ?? new Map()).values()];
  const frame = {
    schemaVersion: VIEW_FRAME_SCHEMA,
    // Actual declared identity only; UNKNOWN when undeclared.
    runId: scene.runId,
    epoch: epochShown,
    manifestRevision: scene.revision ?? options.manifestRevision ?? UNKNOWN_IDENTITY,
    frameKey: `${scene.runId}:${epochShown}:${scene.revision ?? UNKNOWN_IDENTITY}:${scene.tick}`,
    tick: scene.tick,
    simTimeNs: String(scene.simTimeNs),
    timeSeconds: scene.simTimeNs / 1e9,
    provenance: 'host-projection',
    authority: HOST_DEMO_AUTHORITY,
    // Typed feed identity for the motion feed, declared by the caller (the
    // demo page marks its authored feed FEED_MOTION_DEMO; a sealed replay
    // loader marks FEED_MOTION_BENCH). Never inferred from ids or digests.
    feedMotion: options.feedMotion === FEED_MOTION_DEMO || options.feedMotion === FEED_MOTION_BENCH
      ? options.feedMotion
      : UNKNOWN_IDENTITY,
    // Explicit availability of parcel/custody evidence, separate from the
    // motion feed identity: a real-BENCH feed may still have no parcel
    // evidence at this boundary (it stays unknown, never relabelled).
    parcelEvidenceKnown: (frameEvidence?.parcels ?? new Map()).size > 0,
    readiness: {
      motion: 'reported',
      network: networkUsable ? 'demo' : 'missing',
      business: 'missing',
    },
    // (The former frame.motionSource string was superseded by the typed
    // feedMotion above, which never claims bench for a demo feed.)
    identityComplete: scene.contextDeclared,
    // The event that explicitly declares itself a response to THIS rule's
    // flip: response_rule_id must equal the presented rule id and the
    // declared flip instant must match the rule's lastFlip. Causality is the
    // source's explicit declaration only — kind, ordering, timing and
    // proximity infer nothing. null renders as an unbound response (UNKNOWN).
    ruleResponse: (() => {
      if (!ruleUsable || ruleRecord.id === null || ruleRecord.lastFlip === null) return null;
      return frameEvents.find(event =>
        event.responseRuleId === ruleRecord.id &&
        event.responseFlipTime !== null &&
        event.responseFlipTime === ruleRecord.lastFlip &&
        event.time >= ruleRecord.lastFlip) ?? null;
    })(),
    entities,
    parcels,
    unresolvedParcelIds,
    routes: frameEvidence?.routes ?? null,
    activeRoute: options.activeRoute ?? null,
    events: frameEvents,
    script: options.script ?? [],
    network: networkUsable
      ? {
          id: networkRecord.id,
          degraded: networkRecord.degraded,
          rssiDbm: networkRecord.rssiDbm,
          source: {
            pointer: networkRecord.sourcePointer ?? `evidence.network[link_id=${networkRecord.id}]`,
            frameId: null,
            authority: networkRecord.authority,
            provenance: networkRecord.provenance,
          },
        }
      : null,
    rule: ruleUsable
      ? {
          id: ruleRecord.id,
          value: ruleRecord.truth,
          inputs: ruleRecord.inputs,
          inputsKnown: ruleRecord.inputs !== null,
          inputSource: ruleRecord.inputSource,
          provenance: ruleRecord.provenance,
          engine: ruleRecord.engine,
          atlasValue: 'unknown',
          lastFlip: ruleRecord.lastFlip,
        }
      : unknownRule,
    generation: generationShown,
    hostContext: {
      runId: scene.runId,
      scenarioDigest: scene.scenarioDigest,
      sceneStateDigest: scene.sceneStateDigest,
      tick: scene.tick,
      simTimeNs: String(scene.simTimeNs),
      epoch: epochShown,
      generation: generationShown,
      generationIsInteger: typeof scene.generation === 'number',
      revision: scene.revision ?? UNKNOWN_IDENTITY,
      declaredEntityIds: scene.declaredEntityIds,
      parcelEvidenceCount: (frameEvidence?.parcels ?? new Map()).size,
      unresolvedParcelIds: [...unresolvedParcelIds],
      networkKnown: networkUsable,
      ruleKnown: ruleUsable && ruleRecord.truth !== null,
    },
  };
  return frame;
}

/**
 * Convenience for simple callers and tests: ingest once, project once.
 * The mount hot path uses ingest* + projectHostViewFrame directly instead,
 * so the mount re-projects without revalidating.
 */
export function buildHostViewFrame(scene, frameEvidence, ruleEvidence, options = {}) {
  const sceneIngest = ingestSceneState(scene, options.sceneContext ?? null);
  const frameIngest = frameEvidence === null || frameEvidence === undefined
    ? null
    : ingestFrameEvidence(frameEvidence);
  const ruleIngest = ruleEvidence === null || ruleEvidence === undefined
    ? null
    : ingestRuleEvidence(ruleEvidence);
  return projectHostViewFrame(sceneIngest, frameIngest, ruleIngest, options);
}

/** Exact run/epoch/revision/frame/generation context for one target. */
export function selectionFor(frame, entityId) {
  const entity = [...frame.parcels, ...frame.entities].find(e => e.id === entityId);
  if (!entity) return null;
  return {
    runId: frame.runId,
    epoch: frame.epoch,
    manifestRevision: frame.manifestRevision,
    frameKey: frame.frameKey,
    generation: entity.generation ?? null,
    entityId,
    entity,
  };
}

// --- Attribution: projection and glyph helpers reused from the prototype ---

export const escapeHtml = value =>
  String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export { escapeHtml as h };

/** Prototype ENU→canvas projection, attributed reuse (src/scene.js project). */
export function project([e, n, u = 0]) {
  return [240 + 5.8 * e - 3 * n, 570 - 1.85 * e - 2.7 * n - 5 * u];
}

/** Prototype isometric box, attributed reuse (src/scene.js box). */
export function isoBox(e, n, u, w, d, hgt, colors, extra = '') {
  const a = [e, n, u], b = [e + w, n, u], c = [e + w, n + d, u], f = [e, n + d, u];
  const top = [a, b, c, f].map(v => [v[0], v[1], v[2] + hgt]);
  const poly = (points, fill, stroke = 'none') =>
    `<polygon points="${points.map(p => project(p).map(n2 => n2.toFixed(2)).join(',')).join(' ')}" fill="${fill}" stroke="${stroke}"/>`;
  return `<g ${extra}>${poly([a, b, top[1], top[0]], colors[1])}${poly([a, f, top[3], top[0]], colors[2])}${poly(top, colors[0])}</g>`;
}

export function pointOnPath(points, progress) {
  const distances = points.slice(1).map((p, i) => Math.hypot(...p.map((n, j) => n - points[i][j])));
  const total = distances.reduce((a, b) => a + b, 0);
  if (!total) return [...points[0]];
  let distance = Math.max(0, Math.min(1, progress)) * total;
  for (let i = 0; i < distances.length; i++) {
    if (distance <= distances[i] || i === distances.length - 1) {
      const f = distances[i] ? distance / distances[i] : 0;
      return points[i].map((v, j) => v + (points[i + 1][j] - v) * f);
    }
    distance -= distances[i];
  }
}
