import { describe, expect, it, vi } from "vitest";
import { createRenderWeatherControls } from "./city-render-weather-controls";
import { CITY_WEATHER_PRESETS } from "./city-weather";

describe("render-scene visual weather controls", () => {
  it("selects real presets without modifying the shared preset data", () => {
    const change = vi.fn();
    const { element } = createRenderWeatherControls(change);
    const preset = element.querySelector<HTMLSelectElement>('[name="render-weather-preset"]')!;
    preset.value = "rain"; preset.dispatchEvent(new Event("change"));
    expect(change).toHaveBeenCalledWith(CITY_WEATHER_PRESETS.rain);
    expect(change.mock.lastCall![0]).not.toBe(CITY_WEATHER_PRESETS.rain);
    const note = element.querySelector<HTMLElement>("span[data-role='render-weather-note']")!;
    expect(note.textContent).toContain("未保存");
    expect(note.dataset.mode).toBe("preview");
  });

  it("edits wind independently and marks a modified preset as custom", () => {
    const change = vi.fn(); const { element, sync } = createRenderWeatherControls(change);
    sync({ ...CITY_WEATHER_PRESETS.rain });
    const speed = element.querySelector<HTMLInputElement>('[name="render-wind-speed"]')!;
    speed.value = "2.5"; speed.dispatchEvent(new Event("change"));
    expect(change).toHaveBeenLastCalledWith({ ...CITY_WEATHER_PRESETS.rain, windMps: 2.5 });
    expect(element.querySelector<HTMLSelectElement>("select")!.value).toBe("");
    const direction = element.querySelector<HTMLInputElement>('[name="render-wind-direction"]')!;
    direction.value = "180"; direction.dispatchEvent(new Event("change"));
    expect(change).toHaveBeenLastCalledWith({ ...CITY_WEATHER_PRESETS.rain, windMps: 2.5, windDirectionDeg: 180 });
  });

  it("rejects empty, negative and out-of-range wind inputs before emitting", () => {
    const change = vi.fn(); const { element } = createRenderWeatherControls(change);
    const speed = element.querySelector<HTMLInputElement>('[name="render-wind-speed"]')!;
    for (const value of ["", "-1"]) { speed.value = value; speed.dispatchEvent(new Event("change")); }
    const direction = element.querySelector<HTMLInputElement>('[name="render-wind-direction"]')!;
    direction.value = "360"; direction.dispatchEvent(new Event("change"));
    expect(change).not.toHaveBeenCalled();
  });
});
