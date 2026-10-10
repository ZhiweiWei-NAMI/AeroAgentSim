#!/usr/bin/env node
/** Capture the actual public replay UI; never inject or synthesize flight state. */
import { createRequire } from "node:module";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { parseJsonObjectBytes } from "../frontend/shared/json-bytes.mjs";

const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const [tracePath, outputPath, origin = "http://127.0.0.1:5197", mode = "full"] = process.argv.slice(2);
if (!tracePath || !outputPath || !["full", "final-frame-only", "inspection"].includes(mode)) {
  throw new Error("Usage: node tools/capture_urban_flight_report.mjs <exact-workspace-trace-path> <new-output-dir> [local-viewer-origin] [full|final-frame-only|inspection]");
}
const viewer = new URL(origin);
if (!["127.0.0.1", "localhost", "[::1]"].includes(viewer.hostname)) {
  throw new Error("Report capture requires a local public viewer");
}
const output = resolve(outputPath);
await mkdir(output, { recursive: false });
const report = {
  purpose: "recorded-public-flight-ui-capture",
  trace_path: tracePath,
  capture_mode: mode,
  viewer_url: new URL("/?view=replay", viewer).href,
  status: "failed",
  formal_benchmark_pass: false,
  physics_acceptance: "not_assessed_by_browser_capture",
  visual_scoring: "not_run",
  screenshots: [],
  page_errors: [],
  request_failures: [],
};
let browser;
let context;
try {
  const response = await fetch(new URL("/workspace-traces/catalog.json", viewer));
  if (!response.ok) throw new Error(`Catalog HTTP ${response.status}`);
  const catalog = await response.json();
  const matches = catalog.traces.filter(entry => entry.relative_path === tracePath);
  if (matches.length !== 1 || !matches[0].loadable) throw new Error("Exact trace is not uniquely loadable");
  report.run_id = matches[0].run_id;
  report.trace_url = matches[0].url;
  // Expected screenshots need only real UAV samples and recorded decisions.
  // Parse the full file without joining a V8 document-sized string.
  const recorded = { run_id: null, scene_states: [], events: [] };
  const tickSet = new Set();
  const tickArrays = new Set(["scene_states", "events", "mission_events", "mission_status_history", "network_events", "network_frames", "sensor_frames"]);
  const metadata = parseJsonObjectBytes(await readFile(resolve(tracePath)), {
    keys: new Set(["run_id", "time"]),
    onArrayItem(key, item) {
      if (tickArrays.has(key)) tickSet.add(item.at.tick);
      if (key === "scene_states") recorded.scene_states.push({ at: item.at,
        samples: item.samples.filter(sample => sample.entity_id === "uav.01" || sample.entity_id === "uav.02") });
      if (key === "events" && item.interaction_type === "agent.decision_summary.v1") recorded.events.push(item);
    },
  });
  recorded.run_id = metadata.run_id;
  tickSet.add(metadata.time.tick);
  const replayTicks = [...tickSet].sort((a, b) => a - b);
  if (recorded.run_id !== report.run_id) throw new Error("Local public trace identity differs from catalog");
  const expectedFinal = recorded.scene_states.at(-1)?.samples.find(sample => sample.entity_id === "uav.01");
  if (!expectedFinal) throw new Error("Missing recorded final UAV sample");
  report.expected_final_sample = { at: expectedFinal.at, sample_digest: expectedFinal.sample_digest, enu: expectedFinal.pose.position.enu };
  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: join(output, "video"), size: { width: 1920, height: 1080 } },
  });
  const page = await context.newPage();
  page.on("pageerror", error => report.page_errors.push(error.message));
  page.on("requestfailed", request => report.request_failures.push({ url: request.url(), error: request.failure()?.errorText }));
  await page.addInitScript(() => {
    window.__loadStages = [];
    new MutationObserver(() => {
      const node = document.querySelector(".loading-progress");
      if (!node || node.hidden) return;
      const last = window.__loadStages.at(-1);
      if (last?.stage !== node.dataset.stage) window.__loadStages.push({ stage: node.dataset.stage, text: node.textContent });
    }).observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  });
  await page.goto(report.viewer_url);
  const select = page.locator("#workspace-trace-select");
  await expect(select.locator("option").filter({ hasText: tracePath })).toHaveCount(1, { timeout: 60000 });
  report.catalog_entries = await select.locator("option").evaluateAll(options => options
    .filter(option => option.value !== "").map(option => ({ value: option.value, label: option.textContent, disabled: option.disabled })));
  if (report.catalog_entries.some(entry => entry.disabled || entry.value.includes("/fixtures/"))) throw new Error("Unavailable or fixture trace remains in the source selector");
  if (new Set(report.catalog_entries.map(entry => entry.value)).size !== report.catalog_entries.length) throw new Error("Duplicate source selector entry");
  await select.selectOption(matches[0].url);
  await page.getByRole("button", { name: /加载选中轨迹|Load selected trace/ }).click();
  await expect(page.getByText("完整性：已密封").or(page.getByText("INTEGRITY: SEALED"))).toBeVisible({ timeout: mode === "inspection" ? 600000 : 180000 });
  const scrubber = page.locator("input.scrubber");
  await expect(scrubber).toBeEnabled();
  const max = Number(await scrubber.getAttribute("max"));
  if (max < 1) throw new Error("No recorded playback intervals");
  if (max !== replayTicks.length - 1) throw new Error("Playback cursor differs from recorded timeline ticks");
  const firstIndex = replayTicks.indexOf(recorded.scene_states[0].at.tick);
  await expect(scrubber).toHaveValue(String(firstIndex));
  report.recorded_intervals = max;
  const pause = page.getByRole("button", { name: /(?:暂停|Pause)$/ });
  if (await pause.isVisible()) await pause.dispatchEvent("click");
  await expect(page.locator("#city-map")).toHaveAttribute("data-scene-ready", "true", { timeout: 120000 });
  await expect(page.locator("#city-map")).toHaveAttribute("data-scene-source", "official-mesh-pack");
  await expect(page.locator(".loading-progress")).toHaveAttribute("data-stage", "ready");
  report.loading_stages = await page.evaluate(() => window.__loadStages);
  const hud = page.locator(".telemetry-hud");
  const minimize = hud.locator(".gz-minimize");
  await minimize.click();
  await expect(hud).toHaveClass(/minimized/);
  await expect(minimize).toHaveText("展开");
  await expect(minimize).toBeInViewport();
  report.restore_button_clickable = await minimize.evaluate(button => {
    const box = button.getBoundingClientRect();
    return button.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
  });
  if (!report.restore_button_clickable) throw new Error("Minimized restore button is obscured");
  await page.screenshot({ path: join(output, "00-minimized-restorable.png") });
  await minimize.click();
  await expect(hud).not.toHaveClass(/minimized/);
  await expect(hud.locator("canvas.gz-canvas")).toBeVisible();
  report.hud_restore_passed = true;
  await page.getByRole("button", { name: /首帧|First/ }).click();
  await expect(scrubber).toHaveValue(String(firstIndex));
  const screenshot = async name => {
    await page.screenshot({ path: join(output, name) });
    report.screenshots.push({ file: name, clock: await page.locator(".clock").innerText() });
  };
  await expect(page.locator("#city-map canvas")).toBeVisible();
  await screenshot("01-shanghai-overview.png");
  await page.locator(".entity-select").filter({ hasText: /^uav\.01$/ }).click();
  await page.locator('[role="tab"][data-tab="telemetry"]').click();
  await page.locator("#camera-mode-select").selectOption("chase");
  if (mode === "inspection") {
    report.playback_windows = [];
    for (const [startTick, endTick, speed] of [[1, 12, "1"], [250, 262, "4"], [506, 518, "4"], [100, 550, "32"]]) {
      const startIndex = replayTicks.indexOf(startTick);
      const endIndex = replayTicks.indexOf(endTick);
      if (startIndex < 0 || endIndex < 0) throw new Error("Missing recorded playback test window");
      await scrubber.evaluate((input, index) => { input.value = String(index); input.dispatchEvent(new Event("input", { bubbles: true })); }, startIndex);
      await expect(hud).toBeVisible();
      await page.locator(".speed-select").selectOption(speed);
      await page.evaluate(() => {
        window.__playbackRows = [];
        window.__watchPlayback = true;
        const observe = () => {
          if (!window.__watchPlayback) return;
          const map = document.querySelector("#city-map");
          const hud = document.querySelector(".telemetry-hud");
          window.__playbackRows.push({ tick: Number(map.dataset.sceneTick),
            uavCount: Number(map.dataset.renderedUavCount), visible: !hud.hidden,
            sampleDigest: hud.dataset.sampleDigest });
          requestAnimationFrame(observe);
        };
        requestAnimationFrame(observe);
      });
      await page.getByRole("button", { name: /(?:播放|Play)$/ }).click();
      await expect.poll(async () => Number(await scrubber.inputValue()), { timeout: 30000 }).toBeGreaterThanOrEqual(endIndex);
      await pause.click();
      const rows = await page.evaluate(() => { window.__watchPlayback = false; return window.__playbackRows; });
      if (!rows.length) throw new Error("Playback produced no observed frames");
      for (const row of rows) {
        const expected = recorded.scene_states.find(state => state.at.tick === row.tick)?.samples.find(sample => sample.entity_id === "uav.01");
        if (!row.visible || row.uavCount !== 2 || !expected || row.sampleDigest !== expected.sample_digest) {
          throw new Error(`Playback lost or mismatched a recorded UAV frame: ${JSON.stringify(row)}`);
        }
      }
      report.playback_windows.push({ start_tick: startTick, end_tick: endTick, speed: Number(speed),
        observed_frames: rows.length, observed_ticks: [...new Set(rows.map(row => row.tick))], all_uavs_visible: true });
      await screenshot(`playback-${speed}x-from-${startTick}.png`);
    }
    await page.locator(".speed-select").selectOption("1");
    const phases = ["inspection", "return_goto", "land", "done"];
    report.mission_checkpoints = [];
    for (const phase of phases) {
      const event = recorded.events.find(event => event.agent_id === "uav.policy.01" && event.interaction_type === "agent.decision_summary.v1"
        && event.public_payload.some(item => item.name === "decision_summary" && item.value.includes(`mission_phase=${phase};`)));
      if (!event) throw new Error(`Missing recorded inspection phase ${phase}`);
      const state = recorded.scene_states.find(state => state.at.tick === event.at.tick);
      const index = replayTicks.indexOf(event.at.tick);
      if (index < 0 || !state) throw new Error(`Missing recorded scene at phase ${phase}`);
      const sample = state.samples.find(sample => sample.entity_id === "uav.01");
      await scrubber.evaluate((input, index) => { input.value = String(index); input.dispatchEvent(new Event("input", { bubbles: true })); }, index);
      const digest = sample.sample_digest;
      await expect(page.locator(".inspector .inspector-body")).toContainText(`${digest.slice(0, 8)}…${digest.slice(-8)}`, { timeout: 180000 });
      await screenshot(`phase-${phase}.png`);
      report.mission_checkpoints.push({ phase, at: event.at, sample_digest: digest });
    }
    await page.locator('[role="tab"][data-tab="agent"]').click();
    await expect(page.locator(".agent-list")).toBeVisible();
    await expect(page.locator(".agent-list")).toContainText("mission_phase=done");
    await screenshot("agent-interactions.png");
    await page.locator('[role="tab"][data-tab="telemetry"]').click();
    await expect(page.locator(".telemetry-panel")).not.toContainText("未声明");
    report.agent_interactions_visible = true;
  }
  if (mode === "full") {
    await page.getByRole("button", { name: /(?:播放|Play)$/ }).dispatchEvent("click");
    await expect.poll(() => scrubber.inputValue(), { timeout: 15000 }).not.toBe("0");
    report.playback_clock = await page.locator(".clock").innerText();
    // Record the flight through its existing UI clock, without creating samples.
    await expect.poll(async () => Number(await scrubber.inputValue()), { timeout: 120000 }).toBeGreaterThanOrEqual(Math.ceil(max * 0.6));
    if (await pause.isVisible()) await pause.dispatchEvent("click");
    await screenshot("02-uav-telemetry-and-flight.png");
    await page.getByRole("button", { name: /(?:播放|Play)$/ }).dispatchEvent("click");
    await expect.poll(async () => Number(await scrubber.inputValue()), { timeout: 300000 }).toBe(max);
    if (await pause.isVisible()) await pause.dispatchEvent("click");
  }
  await scrubber.focus();
  await scrubber.press("End");
  await expect(scrubber).toHaveValue(String(max));
  // A changed clock does not mean the indexed SceneState has finished loading.
  const digest = expectedFinal.sample_digest;
  await expect(page.getByText(`${digest.slice(0, 8)}…${digest.slice(-8)}`, { exact: true })).toBeVisible({ timeout: 180000 });
  await screenshot("03-recorded-flight-control.png");
  report.final_clock = await page.locator(".clock").innerText();
  if (mode === "inspection") {
    const second = recorded.scene_states.at(-1).samples.find(sample => sample.entity_id === "uav.02");
    if (!second) throw new Error("Missing recorded second UAV sample");
    await page.locator(".entity-select").filter({ hasText: /^uav\.02$/ }).click();
    await expect(hud).toHaveAttribute("data-entity-id", second.entity_id);
    await expect(hud).toHaveAttribute("data-sample-digest", second.sample_digest);
    report.paused_selection_passed = true;
    report.second_uav_final_sample_digest = second.sample_digest;
    await screenshot("04-second-uav-paused-selection.png");
    await page.locator(".entity-select").filter({ hasText: /^uav\.01$/ }).click();
    await expect(hud).toHaveAttribute("data-sample-digest", expectedFinal.sample_digest);
  }
  report.absent_field_rows = await page.locator(".tab-panel .kv > strong, .tab-panel .status-line > strong").evaluateAll(nodes => nodes
    .filter(node => ["未声明", "undeclared", "—", ""].includes(node.textContent.trim())).map(node => node.parentElement.textContent));
  if (report.absent_field_rows.length) throw new Error("Absent field rows remain in the displayed panel");
  report.visible_text = await page.locator("body").innerText();
  await expect(page.locator(".source-message.error")).toHaveCount(0);
  if (report.page_errors.length) throw new Error("Browser page errors occurred");
  const video = page.video();
  await context.close();
  context = undefined;
  report.video = video ? await video.path() : null;
  report.status = "passed";
} catch (error) {
  report.error = error instanceof Error ? error.message : String(error);
  process.exitCode = 1;
  if (context) {
    const page = context.pages()[0];
    if (page) {
      await page.screenshot({ path: join(output, "failure.png") }).catch(() => {});
      report.visible_text = await page.locator("body").innerText().catch(() => "");
    }
  }
} finally {
  if (context) await context.close();
  if (browser) await browser.close();
  await writeFile(join(output, "capture.json"), JSON.stringify(report, null, 2) + "\n", { flag: "wx" });
  console.log(JSON.stringify({ status: report.status, output, error: report.error, screenshots: report.screenshots, video: report.video }));
}
