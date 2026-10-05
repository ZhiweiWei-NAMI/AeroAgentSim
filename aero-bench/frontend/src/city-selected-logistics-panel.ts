/** Selected-city logistics authoring panel.
 *
 * This is the selected-OSM-city logistics panel: it authors a
 * CitySelectedLogistics v1 document bound to exactly one SelectedSceneDraft —
 * the same identity as the current CitySelectedScenario v2. It edits manual
 * order requests against the scenario's cargo-transfer facilities (vertiport and
 * hub; a charger can never carry cargo), user-declared fleet performance
 * profiles, algorithm choices and the deterministic order-generation
 * configuration.
 *
 * It is NOT the old scenePath city-algorithm-panel: nothing here is keyed by
 * scenePath, and the document carries no order status, evidence, trajectories,
 * dispatch or provider state. Orders and generated requests only declare
 * demand; saving never claims a Business Provider created orders or that
 * dispatch executed. Performance profiles are explicit operator-supplied values
 * that are never inferred from a GLB display size; an absent profile stays
 * absent until the operator fills every field.
 *
 * Every valid change is re-parsed with `parseCitySelectedLogistics` against the
 * current scenario and then committed through an async `onChange`. If the
 * caller rejects, the returned message is shown verbatim and the previously
 * displayed document is retained; if parsing fails, the error is shown and no
 * commit is attempted. A logistics document bound to a different selected city
 * can never be silently re-saved: it fails the same selected-scene check. */
import { sameSelectedSceneDraft, type SelectedSceneDraft } from "./city-selected-draft";
import type {
  CitySelectedLogistics, FleetPerformanceProfile, LogisticsAlgorithms, LogisticsOrderGeneration,
  LogisticsOrderRequest,
} from "./city-selected-logistics-draft";
import { facilityPermitsCargoTransfer, missingPerformanceProfiles, parseCitySelectedLogistics,
  parseCitySelectedLogisticsForRepair } from "./city-selected-logistics-draft";
import { generateSelectedOrderRequests } from "./city-selected-order-generator";
import type { DispatchPlan } from "./city-selected-dispatch";
import type {
  CitySelectedScenario, SelectedScenarioFacility, SelectedScenarioFleetEntry,
} from "./city-selected-scenario";

const KIND_LABELS: Record<SelectedScenarioFacility["kind"], string> = {
  vertiport: "起降点", hub: "物流中转站", charger: "充电站",
};

interface ManualOrderDraft {
  sourceFacilityId: string;
  destinationFacilityId: string;
  hubHandoffFacilityId: string | null;
  cargoKg: number;
  releaseAtS: number;
  deliverByS: number;
}

/** Add-profile form values kept as strings so an empty field stays visibly empty
 * and is only rejected at parse time — a partial profile is never committed. */
interface ProfileAddDraft {
  sourceLabel: string;
  provenance: string;
  xM: string;
  yM: string;
  zM: string;
  cruiseSpeedMps: string;
  cruisePowerW: string;
  hoverPowerW: string;
  chargeEfficiency: string;
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
    action(input.valueAsNumber);
  });
  wrapper.append(caption, input);
  return wrapper;
}

function textField(label: string, value: string, action: (value: string) => void): HTMLLabelElement {
  const wrapper = element("label", "studio-field");
  const caption = element("span", "studio-field-label", label);
  const input = element("input", "studio-input");
  input.type = "text";
  input.value = value;
  input.addEventListener("change", () => action(input.value));
  wrapper.append(caption, input);
  return wrapper;
}

/** Numeric field for the add-profile draft that preserves blank entries as an
 * empty string instead of coercing them to 0 before the operator fills them. */
function numericStringField(label: string, value: string, action: (value: string) => void): HTMLLabelElement {
  const wrapper = element("label", "studio-field");
  const caption = element("span", "studio-field-label", label);
  const input = element("input", "studio-input");
  input.type = "number";
  input.step = "any";
  input.value = value;
  input.addEventListener("change", () => action(input.value));
  wrapper.append(caption, input);
  return wrapper;
}

function selectField<T extends string>(label: string, value: T,
                                       options: readonly (readonly [T, string])[],
                                       action: (value: T) => void): HTMLLabelElement {
  const wrapper = element("label", "studio-field");
  const caption = element("span", "studio-field-label", label);
  const select = element("select", "studio-input");
  select.setAttribute("aria-label", label);
  for (const [optionValue, optionLabel] of options) select.append(new Option(optionLabel, optionValue));
  select.value = value;
  select.addEventListener("change", () => action(select.value as T));
  wrapper.append(caption, select);
  return wrapper;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function nextManualOrderId(current: readonly LogisticsOrderRequest[]): string {
  const known = new Set(current.map(order => order.id));
  let counter = 1;
  while (known.has(`order-${counter}`)) counter++;
  return `order-${counter}`;
}

class LogisticsPanel {
  private scenario: CitySelectedScenario | null = null;
  private logistics: CitySelectedLogistics | null = null;
  private onChange: ((next: CitySelectedLogistics) => Promise<string | null>) | null = null;
  private onPlan: (() => Promise<DispatchPlan>) | null = null;
  private plan: DispatchPlan | null = null;
  private planError: string | null = null;
  private planning = false;
  private revision = 0;
  private error: string | null = null;
  private busy = false;
  private addingOrder = false;
  private orderDraft: ManualOrderDraft | null = null;
  private profileAddingEntry: string | null = null;
  private profileDraft: ProfileAddDraft | null = null;
  private lastScene: SelectedSceneDraft | null = null;

  constructor(private readonly root: HTMLElement) {}

  update(scenario: CitySelectedScenario, logistics: CitySelectedLogistics,
         onChange: (next: CitySelectedLogistics) => Promise<string | null>,
         onPlan?: () => Promise<DispatchPlan>): void {
    const sceneChanged = this.lastScene === null
      || !sameSelectedSceneDraft(this.lastScene, scenario.selectedScene);
    if (sceneChanged) {
      this.error = null;
      this.addingOrder = false;
      this.orderDraft = null;
      this.profileAddingEntry = null;
      this.profileDraft = null;
    }
    this.scenario = scenario;
    this.revision++;
    this.planning = false;
    this.busy = false;
    this.logistics = logistics;
    this.onChange = onChange;
    this.onPlan = onPlan ?? null;
    this.plan = null;
    this.planError = null;
    this.lastScene = scenario.selectedScene;
    this.render();
  }

  /** Parse with the v1 contract against the current scenario, then commit.
   * `base` is the document the user acted on; it is restored (old state
   * retained) when the caller rejects. A document bound to another selected
   * city fails the parse and is never silently re-saved. */
  private async commit(next: CitySelectedLogistics, base: CitySelectedLogistics,
                       onAccepted?: () => void): Promise<void> {
    if (this.busy) return;
    const scenario = this.scenario;
    if (scenario === null) return;
    let parsed: CitySelectedLogistics;
    try {
      let repairing = false;
      try { parseCitySelectedLogistics(base, scenario); } catch { repairing = true; }
      parsed = repairing ? parseCitySelectedLogisticsForRepair(next, scenario)
        : parseCitySelectedLogistics(next, scenario);
    } catch (error) {
      this.fail(errorMessage(error));
      return;
    }
    const onChange = this.onChange;
    if (onChange === null) return;
    this.busy = true;
    const revision = this.revision;
    this.render();
    try {
      const message = await onChange(parsed);
      if (revision !== this.revision) return;
      if (message !== null) {
        this.logistics = base;
        this.error = message;
      } else {
        this.logistics = parsed;
        this.error = null;
        try { parseCitySelectedLogistics(parsed, scenario); }
        catch (error) { this.error = `修复进度已保存，仍需修正：${errorMessage(error)}`; }
        this.plan = null;
        this.planError = null;
        if (onAccepted !== undefined) onAccepted();
      }
    } catch (error) {
      if (revision !== this.revision) return;
      this.logistics = base;
      this.error = `保存失败：${errorMessage(error)}`;
    } finally {
      if (revision === this.revision) {
        this.busy = false;
        if (this.root.querySelector(".studio-logistics-panel") !== null) this.render();
      }
    }
  }

  private fail(message: string): void {
    this.error = message;
    this.render();
  }

  private async previewDispatch(): Promise<void> {
    if (this.planning || this.busy || this.onPlan === null) return;
    this.planning = true;
    this.planError = null;
    this.plan = null;
    this.render();
    const revision = this.revision;
    try {
      const plan = await this.onPlan();
      if (revision === this.revision) this.plan = plan;
    } catch (error) {
      if (revision === this.revision) this.planError = errorMessage(error);
    } finally {
      if (revision === this.revision) {
        this.planning = false;
        if (this.root.querySelector(".studio-logistics-panel") !== null) this.render();
      }
    }
  }

  private dispatchSection(): HTMLElement {
    const section = element("section", "studio-logistics-dispatch");
    section.append(element("h3", "studio-section-title", "派单规划预览"));
    section.append(element("p", "studio-help",
      "按机队配置声明初始假设：每架飞机在归属设施、满电且 0 秒可用。航路使用当前已验证城市的建筑、街道设施、起降面和禁飞区碰撞盒；结果仅是规划，不代表已派单或执行。"));
    section.append(button("预览派单规划", () => void this.previewDispatch(), "studio-button studio-button-primary"));
    if (this.planning) section.append(element("p", "studio-note", "正在校验当前城市并规划航路…"));
    if (this.planError !== null) {
      const error = element("p", "studio-note", this.planError);
      error.setAttribute("role", "alert"); section.append(error);
    }
    if (this.plan !== null) {
      section.append(element("p", "studio-note",
        `规划：${this.plan.acceptedAssignments.length} 单可分配，${this.plan.unassignedOrders.length} 单不可分配；${this.plan.plannedChargingStops.length} 次预定充电。仅供编辑预览。`));
      for (const assignment of this.plan.acceptedAssignments) section.append(element("p", "studio-source",
        `${assignment.orderId} → ${assignment.unitId}，预计 ${assignment.arrivalAtS.toFixed(1)} s 送达，余电 ${assignment.batteryAtArrivalWh.toFixed(1)} Wh`));
      for (const order of this.plan.unassignedOrders) section.append(element("p", "studio-source",
        `${order.orderId} 未分配：${order.primaryReason}；${order.perUnitFailures.map(failure => failure.message).join("；")}`));
    }
    return section;
  }

  private cargoFacilities(): SelectedScenarioFacility[] {
    const scenario = this.scenario;
    if (scenario === null) return [];
    return scenario.facilities.filter(facilityPermitsCargoTransfer);
  }

  private hubFacilities(): SelectedScenarioFacility[] {
    const scenario = this.scenario;
    if (scenario === null) return [];
    return scenario.facilities.filter(facility => facility.kind === "hub");
  }

  // ---- manual orders ------------------------------------------------------

  private openAddOrder(): void {
    const facilities = this.cargoFacilities();
    if (facilities.length < 2) {
      this.error = "至少需要两处可装卸货物设施（起降点/物流中转站）才能添加手动订单";
      this.render();
      return;
    }
    if (this.hubFacilities().length === 0) {
      this.error = "订单必须经过物流中转站交接货物，当前场景没有物流中转站";
      this.render();
      return;
    }
    this.error = null;
    this.addingOrder = true;
    this.orderDraft = {
      sourceFacilityId: facilities[0]!.id,
      destinationFacilityId: facilities[1]!.id,
      hubHandoffFacilityId: null,
      cargoKg: 1,
      releaseAtS: 0,
      deliverByS: 600,
    };
    this.render();
  }

  private async commitAddOrder(): Promise<void> {
    if (this.busy) return;
    const scenario = this.scenario;
    const logistics = this.logistics;
    const draft = this.orderDraft;
    if (scenario === null || logistics === null || draft === null) return;
    const order: LogisticsOrderRequest = {
      id: nextManualOrderId(logistics.orders),
      sourceFacilityId: draft.sourceFacilityId,
      destinationFacilityId: draft.destinationFacilityId,
      hubHandoffFacilityId: draft.hubHandoffFacilityId,
      cargoKg: draft.cargoKg,
      releaseAtS: draft.releaseAtS,
      deliverByS: draft.deliverByS,
    };
    const next: CitySelectedLogistics = { ...logistics, orders: [...logistics.orders, order] };
    await this.commit(next, logistics, () => {
      this.addingOrder = false;
      this.orderDraft = null;
    });
  }

  private updateOrder(order: LogisticsOrderRequest, patch: Partial<LogisticsOrderRequest>): void {
    const logistics = this.logistics;
    if (logistics === null) return;
    const next: LogisticsOrderRequest = { ...order, ...patch };
    this.commit({ ...logistics, orders: logistics.orders.map(item => item.id === order.id ? next : item) }, logistics);
  }

  private deleteOrder(order: LogisticsOrderRequest): void {
    const logistics = this.logistics;
    if (logistics === null) return;
    this.commit({ ...logistics, orders: logistics.orders.filter(item => item.id !== order.id) }, logistics);
  }

  private orderFacilityField(currentId: string, ariaLabel: string, action: (id: string) => void): HTMLLabelElement {
    const wrapper = element("label", "studio-field");
    const caption = element("span", "studio-field-label", ariaLabel);
    const select = element("select", "studio-input");
    select.setAttribute("aria-label", ariaLabel);
    for (const facility of this.cargoFacilities()) {
      select.append(new Option(`${KIND_LABELS[facility.kind]} · ${facility.name}`, facility.id));
    }
    select.value = currentId;
    select.addEventListener("change", () => action(select.value));
    wrapper.append(caption, select);
    return wrapper;
  }

  /** Hub cargo handoff selector: mandatory when neither endpoint is a hub. */
  private hubHandoffField(currentId: string | null, action: (id: string | null) => void): HTMLLabelElement {
    const wrapper = element("label", "studio-field");
    const caption = element("span", "studio-field-label", "物流中转站交接（必填当起终点非中转站）");
    const select = element("select", "studio-input");
    select.setAttribute("aria-label", "物流中转站交接");
    select.append(new Option("（无 · 起终点之一须为中转站）", ""));
    for (const facility of this.hubFacilities()) {
      select.append(new Option(`${KIND_LABELS[facility.kind]} · ${facility.name}`, facility.id));
    }
    select.value = currentId ?? "";
    select.addEventListener("change", () => action(select.value === "" ? null : select.value));
    wrapper.append(caption, select);
    return wrapper;
  }

  private orderCard(order: LogisticsOrderRequest): HTMLElement {
    const card = element("article", "studio-card");
    card.dataset.orderId = order.id;
    card.append(element("h4", "studio-card-title", `订单 ${order.id}`));
    card.append(element("p", "studio-source",
      "需求请求：只声明货物、释放与期限，不含订单状态、揽收或送达证据，不代表业务方已派单或已配送。"));
    card.append(element("p", "studio-source",
      "货物必须经由物流中转站交接：起终点之一为中转站，或另选中转站作为中途交接点；"
      + "禁止起降点直达起降点绕过中转站。"));
    const fields = element("div", "studio-field-grid");
    fields.append(
      this.orderFacilityField(order.sourceFacilityId, "来源设施", sourceFacilityId => {
        if (sourceFacilityId !== order.sourceFacilityId) this.updateOrder(order, { sourceFacilityId });
      }),
      this.orderFacilityField(order.destinationFacilityId, "目的设施", destinationFacilityId => {
        if (destinationFacilityId !== order.destinationFacilityId) this.updateOrder(order, { destinationFacilityId });
      }),
      this.hubHandoffField(order.hubHandoffFacilityId, hubHandoffFacilityId => {
        if (hubHandoffFacilityId !== order.hubHandoffFacilityId) this.updateOrder(order, { hubHandoffFacilityId });
      }),
      numericField("货物重量 / kg", order.cargoKg, cargoKg => this.updateOrder(order, { cargoKg }), { min: 0 }),
      numericField("释放时间 / s", order.releaseAtS, releaseAtS => this.updateOrder(order, { releaseAtS }), { min: 0 }),
      numericField("交付期限 / s", order.deliverByS, deliverByS => this.updateOrder(order, { deliverByS })),
    );
    card.append(fields);
    const handoff = order.hubHandoffFacilityId;
    const endpointHub = this.hubFacilities().some(facility =>
      facility.id === order.sourceFacilityId || facility.id === order.destinationFacilityId);
    if (handoff === null && !endpointHub) {
      const warning = element("p", "studio-note",
        "该订单起终点均非物流中转站且未选中途交接点，保存将被拒绝：货物不得绕过中转站。");
      warning.setAttribute("role", "alert");
      card.append(warning);
    }
    const usedHubs = new Set<string>([order.sourceFacilityId, order.destinationFacilityId].filter(id =>
      this.hubFacilities().some(facility => facility.id === id)));
    if (handoff !== null) usedHubs.add(handoff);
    for (const hub of this.hubFacilities().filter(facility => usedHubs.has(facility.id))) {
      if (hub.cargo !== null) {
        card.append(element("p", "studio-source",
          `${hub.name} 货站：存储 ${hub.cargo.storageCapacityKg} kg，处理 ${hub.cargo.throughputPerHourKg} kg/小时；`
          + `单笔货重 ${order.cargoKg} kg 不得超过存储容量。`));
      }
    }
    card.append(button("删除订单", () => this.deleteOrder(order), "studio-button studio-danger"));
    return card;
  }

  private orderAddForm(): HTMLElement {
    const draft = this.orderDraft;
    const form = element("section", "studio-card");
    form.dataset.orderAdd = "form";
    if (draft === null) return form;
    form.append(element("h4", "studio-card-title", "新增手动订单"));
    form.append(element("p", "studio-help",
      "起终点必须为允许货物交接的设施（起降点/物流中转站）且不得相同；充电站不参与货物交接。订单只声明需求，不含状态与揽收/送达字段。"));
    const fields = element("div", "studio-field-grid");
    const set = (patch: Partial<ManualOrderDraft>): void => {
      if (this.orderDraft !== null) Object.assign(this.orderDraft, patch);
    };
    fields.append(
      this.orderFacilityField(draft.sourceFacilityId, "来源设施", sourceFacilityId => set({ sourceFacilityId })),
      this.orderFacilityField(draft.destinationFacilityId, "目的设施", destinationFacilityId => set({ destinationFacilityId })),
      numericField("货物重量 / kg", draft.cargoKg, cargoKg => set({ cargoKg }), { min: 0 }),
      numericField("释放时间 / s", draft.releaseAtS, releaseAtS => set({ releaseAtS }), { min: 0 }),
      numericField("交付期限 / s", draft.deliverByS, deliverByS => set({ deliverByS })),
    );
    form.append(fields);
    const actions = element("div", "studio-row");
    actions.append(
      button("确认添加订单", () => void this.commitAddOrder(), "studio-button studio-button-primary"),
      button("取消", () => {
        this.addingOrder = false;
        this.orderDraft = null;
        this.error = null;
        this.render();
      }),
    );
    form.append(actions);
    return form;
  }

  private ordersSection(): HTMLElement {
    const section = element("section", "studio-logistics-orders");
    const logistics = this.logistics;
    if (logistics === null) return section;
    section.append(element("h3", "studio-section-title", `订单请求 (${logistics.orders.length})`));
    section.append(element("p", "studio-help",
      "手动录入与按配置生成的订单请求都只是需求声明：文档不含订单状态、揽收或送达证据，也不代表业务方已接受或已配送。"));
    if (logistics.orders.length === 0 && !this.addingOrder) {
      section.append(element("p", "studio-empty",
        "尚未录入任何订单请求。订单只声明需求；添加本身不创建业务订单，也不触发派单或配送。"));
    }
    for (const order of logistics.orders) section.append(this.orderCard(order));
    if (this.addingOrder) section.append(this.orderAddForm());
    if (!this.addingOrder) {
      const add = button("添加手动订单", () => this.openAddOrder(), "studio-button studio-button-primary");
      add.setAttribute("aria-label", "添加手动订单");
      add.title = "添加一条新的手动订单请求（需至少两处可装卸货物设施）";
      section.append(add);
    }
    return section;
  }

  // ---- performance profiles -----------------------------------------------

  private openAddProfile(entry: SelectedScenarioFleetEntry): void {
    this.error = null;
    this.profileAddingEntry = entry.id;
    this.profileDraft = {
      sourceLabel: "", provenance: "",
      xM: "", yM: "", zM: "",
      cruiseSpeedMps: "", cruisePowerW: "", hoverPowerW: "", chargeEfficiency: "",
    };
    this.render();
  }

  private async commitAddProfile(entry: SelectedScenarioFleetEntry): Promise<void> {
    if (this.busy) return;
    const logistics = this.logistics;
    const draft = this.profileDraft;
    if (logistics === null || draft === null) return;
    const profile: FleetPerformanceProfile = {
      fleetEntryId: entry.id,
      sourceLabel: draft.sourceLabel,
      provenance: draft.provenance,
      aircraftBody: { xM: Number(draft.xM), yM: Number(draft.yM), zM: Number(draft.zM) },
      cruiseSpeedMps: Number(draft.cruiseSpeedMps),
      cruisePowerW: Number(draft.cruisePowerW),
      hoverPowerW: Number(draft.hoverPowerW),
      chargeEfficiency: Number(draft.chargeEfficiency),
    };
    const next: CitySelectedLogistics = {
      ...logistics,
      performanceProfiles: [...logistics.performanceProfiles, profile],
    };
    await this.commit(next, logistics, () => {
      this.profileAddingEntry = null;
      this.profileDraft = null;
    });
  }

  private updateProfile(profile: FleetPerformanceProfile, patch: Partial<FleetPerformanceProfile>): void {
    const logistics = this.logistics;
    if (logistics === null) return;
    const next: FleetPerformanceProfile = { ...profile, ...patch };
    this.commit({
      ...logistics,
      performanceProfiles: logistics.performanceProfiles
        .map(item => item.fleetEntryId === profile.fleetEntryId ? next : item),
    }, logistics);
  }

  private deleteProfile(profile: FleetPerformanceProfile): void {
    const logistics = this.logistics;
    if (logistics === null) return;
    this.commit({
      ...logistics,
      performanceProfiles: logistics.performanceProfiles.filter(item => item.fleetEntryId !== profile.fleetEntryId),
    }, logistics);
  }

  private profileAddForm(entry: SelectedScenarioFleetEntry): HTMLElement {
    const draft = this.profileDraft;
    const container = element("div");
    if (draft === null) return container;
    const fields = element("div", "studio-field-grid");
    const set = (patch: Partial<ProfileAddDraft>): void => {
      if (this.profileDraft !== null) Object.assign(this.profileDraft, patch);
    };
    fields.append(
      textField("来源标签", draft.sourceLabel, sourceLabel => set({ sourceLabel })),
      textField("出处", draft.provenance, provenance => set({ provenance })),
      numericStringField("机体长度 x / m", draft.xM, xM => set({ xM })),
      numericStringField("机体高度 y / m", draft.yM, yM => set({ yM })),
      numericStringField("机体宽度 z / m", draft.zM, zM => set({ zM })),
      numericStringField("巡航速度 / (m/s)", draft.cruiseSpeedMps, cruiseSpeedMps => set({ cruiseSpeedMps })),
      numericStringField("巡航功率 / W", draft.cruisePowerW, cruisePowerW => set({ cruisePowerW })),
      numericStringField("悬停功率 / W", draft.hoverPowerW, hoverPowerW => set({ hoverPowerW })),
      numericStringField("充电效率 / 0–1", draft.chargeEfficiency, chargeEfficiency => set({ chargeEfficiency })),
    );
    container.append(fields);
    const actions = element("div", "studio-row");
    actions.append(
      button("确认添加性能档案", () => void this.commitAddProfile(entry), "studio-button studio-button-primary"),
      button("取消", () => {
        this.profileAddingEntry = null;
        this.profileDraft = null;
        this.error = null;
        this.render();
      }),
    );
    container.append(actions);
    return container;
  }

  private profileEntryCard(entry: SelectedScenarioFleetEntry): HTMLElement {
    const card = element("article", "studio-card");
    card.dataset.fleetEntryId = entry.id;
    const logistics = this.logistics;
    const profile = logistics?.performanceProfiles
      .find(item => item.fleetEntryId === entry.id) ?? null;
    card.append(element("h4", "studio-card-title",
      profile === null ? `机队条目 ${entry.id}` : `机队条目 ${entry.id} · 已建档`));
    if (profile === null) {
      card.append(element("p", "studio-note",
        `该机队条目尚无性能档案。档案需由操作员逐项填写来源、出处、机体尺寸与动力参数，缺任一项都保持未建档；面板不会从模型 GLB 显示尺寸推断任何数值。`));
      if (this.profileAddingEntry === entry.id && this.profileDraft !== null) {
        card.append(this.profileAddForm(entry));
      } else {
        card.append(button("添加性能档案", () => this.openAddProfile(entry)));
      }
      return card;
    }
    const fields = element("div", "studio-field-grid");
    fields.append(
      textField("来源标签", profile.sourceLabel, sourceLabel => this.updateProfile(profile, { sourceLabel })),
      textField("出处", profile.provenance, provenance => this.updateProfile(profile, { provenance })),
      numericField("机体长度 x / m", profile.aircraftBody.xM, xM => {
        this.updateProfile(profile, { aircraftBody: { ...profile.aircraftBody, xM } });
      }, { min: 0 }),
      numericField("机体高度 y / m", profile.aircraftBody.yM, yM => {
        this.updateProfile(profile, { aircraftBody: { ...profile.aircraftBody, yM } });
      }, { min: 0 }),
      numericField("机体宽度 z / m", profile.aircraftBody.zM, zM => {
        this.updateProfile(profile, { aircraftBody: { ...profile.aircraftBody, zM } });
      }, { min: 0 }),
      numericField("巡航速度 / (m/s)", profile.cruiseSpeedMps, cruiseSpeedMps => {
        this.updateProfile(profile, { cruiseSpeedMps });
      }, { min: 0 }),
      numericField("巡航功率 / W", profile.cruisePowerW, cruisePowerW => {
        this.updateProfile(profile, { cruisePowerW });
      }, { min: 0 }),
      numericField("悬停功率 / W", profile.hoverPowerW, hoverPowerW => {
        this.updateProfile(profile, { hoverPowerW });
      }, { min: 0 }),
      numericField("充电效率 / 0–1", profile.chargeEfficiency, chargeEfficiency => {
        this.updateProfile(profile, { chargeEfficiency });
      }, { min: 0, max: 1, step: "0.05" }),
    );
    card.append(fields);
    card.append(button("删除性能档案", () => this.deleteProfile(profile), "studio-button studio-danger"));
    return card;
  }

  private missingProfilesNotice(): HTMLElement | null {
    const scenario = this.scenario;
    const logistics = this.logistics;
    if (scenario === null || logistics === null) return null;
    const missing = missingPerformanceProfiles(logistics, scenario);
    if (missing.length === 0) return null;
    const note = element("p", "studio-note", `以下机队条目尚无性能档案，规划前需补全：${missing.join("、")}`);
    note.setAttribute("role", "status");
    return note;
  }

  private profilesSection(): HTMLElement {
    const section = element("section", "studio-logistics-profiles");
    const scenario = this.scenario;
    const logistics = this.logistics;
    if (scenario === null || logistics === null) return section;
    section.append(element("h3", "studio-section-title",
      `机队性能档案 (${logistics.performanceProfiles.length}/${scenario.fleet.length})`));
    section.append(element("p", "studio-help",
      "性能档案是操作员声明的估算值，不来自模型 GLB 显示尺寸，也不代表适航或续航已经过验证。"));
    for (const entry of scenario.fleet) section.append(this.profileEntryCard(entry));
    const currentFleet = new Set(scenario.fleet.map(entry => entry.id));
    for (const profile of logistics.performanceProfiles.filter(item => !currentFleet.has(item.fleetEntryId))) {
      const card = element("article", "studio-card");
      card.append(element("h4", "studio-card-title", `失效性能档案 ${profile.fleetEntryId}`));
      card.append(element("p", "studio-note", "该机队条目已被删除；可删除此档案后保存其余物流配置。"));
      card.append(button("删除性能档案", () => this.deleteProfile(profile), "studio-button studio-danger"));
      section.append(card);
    }
    return section;
  }

  // ---- algorithms ---------------------------------------------------------

  private updateAlgorithms(patch: Partial<LogisticsAlgorithms>): void {
    const logistics = this.logistics;
    if (logistics === null) return;
    this.commit({ ...logistics, algorithms: { ...logistics.algorithms, ...patch } }, logistics);
  }

  private externalImageField(current: string | null): HTMLLabelElement {
    const wrapper = element("label", "studio-field");
    const caption = element("span", "studio-field-label", "外部算法镜像引用");
    const input = element("input", "studio-input");
    input.type = "text";
    input.value = current ?? "";
    input.placeholder = "registry.example/agent@sha256:<64 位小写十六进制>";
    input.addEventListener("change", () => {
      const next = input.value.trim();
      this.updateAlgorithms({ externalImageRef: next === "" ? null : next });
    });
    wrapper.append(caption, input);
    return wrapper;
  }

  private parametersField(parameters: Readonly<Record<string, string | number | boolean>>): HTMLLabelElement {
    const wrapper = element("label", "studio-field");
    const caption = element("span", "studio-field-label", "算法参数 JSON");
    const textarea = element("textarea", "studio-input");
    textarea.setAttribute("aria-label", "算法参数 JSON");
    textarea.rows = 5;
    textarea.spellcheck = false;
    textarea.value = JSON.stringify(parameters, null, 2);
    textarea.addEventListener("change", () => {
      let parsed: Record<string, string | number | boolean>;
      try {
        parsed = JSON.parse(textarea.value);
      } catch (error) {
        this.fail(`算法参数必须是有效 JSON：${errorMessage(error)}`);
        return;
      }
      this.updateAlgorithms({ parameters: parsed });
    });
    wrapper.append(caption, textarea);
    return wrapper;
  }

  private algorithmsSection(): HTMLElement {
    const section = element("section", "studio-logistics-algorithms");
    const logistics = this.logistics;
    if (logistics === null) return section;
    section.append(element("h3", "studio-section-title", "调度与算法选择"));
    section.append(element("p", "studio-help",
      "内置选项只是规划模板，尚未部署；选择任意外部选项时，“外部算法镜像引用”必填，且必须是带 @sha256: 固定摘要的 OCI 引用。本文档不含部署或执行器字段。"));
    const grid = element("div", "studio-field-grid");
    const algorithms = logistics.algorithms;
    grid.append(
      selectField("调度模式", algorithms.mode, [
        ["centralized", "中心式调度"],
        ["distributed", "分布式协商"],
      ] as const, mode => {
        if (mode !== algorithms.mode) this.updateAlgorithms({ mode });
      }),
      selectField("订单分配", algorithms.assignment, [
        ["nearest_feasible", "最近可行分配 · 内置规划模板"],
        ["sealed_bid", "密封竞价 · 内置规划模板"],
        ["external", "外部分配 · 需固定 OCI 引用"],
      ] as const, assignment => {
        if (assignment !== algorithms.assignment) this.updateAlgorithms({ assignment });
      }),
      selectField("航路规划", algorithms.routing, [
        ["grid_astar", "网格 A* · 内置规划模板"],
        ["external", "外部航路 · 需固定 OCI 引用"],
      ] as const, routing => {
        if (routing !== algorithms.routing) this.updateAlgorithms({ routing });
      }),
      selectField("能源策略", algorithms.charging, [
        ["reserve_threshold", "预留电量阈值 · 内置规划模板"],
        ["external", "外部能源 · 需固定 OCI 引用"],
      ] as const, charging => {
        if (charging !== algorithms.charging) this.updateAlgorithms({ charging });
      }),
    );
    section.append(grid);
    const ref = element("div", "studio-field-grid");
    ref.append(this.externalImageField(algorithms.externalImageRef));
    section.append(ref);
    section.append(element("p", "studio-note",
      "若任一算法选择为“外部”，上面引用必填；否则可留空。外部镜像的存在性、内容与运行权限不在本文档校验范围内。"));
    const params = element("div", "studio-field-grid");
    params.append(this.parametersField(algorithms.parameters));
    section.append(params);
    section.append(element("p", "studio-note",
      "预览航路时必须在算法参数 JSON 中设置正数 cruiseAltitudeM（米），例如 {\"cruiseAltitudeM\":120}。高度由操作员声明，不从建筑或模型自动猜测。"));
    return section;
  }

  // ---- order generation ---------------------------------------------------

  private updateGeneration(patch: Partial<LogisticsOrderGeneration>): void {
    const logistics = this.logistics;
    if (logistics === null) return;
    this.commit({ ...logistics, orderGeneration: { ...logistics.orderGeneration, ...patch } }, logistics);
  }

  private async appendGeneratedOrders(): Promise<void> {
    if (this.busy) return;
    const scenario = this.scenario;
    const logistics = this.logistics;
    if (scenario === null || logistics === null) return;
    let generated: ReturnType<typeof generateSelectedOrderRequests>;
    try {
      generated = generateSelectedOrderRequests(scenario, logistics.orderGeneration);
    } catch (error) {
      this.fail(errorMessage(error));
      return;
    }
    if (generated.length === 0) {
      this.fail("最大订单数为 0，未生成任何订单请求。");
      return;
    }
    const existing = new Set(logistics.orders.map(order => order.id));
    const conflicting = generated.find(order => existing.has(order.id));
    if (conflicting !== undefined) {
      this.fail(`生成订单请求与现有订单 ID 冲突：${conflicting.id}；请调整随机种子或先删除同名订单。`);
      return;
    }
    await this.commit({ ...logistics, orders: [...logistics.orders, ...generated] }, logistics);
  }

  private orderGenerationSection(): HTMLElement {
    const section = element("section", "studio-logistics-generation");
    const logistics = this.logistics;
    if (logistics === null) return section;
    section.append(element("h3", "studio-section-title", "订单生成配置"));
    section.append(element("p", "studio-help",
      "种子、数量、时间窗口、货重区间与期限超前只描述输入需求；点击“生成订单请求”只构造确定性的需求请求并追加到订单列表，不创建业务订单、不派单、不执行。"));
    const generation = logistics.orderGeneration;
    const fields = element("div", "studio-field-grid");
    fields.append(
      numericField("随机种子 seed", generation.seed, seed => {
        if (seed !== generation.seed) this.updateGeneration({ seed });
      }, { min: 0, step: "1" }),
      numericField("最大订单数", generation.maxOrders, maxOrders => {
        if (maxOrders !== generation.maxOrders) this.updateGeneration({ maxOrders });
      }, { min: 0, step: "1" }),
      numericField("生成开始时间 / s", generation.startAtS, startAtS => {
        if (startAtS !== generation.startAtS) this.updateGeneration({ startAtS });
      }, { min: 0 }),
      numericField("生成结束时间 / s", generation.endAtS, endAtS => {
        if (endAtS !== generation.endAtS) this.updateGeneration({ endAtS });
      }),
      numericField("最小货物重量 / kg", generation.cargoMinKg, cargoMinKg => {
        if (cargoMinKg !== generation.cargoMinKg) this.updateGeneration({ cargoMinKg });
      }, { min: 0 }),
      numericField("最大货物重量 / kg", generation.cargoMaxKg, cargoMaxKg => {
        if (cargoMaxKg !== generation.cargoMaxKg) this.updateGeneration({ cargoMaxKg });
      }, { min: 0 }),
      numericField("交付期限超前 / s", generation.deadlineLeadS, deadlineLeadS => {
        if (deadlineLeadS !== generation.deadlineLeadS) this.updateGeneration({ deadlineLeadS });
      }, { min: 0 }),
    );
    section.append(fields);
    const actions = element("div", "studio-row");
    const generate = button("生成订单请求", () => void this.appendGeneratedOrders(), "studio-button studio-button-primary");
    generate.setAttribute("aria-label", "生成订单请求");
    generate.title = "按当前配置生成确定性订单请求并追加（ID 冲突时拒绝提交）";
    actions.append(generate);
    section.append(actions);
    return section;
  }

  // ---- render -------------------------------------------------------------

  private render(): void {
    const scenario = this.scenario;
    const logistics = this.logistics;
    if (scenario === null || logistics === null) return;
    const panel = element("section", "studio-logistics-panel");
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "选城物流撰写");
    panel.append(element("h2", "studio-panel-title", "物流订单与算法"));
    panel.append(element("p", "studio-help",
      "本面板只面向当前选城场景撰写物流需求、性能档案与算法选择：全部为不可执行的作者输入。保存不创建订单、不派单、不执行仿真或配送；任何编辑都以当前场景为唯一引用，不能把外来城市的订单静默复用过来。"));
    if (this.error !== null) {
      const alert = element("p", "studio-note", this.error);
      alert.setAttribute("role", "alert");
      panel.append(alert);
    }
    const missing = this.missingProfilesNotice();
    if (missing !== null) panel.append(missing);
    panel.append(this.ordersSection(), this.profilesSection(), this.algorithmsSection(),
      this.orderGenerationSection());
    if (this.onPlan !== null) panel.append(this.dispatchSection());
    if (this.busy || this.planning) {
      panel.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement>(
        "input, select, textarea, button")
        .forEach(control => { control.disabled = true; });
    }
    this.root.replaceChildren(panel);
  }
}

const panels = new WeakMap<HTMLElement, LogisticsPanel>();

/** Render (or update) the selected-city logistics authoring panel.
 * Every valid edit commits through `onChange` synchronously-ordered per input;
 * a non-null returned message is shown verbatim and the previous document is
 * restored. */
export function renderSelectedLogisticsPanel(root: HTMLElement, scenario: CitySelectedScenario,
                                             logistics: CitySelectedLogistics,
                                             onChange: (next: CitySelectedLogistics) => Promise<string | null>,
                                             onPlan?: () => Promise<DispatchPlan>): void {
  let panel = panels.get(root);
  if (panel === undefined) {
    panel = new LogisticsPanel(root);
    panels.set(root, panel);
  }
  panel.update(scenario, logistics, onChange, onPlan);
}
