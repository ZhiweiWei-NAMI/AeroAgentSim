/**
 * Live run session store.
 *
 * The single state container for the operations console. It ingests
 * only validated Control API responses and stream envelopes, keeps
 * exact recorded SceneState ticks without interpolation, applies
 * bounded buffers with deterministic ordering, and exposes an
 * immutable state snapshot per change. Run credentials stay in
 * private memory: they are never published to subscribers, never
 * rendered, and never persisted.
 */
import type { ControlReplaySource } from "./control-replay-source";
import { ControlAssetQueue } from "./control-asset-queue";
import type { RunStartIdentityStore } from "./run-start-identity";
import type { Unsubscribe } from "./state/observable";
import { Observable } from "./state/observable";
import {
  ControlClient,
  ControlProtocolError,
  RunEventStream,
  type BootstrapCredentials,
  type ControlOperation,
  type RunCredentials,
  type StreamEnvelope,
} from "./run-control";
import type {
  ControlCatalog,
  PublicRunEvent,
  PublicTrafficLightFrame,
  RunAccessCredentials,
  PublicScenario,
  RunExecutionSnapshot,
  RunExecutionTransition,
  RuntimeControlStatus,
  SceneState,
  StartRunResponse,
} from "./generated/aero-bench-contracts";

/** Bounded buffers; the console renders recent authoritative history. */
export const MAX_SCENE_STATES = 240;
export const MAX_EVENTS = 1000;
export const MAX_TRANSITIONS = 200;

export type ConnectionState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "closed-terminal"
  | "closed"
  | "error";

export interface RunSessionState {
  readonly connection: ConnectionState;
  readonly catalog: ControlCatalog | null;
  readonly catalogError: string | null;
  readonly selectedRunId: string | null;
  readonly hasRunCredentials: boolean;
  readonly snapshot: RunExecutionSnapshot | null;
  /** Projected public scenario issued with the start receipt; never invented. */
  readonly scenario: PublicScenario | null;
  readonly runtimeControl: RuntimeControlStatus | null;
  readonly transitions: readonly RunExecutionTransition[];
  /** Recorded SceneStates in ascending tick order (bounded, exact). */
  readonly sceneStates: readonly SceneState[];
  readonly latestTick: number | null;
  /** Public RunEvents in ascending sequence order (bounded, exact). */
  readonly events: readonly PublicRunEvent[];
  /** Latest authoritative SUMO TraCI traffic-light frame. */
  readonly trafficLightFrame: PublicTrafficLightFrame | null;
  readonly sessionError: string | null;
  readonly pendingControl: ControlOperation | null;
}

export interface RunSessionOptions {
  /** Generate the client-side idempotency identifiers (test seam). */
  readonly newIdentifier?: (prefix: string) => string;
  readonly startIdentities?: RunStartIdentityStore;
}

const TERMINAL_PHASES: ReadonlySet<string> = new Set(["completed", "cancelled", "error"]);

function defaultIdentifier(prefix: string): string {
  const random = new Uint8Array(8);
  globalThis.crypto.getRandomValues(random);
  let suffix = "";
  for (const byte of random) {
    suffix += byte.toString(16).padStart(2, "0");
  }
  return `${prefix}.${suffix}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function initialState(): RunSessionState {
  return {
    connection: "idle",
    catalog: null,
    catalogError: null,
    selectedRunId: null,
    hasRunCredentials: false,
    snapshot: null,
    scenario: null,
    runtimeControl: null,
    transitions: [],
    sceneStates: [],
    latestTick: null,
    events: [],
    trafficLightFrame: null,
    sessionError: null,
    pendingControl: null,
  };
}

function trafficLightFrame(event: PublicRunEvent): PublicTrafficLightFrame | null {
  if (event.event_type !== "public.traffic-light") return null;
  if (event.payload_schema_id !== "sumo.traffic_light.v1") {
    throw new ControlProtocolError("SUMO traffic-light event has an unexpected schema");
  }
  const payload = Object.fromEntries(event.public_payload.map((item) => [item.name, item.value]));
  if (
    Object.keys(payload).sort().join(",") !==
    "simulation_time_ns,snapshot_digest,traffic_lights_json"
    || typeof payload.snapshot_digest !== "string"
    || typeof payload.simulation_time_ns !== "number"
    || !Number.isInteger(payload.simulation_time_ns)
    || payload.simulation_time_ns !== event.at.sim_time_ns
    || typeof payload.traffic_lights_json !== "string"
  ) {
    throw new ControlProtocolError("SUMO traffic-light event payload is invalid");
  }
  let raw: unknown;
  try {
    raw = JSON.parse(payload.traffic_lights_json);
  } catch {
    throw new ControlProtocolError("SUMO traffic-light state is not valid JSON");
  }
  if (!Array.isArray(raw)) {
    throw new ControlProtocolError("SUMO traffic-light state is not an array");
  }
  const states = raw.map((item, index) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      throw new ControlProtocolError(`SUMO traffic-light state ${index} is invalid`);
    }
    const value = item as Record<string, unknown>;
    if (
      Object.keys(value).sort().join(",") !==
      "next_switch_s,phase_index,program_id,signal_id,state,telemetry_source"
      || typeof value.signal_id !== "string"
      || typeof value.state !== "string"
      || typeof value.phase_index !== "number"
      || !Number.isInteger(value.phase_index)
      || value.phase_index < 0
      || typeof value.next_switch_s !== "number"
      || !Number.isFinite(value.next_switch_s)
      || typeof value.program_id !== "string"
      || value.telemetry_source !== "sumo-traci"
    ) {
      throw new ControlProtocolError(`SUMO traffic-light state ${index} is invalid`);
    }
    return value as unknown as PublicTrafficLightFrame["states"][number];
  });
  const ids = states.map((state) => state.signal_id);
  if (ids.some((id, index) => index > 0 && id <= ids[index - 1]!)) {
    throw new ControlProtocolError("SUMO traffic-light states are not sorted and unique");
  }
  return {
    event_id: event.event_id,
    sequence: event.sequence,
    at: event.at,
    provider_id: event.source,
    snapshot_digest: payload.snapshot_digest,
    states,
  };
}

export class RunSession {
  private readonly state: Observable<RunSessionState>;
  private readonly client: ControlClient;
  private readonly newIdentifier: (prefix: string) => string;
  private readonly startIdentities: RunStartIdentityStore | undefined;
  private stream: RunEventStream | null = null;
  private runCredentials: RunCredentials | null = null;
  private assetQueue: ControlAssetQueue | null = null;
  private statusController: AbortController | null = null;
  private disposed = false;

  constructor(client: ControlClient, options: RunSessionOptions = {}) {
    this.client = client;
    this.newIdentifier = options.newIdentifier ?? defaultIdentifier;
    this.startIdentities = options.startIdentities;
    this.state = new Observable<RunSessionState>(initialState());
  }

  subscribe(listener: (state: RunSessionState) => void): Unsubscribe {
    return this.state.subscribe(listener);
  }

  async publicAsset(digest: string, signal?: AbortSignal): Promise<Response> {
    if (this.disposed || this.runCredentials === null) throw new ControlProtocolError("no live run credentials are held");
    if (this.assetQueue === null) throw new ControlProtocolError("no run asset queue is held");
    return this.assetQueue.fetch(digest, signal);
  }

  get assetDiagnostics(): Readonly<Record<string, number>> | null { return this.assetQueue?.diagnostics ?? null; }

  /** Sealed replay of the finished run. The source keeps its own copy of the run credentials in
   * memory, so it stays usable after this session is disposed. */
  sealedReplaySource(): ControlReplaySource {
    const credentials = this.runCredentials;
    if (this.disposed || credentials === null) throw new ControlProtocolError("no live run credentials are held");
    if (!this.isTerminal) throw new ControlProtocolError("the run has not reached a terminal phase");
    const client = this.client;
    const assets = this.assetQueue;
    if (assets === null) throw new ControlProtocolError("no run asset queue is held");
    return Object.freeze({
      runId: credentials.runId,
      manifest: (signal?: AbortSignal) => client.publicReplayManifest(credentials, signal),
      trace: (signal?: AbortSignal) => client.publicTrace(credentials, signal),
      asset: (digest: string, signal?: AbortSignal) => assets.fetch(digest, signal),
      assetQueueManaged: true,
    });
  }

  get currentState(): RunSessionState {
    return this.state.value;
  }

  private patch(partial: Partial<RunSessionState>): void {
    this.state.set({ ...this.state.value, ...partial });
  }

  /** Load the run catalog with the bootstrap bearer token. The token is used once. */
  async loadCatalog(operatorToken: string, signal?: AbortSignal): Promise<void> {
    if (this.disposed) {
      return;
    }
    this.patch({ connection: "connecting", catalogError: null });
    try {
      const catalog = await this.client.catalog(operatorToken, signal);
      this.patch({ connection: this.state.value.hasRunCredentials ? "connected" : "idle", catalog });
    } catch (error) {
      if (signal?.aborted) {
        return;
      }
      this.patch({
        connection: this.state.value.hasRunCredentials ? "connected" : "error",
        catalogError: errorMessage(error),
      });
    }
  }

  selectRun(runId: string | null): void {
    if (runId !== null && !this.state.value.catalog?.runs.some((run) => run.run_id === runId)) {
      throw new ControlProtocolError("selected run is absent from the loaded catalog");
    }
    this.patch({ selectedRunId: runId });
  }

  /**
   * Start the selected catalog run through POST /v1/runs and open the
   * authenticated event stream. Bootstrap credentials are consumed
   * within this call and never stored.
   */
  async start(bootstrap: BootstrapCredentials): Promise<void> {
    if (this.disposed) {
      return;
    }
    const runId = this.state.value.selectedRunId;
    if (runId === null) {
      throw new ControlProtocolError("no catalog run is selected");
    }
    this.closeStream();
    this.runCredentials = null;
    this.assetQueue = null;
    this.patch({
      connection: "connecting",
      hasRunCredentials: false,
      snapshot: null,
      scenario: null,
      runtimeControl: null,
      transitions: [],
      sceneStates: [],
      latestTick: null,
      events: [],
      trafficLightFrame: null,
      sessionError: null,
      pendingControl: null,
    });
    let response: StartRunResponse;
    try {
      const startId = this.startIdentities === undefined ? this.newIdentifier("start")
        : this.startIdentities.getOrCreate(runId, () => this.newIdentifier("start")).startId;
      response = await this.client.startRun(bootstrap, runId, startId);
    } catch (error) {
      this.patch({ connection: "error", sessionError: errorMessage(error) });
      return;
    }
    this.runCredentials = this.credentialsOf(response.credentials);
    const credentials = this.runCredentials;
    this.assetQueue = new ControlAssetQueue((digest, signal) => this.client.publicAsset(credentials, digest, signal));
    this.patch({
      connection: "connected",
      hasRunCredentials: true,
      snapshot: response.snapshot,
      scenario: response.scenario,
      sessionError: null,
    });
    this.openStream();
  }

  private credentialsOf(credentials: RunAccessCredentials): RunCredentials {
    return {
      runId: credentials.run_id,
      operatorToken: credentials.operator_token,
      csrfToken: credentials.csrf_token,
    };
  }

  private openStream(): void {
    if (this.runCredentials === null || this.disposed) {
      return;
    }
    const stream = new RunEventStream(this.client, this.runCredentials, {
      onEnvelope: (envelope) => this.ingestEnvelope(envelope),
      onConnected: () => this.patch({ connection: "connected" }),
      onError: (error) => this.patch({ sessionError: errorMessage(error) }),
      onClosed: (reason) => {
        this.patch({
          connection:
            reason === "terminal" ? "closed-terminal" : reason === "aborted" ? "closed" : "error",
        });
      },
    });
    this.stream = stream;
    stream.open();
  }

  private ingestEnvelope(envelope: StreamEnvelope): void {
    if ("transition" in envelope) {
      const transition = envelope.transition;
      const transitions = [...this.state.value.transitions, transition].slice(-MAX_TRANSITIONS);
      this.patch({
        transitions,
        snapshot:
          this.state.value.snapshot === null
            ? null
            : { ...this.state.value.snapshot, phase: transition.phase, transition_sequence: transition.sequence },
        runtimeControl: transition.runtime_control?.status ?? this.state.value.runtimeControl,
      });
      return;
    }
    if ("scene_state" in envelope) {
      this.ingestSceneState(envelope.scene_state);
      return;
    }
    this.ingestEvent(envelope.event);
  }

  /**
   * Ingest one exact SceneState tick. Ticks must strictly advance; a
   * regressing or repeated tick is a stream contract violation and is
   * recorded as a session error — never merged into history.
   */
  private ingestSceneState(state: SceneState): void {
    const current = this.state.value;
    if (current.latestTick !== null && state.at.tick <= current.latestTick) {
      this.patch({
        sessionError: `scene state tick ${state.at.tick} did not advance past ${current.latestTick}`,
      });
      return;
    }
    const next = [...current.sceneStates, state];
    const trimmed = next.length > MAX_SCENE_STATES ? next.slice(next.length - MAX_SCENE_STATES) : next;
    this.patch({ sceneStates: trimmed, latestTick: state.at.tick });
  }

  private ingestEvent(event: PublicRunEvent): void {
    const events = this.state.value.events;
    const last = events[events.length - 1];
    if (last !== undefined && event.sequence <= last.sequence) {
      this.patch({ sessionError: `public event sequence ${event.sequence} did not advance` });
      return;
    }
    const next = [...events, event];
    const frame = trafficLightFrame(event);
    this.patch({
      events: next.length > MAX_EVENTS ? next.slice(next.length - MAX_EVENTS) : next,
      ...(frame === null ? {} : { trafficLightFrame: frame }),
    });
  }

  /** Issue one runtime control mutation through the formal API. */
  async control(operation: ControlOperation): Promise<void> {
    if (this.disposed || this.runCredentials === null) {
      throw new ControlProtocolError("no live run credentials are held");
    }
    if (this.state.value.pendingControl !== null) {
      return;
    }
    this.patch({ pendingControl: operation });
    try {
      const response = await this.client.control(
        this.runCredentials,
        operation,
        this.newIdentifier("control"),
      );
      this.patch({
        snapshot: response.snapshot,
        runtimeControl: response.receipt.status,
        pendingControl: null,
      });
    } catch (error) {
      this.patch({ pendingControl: null, sessionError: errorMessage(error) });
    }
  }

  /** Poll the run status snapshot once (run bearer token). */
  async refreshStatus(): Promise<void> {
    if (this.disposed || this.runCredentials === null) {
      return;
    }
    this.statusController?.abort();
    const controller = new AbortController();
    this.statusController = controller;
    try {
      const response = await this.client.status(this.runCredentials, controller.signal);
      this.patch({ snapshot: response.snapshot });
    } catch (error) {
      if (!controller.signal.aborted) {
        this.patch({ sessionError: errorMessage(error) });
      }
    }
  }

  /** The stream is terminal only when a validated transition said so. */
  get isTerminal(): boolean {
    const snapshot = this.state.value.snapshot;
    return snapshot !== null && TERMINAL_PHASES.has(snapshot.phase);
  }

  /** Close the stream and drop held run credentials (memory only). */
  disconnect(): void {
    this.closeStream();
    this.runCredentials = null;
    this.assetQueue = null;
    this.patch({
      connection: "closed",
      hasRunCredentials: false,
      pendingControl: null,
    });
  }

  private closeStream(): void {
    this.stream?.close();
    this.stream = null;
  }

  dispose(): void {
    this.disposed = true;
    this.statusController?.abort();
    this.statusController = null;
    this.closeStream();
    this.runCredentials = null;
    this.assetQueue = null;
    this.state.set(initialState());
  }
}
