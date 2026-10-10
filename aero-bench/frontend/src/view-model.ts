import type {
  PublicGoalResult,
  PublicMetricResult,
  PublicNetworkFrame,
  PublicScenario,
  PublicTrace,
  PublicTrajectory,
  PublicVerificationReport,
  RunExecutionSnapshot,
  SceneState,
  StateSample,
} from "./generated/aero-bench-contracts";

export const UNAVAILABLE = "—";

export type Tone = "ok" | "warn" | "bad" | "muted";

export type TracePhase = PublicTrace["phase"];
export type LivePhase = RunExecutionSnapshot["phase"];
export type RuntimePhase = NonNullable<RunExecutionSnapshot["runtime_control"]>["phase"];

export interface Integrity {
  readonly key: "integrity.verified" | "integrity.sealed" | "integrity.aborted" | "integrity.streaming";
  readonly tone: Tone;
}

/** Integrity presentation for the sealed trace phases only. */
export function integrityForTracePhase(phase: TracePhase): Integrity {
  switch (phase) {
    case "verified":
      return { key: "integrity.verified", tone: "ok" };
    case "sealed":
      return { key: "integrity.sealed", tone: "ok" };
    case "aborted":
      return { key: "integrity.aborted", tone: "bad" };
  }
}

export function displayValue(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return UNAVAILABLE;
  }
  return String(value);
}

export function displayRatio(value: number | null | undefined): string {
  return value === null || value === undefined ? UNAVAILABLE : `${(value * 100).toFixed(1)}%`;
}

export function displayMeters(value: number | undefined): string {
  return value === undefined ? UNAVAILABLE : `${value.toFixed(2)} m`;
}

export function displaySpeed(value: number | undefined): string {
  return value === undefined ? UNAVAILABLE : `${value.toFixed(2)} m/s`;
}

export function displayDegrees(value: number | undefined): string {
  return value === undefined ? UNAVAILABLE : `${value.toFixed(6)}°`;
}

export function formatSimTime(simTimeNs: number): string {
  const totalMilliseconds = Math.floor(simTimeNs / 1_000_000);
  const milliseconds = totalMilliseconds % 1000;
  const totalSeconds = Math.floor(totalMilliseconds / 1000);
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

export function shortDigest(digest: string): string {
  return digest.length <= 16 ? digest : `${digest.slice(0, 8)}…${digest.slice(-8)}`;
}

export function statusTone(state: string): Tone {
  if (
    state === "verified" ||
    state === "passed" ||
    state === "ready" ||
    state === "complete" ||
    state === "completed" ||
    state === "running" ||
    state === "pass" ||
    state === "delivered" ||
    state === "nominal"
  ) {
    return "ok";
  }
  if (
    state === "failed" ||
    state === "invalid" ||
    state === "fail" ||
    state === "aborted" ||
    state === "error" ||
    state === "cancelled" ||
    state === "blocked" ||
    state === "critical"
  ) {
    return "bad";
  }
  if (
    state === "warning" ||
    state === "sealing" ||
    state === "verifying" ||
    state === "projecting" ||
    state === "materializing" ||
    state === "preflight" ||
    state === "starting" ||
    state === "pausing" ||
    state === "paused" ||
    state === "stepping" ||
    state === "stopping" ||
    state === "resolved" ||
    state === "degraded"
  ) {
    return "warn";
  }
  return "muted";
}

/** i18n key for a live lifecycle phase. */
export function livePhaseKey(phase: LivePhase): `livePhase.${LivePhase}` {
  return `livePhase.${phase}`;
}

/** i18n key for the runtime control phase carried by receipts. */
export function runtimePhaseKey(phase: RuntimePhase): `runPhase.${RuntimePhase}` {
  return `runPhase.${phase}`;
}

export function tracePhaseKey(phase: TracePhase): `phase.${TracePhase}` {
  return `phase.${phase}`;
}

/** The declared frame i18n key for a StateSample stage. */
export function stageKey(stage: StateSample["stage"]): "stage.motion" | "stage.network" | "stage.business_environment" {
  return `stage.${stage}`;
}

// ---------------------------------------------------------------------------
// Exact-tick lookups over the canonical SceneState history
// ---------------------------------------------------------------------------

/** The exact recorded SceneState at one tick, or null. Never interpolated. */
export function sceneStateAtTick(trace: PublicTrace, tick: number): SceneState | null {
  return trace.scene_states.find((state) => state.at.tick === tick) ?? null;
}

/** Deterministic entity→sample map for one SceneState. */
export function samplesByEntity(state: SceneState): ReadonlyMap<string, StateSample> {
  return new Map(state.samples.map((sample) => [sample.entity_id, sample]));
}

/** The exact recorded sample of one entity at one tick, or null. */
export function sampleAtTick(state: SceneState | null, entityId: string): StateSample | null {
  if (state === null) {
    return null;
  }
  return samplesByEntity(state).get(entityId) ?? null;
}

/**
 * Project the declared public network topology onto one exact live SceneState.
 * Positions come only from that state; an incomplete binding is rejected by
 * returning null rather than creating a placeholder link or node.
 */
export function networkFrameAtSceneState(
  scenario: PublicScenario | null,
  state: SceneState | null,
): PublicNetworkFrame | null {
  const network = scenario?.network;
  if (network === null || network === undefined || state === null) {
    return null;
  }
  const byEntity = samplesByEntity(state);
  const nodes = network.node_bindings.map((binding) => {
    const sample = byEntity.get(binding.entity_id);
    if (sample === undefined) {
      return null;
    }
    return {
      node_id: binding.node_id,
      entity_id: binding.entity_id,
      endpoint_id: binding.endpoint_id,
      pose: sample.pose,
    };
  });
  if (nodes.some((node) => node === null)) {
    return null;
  }
  const nodeById = new Map(nodes.map((node) => [node!.node_id, node!]));
  const links = network.links.map((link) => {
    const source = nodeById.get(link.source_node_id);
    const destination = nodeById.get(link.destination_node_id);
    if (source === undefined || destination === undefined) {
      return null;
    }
    return {
      link_id: link.link_id,
      source_node_id: link.source_node_id,
      destination_node_id: link.destination_node_id,
      source_pose: source.pose,
      destination_pose: destination.pose,
    };
  });
  if (links.some((link) => link === null)) {
    return null;
  }
  return {
    at: state.at,
    scene_state_digest: state.scene_state_digest,
    nodes: nodes as PublicNetworkFrame["nodes"],
    links: links as PublicNetworkFrame["links"],
  };
}

/**
 * The recorded ticks of the trace: the terminal time, every canonical
 * SceneState tick and every projected record time. The cursor only
 * ever rests on this set.
 */
export function recordedTicks(trace: PublicTrace): readonly number[] {
  const ticks = new Set<number>([trace.time.tick]);
  for (const state of trace.scene_states) {
    ticks.add(state.at.tick);
  }
  for (const event of trace.events) {
    ticks.add(event.at.tick);
  }
  for (const item of trace.mission_events) {
    ticks.add(item.at.tick);
  }
  for (const item of trace.mission_status_history) {
    ticks.add(item.at.tick);
  }
  for (const item of trace.network_events) {
    ticks.add(item.at.tick);
  }
  for (const item of trace.network_frames) {
    ticks.add(item.at.tick);
  }
  for (const item of trace.sensor_frames) {
    ticks.add(item.at.tick);
  }
  return [...ticks].sort((left, right) => left - right);
}

/**
 * The authoritative simulation time recorded for one tick, or null when
 * the tick carries no recorded time. Derived only from declared
 * records; there is no fallback to a trace-level time.
 */
export function simTimeAtTick(trace: PublicTrace, tick: number): number | null {
  if (trace.time.tick === tick) {
    return trace.time.sim_time_ns;
  }
  for (const state of trace.scene_states) {
    if (state.at.tick === tick) {
      return state.at.sim_time_ns;
    }
  }
  for (const event of trace.events) {
    if (event.at.tick === tick) {
      return event.at.sim_time_ns;
    }
  }
  for (const item of [
    ...trace.mission_events,
    ...trace.mission_status_history,
    ...trace.network_events,
    ...trace.network_frames,
    ...trace.sensor_frames,
  ]) {
    if (item.at.tick === tick) {
      return item.at.sim_time_ns;
    }
  }
  return null;
}

/**
 * Canonical live trajectories: one sample per dynamic entity at each
 * recorded SceneState tick. Positions are copied from the samples; nothing
 * is interpolated or invented for a missing entity.
 */
export function trajectoriesFromSceneStates(states: readonly SceneState[]): readonly PublicTrajectory[] {
  const samples = new Map<string, PublicTrajectory["samples"][number][]>();
  for (const state of states) {
    for (const sample of state.samples) {
      if (sample.sample_kind !== "dynamic") {
        continue;
      }
      const series = samples.get(sample.entity_id) ?? [];
      series.push({ at: state.at, pose: sample.pose, sample_digest: sample.sample_digest });
      samples.set(sample.entity_id, series);
    }
  }
  return [...samples.entries()]
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .flatMap(([entity_id, series]) => {
      const first = series[0];
      if (first === undefined) {
        return [];
      }
      return [{ entity_id, samples: [first, ...series.slice(1)] }];
    });
}

export function publicMetrics(trace: PublicTrace): readonly PublicMetricResult[] {
  if (trace.verifier_public === null) {
    return [];
  }
  return trace.verifier_public.goals.flatMap((goal) => goal.metrics);
}

/** Deterministic SI byte formatting for declared artifact sizes. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return UNAVAILABLE;
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${unit === 0 ? String(value) : value.toFixed(1)} ${units[unit]}`;
}

export function formatMilliseconds(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) {
    return UNAVAILABLE;
  }
  if (ms < 100) {
    return `${ms.toFixed(2)} ms`;
  }
  return ms < 1000 ? `${ms.toFixed(1)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

// ---------------------------------------------------------------------------
// Exact StateSample instrument projection
// ---------------------------------------------------------------------------

export type InstrumentKey = "armed" | "battery" | "mode" | "health" | "contacts";

export interface Instrument {
  readonly key: InstrumentKey;
  readonly declared: boolean;
  readonly value: boolean | number | string | null;
}

/**
 * The bounded set of instruments read from the exact StateSample
 * fields only: declared contract fields with no local derivation.
 * An absent optional field is declared=false and is never guessed.
 */
export function sampleInstruments(sample: StateSample | null): readonly Instrument[] {
  if (sample === null) {
    return [
      { key: "armed", declared: false, value: null },
      { key: "battery", declared: false, value: null },
      { key: "mode", declared: false, value: null },
      { key: "health", declared: false, value: null },
      { key: "contacts", declared: false, value: null },
    ];
  }
  const instruments: Instrument[] = [
    { key: "armed", declared: sample.armed !== undefined, value: sample.armed ?? null },
    {
      key: "battery",
      declared: sample.battery !== undefined && sample.battery !== null && sample.battery.remaining_fraction !== undefined,
      value: sample.battery?.remaining_fraction ?? null,
    },
    { key: "mode", declared: sample.mode !== undefined, value: sample.mode ?? null },
    {
      key: "health",
      declared: sample.health !== undefined && sample.health !== null && sample.health.healthy !== undefined,
      value: sample.health?.healthy ?? null,
    },
    {
      key: "contacts",
      declared: sample.contacts !== undefined && sample.contacts.length > 0,
      value: sample.contacts !== undefined && sample.contacts.length > 0 ? sample.contacts.join(", ") : null,
    },
  ];
  return instruments;
}

export interface VerificationSummary {
  readonly status: PublicVerificationReport["status"] | null;
  readonly goals: readonly PublicGoalResult[];
  readonly coverageComplete: boolean | null;
}
