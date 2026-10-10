import { beforeEach, describe, expect, it, vi } from "vitest";
import { CITY_FLEET_ASSETS, createDefaultCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";
import { renderCityRuntimePanel } from "./city-runtime-panel";

function control<T extends HTMLInputElement | HTMLSelectElement>(root: HTMLElement, name: string): T {
  const element = root.querySelector<T>(`[name="${name}"]`);
  if (!element) throw new Error(`Missing control: ${name}`);
  return element;
}

function changeValue(root: HTMLElement, name: string, value: string): void {
  const element = control(root, name);
  element.value = value;
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

function clickButton(root: HTMLElement, label: string): void {
  const button = [...root.querySelectorAll("button")].find(node => node.textContent === label);
  if (!button) throw new Error(`Missing button: ${label}`);
  button.click();
}

describe("renderCityRuntimePanel", () => {
  beforeEach(() => document.body.replaceChildren());

  it("writes weather presets and individual environment values to the draft callback", () => {
    const root = document.createElement("div");
    const initial = createDefaultCityWorkspaceConfig();
    const originalEnvironment = structuredClone(initial.environment);
    const changed = vi.fn<(next: CityWorkspaceConfig) => void>();
    renderCityRuntimePanel(root, initial, changed);

    const preset = control<HTMLSelectElement>(root, "environment.preset");
    expect([...preset.options].map(option => option.textContent)).toEqual(
      ["自定义", "晴", "多云", "小雨", "大雨", "雾", "雪"]);
    changeValue(root, "environment.preset", "heavyRain");
    expect(changed).toHaveBeenCalledTimes(1);
    expect(changed.mock.lastCall?.[0].environment).toEqual({
      cloudCover: 0.95, precipitation: "rain", precipitationRateMmPerH: 12,
      visibilityM: 900, windMps: 9, windDirectionDeg: 70,
      timeOfDay: "day", reflectionsEnabled: true,
    });
    expect(initial.environment).toEqual(originalEnvironment);

    changeValue(root, "environment.cloudCover", "0.355");
    changeValue(root, "environment.timeOfDay", "night");
    const reflections = control<HTMLInputElement>(root, "environment.reflectionsEnabled");
    reflections.checked = false;
    reflections.dispatchEvent(new Event("change", { bubbles: true }));
    expect(changed.mock.lastCall?.[0].environment).toMatchObject({
      cloudCover: 0.355, precipitation: "rain", timeOfDay: "night", reflectionsEnabled: false,
    });
    expect(control<HTMLSelectElement>(root, "environment.preset").value).toBe("");
    const timeOfDay = control<HTMLSelectElement>(root, "environment.timeOfDay");
    expect([...timeOfDay.options].map(option => [option.value, option.textContent])).toEqual([
      ["day", "日景"], ["twilight", "黄昏"], ["night", "夜景"],
    ]);
    changeValue(root, "environment.timeOfDay", "twilight");
    expect(changed.mock.lastCall?.[0].environment.timeOfDay).toBe("twilight");
    // "dusk" is not an option: the browser coerces it to "", the panel rejects it loudly
    // as an uncaught listener error, and no legacy mood field is ever emitted.
    const listenerErrors: unknown[] = [];
    const onError = (event: ErrorEvent): void => { listenerErrors.push(event.error); event.preventDefault(); };
    window.addEventListener("error", onError);
    try { changeValue(root, "environment.timeOfDay", "dusk"); }
    finally { window.removeEventListener("error", onError); }
    expect(listenerErrors).toHaveLength(1);
    expect(String(listenerErrors[0])).toContain("Unknown draft time of day");
    expect(changed.mock.calls.every(([draft]) => "mood" in draft.environment)).toBe(false);
  });

  it("adds, configures, and removes fleet entries using declared assets and landing facilities", () => {
    const root = document.createElement("div");
    const initial: CityWorkspaceConfig = {
      ...createDefaultCityWorkspaceConfig(),
      facilities: [
        { id: "pad-test", name: "测试起降点", kind: "vertiport", position: { x: 0, z: 0 },
          rotationDeg: 0, widthM: 20, depthM: 20, heightM: 4, capacity: 2, chargingPowerW: 1000 },
        { id: "hub-test", name: "测试枢纽", kind: "hub", position: { x: 40, z: 0 },
          rotationDeg: 0, widthM: 30, depthM: 20, heightM: 5, capacity: 4, chargingPowerW: 2000 },
        { id: "charger-test", name: "测试充电点", kind: "charger", position: { x: 80, z: 0 },
          rotationDeg: 0, widthM: 10, depthM: 10, heightM: 3.2, capacity: 1, chargingPowerW: 500 },
      ],
    };
    const landingFacility = initial.facilities.find(facility => facility.kind === "charger");
    if (!landingFacility) throw new Error("Default workspace has no landing facility");
    const changed = vi.fn<(next: CityWorkspaceConfig) => void>();
    renderCityRuntimePanel(root, initial, changed);

    clickButton(root, "添加编队");
    const index = initial.fleet.length;
    expect(changed.mock.lastCall?.[0].fleet).toHaveLength(index + 1);
    expect(changed.mock.lastCall?.[0].fleet[index]).toMatchObject({
      assetId: CITY_FLEET_ASSETS[0]?.id, count: 1, homeFacilityId: null,
    });
    const assetControl = control<HTMLSelectElement>(root, `fleet.${index}.assetId`);
    expect([...assetControl.options].map(option => option.value)).toEqual(CITY_FLEET_ASSETS.map(asset => asset.id));
    const homeControl = control<HTMLSelectElement>(root, `fleet.${index}.homeFacilityId`);
    expect([...homeControl.options].map(option => option.value)).toEqual(
      ["", "pad-test", "hub-test", "charger-test"]);

    changeValue(root, `fleet.${index}.assetId`, CITY_FLEET_ASSETS.at(-1)!.id);
    changeValue(root, `fleet.${index}.count`, "3");
    changeValue(root, `fleet.${index}.homeFacilityId`, landingFacility.id);
    changeValue(root, `fleet.${index}.batteryWh`, "720.5");
    changeValue(root, `fleet.${index}.reserveRatio`, "0.281");
    expect(changed.mock.lastCall?.[0].fleet[index]).toMatchObject({
      assetId: CITY_FLEET_ASSETS.at(-1)!.id, count: 3,
      homeFacilityId: landingFacility.id, batteryWh: 720.5, reserveRatio: 0.281,
    });
    expect(initial.fleet).toHaveLength(index);

    clickButton(root, `删除编队 ${index + 1}`);
    expect(changed.mock.lastCall?.[0].fleet).toHaveLength(index);
  });

  it("records background traffic demand and explains the SUMO replay limit", () => {
    const root = document.createElement("div");
    const changed = vi.fn<(next: CityWorkspaceConfig) => void>();
    renderCityRuntimePanel(root, createDefaultCityWorkspaceConfig(), changed);
    changeValue(root, "traffic.vehicles", "12");
    changeValue(root, "traffic.bicycles", "7");
    changeValue(root, "traffic.pedestrians", "34");
    expect(changed.mock.lastCall?.[0].traffic).toEqual({ vehicles: 12, bicycles: 7, pedestrians: 34 });
    expect(root.textContent).toContain("增加流量需重新生成SUMO场景");
  });

  it("edits the draft seed as a non-negative integer and ignores invalid input", () => {
    const root = document.createElement("div");
    const changed = vi.fn<(next: CityWorkspaceConfig) => void>();
    renderCityRuntimePanel(root, createDefaultCityWorkspaceConfig(), changed);
    expect(control<HTMLInputElement>(root, "seed").value).toBe("1");
    changeValue(root, "seed", "4242");
    expect(changed.mock.lastCall?.[0].seed).toBe(4242);
    changeValue(root, "seed", "-1");
    changeValue(root, "seed", "2.5");
    expect(changed).toHaveBeenCalledTimes(1);
  });
});
