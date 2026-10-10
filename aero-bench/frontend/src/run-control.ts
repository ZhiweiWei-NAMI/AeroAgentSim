/**
 * Formal Control API client and authenticated event stream.
 *
 * Every request uses the strict generated request contracts; every
 * response body is validated with the generated Ajv validators before
 * it is handed to callers. Credentials exist only in caller-owned
 * memory: they are never persisted, never logged and never placed in
 * URLs. The SSE stream is consumed over fetch (EventSource cannot set
 * Authorization), parsed by a bounded frame parser, and resumes from
 * canonical server cursors.
 */
import {
  assertControlCatalog,
  assertPublicRunEventStreamEvent,
  assertRunStatusResponse,
  assertRunTransitionEvent,
  assertRuntimeControlRequest,
  assertRuntimeControlResponse,
  assertSceneStateStreamEvent,
  assertStartRunRequest,
  assertStartRunResponse,
  isControlApiErrorResponse,
} from "./generated/contract-validators";
import type {
  ControlApiErrorResponse,
  ControlCatalog,
  PublicRunEventStreamEvent,
  RunStatusResponse,
  RunTransitionEvent,
  Operation,
  RuntimeControlRequest,
  RuntimeControlResponse,
  SceneStateStreamEvent,
  StartRunRequest,
  StartRunResponse,
} from "./generated/aero-bench-contracts";

/** State-changing control operations; the read-only "status" operation is excluded. */
export type ControlOperation = Exclude<Operation, "status">;

export const START_RUN_SCHEMA_VERSION = "aero-bench.start-run-request/v1" as const;
export const RUNTIME_CONTROL_SCHEMA_VERSION = "aero-bench.runtime-control-request/v1" as const;

export type StreamEnvelope = RunTransitionEvent | SceneStateStreamEvent | PublicRunEventStreamEvent;

export class ControlApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;

  constructor(status: number, code: string, detail: string) {
    super(`control api error ${status} ${code}: ${detail}`);
    this.name = "ControlApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export class ControlProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ControlProtocolError";
  }
}

/** Operator-supplied bootstrap credentials for catalog/start; memory only. */
export interface BootstrapCredentials {
  readonly operatorToken: string;
  readonly csrfToken: string;
}

/** Per-run access credentials returned by a validated StartRunResponse; memory only. */
export interface RunCredentials {
  readonly runId: string;
  readonly operatorToken: string;
  readonly csrfToken: string;
}

const RUN_ID = /^[0-9a-f]{64}$/;
const TOKEN = /^[0-9a-f]{64}$/;
const IDENTIFIER = /^[a-z][a-z0-9_.-]*$/;

function assertRunId(runId: string): void {
  if (!RUN_ID.test(runId)) {
    throw new ControlProtocolError("run_id must be a 64-character lowercase digest");
  }
}

function assertToken(token: string, label: string): void {
  if (!TOKEN.test(token)) {
    throw new ControlProtocolError(`${label} must be a 64-character lowercase digest`);
  }
}

/**
 * Validate the operator-provided control service base. Only an absolute
 * http(s) origin is accepted: no path, query, fragment, credentials or
 * protocol-relative form.
 */
export function controlServiceOrigin(baseUrl: string): string {
  let url: URL;
  try {
    url = new URL(baseUrl);
  } catch {
    throw new ControlProtocolError("the control service endpoint is not a valid URL");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new ControlProtocolError("the control service must use http or https");
  }
  if (
    url.username !== "" ||
    url.password !== "" ||
    (url.pathname !== "/" && url.pathname !== "") ||
    url.search !== "" ||
    url.hash !== ""
  ) {
    throw new ControlProtocolError("the control service endpoint must be a bare origin");
  }
  return url.origin;
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ControlProtocolError(`control response ${response.status} carried invalid JSON`);
  }
}

/**
 * Parse one non-2xx control response. A schema-valid error envelope is
 * surfaced with its authoritative code/detail; anything else is a
 * protocol error — never a guessed failure reason.
 */
export async function controlErrorFromResponse(response: Response): Promise<ControlApiError> {
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    return new ControlApiError(response.status, "response.unparseable", "control error body was not JSON");
  }
  if (isControlApiErrorResponse(value)) {
    return new ControlApiError(response.status, value.error.code, value.error.detail);
  }
  return new ControlApiError(response.status, "response.schema", "control error body violated its contract");
}

// ---------------------------------------------------------------------------
// Bounded SSE frame parsing
// ---------------------------------------------------------------------------

export interface SseFrame {
  readonly event: string;
  readonly id: string | null;
  readonly data: string;
}

/**
 * Incremental parser for the exact frame grammar the control server
 * emits: `event:`/`id:`/`data:` fields separated by blank lines, with
 * `:` comment lines used as heartbeats. Field content is taken
 * literally after one optional leading space; multi-line data is
 * joined with `\n`. The pending buffer is bounded — a stream that
 * never terminates a frame is a protocol error, never an unbounded
 * buffer.
 */
export class SseFrameParser {
  private pending = "";
  private readonly maxBufferBytes: number;

  constructor(maxBufferBytes = 8 * 1024 * 1024) {
    this.maxBufferBytes = maxBufferBytes;
  }

  /** Feed one decoded text chunk and return every completed frame. */
  push(chunk: string): SseFrame[] {
    this.pending += chunk;
    if (this.pending.length > this.maxBufferBytes) {
      this.pending = "";
      throw new ControlProtocolError(
        `sse frame exceeded the bounded buffer (${this.maxBufferBytes} bytes) before terminating`,
      );
    }
    const frames: SseFrame[] = [];
    let boundary = this.pending.search(/\r?\n\r?\n/);
    while (boundary !== -1) {
      const rawFrame = this.pending.slice(0, boundary);
      const separatorLength = this.pending.slice(boundary).match(/^\r?\n\r?\n/)![0].length;
      this.pending = this.pending.slice(boundary + separatorLength);
      const frame = parseSseFrame(rawFrame);
      if (frame !== null) {
        frames.push(frame);
      }
      boundary = this.pending.search(/\r?\n\r?\n/);
    }
    return frames;
  }

  /** Drop any pending partial frame (called on abort/close). */
  reset(): void {
    this.pending = "";
  }
}

function parseSseFrame(rawFrame: string): SseFrame | null {
  let event = "message";
  let id: string | null = null;
  const dataLines: string[] = [];
  let hasData = false;
  for (const rawLine of rawFrame.split(/\r?\n/)) {
    if (rawLine.startsWith(":")) {
      continue;
    }
    const colon = rawLine.indexOf(":");
    const field = colon === -1 ? rawLine : rawLine.slice(0, colon);
    let value = colon === -1 ? "" : rawLine.slice(colon + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    switch (field) {
      case "event":
        event = value;
        break;
      case "id":
        id = value;
        break;
      case "data":
        dataLines.push(value);
        hasData = true;
        break;
      default:
        break;
    }
  }
  if (!hasData) {
    return null;
  }
  return { event, id, data: dataLines.join("\n") };
}

// ---------------------------------------------------------------------------
// HTTP control client
// ---------------------------------------------------------------------------

export interface ControlClientOptions {
  /** Absolute http(s) origin of the formal control service. */
  readonly baseUrl: string;
  readonly fetch?: typeof fetch;
}

/** Validated, credential-carrying client for the formal Control API. */
export class ControlClient {
  private readonly origin: string;
  /** The bounded fetch implementation shared with the event stream. */
  readonly fetchImpl: typeof fetch;

  constructor(options: ControlClientOptions) {
    this.origin = controlServiceOrigin(options.baseUrl);
    this.fetchImpl = options.fetch ?? fetch.bind(globalThis);
  }

  get serviceOrigin(): string {
    return this.origin;
  }

  async publicAsset(credentials: RunCredentials, digest: string, signal?: AbortSignal): Promise<Response> {
    assertRunId(credentials.runId);
    assertRunId(digest);
    assertToken(credentials.operatorToken, "run operator token");
    return this.fetchImpl(`${this.origin}/v1/runs/${credentials.runId}/assets/${digest}`, {
      headers: { ...authHeaders(credentials.operatorToken), Accept: "application/json" }, signal, redirect: "error",
    });
  }

  /** GET /v1/runs/{run_id}/public/trace: the exact sealed public-trace.json bytes of a terminal run. */
  async publicTrace(credentials: RunCredentials, signal?: AbortSignal): Promise<Response> {
    return this.sealedPublicDocument(credentials, "trace", signal);
  }

  /** GET /v1/runs/{run_id}/public/replay-manifest: the exact sealed replay-manifest.json bytes. */
  async publicReplayManifest(credentials: RunCredentials, signal?: AbortSignal): Promise<Response> {
    return this.sealedPublicDocument(credentials, "replay-manifest", signal);
  }

  /** The body is returned unread: callers hash and validate the exact bytes. A run that is not
   * terminal yet answers 409 run.public_trace_unavailable, surfaced as a ControlApiError. */
  private async sealedPublicDocument(credentials: RunCredentials, document: "trace" | "replay-manifest",
      signal?: AbortSignal): Promise<Response> {
    assertRunId(credentials.runId);
    assertToken(credentials.operatorToken, "run operator token");
    const response = await this.fetchImpl(`${this.origin}/v1/runs/${credentials.runId}/public/${document}`, {
      method: "GET", headers: { ...authHeaders(credentials.operatorToken), Accept: "application/json" },
      signal, redirect: "error",
    });
    if (!response.ok) throw await controlErrorFromResponse(response);
    return response;
  }

  /** GET /v1/catalog with the bootstrap bearer token. */
  async catalog(operatorToken: string, signal?: AbortSignal): Promise<ControlCatalog> {
    assertToken(operatorToken, "bootstrap operator token");
    const response = await this.fetchImpl(`${this.origin}/v1/catalog`, {
      method: "GET",
      headers: { ...authHeaders(operatorToken), Accept: "application/json" },
      signal,
    });
    if (!response.ok) {
      throw await controlErrorFromResponse(response);
    }
    const value = await readJson(response);
    try {
      assertControlCatalog(value);
    } catch (error) {
      throw new ControlProtocolError(`catalog violated its contract: ${errorMessage(error)}`);
    }
    return value;
  }

  /**
   * POST /v1/runs. The request body is validated with the generated
   * contract before it is serialized; the response carries the per-run
   * credentials, which the caller must keep in memory only.
   */
  async startRun(
    credentials: BootstrapCredentials,
    runId: string,
    startId: string,
    signal?: AbortSignal,
  ): Promise<StartRunResponse> {
    assertToken(credentials.operatorToken, "bootstrap operator token");
    assertToken(credentials.csrfToken, "bootstrap CSRF token");
    assertRunId(runId);
    if (!IDENTIFIER.test(startId)) {
      throw new ControlProtocolError("start_id must be a lowercase dotted identifier");
    }
    const request: StartRunRequest = {
      schema_version: START_RUN_SCHEMA_VERSION,
      start_id: startId,
      run_id: runId,
    };
    assertStartRunRequest(request);
    const response = await this.fetchImpl(`${this.origin}/v1/runs`, {
      method: "POST",
      headers: {
        ...authHeaders(credentials.operatorToken),
        "X-Aero-Bench-CSRF": credentials.csrfToken,
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(request),
      signal,
    });
    if (!response.ok) {
      throw await controlErrorFromResponse(response);
    }
    const value = await readJson(response);
    try {
      assertStartRunResponse(value);
    } catch (error) {
      throw new ControlProtocolError(`start response violated its contract: ${errorMessage(error)}`);
    }
    if (value.credentials.run_id !== runId || value.run_id !== runId) {
      throw new ControlProtocolError("start response identities disagree with the requested run");
    }
    return value;
  }

  /** GET /v1/runs/{run_id} with the per-run bearer token. */
  async status(credentials: RunCredentials, signal?: AbortSignal): Promise<RunStatusResponse> {
    assertRunId(credentials.runId);
    assertToken(credentials.operatorToken, "run operator token");
    const response = await this.fetchImpl(
      `${this.origin}/v1/runs/${credentials.runId}`,
      {
        method: "GET",
        headers: { ...authHeaders(credentials.operatorToken), Accept: "application/json" },
        signal,
      },
    );
    if (!response.ok) {
      throw await controlErrorFromResponse(response);
    }
    const value = await readJson(response);
    try {
      assertRunStatusResponse(value);
    } catch (error) {
      throw new ControlProtocolError(`run status violated its contract: ${errorMessage(error)}`);
    }
    if (value.snapshot.run_id !== credentials.runId) {
      throw new ControlProtocolError("run status belongs to another run");
    }
    return value;
  }

  /**
   * POST /v1/runs/{run_id}/controls/{operation} — the only mutation
   * path. Sends the strict runtime-control request with both the run
   * bearer token and the run CSRF header, and validates the receipt.
   */
  async control(
    credentials: RunCredentials,
    operation: ControlOperation,
    controlId: string,
    signal?: AbortSignal,
  ): Promise<RuntimeControlResponse> {
    assertRunId(credentials.runId);
    assertToken(credentials.operatorToken, "run operator token");
    assertToken(credentials.csrfToken, "run CSRF token");
    if (!IDENTIFIER.test(controlId)) {
      throw new ControlProtocolError("control_id must be a lowercase dotted identifier");
    }
    const request: RuntimeControlRequest = {
      schema_version: RUNTIME_CONTROL_SCHEMA_VERSION,
      control_id: controlId,
    };
    assertRuntimeControlRequest(request);
    const response = await this.fetchImpl(
      `${this.origin}/v1/runs/${credentials.runId}/controls/${operation}`,
      {
        method: "POST",
        headers: {
          ...authHeaders(credentials.operatorToken),
          "X-Aero-Bench-CSRF": credentials.csrfToken,
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(request),
        signal,
      },
    );
    if (!response.ok) {
      throw await controlErrorFromResponse(response);
    }
    const value = await readJson(response);
    try {
      assertRuntimeControlResponse(value);
    } catch (error) {
      throw new ControlProtocolError(`control response violated its contract: ${errorMessage(error)}`);
    }
    if (value.receipt.status.run_id !== credentials.runId || value.snapshot.run_id !== credentials.runId) {
      throw new ControlProtocolError("control response belongs to another run");
    }
    return value;
  }
}

// ---------------------------------------------------------------------------
// Authenticated SSE event stream with canonical cursor resume
// ---------------------------------------------------------------------------

/**
 * Canonical server cursors. The events endpoint requires all three as
 * exact integer texts; `after_transition`/`after_event_sequence` start
 * at -1 and `after_scene_tick` starts at 0, exactly as the server
 * defines them.
 */
export interface StreamCursors {
  readonly afterTransition: number;
  readonly afterSceneTick: number;
  readonly afterEventSequence: number;
}

export const INITIAL_CURSORS: StreamCursors = {
  afterTransition: -1,
  afterSceneTick: 0,
  afterEventSequence: -1,
};

/** Serialize cursors into the canonical query the server parses strictly. */
export function cursorsToQuery(cursors: StreamCursors): string {
  for (const [name, value] of [
    ["after_transition", cursors.afterTransition],
    ["after_scene_tick", cursors.afterSceneTick],
    ["after_event_sequence", cursors.afterEventSequence],
  ] as const) {
    if (!Number.isSafeInteger(value) || (name === "after_scene_tick" ? value < 0 : value < -1)) {
      throw new ControlProtocolError(`stream cursor ${name} is outside the server's accepted range`);
    }
  }
  const query = new URLSearchParams();
  query.set("after_transition", String(cursors.afterTransition));
  query.set("after_scene_tick", String(cursors.afterSceneTick));
  query.set("after_event_sequence", String(cursors.afterEventSequence));
  return query.toString();
}

/** Advance cursors from one validated envelope. Unknown shapes never advance. */
export function cursorsAfterEnvelope(cursors: StreamCursors, envelope: StreamEnvelope): StreamCursors {
  if ("transition" in envelope) {
    return { ...cursors, afterTransition: envelope.transition.sequence };
  }
  if ("scene_state" in envelope) {
    return { ...cursors, afterSceneTick: envelope.scene_state.at.tick };
  }
  return { ...cursors, afterEventSequence: envelope.event.sequence };
}

export interface StreamHandlers {
  readonly onEnvelope: (envelope: StreamEnvelope) => void;
  readonly onConnected: () => void;
  readonly onError: (error: unknown) => void;
  /** Called once the stream is closed and no further reconnect is attempted. */
  readonly onClosed: (reason: "aborted" | "terminal" | "unavailable") => void;
}

export interface EventStreamOptions {
  /** Reconnect delay in ms while the run is not terminal (default 2000). */
  readonly reconnectDelayMs?: number;
  readonly maxFrameBytes?: number;
}

const TERMINAL_PHASES: ReadonlySet<string> = new Set(["completed", "cancelled", "error"]);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * One authenticated SSE subscription to /v1/runs/{run_id}/events.
 * Frames are validated against the generated stream contracts, cursors
 * advance only from validated envelopes, and the stream reconnects from
 * its cursors until aborted or until the run reaches a terminal phase.
 * All listeners run inside the caller's subscriber set; abort() is the
 * single cleanup path.
 */
export class RunEventStream {
  private readonly client: ControlClient;
  private readonly credentials: RunCredentials;
  private readonly handlers: StreamHandlers;
  private readonly options: EventStreamOptions;
  private cursors: StreamCursors;
  private controller: AbortController | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  private connecting = false;

  constructor(
    client: ControlClient,
    credentials: RunCredentials,
    handlers: StreamHandlers,
    options: EventStreamOptions = {},
  ) {
    this.client = client;
    this.credentials = credentials;
    this.handlers = handlers;
    this.options = options;
    this.cursors = INITIAL_CURSORS;
  }

  get currentCursors(): StreamCursors {
    return this.cursors;
  }

  get isClosed(): boolean {
    return this.closed;
  }

  open(): void {
    if (this.closed || this.controller !== null || this.reconnectTimer !== null) {
      return;
    }
    void this.runLoop();
  }

  /** Abort any active request or pending reconnect; the stream is final afterwards. */
  close(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.controller !== null) {
      this.controller.abort();
      this.controller = null;
    }
    if (!this.closed) {
      this.closed = true;
      this.handlers.onClosed("aborted");
    }
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer !== null) {
      return;
    }
    const delay = this.options.reconnectDelayMs ?? 2_000;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.closed) {
        void this.runLoop();
      }
    }, delay);
  }

  private async runLoop(): Promise<void> {
    if (this.closed || this.connecting) {
      return;
    }
    this.connecting = true;
    const controller = new AbortController();
    this.controller = controller;
    const parser = new SseFrameParser(this.options.maxFrameBytes);
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    let terminal = false;
    let unavailable = false;
    try {
      const response = await this.clientDoFetch(controller.signal);
      if (!response.ok) {
        const error = await controlErrorFromResponse(response);
        // Client errors other than the retryable 408/429 will not heal
        // by reconnecting; capacity and server failures may.
        if (error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429) {
          unavailable = true;
        }
        throw error;
      }
      const contentType = response.headers.get("Content-Type") ?? "";
      if (!contentType.startsWith("text/event-stream")) {
        unavailable = true;
        throw new ControlProtocolError("the events endpoint did not answer with an event stream");
      }
      if (response.body === null) {
        unavailable = true;
        throw new ControlProtocolError("the events endpoint returned no body");
      }
      this.handlers.onConnected();
      reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8", { fatal: true });
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        const chunk = decoder.decode(value, { stream: true });
        for (const frame of parser.push(chunk)) {
          const envelope = decodeFrame(frame, this.credentials.runId);
          if (envelope === null) {
            continue;
          }
          this.cursors = cursorsAfterEnvelope(this.cursors, envelope);
          this.handlers.onEnvelope(envelope);
          if ("transition" in envelope && TERMINAL_PHASES.has(envelope.transition.phase)) {
            terminal = true;
          }
        }
      }
      parser.reset();
    } catch (error) {
      if (!controller.signal.aborted) {
        controller.abort();
        this.handlers.onError(error);
      }
      parser.reset();
    } finally {
      if (reader !== null) {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
      }
      this.connecting = false;
      if (this.controller === controller) {
        this.controller = null;
      }
    }
    if (this.closed) {
      return;
    }
    if (terminal) {
      this.closed = true;
      this.handlers.onClosed("terminal");
      return;
    }
    if (unavailable) {
      this.closed = true;
      this.handlers.onClosed("unavailable");
      return;
    }
    this.scheduleReconnect();
  }

  private async clientDoFetch(signal: AbortSignal): Promise<Response> {
    assertRunId(this.credentials.runId);
    assertToken(this.credentials.operatorToken, "run operator token");
    const query = cursorsToQuery(this.cursors);
    return this.clientFetch(`/v1/runs/${this.credentials.runId}/events?${query}`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${this.credentials.operatorToken}`,
        Accept: "text/event-stream",
      },
      signal,
    });
  }

  private clientFetch(path: string, init: RequestInit): Promise<Response> {
    return this.client.fetchImpl(`${this.client.serviceOrigin}${path}`, init);
  }
}

/**
 * Decode one SSE frame into a validated stream envelope. The frame
 * event name selects exactly one generated contract; anything else is a
 * protocol error, never a guessed envelope. Frames whose payload is
 * valid JSON but fails its contract are protocol errors.
 */
export function decodeFrame(frame: SseFrame, expectedRunId: string): StreamEnvelope | null {
  let value: unknown;
  try {
    value = JSON.parse(frame.data) as unknown;
  } catch {
    throw new ControlProtocolError("sse data line is not valid JSON");
  }
  switch (frame.event) {
    case "run.transition": {
      assertOrThrow<RunTransitionEvent>(value, assertRunTransitionEvent, "run.transition");
      if (value.run_id !== expectedRunId) {
        throw new ControlProtocolError("transition envelope belongs to another run");
      }
      return value;
    }
    case "scene.state": {
      assertOrThrow<SceneStateStreamEvent>(value, assertSceneStateStreamEvent, "scene.state");
      if (value.run_id !== expectedRunId) {
        throw new ControlProtocolError("scene envelope belongs to another run");
      }
      return value;
    }
    case "run.event": {
      assertOrThrow<PublicRunEventStreamEvent>(value, assertPublicRunEventStreamEvent, "run.event");
      if (value.run_id !== expectedRunId) {
        throw new ControlProtocolError("event envelope belongs to another run");
      }
      return value;
    }
    default:
      throw new ControlProtocolError(`sse frame carried the unknown event name '${frame.event}'`);
  }
}

function assertOrThrow<T>(
  value: unknown,
  assert: (input: unknown) => asserts input is T,
  contract: string,
): asserts value is T {
  try {
    assert(value);
  } catch (error) {
    throw new ControlProtocolError(`${contract} envelope violated its contract: ${errorMessage(error)}`);
  }
}

export type { ControlApiErrorResponse };
