/**
 * Strict Public Trace v3 boundary. The generated Ajv contract graph is
 * the sole validator; this module adds no parsing of its own and no
 * compatibility shape. Every external JSON document is validated before
 * it can be stored or rendered, and nothing here invents data that the
 * document does not carry.
 */
import { assertPublicTrace } from "./generated/contract-validators";
import { assertNotAborted, readBoundedResponse } from "./verified-bytes";
import { parseStrictJson } from "./strict-json";
import { parseJsonObjectBytes } from "../shared/json-bytes.mjs";
import type { ProgressListener } from "./loading-progress";
import type {
  ArtifactReference,
  PublicMissionEvent,
  PublicMissionStatus,
  PublicNetworkEvent,
  PublicNetworkFrame,
  PublicProviderStatus,
  PublicRunEvent,
  PublicRuntimeArtifact,
  PublicScenario,
  PublicSensorFrameReference,
  PublicTerminal,
  PublicTrace,
  PublicTrajectory,
  PublicVerificationReport,
  RunExecutionSnapshot,
  RuntimeControlStatus,
  SceneState,
  StateSample,
} from "./generated/aero-bench-contracts";

export const PUBLIC_TRACE_SCHEMA_VERSION = "aero-bench.public-trace/v3" as const;
// Full 120 s recordings exceed V8's single-string limit. The byte parser
// retains the existing complete document contract and decodes one item at a time.
export const MAX_PUBLIC_TRACE_BYTES = 1024 * 1024 * 1024;
export const PUBLIC_VERIFICATION_SCHEMA_VERSION =
  "aero-bench.verification-public/v3" as const;
export const PUBLIC_PROJECTOR_VERSION = "aero-bench.public-projector/v2" as const;

export type {
  ArtifactReference,
  PublicMissionEvent,
  PublicMissionStatus,
  PublicNetworkEvent,
  PublicNetworkFrame,
  PublicProviderStatus,
  PublicRunEvent,
  PublicRuntimeArtifact,
  PublicScenario,
  PublicSensorFrameReference,
  PublicTerminal,
  PublicTrace,
  PublicTrajectory,
  PublicVerificationReport,
  RunExecutionSnapshot,
  RuntimeControlStatus,
  SceneState,
  StateSample,
};

export class PublicTraceValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PublicTraceValidationError";
  }
}

async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  if (globalThis.crypto?.subtle === undefined) {
    throw new PublicTraceValidationError("Web Crypto is unavailable; public trace cannot be verified");
  }
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

/** Validate one untrusted value against the current contract. */
export function parsePublicTrace(value: unknown): PublicTrace {
  if (value === null || typeof value !== "object") {
    throw new PublicTraceValidationError("trace must be a JSON object");
  }
  try {
    assertPublicTrace(value);
  } catch (error) {
    throw new PublicTraceValidationError(
      error instanceof Error ? error.message : "public-trace/v3 validation failed",
    );
  }
  return value as PublicTrace;
}

export function parsePublicTraceJson(json: string): PublicTrace {
  let value: unknown;
  try {
    value = parseStrictJson(json);
  } catch (error) {
    const detail = error instanceof Error ? error.message : "invalid JSON";
    throw new PublicTraceValidationError(`trace: invalid JSON (${detail})`);
  }
  return parsePublicTrace(value);
}

export function parsePublicTraceBytes(bytes: ArrayBuffer): PublicTrace {
  let value: unknown;
  try {
    value = parseJsonObjectBytes(new Uint8Array(bytes), { parseValue: parseStrictJson });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "invalid JSON";
    throw new PublicTraceValidationError(`trace: invalid JSON (${detail})`);
  }
  return parsePublicTrace(value);
}

/**
 * Resolve a viewer trace or asset endpoint. Only a same-origin relative
 * HTTP(S) endpoint is accepted: absolute URLs, other origins,
 * protocol-relative hosts and any non-HTTP scheme are rejected before
 * any request is made.
 */
export function sameOriginRelativeEndpoint(selector: string, baseHref: string): URL {
  if (selector.length === 0) {
    throw new PublicTraceValidationError("a relative endpoint is required");
  }
  if (selector.includes("\\")) {
    throw new PublicTraceValidationError("the endpoint must not contain backslashes");
  }
  if (selector.startsWith("//")) {
    throw new PublicTraceValidationError(
      "protocol-relative endpoints are rejected; use a same-origin relative endpoint",
    );
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(selector)) {
    throw new PublicTraceValidationError(
      "absolute URLs are rejected; use a same-origin relative endpoint",
    );
  }
  let url: URL;
  try {
    url = new URL(selector, baseHref);
  } catch {
    throw new PublicTraceValidationError("the endpoint is not a valid relative reference");
  }
  const base = new URL(baseHref);
  if (url.origin !== base.origin) {
    throw new PublicTraceValidationError("the endpoint must resolve to the same origin as the viewer");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new PublicTraceValidationError("the endpoint must use http or https on the viewer origin");
  }
  return url;
}

export interface PublicTraceStoreListener {
  (trace: Readonly<PublicTrace>): void;
}

export interface PublicTraceErrorListener {
  (error: unknown): void;
}

/** One exact endpoint payload parsed and hashed but not yet committed. */
export interface VerifiedPublicTracePayload {
  readonly trace: Readonly<PublicTrace>;
  readonly sourcePayloadSha256: string;
  readonly sourcePayloadSizeBytes: number;
}

/**
 * Holds the single accepted public trace. A snapshot of the same run
 * must never move authoritative time backwards; a different run_id
 * replaces the document (an explicit sealed replay reload). Everything
 * else is rejected instead of silently accepted.
 */
export class PublicTraceStore {
  private current: Readonly<PublicTrace> | null = null;
  private sourcePayloadDigest: string | null = null;
  private readonly verifiedPayloads = new WeakMap<object, { generation: number; signal: AbortSignal; callerSignal?: AbortSignal }>();
  private generation = 0;
  private request: AbortController | null = null;
  private disposed = false;
  private readonly listeners = new Set<PublicTraceStoreListener>();
  private readonly errorListeners = new Set<PublicTraceErrorListener>();

  get value(): Readonly<PublicTrace> | null {
    return this.current;
  }

  /** SHA-256 of the exact UTF-8 payload accepted by loadRelative. */
  get sourcePayloadSha256(): string | null {
    return this.sourcePayloadDigest;
  }

  subscribe(listener: PublicTraceStoreListener): () => void {
    this.listeners.add(listener);
    if (this.current !== null) {
      listener(this.current);
    }
    return () => this.listeners.delete(listener);
  }

  onError(listener: PublicTraceErrorListener): () => void {
    this.errorListeners.add(listener);
    return () => this.errorListeners.delete(listener);
  }

  set(value: unknown): Readonly<PublicTrace> {
    this.assertAlive();
    this.sourcePayloadDigest = null;
    return this.accept(parsePublicTrace(value), null);
  }

  /** Parse strict JSON text before applying the document invariants. */
  setJson(json: string): Readonly<PublicTrace> {
    this.assertAlive();
    this.sourcePayloadDigest = null;
    return this.accept(parsePublicTraceJson(json), null);
  }

  private assertAlive(): void {
    if (this.disposed) {
      throw new PublicTraceValidationError("the trace store is disposed");
    }
  }

  private accept(
    trace: Readonly<PublicTrace>,
    sourcePayloadDigest: string | null = this.sourcePayloadDigest,
  ): Readonly<PublicTrace> {
    const prior = this.current;
    if (prior !== null && trace.run_id === prior.run_id) {
      if (trace.time.tick < prior.time.tick) {
        throw new PublicTraceValidationError("trace.time.tick: live snapshots cannot move tick backwards");
      }
      if (trace.time.sim_time_ns < prior.time.sim_time_ns) {
        throw new PublicTraceValidationError(
          "trace.time.sim_time_ns: live snapshots cannot move sim_time_ns backwards",
        );
      }
    }
    this.sourcePayloadDigest = sourcePayloadDigest;
    this.current = trace;
    for (const listener of this.listeners) {
      listener(trace);
    }
    return trace;
  }

  /** Fetch and validate one endpoint payload without notifying subscribers. */
  async fetchRelative(selector: string, signal?: AbortSignal, onProgress?: ProgressListener): Promise<VerifiedPublicTracePayload> {
    const url = sameOriginRelativeEndpoint(selector, window.location.href);
    return this.fetchFrom(requestSignal =>
      fetch(url, { signal: requestSignal, redirect: "error", headers: { Accept: "application/json" } }), signal, onProgress);
  }

  /** Fetch and validate one payload from a caller-supplied request (e.g. an authenticated route). */
  async fetchFrom(request: (signal: AbortSignal) => Promise<Response>, signal?: AbortSignal,
      onProgress?: ProgressListener): Promise<VerifiedPublicTracePayload> {
    this.assertAlive();
    assertNotAborted(signal);
    this.request?.abort();
    const controller = new AbortController();
    this.request = controller;
    const generation = ++this.generation;
    const abort = (): void => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const assertCurrent = (): void => {
      this.assertAlive();
      assertNotAborted(controller.signal);
      if (generation !== this.generation) throw new DOMException("Superseded trace request", "AbortError");
    };
    try {
      const response = await request(controller.signal);
      assertCurrent();
      if (!response.ok) throw new PublicTraceValidationError(`public trace request failed (${response.status})`);
      const payload = await readBoundedResponse(response, MAX_PUBLIC_TRACE_BYTES, "public trace", controller.signal, undefined,
        (completed, total) => { assertCurrent(); onProgress?.({ stage: "download", completed, total }); });
      assertCurrent();
      const prefix = new TextDecoder("utf-8").decode(payload.slice(0, 256));
      if ((response.headers.get("content-type") ?? "").includes("text/html") || prefix.trimStart().startsWith("<")) {
        throw new PublicTraceValidationError("public trace endpoint returned HTML instead of JSON");
      }
      onProgress?.({ stage: "hash" });
      const sourcePayloadSha256 = await sha256Hex(payload);
      assertCurrent();
      onProgress?.({ stage: "parse" });
      // Let the browser paint the stage before synchronous JSON/contract work.
      await new Promise<void>(resolve => setTimeout(resolve, 0));
      assertCurrent();
      const trace = parsePublicTraceBytes(payload);
      const freeze = (value: unknown): void => {
        if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
          for (const child of Object.values(value)) freeze(child);
          Object.freeze(value);
        }
      };
      freeze(trace);
      const candidate = Object.freeze({ trace, sourcePayloadSha256, sourcePayloadSizeBytes: payload.byteLength });
      this.verifiedPayloads.set(candidate, { generation, signal: controller.signal, callerSignal: signal });
      return candidate;
    } finally {
      signal?.removeEventListener("abort", abort);
    }
  }

  /** Commit a payload returned by fetchRelative after any caller-side checks. */
  acceptFetched(payload: VerifiedPublicTracePayload): Readonly<PublicTrace> {
    this.assertAlive();
    const pending = payload !== null && typeof payload === "object" ? this.verifiedPayloads.get(payload) : undefined;
    if (pending === undefined || pending.generation !== this.generation || pending.signal.aborted || pending.callerSignal?.aborted) {
      throw new PublicTraceValidationError("the public trace payload is not a pending endpoint load");
    }
    this.verifiedPayloads.delete(payload);
    return this.accept(payload.trace, payload.sourcePayloadSha256);
  }

  /** Fetch one public trace from a same-origin relative endpoint. */
  async loadRelative(selector: string, signal?: AbortSignal): Promise<Readonly<PublicTrace>> {
    return this.acceptFetched(await this.fetchRelative(selector, signal));
  }

  /** Reset endpoint authority without publishing an unvalidated replacement. */
  clear(): void {
    this.generation += 1;
    this.request?.abort();
    this.request = null;
    this.current = null;
    this.sourcePayloadDigest = null;
  }

  dispose(): void {
    this.clear();
    this.disposed = true;
    this.listeners.clear();
    this.errorListeners.clear();
    this.current = null;
    this.sourcePayloadDigest = null;
  }
}
