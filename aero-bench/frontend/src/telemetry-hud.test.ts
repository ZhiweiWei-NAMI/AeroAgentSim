import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TelemetryHud } from "./telemetry-hud";
import { identifier, sceneState, stateSample } from "./testing/trace-v3-fixture";
import type { SceneState } from "./generated/aero-bench-contracts";

describe("TelemetryHud PFD", () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });
  afterEach(() => vi.restoreAllMocks());

  it("does not invent battery, mode, or a target lock from missing fields", () => {
    const viewport = new TelemetryHud({ onCameraModeChange: () => undefined });
    const sample = stateSample(1, identifier("uav"));
    delete (sample as Record<string, unknown>).battery;
    delete (sample as Record<string, unknown>).mode;
    viewport.update(sceneState(1, [identifier("uav")], [sample]) as unknown as SceneState);
    expect(viewport.root.textContent).toContain("TICK 1");
    expect(viewport.root.textContent).not.toContain("target.facade_crack");
    viewport.dispose();
  });

  it("skips drawing hidden canvases while minimized and redraws the latest state on restore", () => {
    const draws: string[] = [];
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
      const canvas = this;
      // Any other call returns another inert proxy, so gradients and measurements chain.
      const inert: object = new Proxy(() => inert, { get: () => inert, apply: () => inert, set: () => true });
      return new Proxy({}, { get: (_target, name) => typeof name === "string" && ["clearRect", "fillText"].includes(name)
        ? () => draws.push(`${canvas.className}:${name}`) : inert, set: () => true }) as unknown as CanvasRenderingContext2D;
    } as unknown as HTMLCanvasElement["getContext"]);
    const viewport = new TelemetryHud({ onCameraModeChange: () => undefined });
    document.body.append(viewport.root);
    const state = (tick: number) => sceneState(tick, [identifier("uav")], [stateSample(tick, identifier("uav"))]) as unknown as SceneState;
    viewport.update(state(1));
    expect(draws.some(draw => draw.startsWith("gz-canvas:"))).toBe(true);
    expect(draws.some(draw => draw.startsWith("uav-view-canvas:"))).toBe(true);
    const button = viewport.root.querySelector<HTMLButtonElement>(".gz-minimize")!;
    button.click();
    draws.length = 0;
    viewport.update(state(2));
    viewport.update(state(3));
    expect(draws).toEqual([]);
    expect(viewport.root.textContent).toContain("TICK 3");
    button.click();
    expect(draws.some(draw => draw.startsWith("gz-canvas:"))).toBe(true);
    expect(draws.some(draw => draw.startsWith("uav-view-canvas:"))).toBe(true);
    viewport.dispose();
  });

  it("keeps an explicit restore button and expands again", () => {
    const viewport = new TelemetryHud({ onCameraModeChange: () => undefined });
    document.body.append(viewport.root);
    const button = viewport.root.querySelector<HTMLButtonElement>(".gz-minimize")!;
    button.click();
    expect(viewport.root.classList.contains("minimized")).toBe(true);
    expect(button.textContent).toBe("展开");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    button.click();
    expect(viewport.root.classList.contains("minimized")).toBe(false);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(viewport.root.querySelector<HTMLButtonElement>(".gz-swap")?.hidden).toBe(true);
    viewport.dispose();
  });
});
