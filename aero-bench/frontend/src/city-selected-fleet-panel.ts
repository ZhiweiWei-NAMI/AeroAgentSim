/** Selected-city fleet and background-demand authoring panel.
 *
 * This is the selected-OSM-city fleet panel: it edits the CitySelectedScenario
 * v2 `fleet` entries and `demand` counts bound to exactly one SelectedSceneDraft.
 * It is NOT the old scenePath runtime panel — the document carries no
 * trajectories, orders or dynamics, and nothing here simulates a flight.
 *
 * The panel only consumes locally available curated `model:*` UAV assets passed
 * by the caller. It never fetches assets. Every valid change commits through an
 * async `onChange`; if the caller rejects, the returned message is shown and the
 * previously displayed scenario is retained. Candidates are always re-parsed with
 * `parseCitySelectedScenario` before committing — bad numeric data is surfaced,
 * never silently coerced. A saved entry whose model is missing from the supplied
 * asset list is flagged as a mismatch, not substituted. */
import type { SelectedSceneDraft } from "./city-selected-draft";
import { sameSelectedSceneDraft } from "./city-selected-draft";
import type {
  CitySelectedScenario, SelectedScenarioDemand, SelectedScenarioFacility, SelectedScenarioFleetEntry,
} from "./city-selected-scenario";
import { parseCitySelectedScenario } from "./city-selected-scenario";

/** One locally available curated UAV model the caller offers for selection.
 * Only options whose caller is the authoring app itself are listed here; the
 * panel never discovers or downloads model entries. */
export interface SelectedFleetAssetOption {
  /** A `model:*` asset id, e.g. "model:holybro-x500". */
  readonly id: string;
  /** Practical Chinese display title shown in the model selector. */
  readonly title: string;
  /** Curated subgroup / family, rendered as the select optgroup label. */
  readonly subgroup: string;
  /** Optional short preview text shown under the model choice. */
  readonly preview?: string;
}

const KIND_LABELS: Record<SelectedScenarioFacility["kind"], string> = {
  vertiport: "起降点", hub: "物流中转站", charger: "充电站",
};

interface AddDraft {
  assetId: string;
  count: number;
  homeFacilityId: string | null;
  batteryWh: number;
  reserveRatio: number;
  maxPayloadKg: number;
}

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const result = document.createElement(tag);
  if (className !== undefined) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function button(label: string, action: () => void, className = "studio-button"): HTMLButtonElement {
  const result = element("button", className, label);
  result.type = "button";
  result.addEventListener("click", action);
  return result;
}

function numericField(label: string, value: number, action: (value: number) => void,
                      options: { step?: string; min?: number; max?: number } = {}): HTMLLabelElement {
  const wrapper = element("label", "studio-field");
  const caption = element("span", "studio-field-label", label);
  const input = element("input", "studio-input");
  input.type = "number";
  input.step = options.step ?? "any";
  if (options.min !== undefined) input.min = String(options.min);
  if (options.max !== undefined) input.max = String(options.max);
  input.value = String(value);
  input.addEventListener("change", () => {
    // Empty reads as NaN so parseCitySelectedScenario rejects it explicitly;
    // bad numeric input is never silently normalised in the panel.
    action(input.valueAsNumber);
  });
  wrapper.append(caption, input);
  return wrapper;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function nextFleetId(current: readonly { id: string }[]): string {
  const known = new Set(current.map(entry => entry.id));
  let counter = 1;
  while (known.has(`uav-${counter}`)) counter++;
  return `uav-${counter}`;
}

class SelectedFleetPanel {
  private scenario: CitySelectedScenario | null = null;
  private onChange: ((next: CitySelectedScenario) => Promise<string | null>) | null = null;
  private assets: readonly SelectedFleetAssetOption[] = [];
  private assetById = new Map<string, SelectedFleetAssetOption>();
  private error: string | null = null;
  private busy = false;
  private adding = false;
  private addDraft: AddDraft | null = null;
  private lastScene: SelectedSceneDraft | null = null;

  constructor(private readonly root: HTMLElement) {}

  update(scenario: CitySelectedScenario, onChange: (next: CitySelectedScenario) => Promise<string | null>,
         assets: readonly SelectedFleetAssetOption[]): void {
    const sceneChanged = this.lastScene === null
      || !sameSelectedSceneDraft(this.lastScene, scenario.selectedScene);
    if (sceneChanged) {
      this.error = null;
      this.adding = false;
      this.addDraft = null;
    }
    this.scenario = scenario;
    this.onChange = onChange;
    this.assets = assets;
    this.assetById = new Map(assets.map(asset => [asset.id, asset]));
    this.lastScene = scenario.selectedScene;
    this.render();
  }

  /** Parse with the v2 contract, then commit. `base` is the scenario the user
   * acted on; it is restored (old state retained) when the caller rejects. */
  private async commit(next: CitySelectedScenario, base: CitySelectedScenario): Promise<void> {
    if (this.busy) return;
    let parsed: CitySelectedScenario;
    try {
      parsed = parseCitySelectedScenario(next);
    } catch (error) {
      this.fail(errorMessage(error));
      return;
    }
    const onChange = this.onChange;
    if (onChange === null) return;
    this.busy = true;
    this.render();
    try {
      const message = await onChange(parsed);
      if (message !== null) {
        this.scenario = base;
        this.error = message;
      } else {
        this.scenario = parsed;
        this.error = null;
      }
    } catch (error) {
      this.scenario = base;
      this.error = `保存失败：${errorMessage(error)}`;
    } finally {
      this.busy = false;
      if (this.root.querySelector(".studio-fleet-panel") !== null) this.render();
    }
  }

  private fail(message: string): void {
    this.error = message;
    this.render();
  }

  private commitFleetEntry(entry: SelectedScenarioFleetEntry,
                           patch: Partial<SelectedScenarioFleetEntry>): void {
    const scenario = this.scenario;
    if (scenario === null) return;
    const next: SelectedScenarioFleetEntry = { ...entry, ...patch };
    this.commit({ ...scenario, fleet: scenario.fleet.map(item => item.id === entry.id ? next : item) }, scenario);
  }

  private deleteFleetEntry(entry: SelectedScenarioFleetEntry): void {
    const scenario = this.scenario;
    if (scenario === null) return;
    this.commit({ ...scenario, fleet: scenario.fleet.filter(item => item.id !== entry.id) }, scenario);
  }

  private commitDemand(patch: Partial<SelectedScenarioDemand>): void {
    const scenario = this.scenario;
    if (scenario === null) return;
    this.commit({ ...scenario, demand: { ...scenario.demand, ...patch } }, scenario);
  }

  private openAdd(): void {
    if (this.assets.length === 0) return;
    const first = this.assets[0];
    if (first === undefined) return;
    this.error = null;
    this.adding = true;
    this.addDraft = {
      assetId: first.id,
      count: 1,
      homeFacilityId: null,
      batteryWh: 500,
      reserveRatio: 0.2,
      maxPayloadKg: 0,
    };
    this.render();
  }

  private async commitAdd(): Promise<void> {
    if (this.busy) return;
    const scenario = this.scenario;
    const draft = this.addDraft;
    if (scenario === null || draft === null) return;
    const entry: SelectedScenarioFleetEntry = {
      id: nextFleetId(scenario.fleet),
      assetId: draft.assetId,
      count: draft.count,
      homeFacilityId: draft.homeFacilityId,
      batteryWh: draft.batteryWh,
      reserveRatio: draft.reserveRatio,
      maxPayloadKg: draft.maxPayloadKg,
    };
    let parsed: CitySelectedScenario;
    try {
      parsed = parseCitySelectedScenario({ ...scenario, fleet: [...scenario.fleet, entry] });
    } catch (error) {
      this.fail(errorMessage(error));
      return;
    }
    const onChange = this.onChange;
    if (onChange === null) return;
    this.busy = true;
    this.render();
    try {
      const message = await onChange(parsed);
      if (message !== null) {
        this.error = message;
        return;
      }
    } catch (error) {
      this.error = `保存失败：${errorMessage(error)}`;
      return;
    } finally {
      this.busy = false;
      if (this.root.querySelector(".studio-fleet-panel") !== null) this.render();
    }
    this.scenario = parsed;
    this.error = null;
    this.adding = false;
    this.addDraft = null;
    if (this.root.querySelector(".studio-fleet-panel") !== null) this.render();
  }

  private modelField(currentAssetId: string, action: (assetId: string) => void, label: string): HTMLLabelElement {
    const wrapper = element("label", "studio-field");
    const caption = element("span", "studio-field-label", label);
    const select = element("select", "studio-input");
    select.setAttribute("aria-label", label);
    const groupNames = [...new Set(this.assets.map(asset => asset.subgroup))].sort();
    const byGroup = new Map<string, SelectedFleetAssetOption[]>();
    for (const asset of this.assets) {
      const group = byGroup.get(asset.subgroup);
      if (group !== undefined) group.push(asset);
      else byGroup.set(asset.subgroup, [asset]);
    }
    for (const groupName of groupNames) {
      const options = [...(byGroup.get(groupName) ?? [])].sort((left, right) => left.title.localeCompare(right.title));
      const optgroup = document.createElement("optgroup");
      optgroup.label = groupName;
      for (const asset of options) optgroup.append(new Option(asset.title, asset.id));
      select.append(optgroup);
    }
    if (!this.assetById.has(currentAssetId)) {
      select.append(new Option(`素材缺失：${currentAssetId}`, currentAssetId));
    }
    select.value = currentAssetId;
    select.addEventListener("change", () => action(select.value));
    wrapper.append(caption, select);
    return wrapper;
  }

  private facilityField(current: string | null,
                        action: (homeFacilityId: string | null) => void): HTMLLabelElement {
    const wrapper = element("label", "studio-field");
    const caption = element("span", "studio-field-label", "驻地设施");
    const select = element("select", "studio-input");
    select.setAttribute("aria-label", "驻地设施");
    select.append(new Option("未分配（无固定驻地）", ""));
    const scenario = this.scenario;
    if (scenario !== null) {
      for (const facility of scenario.facilities) {
        select.append(new Option(`${KIND_LABELS[facility.kind]} · ${facility.name}`, facility.id));
      }
    }
    select.value = current ?? "";
    select.addEventListener("change", () => action(select.value === "" ? null : select.value));
    wrapper.append(caption, select);
    return wrapper;
  }

  private entryCard(entry: SelectedScenarioFleetEntry): HTMLElement {
    const card = element("article", "studio-card");
    card.dataset.fleetEntryId = entry.id;
    const asset = this.assetById.get(entry.assetId);
    card.append(element("h4", "studio-card-title",
      asset !== undefined ? asset.title : `${entry.assetId}（素材缺失）`));
    card.append(element("p", "studio-source", `机队条目 ${entry.id}`));
    if (asset === undefined) {
      card.append(element("p", "studio-note",
        `该条目标识的机型 ${entry.assetId} 不在本次本地可用素材中，面板未将其替换为其他机型。在下方的机型选择里选择新素材后才写入新的 model:* 机型。`));
    }
    const fields = element("div", "studio-field-grid");
    fields.append(
      this.modelField(entry.assetId, assetId => {
        if (assetId !== entry.assetId) this.commitFleetEntry(entry, { assetId });
      }, "机型"),
      numericField("数量", entry.count, count => {
        if (count !== entry.count) this.commitFleetEntry(entry, { count });
      }, { min: 1, step: "1" }),
      this.facilityField(entry.homeFacilityId, homeFacilityId => {
        if (homeFacilityId !== entry.homeFacilityId) this.commitFleetEntry(entry, { homeFacilityId });
      }),
      numericField("电池电量 / Wh", entry.batteryWh, batteryWh => {
        if (batteryWh !== entry.batteryWh) this.commitFleetEntry(entry, { batteryWh });
      }),
      numericField("备用比例 / 0–1", entry.reserveRatio, reserveRatio => {
        if (reserveRatio !== entry.reserveRatio) this.commitFleetEntry(entry, { reserveRatio });
      }, { min: 0, max: 1, step: "0.05" }),
      numericField("最大载荷 / kg", entry.maxPayloadKg, maxPayloadKg => {
        if (maxPayloadKg !== entry.maxPayloadKg) this.commitFleetEntry(entry, { maxPayloadKg });
      }, { min: 0 }),
    );
    card.append(fields);
    const preview = asset?.preview;
    if (preview !== undefined) card.append(element("p", "studio-source", preview));
    card.append(button("删除机队条目", () => this.deleteFleetEntry(entry), "studio-button studio-danger"));
    return card;
  }

  private addForm(): HTMLElement {
    const draft = this.addDraft;
    const form = element("section", "studio-card");
    form.dataset.fleetAdd = "form";
    if (draft === null) return form;
    form.append(element("h4", "studio-card-title", "新增机队条目"));
    form.append(element("p", "studio-help",
      "默认电量 500 Wh、备用比例 0.2 只是初始配置值，不代表任何机型的实测参数。"));
    const fields = element("div", "studio-field-grid");
    fields.append(
      this.modelField(draft.assetId, assetId => { if (this.addDraft !== null) this.addDraft.assetId = assetId; }, "机型"),
      numericField("数量", draft.count, count => { if (this.addDraft !== null) this.addDraft.count = count; }, { min: 1, step: "1" }),
      this.facilityField(draft.homeFacilityId, homeFacilityId => { if (this.addDraft !== null) this.addDraft.homeFacilityId = homeFacilityId; }),
      numericField("电池电量 / Wh", draft.batteryWh, batteryWh => { if (this.addDraft !== null) this.addDraft.batteryWh = batteryWh; }),
      numericField("备用比例 / 0–1", draft.reserveRatio, reserveRatio => { if (this.addDraft !== null) this.addDraft.reserveRatio = reserveRatio; }, { min: 0, max: 1, step: "0.05" }),
      numericField("最大载荷 / kg", draft.maxPayloadKg, maxPayloadKg => { if (this.addDraft !== null) this.addDraft.maxPayloadKg = maxPayloadKg; }, { min: 0 }),
    );
    form.append(fields);
    const actions = element("div", "studio-row");
    actions.append(
      button("确认添加机队条目", () => void this.commitAdd(), "studio-button studio-button-primary"),
      button("取消", () => {
        this.adding = false;
        this.addDraft = null;
        this.error = null;
        this.render();
      }),
    );
    form.append(actions);
    return form;
  }

  private fleetSection(): HTMLElement {
    const scenario = this.scenario;
    const section = element("section", "studio-fleet-list");
    if (scenario === null) return section;
    section.append(element("h3", "studio-section-title", `机队 (${scenario.fleet.length})`));
    if (scenario.fleet.length === 0 && !this.adding) {
      section.append(element("p", "studio-empty",
        "尚未配置机队条目。从调用方提供的本地可用素材中选择机型并添加；本面板不拉取任何外部模型。"));
    }
    for (const entry of scenario.fleet) section.append(this.entryCard(entry));
    if (this.adding) section.append(this.addForm());
    if (!this.adding) {
      const add = button("添加机队条目", () => this.openAdd(), "studio-button studio-button-primary");
      add.setAttribute("aria-label", "添加机队条目");
      add.disabled = this.assets.length === 0;
      add.title = this.assets.length === 0
        ? "本地模型素材不可用，无法添加机队条目"
        : "添加一条新的机队条目（需从本地可用素材中选择机型）";
      section.append(add);
    }
    return section;
  }

  private demandSection(): HTMLElement {
    const scenario = this.scenario;
    const section = element("section", "studio-fleet-demand");
    if (scenario === null) return section;
    section.append(element("h3", "studio-section-title", "背景需求（仅数量配置）"));
    section.append(element("p", "studio-help",
      "背景车辆、行人、自行车数量只是写入文档的配置值，不生成任何模拟轨迹或道路占用。"));
    const fields = element("div", "studio-field-grid");
    fields.append(
      numericField("背景车辆数", scenario.demand.vehicles, vehicles => {
        if (vehicles !== scenario.demand.vehicles) this.commitDemand({ vehicles });
      }, { min: 0, step: "1" }),
      numericField("背景行人数量", scenario.demand.pedestrians, pedestrians => {
        if (pedestrians !== scenario.demand.pedestrians) this.commitDemand({ pedestrians });
      }, { min: 0, step: "1" }),
      numericField("背景自行车数量", scenario.demand.bicycles, bicycles => {
        if (bicycles !== scenario.demand.bicycles) this.commitDemand({ bicycles });
      }, { min: 0, step: "1" }),
    );
    section.append(fields);
    return section;
  }

  private render(): void {
    const scenario = this.scenario;
    if (scenario === null) return;
    const panel = element("section", "studio-fleet-panel");
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "选城场景机队与背景需求配置");
    panel.append(element("h2", "studio-panel-title", "机队与背景需求"));
    panel.append(element("p", "studio-help",
      "这里只编辑选城场景的机队条目与背景需求配置值。数量、电量、备用比例与载荷都是保存到文档的配置：它们不生成任何轨迹或订单，也不代表续航、动力或适航性已经过验证；保存仅校验格式与交叉引用，不承诺碰撞或可飞性。"));
    if (this.error !== null) {
      const alert = element("p", "studio-note", this.error);
      alert.setAttribute("role", "alert");
      panel.append(alert);
    }
    if (this.assets.length === 0) {
      panel.append(element("p", "studio-note",
        "本地 UAV 模型素材不可用：未提供任何 model:* 素材，因此无法添加或更换机队机型，也不提供伪造机型。已保存条目的机型会按素材缺失标明；其余配置与背景需求仍可编辑。"));
    }
    panel.append(this.fleetSection(), this.demandSection());
    panel.append(element("p", "studio-help", "本面板只使用调用方传入的本地素材，不会从网络获取模型信息。"));
    if (this.busy) {
      panel.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLButtonElement>("input, select, button")
        .forEach(control => { control.disabled = true; });
    }
    this.root.replaceChildren(panel);
  }
}

const panels = new WeakMap<HTMLElement, SelectedFleetPanel>();

/** Render (or update) the selected-city fleet and background demand panel.
 * Every valid edit commits through `onChange` synchronously-ordered per input;
 * a non-null returned message is shown and the previous scenario is restored. */
export function renderSelectedFleetPanel(root: HTMLElement, scenario: CitySelectedScenario,
                                         onChange: (next: CitySelectedScenario) => Promise<string | null>,
                                         assets: readonly SelectedFleetAssetOption[]): void {
  let panel = panels.get(root);
  if (panel === undefined) {
    panel = new SelectedFleetPanel(root);
    panels.set(root, panel);
  }
  panel.update(scenario, onChange, assets);
}
