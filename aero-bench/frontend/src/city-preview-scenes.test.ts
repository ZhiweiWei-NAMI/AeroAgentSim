// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import {
  CITY_PREVIEW_SCENE_PRESETS,
  cityPreviewScenePresetForPath,
  createCityPreviewSceneDraft,
  renderCityPreviewScenePresetPanel,
} from "./city-preview-scenes";
import { parseCityWorkspaceConfig } from "./city-workspace-config";

describe("ordinary city preview presets", () => {
  it("publishes unique explicit local scene choices", () => {
    expect(CITY_PREVIEW_SCENE_PRESETS.map(item => item.id)).toEqual(["huangpu", "jingan"]);
    expect(new Set(CITY_PREVIEW_SCENE_PRESETS.map(item => item.scenePath)).size)
      .toBe(CITY_PREVIEW_SCENE_PRESETS.length);
    for (const preset of CITY_PREVIEW_SCENE_PRESETS) {
      expect(preset.scenePath).toMatch(/^\/city-presentation\/[A-Za-z0-9_-]+\.json$/);
      expect(cityPreviewScenePresetForPath(preset.scenePath)).toBe(preset);
    }
    expect(cityPreviewScenePresetForPath("/city-presentation/unpublished.json")).toBeNull();
  });

  it("creates a strict fresh Jing'an draft from measured staging defaults", () => {
    const preset = CITY_PREVIEW_SCENE_PRESETS.find(item => item.id === "jingan")!;
    const draft = createCityPreviewSceneDraft(preset);
    expect(parseCityWorkspaceConfig(draft)).toEqual(draft);
    expect(draft).toMatchObject({
      name: "Jing'an capacity-bounded internal research preview",
      scenePath: "/city-presentation/jingan-engineering-preview-v3.json",
      seed: 24_427,
      environment: {
        cloudCover: 0, precipitation: "none", precipitationRateMmPerH: 0,
        visibilityM: 10_000, windMps: 0, windDirectionDeg: 0,
        timeOfDay: "day", reflectionsEnabled: true,
      },
      traffic: { vehicles: 60, pedestrians: 3, bicycles: 4 },
      algorithms: {
        mode: "centralized", assignment: "external", routing: "external", energy: "external",
        parameters: { profile: "jingan.capacity-bounded.internal-research.v3" },
      },
      deployment: { executor: "docker_reference", imageRef: "" },
      orderGeneration: { seed: 24_427, maxOrders: 0 },
    });
    expect(draft.fleet).toEqual([]);
    expect(draft.facilities).toEqual([]);
    expect(draft.airspace).toEqual([]);
    expect(draft.orders).toEqual([]);
    expect(draft.performanceProfiles).toEqual([]);
    expect(draft.authoredLandscape).toEqual([]);
    expect(draft.stateKeyframes).toEqual([]);
  });

  it("returns independent Huangpu defaults instead of carrying another region's edits", () => {
    const preset = CITY_PREVIEW_SCENE_PRESETS.find(item => item.id === "huangpu")!;
    const first = createCityPreviewSceneDraft(preset);
    const second = createCityPreviewSceneDraft(preset);
    first.fleet[0]!.count = 99;
    first.facilities.push({ id: "foreign", name: "foreign", kind: "vertiport",
      position: { x: 0, z: 0 }, rotationDeg: 0, widthM: 10, depthM: 10, heightM: 4,
      capacity: 1, chargingPowerW: 0 });
    expect(second.fleet[0]!.count).toBe(1);
    expect(second.facilities).toEqual([]);
  });

  it("keeps selection inert until explicit apply and states replacement consequences", async () => {
    const root = document.createElement("div");
    const onApply = vi.fn(async () => ({ ok: true,
      message: "静安场景已核验；新草稿等待三维预览门后保存。" }));
    renderCityPreviewScenePresetPanel(root,
      "/city-presentation/default-scene-v1.json", onApply);
    const select = root.querySelector("select")!;
    const button = root.querySelector<HTMLButtonElement>("button")!;
    expect(select.value).toBe("huangpu");
    expect(button.disabled).toBe(true);
    expect(root.querySelector('[data-role="scene-preset-consequence"]')?.textContent)
      .toContain("当前设施、机队绑定、订单、事件和创作景观不会跨区迁移");

    select.value = "jingan";
    select.dispatchEvent(new Event("change"));
    expect(onApply).not.toHaveBeenCalled();
    expect(button.disabled).toBe(false);
    expect(root.querySelector('[data-role="scene-preset-result"]')?.textContent)
      .toContain("未保存更改不会迁移");
    button.click();
    await vi.waitFor(() => expect(onApply).toHaveBeenCalledOnce());
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ id: "jingan" }));
    await vi.waitFor(() => expect(root.querySelector('[data-role="scene-preset-result"]')?.textContent)
      .toContain("等待三维预览门后保存"));
  });

  it("surfaces a failed strict load without claiming a switch", async () => {
    const root = document.createElement("div");
    renderCityPreviewScenePresetPanel(root,
      "/city-presentation/default-scene-v1.json", async () => ({
        ok: false, message: "静安场景核验失败；当前草稿与本地存储未改动。",
      }));
    const select = root.querySelector("select")!;
    select.value = "jingan";
    select.dispatchEvent(new Event("change"));
    root.querySelector<HTMLButtonElement>("button")!.click();
    await vi.waitFor(() => expect(root.querySelector<HTMLElement>('[data-role="scene-preset-result"]')?.dataset.state)
      .toBe("error"));
    expect(root.textContent).toContain("当前草稿与本地存储未改动");
  });
});
