import { createHash } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("./map", () => ({ PublicTraceMap: class {
  isAvailable = true;
  render = vi.fn(); focus = vi.fn(() => true); setFollow = vi.fn();
  refreshCursorLabels = vi.fn(); destroy = vi.fn();
} }));
import { PublicTraceApp } from "./app";
import type { ControlReplaySource } from "./control-replay-source";
import { RUN_ID, SCENARIO_DIGEST, CHAIN_ROOT, publicTrace } from "./testing/trace-v3-fixture";

let app: PublicTraceApp | undefined;
afterEach(() => { app?.dispose(); document.body.replaceChildren(); vi.unstubAllGlobals(); });

it("defaults the editable Control endpoint to the current browser origin", () => {
  const root = document.createElement("div"); document.body.append(root);
  app = new PublicTraceApp(root, "live");
  const endpoint = root.querySelector<HTMLInputElement>('input[type="text"].credential-input')!;
  expect(endpoint.value).toBe(window.location.origin);
  expect(endpoint.readOnly).toBe(false);
  expect(endpoint.disabled).toBe(false);
  endpoint.value = "http://localhost:9000";
  expect(endpoint.value).toBe("http://localhost:9000");
});

it("replaces live controls with embedded Control replay and binds its verified asset directory", async () => {
  const root = document.createElement("div"); document.body.append(root);
  app = new PublicTraceApp(root, "live");
  root.querySelector<HTMLButtonElement>(".p02-controls-toggle")!.click();
  expect(document.body.classList.contains("p02-controls-open")).toBe(true);
  const bytes = new TextEncoder().encode(JSON.stringify(publicTrace()));
  const digest = createHash("sha256").update(bytes).digest("hex");
  const assetDigest = "b".repeat(64);
  const source = {
    runId: RUN_ID,
    trace: vi.fn(async () => new Response(bytes)),
    manifest: vi.fn(async () => new Response(JSON.stringify({
      schema_version: "aero-bench.public-replay-manifest/v1", run_id: RUN_ID,
      scenario_digest: SCENARIO_DIGEST, event_chain_root: CHAIN_ROOT, replay_mode: "embedded",
      trace_sha256: digest, replay_index: null, scene_state_history: null,
      files: [{ relative_path: "public-trace.json", sha256: digest, size_bytes: bytes.byteLength }],
    }))),
    asset: vi.fn(async () => new Response("sealed asset")),
  };
  const network = vi.fn(() => { throw new Error("Assets must use the authenticated Control closure"); });
  vi.stubGlobal("fetch", network);
  const original = app as unknown as { replaceWithControlReplay(source: ControlReplaySource): Promise<void>;
    replacementApp: PublicTraceApp };
  await original.replaceWithControlReplay(source);
  expect(document.body.classList.contains("p02-controls-open")).toBe(false);
  expect(root.querySelector(".p02-controls-toggle")).toBeNull();
  const boundary = original.replacementApp as unknown as { replayAssetBase: string; replayAssetFetch: typeof fetch };
  const assetUrl = new URL(`assets/${assetDigest}`, boundary.replayAssetBase);
  expect(assetUrl.pathname).toBe(`/__control-replay/${RUN_ID}/replay/assets/${assetDigest}`);
  expect(await (await boundary.replayAssetFetch(assetUrl)).text()).toBe("sealed asset");
  expect(source.asset).toHaveBeenCalledExactlyOnceWith(assetDigest, undefined);
  expect(network).not.toHaveBeenCalled();
  await expect(boundary.replayAssetFetch(new URL(`assets/${assetDigest}`, new URL("../", boundary.replayAssetBase))))
    .rejects.toThrow("no route");
});

it("clears an open live control rail before a new live app starts with its toggle collapsed", () => {
  const root = document.createElement("div"); document.body.append(root);
  app = new PublicTraceApp(root, "live");
  root.querySelector<HTMLButtonElement>(".p02-controls-toggle")!.click();
  app.dispose();
  app = new PublicTraceApp(root, "live");
  expect(document.body.classList.contains("p02-controls-open")).toBe(false);
  expect(root.querySelector(".p02-controls-toggle")!.getAttribute("aria-expanded")).toBe("false");
});
