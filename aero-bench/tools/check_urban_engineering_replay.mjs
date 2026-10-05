#!/usr/bin/env node
/** Minimal real-browser check of one exact recorded public workspace trace. */
import { createRequire } from "node:module";
import { open } from "node:fs/promises";

const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const [tracePath, reportPath, origin = "http://127.0.0.1:5197"] = process.argv.slice(2);
if (!tracePath || !reportPath) {
  throw new Error("Usage: node tools/check_urban_engineering_replay.mjs <workspace-public-trace-path> <new-report.json> [local-viewer-origin]");
}
const viewer = new URL(origin);
if (!["127.0.0.1", "localhost", "[::1]"].includes(viewer.hostname)) {
  throw new Error("The engineering replay check requires a local viewer");
}
const reportFile = await open(reportPath, "wx", 0o600);
const report = {
  check: "engineering-recorded-public-replay",
  trace_path: tracePath,
  viewer_origin: viewer.origin,
  status: "failed",
  benchmark_scored: false,
  visual_scoring: "not_run",
  page_errors: [],
};
let browser;
try {
  const response = await fetch(new URL("/workspace-traces/catalog.json", viewer));
  if (!response.ok) throw new Error(`Trace catalog HTTP ${response.status}`);
  const catalog = await response.json();
  const matches = catalog.traces.filter((entry) => entry.relative_path === tracePath);
  if (matches.length !== 1 || !matches[0].loadable) {
    throw new Error("The exact requested public trace is not uniquely loadable");
  }
  const entry = matches[0];
  report.run_id = entry.run_id;
  report.trace_url = entry.url;
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on("pageerror", (error) => report.page_errors.push(error.message));
  await page.goto(new URL("/?view=replay", viewer).href);
  const select = page.locator("#workspace-trace-select");
  await expect(select.locator("option").filter({ hasText: tracePath })).toHaveCount(1, { timeout: 30000 });
  const option = select.locator("option").filter({ hasText: tracePath });
  await select.selectOption(await option.getAttribute("value"));
  await page.getByRole("button", { name: /加载选中轨迹|Load selected trace/ }).click();
  await expect(page.getByText("完整性：已密封").or(page.getByText("INTEGRITY: SEALED"))).toBeVisible({ timeout: 120000 });
  const scrubber = page.locator("input.scrubber");
  await expect(scrubber).toBeEnabled();
  report.recorded_intervals = Number(await scrubber.getAttribute("max"));
  if (report.recorded_intervals < 1) throw new Error("The trace has no playable recorded interval");
  const pause = page.getByRole("button", { name: /(?:暂停|Pause)$/ });
  if (await pause.isVisible()) await pause.dispatchEvent("click");
  await scrubber.focus();
  await scrubber.press("Home");
  await expect(scrubber).toHaveValue("0");
  report.initial_clock = await page.locator(".clock").innerText();
  const play = page.getByRole("button", { name: /(?:播放|Play)$/ });
  await play.dispatchEvent("click");
  await expect.poll(() => scrubber.inputValue(), { timeout: 15000 }).not.toBe("0");
  report.playback_clock = await page.locator(".clock").innerText();
  await pause.dispatchEvent("click");
  await scrubber.focus();
  await scrubber.press("End");
  await expect(scrubber).toHaveValue(String(report.recorded_intervals));
  report.final_clock = await page.locator(".clock").innerText();
  await expect(page.locator(".source-message.error")).toHaveCount(0);
  await expect(page.locator("#city-map canvas")).toBeVisible();
  if (report.page_errors.length > 0) throw new Error("Browser page errors occurred");
  report.status = "passed";
} catch (error) {
  report.error = error instanceof Error ? error.message : String(error);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  await reportFile.writeFile(`${JSON.stringify(report, null, 2)}\n`);
  await reportFile.close();
  console.log(JSON.stringify(report));
}
