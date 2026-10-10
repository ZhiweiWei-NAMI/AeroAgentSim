/**
 * Sealed replay of a finished Control API run, read over authenticated HTTP.
 *
 * The replay viewer reads a sealed run as a directory: `public-trace.json`,
 * `replay/replay-manifest.json`, `replay/artifacts/<sha256>` and
 * `replay/assets/<sha256>`. For a Control run that directory is virtual: a
 * same-origin path prefix that never reaches the network. Its fetch adapter maps
 * each file to the bearer-authenticated Control route that serves the same bytes,
 * and every other path is an explicit error. Credentials stay inside the source's
 * closures; they are never placed in a URL or persisted.
 */

export interface ControlReplaySource {
  readonly runId: string;
  manifest(signal?: AbortSignal): Promise<Response>;
  trace(signal?: AbortSignal): Promise<Response>;
  asset(digest: string, signal?: AbortSignal): Promise<Response>;
}

const CONTROL_REPLAY_PREFIX = "/__control-replay/";
const RUN_ID = /^[0-9a-f]{64}$/;
const SEALED_FILE = /^replay\/(?:artifacts|assets)\/([0-9a-f]{64})$/;
const DEFAULT_ASSET_RETRY_DELAY_MS = 60_000;
const MAX_ASSET_RETRY_DELAY_MS = 5 * 60_000;
const MAX_ASSET_RATE_LIMIT_RETRIES = 4;

function abortError(): DOMException {
  return new DOMException("Control replay asset request was aborted", "AbortError");
}

function retryDelayMs(response: Response): number | null {
  const value = response.headers.get("Retry-After")?.trim();
  let delay = DEFAULT_ASSET_RETRY_DELAY_MS;
  if (value !== undefined && value !== "") {
    if (/^[0-9]+$/.test(value)) {
      delay = Number(value) * 1000;
    } else {
      const at = Date.parse(value);
      delay = Number.isFinite(at) ? Math.max(0, at - Date.now()) : DEFAULT_ASSET_RETRY_DELAY_MS;
    }
  }
  return Number.isSafeInteger(delay) && delay >= 0 && delay <= MAX_ASSET_RETRY_DELAY_MS ? delay : null;
}

function waitForRetry(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cleanup = (): void => signal?.removeEventListener("abort", abort);
    const abort = (): void => {
      if (timer !== undefined) clearTimeout(timer);
      cleanup();
      reject(abortError());
    };
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) { abort(); return; }
    timer = setTimeout(() => { cleanup(); resolve(); }, delayMs);
  });
}

async function fetchSealedAsset(source: ControlReplaySource, digest: string,
    signal?: AbortSignal): Promise<Response> {
  for (let retry = 0; ; retry += 1) {
    if (signal?.aborted) throw abortError();
    const response = await source.asset(digest, signal);
    if (signal?.aborted) {
      void response.body?.cancel().catch(() => undefined);
      throw abortError();
    }
    if (response.status !== 429 || retry >= MAX_ASSET_RATE_LIMIT_RETRIES) return response;
    const delay = retryDelayMs(response);
    if (delay === null) return response;
    await response.body?.cancel().catch(() => undefined);
    await waitForRetry(delay, signal);
  }
}

/** Virtual directory of one run's sealed public files, on the viewer's own origin. */
export function controlReplayBaseHref(runId: string, origin: string): string {
  if (!RUN_ID.test(runId)) throw new Error("Control replay run_id must be a lowercase sha256 hex identifier");
  return new URL(`${CONTROL_REPLAY_PREFIX}${runId}/`, origin).href;
}

/** Fetch adapter for the virtual directory under `baseHref`. */
export function controlReplayFetch(source: ControlReplaySource, baseHref: string): typeof fetch {
  const base = new URL(baseHref);
  return (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    if (url.origin !== base.origin || !url.pathname.startsWith(base.pathname) || url.search !== "") {
      throw new Error(`Control replay request is outside run ${source.runId}: ${url.pathname}`);
    }
    const method = init?.method ?? (input instanceof Request ? input.method : "GET");
    if (method !== "GET") {
      throw new Error("Control replay files are read-only");
    }
    const path = url.pathname.slice(base.pathname.length);
    const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
    if (path === "public-trace.json" || path === "replay/public-trace.json") return source.trace(signal);
    if (path === "replay/replay-manifest.json") return source.manifest(signal);
    const sealed = SEALED_FILE.exec(path);
    if (sealed !== null) return fetchSealedAsset(source, sealed[1]!, signal);
    throw new Error(`Control replay has no route for ${path}`);
  }) as typeof fetch;
}
