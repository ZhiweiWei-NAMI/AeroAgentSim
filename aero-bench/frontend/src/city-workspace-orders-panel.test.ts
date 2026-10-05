// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import {
  parseCityWorkspaceOrdersContext,
  renderCityWorkspaceOrdersPanel,
  type CityWorkspaceOrdersContext,
  type CityWorkspaceOrdersFields,
} from "./city-workspace-orders-panel";
import type {
  FleetPerformanceProfile,
  LogisticsOrderRequest,
} from "./city-selected-logistics-draft";

function order(
  overrides: Partial<LogisticsOrderRequest> = {},
): LogisticsOrderRequest {
  return {
    id: "order-1",
    sourceFacilityId: "vertiport-a",
    destinationFacilityId: "vertiport-b",
    hubHandoffFacilityId: "hub-a",
    cargoKg: 4,
    releaseAtS: 10,
    deliverByS: 300,
    ...overrides,
  };
}

function profile(
  overrides: Partial<FleetPerformanceProfile> = {},
): FleetPerformanceProfile {
  return {
    fleetEntryId: "fleet-a",
    sourceLabel: "操作员规格表",
    provenance: "用户声明，尚未独立验证",
    aircraftBody: { xM: 1.2, yM: 0.5, zM: 1.2 },
    cruiseSpeedMps: 12,
    cruisePowerW: 420,
    hoverPowerW: 500,
    chargeEfficiency: 0.88,
    ...overrides,
  };
}

function context(
  overrides: Partial<CityWorkspaceOrdersContext> = {},
): CityWorkspaceOrdersContext {
  return {
    facilities: [
      { id: "vertiport-a", name: "东侧起降点", kind: "vertiport" },
      { id: "vertiport-b", name: "西侧起降点", kind: "vertiport" },
      { id: "hub-a", name: "中心货站", kind: "hub" },
      { id: "charger-a", name: "独立充电站", kind: "charger" },
    ],
    fleet: [
      { id: "fleet-a", assetId: "model:uav-a", count: 2 },
      { id: "fleet-b", assetId: "model:uav-b", count: 1 },
    ],
    orders: [],
    orderGeneration: {
      seed: 7,
      maxOrders: 0,
      startAtS: 0,
      endAtS: 1,
      cargoMinKg: 0.1,
      cargoMaxKg: 0.1,
      deadlineLeadS: 1,
    },
    performanceProfiles: [],
    ...overrides,
  };
}

function button(root: HTMLElement, name: string): HTMLButtonElement {
  const result = [...root.querySelectorAll("button")].find(
    (candidate) => candidate.textContent === name,
  );
  if (result === undefined) throw new Error(`Missing button ${name}`);
  return result;
}

function ariaButton(root: HTMLElement, name: string): HTMLButtonElement {
  const result = root.querySelector<HTMLButtonElement>(
    `button[aria-label="${name}"]`,
  );
  if (result === null) throw new Error(`Missing button ${name}`);
  return result;
}

function input(root: HTMLElement, label: string): HTMLInputElement {
  const result = root.querySelector<HTMLInputElement>(
    `input[aria-label="${label}"]`,
  );
  if (result === null) throw new Error(`Missing input ${label}`);
  return result;
}

function setInput(root: HTMLElement, label: string, value: string): void {
  const result = input(root, label);
  result.value = value;
  result.dispatchEvent(new Event("input", { bubbles: true }));
}

function choose(root: HTMLElement, label: string, value: string): void {
  const result = root.querySelector<HTMLSelectElement>(
    `select[aria-label="${label}"]`,
  );
  if (result === null) throw new Error(`Missing select ${label}`);
  result.value = value;
  result.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("workspace orders context", () => {
  it("parses the exact logistics shapes and only projects required workspace references", () => {
    const raw = {
      ...context({ orders: [order()], performanceProfiles: [profile()] }),
      unrelatedWorkspaceField: "not consumed",
      facilities: context().facilities.map((facility) => ({
        ...facility,
        position: { x: 0, z: 0 },
      })),
      fleet: context().fleet.map((entry) => ({ ...entry, batteryWh: 500 })),
    };
    const parsed = parseCityWorkspaceOrdersContext(raw);
    expect(parsed.orders).toEqual([order()]);
    expect(parsed.performanceProfiles).toEqual([profile()]);
    expect(parsed.facilities[0]).toEqual({
      id: "vertiport-a",
      name: "东侧起降点",
      kind: "vertiport",
    });
    expect(parsed.fleet[0]).toEqual({
      id: "fleet-a",
      assetId: "model:uav-a",
      count: 2,
    });
    expect(parsed).not.toHaveProperty("unrelatedWorkspaceField");
  });

  it("rejects invalid facility references, handoff semantics, duplicate IDs, and extra order fields", () => {
    const invalid = [
      order({ sourceFacilityId: "missing" }),
      order({ destinationFacilityId: "vertiport-a" }),
      order({ sourceFacilityId: "charger-a" }),
      order({ hubHandoffFacilityId: null }),
      order({ hubHandoffFacilityId: "vertiport-b" }),
      { ...order(), status: "accepted" },
    ];
    for (const suspect of invalid) {
      expect(() =>
        parseCityWorkspaceOrdersContext(context({ orders: [suspect] })),
      ).toThrow();
    }
    expect(() =>
      parseCityWorkspaceOrdersContext(context({ orders: [order(), order()] })),
    ).toThrow(/重复/);
    expect(() =>
      parseCityWorkspaceOrdersContext(
        context({
          orders: [
            order({ sourceFacilityId: "hub-a", hubHandoffFacilityId: null }),
          ],
        }),
      ),
    ).not.toThrow();
  });

  it("keeps capacity outside the editor contract while validating generation and profile provenance", () => {
    expect(() =>
      parseCityWorkspaceOrdersContext(
        context({ orders: [order({ cargoKg: 1_000_000_000 })] }),
      ),
    ).not.toThrow();
    expect(() =>
      parseCityWorkspaceOrdersContext(
        context({
          orderGeneration: {
            ...context().orderGeneration,
            endAtS: 0,
          },
        }),
      ),
    ).toThrow(/结束时间/);
    expect(() =>
      parseCityWorkspaceOrdersContext(
        context({
          performanceProfiles: [profile({ fleetEntryId: "missing" })],
        }),
      ),
    ).toThrow(/未知机队/);
    expect(() =>
      parseCityWorkspaceOrdersContext(
        context({ performanceProfiles: [profile({ provenance: "" })] }),
      ),
    ).toThrow(/出处/);
    expect(() =>
      parseCityWorkspaceOrdersContext(
        context({ performanceProfiles: [profile(), profile()] }),
      ),
    ).toThrow(/重复/);
  });

  it("rejects generated order counts above the workspace-v3 contract cap", () => {
    expect(() =>
      parseCityWorkspaceOrdersContext(
        context({
          orderGeneration: {
            ...context().orderGeneration,
            maxOrders: 10_001,
          },
        }),
      ),
    ).toThrow(/最大订单数不得超过 10,000/);
    expect(() =>
      parseCityWorkspaceOrdersContext(
        context({
          orderGeneration: {
            ...context().orderGeneration,
            maxOrders: 10_000,
          },
        }),
      ),
    ).not.toThrow();
  });
});

describe("workspace orders panel", () => {
  it("creates, edits, and deletes a manual order without inventing form defaults", () => {
    const root = document.createElement("div");
    const changes: CityWorkspaceOrdersFields[] = [];
    renderCityWorkspaceOrdersPanel(root, context(), (next) =>
      changes.push(next),
    );
    expect(root.textContent).toContain("不会创建业务订单");
    ariaButton(root, "添加手动订单").click();
    expect(input(root, "新订单 ID").value).toBe("");
    expect(input(root, "新订单 货物重量 / kg").value).toBe("");
    expect(changes).toHaveLength(0);

    setInput(root, "新订单 ID", "manual-1");
    choose(root, "新订单 来源设施", "vertiport-a");
    choose(root, "新订单 目的设施", "vertiport-b");
    choose(root, "新订单 物流中转站交接", "hub-a");
    setInput(root, "新订单 货物重量 / kg", "3.5");
    setInput(root, "新订单 释放时间 / s", "20");
    setInput(root, "新订单 交付期限 / s", "400");
    button(root, "保存订单").click();
    expect(changes).toHaveLength(1);
    expect(changes[0]!.orders).toEqual([
      order({
        id: "manual-1",
        cargoKg: 3.5,
        releaseAtS: 20,
        deliverByS: 400,
      }),
    ]);

    ariaButton(root, "编辑订单 manual-1").click();
    setInput(root, "订单 manual-1 ID", "manual-renamed");
    setInput(root, "订单 manual-1 货物重量 / kg", "5");
    button(root, "保存订单").click();
    expect(changes.at(-1)!.orders[0]).toMatchObject({
      id: "manual-renamed",
      cargoKg: 5,
    });
    ariaButton(root, "删除订单 manual-renamed").click();
    expect(changes.at(-1)!.orders).toEqual([]);
  });

  it("shows invalid manual edits and emits nothing until the whole order is valid", () => {
    const root = document.createElement("div");
    const onChange = vi.fn<(next: CityWorkspaceOrdersFields) => void>();
    renderCityWorkspaceOrdersPanel(
      root,
      context({ orders: [order()] }),
      onChange,
    );
    ariaButton(root, "编辑订单 order-1").click();
    choose(root, "订单 order-1 目的设施", "vertiport-a");
    button(root, "保存订单").click();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(
      "起终点必须不同",
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(input(root, "订单 order-1 ID").value).toBe("order-1");
  });

  it("edits every generation field atomically and reports invalid ranges", () => {
    const root = document.createElement("div");
    const changes: CityWorkspaceOrdersFields[] = [];
    renderCityWorkspaceOrdersPanel(root, context(), (next) =>
      changes.push(next),
    );
    button(root, "编辑生成配置").click();
    for (const [label, value] of [
      ["生成随机种子", "99"],
      ["生成订单数量", "8"],
      ["生成开始时间 / s", "10"],
      ["生成结束时间 / s", "110"],
      ["生成最小货重 / kg", "1.5"],
      ["生成最大货重 / kg", "7.5"],
      ["生成交付期限超前 / s", "240"],
    ] as const) {
      setInput(root, label, value);
    }
    button(root, "保存生成配置").click();
    expect(changes.at(-1)!.orderGeneration).toEqual({
      seed: 99,
      maxOrders: 8,
      startAtS: 10,
      endAtS: 110,
      cargoMinKg: 1.5,
      cargoMaxKg: 7.5,
      deadlineLeadS: 240,
    });

    button(root, "编辑生成配置").click();
    setInput(root, "生成结束时间 / s", "5");
    button(root, "保存生成配置").click();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(
      "结束时间",
    );
    expect(changes).toHaveLength(1);
  });

  it("adds, edits, and deletes fleet-keyed profiles with explicit provenance", () => {
    const root = document.createElement("div");
    const changes: CityWorkspaceOrdersFields[] = [];
    renderCityWorkspaceOrdersPanel(root, context(), (next) =>
      changes.push(next),
    );
    expect(root.textContent).toContain("不会填入型号默认值");
    ariaButton(root, "添加性能档案 fleet-a").click();
    expect(input(root, "性能档案来源标签").value).toBe("");
    expect(input(root, "性能档案机队 ID").readOnly).toBe(true);
    button(root, "保存性能档案").click();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(
      "来源标签",
    );
    expect(changes).toHaveLength(0);

    for (const [label, value] of [
      ["性能档案来源标签", "操作员规格表"],
      ["性能档案出处（用户声明）", "用户声明，尚未独立验证"],
      ["机体长度 x / m", "1.2"],
      ["机体高度 y / m", "0.5"],
      ["机体宽度 z / m", "1.2"],
      ["巡航速度 / (m/s)", "12"],
      ["巡航功率 / W", "420"],
      ["悬停功率 / W", "500"],
      ["充电效率 / 0–1", "0.88"],
    ] as const) {
      setInput(root, label, value);
    }
    button(root, "保存性能档案").click();
    expect(changes.at(-1)!.performanceProfiles).toEqual([profile()]);

    ariaButton(root, "编辑性能档案 fleet-a").click();
    setInput(root, "性能档案出处（用户声明）", "修订后的用户声明");
    setInput(root, "巡航速度 / (m/s)", "13");
    button(root, "保存性能档案").click();
    expect(changes.at(-1)!.performanceProfiles[0]).toMatchObject({
      fleetEntryId: "fleet-a",
      provenance: "修订后的用户声明",
      cruiseSpeedMps: 13,
    });
    ariaButton(root, "删除性能档案 fleet-a").click();
    expect(changes.at(-1)!.performanceProfiles).toEqual([]);
  });

  it("blocks malformed incoming fields visibly without substituting defaults", () => {
    const root = document.createElement("div");
    const onChange = vi.fn();
    renderCityWorkspaceOrdersPanel(
      root,
      context({ orders: [order({ sourceFacilityId: "missing" })] }),
      onChange,
    );
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(
      "未知来源设施",
    );
    expect(root.querySelector("input")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();

    renderCityWorkspaceOrdersPanel(root, context(), onChange);
    expect(root.querySelector('[role="alert"]')).toBeNull();
    expect(ariaButton(root, "添加手动订单").disabled).toBe(false);
  });
});
