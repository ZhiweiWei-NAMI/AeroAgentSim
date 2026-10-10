import { describe, expect, it, vi } from "vitest";
import { SealedReplayError, SealedReplayLoader } from "./replay-loader";
import { canonical, encode, indexedReplayFixture, sha256 } from "./testing/indexed-replay-fixture";
import type { SceneState } from "./generated/aero-bench-contracts";

const endpoint = "/run/public/replay/replay-manifest.json";
function rehash(state: SceneState): void {
  const { scene_state_digest: _ignored, ...payload } = state;
  state.scene_state_digest = sha256(encode(canonical(payload)));
}

describe("SealedReplayLoader", () => {
  it("verifies real hashes and deduplicated single-shard history", async () => {
    const fixture = indexedReplayFixture();
    const loader = await SealedReplayLoader.open(endpoint, fixture.options);
    expect(loader.sceneStateCount).toBe(1);
    expect(await loader.loadSceneState(1)).toEqual(fixture.states[0]);
    expect(Object.isFrozen(await loader.loadSceneState(1))).toBe(true);
    expect(loader.verifiedSceneState(1)).toEqual(fixture.states[0]);
    expect(Object.isFrozen(loader.verifiedSceneState(1))).toBe(true);
    expect(() => loader.verifiedSceneState(0)).toThrow("outside");
    loader.dispose();
    expect(() => loader.verifiedSceneState(1)).toThrow("disposed");
    await expect(loader.loadSceneState(1)).rejects.toThrow("disposed");
  });

  it("validates standalone raw trace bytes before accepting indexed history", async () => {
    const fixture = indexedReplayFixture();
    const loader = await SealedReplayLoader.open(endpoint, { fetch: fixture.fetcher, digest: fixture.options.digest });
    expect(loader.sceneStateCount).toBe(1);
    loader.dispose();
    fixture.files.set("public-trace.json", encode("{}"));
    await expect(SealedReplayLoader.open(endpoint, { fetch: fixture.fetcher, digest: fixture.options.digest })).rejects.toThrow();
  });

  it("supports a genuinely empty pre-motion aborted history", async () => {
    const fixture = indexedReplayFixture(0);
    const loader = await SealedReplayLoader.open(endpoint, fixture.options);
    expect(loader.sceneStateCount).toBe(0);
    await expect(loader.loadSceneState(1)).rejects.toThrow("outside");
    loader.dispose();
  });

  it.each(["replay_mode", "replay_index", "scene_state_history"] as const)("rejects missing %s", async field => {
    const fixture = indexedReplayFixture(1, { manifest: manifest => { delete manifest[field]; } });
    await expect(SealedReplayLoader.open(endpoint, fixture.options)).rejects.toBeInstanceOf(SealedReplayError);
  });

  it.each(["trace", "history", "manifest"] as const)("rejects a mismatched expected %s digest", async part => {
    const fixture = indexedReplayFixture();
    const key = { trace: "expectedTraceSha256", history: "expectedSceneStateHistorySha256", manifest: "expectedManifestSha256" }[part];
    await expect(SealedReplayLoader.open(endpoint, { ...fixture.options, [key]: "f".repeat(64) })).rejects.toThrow(/bind|digest/);
  });

  it("rejects missing files, bad lengths, and truncated content without readiness", async () => {
    for (const kind of ["missing", "length", "digest"]) {
      const fixture = indexedReplayFixture();
      const path = fixture.manifest.replay_index!.relative_path;
      const original = fixture.files.get(path)!;
      if (kind === "missing") fixture.files.delete(path);
      else fixture.files.set(path, kind === "length" ? original.slice(0, -1) : encode("x".repeat(original.byteLength)));
      await expect(SealedReplayLoader.open(endpoint, fixture.options)).rejects.toBeInstanceOf(SealedReplayError);
    }
  });

  it.each([
    { name: "trailing history bytes", history: (text: string) => `${text}\n` },
    { name: "different history bytes with the same line count", history: (text: string) => text.replace('"schema_version":', '"schema_version" :') },
    { name: "truncated shard", shards: (texts: string[]) => { texts[0] = texts[0]!.slice(0, -1); } },
    { name: "duplicate object key", shards: (texts: string[]) => { texts[0] = texts[0]!.replace('{"at":', '{"at":{},"at":'); } },
    { name: "noncanonical shard", shards: (texts: string[]) => { texts[0] = ` ${texts[0]}`; } },
    { name: "forged state digest", states: (states: SceneState[]) => { states[0]!.scene_state_digest = "f".repeat(64); } },
    { name: "wrong run", states: (states: SceneState[]) => { states[0]!.run_id = "f".repeat(64); } },
    { name: "wrong chain root", states: (states: SceneState[]) => { states[0]!.previous_scene_state_digest = "f".repeat(64); rehash(states[0]!); } },
  ])("rejects $name", async mutation => {
    const fixture = indexedReplayFixture(1, mutation);
    await expect(SealedReplayLoader.open(endpoint, fixture.options)).rejects.toBeInstanceOf(SealedReplayError);
  });

  it.each(["gap", "duplicate", "order", "size", "coverage", "identity"])("rejects index %s corruption", async kind => {
    const fixture = indexedReplayFixture(257, { index: index => {
      if (kind === "gap") index.shards[1]!.first_tick += 1;
      if (kind === "duplicate") index.shards[1] = { ...index.shards[0]! };
      if (kind === "order") index.shards.reverse();
      if (kind === "size") index.shards[1]!.size_bytes += 1;
      if (kind === "coverage") index.scene_state_count -= 1;
      if (kind === "identity") index.run_id = "f".repeat(64);
    } });
    await expect(SealedReplayLoader.open(endpoint, fixture.options)).rejects.toThrow();
  });

  it.each(["predecessor", "time"])("checks %s across the 256-record boundary", async kind => {
    const fixture = indexedReplayFixture(257, { states: states => {
      const last = states[256]!;
      if (kind === "predecessor") last.previous_scene_state_digest = "f".repeat(64);
      else last.at.sim_time_ns = states[255]!.at.sim_time_ns;
      rehash(last);
    } });
    await expect(SealedReplayLoader.open(endpoint, fixture.options)).rejects.toThrow("boundary");
  });

  it("never opens when cancellation occurs during hashing", async () => {
    const fixture = indexedReplayFixture();
    const controller = new AbortController();
    await expect(SealedReplayLoader.open(endpoint, {
      ...fixture.options, digest: async bytes => { controller.abort(); return sha256(bytes); },
    }, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  });

  it("coalesces concurrent requests and retains only one shard", async () => {
    const fixture = indexedReplayFixture(257);
    const fetch = vi.fn(fixture.fetcher);
    const loader = await SealedReplayLoader.open(endpoint, { ...fixture.options, fetch });
    fetch.mockClear();
    const [first, second] = await Promise.all([loader.loadSceneState(1), loader.loadSceneState(2)]);
    expect([first.at.tick, second.at.tick]).toEqual([1, 2]);
    expect(fetch).toHaveBeenCalledTimes(1);
    await loader.loadSceneState(257);
    await loader.loadSceneState(1);
    expect(fetch).toHaveBeenCalledTimes(3);
    loader.dispose();
  });

  it("makes a failed refetch terminal instead of retrying on every render", async () => {
    const fixture = indexedReplayFixture(257);
    const fetch = vi.fn(fixture.fetcher);
    const loader = await SealedReplayLoader.open(endpoint, { ...fixture.options, fetch });
    fixture.files.delete(fixture.index.shards[0]!.relative_path);
    fetch.mockClear();
    await expect(loader.loadSceneState(1)).rejects.toThrow();
    await expect(loader.loadSceneState(1)).rejects.toThrow();
    await expect(loader.loadSceneState(257)).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(1);
    loader.dispose();
  });

  it("aborts disposed shard reads even if the fetch implementation ignores the signal", async () => {
    const fixture = indexedReplayFixture(257);
    const fetch = vi.fn(fixture.fetcher);
    const loader = await SealedReplayLoader.open(endpoint, { ...fixture.options, fetch });
    let release!: (value: Response) => void;
    let requestSignal: AbortSignal | undefined;
    fetch.mockImplementationOnce((_url, init) => {
      requestSignal = init?.signal ?? undefined;
      return new Promise(resolve => { release = resolve; });
    });
    const result = loader.loadSceneState(1);
    const rejection = expect(result).rejects.toThrow();
    loader.dispose();
    expect(requestSignal?.aborted).toBe(true);
    release(new Response(fixture.files.get(fixture.index.shards[0]!.relative_path)!));
    await rejection;
  });
});
