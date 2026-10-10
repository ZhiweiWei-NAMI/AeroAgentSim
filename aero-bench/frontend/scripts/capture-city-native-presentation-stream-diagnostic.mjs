/** Compare real native responses with the current bounded reader; no acceptance gate is bypassed. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import ts from "typescript";
import { installNativeRequestTracing, verifyFrozenBuild } from "./capture-city-native-presentation.mjs";

const origin = process.argv[2], output = resolve(process.argv[3]);
assert(process.argv[4] === undefined || process.argv[4] === "--confirmation");
const confirmation = process.argv[4] === "--confirmation";
assert.equal(new URL(origin).hostname, "127.0.0.1");
await mkdir(output, { recursive: true });
const source = await readFile(new URL("../src/verified-bytes.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.ESNext } }).outputText.replace(/^export /gm, "");
const report = { schemaVersion: "aero-bench.native-stream-diagnostic/v1", startedAt: new Date().toISOString(),
  scope: "diagnostic-only-not-native-presentation-acceptance", origin,
  readerSourceSha256: createHash("sha256").update(source).digest("hex"),
  frozenBuild: await verifyFrozenBuild(origin), trials: [], failures: [], finished: [] };
assert.equal(report.readerSourceSha256, report.frozenBuild.sourceHashes["src/verified-bytes.ts"],
  "Diagnostic reader must match the frozen application build");
// This is a protocol-only test: no map, graphics context, screenshot, or GPU work.
const browser = await chromium.launch({ channel: "chromium", headless: true,
  args: ["--disable-gpu", "--disable-software-rasterizer"] });
try {
  const page = await browser.newPage();
  await page.addInitScript(installNativeRequestTracing);
  page.on("requestfailed", request => report.failures.push({ url: request.url(), error: request.failure()?.errorText }));
  page.on("requestfinished", request => {
    if (request.url().includes("/authoring/v1/native-scenes/")) report.finished.push(request.url());
  });
  await page.goto(`${origin}/capture-build-receipt.json`);
  const paths = [
    ["scene", "b3f601f7b9ec49d4b493c10528303d4a9ddb22ca63ac827adb30803310fc3cfb", 3_550_662],
    ["assets/89ed7bfdb48d8a5d6628d4b5832725f28648ebbe16ce2870b53b494d17524702",
      "89ed7bfdb48d8a5d6628d4b5832725f28648ebbe16ce2870b53b494d17524702", 2_728_239],
    ["assets/779d65f408873b0272a77e29e416382f92600680cef42016708fe34a311dada1",
      "779d65f408873b0272a77e29e416382f92600680cef42016708fe34a311dada1", 55_711_972],
  ];
  const variants = confirmation
    ? Array.from({ length: 5 }, () => ["bounded", "bounded-pipe", "array-buffer"]).flat()
    : ["bounded", "bounded-retain-response", "bounded-retain-reader", "bounded-no-release",
      "bounded-closed-release", "bounded-task-release", "bounded-pipe", "array-buffer"];
  for (const variant of variants) {
    for (const [path, expectedSha256, expectedBytes] of paths) {
      const failureStart = report.failures.length, finishedStart = report.finished.length;
      const result = await page.evaluate(async ({ compiled, variant, path, expectedBytes }) => {
        window.__heldResponses = []; window.__heldReaders = [];
        const releases = {
          "bounded-retain-reader": "window.__heldReaders.push(reader);",
          "bounded-no-release": "",
          "bounded-closed-release": "await reader.closed; reader.releaseLock();",
          "bounded-task-release": "await new Promise(resolve => setTimeout(resolve, 0)); reader.releaseLock();",
        };
        let script = Object.hasOwn(releases, variant)
          ? compiled.replace("reader.releaseLock();", releases[variant]) : compiled;
        if (variant === "bounded-pipe") {
          const index = compiled.indexOf("const reader = response.body.getReader();");
          if (index < 0) throw new Error("Diagnostic bounded-pipe source replacement is missing");
          // Keep the original strict preflight; compare only the byte-consumer mechanism.
          script = compiled.slice(0, index) + `
            let length = 0;
            const total = expectedBytes ?? (header !== null && !response.headers.get("Content-Encoding")
              ? Number(header) : undefined);
            onProgress?.(0, total);
            const bounded = response.body.pipeThrough(new TransformStream({
              transform(value, controller) {
                assertNotAborted(signal);
                length += value.byteLength;
                if (length > maxBytes) throw new Error(label + " exceeds the " + maxBytes + "-byte limit");
                onProgress?.(length, total);
                controller.enqueue(value);
              }
            }), { signal });
            const bytes = await new Response(bounded).arrayBuffer();
            assertNotAborted(signal);
            if (total !== undefined && length !== total) throw new Error(label + " byte length differs");
            if (expectedBytes !== undefined && length !== expectedBytes) throw new Error(label + " size differs");
            return bytes;
          }`;
        }
        const read = new Function(`${script}; return readBoundedResponse;`)();
        const url = `/authoring/v1/native-scenes/inspection.huangpu.native.v6/${path}`;
        const signal = new AbortController().signal;
        const response = await fetch(url, { signal, redirect: "error" });
        if (response.status !== 200) throw new Error(`Native diagnostic HTTP ${response.status}`);
        if (variant === "bounded-retain-response") window.__heldResponses.push(response);
        const bytes = variant === "array-buffer" ? await response.arrayBuffer()
          : await read(response, expectedBytes, "native diagnostic", signal, expectedBytes);
        const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
          byte => byte.toString(16).padStart(2, "0")).join("");
        return { url, sizeBytes: bytes.byteLength, sha256 };
      }, { compiled, variant, path, expectedBytes });
      assert.equal(result.sizeBytes, expectedBytes); assert.equal(result.sha256, expectedSha256);
      await page.waitForTimeout(100);
      report.trials.push({ variant, ...result, requestFailures: report.failures.slice(failureStart),
        requestFinishedCount: report.finished.length - finishedStart });
    }
  }
  report.events = await page.evaluate(() => window.__aeroNativeRequestEvents);
} catch (error) {
  report.failure = String(error);
  process.exitCode = 1;
} finally {
  await browser.close();
  report.completedAt = new Date().toISOString();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
  process.stdout.write(JSON.stringify({ trials: report.trials.length, failedRequests: report.failures,
    finishedRequests: report.finished.length }, null, 2) + "\n");
}
