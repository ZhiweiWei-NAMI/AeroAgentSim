/**
 * Browser half of the native-reference authoring-to-replay acceptance flow.
 *
 * Configuration and credentials arrive over stdin. Stdout is a small JSON-line
 * protocol containing public IDs and observations only. Bootstrap credentials
 * are never accepted on the command line, written to evidence, or included in
 * screenshots.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import readline from "node:readline";
import { chromium } from "playwright";

const GPU_ARGS = [
  "--enable-gpu",
  "--use-angle=vulkan",
  "--enable-features=Vulkan",
  "--disable-vulkan-surface",
  "--disable-software-rasterizer",
  "--ignore-gpu-blocklist",
];
const SHA256 = /^[0-9a-f]{64}$/;
const TOKEN = /^[0-9a-f]{64}$/;
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
const messages = input[Symbol.asyncIterator]();
let secrets = [];

function redact(value) {
  let text = value instanceof Error ? value.message : String(value);
  for (const secret of secrets) text = text.replaceAll(secret, "[redacted]");
  return text;
}

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

async function receive(type) {
  const item = await messages.next();
  if (item.done) throw new Error(`stdin closed before ${type}`);
  let value;
  try {
    value = JSON.parse(item.value);
  } catch {
    throw new Error(`stdin ${type} message is not JSON`);
  }
  if (value === null || typeof value !== "object" || Array.isArray(value) || value.type !== type) {
    throw new Error(`stdin message is not ${type}`);
  }
  return value;
}

function digest(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

function countChecks(checks) {
  return {
    passed: checks.filter(item => item.status === "passed").length,
    failed: checks.filter(item => item.status === "failed").length,
    total: checks.length,
  };
}

const init = await receive("init");
const originUrl = new URL(init.origin);
if (originUrl.protocol !== "http:" || originUrl.hostname !== "127.0.0.1" || originUrl.pathname !== "/") {
  throw new Error("init origin must be a loopback HTTP origin");
}
const origin = originUrl.origin;
const output = resolve(String(init.outputDir));
const studioTimeoutMs = Number(init.studioTimeoutMs ?? 900_000);
if (!Number.isSafeInteger(studioTimeoutMs) || studioTimeoutMs < 30_000) {
  throw new Error("init studioTimeoutMs is invalid");
}
const workspaceStorageKey = String(init.workspaceStorageKey ?? "");
if (!/^aero-bench\.city-workspace\.v[1-9][0-9]*$/.test(workspaceStorageKey)) {
  throw new Error("init workspaceStorageKey is invalid");
}
const expectedNativeMeshStatus = String(init.expectedNativeMeshStatus ?? "");
if (!["not_declared", "loaded_verified"].includes(expectedNativeMeshStatus)) {
  throw new Error("init expectedNativeMeshStatus is invalid");
}
await mkdir(output, { recursive: true });

const report = {
  schema_version: "aero-bench.city-reference-browser-observations/v1",
  status: "running",
  checks: [],
  observations: {},
  screenshots: [],
  page_errors: [],
};
let browser;
let context;
let page;
let credentialsConsumed = false;
let requestStage = "studio";
const requestStages = new WeakMap();
const controlResponses = [];

async function record() {
  report.check_counts = countChecks(report.checks);
  await writeFile(resolve(output, "browser-observations.json"), `${JSON.stringify(report, null, 2)}\n`, {
    encoding: "utf8", mode: 0o600,
  });
}

async function check(name, action) {
  const started = performance.now();
  try {
    const result = await action();
    report.checks.push({ name, status: "passed", duration_ms: Math.round(performance.now() - started) });
    return result;
  } catch (error) {
    report.checks.push({ name, status: "failed", duration_ms: Math.round(performance.now() - started),
      error: redact(error) });
    throw error;
  }
}

async function screenshot(name) {
  if (credentialsConsumed) {
    const passwordValues = await page.locator('input[type="password"]').evaluateAll(inputs =>
      inputs.map(input => input.value));
    assert(passwordValues.every(value => value === ""), "credential fields were not cleared before screenshot");
  }
  await page.screenshot({ path: resolve(output, name), animations: "disabled", fullPage: true });
  report.screenshots.push(name);
}

async function rendererIdentity() {
  return page.locator("#studio-map canvas, #city-map canvas").first().evaluate(canvas => {
    const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
    if (gl === null) return "unavailable";
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    return String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
  });
}

try {
  browser = await chromium.launch({ channel: "chromium", headless: true, args: GPU_ARGS });
  context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
  });
  page = await context.newPage();
  page.on("pageerror", error => report.page_errors.push(redact(error)));
  page.on("request", request => requestStages.set(request, requestStage));
  page.on("response", response => {
    const request = response.request();
    if (requestStages.get(request) !== "replay") return;
    const url = new URL(response.url());
    if (!/^\/v1\/runs\/[0-9a-f]{64}\/(?:public\/(?:trace|replay-manifest)|assets\/[0-9a-f]{64})$/.test(url.pathname)) return;
    controlResponses.push({ method: request.method(), path: url.pathname, status: response.status() });
  });

  let registrationId;
  let expectedDraft;
  let storedDraft;
  let storageSha256;
  let nativeMeshStatus;
  const studioRenderer = await check("load explicit registered reference through the compile panel", async () => {
    await page.goto(`${origin}/city-studio.html?tab=compile`, {
      waitUntil: "domcontentloaded", timeout: 60_000,
    });
    const selection = page.getByLabel("原生场景注册", { exact: true });
    await selection.waitFor({ state: "visible", timeout: studioTimeoutMs });
    await page.waitForFunction(() => {
      const node = document.querySelector('select[aria-label="原生场景注册"]');
      return node instanceof HTMLSelectElement && !node.disabled && node.value.length > 0;
    }, undefined, { timeout: studioTimeoutMs });
    registrationId = await selection.inputValue();
    assert.match(registrationId, /^[a-z][a-z0-9_.-]*$/);
    expectedDraft = await page.evaluate(async id => {
      const response = await fetch("/authoring/v1/native-scenes", { redirect: "error" });
      if (!response.ok) throw new Error(`native catalog HTTP ${response.status}`);
      const catalog = await response.json();
      const registration = catalog.registrations.find(item => item.registration_id === id);
      if (registration === undefined) throw new Error("selected registration is absent from catalog");
      return registration.reference_draft;
    }, registrationId);
    await page.getByRole("button", { name: "载入所选注册的参考草稿", exact: true }).click();
    const referenceSuccess = page.locator(".studio-note").filter({ hasText: `已显式载入 ${registrationId}` });
    const referenceError = page.locator(".studio-note").filter({ hasText: "参考草稿载入失败" });
    const referenceOutcome = await Promise.race([
      referenceSuccess.waitFor({ timeout: studioTimeoutMs }).then(async () => ({
        kind: "loaded", detail: (await referenceSuccess.textContent())?.trim() ?? "",
      })),
      referenceError.waitFor({ timeout: studioTimeoutMs }).then(async () => ({
          kind: "failed", detail: (await referenceError.textContent())?.trim() ?? "reference selection failed",
        })),
    ]);
    if (referenceOutcome.kind === "failed") throw new Error(referenceOutcome.detail);
    const map = page.locator("#studio-map");
    assert.equal(await map.getAttribute("data-render-backend"), "hardware");
    if (referenceOutcome.detail.includes("该注册未发布三维网格")) {
      const status = page.locator('#studio-preview-state[data-state="warning"]');
      await status.waitFor({ timeout: 30_000 });
      assert.equal((await status.textContent())?.trim(), "原生参考已校验；未发布三维网格");
      assert.notEqual(await map.getAttribute("data-scene-ready"), "true");
      nativeMeshStatus = "not_declared";
    } else {
      await page.locator('#studio-preview-state[data-state="ready"]').waitFor({ timeout: studioTimeoutMs });
      await page.locator('#studio-map[data-scene-ready="true"]').waitFor({ timeout: studioTimeoutMs });
      nativeMeshStatus = "loaded_verified";
    }
    assert.equal(nativeMeshStatus, expectedNativeMeshStatus,
      `native mesh state differs from explicit expectation ${expectedNativeMeshStatus}`);
    const renderer = await rendererIdentity();
    assert(!/swiftshader|llvmpipe|software/i.test(renderer), `software renderer reported: ${renderer}`);
    await screenshot("01-reference-loaded.png");
    return renderer;
  });

  await check("save and reload the exact reference draft", async () => {
    await page.locator("#studio-save").click();
    const status = page.locator('#studio-save-status[data-state="saved"]');
    await status.waitFor({ timeout: 30_000 });
    assert.equal((await status.textContent())?.trim(), "已保存到本地浏览器");
    const raw = await page.evaluate(key => localStorage.getItem(key), workspaceStorageKey);
    assert.notEqual(raw, null, "reference draft was not persisted");
    storedDraft = JSON.parse(raw);
    assert.deepEqual(storedDraft, expectedDraft, "saved draft differs from the registration reference_draft");
    storageSha256 = digest(raw);
    await page.reload({ waitUntil: "domcontentloaded", timeout: 60_000 });
    const reloadedStatus = page.locator('#studio-save-status[data-state="saved"]');
    await reloadedStatus.waitFor({ timeout: studioTimeoutMs });
    assert.equal((await reloadedStatus.textContent())?.trim(), "本地草稿已加载");
    const reloadedRaw = await page.evaluate(key => localStorage.getItem(key), workspaceStorageKey);
    assert.equal(digest(reloadedRaw ?? ""), storageSha256, "persisted draft changed across reload");
    assert.equal(await page.locator("#studio-name").inputValue(), expectedDraft.name);
  });

  let compilationId;
  let runId;
  await check("compile the reloaded draft through the real authoring API", async () => {
    const selection = page.getByLabel("原生场景注册", { exact: true });
    await page.waitForFunction(() => {
      const node = document.querySelector('select[aria-label="原生场景注册"]');
      return node instanceof HTMLSelectElement && !node.disabled && node.value.length > 0;
    }, undefined, { timeout: studioTimeoutMs });
    assert.equal(await selection.inputValue(), registrationId);
    const responsePromise = page.waitForResponse(response => {
      const url = new URL(response.url());
      return response.request().method() === "POST" && url.pathname === "/authoring/v1/compilations";
    }, { timeout: studioTimeoutMs });
    await page.getByRole("button", { name: "编译当前草稿", exact: true }).click();
    const response = await responsePromise;
    assert.equal(response.status(), 201, `compile HTTP ${response.status()}`);
    const compiled = page.getByRole("heading", {
      name: "编译完成（尚未执行、尚未验证）", exact: true,
    }).locator("..");
    await compiled.waitFor({ timeout: studioTimeoutMs });
    const rows = await compiled.locator(".studio-row").evaluateAll(nodes => nodes.map(node => ({
      key: node.querySelector("strong")?.textContent?.trim() ?? "",
      value: node.querySelector("span")?.textContent?.trim() ?? "",
    })));
    compilationId = rows.find(row => row.key === "编译 ID")?.value;
    const runs = rows.filter(row => row.key === "已解析 Run ID");
    assert.match(compilationId ?? "", SHA256);
    assert.equal(runs.length, 1, "reference compilation did not resolve exactly one run");
    runId = runs[0].value.split(" · ", 1)[0];
    assert.match(runId, SHA256);
    await compiled.getByRole("button", { name: "运行", exact: true }).click();
    await page.locator('[data-role="compiled-handoff"]').waitFor({ timeout: 30_000 });
  });

  report.observations = {
    registration_id: registrationId,
    reference_scene_path: storedDraft.scenePath,
    saved_draft_sha256: storageSha256,
    compilation_id: compilationId,
    run_id: runId,
    studio_render_backend: "hardware",
    studio_renderer: studioRenderer,
    native_mesh_status: nativeMeshStatus,
    workspace_storage_key: workspaceStorageKey,
  };
  emit({ type: "compiled", compilationId, registrationId, runId, savedDraftSha256: storageSha256,
    renderer: studioRenderer, nativeMeshStatus });

  const control = await receive("control-ready");
  const controlUrl = new URL(control.baseUrl);
  if (controlUrl.protocol !== "http:" || controlUrl.hostname !== "127.0.0.1" || controlUrl.pathname !== "/") {
    throw new Error("control-ready baseUrl must be a loopback HTTP origin");
  }
  if (control.runId !== runId || !TOKEN.test(control.bootstrapToken)
    || !TOKEN.test(control.csrfToken) || control.bootstrapToken === control.csrfToken) {
    throw new Error("control-ready identity or credentials are invalid");
  }
  const runTimeoutMs = Number(control.runTimeoutMs);
  if (!Number.isSafeInteger(runTimeoutMs) || runTimeoutMs < 60_000) {
    throw new Error("control-ready runTimeoutMs is invalid");
  }
  secrets = [control.bootstrapToken, control.csrfToken];

  await check("open the handoff and start the compiled run through the Control UI", async () => {
    const link = page.locator('[data-role="compiled-handoff"]').getByRole("link", {
      name: "打开运行控制台", exact: true,
    });
    await Promise.all([
      page.waitForURL(url => url.origin === origin && url.pathname === "/", { timeout: 60_000 }),
      link.click(),
    ]);
    await page.getByLabel("控制服务地址", { exact: true }).fill(controlUrl.origin);
    await page.getByLabel("引导操作员令牌", { exact: true }).fill(control.bootstrapToken);
    await page.getByLabel("引导 CSRF 令牌", { exact: true }).fill(control.csrfToken);
    const catalogResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return response.request().method() === "GET" && url.origin === controlUrl.origin && url.pathname === "/v1/catalog";
    }, { timeout: 120_000 });
    await page.getByRole("button", { name: "加载运行目录", exact: true }).click();
    assert.equal((await catalogResponse).status(), 200);
    credentialsConsumed = true;
    assert.equal(await page.getByLabel("引导操作员令牌", { exact: true }).inputValue(), "");
    assert.equal(await page.getByLabel("引导 CSRF 令牌", { exact: true }).inputValue(), "");
    const select = page.locator("select.run-select");
    await select.locator(`option[value="${runId}"]`).waitFor({ state: "attached", timeout: 120_000 });
    await select.selectOption(runId);
    assert.equal(await select.inputValue(), runId);
    const startResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return response.request().method() === "POST" && url.origin === controlUrl.origin && url.pathname === "/v1/runs";
    }, { timeout: 120_000 });
    await page.getByRole("button", { name: "启动运行", exact: true }).click();
    assert.equal((await startResponse).status(), 202);
  });
  emit({ type: "started", compilationId, registrationId, runId });

  let terminalPhase;
  await check("wait for the actual run and verifier to reach a terminal phase", async () => {
    const replay = page.locator('[data-role="load-control-replay"]');
    await replay.waitFor({ timeout: runTimeoutMs });
    const phase = page.locator(".run-meta > .pill[title]").filter({ hasNot: page.locator("[hidden]") });
    terminalPhase = await phase.first().getAttribute("title");
    assert(["completed", "cancelled", "error"].includes(terminalPhase), `unexpected terminal phase ${terminalPhase}`);
    assert.equal(await page.getByLabel("引导操作员令牌", { exact: true }).inputValue(), "");
    assert.equal(await page.getByLabel("引导 CSRF 令牌", { exact: true }).inputValue(), "");
    await screenshot("02-terminal-control.png");
  });

  let replayPhase;
  let verdict;
  let coverage;
  await check("load and verify the fresh sealed replay through authenticated Control routes", async () => {
    requestStage = "replay";
    await page.locator('[data-role="load-control-replay"]').click();
    await page.locator('#mode-pill').waitFor({ timeout: runTimeoutMs });
    await page.locator('.run-meta > .pill[title="verified"]').waitFor({ timeout: runTimeoutMs });
    replayPhase = "verified";
    const integrity = page.locator(".run-meta > .pill").nth(3);
    assert(await integrity.isVisible(), "sealed replay integrity indicator is hidden");
    assert((await integrity.getAttribute("class"))?.includes("tone-ok"), "sealed replay integrity did not pass");
    await page.getByRole("tab", { name: "证据", exact: true }).click();
    const verdictChip = page.locator(".verdict-chip.tone-ok");
    await verdictChip.waitFor({ timeout: 120_000 });
    const coverageChip = page.locator(".verdict-row .chip-ok");
    await coverageChip.waitFor({ timeout: 120_000 });
    verdict = (await verdictChip.textContent())?.trim();
    coverage = (await coverageChip.textContent())?.trim();
    assert.equal(verdict, "通过");
    assert.equal(coverage, "覆盖完整");
    assert.equal(await page.locator(".source-message.error:visible").count(), 0);
    await screenshot("03-sealed-verified-replay.png");
  });

  await check("keep credentials out of persisted browser state and evidence", async () => {
    const persisted = await page.evaluate(() => JSON.stringify({
      local: { ...localStorage }, session: { ...sessionStorage }, url: location.href,
    }));
    for (const secret of secrets) assert(!persisted.includes(secret), "a Control credential reached browser storage or URL");
    assert.equal(await page.locator('input[type="password"]').evaluateAll(inputs =>
      inputs.some(input => input.value !== "")), false);
    assert.equal(report.page_errors.length, 0, `page errors: ${report.page_errors.join("; ")}`);
  });

  const replayResponses = controlResponses.filter(item => item.status === 200);
  const traceResponses = replayResponses.filter(item => item.path.endsWith("/public/trace"));
  const manifestResponses = replayResponses.filter(item => item.path.endsWith("/public/replay-manifest"));
  const assetDigests = [...new Set(replayResponses.flatMap(item => {
    const match = /\/assets\/([0-9a-f]{64})$/.exec(item.path);
    return match === null ? [] : [match[1]];
  }))].sort();
  assert(traceResponses.length > 0, "browser did not read the sealed public trace");
  assert(manifestResponses.length > 0, "browser did not read the sealed replay manifest");

  report.status = "passed";
  report.observations = {
    ...report.observations,
    terminal_phase: terminalPhase,
    replay_phase: replayPhase,
    verifier_verdict: verdict,
    verifier_coverage: coverage,
    replay_trace_response_count: traceResponses.length,
    replay_manifest_response_count: manifestResponses.length,
    replay_asset_digests: assetDigests,
    credentials_persisted: false,
  };
  await record();
  await browser.close();
  browser = undefined;
  secrets = [];
  emit({ type: "finished", compilationId, registrationId, runId, terminalPhase, replayPhase,
    verdict, coverage, replayTraceResponses: traceResponses.length,
    replayManifestResponses: manifestResponses.length, replayAssetDigests: assetDigests,
    checkCounts: countChecks(report.checks), renderer: studioRenderer, nativeMeshStatus });
} catch (error) {
  report.status = "failed";
  report.failure = redact(error);
  await record().catch(() => {});
  await browser?.close().catch(() => {});
  secrets = [];
  emit({ type: "failed", error: report.failure, checkCounts: countChecks(report.checks) });
  process.exitCode = 1;
} finally {
  input.close();
}
