import { describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import {
  AssetResolutionError,
  AssetResolver,
  DECLARED_REPLAY_PATH,
  MAX_ASSET_BYTES,
  resolveAssetUrl,
} from "./asset-resolver";

const BASE = "http://127.0.0.1:8000/replay/";
const BYTES = "declared asset bytes";
const DIGEST = createHash("sha256").update(BYTES).digest("hex");

function digestHex(bytes: ArrayBuffer): Promise<string> {
  return Promise.resolve(createHash("sha256").update(new Uint8Array(bytes)).digest("hex"));
}

function resolverWith(
  body: string | null,
  overrides: { status?: number } = {},
): AssetResolver {
  return new AssetResolver({
    baseHref: BASE,
    digest: digestHex,
    createObjectUrl: () => "blob:fixture",
    fetch: (async () =>
      new Response(body === null ? null : body, {
        status: overrides.status ?? 200,
        headers: {
          "Content-Type": "application/octet-stream",
          "Content-Length": String(body?.length ?? 0),
        },
      })) as unknown as typeof fetch,
  });
}

describe("declared replay path grammar", () => {
  it("accepts only content-addressed assets and artifacts paths", () => {
    expect(DECLARED_REPLAY_PATH.test(`assets/${DIGEST}`)).toBe(true);
    expect(DECLARED_REPLAY_PATH.test(`artifacts/${DIGEST}`)).toBe(true);
    expect(DECLARED_REPLAY_PATH.test("assets/model.gltf")).toBe(false);
    expect(DECLARED_REPLAY_PATH.test("../secrets")).toBe(false);
    expect(DECLARED_REPLAY_PATH.test("/etc/passwd")).toBe(false);
    expect(DECLARED_REPLAY_PATH.test(`assets/${DIGEST}/extra`)).toBe(false);
  });

  it("resolves declared paths to same-origin URLs relative to the bundle base", () => {
    const url = resolveAssetUrl(`assets/${DIGEST}`, BASE);
    expect(url.origin).toBe(new URL(BASE).origin);
    expect(url.pathname).toBe(`/replay/assets/${DIGEST}`);
  });

  it("rejects traversal, absolute URLs, and cross-origin outcomes", () => {
    expect(() => resolveAssetUrl("../secrets", BASE)).toThrow(AssetResolutionError);
    expect(() => resolveAssetUrl(`assets/${DIGEST}\\..\\windows`, BASE)).toThrow(AssetResolutionError);
    expect(() => resolveAssetUrl("https://cdn.example/assets/" + DIGEST, BASE)).toThrow(AssetResolutionError);
    expect(() => resolveAssetUrl(`assets/${DIGEST}`, "file:///tmp/replay/")).toThrow(AssetResolutionError);
  });
});

describe("AssetResolver.fetchVerified", () => {
  it("publishes bytes only when the length and SHA-256 both match", async () => {
    const resolver = resolverWith(BYTES);
    const resolved = await resolver.fetchVerified(`assets/${DIGEST}`, {
      sha256: DIGEST,
      sizeBytes: BYTES.length,
      mediaType: "application/json",
    });
    expect(resolved.url).toBe("blob:fixture");
    expect(resolved.sha256).toBe(DIGEST);
    resolver.dispose();
  });

  it("rejects byte-length mismatches before hashing anything", async () => {
    const resolver = new AssetResolver({
      baseHref: BASE,
      digest: digestHex,
      createObjectUrl: () => "blob:fixture",
      fetch: (async () =>
        new Response(BYTES.slice(0, -1), { status: 200, headers: { "Content-Type": "application/octet-stream" } })) as unknown as typeof fetch,
    });
    await expect(
      resolver.fetchVerified(`assets/${DIGEST}`, { sha256: DIGEST, sizeBytes: BYTES.length, mediaType: null }),
    ).rejects.toThrow(/byte length .* differs from the declared size/);
    resolver.dispose();
  });

  it("rejects digest mismatches without publishing a blob URL", async () => {
    const createObjectUrl = vi.fn(() => "blob:fixture");
    const resolver = new AssetResolver({
      baseHref: BASE,
      digest: digestHex,
      createObjectUrl,
      fetch: (async () => new Response(BYTES + "!", { status: 200 })) as unknown as typeof fetch,
    });
    await expect(
      resolver.fetchVerified(`assets/${DIGEST}`, { sha256: DIGEST, sizeBytes: (BYTES + "!").length, mediaType: null }),
    ).rejects.toThrow(/digest .* differs from the declared digest/);
    expect(createObjectUrl).not.toHaveBeenCalled();
    resolver.dispose();
  });

  it("rejects placeholder digests and oversized declared sizes up front", async () => {
    const resolver = resolverWith(BYTES);
    await expect(
      resolver.fetchVerified(`assets/${DIGEST}`, { sha256: "0".repeat(64), sizeBytes: 1, mediaType: null }),
    ).rejects.toThrow(/placeholder/);
    await expect(
      resolver.fetchVerified(`assets/${DIGEST}`, {
        sha256: DIGEST,
        sizeBytes: MAX_ASSET_BYTES + 1,
        mediaType: null,
      }),
    ).rejects.toThrow(/exceeds the resolver bound/);
    resolver.dispose();
  });

  it("rejects Content-Length headers that contradict the declaration", async () => {
    const resolver = new AssetResolver({
      baseHref: BASE,
      digest: digestHex,
      createObjectUrl: () => "blob:fixture",
      fetch: (async () =>
        new Response(BYTES, {
          status: 200,
          headers: { "Content-Type": "application/octet-stream", "Content-Length": "1" },
        })) as unknown as typeof fetch,
    });
    await expect(
      resolver.fetchVerified(`assets/${DIGEST}`, { sha256: DIGEST, sizeBytes: BYTES.length, mediaType: null }),
    ).rejects.toThrow(/Content-Length/);
    resolver.dispose();
  });

  it("rejects HTTP failure statuses with an explicit error", async () => {
    const resolver = resolverWith("{}", { status: 404 });
    await expect(
      resolver.fetchVerified(`assets/${DIGEST}`, { sha256: DIGEST, sizeBytes: 2, mediaType: null }),
    ).rejects.toThrow(/asset request failed \(404\)/);
    resolver.dispose();
  });

  it("revokes every published object URL on dispose", async () => {
    const created: string[] = [];
    const resolver = new AssetResolver({
      baseHref: BASE,
      digest: digestHex,
      createObjectUrl: (blob) => {
        const url = `blob:${created.length}`;
        created.push(url);
        expect(blob.size).toBe(BYTES.length);
        return url;
      },
      fetch: (async () => new Response(BYTES, { status: 200 })) as unknown as typeof fetch,
    });
    await resolver.fetchVerified(`assets/${DIGEST}`, {
      sha256: DIGEST,
      sizeBytes: BYTES.length,
      mediaType: "application/octet-stream",
    });
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    resolver.dispose();
    expect(revokeSpy).toHaveBeenCalledWith("blob:0");
    revokeSpy.mockRestore();
    await expect(
      resolver.fetchVerified(`assets/${DIGEST}`, { sha256: DIGEST, sizeBytes: BYTES.length, mediaType: null }),
    ).rejects.toThrow(/disposed/);
  });

  it("refuses to operate when Web Crypto is unavailable", async () => {
    const resolver = new AssetResolver({
      baseHref: BASE,
      digest: (bytes) => {
        void bytes;
        return Promise.reject(new AssetResolutionError("Web Crypto is unavailable; assets cannot be verified"));
      },
      createObjectUrl: () => "blob:fixture",
      fetch: (async () => new Response(BYTES, { status: 200 })) as unknown as typeof fetch,
    });
    await expect(
      resolver.fetchVerified(`assets/${DIGEST}`, { sha256: DIGEST, sizeBytes: BYTES.length, mediaType: null }),
    ).rejects.toThrow(/Web Crypto/);
    resolver.dispose();
  });
});
