import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./map", () => ({ PublicTraceMap: class {
  isAvailable = true;
  render = vi.fn(); focus = vi.fn(() => true); setFollow = vi.fn();
  refreshCursorLabels = vi.fn(); destroy = vi.fn();
} }));

import { PublicTraceApp } from "./app";
import type { ReplayState } from "./state/replay";
import type { RunSessionState } from "./run-store";
import type { SceneState } from "./trace";
import { sceneState, stateSample } from "./testing/trace-v3-fixture";
import { publicTrace } from "./testing/trace-v3-fixture";
import { parsePublicTrace, type PublicTrace } from "./trace";

let app: PublicTraceApp | null = null;
afterEach(() => { app?.dispose(); document.body.replaceChildren(); });

describe("P02 live playback entry", () => {
  it("refreshes the live mode badge when the authenticated session connects", () => {
    const root = document.createElement("div"); document.body.append(root);
    app = new PublicTraceApp(root, "live");
    const state = app as unknown as {
      session: { currentState: RunSessionState; dispose: () => void };
      renderSession(session: RunSessionState): void;
    };
    const connected: RunSessionState = {
      connection: "connected", catalog: null, catalogError: null, selectedRunId: null,
      discoveredStartId: null, hasRunCredentials: false, snapshot: null, scenario: null,
      runtimeControl: null, transitions: [], sceneStates: [], latestTick: null,
      events: [], trafficLightFrame: null, sessionError: null, pendingControl: null,
    };
    state.session = { dispose: vi.fn(), currentState: connected };
    expect(root.querySelector("#mode-pill")?.textContent).toBe("实时 · 未连接");
    state.renderSession(connected);
    expect(root.querySelector("#mode-pill")?.textContent).toBe("实时运行");
    state.renderSession({ ...connected, connection: "closed" });
    expect(root.querySelector("#mode-pill")?.textContent).toBe("实时 · 未连接");
  });
  it("exposes the real playback action in the visible header when the footer is hidden", () => {
    const root = document.createElement("div"); document.body.append(root);
    app = new PublicTraceApp(root, "live");
    const state = app as unknown as {
      replay: ReplayState;
      session: { currentState: RunSessionState; dispose: () => void };
      renderTimeline(): void;
    };
    const states = [sceneState(1, ["uav.test"], [stateSample(1, "uav.test")])] as unknown as SceneState[];
    state.session = { dispose: vi.fn(), currentState: {
      connection: "connected", catalog: null, catalogError: null, selectedRunId: null,
      discoveredStartId: null, hasRunCredentials: false, snapshot: null, scenario: null,
      runtimeControl: null, transitions: [], sceneStates: states, latestTick: 1,
      events: [], trafficLightFrame: null, sessionError: null, pendingControl: null,
    } };
    state.replay.setSceneStates(states); state.renderTimeline();
    const button = root.querySelector<HTMLButtonElement>('.topbar [data-role="p02-live-play"]')!;
    expect(button).not.toBeNull();
    expect(button.disabled).toBe(false);
    expect(root.querySelector('.timeline [data-role="p02-live-play"]')).toBeNull();
    button.click();
    expect(state.replay.isPlaying()).toBe(true);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    state.renderTimeline();
    expect(root.querySelectorAll('[data-role="p02-live-play"]')).toHaveLength(1);
  });
  it("also exposes playback for the sealed replay view whose footer is hidden", () => {
    const root = document.createElement("div"); document.body.append(root);
    app = new PublicTraceApp(root, "replay");
    const state = app as unknown as { trace: PublicTrace; replay: ReplayState; renderTimeline(): void };
    state.trace = parsePublicTrace(publicTrace()); state.replay.setTrace(state.trace);
    state.renderTimeline();
    const button = root.querySelector<HTMLButtonElement>('.topbar [data-role="p02-live-play"]')!;
    expect(button.disabled).toBe(false);
    button.click(); expect(state.replay.isPlaying()).toBe(true);
  });
});
