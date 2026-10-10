// @vitest-environment jsdom
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { createCityPreviewSceneButtons, PublicTraceMap } from "./map";
import { createRenderWeatherControls } from "./city-render-weather-controls";
import { CITY_WEATHER_PRESETS } from "./city-weather";
import { parseCitySceneConfig } from "./city-scene-config";

describe("published preview navigation", () => {
  it("navigates only after an explicit click and labels both regions as previews", () => {
    const navigate = vi.fn();
    const buttons = createCityPreviewSceneButtons(navigate);
    expect(navigate).not.toHaveBeenCalled();
    expect(buttons.map(button => button.textContent)).toEqual([
      "上海 · 黄浦工程预览", "上海 · 静安工程预览",
    ]);
    buttons[1]!.click();
    expect(navigate).toHaveBeenCalledExactlyOnceWith(
      "/city-presentation/jingan-engineering-preview-v3.json",
    );
    buttons[0]!.click();
    expect(navigate).toHaveBeenLastCalledWith("/city-presentation/default-scene-v1.json");
  });

  it("creates independent controls for the road and building-only viewer bars", () => {
    const first = createCityPreviewSceneButtons(vi.fn());
    const secondNavigate = vi.fn();
    const second = createCityPreviewSceneButtons(secondNavigate);
    expect(first[0]).not.toBe(second[0]);
    second[1]!.click();
    expect(secondNavigate).toHaveBeenCalledExactlyOnceWith(second[1]!.dataset.scenePath);
    expect(second.every(button => button.type === "button")).toBe(true);
  });

  it.each(["v2", "v3"])("retains a valid published Jing'an %s preset and pinned road/environment bytes", version => {
    const raw = readFileSync(resolve(`public/city-presentation/jingan-engineering-preview-${version}.json`));
    if (version === "v3") {
      expect(createHash("sha256").update(raw).digest("hex"))
        .toBe("ae56bd4d9b81d99b1f2a67734bde9ecaa5dabc3a00525c23918a5ca69c4771df");
    }
    const scene = parseCitySceneConfig(JSON.parse(raw.toString("utf8")));
    if (scene.building_render === undefined) throw new Error("Published Jing'an preset has no building-render binding");
    expect(scene.building_render.source_scene_id).toBe("shanghai-jingan-osm-v1");
    expect(scene.road_assets).toBeDefined();
    expect(scene.environment_source).toBeDefined();
    const refs = [scene.road_assets!.road, scene.road_assets!.effective_fixtures,
      scene.road_assets!.traffic, scene.road_assets!.flight, scene.environment_source!];
    for (const ref of refs) {
      const bytes = readFileSync(resolve("public", ref.url.slice(1)));
      expect(bytes.byteLength).toBe(ref.size_bytes);
      expect(createHash("sha256").update(bytes).digest("hex")).toBe(ref.sha256);
    }
  });
});

describe("weather controls in the active scene dock", () => {
  it("keeps preview weather visible and retains the same handlers across render-only scene switches", () => {
    const root = document.createElement("div"); document.body.append(root);
    const previewControls = document.createElement("div"), renderControls = document.createElement("div");
    previewControls.hidden = false; renderControls.hidden = true;
    root.append(previewControls, renderControls);
    const change = vi.fn();
    const renderWeatherControls = createRenderWeatherControls(change);
    renderControls.append(renderWeatherControls.element);
    const map = { previewControls, renderControls, renderWeatherControls };
    const mount = (PublicTraceMap.prototype as unknown as {
      mountRenderWeatherControls(renderScene: boolean, trafficPresent: boolean): void;
    }).mountRenderWeatherControls;
    try {
      mount.call(map, true, true);
      const preset = root.querySelector<HTMLSelectElement>('[name="render-weather-preset"]')!;
      expect(preset.closest("[hidden]")).toBeNull();
      expect(renderWeatherControls.element.parentElement).toBe(previewControls);
      preset.value = "rain"; preset.dispatchEvent(new Event("change"));
      expect(change).toHaveBeenLastCalledWith(CITY_WEATHER_PRESETS.rain);

      previewControls.hidden = true; renderControls.hidden = false;
      mount.call(map, true, false);
      expect(preset.closest("[hidden]")).toBeNull();
      expect(renderWeatherControls.element.parentElement).toBe(renderControls);
      expect(root.querySelector('[name="render-weather-preset"]')).toBe(preset);
      expect(root.querySelectorAll('[name="render-weather-preset"]')).toHaveLength(1);
      preset.value = "fog"; preset.dispatchEvent(new Event("change"));
      expect(change).toHaveBeenLastCalledWith(CITY_WEATHER_PRESETS.fog);

      mount.call(map, false, true);
      expect(renderWeatherControls.element.parentElement).toBe(renderControls);
    } finally { root.remove(); }
  });
});
