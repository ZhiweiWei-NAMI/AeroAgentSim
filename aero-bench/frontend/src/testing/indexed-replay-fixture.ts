/** Unit-test bytes only; these are not simulator evidence or delivery data. */
import { createHash } from "node:crypto";
import type { PublicReplayIndex, PublicReplayManifest, SceneState } from "../generated/aero-bench-contracts";
import { publicTrace, scenario, sceneState, stateSample, RUN_ID, SCENARIO_DIGEST, CHAIN_ROOT } from "./trace-v3-fixture";

export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export const encode = (value: string): ArrayBuffer => new TextEncoder().encode(value).buffer;
export const sha256 = (bytes: ArrayBuffer): string => createHash("sha256").update(new Uint8Array(bytes)).digest("hex");

interface FixtureOptions {
  readonly states?: (states: SceneState[]) => void;
  readonly shards?: (texts: string[]) => void;
  readonly history?: (text: string) => string;
  readonly index?: (index: PublicReplayIndex) => void;
  readonly manifest?: (manifest: PublicReplayManifest) => void;
}

export function indexedReplayFixture(count = 1, mutate: FixtureOptions = {}) {
  const states: SceneState[] = [];
  for (let tick = 1; tick <= count; tick += 1) {
    const state = sceneState(tick, ["fixture.uav"], [stateSample(tick, "fixture.uav")], {
      previous_scene_state_digest: states.at(-1)?.scene_state_digest ?? "0".repeat(64),
    });
    const { scene_state_digest: _ignored, ...payload } = state;
    state.scene_state_digest = sha256(encode(canonical(payload)));
    states.push(state as unknown as SceneState);
  }
  mutate.states?.(states);
  const shardTexts: string[] = [];
  for (let start = 0; start < states.length; start += 256) {
    shardTexts.push(states.slice(start, start + 256).map(state => `${canonical(state)}\n`).join(""));
  }
  const historyBytes = encode(mutate.history?.(shardTexts.join("")) ?? shardTexts.join(""));
  mutate.shards?.(shardTexts);
  const historyDigest = sha256(historyBytes);
  const historyFile = { relative_path: `artifacts/${historyDigest}`, sha256: historyDigest, size_bytes: historyBytes.byteLength };
  const trace = publicTrace({
    scenario: scenario({ replay_mode: "indexed" }),
    time: { tick: count, sim_time_ns: count * 1_000_000_000 },
    scene_states: states,
    scene_state_history_artifact: { artifact_id: "fixture.history", selector: "artifacts/scene-state-history", digest: historyDigest, visibility: "public" },
    runtime_artifacts: [{ artifact_id: "fixture.history", artifact_type: "scene.state-history", selector: "artifacts/scene-state-history", sha256: historyDigest, size_bytes: historyBytes.byteLength, replay_path: historyFile.relative_path }],
  });
  const traceBytes = encode(canonical(trace));
  const traceDigest = sha256(traceBytes);
  const files = new Map<string, ArrayBuffer>([[historyFile.relative_path, historyBytes], ["public-trace.json", traceBytes]]);
  const shards = shardTexts.map((text, number) => {
    const bytes = encode(text);
    const digest = sha256(bytes);
    const relative_path = `artifacts/${digest}`;
    files.set(relative_path, bytes);
    const first_tick = number * 256 + 1;
    const last_tick = Math.min((number + 1) * 256, count);
    return { relative_path, sha256: digest, size_bytes: bytes.byteLength, first_tick, last_tick, scene_state_count: last_tick - first_tick + 1 };
  });
  const index: PublicReplayIndex = {
    schema_version: "aero-bench.public-replay-index/v1", run_id: RUN_ID, scenario_digest: SCENARIO_DIGEST,
    event_chain_root: CHAIN_ROOT, first_tick: count > 0 ? 1 : 0, last_tick: count, scene_state_count: count, shards,
  };
  mutate.index?.(index);
  const indexBytes = encode(canonical(index));
  const indexDigest = sha256(indexBytes);
  const indexFile = { relative_path: `artifacts/${indexDigest}`, sha256: indexDigest, size_bytes: indexBytes.byteLength };
  files.set(indexFile.relative_path, indexBytes);
  const manifest: PublicReplayManifest = {
    schema_version: "aero-bench.public-replay-manifest/v1", run_id: RUN_ID, scenario_digest: SCENARIO_DIGEST,
    event_chain_root: CHAIN_ROOT, trace_sha256: traceDigest, replay_mode: "indexed", replay_index: indexFile,
    scene_state_history: historyFile,
    files: [...files].map(([relative_path, bytes]) => ({ relative_path, sha256: sha256(bytes), size_bytes: bytes.byteLength }))
      .sort((a, b) => a.relative_path < b.relative_path ? -1 : 1) as PublicReplayManifest["files"],
  };
  mutate.manifest?.(manifest);
  const manifestBytes = encode(canonical(manifest));
  files.set("replay-manifest.json", manifestBytes);
  const fetcher: typeof fetch = async (url) => {
    const path = new URL(url instanceof Request ? url.url : url).pathname;
    const key = path.replace(/^\/run\/public\/(replay\/)?/, "");
    const bytes = files.get(key);
    return bytes === undefined ? new Response(null, { status: 404 }) : new Response(bytes, {
      headers: { "Content-Length": String(bytes.byteLength), "Content-Type": "application/json" },
    });
  };
  return {
    states, trace, files, manifest, index, fetcher,
    options: {
      fetch: fetcher, digest: async (bytes: ArrayBuffer) => sha256(bytes),
      expectedManifestSha256: sha256(manifestBytes), expectedTraceSha256: traceDigest,
      expectedTraceSizeBytes: traceBytes.byteLength, expectedSceneStates: states,
      expectedSceneStateHistorySha256: historyDigest, expectedSceneStateHistorySizeBytes: historyBytes.byteLength,
    },
  };
}
