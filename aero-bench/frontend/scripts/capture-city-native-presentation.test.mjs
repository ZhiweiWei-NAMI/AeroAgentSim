import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { runInNewContext } from "node:vm";

import {
  assertCleanBrowserEvidence,
  assertHardwareGpuReady,
  assertNativeRequestInventory,
  derivePresentationInventory,
  nativeAssetDigest,
  parseHarnessArguments,
  installNativeRequestTracing,
} from "./capture-city-native-presentation.mjs";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const scenarioPath = resolve(frontendRoot,
  "../validation/platform-plan-20261001/W4-NATIVE/registered-city-inspection-v7/city-presentation/huangpu-native-inspection-v7.json");
const registrationId = "inspection.huangpu.native.v7";

test("reports simultaneous console and transport failures without dropping either", () => {
  const clean = { pageErrors: [], consoleErrors: [], requestFailures: [], httpErrors: [],
    observations: { webglContextLosses: [] } };
  assert.doesNotThrow(() => assertCleanBrowserEvidence(clean));
  assert.throws(() => assertCleanBrowserEvidence({ ...clean, consoleErrors: ["favicon.ico HTTP 404"],
    requestFailures: [{ error: "net::ERR_ABORTED" }] }), error => {
    assert.match(error.message, /favicon.ico HTTP 404/);
    assert.match(error.message, /net::ERR_ABORTED/);
    return true;
  });
  assert.throws(() => assertCleanBrowserEvidence({ ...clean,
    observations: { webglContextLosses: [{ connected: true }] } }), /zero browser, transport and context errors/);
});

test("requires initialized hardware Vulkan without a GPU process crash", () => {
  const auxiliary = { hardwareSupportsVulkan: true, glImplementationParts: "(gl=egl-angle,angle=vulkan)",
    glRenderer: "ANGLE (NVIDIA, Vulkan)", processCrashCount: 0, initializationTime: 0.4 };
  const info = value => ({ gpu: { auxAttributes: value } });
  assert.deepEqual(assertHardwareGpuReady(info(auxiliary)), { renderer: auxiliary.glRenderer,
    implementation: auxiliary.glImplementationParts, initializationTimeSeconds: 0.4, processCrashCount: 0 });
  assert.throws(() => assertHardwareGpuReady(info({ ...auxiliary, hardwareSupportsVulkan: false })),
    /initialize hardware Vulkan/);
  assert.throws(() => assertHardwareGpuReady(info({ ...auxiliary, glRenderer: "SwiftShader" })), /hardware/);
  assert.throws(() => assertHardwareGpuReady(info({ ...auxiliary, glImplementationParts: "angle=opengl" })),
    /angle=vulkan/);
  assert.throws(() => assertHardwareGpuReady(info({ ...auxiliary, processCrashCount: 1 })), /crashed/);
});

test("derives the exact published v7 presentation inventory and recorded known building", async () => {
  const scenario = JSON.parse(await readFile(scenarioPath, "utf8"));
  const inventory = derivePresentationInventory(scenario);
  assert.equal(inventory.presentationAssets.length, 418);
  assert.equal(inventory.expectedDigests.length, 418);
  assert.equal(inventory.totalBytes, 160_364_476);
  assert.equal(inventory.knownBuilding.building_id, "building.way.372180501.component.0");
  assert.equal(inventory.knownEntity.entity_id, "static.building.way.372180501.component.0");
});

test("rejects an embedded replay policy or changed scenario identity", async () => {
  const scenario = JSON.parse(await readFile(scenarioPath, "utf8"));
  assert.throws(() => derivePresentationInventory({ ...scenario, replay_mode: "embedded" }),
    /embedded/);
  assert.throws(() => derivePresentationInventory({ ...scenario, scenario_digest: "0".repeat(64) }),
    /000000/);
});

test("request tracing observes original promises and reader EOF without cancelling bodies", async () => {
  let reads = 0, cancels = 0;
  class Reader {
    read() { return Promise.resolve(reads++ === 0
      ? { done: false, value: new Uint8Array(7) } : { done: true }); }
    cancel() { cancels++; return Promise.resolve(); }
    releaseLock() {}
  }
  class Stream { getReader() { return new Reader(); } }
  class TracedAbortController extends AbortController {}
  const body = new Stream();
  const promise = Promise.resolve({ body, status: 200, headers: new Headers({ "content-length": "7" }) });
  const window = { fetch: () => promise };
  runInNewContext(`(${installNativeRequestTracing.toString()})()`, {
    window, location: { href: "http://127.0.0.1:5402/" }, URL, Request,
    ReadableStream: Stream, ReadableStreamDefaultReader: Reader, AbortController: TracedAbortController,
    performance,
  });
  assert.equal(window.fetch("/authoring/v1/native-scenes/native.v6/scene"), promise);
  const response = await promise;
  const reader = response.body.getReader();
  await reader.read();
  await reader.read();
  reader.releaseLock();
  assert.equal(reads, 2);
  assert.equal(cancels, 0);
  const end = window.__aeroNativeRequestEvents.find(event => event.kind === "body-end");
  assert.equal(end.total, 7);
  assert.equal(end.reads, 2);
  assert.equal(window.__aeroNativeRequestEvents.filter(event => event.kind === "reader-release").length, 1);
  assert.equal(window.__aeroNativeRequestEvents.filter(event => /abort|cancel|error/.test(event.kind)).length, 0);
});

test("requires an explicit loopback origin and registration", () => {
  assert.deepEqual(parseHarnessArguments([
    "http://127.0.0.1:5393",
    "../validation/codex-takeover-20261001/N/native-city",
    registrationId,
  ]), {
    origin: "http://127.0.0.1:5393",
    // CLI paths resolve against the caller's working directory.
    outputDir: resolve("../validation/codex-takeover-20261001/N/native-city"),
    registrationId,
    timeoutMs: 1_200_000,
  });
  assert.throws(() => parseHarnessArguments(["https://example.com", "out", registrationId]),
    /loopback HTTP/);
  assert.throws(() => parseHarnessArguments(["http://127.0.0.1:5393", "out", ""]),
    /explicit native-city registration ID/);
});

test("accepts one digest-addressed browser response per declared presentation asset", async () => {
  const scenario = JSON.parse(await readFile(scenarioPath, "utf8"));
  const inventory = derivePresentationInventory(scenario);
  const scenePath = `/authoring/v1/native-scenes/${registrationId}/scene`;
  const assetPaths = inventory.expectedDigests.map(digest =>
    `/authoring/v1/native-scenes/${registrationId}/assets/${digest}`);
  const records = {
    origin: "http://127.0.0.1:5393",
    requests: [
      { method: "GET", protocol: "http:", origin: "http://127.0.0.1:5393",
        pathname: scenePath, resourceType: "fetch" },
      ...assetPaths.map(pathname => ({ method: "GET", protocol: "http:",
        origin: "http://127.0.0.1:5393", pathname, resourceType: "fetch" })),
      { method: "GET", protocol: "blob:", origin: "http://127.0.0.1:5393",
        pathname: "http://127.0.0.1:5393/embedded-texture", resourceType: "image" },
    ],
    responses: [
      { method: "GET", protocol: "http:", origin: "http://127.0.0.1:5393",
        pathname: scenePath, status: 200, resourceType: "fetch" },
      ...assetPaths.map(pathname => ({ method: "GET", protocol: "http:",
        origin: "http://127.0.0.1:5393", pathname, status: 200, resourceType: "fetch" })),
    ],
  };
  const result = assertNativeRequestInventory(records, registrationId, inventory.expectedDigests);
  assert.equal(result.assetResponseCount, 418);
  assert.equal(result.foreignRequestCount, 0);
  assert.equal(nativeAssetDigest(assetPaths[0], registrationId), inventory.expectedDigests[0]);
  assert.equal(nativeAssetDigest("/models/foreign.glb", registrationId), null);
  assert.throws(() => assertNativeRequestInventory({ ...records,
    requests: [...records.requests, { method: "GET", protocol: "http:",
      origin: "http://127.0.0.1:5393", pathname: "/models/foreign.glb", resourceType: "fetch" }] },
  registrationId, inventory.expectedDigests), /foreign preview assets/);
});
