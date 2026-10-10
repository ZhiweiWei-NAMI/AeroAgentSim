/** Indexed replay becomes readable only after the complete sealed byte closure. */
import { AssetResolver } from "./asset-resolver";
import {
  assertPublicReplayIndex,
  assertPublicReplayManifest,
  assertSceneState,
} from "./generated/contract-validators";
import type {
  PublicReplayFile, PublicReplayIndex, PublicReplayManifest, PublicReplayShard, SceneState,
} from "./generated/aero-bench-contracts";
import { MAX_PUBLIC_TRACE_BYTES, parsePublicTraceBytes, sameOriginRelativeEndpoint } from "./trace";
import { assertNotAborted, readBoundedResponse } from "./verified-bytes";
import { canonicalJsonWithoutMember, parseStrictJson } from "./strict-json";

export class SealedReplayError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SealedReplayError";
  }
}

export interface SealedReplayLoaderOptions {
  readonly fetch?: typeof fetch;
  readonly digest?: (bytes: ArrayBuffer) => Promise<string>;
  readonly maxBytes?: number;
  readonly expectedManifestSha256?: string;
  readonly expectedTraceSha256?: string;
  readonly expectedTraceSizeBytes?: number;
  readonly expectedSceneStateHistorySha256?: string;
  readonly expectedSceneStateHistorySizeBytes?: number;
  readonly maxManifestBytes?: number;
  readonly expectedSceneStates?: readonly SceneState[];
  readonly onProgress?: (completed: number, total: number) => void;
}

export const MAX_MANIFEST_BYTES = 8 * 1024 * 1024;
const MAX_TRACE_BYTES = MAX_PUBLIC_TRACE_BYTES;

async function defaultDigest(bytes: ArrayBuffer): Promise<string> {
  if (globalThis.crypto?.subtle === undefined) {
    throw new SealedReplayError("Web Crypto is unavailable; replay cannot be verified");
  }
  const result = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(result), byte => byte.toString(16).padStart(2, "0")).join("");
}

function text(bytes: ArrayBuffer): string {
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

export function jsonValuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((value, index) => jsonValuesEqual(value, right[index]));
  }
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") return false;
  const keys = Object.keys(left).sort();
  const other = Object.keys(right).sort();
  return keys.length === other.length && keys.every((key, index) => key === other[index]
    && jsonValuesEqual((left as Record<string, unknown>)[key], (right as Record<string, unknown>)[key]));
}

function freezeJson<T>(value: T): T {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const item of Object.values(value)) freezeJson(item);
    Object.freeze(value);
  }
  return value;
}

function publicFile(manifest: PublicReplayManifest, path: string): PublicReplayFile {
  const file = manifest.files.find(item => item.relative_path === path);
  if (file === undefined) throw new SealedReplayError(`replay manifest does not declare ${path}`);
  return file;
}

function assertDescriptor(manifest: PublicReplayManifest, reference: PublicReplayFile): void {
  const file = publicFile(manifest, reference.relative_path);
  if (reference.relative_path !== `artifacts/${reference.sha256}`
    || file.sha256 !== reference.sha256 || file.size_bytes !== reference.size_bytes) {
    throw new SealedReplayError("replay descriptor differs from its manifest file");
  }
}

function parseManifest(bytes: ArrayBuffer): PublicReplayManifest {
  const value = parseStrictJson(text(bytes));
  assertPublicReplayManifest(value);
  const manifest = value as PublicReplayManifest;
  const paths = manifest.files.map(file => file.relative_path);
  if (paths.some((path, offset) => offset > 0 && path <= paths[offset - 1]!)) {
    throw new SealedReplayError("replay manifest files are not sorted and unique");
  }
  for (const file of manifest.files) {
    if (!Number.isSafeInteger(file.size_bytes) || file.sha256 === "0".repeat(64)
      || (file.relative_path !== "public-trace.json" && !file.relative_path.endsWith(file.sha256))) {
      throw new SealedReplayError("replay manifest file identity is invalid");
    }
  }
  if (publicFile(manifest, "public-trace.json").sha256 !== manifest.trace_sha256) {
    throw new SealedReplayError("replay manifest does not bind its public trace");
  }
  if (manifest.replay_mode !== "indexed" || manifest.replay_index == null || manifest.scene_state_history == null) {
    throw new SealedReplayError("indexed replay must explicitly declare its mode, index and SceneState history");
  }
  assertDescriptor(manifest, manifest.replay_index);
  assertDescriptor(manifest, manifest.scene_state_history);
  return freezeJson(manifest);
}

function parseIndex(bytes: ArrayBuffer, manifest: PublicReplayManifest): PublicReplayIndex {
  const value = parseStrictJson(text(bytes));
  assertPublicReplayIndex(value);
  const index = value as PublicReplayIndex;
  if (index.run_id !== manifest.run_id || index.scenario_digest !== manifest.scenario_digest
    || index.event_chain_root !== manifest.event_chain_root) {
    throw new SealedReplayError("replay index identity differs from its manifest");
  }
  let tick = 1;
  const paths = new Set<string>();
  for (const [offset, shard] of index.shards.entries()) {
    assertDescriptor(manifest, shard);
    if (paths.has(shard.relative_path) || shard.first_tick !== tick
      || shard.scene_state_count !== shard.last_tick - shard.first_tick + 1
      || (offset < index.shards.length - 1 && shard.scene_state_count !== 256)) {
      throw new SealedReplayError("replay index shards are not unique contiguous 256-record ranges");
    }
    paths.add(shard.relative_path);
    tick = shard.last_tick + 1;
  }
  if (index.scene_state_count === 0
    ? index.first_tick !== 0 || index.last_tick !== 0 || index.shards.length !== 0
    : index.first_tick !== 1 || index.last_tick !== index.scene_state_count || tick !== index.last_tick + 1) {
    throw new SealedReplayError("replay index does not cover every recorded tick");
  }
  return freezeJson(index);
}

interface Boundary {
  readonly lastTick: number;
  readonly lastTime: number;
  readonly digest: string;
}

interface PendingShard {
  readonly number: number;
  readonly controller: AbortController;
  readonly promise: Promise<readonly SceneState[]>;
  users: number;
}

export class SealedReplayLoader {
  private readonly boundaries = new Map<number, Boundary>();
  private cachedShardNumber: number | null = null;
  private cachedStates: readonly SceneState[] = [];
  private pending: PendingShard | null = null;
  private disposed = false;
  private ready = false;
  private failure: SealedReplayError | null = null;

  private constructor(
    readonly manifest: PublicReplayManifest,
    readonly index: PublicReplayIndex,
    readonly manifestDigest: string,
    private readonly manifestUrl: URL,
    private readonly resolver: AssetResolver,
    private readonly digest: (bytes: ArrayBuffer) => Promise<string>,
    private readonly expectedStates: readonly SceneState[],
  ) {}

  static async open(selector: string, options: SealedReplayLoaderOptions = {}, signal?: AbortSignal): Promise<SealedReplayLoader> {
    let resolver: AssetResolver | null = null;
    let loader: SealedReplayLoader | null = null;
    try {
      assertNotAborted(signal);
      const url = sameOriginRelativeEndpoint(selector, window.location.href);
      const doFetch = options.fetch ?? fetch.bind(globalThis);
      const digest = options.digest ?? defaultDigest;
      const response = await doFetch(url, { signal, redirect: "error", headers: { Accept: "application/json" } });
      assertNotAborted(signal);
      if (!response.ok) throw new SealedReplayError(`replay manifest request failed (${response.status})`);
      const limit = options.maxManifestBytes ?? MAX_MANIFEST_BYTES;
      if (limit > MAX_MANIFEST_BYTES || limit <= 0) throw new SealedReplayError("manifest byte bound is invalid");
      const bytes = await readBoundedResponse(response, limit, "replay manifest", signal);
      assertNotAborted(signal);
      const manifestDigest = await digest(bytes);
      assertNotAborted(signal);
      if (options.expectedManifestSha256 !== undefined && manifestDigest !== options.expectedManifestSha256) {
        throw new SealedReplayError("replay manifest digest differs from the trace identity");
      }
      const manifest = parseManifest(bytes);
      const traceFile = publicFile(manifest, "public-trace.json");
      if (options.expectedTraceSha256 !== undefined && manifest.trace_sha256 !== options.expectedTraceSha256
        || options.expectedTraceSizeBytes !== undefined && traceFile.size_bytes !== options.expectedTraceSizeBytes) {
        throw new SealedReplayError("replay manifest does not bind the loaded public trace");
      }
      // A standalone caller has not yet authenticated the raw public-trace bytes.
      let expected = options.expectedSceneStates;
      if (options.expectedTraceSha256 === undefined || expected === undefined) {
        const traceResponse = await doFetch(new URL("public-trace.json", url), { signal, redirect: "error" });
        assertNotAborted(signal);
        if (!traceResponse.ok) throw new SealedReplayError("public trace request failed");
        if (traceFile.size_bytes > MAX_TRACE_BYTES) throw new SealedReplayError("public trace exceeds its byte bound");
        const traceBytes = await readBoundedResponse(traceResponse, traceFile.size_bytes, "public trace", signal, traceFile.size_bytes);
        assertNotAborted(signal);
        const traceDigest = await digest(traceBytes);
        assertNotAborted(signal);
        if (traceDigest !== traceFile.sha256) throw new SealedReplayError("public trace digest differs from its manifest");
        const trace = parsePublicTraceBytes(traceBytes);
        const historyArtifact = trace.runtime_artifacts.find(file => file.artifact_id === trace.scene_state_history_artifact.artifact_id);
        if (historyArtifact === undefined || historyArtifact.artifact_type !== "scene.state-history"
          || historyArtifact.sha256 !== manifest.scene_state_history!.sha256
          || historyArtifact.size_bytes !== manifest.scene_state_history!.size_bytes
          || historyArtifact.selector !== trace.scene_state_history_artifact.selector
          || historyArtifact.replay_path !== manifest.scene_state_history!.relative_path) {
          throw new SealedReplayError("public trace history descriptor differs from its manifest");
        }
        if (trace.scenario.replay_mode !== "indexed" || trace.run_id !== manifest.run_id
          || trace.scenario_digest !== manifest.scenario_digest || trace.event_chain_root !== manifest.event_chain_root
          || trace.scene_state_history_artifact.digest !== manifest.scene_state_history!.sha256) {
          throw new SealedReplayError("public trace identity differs from the replay manifest");
        }
        expected = trace.scene_states;
      }
      const history = manifest.scene_state_history!;
      if (options.expectedSceneStateHistorySha256 !== undefined && history.sha256 !== options.expectedSceneStateHistorySha256
        || options.expectedSceneStateHistorySizeBytes !== undefined && history.size_bytes !== options.expectedSceneStateHistorySizeBytes) {
        throw new SealedReplayError("replay manifest does not bind the sealed SceneState history artifact");
      }
      resolver = new AssetResolver({ baseHref: url.href, fetch: doFetch, digest, maxBytes: options.maxBytes });
      const historyBytes = await resolver.fetchVerifiedBytes(history.relative_path, {
        sha256: history.sha256, sizeBytes: history.size_bytes, mediaType: "application/jsonl",
      }, signal);
      assertNotAborted(signal);
      const indexFile = manifest.replay_index!;
      const indexBytes = await resolver.fetchVerifiedBytes(indexFile.relative_path, {
        sha256: indexFile.sha256, sizeBytes: indexFile.size_bytes, mediaType: "application/json",
      }, signal);
      assertNotAborted(signal);
      const index = parseIndex(indexBytes, manifest);
      loader = new SealedReplayLoader(manifest, index, manifestDigest, url, resolver, digest, freezeJson(expected));
      await loader.validateAll(new Uint8Array(historyBytes), signal, options.onProgress);
      assertNotAborted(signal);
      loader.ready = true;
      return loader;
    } catch (error) {
      loader?.dispose();
      resolver?.dispose();
      assertNotAborted(signal);
      throw error instanceof SealedReplayError ? error
        : new SealedReplayError(error instanceof Error ? error.message : "sealed replay validation failed");
    }
  }

  private assertActive(signal?: AbortSignal): void {
    assertNotAborted(signal);
    if (this.disposed) throw new SealedReplayError("sealed replay loader is disposed");
    if (this.failure !== null) throw this.failure;
  }

  private async fetchShard(number: number, signal?: AbortSignal): Promise<{ bytes: Uint8Array; states: readonly SceneState[] }> {
    this.assertActive(signal);
    const shard = this.index.shards[number]!;
    const bytes = await this.resolver.fetchVerifiedBytes(shard.relative_path, {
      sha256: shard.sha256, sizeBytes: shard.size_bytes, mediaType: "application/jsonl",
    }, signal);
    this.assertActive(signal);
    const states = await this.parseShard(bytes, shard, signal);
    this.assertActive(signal);
    return { bytes: new Uint8Array(bytes), states };
  }

  private async parseShard(bytes: ArrayBuffer, shard: PublicReplayShard, signal?: AbortSignal): Promise<readonly SceneState[]> {
    const content = text(bytes);
    if (!content.endsWith("\n")) throw new SealedReplayError("replay shard is not newline terminated");
    const lines = content.slice(0, -1).split("\n");
    if (lines.length !== shard.scene_state_count) throw new SealedReplayError("replay shard has incomplete records");
    const states: SceneState[] = [];
    for (const [offset, line] of lines.entries()) {
      const value = parseStrictJson(line);
      assertSceneState(value);
      const state = value as SceneState;
      if (state.run_id !== this.index.run_id || state.scenario_digest !== this.index.scenario_digest
        || state.at.tick !== shard.first_tick + offset || !Number.isSafeInteger(state.at.sim_time_ns)) {
        throw new SealedReplayError("replay shard record has the wrong run, tick, or time");
      }
      const payload = new TextEncoder().encode(canonicalJsonWithoutMember(line, "scene_state_digest"));
      const digest = await this.digest(payload.buffer);
      this.assertActive(signal);
      if (digest !== state.scene_state_digest) throw new SealedReplayError("SceneState digest differs from its canonical record");
      if (!jsonValuesEqual(this.expectedStates[state.at.tick - 1], state)) {
        throw new SealedReplayError(`replay shard state at tick ${state.at.tick} differs from public trace`);
      }
      const previous = states.at(-1);
      if (previous !== undefined && (state.previous_scene_state_digest !== previous.scene_state_digest
        || state.at.sim_time_ns <= previous.at.sim_time_ns)) {
        throw new SealedReplayError("replay shard predecessor chain is invalid");
      }
      states.push(freezeJson(state));
    }
    return Object.freeze(states);
  }

  private recordBoundary(number: number, states: readonly SceneState[]): void {
    const first = states[0]!;
    const last = states.at(-1)!;
    const previous = this.boundaries.get(number - 1);
    if (number === 0 ? first.previous_scene_state_digest !== "0".repeat(64)
      : previous === undefined || first.at.tick !== previous.lastTick + 1
        || first.previous_scene_state_digest !== previous.digest || first.at.sim_time_ns <= previous.lastTime) {
      throw new SealedReplayError("replay shard predecessor boundary is invalid");
    }
    this.boundaries.set(number, { lastTick: last.at.tick, lastTime: last.at.sim_time_ns, digest: last.scene_state_digest });
  }

  private async validateAll(history: Uint8Array, signal?: AbortSignal, onProgress?: (completed: number, total: number) => void): Promise<void> {
    onProgress?.(0, this.index.shards.length);
    if (this.expectedStates.length !== this.index.scene_state_count) {
      throw new SealedReplayError("public trace SceneState count differs from sealed replay index");
    }
    let offset = 0;
    for (let number = 0; number < this.index.shards.length; number += 1) {
      const { bytes, states } = await this.fetchShard(number, signal);
      this.assertActive(signal);
      if (offset + bytes.length > history.length || bytes.some((byte, index) => byte !== history[offset + index])) {
        throw new SealedReplayError("replay shard bytes differ from the sealed SceneState history");
      }
      offset += bytes.length;
      this.recordBoundary(number, states);
      this.cachedShardNumber = number;
      this.cachedStates = states;
      onProgress?.(number + 1, this.index.shards.length);
    }
    if (offset !== history.length) throw new SealedReplayError("replay index does not cover the sealed history bytes");
  }

  async loadShard(number: number, signal?: AbortSignal): Promise<readonly SceneState[]> {
    this.assertActive(signal);
    if (!this.ready) throw new SealedReplayError("sealed replay has not completed validation");
    if (!Number.isSafeInteger(number) || number < 0 || number >= this.index.shards.length) {
      throw new SealedReplayError("replay shard number is outside the sealed index");
    }
    if (this.cachedShardNumber === number) return this.cachedStates;
    if (this.pending?.number !== number || this.pending.controller.signal.aborted) {
      this.pending?.controller.abort();
      const controller = new AbortController();
      const promise = this.fetchShard(number, controller.signal).then(({ states }) => {
        this.assertActive(controller.signal);
        this.recordBoundary(number, states);
        this.cachedShardNumber = number;
        this.cachedStates = states;
        return states;
      }).catch((error: unknown) => {
        if (!controller.signal.aborted && !this.disposed) {
          this.failure = new SealedReplayError(error instanceof Error ? error.message : "replay shard failed");
          this.cachedShardNumber = null;
          this.cachedStates = [];
        }
        throw error;
      }).finally(() => {
        if (this.pending?.controller === controller) this.pending = null;
      });
      this.pending = { number, controller, promise, users: 0 };
    }
    const pending = this.pending;
    pending.users += 1;
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (): boolean => {
        if (settled) return false;
        settled = true;
        signal?.removeEventListener("abort", abort);
        pending.users -= 1;
        if (pending.users === 0) pending.controller.abort();
        return true;
      };
      const abort = (): void => {
        if (finish()) reject(new DOMException("Replay request was aborted", "AbortError"));
      };
      signal?.addEventListener("abort", abort, { once: true });
      pending.promise.then(states => { if (finish()) resolve(states); }, error => { if (finish()) reject(error); });
      if (signal?.aborted) abort();
    });
  }

  async loadSceneState(tick: number, signal?: AbortSignal): Promise<SceneState> {
    this.assertActive(signal);
    if (!Number.isSafeInteger(tick) || tick < 1 || tick > this.index.last_tick) {
      throw new SealedReplayError("replay tick is outside the sealed index");
    }
    const number = Math.floor((tick - 1) / 256);
    const states = await this.loadShard(number, signal);
    this.assertActive(signal);
    return states[tick - this.index.shards[number]!.first_tick]!;
  }

  /** Exact immutable frame authenticated by the complete open-time closure. */
  verifiedSceneState(tick: number): SceneState {
    this.assertActive();
    if (!this.ready) throw new SealedReplayError("sealed replay has not completed validation");
    if (!Number.isSafeInteger(tick) || tick < 1 || tick > this.index.last_tick) {
      throw new SealedReplayError("replay tick is outside the sealed index");
    }
    const state = this.expectedStates[tick - 1];
    if (state === undefined || state.at.tick !== tick) {
      throw new SealedReplayError("verified frame differs from the sealed index");
    }
    return state;
  }

  dispose(): void {
    this.disposed = true;
    this.pending?.controller.abort();
    this.pending = null;
    this.resolver.dispose();
    this.cachedShardNumber = null;
    this.cachedStates = [];
    this.boundaries.clear();
  }

  get sceneStateCount(): number { return this.index.scene_state_count; }
  get source(): URL { return new URL(this.manifestUrl.href); }
}
