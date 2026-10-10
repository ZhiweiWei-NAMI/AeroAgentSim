import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderCityEventPanel } from "./city-event-panel";
import { createDefaultCityWorkspaceConfig, parseCityWorkspaceConfig } from "./city-workspace-config";
import type { CityWorkspaceConfig } from "./city-workspace-config";

function draft(): CityWorkspaceConfig {
  return { ...createDefaultCityWorkspaceConfig(), name: "untouched workspace field" };
}

function control<T extends HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
  root: HTMLElement, labelText: string,
): T {
  const label = Array.from(root.querySelectorAll("label"))
    .find(node => node.firstElementChild?.textContent === labelText);
  const node = label?.querySelector("input, select, textarea");
  if (node === null || node === undefined) throw new Error(`Missing control: ${labelText}`);
  return node as T;
}

function input(root: HTMLElement, label: string, value: string): void {
  const node = control<HTMLInputElement | HTMLTextAreaElement>(root, label);
  node.value = value;
  node.dispatchEvent(new Event("input", { bubbles: true }));
}

function choose(root: HTMLElement, label: string, value: string): void {
  const node = control<HTMLSelectElement>(root, label);
  node.value = value;
  node.dispatchEvent(new Event("change", { bubbles: true }));
}

function click(root: HTMLElement, ariaLabel: string): void {
  const button = Array.from(root.querySelectorAll("button"))
    .find(node => node.getAttribute("aria-label") === ariaLabel);
  if (button === undefined) throw new Error(`Missing button: ${ariaLabel}`);
  button.click();
}

describe("city event panel", () => {
  beforeEach(() => document.body.replaceChildren());

  it("shows the four-stage flow and keeps draft choreography distinct from Provider evidence", () => {
    const root = document.createElement("div");
    const onChange = vi.fn();
    renderCityEventPanel(root, draft(), onChange);

    expect(root.textContent).toContain("事件到画面的编排");
    expect(root.textContent).toContain("动作转换");
    expect(root.textContent).toContain("状态与标签");
    expect(root.textContent).toContain("视觉");
    expect(root.textContent).toContain("Provider 返回的真实状态");
    expect(root.textContent).toContain("不是物理证据");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("adds, edits, and removes an event without emitting invalid payloads", () => {
    const original = draft();
    const root = document.createElement("div");
    const changes: CityWorkspaceConfig[] = [];
    renderCityEventPanel(root, original, next => changes.push(next));

    click(root, "添加事件");
    expect(() => parseCityWorkspaceConfig(changes.at(-1))).not.toThrow();
    const beforeEmpty = changes.length;
    input(root, "事件 ID", "");
    expect(changes).toHaveLength(beforeEmpty);
    expect(root.textContent).toContain("此字段不能为空");
    input(root, "事件 ID", "weather-front");
    input(root, "发生时间（秒）", "12.5");
    choose(root, "事件类型", "weather.changed");
    input(root, "目标 ID", "district-north");
    const beforeInvalid = changes.length;
    input(root, "事件载荷（JSON 对象）", "[");
    expect(changes).toHaveLength(beforeInvalid);
    expect(root.textContent).toContain("JSON 格式无效");
    input(root, "事件载荷（JSON 对象）", "[1]");
    expect(changes).toHaveLength(beforeInvalid);
    expect(root.textContent).toContain("载荷必须是 JSON 对象");
    input(root, "事件载荷（JSON 对象）", '{"windKts":1e999}');
    expect(changes).toHaveLength(beforeInvalid);
    expect(root.textContent).toContain("JSON 数字必须有限");
    input(root, "事件载荷（JSON 对象）", '{"windKts":18}');

    expect(changes.at(-1)?.events).toEqual([{
      id: "weather-front", atS: 12.5, type: "weather.changed",
      targetId: "district-north", payload: { windKts: 18 },
    }]);
    expect(original.events).toEqual([]);
    expect((changes.at(-1) as unknown as { name: string }).name).toBe("untouched workspace field");

    click(root, "删除事件 weather-front");
    expect(changes.at(-1)?.events).toEqual([]);
  });

  it("edits action mappings through labelled controls", () => {
    const root = document.createElement("div");
    const changes: CityWorkspaceConfig[] = [];
    renderCityEventPanel(root, draft(), next => changes.push(next));

    click(root, "添加动作规则");
    expect(() => parseCityWorkspaceConfig(changes.at(-1))).not.toThrow();
    input(root, "规则 ID", "dispatch");
    input(root, "触发事件类型", "order.created");
    choose(root, "动作", "follow_route");
    choose(root, "执行角色", "vehicle");
    const beforeInvalid = changes.length;
    input(root, "动作参数（JSON 对象）", "[1]");
    expect(changes).toHaveLength(beforeInvalid);
    expect(root.textContent).toContain("参数必须是 JSON 对象");
    input(root, "动作参数（JSON 对象）", '{"routeId":"route-1","speedMps":4}');
    expect(changes.at(-1)?.actionRules).toEqual([{
      id: "dispatch", eventType: "order.created", action: "follow_route", executorRole: "vehicle",
      arguments: { routeId: "route-1", speedMps: 4 },
    }]);
    expect(() => parseCityWorkspaceConfig(changes.at(-1))).not.toThrow();
    click(root, "删除动作规则 dispatch");
    expect(changes.at(-1)?.actionRules).toEqual([]);
  });

  it("keeps the selected row when the parent redraws after a change", () => {
    const root = document.createElement("div");
    const onChange = (next: CityWorkspaceConfig): void => renderCityEventPanel(root, next, onChange);
    renderCityEventPanel(root, draft(), onChange);

    click(root, "添加事件");
    click(root, "添加事件");
    expect(control<HTMLInputElement>(root, "事件 ID").value).toBe("event-2");
    input(root, "事件 ID", "second-event");
    expect(control<HTMLInputElement>(root, "事件 ID").value).toBe("second-event");
    expect(root.querySelector('[aria-label="选择事件 second-event"]')?.getAttribute("aria-pressed")).toBe("true");
  });

  it("edits keyframe coordinates and typed Provider label conditions", () => {
    const root = document.createElement("div");
    const changes: CityWorkspaceConfig[] = [];
    renderCityEventPanel(root, draft(), next => changes.push(next));

    click(root, "添加状态关键帧");
    expect(changes).toHaveLength(0);
    click(root, "保存关键帧");
    expect(changes).toHaveLength(0);
    expect(root.textContent).toContain("请填写有效的时间和三个位置坐标");
    input(root, "新预览时间（秒）", "8");
    input(root, "新实体 ID", "uav.01");
    input(root, "新位置 x（米）", "42");
    input(root, "新位置 y（机体底部高度，米）", "12");
    input(root, "新位置 z（米）", "15.5");
    input(root, "新预览标签", "抵达路口");
    click(root, "保存关键帧");
    expect(() => parseCityWorkspaceConfig(changes.at(-1))).not.toThrow();
    input(root, "位置 x（米）", "43");
    input(root, "位置 y（机体底部高度，米）", "13");
    expect(changes.at(-1)?.stateKeyframes[0]).toMatchObject({
      atS: 8, entityId: "uav.01", position: { x: 43, y: 13, z: 15.5 }, label: "抵达路口",
    });
    const beforeInvalid = changes.length;
    input(root, "预览时间（秒）", "-1");
    expect(changes).toHaveLength(beforeInvalid);
    expect(root.textContent).toContain("请输入不小于 0 的有限数字");

    click(root, "添加标签规则");
    expect(() => parseCityWorkspaceConfig(changes.at(-1))).not.toThrow();
    input(root, "Provider 状态字段", "battery_pct");
    choose(root, "比较", "lt");
    choose(root, "值类型", "number");
    input(root, "比较值", "20");
    input(root, "显示标签", "低电量");
    expect(changes.at(-1)?.labelRules[0]).toMatchObject({
      field: "battery_pct", operator: "lt", value: 20, label: "低电量",
    });
    choose(root, "值类型", "boolean");
    choose(root, "比较值", "true");
    expect(changes.at(-1)?.labelRules[0]?.value).toBe(true);
    click(root, "删除标签规则 label-1");
    click(root, "删除状态关键帧 keyframe-1");
    expect(changes.at(-1)?.labelRules).toEqual([]);
    expect(changes.at(-1)?.stateKeyframes).toEqual([]);
  });
});
