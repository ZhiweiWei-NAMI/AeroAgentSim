/**
 * Declared offline replay asset resolver.
 *
 * Only declared, same-origin, content-addressed bytes are accepted. Size and
 * SHA-256 are verified before publication; disposal cancels every pending read.
 */
import { assertNotAborted, readBoundedResponse } from "./verified-bytes";

export const DECLARED_REPLAY_PATH = /^(assets|artifacts)\/[0-9a-f]{64}$/;
export const MAX_ASSET_BYTES = 512 * 1024 * 1024;

export class AssetResolutionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AssetResolutionError";
  }
}

export interface DeclaredAsset {
  readonly sha256: string;
  readonly sizeBytes: number;
  readonly mediaType: string | null;
}

export interface ResolvedAsset {
  readonly url: string;
  readonly sha256: string;
  readonly sizeBytes: number;
  readonly mediaType: string | null;
}

export interface AssetResolverOptions {
  readonly baseHref: string;
  readonly fetch?: typeof fetch;
  readonly digest?: (bytes: ArrayBuffer) => Promise<string>;
  readonly createObjectUrl?: (blob: Blob) => string;
  readonly maxBytes?: number;
}

function defaultDigest(bytes: ArrayBuffer): Promise<string> {
  if (globalThis.crypto?.subtle === undefined) {
    throw new AssetResolutionError("Web Crypto is unavailable; assets cannot be verified");
  }
  return globalThis.crypto.subtle.digest("SHA-256", bytes).then(buffer =>
    Array.from(new Uint8Array(buffer), byte => byte.toString(16).padStart(2, "0")).join(""));
}

export function resolveAssetUrl(replayPath: string, baseHref: string): URL {
  if (!DECLARED_REPLAY_PATH.test(replayPath)) {
    throw new AssetResolutionError(
      "asset path must be a declared replay path ('assets/<sha256>' or 'artifacts/<sha256>')",
    );
  }
  if (baseHref.length === 0) throw new AssetResolutionError("an asset base endpoint is required");
  let url: URL;
  try {
    url = new URL(replayPath, baseHref);
  } catch {
    throw new AssetResolutionError("the asset base endpoint is not a valid URL");
  }
  if (url.origin !== new URL(baseHref).origin) {
    throw new AssetResolutionError("the asset must resolve to the same origin as the viewer");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new AssetResolutionError("the asset must use http or https on the viewer origin");
  }
  return url;
}

export class AssetResolver {
  private readonly baseHref: string;
  private readonly doFetch: typeof fetch;
  private readonly digest: (bytes: ArrayBuffer) => Promise<string>;
  private readonly createObjectUrl: (blob: Blob) => string;
  private readonly maxBytes: number;
  private readonly objectUrls = new Set<string>();
  private readonly requests = new Set<AbortController>();
  private disposed = false;

  constructor(options: AssetResolverOptions) {
    this.baseHref = options.baseHref;
    this.doFetch = options.fetch ?? fetch.bind(globalThis);
    this.digest = options.digest ?? defaultDigest;
    this.createObjectUrl = options.createObjectUrl ?? (blob => URL.createObjectURL(blob));
    this.maxBytes = options.maxBytes ?? MAX_ASSET_BYTES;
    if (!Number.isSafeInteger(this.maxBytes) || this.maxBytes <= 0 || this.maxBytes > MAX_ASSET_BYTES) {
      throw new AssetResolutionError("the resolver byte bound is invalid");
    }
  }

  private assertActive(signal?: AbortSignal): void {
    if (this.disposed) throw new AssetResolutionError("the asset resolver is disposed");
    assertNotAborted(signal);
  }

  async fetchVerified(replayPath: string, declared: DeclaredAsset, signal?: AbortSignal): Promise<ResolvedAsset> {
    const bytes = await this.fetchVerifiedBytes(replayPath, declared, signal);
    this.assertActive(signal);
    const mediaType = declared.mediaType ?? "application/octet-stream";
    if (!/^[-+.a-z0-9]+\/[-+.a-z0-9]+$/.test(mediaType)) {
      throw new AssetResolutionError("declared asset media type is invalid");
    }
    const blobUrl = this.createObjectUrl(new Blob([bytes], { type: mediaType }));
    if (typeof blobUrl !== "string" || blobUrl.length === 0) {
      throw new AssetResolutionError("the runtime could not publish the verified asset bytes");
    }
    this.objectUrls.add(blobUrl);
    return { url: blobUrl, sha256: declared.sha256, sizeBytes: declared.sizeBytes, mediaType: declared.mediaType };
  }

  async fetchVerifiedBytes(replayPath: string, declared: DeclaredAsset, signal?: AbortSignal): Promise<ArrayBuffer> {
    this.assertActive(signal);
    const url = resolveAssetUrl(replayPath, this.baseHref);
    if (!Number.isSafeInteger(declared.sizeBytes) || declared.sizeBytes < 0) {
      throw new AssetResolutionError("declared asset size must be a non-negative safe integer");
    }
    if (declared.sizeBytes > this.maxBytes) {
      throw new AssetResolutionError(`declared asset size exceeds the resolver bound (${this.maxBytes})`);
    }
    if (declared.sha256 === "0".repeat(64)) {
      throw new AssetResolutionError("declared asset digest is a placeholder");
    }
    if (!/^[0-9a-f]{64}$/.test(declared.sha256) || !replayPath.endsWith(declared.sha256)) {
      throw new AssetResolutionError("declared asset path differs from its digest");
    }
    const controller = new AbortController();
    const abort = (): void => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    this.requests.add(controller);
    try {
      const response = await this.doFetch(url, {
        signal: controller.signal, redirect: "error", headers: { Accept: "*/*" },
      });
      this.assertActive(controller.signal);
      if (!response.ok) throw new AssetResolutionError(`asset request failed (${response.status})`);
      const bytes = await readBoundedResponse(
        response, declared.sizeBytes, "asset", controller.signal, declared.sizeBytes,
      );
      this.assertActive(controller.signal);
      const actualDigest = await this.digest(bytes);
      this.assertActive(controller.signal);
      if (actualDigest !== declared.sha256) {
        throw new AssetResolutionError(
          `asset digest ${actualDigest} differs from the declared digest ${declared.sha256}`,
        );
      }
      return bytes;
    } catch (error) {
      const aborted = controller.signal.aborted;
      controller.abort();
      if (aborted) throw new DOMException("Asset request was aborted", "AbortError");
      if (error instanceof AssetResolutionError) throw error;
      throw new AssetResolutionError(`asset could not be loaded (${error instanceof Error ? error.message : "unknown error"})`);
    } finally {
      signal?.removeEventListener("abort", abort);
      this.requests.delete(controller);
    }
  }

  revoke(url: string): void {
    if (this.objectUrls.delete(url)) URL.revokeObjectURL(url);
  }

  dispose(): void {
    this.disposed = true;
    for (const controller of this.requests) controller.abort();
    this.requests.clear();
    for (const url of this.objectUrls) URL.revokeObjectURL(url);
    this.objectUrls.clear();
  }
}
