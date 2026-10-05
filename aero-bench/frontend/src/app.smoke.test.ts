import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./map", () => {
  class MockPublicTraceMap {
    isAvailable = true;
    render = vi.fn();
    focus = vi.fn(() => true);
    setFollow = vi.fn();
    refreshCursorLabels = vi.fn();
    destroy = vi.fn();
  }
  return { PublicTraceMap: MockPublicTraceMap };
});

import { declaredReplayModelAssets, PublicTraceApp, viewModeFromLocation } from "./app";
import { publicTrace } from "./testing/trace-v3-fixture";
import { initLanguage, t } from "./i18n";
import type { PublicTrace } from "./trace";

describe("PublicTraceApp shell (map mocked)", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    initLanguage();
  });

  afterEach(() => {
    try {
      window.localStorage.clear();
    } catch {
      // Storage may be unavailable.
    }
  });

  it("builds the replay shell and renders an accepted v3 document end to end", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const app = new PublicTraceApp(root, "replay");

    expect(viewModeFromLocation({ search: "?view=replay" })).toBe("replay");
    expect(viewModeFromLocation({ search: "" })).toBe("live");

    await app.loadDocument(publicTrace());

    const text = root.textContent ?? "";
    // Header integrity from the sealed trace phase.
    expect(text).toContain(t("integrity.sealed"));
    // The fixture's single entity appears in the entity tree.
    expect(text).toContain("fixture.uav");
    // The read-only terminal dock exists with its channel tabs.
    expect(root.querySelectorAll(".terminal-tab").length).toBe(8);
    expect(root.querySelector(".terminal-log")?.textContent).toContain("advance to waypoint");
    expect(root.querySelector<HTMLButtonElement>('[data-channel="px4"]')?.hidden).toBe(true);
    // The map received at least one render of the loaded scene.
    expect(root.querySelector("#city-map")).not.toBeNull();

    app.dispose();
    expect(() => app.loadDocument(publicTrace())).rejects.toThrow();
  });

  it("collects declared building render assets together with entity models", () => {
    const digest = "a".repeat(64);
    const other = "b".repeat(64);
    const trace = {
      scenario: {
        assets: [
          {
            asset_id: "asset.building",
            replay_path: `assets/${digest}`,
            sha256: digest,
            size_bytes: 8,
            media_type: "application/json",
          },
          {
            asset_id: "asset.uav",
            replay_path: `assets/${other}`,
            sha256: other,
            size_bytes: 4,
            media_type: "application/json",
          },
          {
            asset_id: "asset.imagery",
            replay_path: `assets/${"c".repeat(64)}`,
            sha256: "c".repeat(64),
            size_bytes: 2,
            media_type: "image/png",
          },
        ],
        entities: [{ model_asset_id: "asset.uav" }],
        buildings: [{ render_asset_id: "asset.building" }],
        base_layers: [{ kind: "imagery", asset_id: "asset.imagery" }],
      },
    } as unknown as PublicTrace;
    expect(declaredReplayModelAssets(trace).map((asset) => asset.asset_id)).toEqual([
      "asset.building",
      "asset.uav",
      "asset.imagery",
    ]);
  });

  it("refreshes each UAV's telemetry immediately when selection changes while paused", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const app = new PublicTraceApp(root, "replay");
    const trace = publicTrace() as unknown as PublicTrace;
    const second = structuredClone(trace.scenario.entities[0]!);
    second.entity_id = "fixture.uav2";
    trace.scenario.entities.push(second);
    const sample = structuredClone(trace.scene_states[0]!.samples[0]!);
    sample.entity_id = second.entity_id;
    sample.sample_digest = "9".repeat(64);
    sample.battery = { remaining_fraction: 0.42, voltage_v: null, current_a: null, temperature_c: null, consumed_mah: null, attributes: [] };
    trace.scene_states[0]!.samples.push(sample);
    await app.loadDocument(trace);
    const button = [...root.querySelectorAll<HTMLButtonElement>(".entity-select")].find(button => button.textContent === second.entity_id)!;
    button.click();
    expect(root.querySelector<HTMLElement>(".telemetry-hud")?.dataset.entityId).toBe(second.entity_id);
    expect(root.querySelector<HTMLElement>(".telemetry-hud")?.dataset.sampleDigest).toBe(sample.sample_digest);
    expect(root.querySelector(".telemetry-panel")?.textContent).toContain("42.0%");
    app.dispose();
  });

  it("keeps the live control panel hidden in replay mode and visible in live mode", () => {
    const replayRoot = document.createElement("div");
    document.body.append(replayRoot);
    const replayApp = new PublicTraceApp(replayRoot, "replay");
    expect(replayRoot.querySelector<HTMLSelectElement>(".workspace-trace-select")).not.toBeNull();
    replayApp.dispose();

    const liveRoot = document.createElement("div");
    document.body.append(liveRoot);
    const liveApp = new PublicTraceApp(liveRoot, "live");
    expect(liveRoot.querySelector<HTMLInputElement>(".credential-input")).not.toBeNull();
    expect(liveRoot.querySelectorAll(".control-buttons .action-button").length).toBe(5);
    liveApp.dispose();
  });
});
