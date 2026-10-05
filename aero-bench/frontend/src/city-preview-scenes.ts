import {
  createDefaultCityWorkspaceConfig,
  parseCityWorkspaceConfig,
  type CityWorkspaceConfig,
} from "./city-workspace-config";

export type CityPreviewSceneId = "huangpu" | "jingan";

export interface CityPreviewScenePreset {
  readonly id: CityPreviewSceneId;
  readonly scenePath: string;
  readonly optionLabel: string;
  readonly placeLabel: string;
  readonly draftName: string;
  readonly draftSeed: number;
  readonly draftEnvironment: CityWorkspaceConfig["environment"];
  readonly baselineTraffic: CityWorkspaceConfig["traffic"];
  readonly draftAlgorithms: CityWorkspaceConfig["algorithms"];
  readonly draftDeployment: CityWorkspaceConfig["deployment"];
  readonly includeDefaultFleet: boolean;
}

export interface CityPreviewSceneApplyResult {
  readonly ok: boolean;
  readonly message: string;
}

export interface CityPreviewScenePresetPanelHandle {
  dispose(): void;
}

/** Explicitly published ordinary previews. Choosing one creates a new authoring
 * draft; these entries never imply a native-scene registration or formal run. */
export const CITY_PREVIEW_SCENE_PRESETS = [
  {
    id: "huangpu",
    scenePath: "/city-presentation/default-scene-v1.json",
    optionLabel: "上海黄浦 · 当前默认工程预览",
    placeLabel: "上海 · 黄浦",
    draftName: "Shanghai Huangpu draft",
    draftSeed: 1,
    draftEnvironment: {
      cloudCover: 0, precipitation: "none", precipitationRateMmPerH: 0,
      visibilityM: 10_000, windMps: 0, windDirectionDeg: 0,
      timeOfDay: "day", reflectionsEnabled: true,
    },
    baselineTraffic: { vehicles: 0, pedestrians: 0, bicycles: 0 },
    draftAlgorithms: {
      mode: "centralized", assignment: "greedy", routing: "astar",
      energy: "reserve_threshold", parameters: {},
    },
    draftDeployment: { executor: "docker_reference", imageRef: "" },
    includeDefaultFleet: true,
  },
  {
    id: "jingan",
    scenePath: "/city-presentation/jingan-engineering-preview-v3.json",
    optionLabel: "上海静安 · 工程预览",
    placeLabel: "上海 · 静安",
    draftName: "Jing'an capacity-bounded internal research preview",
    draftSeed: 24_427,
    draftEnvironment: {
      cloudCover: 0, precipitation: "none", precipitationRateMmPerH: 0,
      visibilityM: 10_000, windMps: 0, windDirectionDeg: 0,
      timeOfDay: "day", reflectionsEnabled: true,
    },
    baselineTraffic: { vehicles: 60, pedestrians: 3, bicycles: 4 },
    draftAlgorithms: {
      mode: "centralized", assignment: "external", routing: "external", energy: "external",
      parameters: { profile: "jingan.capacity-bounded.internal-research.v3" },
    },
    draftDeployment: { executor: "docker_reference", imageRef: "" },
    includeDefaultFleet: false,
  },
] as const satisfies readonly CityPreviewScenePreset[];

export function cityPreviewScenePresetForPath(scenePath: string): CityPreviewScenePreset | null {
  return CITY_PREVIEW_SCENE_PRESETS.find(preset => preset.scenePath === scenePath) ?? null;
}

/** Build a new region-local draft. No facility, flight, order, landscape, or
 * selected/native identity crosses the scene boundary. The strict scene loader
 * verifies the manifest and its environment source before Studio installs it. */
export function createCityPreviewSceneDraft(preset: CityPreviewScenePreset): CityWorkspaceConfig {
  const base = createDefaultCityWorkspaceConfig();
  return parseCityWorkspaceConfig({
    ...base,
    name: preset.draftName,
    scenePath: preset.scenePath,
    seed: preset.draftSeed,
    environment: { ...preset.draftEnvironment },
    fleet: preset.includeDefaultFleet ? base.fleet.map(entry => ({ ...entry })) : [],
    traffic: { ...preset.baselineTraffic },
    facilities: [],
    airspace: [],
    events: [],
    actionRules: [],
    stateKeyframes: [],
    labelRules: [],
    algorithms: { ...preset.draftAlgorithms, parameters: { ...preset.draftAlgorithms.parameters } },
    deployment: { ...preset.draftDeployment },
    orders: [],
    orderGeneration: { ...base.orderGeneration, seed: preset.draftSeed },
    performanceProfiles: [],
    authoredLandscape: [],
  });
}

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K, className: string, text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Render an explicit scene switch. Selecting an option is inert; only the
 * button authorizes replacement through Studio's strict import/reload gate. */
export function renderCityPreviewScenePresetPanel(
  root: HTMLElement,
  currentScenePath: string,
  onApply: (preset: CityPreviewScenePreset) => Promise<CityPreviewSceneApplyResult>,
): CityPreviewScenePresetPanelHandle {
  let disposed = false;
  let busy = false;
  const panel = element("section", "studio-card");
  panel.dataset.role = "ordinary-scene-preset";
  panel.append(element("h2", "studio-panel-title", "城市工程预览"));
  const note = element("p", "studio-note",
    "选择其他地区不会立即修改草稿。明确应用后，系统先校验该地区的场景清单和空间资料；"
    + "只有核验成功才会用一份全新的地区草稿替换当前草稿，并在三维预览门通过后保存。"
    + "当前设施、机队绑定、订单、事件和创作景观不会跨区迁移。");
  note.dataset.role = "scene-preset-consequence";
  const field = element("label", "studio-field");
  field.append(element("span", "", "普通城市预览"));
  const select = document.createElement("select");
  select.setAttribute("aria-label", "普通城市预览地区");
  const current = cityPreviewScenePresetForPath(currentScenePath);
  if (current === null) select.append(new Option("当前草稿不是已发布地区预设", ""));
  for (const preset of CITY_PREVIEW_SCENE_PRESETS) {
    select.append(new Option(preset.optionLabel, preset.id));
  }
  select.value = current?.id ?? "";
  field.append(select);
  const actions = element("div", "studio-row");
  const apply = element("button", "studio-button", "校验并切换地区");
  apply.type = "button";
  const result = element("p", "studio-note",
    current === null ? "当前草稿路径不会被自动替换。" : `当前地区：${current.optionLabel}。`);
  result.dataset.role = "scene-preset-result";
  result.setAttribute("aria-live", "polite");
  actions.append(apply);
  panel.append(note, field, actions, result);
  root.replaceChildren(panel);

  const selectedPreset = (): CityPreviewScenePreset | null =>
    CITY_PREVIEW_SCENE_PRESETS.find(preset => preset.id === select.value) ?? null;
  const sync = (): void => {
    const selected = selectedPreset();
    apply.disabled = busy || selected === null || selected.scenePath === currentScenePath;
  };
  select.addEventListener("change", () => {
    if (disposed) return;
    const selected = selectedPreset();
    result.textContent = selected === null || selected.scenePath === currentScenePath
      ? `当前地区：${current?.optionLabel ?? "非预设场景"}。草稿未修改。`
      : `将创建 ${selected.optionLabel} 的全新草稿；当前草稿及其未保存更改不会迁移。`;
    delete result.dataset.state;
    sync();
  });
  apply.addEventListener("click", () => {
    const selected = selectedPreset();
    if (disposed || busy || selected === null || selected.scenePath === currentScenePath) return;
    busy = true;
    apply.textContent = "正在校验地区场景…";
    result.textContent = `正在读取并核验 ${selected.optionLabel}；当前草稿与本地存储尚未改动。`;
    result.dataset.state = "dirty";
    sync();
    void onApply(selected).then(outcome => {
      if (disposed) return;
      result.textContent = outcome.message;
      result.dataset.state = outcome.ok ? "saved" : "error";
    }).catch((error: unknown) => {
      if (disposed) return;
      result.textContent = `地区切换失败：${error instanceof Error ? error.message : String(error)}。当前草稿与本地存储未改动。`;
      result.dataset.state = "error";
    }).finally(() => {
      if (disposed) return;
      busy = false;
      apply.textContent = "校验并切换地区";
      sync();
    });
  });
  sync();

  return {
    dispose(): void {
      if (disposed) return;
      disposed = true;
      root.replaceChildren();
    },
  };
}
