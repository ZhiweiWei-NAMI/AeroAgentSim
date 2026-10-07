import { afterEach, expect, it, vi } from "vitest";
vi.mock("./map", () => ({ PublicTraceMap: class {
  isAvailable = true;
  render = vi.fn(); focus = vi.fn(() => true); setFollow = vi.fn();
  refreshCursorLabels = vi.fn(); destroy = vi.fn();
} }));
import { PublicTraceApp } from "./app";
import { ControlClient } from "./run-control";
import { RunSession, type RunSessionState } from "./run-store";
import { RUN_ID } from "./testing/trace-v3-fixture";

let app: PublicTraceApp | undefined;
afterEach(() => { app?.dispose(); document.body.replaceChildren(); });

it("routes an authenticated managed run through reconnect and blocks unsupported registered replay access", async () => {
  const root = document.createElement("div"); document.body.append(root);
  app = new PublicTraceApp(root, "live");
  const fetcher = vi.fn();
  const client = new ControlClient({ baseUrl: "http://127.0.0.1:8769", fetch: fetcher });
  const session = new RunSession(client);
  Object.assign(session.currentState, { selectedRunId: RUN_ID, catalog: {
    schema_version: "aero-bench.control-catalog/v1", suite_id: "suite.unit", suite_sha256: "f".repeat(64),
    runs: [{ run_id: RUN_ID, start_id: "start.unit", case_id: "case.unit", seed: 1,
      launch_site_id: "launch.unit", scenario_digest: "a".repeat(64), matrix_axis_ids: [],
      execution_scope: "formal_benchmark", suite_id: "suite.unit", task_id: "task.unit", world_id: "world.unit" }],
  } });
  const boundary = app as unknown as { controlClient: ControlClient; session: RunSession;
    bootstrap: { operatorToken: string; csrfToken: string }; renderRunControls(state: RunSessionState): void };
  Object.assign(boundary, { controlClient: client, session,
    bootstrap: { operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) } });
  boundary.renderRunControls(session.currentState);
  expect(root.querySelector<HTMLButtonElement>('[data-role="open-registered-replay"]')!.disabled).toBe(true);
  const reconnect = root.querySelector<HTMLButtonElement>('[data-reconnect="reconnect"]')!;
  expect(reconnect).not.toBeNull();
  expect(reconnect.disabled).toBe(false);
  await expect(app.replayRegisteredRun()).rejects.toThrow("managed run requires reconnect");
  expect(fetcher).not.toHaveBeenCalled();
  Object.assign(session.currentState.catalog!.runs[0]!, { start_id: null });
  boundary.renderRunControls(session.currentState);
  expect(root.querySelector<HTMLButtonElement>('[data-role="open-registered-replay"]')!.disabled).toBe(false);
});
