import { readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

// The checked-in Public Trace v3 fixture the replay test names. The suite serves it
// through page.route so the test is deterministic and independent of whatever the
// host worktree's own validation outputs happen to be.
const FIXTURE_PATH = fileURLToPath(new URL("./fixtures/public-trace-v3.json", import.meta.url));
const FIXTURE_BYTES = readFileSync(FIXTURE_PATH);
const FIXTURE_SIZE_BYTES = statSync(FIXTURE_PATH).size;
const FIXTURE = JSON.parse(FIXTURE_BYTES.toString("utf-8")) as {
  schema_version: string;
  run_id: string;
  suite_id: string;
  case_id: string;
  phase: string;
};

// One-entry catalog that passes fetchWorkspaceTraceCatalog's validation, built from
// the fixture's own identity so the served trace matches the served catalog.
const RELATIVE_PATH = "e2e/fixtures/public-trace-v3.json";
const TRACE_URL = "/workspace-traces/e2e/public-trace-v3.json";

function catalogJson(): string {
  return JSON.stringify({
    schema_version: "aero-bench.viewer-workspace-traces/v1",
    traces: [
      {
        id: "e2e.public-trace-v3.json",
        relative_path: RELATIVE_PATH,
        url: TRACE_URL,
        schema_version: FIXTURE.schema_version,
        run_id: FIXTURE.run_id,
        suite_id: FIXTURE.suite_id,
        case_id: FIXTURE.case_id,
        phase: FIXTURE.phase,
        size_bytes: FIXTURE_SIZE_BYTES,
        loadable: true,
        blocker: null,
      },
    ],
  });
}

async function serveCheckedInTrace(page: Page): Promise<void> {
  await page.route("**/workspace-traces/catalog.json", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "Content-Type": "application/json" },
      body: catalogJson(),
    });
  });
  await page.route(TRACE_URL, async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "Content-Type": "application/json" },
      body: FIXTURE_BYTES,
    });
  });
}

test("live mode opens the city operations console", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("运行控制").or(page.getByText("Run control"))).toBeVisible();
  await expect(page.locator("#city-map canvas")).toBeVisible();
  await expect(page.locator("#workspace-trace-select")).toBeHidden();
});

test("replay loads the checked-in Public Trace v3 and exposes playback controls", async ({ page }) => {
  // Measured in software Chromium at 1440x900: /?view=replay reaches scene-ready at 102.4 s
  // with 943 requests, and the catalog/trace fetches queue behind those city requests, so
  // the integrity label needs a city-page-scale budget, not the 60 s default.
  const REPLAY_LOAD_TIMEOUT_MS = 240_000;
  test.setTimeout(REPLAY_LOAD_TIMEOUT_MS);
  await serveCheckedInTrace(page);
  await page.goto("/?view=replay");
  // With exactly one loadable entry, refreshWorkspaceTraces auto-loads it.
  const integrityLabel = page.getByText("完整性：已密封").or(page.getByText("INTEGRITY: SEALED"));
  await expect(integrityLabel).toBeVisible({ timeout: REPLAY_LOAD_TIMEOUT_MS });
  await expect(page.locator("#workspace-trace-select")).toHaveValue(TRACE_URL);
  await expect(page.locator("input.scrubber")).toBeVisible();
  await expect(page.getByRole("button", { name: /播放|Play|暂停|Pause/ })).toBeVisible();
});

test("city canvas renders and the telemetry HUD is explicitly hidden while no trace data is loaded", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#city-map canvas")).toBeVisible();
  // No trace is loaded and the viewer never invents UAV samples, so the HUD is present
  // but explicitly hidden (root.hidden = true), not visible with fake data.
  const hud = page.locator(".telemetry-hud");
  await expect(hud).toBeAttached();
  await expect(hud).toBeHidden();
  // The camera mode select belongs to the hidden HUD, so it is attached but not
  // interactable without data; the product hides it with the HUD instead of leaving a
  // dead control on screen. Assert its options in the DOM.
  const modeSelect = page.locator("#camera-mode-select");
  await expect(modeSelect).toBeAttached();
  await expect(modeSelect).toBeHidden();
  await expect(modeSelect.locator("option[value='chase']")).toBeAttached();
});

test("invalid trace reports a readable loading error", async ({ page }) => {
  await serveCheckedInTrace(page);
  await page.goto("/?view=replay&trace=missing");
  await expect(page.locator("#city-map canvas")).toBeVisible();
  // The requested ?trace= path is not in the catalog: the viewer shows a readable
  // message and must not auto-load a different trace in its place.
  await expect(
    page.getByText("请求的工作区轨迹不存在：missing").or(page.getByText("Requested workspace trace not found: missing")),
  ).toBeVisible();
});
