import {
  CITY_FLEET_ASSETS,
  createDefaultCityWorkspaceConfig,
  type CityWorkspaceConfig,
} from "./city-workspace-config";
import { facilityLandingPads } from "./city-facility-models";
import { CITY_WEATHER_PRESETS } from "./city-weather";
import type { CityDraftEnvironment } from "./city-draft-environment";

type Environment = CityDraftEnvironment;
type FleetEntry = CityWorkspaceConfig["fleet"][number];

const WEATHER_PRESETS = {
  clear: { ...CITY_WEATHER_PRESETS.clear, timeOfDay: "day", reflectionsEnabled: false },
  cloudy: { ...CITY_WEATHER_PRESETS.cloudy, timeOfDay: "day", reflectionsEnabled: false },
  lightRain: { cloudCover: 0.8, precipitation: "drizzle", precipitationRateMmPerH: 1.5,
    visibilityM: 3500, windMps: 4, windDirectionDeg: 70, timeOfDay: "day", reflectionsEnabled: true },
  heavyRain: { cloudCover: 0.95, precipitation: "rain", precipitationRateMmPerH: 12,
    visibilityM: 900, windMps: 9, windDirectionDeg: 70, timeOfDay: "day", reflectionsEnabled: true },
  fog: { ...CITY_WEATHER_PRESETS.fog, timeOfDay: "day", reflectionsEnabled: false },
  snow: { ...CITY_WEATHER_PRESETS.snow, timeOfDay: "day", reflectionsEnabled: false },
} as const satisfies Record<string, Environment>;

const TIME_OF_DAY_OPTIONS = [
  ["day", "日景"], ["twilight", "黄昏"], ["night", "夜景"],
] as const;

const PRESET_OPTIONS = [
  ["clear", "晴"], ["cloudy", "多云"], ["lightRain", "小雨"],
  ["heavyRain", "大雨"], ["fog", "雾"], ["snow", "雪"],
] as const;

function field(label: string, control: HTMLInputElement | HTMLSelectElement): HTMLLabelElement {
  const node = document.createElement("label");
  node.className = "studio-field";
  const caption = document.createElement("span");
  caption.textContent = label;
  control.setAttribute("aria-label", label);
  node.append(caption, control);
  return node;
}

function selectControl(
  name: string,
  value: string,
  options: ReadonlyArray<readonly [string, string]>,
  onChange: (value: string) => void,
): HTMLSelectElement {
  const select = document.createElement("select");
  select.name = name;
  for (const [optionValue, label] of options) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = label;
    select.append(option);
  }
  select.value = value;
  if (select.value !== value) throw new Error(`${name} has unavailable value: ${value}`);
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function numberControl(
  name: string,
  value: number,
  min: number,
  max: number | null,
  step: string,
  onChange: (value: number) => void,
): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "number";
  input.name = name;
  input.value = String(value);
  input.min = String(min);
  if (max !== null) input.max = String(max);
  input.step = step;
  input.required = true;
  input.addEventListener("change", () => {
    if (!input.reportValidity() || !Number.isFinite(input.valueAsNumber)) return;
    onChange(input.valueAsNumber);
  });
  return input;
}

function card(title: string): HTMLElement {
  const section = document.createElement("section");
  section.className = "studio-card";
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);
  return section;
}

function grid(): HTMLElement {
  const node = document.createElement("div");
  node.className = "studio-grid";
  return node;
}

function button(label: string, onClick: () => void): HTMLButtonElement {
  const node = document.createElement("button");
  node.type = "button";
  node.className = "studio-button";
  node.textContent = label;
  node.addEventListener("click", onClick);
  return node;
}

function presetFor(environment: Environment): string {
  for (const [id] of PRESET_OPTIONS) {
    const preset = WEATHER_PRESETS[id];
    if (environment.cloudCover === preset.cloudCover
      && environment.precipitation === preset.precipitation
      && environment.precipitationRateMmPerH === preset.precipitationRateMmPerH
      && environment.visibilityM === preset.visibilityM
      && environment.windMps === preset.windMps
      && environment.windDirectionDeg === preset.windDirectionDeg
      && environment.timeOfDay === preset.timeOfDay
      && environment.reflectionsEnabled === preset.reflectionsEnabled) return id;
  }
  return "";
}

function nextFleetId(fleet: CityWorkspaceConfig["fleet"]): string {
  const existing = new Set(fleet.map(entry => entry.id));
  let index = 1;
  while (existing.has(`fleet-${index}`)) index++;
  return `fleet-${index}`;
}

/** Render editable scene demand. Recorded replay evidence remains separate from this draft. */
export function renderCityRuntimePanel(
  root: HTMLElement,
  config: CityWorkspaceConfig,
  onChange: (next: CityWorkspaceConfig) => void,
): void {
  let current = config;
  const emit = (next: CityWorkspaceConfig): void => {
    current = next;
    renderCityRuntimePanel(root, next, onChange);
    onChange(next);
  };
  const changeEnvironment = (patch: Partial<Environment>): void => {
    emit({ ...current, environment: { ...current.environment, ...patch } });
  };
  const changeFleet = (index: number, update: (entry: FleetEntry) => FleetEntry): void => {
    const fleet = current.fleet.map((entry, row) => row === index ? update(entry) : entry);
    emit({ ...current, fleet });
  };

  const weather = card("环境");
  const weatherFields = grid();
  const preset = selectControl("environment.preset", presetFor(current.environment),
    [["", "自定义"], ...PRESET_OPTIONS], value => {
      if (!value) return;
      const selected = WEATHER_PRESETS[value as keyof typeof WEATHER_PRESETS];
      if (!selected) throw new Error(`Unknown weather preset: ${value}`);
      changeEnvironment(selected);
    });
  weatherFields.append(field("环境预设", preset));
  weatherFields.append(field("云量（0–1）", numberControl("environment.cloudCover",
    current.environment.cloudCover, 0, 1, "any", cloudCover => changeEnvironment({ cloudCover }))));
  weatherFields.append(field("降水类型", selectControl("environment.precipitation",
    current.environment.precipitation,
    [["none", "无"], ["drizzle", "小雨"], ["rain", "雨"], ["snow", "雪"], ["hail", "冰雹"]],
    precipitation => changeEnvironment({ precipitation: precipitation as Environment["precipitation"] }))));
  weatherFields.append(field("降水强度（毫米/小时）", numberControl("environment.precipitationRateMmPerH",
    current.environment.precipitationRateMmPerH, 0, null, "any",
    precipitationRateMmPerH => changeEnvironment({ precipitationRateMmPerH }))));
  weatherFields.append(field("能见度（米）", numberControl("environment.visibilityM",
    current.environment.visibilityM, Number.MIN_VALUE, null, "any", visibilityM => changeEnvironment({ visibilityM }))));
  weatherFields.append(field("风速（米/秒）", numberControl("environment.windMps",
    current.environment.windMps, 0, null, "any", windMps => changeEnvironment({ windMps }))));
  weatherFields.append(field("风向（度）", numberControl("environment.windDirectionDeg",
    current.environment.windDirectionDeg, 0, 359.99999999999994, "any",
    windDirectionDeg => changeEnvironment({ windDirectionDeg }))));
  weatherFields.append(field("时段", selectControl("environment.timeOfDay", current.environment.timeOfDay,
    TIME_OF_DAY_OPTIONS, timeOfDay => {
      const selected = TIME_OF_DAY_OPTIONS.find(([value]) => value === timeOfDay);
      if (selected === undefined) throw new Error(`Unknown draft time of day: ${timeOfDay}`);
      changeEnvironment({ timeOfDay: selected[0] });
    })));
  const reflections = document.createElement("input");
  reflections.type = "checkbox";
  reflections.name = "environment.reflectionsEnabled";
  reflections.checked = current.environment.reflectionsEnabled;
  reflections.addEventListener("change", () => changeEnvironment({ reflectionsEnabled: reflections.checked }));
  weatherFields.append(field("地面反射", reflections));
  weather.append(weatherFields);

  const fleetCard = card("无人机编队");
  for (const [index, entry] of current.fleet.entries()) {
    const row = document.createElement("div");
    row.className = "studio-row";
    const heading = document.createElement("h4");
    heading.textContent = `编队 ${index + 1} · ${entry.id}`;
    row.append(heading, button(`删除编队 ${index + 1}`, () => {
      emit({ ...current, fleet: current.fleet.filter((_, rowIndex) => rowIndex !== index) });
    }));
    const fields = grid();
    fields.append(field(`编队 ${index + 1} 型号`, selectControl(`fleet.${index}.assetId`, entry.assetId,
      CITY_FLEET_ASSETS.map(asset => [asset.id, asset.label] as const), assetId => {
        changeFleet(index, fleetEntry => ({ ...fleetEntry, assetId }));
      })));
    fields.append(field(`编队 ${index + 1} 数量`, numberControl(`fleet.${index}.count`, entry.count,
      1, Number.MAX_SAFE_INTEGER, "1", count => changeFleet(index, fleetEntry => ({ ...fleetEntry, count })) )));
    const homeOptions: Array<readonly [string, string]> = [["", "未指定起降点"]];
    for (const facility of current.facilities) {
      if (facilityLandingPads(facility).length > 0) {
        homeOptions.push([facility.id, facility.name]);
      }
    }
    fields.append(field(`编队 ${index + 1} 起降点`, selectControl(`fleet.${index}.homeFacilityId`,
      entry.homeFacilityId ?? "", homeOptions, value => {
        changeFleet(index, fleetEntry => ({ ...fleetEntry, homeFacilityId: value || null }));
      })));
    fields.append(field(`编队 ${index + 1} 电池容量（瓦时）`, numberControl(`fleet.${index}.batteryWh`,
      entry.batteryWh, Number.MIN_VALUE, null, "any", batteryWh => {
        changeFleet(index, fleetEntry => ({ ...fleetEntry, batteryWh }));
      })));
    fields.append(field(`编队 ${index + 1} 预留电量比例（0–1）`, numberControl(`fleet.${index}.reserveRatio`,
      entry.reserveRatio, 0, 1, "any", reserveRatio => {
        changeFleet(index, fleetEntry => ({ ...fleetEntry, reserveRatio }));
      })));
    row.append(fields);
    fleetCard.append(row);
  }
  fleetCard.append(button("添加编队", () => {
    const template = createDefaultCityWorkspaceConfig().fleet[0];
    const asset = CITY_FLEET_ASSETS[0];
    if (!template || !asset) throw new Error("Default fleet asset is missing");
    emit({ ...current, fleet: [...current.fleet,
      { ...template, id: nextFleetId(current.fleet), assetId: asset.id, homeFacilityId: null }] });
  }));

  const traffic = card("背景交通与人流");
  const trafficFields = grid();
  trafficFields.append(field("背景机动车数", numberControl("traffic.vehicles", current.traffic.vehicles,
    0, Number.MAX_SAFE_INTEGER, "1", vehicles => emit({ ...current, traffic: { ...current.traffic, vehicles } }))));
  trafficFields.append(field("背景自行车数", numberControl("traffic.bicycles", current.traffic.bicycles,
    0, Number.MAX_SAFE_INTEGER, "1", bicycles => emit({ ...current, traffic: { ...current.traffic, bicycles } }))));
  trafficFields.append(field("背景行人数", numberControl("traffic.pedestrians", current.traffic.pedestrians,
    0, Number.MAX_SAFE_INTEGER, "1", pedestrians => emit({ ...current, traffic: { ...current.traffic, pedestrians } }))));
  traffic.append(trafficFields);
  const note = document.createElement("p");
  note.className = "studio-note";
  note.textContent = "场景需求；本次SUMO回放仅按已有轨迹显示上限，增加流量需重新生成SUMO场景。";
  traffic.append(note);

  // The seed is the draft's one editable execution field for registered native scenes (B-012).
  const run = card("运行参数");
  const runFields = grid();
  runFields.append(field("随机种子", numberControl("seed", current.seed, 0, Number.MAX_SAFE_INTEGER, "1",
    seed => emit({ ...current, seed }))));
  run.append(runFields);

  root.replaceChildren(run, weather, fleetCard, traffic);
}
