import { MAX_ASSET_BYTES } from "./asset-resolver";
import { assertNotAborted, readBoundedResponse } from "./verified-bytes";

export const MAX_ASSET_RATE_LIMIT_RETRIES = 4;
const MAX_CACHE_BYTES = 256 * 1024 * 1024;

/** The existing Control service shares its rate budget with status and control. */
export function retryDelayMs(response: Response): number | null {
  const value = response.headers.get("Retry-After")?.trim();
  let delay = 60_000;
  if (value !== undefined && value !== "") {
    if (/^[0-9]+$/.test(value)) delay = Number(value) * 1000;
    else {
      const at = Date.parse(value);
      delay = Number.isFinite(at) ? Math.max(0, at - Date.now()) : 60_000;
    }
  }
  return Number.isSafeInteger(delay) && delay >= 0 && delay <= 5 * 60_000 ? delay : null;
}

export function waitForRetry(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DOMException("Asset request was aborted", "AbortError"));
  return new Promise((resolve, reject) => {
    const abort = (): void => {
      clearTimeout(timer); signal?.removeEventListener("abort", abort);
      reject(new DOMException("Asset request was aborted", "AbortError"));
    };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, delayMs);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
  });
}

interface StoredResponse { bytes: ArrayBuffer; status: number; statusText: string; headers: Headers }
interface Pending { controller: AbortController; consumers: number; promise: Promise<StoredResponse> }

/** One authenticated run's immutable assets. No credentials or bytes are persisted. */
export class ControlAssetQueue {
  private startTail: Promise<void> = Promise.resolve();
  private readonly lanes: Promise<void>[];
  private nextLane = 0;
  private nextStart = 0;
  private readonly cached = new Map<string, StoredResponse>();
  private readonly pending = new Map<string, Pending>();
  private cachedBytes = 0;
  private readonly counts = { requests: 0, cacheHits: 0, inflightJoins: 0, rateLimits: 0, retries: 0, failures: 0 };

  constructor(private readonly request: (digest: string, signal?: AbortSignal) => Promise<Response>,
      private readonly intervalMs = 600, concurrency = 4) {
    if (!Number.isSafeInteger(intervalMs) || intervalMs < 0) throw new Error("Invalid asset request interval");
    if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 16) {
      throw new Error("Invalid asset request concurrency");
    }
    this.lanes = Array.from({ length: concurrency }, () => Promise.resolve());
  }

  get diagnostics(): Readonly<Record<string, number>> {
    return Object.freeze({ ...this.counts, cachedAssets: this.cached.size, cachedBytes: this.cachedBytes,
      pendingAssets: this.pending.size, intervalMs: this.intervalMs, concurrency: this.lanes.length });
  }

  async fetch(digest: string, signal?: AbortSignal): Promise<Response> {
    assertNotAborted(signal);
    const stored = this.cached.get(digest);
    if (stored !== undefined) {
      this.counts.cacheHits += 1;
      this.cached.delete(digest); this.cached.set(digest, stored);
      return this.response(stored);
    }
    let job = this.pending.get(digest);
    if (job === undefined) {
      const controller = new AbortController();
      const lane = this.nextLane++ % this.lanes.length;
      const promise = this.lanes[lane]!.then(() => this.load(digest, controller.signal));
      job = { controller, consumers: 0, promise };
      this.pending.set(digest, job);
      this.lanes[lane] = promise.then(() => undefined, () => undefined);
      const current = job;
      void promise.finally(() => { if (this.pending.get(digest) === current) this.pending.delete(digest); }).catch(() => {});
    } else this.counts.inflightJoins += 1;
    const current = job;
    current.consumers += 1;
    return new Promise<Response>((resolve, reject) => {
      let settled = false;
      const finish = (): boolean => {
        if (settled) return false;
        settled = true; signal?.removeEventListener("abort", abort); current.consumers -= 1;
        return true;
      };
      const abort = (): void => {
        if (!finish()) return;
        if (current.consumers === 0) {
          current.controller.abort();
          if (this.pending.get(digest) === current) this.pending.delete(digest);
        }
        reject(new DOMException("Asset request was aborted", "AbortError"));
      };
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) { abort(); return; }
      current.promise.then(value => { if (finish()) resolve(this.response(value)); },
        error => { if (finish()) reject(error); });
    });
  }

  private response(value: StoredResponse): Response {
    return new Response(value.bytes.slice(0), { status: value.status, statusText: value.statusText, headers: value.headers });
  }

  private async paceStart(signal: AbortSignal): Promise<void> {
    // Starts share one rate budget even while earlier response bodies download.
    // Recheck the deadline after a wait: any lane may have received a 429.
    const turn = this.startTail.then(async () => {
      assertNotAborted(signal);
      while (Date.now() < this.nextStart) {
        await waitForRetry(this.nextStart - Date.now(), signal);
        assertNotAborted(signal);
      }
      this.nextStart = Date.now() + this.intervalMs;
    });
    this.startTail = turn.then(() => undefined, () => undefined);
    await turn;
  }

  private async load(digest: string, signal: AbortSignal): Promise<StoredResponse> {
    try {
      for (let retry = 0; ; retry += 1) {
        assertNotAborted(signal);
        await this.paceStart(signal);
        this.counts.requests += 1;
        const response = await this.request(digest, signal);
        if (response.status === 429) {
          this.counts.rateLimits += 1;
          const delay = retryDelayMs(response);
          // An exhausted request still places the whole asset queue in cooldown.
          if (delay !== null) this.nextStart = Math.max(this.nextStart, Date.now() + delay);
          if (delay !== null && retry < MAX_ASSET_RATE_LIMIT_RETRIES) {
            await response.body?.cancel();
            this.counts.retries += 1;
            continue;
          }
        }
        const bytes = response.body === null ? new ArrayBuffer(0)
          : await readBoundedResponse(response, MAX_ASSET_BYTES, "Control asset", signal);
        const stored = { bytes, status: response.status, statusText: response.statusText, headers: new Headers(response.headers) };
        if (response.ok && bytes.byteLength <= MAX_CACHE_BYTES) {
          while (this.cachedBytes + bytes.byteLength > MAX_CACHE_BYTES && this.cached.size > 0) {
            const oldest = this.cached.keys().next().value!;
            this.cachedBytes -= this.cached.get(oldest)!.bytes.byteLength; this.cached.delete(oldest);
          }
          this.cached.set(digest, stored); this.cachedBytes += bytes.byteLength;
        }
        if (!response.ok) this.counts.failures += 1;
        return stored;
      }
    } catch (error) { this.counts.failures += 1; throw error; }
  }
}
