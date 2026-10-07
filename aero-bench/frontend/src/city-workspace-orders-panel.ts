/** Workspace-v3 order authoring UI.
 *
 * This module deliberately depends only on the three existing logistics value
 * shapes, not on CityWorkspaceConfig. The v3 owner can therefore wire it after
 * the schema lands without creating a second order contract. Validation here is
 * structural and referential authoring validation. It does not claim payload,
 * endurance, routing, dispatch, charging, or Provider feasibility. */
import type {
  FleetPerformanceProfile,
  LogisticsOrderGeneration,
  LogisticsOrderRequest,
} from "./city-selected-logistics-draft";

export interface CityWorkspaceOrdersFields {
  readonly orders: LogisticsOrderRequest[];
  readonly orderGeneration: LogisticsOrderGeneration;
  readonly performanceProfiles: FleetPerformanceProfile[];
}

export interface CityWorkspaceOrdersContext extends CityWorkspaceOrdersFields {
  readonly facilities: readonly {
    readonly id: string;
    readonly name: string;
    readonly kind: "vertiport" | "hub" | "charger";
  }[];
  readonly fleet: readonly {
    readonly id: string;
    readonly assetId: string;
    readonly count: number;
  }[];
}

type FacilityReference = CityWorkspaceOrdersContext["facilities"][number];
type FleetReference = CityWorkspaceOrdersContext["fleet"][number];

const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/;
const MAX_GENERATED_ORDERS = 10_000;

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label}格式无效`);
  }
  return value as Record<string, unknown>;
}

function exactObject(
  value: unknown,
  keys: readonly string[],
  label: string,
): Record<string, unknown> {
  const result = object(value, label);
  if (
    Object.keys(result).length !== keys.length ||
    keys.some((key) => !Object.hasOwn(result, key))
  ) {
    throw new Error(`${label}字段不符合协议`);
  }
  return result;
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label}必须是数组`);
  return value;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`${label}不能为空`);
  }
  return value;
}

function identifier(value: unknown, label: string): string {
  const result = text(value, label);
  if (!ID.test(result)) throw new Error(`${label}无效`);
  return result;
}

function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label}必须是有限数值`);
  }
  return value;
}

function positive(value: unknown, label: string): number {
  const result = finite(value, label);
  if (result <= 0) throw new Error(`${label}必须大于 0`);
  return result;
}

function nonNegative(value: unknown, label: string): number {
  const result = finite(value, label);
  if (result < 0) throw new Error(`${label}不能为负`);
  return result;
}

function nonNegativeInteger(value: unknown, label: string): number {
  const result = finite(value, label);
  if (!Number.isSafeInteger(result) || result < 0) {
    throw new Error(`${label}必须是非负整数`);
  }
  return result;
}

function parseFacilities(value: unknown): FacilityReference[] {
  const ids = new Set<string>();
  return array(value, "设施引用").map((raw, index) => {
    // Workspace facilities contain more geometry/capability fields. Only the
    // reference projection is consumed here, so additional workspace fields are
    // intentionally ignored rather than copied into the order contract.
    const item = object(raw, `设施引用 ${index + 1} `);
    const id = identifier(item.id, `设施引用 ${index + 1} ID `);
    if (ids.has(id)) throw new Error(`设施引用 ID 重复：${id}`);
    ids.add(id);
    if (
      item.kind !== "vertiport" &&
      item.kind !== "hub" &&
      item.kind !== "charger"
    ) {
      throw new Error(`设施 ${id} 类型无效`);
    }
    return { id, name: text(item.name, `设施 ${id} 名称 `), kind: item.kind };
  });
}

function parseFleet(value: unknown): FleetReference[] {
  const ids = new Set<string>();
  return array(value, "机队引用").map((raw, index) => {
    const item = object(raw, `机队引用 ${index + 1} `);
    const id = identifier(item.id, `机队引用 ${index + 1} ID `);
    if (ids.has(id)) throw new Error(`机队引用 ID 重复：${id}`);
    ids.add(id);
    const count = finite(item.count, `机队 ${id} 数量 `);
    if (!Number.isSafeInteger(count) || count < 1) {
      throw new Error(`机队 ${id} 数量必须是正整数`);
    }
    return { id, assetId: text(item.assetId, `机队 ${id} 资产 ID `), count };
  });
}

function parseOrder(
  value: unknown,
  facilities: ReadonlyMap<string, FacilityReference>,
): LogisticsOrderRequest {
  const item = exactObject(
    value,
    [
      "id",
      "sourceFacilityId",
      "destinationFacilityId",
      "hubHandoffFacilityId",
      "cargoKg",
      "releaseAtS",
      "deliverByS",
    ],
    "订单请求",
  );
  const order: LogisticsOrderRequest = {
    id: identifier(item.id, "订单 ID "),
    sourceFacilityId: identifier(item.sourceFacilityId, "订单来源设施 ID "),
    destinationFacilityId: identifier(
      item.destinationFacilityId,
      "订单目的设施 ID ",
    ),
    hubHandoffFacilityId:
      item.hubHandoffFacilityId === null
        ? null
        : identifier(item.hubHandoffFacilityId, "订单中转站设施 ID "),
    cargoKg: positive(item.cargoKg, "订单货物重量 "),
    releaseAtS: nonNegative(item.releaseAtS, "订单释放时间 "),
    deliverByS: finite(item.deliverByS, "订单交付期限 "),
  };
  if (order.sourceFacilityId === order.destinationFacilityId) {
    throw new Error(`订单 ${order.id} 起终点必须不同`);
  }
  if (order.deliverByS <= order.releaseAtS) {
    throw new Error(`订单 ${order.id} 的交付期限必须晚于释放时间`);
  }
  const source = facilities.get(order.sourceFacilityId);
  const destination = facilities.get(order.destinationFacilityId);
  if (source === undefined) {
    throw new Error(
      `订单 ${order.id} 引用未知来源设施：${order.sourceFacilityId}`,
    );
  }
  if (destination === undefined) {
    throw new Error(
      `订单 ${order.id} 引用未知目的设施：${order.destinationFacilityId}`,
    );
  }
  if (source.kind === "charger") {
    throw new Error(`订单 ${order.id} 的来源设施不允许货物交接`);
  }
  if (destination.kind === "charger") {
    throw new Error(`订单 ${order.id} 的目的设施不允许货物交接`);
  }
  if (order.hubHandoffFacilityId !== null) {
    if (
      order.hubHandoffFacilityId === order.sourceFacilityId ||
      order.hubHandoffFacilityId === order.destinationFacilityId
    ) {
      throw new Error(`订单 ${order.id} 的中转站必须与起终点不同`);
    }
    const handoff = facilities.get(order.hubHandoffFacilityId);
    if (handoff === undefined) {
      throw new Error(
        `订单 ${order.id} 引用未知中转站设施：${order.hubHandoffFacilityId}`,
      );
    }
    if (handoff.kind !== "hub") {
      throw new Error(`订单 ${order.id} 的中转站必须是物流中转站`);
    }
  } else if (source.kind !== "hub" && destination.kind !== "hub") {
    throw new Error(`订单 ${order.id} 缺少物流中转站交接`);
  }
  return order;
}

function parseGeneration(value: unknown): LogisticsOrderGeneration {
  const item = exactObject(
    value,
    [
      "seed",
      "maxOrders",
      "startAtS",
      "endAtS",
      "cargoMinKg",
      "cargoMaxKg",
      "deadlineLeadS",
    ],
    "订单生成配置",
  );
  const maxOrders = nonNegativeInteger(item.maxOrders, "最大订单数 ");
  if (maxOrders > MAX_GENERATED_ORDERS) {
    throw new Error("最大订单数不得超过 10,000");
  }
  const generation: LogisticsOrderGeneration = {
    seed: nonNegativeInteger(item.seed, "随机种子 "),
    maxOrders,
    startAtS: nonNegative(item.startAtS, "生成开始时间 "),
    endAtS: finite(item.endAtS, "生成结束时间 "),
    cargoMinKg: positive(item.cargoMinKg, "最小货物重量 "),
    cargoMaxKg: finite(item.cargoMaxKg, "最大货物重量 "),
    deadlineLeadS: positive(item.deadlineLeadS, "交付期限超前 "),
  };
  if (generation.endAtS <= generation.startAtS) {
    throw new Error("生成结束时间必须晚于开始时间");
  }
  if (generation.cargoMaxKg < generation.cargoMinKg) {
    throw new Error("最大货物重量不得小于最小货物重量");
  }
  return generation;
}

function parseProfile(
  value: unknown,
  fleetIds: ReadonlySet<string>,
): FleetPerformanceProfile {
  const item = exactObject(
    value,
    [
      "fleetEntryId",
      "sourceLabel",
      "provenance",
      "aircraftBody",
      "cruiseSpeedMps",
      "cruisePowerW",
      "hoverPowerW",
      "chargeEfficiency",
    ],
    "机队性能档案",
  );
  const fleetEntryId = identifier(item.fleetEntryId, "性能档案机队 ID ");
  if (!fleetIds.has(fleetEntryId)) {
    throw new Error(`性能档案引用未知机队条目：${fleetEntryId}`);
  }
  const sourceLabel = text(item.sourceLabel, "性能档案来源标签 ");
  const provenance = text(item.provenance, "性能档案出处 ");
  const body = exactObject(item.aircraftBody, ["xM", "yM", "zM"], "机体尺寸");
  const chargeEfficiency = finite(item.chargeEfficiency, "充电效率 ");
  if (chargeEfficiency <= 0 || chargeEfficiency > 1) {
    throw new Error("充电效率必须在 (0,1] 之间");
  }
  return {
    fleetEntryId,
    sourceLabel,
    provenance,
    aircraftBody: {
      xM: positive(body.xM, "机体长度 "),
      yM: positive(body.yM, "机体高度 "),
      zM: positive(body.zM, "机体宽度 "),
    },
    cruiseSpeedMps: positive(item.cruiseSpeedMps, "巡航速度 "),
    cruisePowerW: positive(item.cruisePowerW, "巡航功率 "),
    hoverPowerW: positive(item.hoverPowerW, "悬停功率 "),
    chargeEfficiency,
  };
}

/** Parse only the editor's required projection. Extra workspace fields remain
 * outside this component and are neither copied nor interpreted. */
export function parseCityWorkspaceOrdersContext(
  value: unknown,
): CityWorkspaceOrdersContext {
  const root = object(value, "工作区订单上下文");
  for (const key of [
    "orders",
    "orderGeneration",
    "performanceProfiles",
    "facilities",
    "fleet",
  ]) {
    if (!Object.hasOwn(root, key))
      throw new Error(`工作区订单上下文缺少 ${key}`);
  }
  const facilities = parseFacilities(root.facilities);
  const fleet = parseFleet(root.fleet);
  const facilityById = new Map(facilities.map((entry) => [entry.id, entry]));
  const fleetIds = new Set(fleet.map((entry) => entry.id));
  const orderIds = new Set<string>();
  const orders = array(root.orders, "订单列表").map((raw) => {
    const order = parseOrder(raw, facilityById);
    if (orderIds.has(order.id)) throw new Error(`订单 ID 重复：${order.id}`);
    orderIds.add(order.id);
    return order;
  });
  const profileIds = new Set<string>();
  const performanceProfiles = array(
    root.performanceProfiles,
    "性能档案列表",
  ).map((raw) => {
    const profile = parseProfile(raw, fleetIds);
    if (profileIds.has(profile.fleetEntryId)) {
      throw new Error(`性能档案机队条目重复：${profile.fleetEntryId}`);
    }
    profileIds.add(profile.fleetEntryId);
    return profile;
  });
  return {
    orders,
    orderGeneration: parseGeneration(root.orderGeneration),
    performanceProfiles,
    facilities,
    fleet,
  };
}

function fieldsOf(
  context: CityWorkspaceOrdersContext,
): CityWorkspaceOrdersFields {
  return {
    orders: context.orders.map((order) => ({ ...order })),
    orderGeneration: { ...context.orderGeneration },
    performanceProfiles: context.performanceProfiles.map((profile) => ({
      ...profile,
      aircraftBody: { ...profile.aircraftBody },
    })),
  };
}

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  content?: string,
): HTMLElementTagNameMap[K] {
  const result = document.createElement(tag);
  if (className !== undefined) result.className = className;
  if (content !== undefined) result.textContent = content;
  return result;
}

function button(
  label: string,
  action: () => void,
  className = "studio-button",
): HTMLButtonElement {
  const result = element("button", className, label);
  result.type = "button";
  result.addEventListener("click", action);
  return result;
}

function field(label: string, control: HTMLElement): HTMLLabelElement {
  const result = element("label", "studio-field");
  result.append(element("span", "studio-field-label", label), control);
  return result;
}

function textInput(
  label: string,
  value: string,
  action: (value: string) => void,
  readOnly = false,
): HTMLLabelElement {
  const input = element("input", "studio-input");
  input.type = "text";
  input.value = value;
  input.readOnly = readOnly;
  input.setAttribute("aria-label", label);
  if (!readOnly) input.addEventListener("input", () => action(input.value));
  return field(label, input);
}

function numberInput(
  label: string,
  value: string,
  action: (value: string) => void,
  options: {
    readonly min?: number;
    readonly max?: number;
    readonly step?: string;
  } = {},
): HTMLLabelElement {
  const input = element("input", "studio-input");
  input.type = "number";
  input.value = value;
  input.step = options.step ?? "any";
  if (options.min !== undefined) input.min = String(options.min);
  if (options.max !== undefined) input.max = String(options.max);
  input.setAttribute("aria-label", label);
  input.addEventListener("input", () => action(input.value));
  return field(label, input);
}

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function draftNumber(value: string): number {
  return value.trim() === "" ? Number.NaN : Number(value);
}

interface OrderDraft {
  id: string;
  sourceFacilityId: string;
  destinationFacilityId: string;
  hubHandoffFacilityId: string;
  cargoKg: string;
  releaseAtS: string;
  deliverByS: string;
}

interface ProfileDraft {
  fleetEntryId: string;
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

type GenerationDraft = Record<keyof LogisticsOrderGeneration, string>;

function orderDraft(order?: LogisticsOrderRequest): OrderDraft {
  return {
    id: order?.id ?? "",
    sourceFacilityId: order?.sourceFacilityId ?? "",
    destinationFacilityId: order?.destinationFacilityId ?? "",
    hubHandoffFacilityId: order?.hubHandoffFacilityId ?? "",
    cargoKg: order === undefined ? "" : String(order.cargoKg),
    releaseAtS: order === undefined ? "" : String(order.releaseAtS),
    deliverByS: order === undefined ? "" : String(order.deliverByS),
  };
}

function profileDraft(
  fleetEntryId: string,
  profile?: FleetPerformanceProfile,
): ProfileDraft {
  return {
    fleetEntryId,
    sourceLabel: profile?.sourceLabel ?? "",
    provenance: profile?.provenance ?? "",
    xM: profile === undefined ? "" : String(profile.aircraftBody.xM),
    yM: profile === undefined ? "" : String(profile.aircraftBody.yM),
    zM: profile === undefined ? "" : String(profile.aircraftBody.zM),
    cruiseSpeedMps: profile === undefined ? "" : String(profile.cruiseSpeedMps),
    cruisePowerW: profile === undefined ? "" : String(profile.cruisePowerW),
    hoverPowerW: profile === undefined ? "" : String(profile.hoverPowerW),
    chargeEfficiency:
      profile === undefined ? "" : String(profile.chargeEfficiency),
  };
}

function generationDraft(value: LogisticsOrderGeneration): GenerationDraft {
  return {
    seed: String(value.seed),
    maxOrders: String(value.maxOrders),
    startAtS: String(value.startAtS),
    endAtS: String(value.endAtS),
    cargoMinKg: String(value.cargoMinKg),
    cargoMaxKg: String(value.cargoMaxKg),
    deadlineLeadS: String(value.deadlineLeadS),
  };
}

class CityWorkspaceOrdersPanel {
  private context: CityWorkspaceOrdersContext | null = null;
  private onChange: ((next: CityWorkspaceOrdersFields) => void) | null = null;
  private fingerprint = "";
  private blockingError: string | null = null;
  private error: string | null = null;
  private activeOrderId: string | null | undefined;
  private orderEditor: OrderDraft | null = null;
  private activeProfileId: string | null = null;
  private profileEditor: ProfileDraft | null = null;
  private generationEditor: GenerationDraft | null = null;

  constructor(private readonly root: HTMLElement) {}

  update(
    raw: CityWorkspaceOrdersContext,
    onChange: (next: CityWorkspaceOrdersFields) => void,
  ): void {
    this.onChange = onChange;
    try {
      const parsed = parseCityWorkspaceOrdersContext(raw);
      const nextFingerprint = JSON.stringify(parsed);
      if (nextFingerprint !== this.fingerprint) {
        this.context = parsed;
        this.fingerprint = nextFingerprint;
        this.closeEditors();
      } else {
        this.context = parsed;
      }
      this.blockingError = null;
    } catch (cause) {
      this.context = null;
      this.fingerprint = "";
      this.closeEditors();
      this.blockingError = errorMessage(cause);
    }
    this.render();
  }

  private closeEditors(): void {
    this.error = null;
    this.activeOrderId = undefined;
    this.orderEditor = null;
    this.activeProfileId = null;
    this.profileEditor = null;
    this.generationEditor = null;
  }

  private fail(cause: unknown): void {
    this.error = errorMessage(cause);
    this.render();
  }

  private commit(fields: CityWorkspaceOrdersFields, close: () => void): void {
    const context = this.context;
    const onChange = this.onChange;
    if (context === null || onChange === null) return;
    try {
      const parsed = parseCityWorkspaceOrdersContext({
        ...context,
        ...fields,
      });
      const next = fieldsOf(parsed);
      onChange(next);
      this.context = parsed;
      this.fingerprint = JSON.stringify(parsed);
      this.error = null;
      close();
      this.render();
    } catch (cause) {
      this.fail(cause);
    }
  }

  private cargoFacilities(): readonly FacilityReference[] {
    return (
      this.context?.facilities.filter(
        (facility) => facility.kind !== "charger",
      ) ?? []
    );
  }

  private facilitySelect(
    label: string,
    value: string,
    choices: readonly FacilityReference[],
    action: (value: string) => void,
    optional = false,
  ): HTMLLabelElement {
    const select = element("select", "studio-input");
    select.setAttribute("aria-label", label);
    select.append(new Option(optional ? "（无）" : "（请选择）", ""));
    for (const choice of choices) {
      const kind = choice.kind === "hub" ? "物流中转站" : "起降点";
      select.append(new Option(`${kind} · ${choice.name}`, choice.id));
    }
    select.value = value;
    select.addEventListener("change", () => action(select.value));
    return field(label, select);
  }

  private beginOrder(order?: LogisticsOrderRequest): void {
    this.error = null;
    this.activeOrderId = order?.id ?? null;
    this.orderEditor = orderDraft(order);
    this.render();
  }

  private saveOrder(): void {
    const context = this.context;
    const draft = this.orderEditor;
    if (context === null || draft === null || this.activeOrderId === undefined)
      return;
    const candidate: LogisticsOrderRequest = {
      id: draft.id,
      sourceFacilityId: draft.sourceFacilityId,
      destinationFacilityId: draft.destinationFacilityId,
      hubHandoffFacilityId:
        draft.hubHandoffFacilityId === "" ? null : draft.hubHandoffFacilityId,
      cargoKg: draftNumber(draft.cargoKg),
      releaseAtS: draftNumber(draft.releaseAtS),
      deliverByS: draftNumber(draft.deliverByS),
    };
    const orders =
      this.activeOrderId === null
        ? [...context.orders, candidate]
        : context.orders.map((order) =>
            order.id === this.activeOrderId ? candidate : order,
          );
    this.commit({ ...fieldsOf(context), orders }, () => {
      this.activeOrderId = undefined;
      this.orderEditor = null;
    });
  }

  private deleteOrder(id: string): void {
    const context = this.context;
    if (context === null) return;
    this.commit(
      {
        ...fieldsOf(context),
        orders: context.orders.filter((order) => order.id !== id),
      },
      () => {
        if (this.activeOrderId === id) {
          this.activeOrderId = undefined;
          this.orderEditor = null;
        }
      },
    );
  }

  private renderOrderEditor(): HTMLElement {
    const context = this.context!;
    const draft = this.orderEditor!;
    const creating = this.activeOrderId === null;
    const card = element("article", "studio-card");
    card.dataset.role = "workspace-order-editor";
    card.append(
      element(
        "h4",
        "studio-card-title",
        creating ? "新增手动订单" : `编辑订单 ${this.activeOrderId}`,
      ),
    );
    const prefix = creating ? "新订单" : `订单 ${this.activeOrderId}`;
    const set = (patch: Partial<OrderDraft>): void => {
      if (this.orderEditor !== null) Object.assign(this.orderEditor, patch);
    };
    const fields = element("div", "studio-field-grid");
    fields.append(
      textInput(`${prefix} ID`, draft.id, (id) => set({ id })),
      this.facilitySelect(
        `${prefix} 来源设施`,
        draft.sourceFacilityId,
        this.cargoFacilities(),
        (sourceFacilityId) => set({ sourceFacilityId }),
      ),
      this.facilitySelect(
        `${prefix} 目的设施`,
        draft.destinationFacilityId,
        this.cargoFacilities(),
        (destinationFacilityId) => set({ destinationFacilityId }),
      ),
      this.facilitySelect(
        `${prefix} 物流中转站交接`,
        draft.hubHandoffFacilityId,
        context.facilities.filter((facility) => facility.kind === "hub"),
        (hubHandoffFacilityId) => set({ hubHandoffFacilityId }),
        true,
      ),
      numberInput(`${prefix} 货物重量 / kg`, draft.cargoKg, (cargoKg) =>
        set({ cargoKg }),
      ),
      numberInput(`${prefix} 释放时间 / s`, draft.releaseAtS, (releaseAtS) =>
        set({ releaseAtS }),
      ),
      numberInput(`${prefix} 交付期限 / s`, draft.deliverByS, (deliverByS) =>
        set({ deliverByS }),
      ),
    );
    card.append(fields);
    const actions = element("div", "studio-row");
    actions.append(
      button(
        "保存订单",
        () => this.saveOrder(),
        "studio-button studio-button-primary",
      ),
      button("取消订单编辑", () => {
        this.activeOrderId = undefined;
        this.orderEditor = null;
        this.error = null;
        this.render();
      }),
    );
    card.append(actions);
    return card;
  }

  private ordersSection(): HTMLElement {
    const context = this.context!;
    const section = element("section");
    section.append(
      element(
        "h3",
        "studio-section-title",
        `手动订单 (${context.orders.length})`,
      ),
      element(
        "p",
        "studio-help",
        "订单只声明作者输入的需求，不表示 Business Provider 已创建、接受、派送或完成订单。此处仅校验设施引用与中转站语义，不声称载荷或航程可行。",
      ),
    );
    if (context.orders.length === 0 && this.orderEditor === null) {
      section.append(element("p", "studio-empty", "当前没有手动订单。"));
    }
    for (const order of context.orders) {
      const card = element("article", "studio-card");
      card.dataset.orderId = order.id;
      card.append(
        element("h4", "studio-card-title", `订单 ${order.id}`),
        element(
          "p",
          "studio-source",
          `${order.sourceFacilityId} → ${order.destinationFacilityId} · ${order.cargoKg} kg · ${order.releaseAtS}–${order.deliverByS} s`,
        ),
      );
      const actions = element("div", "studio-row");
      const edit = button("编辑", () => this.beginOrder(order));
      edit.setAttribute("aria-label", `编辑订单 ${order.id}`);
      const remove = button(
        "删除",
        () => this.deleteOrder(order.id),
        "studio-button studio-danger",
      );
      remove.setAttribute("aria-label", `删除订单 ${order.id}`);
      actions.append(edit, remove);
      card.append(actions);
      section.append(card);
    }
    if (this.orderEditor !== null) section.append(this.renderOrderEditor());
    else {
      const add = button(
        "添加手动订单",
        () => this.beginOrder(),
        "studio-button studio-button-primary",
      );
      add.setAttribute("aria-label", "添加手动订单");
      const canCreate =
        this.cargoFacilities().length >= 2 &&
        context.facilities.some((facility) => facility.kind === "hub");
      add.disabled = !canCreate;
      section.append(add);
      if (!canCreate) {
        section.append(
          element(
            "p",
            "studio-note",
            "添加订单至少需要两处可交接货物的设施，并且其中至少有一处物流中转站。",
          ),
        );
      }
    }
    return section;
  }

  private saveGeneration(): void {
    const context = this.context;
    const draft = this.generationEditor;
    if (context === null || draft === null) return;
    this.commit(
      {
        ...fieldsOf(context),
        orderGeneration: {
          seed: draftNumber(draft.seed),
          maxOrders: draftNumber(draft.maxOrders),
          startAtS: draftNumber(draft.startAtS),
          endAtS: draftNumber(draft.endAtS),
          cargoMinKg: draftNumber(draft.cargoMinKg),
          cargoMaxKg: draftNumber(draft.cargoMaxKg),
          deadlineLeadS: draftNumber(draft.deadlineLeadS),
        },
      },
      () => {
        this.generationEditor = null;
      },
    );
  }

  private generationSection(): HTMLElement {
    const context = this.context!;
    const section = element("section");
    section.append(
      element("h3", "studio-section-title", "订单生成配置"),
      element(
        "p",
        "studio-help",
        "配置只声明确定性需求生成输入；编辑或保存不会生成业务状态、调度结果或 Provider 证据。",
      ),
    );
    if (this.generationEditor === null) {
      const value = context.orderGeneration;
      section.append(
        element(
          "p",
          "studio-source",
          `seed ${value.seed} · 最多 ${value.maxOrders} 单 · ${value.startAtS}–${value.endAtS} s · ${value.cargoMinKg}–${value.cargoMaxKg} kg · 期限超前 ${value.deadlineLeadS} s`,
        ),
      );
      section.append(
        button("编辑生成配置", () => {
          this.error = null;
          this.generationEditor = generationDraft(context.orderGeneration);
          this.render();
        }),
      );
      return section;
    }
    const draft = this.generationEditor;
    const set = (patch: Partial<GenerationDraft>): void => {
      if (this.generationEditor !== null)
        Object.assign(this.generationEditor, patch);
    };
    const fields = element("div", "studio-field-grid");
    fields.append(
      numberInput("生成随机种子", draft.seed, (seed) => set({ seed }), {
        min: 0,
        step: "1",
      }),
      numberInput(
        "生成订单数量",
        draft.maxOrders,
        (maxOrders) => set({ maxOrders }),
        { min: 0, step: "1" },
      ),
      numberInput(
        "生成开始时间 / s",
        draft.startAtS,
        (startAtS) => set({ startAtS }),
        { min: 0 },
      ),
      numberInput("生成结束时间 / s", draft.endAtS, (endAtS) =>
        set({ endAtS }),
      ),
      numberInput("生成最小货重 / kg", draft.cargoMinKg, (cargoMinKg) =>
        set({ cargoMinKg }),
      ),
      numberInput("生成最大货重 / kg", draft.cargoMaxKg, (cargoMaxKg) =>
        set({ cargoMaxKg }),
      ),
      numberInput(
        "生成交付期限超前 / s",
        draft.deadlineLeadS,
        (deadlineLeadS) => set({ deadlineLeadS }),
      ),
    );
    section.append(fields);
    const actions = element("div", "studio-row");
    actions.append(
      button(
        "保存生成配置",
        () => this.saveGeneration(),
        "studio-button studio-button-primary",
      ),
      button("取消生成配置编辑", () => {
        this.generationEditor = null;
        this.error = null;
        this.render();
      }),
    );
    section.append(actions);
    return section;
  }

  private beginProfile(
    fleetEntryId: string,
    profile?: FleetPerformanceProfile,
  ): void {
    this.error = null;
    this.activeProfileId = fleetEntryId;
    this.profileEditor = profileDraft(fleetEntryId, profile);
    this.render();
  }

  private saveProfile(): void {
    const context = this.context;
    const draft = this.profileEditor;
    if (context === null || draft === null || this.activeProfileId === null)
      return;
    const candidate: FleetPerformanceProfile = {
      fleetEntryId: draft.fleetEntryId,
      sourceLabel: draft.sourceLabel,
      provenance: draft.provenance,
      aircraftBody: {
        xM: draftNumber(draft.xM),
        yM: draftNumber(draft.yM),
        zM: draftNumber(draft.zM),
      },
      cruiseSpeedMps: draftNumber(draft.cruiseSpeedMps),
      cruisePowerW: draftNumber(draft.cruisePowerW),
      hoverPowerW: draftNumber(draft.hoverPowerW),
      chargeEfficiency: draftNumber(draft.chargeEfficiency),
    };
    const exists = context.performanceProfiles.some(
      (profile) => profile.fleetEntryId === this.activeProfileId,
    );
    const performanceProfiles = exists
      ? context.performanceProfiles.map((profile) =>
          profile.fleetEntryId === this.activeProfileId ? candidate : profile,
        )
      : [...context.performanceProfiles, candidate];
    this.commit({ ...fieldsOf(context), performanceProfiles }, () => {
      this.activeProfileId = null;
      this.profileEditor = null;
    });
  }

  private deleteProfile(fleetEntryId: string): void {
    const context = this.context;
    if (context === null) return;
    this.commit(
      {
        ...fieldsOf(context),
        performanceProfiles: context.performanceProfiles.filter(
          (profile) => profile.fleetEntryId !== fleetEntryId,
        ),
      },
      () => {
        if (this.activeProfileId === fleetEntryId) {
          this.activeProfileId = null;
          this.profileEditor = null;
        }
      },
    );
  }

  private renderProfileEditor(): HTMLElement {
    const draft = this.profileEditor!;
    const card = element("article", "studio-card");
    card.dataset.role = "workspace-profile-editor";
    card.append(
      element("h4", "studio-card-title", `性能档案 ${draft.fleetEntryId}`),
      element(
        "p",
        "studio-note",
        "来源标签和出处均为作者声明。数值不从 GLB 尺寸推断，也不构成适航、载荷、续航、功率或充电性能已经验证的证明。",
      ),
    );
    const set = (patch: Partial<ProfileDraft>): void => {
      if (this.profileEditor !== null) Object.assign(this.profileEditor, patch);
    };
    const fields = element("div", "studio-field-grid");
    fields.append(
      textInput("性能档案机队 ID", draft.fleetEntryId, () => undefined, true),
      textInput("性能档案来源标签", draft.sourceLabel, (sourceLabel) =>
        set({ sourceLabel }),
      ),
      textInput("性能档案出处（用户声明）", draft.provenance, (provenance) =>
        set({ provenance }),
      ),
      numberInput("机体长度 x / m", draft.xM, (xM) => set({ xM })),
      numberInput("机体高度 y / m", draft.yM, (yM) => set({ yM })),
      numberInput("机体宽度 z / m", draft.zM, (zM) => set({ zM })),
      numberInput("巡航速度 / (m/s)", draft.cruiseSpeedMps, (cruiseSpeedMps) =>
        set({ cruiseSpeedMps }),
      ),
      numberInput("巡航功率 / W", draft.cruisePowerW, (cruisePowerW) =>
        set({ cruisePowerW }),
      ),
      numberInput("悬停功率 / W", draft.hoverPowerW, (hoverPowerW) =>
        set({ hoverPowerW }),
      ),
      numberInput(
        "充电效率 / 0–1",
        draft.chargeEfficiency,
        (chargeEfficiency) => set({ chargeEfficiency }),
        { min: 0, max: 1 },
      ),
    );
    card.append(fields);
    const actions = element("div", "studio-row");
    actions.append(
      button(
        "保存性能档案",
        () => this.saveProfile(),
        "studio-button studio-button-primary",
      ),
      button("取消性能档案编辑", () => {
        this.activeProfileId = null;
        this.profileEditor = null;
        this.error = null;
        this.render();
      }),
    );
    card.append(actions);
    return card;
  }

  private profilesSection(): HTMLElement {
    const context = this.context!;
    const byFleet = new Map(
      context.performanceProfiles.map((profile) => [
        profile.fleetEntryId,
        profile,
      ]),
    );
    const section = element("section");
    section.append(
      element(
        "h3",
        "studio-section-title",
        `机队性能档案 (${context.performanceProfiles.length}/${context.fleet.length})`,
      ),
      element(
        "p",
        "studio-help",
        "档案严格按当前机队条目 ID 关联。空缺保持空缺；面板不会填入型号默认值，也不会把作者声明提升为物理能力或 Provider 测量。",
      ),
    );
    for (const entry of context.fleet) {
      const profile = byFleet.get(entry.id);
      const card = element("article", "studio-card");
      card.dataset.fleetEntryId = entry.id;
      card.append(
        element(
          "h4",
          "studio-card-title",
          `${entry.id} · ${entry.assetId} × ${entry.count}`,
        ),
      );
      if (profile === undefined) {
        card.append(element("p", "studio-empty", "尚未声明性能档案。"));
        const add = button("添加性能档案", () => this.beginProfile(entry.id));
        add.setAttribute("aria-label", `添加性能档案 ${entry.id}`);
        card.append(add);
      } else {
        card.append(
          element(
            "p",
            "studio-source",
            `${profile.sourceLabel} · ${profile.provenance} · ${profile.cruiseSpeedMps} m/s`,
          ),
        );
        const actions = element("div", "studio-row");
        const edit = button("编辑", () => this.beginProfile(entry.id, profile));
        edit.setAttribute("aria-label", `编辑性能档案 ${entry.id}`);
        const remove = button(
          "删除",
          () => this.deleteProfile(entry.id),
          "studio-button studio-danger",
        );
        remove.setAttribute("aria-label", `删除性能档案 ${entry.id}`);
        actions.append(edit, remove);
        card.append(actions);
      }
      section.append(card);
      if (this.activeProfileId === entry.id && this.profileEditor !== null) {
        section.append(this.renderProfileEditor());
      }
    }
    return section;
  }

  private render(): void {
    const panel = element("section", "studio-logistics-panel");
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "工作区订单撰写");
    panel.append(
      element("h2", "studio-panel-title", "订单与机队性能"),
      element(
        "p",
        "studio-help",
        "本面板只编辑工作区作者输入。保存不会创建业务订单、执行调度或证明任何物理能力。",
      ),
    );
    if (this.blockingError !== null) {
      const alert = element(
        "p",
        "studio-note",
        `订单编辑器无法加载：${this.blockingError}`,
      );
      alert.setAttribute("role", "alert");
      panel.append(alert);
      this.root.replaceChildren(panel);
      return;
    }
    if (this.error !== null) {
      const alert = element("p", "studio-note", this.error);
      alert.setAttribute("role", "alert");
      alert.setAttribute("aria-live", "polite");
      panel.append(alert);
    }
    panel.append(
      this.ordersSection(),
      this.generationSection(),
      this.profilesSection(),
    );
    this.root.replaceChildren(panel);
  }
}

const panels = new WeakMap<HTMLElement, CityWorkspaceOrdersPanel>();

/** Render the isolated workspace-v3 orders projection. The caller merges the
 * emitted fields back into the workspace through its normal validated path. */
export function renderCityWorkspaceOrdersPanel(
  root: HTMLElement,
  context: CityWorkspaceOrdersContext,
  onChange: (next: CityWorkspaceOrdersFields) => void,
): void {
  let panel = panels.get(root);
  if (panel === undefined) {
    panel = new CityWorkspaceOrdersPanel(root);
    panels.set(root, panel);
  }
  panel.update(context, onChange);
}
