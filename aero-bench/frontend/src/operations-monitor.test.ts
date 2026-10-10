// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  boundsFor,
  mountOperationsMonitor,
  type OperationsMonitorCallbacks,
  type OperationsSnapshot,
} from "./operations-monitor";

import { setLanguage, t } from "./i18n";

afterEach(() => setLanguage("zh"));

function snapshot(overrides: Partial<OperationsSnapshot> = {}): OperationsSnapshot {
  return {
    sourceKey: "run:alpha",
    sourceLabel: "封存回放 · alpha",
    timeSeconds: 65,
    clockState: "playing",
    selected: { kind: "entity", id: "uav-1" },
    objects: [
      {
        id: "uav-1", kind: "uav", label: "巡检机 01", position: { x: 10, y: 42, z: -20 },
        headingRad: Math.PI / 2, phase: "前往事件点",
        telemetry: {
          altitudeM: 42, altitudeReference: "AGL", speedMps: 8.25, batteryPercent: 67,
          payload: "RGB 相机", nextDestination: "屋顶 A",
          activity: { label: "执行任务", state: "normal" },
          connectivity: { label: "遥测陈旧", state: "warning" },
          health: { label: "正常", state: "normal" },
          freshness: { state: "stale", ageSeconds: 4.5 }, observedAtSeconds: 60.5,
        },
        task: { id: "mission-1", label: "立面巡检", orderId: "order-9", phase: "在途" },
        camera: { source: "simulated_rgb", state: "ready", frameTimeSeconds: 65 },
      },
      { id: "van-1", kind: "ugv", label: "接驳车 01", position: { x: 60, y: 0, z: 30 } },
      { id: "hub-a", kind: "facility", label: "起降站 A", position: { x: -40, y: 0, z: 45 } },
    ],
    routes: [{ id: "route-1", objectId: "uav-1", points: [{ x: 10, z: -20 }, { x: 40, z: 5 }] }],
    polygons: [{
      id: "restricted-1", kind: "restricted", label: "限制区",
      points: [{ x: -20, z: -10 }, { x: -10, z: -10 }, { x: -10, z: 0 }],
    }],
    events: [{
      id: "event-critical", label: "屋顶烟雾", severity: "critical", condition: "持续中",
      timeSeconds: 58, objectIds: ["uav-1"], position: { x: 35, z: 4 }, locationLabel: "屋顶 A",
      missionId: "mission-1", orderId: "order-9", facilityId: "hub-a", dependencies: ["handover-2"],
      evidence: ["frame-58"], acknowledged: false, resolved: false,
    }, {
      id: "event-info", label: "订单已分配", severity: "unknown", condition: "已记录",
      timeSeconds: 62, objectIds: ["van-1"], acknowledged: true, resolved: true,
    }],
    observer: {
      mode: "cockpit", position: { x: 10, z: -20 }, headingRad: Math.PI / 2,
      footprint: [{ x: 5, z: -25 }, { x: 30, z: -10 }, { x: 5, z: 5 }],
    },
    ...overrides,
  };
}

function mount(callbacks: OperationsMonitorCallbacks = {}) {
  const host = document.createElement("div");
  document.body.append(host);
  const monitor = mountOperationsMonitor(host, callbacks);
  monitor.update(snapshot());
  return { host, monitor };
}

function pointer(
  node: Element, type: "pointerdown" | "pointermove" | "pointerup" | "pointercancel",
  clientX: number, clientY: number, pointerId = 1,
): void {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX, clientY, button: 0 });
  Object.defineProperty(event, "pointerId", { value: pointerId });
  node.dispatchEvent(event);
}

function setMapBox(map: SVGSVGElement): void {
  vi.spyOn(map, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 640, bottom: 400, width: 640, height: 400,
    toJSON: () => ({}),
  });
}

describe("operations monitor", () => {
  beforeEach(() => document.body.replaceChildren());

  it("distinguishes an unexecuted configuration from replay playback in both languages", () => {
    const { monitor } = mount();
    monitor.update(snapshot({ clockState: "notrun" }));
    expect(monitor.element.querySelector(".operations-monitor-clock-state")?.textContent).toBe("尚未运行");
    setLanguage("en");
    monitor.update(snapshot({ clockState: "notrun" }));
    expect(monitor.element.querySelector(".operations-monitor-clock-state")?.textContent).toBe("Not run");
    monitor.update(snapshot({ clockState: "playing" }));
    expect(monitor.element.querySelector(".operations-monitor-clock-state")?.textContent).toBe("Playing");
  });

  it("renders one shared selection with explicit source, clock, statuses, camera source, and unknown values", () => {
    const { monitor } = mount();
    expect(monitor.element.getAttribute("aria-label")).toBe("运行态势监视");
    expect(monitor.element.querySelector(".operations-monitor-source")?.textContent).toBe("封存回放 · alpha");
    expect(monitor.element.querySelector(".operations-monitor-source")?.getAttribute("title")).toBe("run:alpha");
    expect(monitor.element.querySelector(".operations-monitor-clock")?.textContent).toBe("00:01:05");
    expect(monitor.element.querySelector('[data-clock-state="playing"]')?.textContent).toBe("播放");
    expect(monitor.element.querySelector('[data-object-id="uav-1"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(monitor.element.querySelector(".operations-monitor-detail")?.textContent).toContain("活动 执行任务");
    expect(monitor.element.querySelector(".operations-monitor-detail")?.textContent).toContain("连接 遥测陈旧");
    expect(monitor.element.querySelector(".operations-monitor-detail")?.textContent).toContain("相机来源：模拟 RGB · 就绪");
    expect(monitor.element.querySelector(".operations-monitor-detail")?.textContent).toContain("云台机载（不可用）");
    expect(monitor.element.querySelector(".operations-monitor-events")?.textContent).toContain("等级未知");
    expect(monitor.element.querySelector(".operations-monitor-map-north")?.textContent).toBe("N ↑");
    expect(monitor.element.querySelector(".operations-monitor-map-scale-label")?.textContent).toMatch(/m$/);
    expect(monitor.element.querySelector(".operations-monitor-map-legend")?.textContent).toContain("虚线 观察范围");
  });

  it("maps fleet and minimap objects, including facilities, to existing entity targets", () => {
    const onSelect = vi.fn();
    const { monitor } = mount({ onSelect });
    monitor.element.querySelector<HTMLButtonElement>('[aria-label="选择设施 起降站 A"]')!.click();
    expect(onSelect).toHaveBeenLastCalledWith({ kind: "entity", id: "hub-a" });

    const marker = monitor.element.querySelector<SVGGElement>('.operations-monitor-map-marker[data-object-id="van-1"]')!;
    marker.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: "entity", id: "van-1" });
    expect(marker.dataset.x).toBe("60");
    expect(marker.dataset.z).toBe("30");
  });

  it("renders declared building squares on the grid without stretching the fit", () => {
    const { monitor } = mount();
    monitor.update(snapshot({ buildings: [
      { id: "cell:100:100", x: 1000, z: 1000, sizeM: 8 },
      { id: "cell:100:101", x: 1008, z: 1012, sizeM: 8 },
      { id: "cell:100:102", x: 1016, z: 1024, sizeM: 8 },
    ] }));
    const squares = monitor.element.querySelectorAll<SVGRectElement>(".operations-monitor-map-building");
    expect(squares).toHaveLength(3);
    // The monitor map transform is linear in the declared metre grid, so
    // equal metre pitches must give equal attribute deltas — no offset, no
    // skew. Attributes are written with 2-decimal rounding, so compare at
    // that precision.
    const attr = (node: SVGRectElement, name: string): number => Number(node.getAttribute(name));
    const dx1 = attr(squares[1]!, "x") - attr(squares[0]!, "x");
    const dx2 = attr(squares[2]!, "x") - attr(squares[1]!, "x");
    const dy1 = attr(squares[1]!, "y") - attr(squares[0]!, "y");
    const dy2 = attr(squares[2]!, "y") - attr(squares[1]!, "y");
    expect(dx1).toBeCloseTo(dx2, 1);
    expect(dy1).toBeCloseTo(dy2, 1);
    expect(Math.abs(dx1)).toBeGreaterThan(0);
    // Equal declared sizeM squares render with equal width and height.
    expect(attr(squares[0]!, "width")).toBeCloseTo(attr(squares[1]!, "width"), 1);
    expect(attr(squares[0]!, "height")).toBeCloseTo(attr(squares[2]!, "height"), 1);
  });

  it("does not include building squares in the fit bounds", () => {
    const baseBounds = boundsFor(snapshot());
    const withBounds = boundsFor(snapshot({ buildings: [
      { id: "cell:500:500", x: 5000, z: 5000, sizeM: 8 },
    ] }));
    expect(withBounds.minX).toBe(baseBounds.minX);
    expect(withBounds.maxX).toBe(baseBounds.maxX);
    expect(withBounds.minZ).toBe(baseBounds.minZ);
    expect(withBounds.maxZ).toBe(baseBounds.maxZ);
  });

  it("keeps keyed controls, keyboard focus, disclosures, and the preview slot stable across clock updates", () => {
    const { monitor } = mount();
    const row = monitor.element.querySelector<HTMLButtonElement>('[aria-label="选择航空器 巡检机 01"]')!;
    const disclosure = monitor.element.querySelector<HTMLDetailsElement>(".operations-monitor-disclosure")!;
    const eventsDisclosure = monitor.element.querySelector<HTMLDetailsElement>(".operations-monitor-events")!;
    expect(eventsDisclosure.open).toBe(false);
    row.focus(); disclosure.open = false;
    eventsDisclosure.open = true;
    const preview = monitor.previewContainer;
    const canvas = document.createElement("canvas"); preview.append(canvas);

    monitor.update(snapshot({ timeSeconds: 66, clockState: "paused" }));
    expect(monitor.element.querySelector('[aria-label="选择航空器 巡检机 01"]')).toBe(row);
    expect(document.activeElement).toBe(row);
    expect(disclosure.open).toBe(false);
    expect(eventsDisclosure.open).toBe(true);
    expect(monitor.previewContainer).toBe(preview);
    expect(preview.contains(canvas)).toBe(true);
    expect(monitor.element.querySelector('[data-clock-state="paused"]')?.textContent).toBe("画面暂停");
  });

  it("keeps UAVs first, promotes a selected non-UAV, and groups the remaining fleet", () => {
    const { monitor } = mount();
    monitor.update(snapshot({
      selected: { kind: "entity", id: "facility-selected" },
      objects: [
        { id: "facility-z", kind: "facility", label: "设施 Z", position: { x: 0, y: 0, z: 0 } },
        { id: "person-a", kind: "pedestrian", label: "人员 A", position: { x: 0, y: 0, z: 0 } },
        { id: "uav-b", kind: "uav", label: "航空器 B", position: { x: 0, y: 0, z: 0 } },
        { id: "vehicle-a", kind: "ugv", label: "车辆 A", position: { x: 0, y: 0, z: 0 } },
        { id: "facility-selected", kind: "facility", label: "设施 A", position: { x: 0, y: 0, z: 0 } },
        { id: "uav-a", kind: "uav", label: "航空器 A", position: { x: 0, y: 0, z: 0 } },
      ],
    }));
    const rows = Array.from(monitor.element.querySelectorAll<HTMLElement>(".operations-monitor-fleet-row"));
    expect(rows.map(row => row.dataset.objectId)).toEqual([
      "uav-a", "uav-b", "facility-selected", "vehicle-a", "person-a", "facility-z",
    ]);
    expect(rows.filter(row => row.dataset.groupStart === "true").map(row => row.dataset.groupLabel))
      .toEqual(["航空器", "当前选择", "地面车辆", "地面人员", "设施"]);
    expect(rows[2]?.getAttribute("aria-pressed")).toBe("true");
    const fleetDisclosure = monitor.element.querySelector(".operations-monitor-disclosure")!;
    expect(fleetDisclosure.nextElementSibling).toBe(monitor.element.querySelector(".operations-monitor-detail"));
  });

  it("caps the fleet viewport so selected context remains reachable on common desktop heights", () => {
    const css = readFileSync(resolve("src/operations-monitor.css"), "utf8");
    expect(css).toMatch(/\.operations-monitor-fleet\s*\{[^}]*max-height:\s*min\(25vh, 180px\)/s);
    expect(css).toMatch(/\.operations-monitor-fleet\s*\{[^}]*overflow:\s*auto/s);
  });

  it("pins the preview controls and slot to the sidebar bottom inside its own bounds", () => {
    const { monitor } = mount();
    const dock = monitor.element.querySelector(".operations-monitor-camera-dock")!;
    expect(dock.closest(".operations-monitor-sidebar")).not.toBeNull();
    expect(dock.querySelector(".operations-monitor-preview-controls")).not.toBeNull();
    expect(dock.contains(monitor.previewContainer)).toBe(true);
    const css = readFileSync(resolve("src/operations-monitor.css"), "utf8");
    expect(css).toMatch(/\.operations-monitor-camera-dock\s*\{[^}]*position:\s*sticky;[^}]*bottom:\s*0;/s);
    expect(css).toMatch(/\.operations-monitor-header\s*\{[^}]*min-width:\s*0;/s);
  });

  it("reserves viewer-chrome bands so map chrome never sits on monitor panels", () => {
    const css = readFileSync(resolve("src/operations-monitor.css"), "utf8");
    const scoped = ".map:has(> .viewer-chrome-header)";
    const rule = (selector: string) => new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`, "s").exec(css)?.[1] ?? "";
    expect(rule(`${scoped} .operations-monitor`)).toMatch(/padding-bottom:\s*58px;/);
    expect(rule(`${scoped} .operations-monitor-sidebar`)).toMatch(/margin-top:\s*40px;[^]*max-height:\s*calc\(100% - 40px\);/);
    const stats = rule(`${scoped} .map-stats`);
    expect(stats).toMatch(/top:\s*auto;/);
    expect(stats).toMatch(/bottom:\s*16px;/);
    expect(stats).toMatch(/transform:\s*none;/);
  });

  it("uses observation-only camera actions and disables modes unsupported by the selected object", () => {
    const onCameraMode = vi.fn(), onPreviewToggle = vi.fn(), onSwap = vi.fn();
    const { monitor } = mount({ onCameraMode, onPreviewToggle, onSwap });
    const free = monitor.element.querySelector<HTMLButtonElement>('[data-camera-mode="free"]')!;
    const chase = monitor.element.querySelector<HTMLButtonElement>('[data-camera-mode="chase"]')!;
    const cockpit = monitor.element.querySelector<HTMLButtonElement>('[data-camera-mode="cockpit"]')!;
    expect(cockpit.getAttribute("aria-pressed")).toBe("true");
    expect(chase.disabled).toBe(false);
    free.click(); expect(onCameraMode).toHaveBeenCalledWith("free");
    expect(free.getAttribute("aria-pressed")).toBe("true");
    monitor.update(snapshot({ observer: { ...snapshot().observer!, mode: "cockpit" } }));
    expect(cockpit.getAttribute("aria-pressed")).toBe("true");

    const preview = monitor.element.querySelector<HTMLButtonElement>(".operations-monitor-preview-controls button")!;
    preview.click(); expect(onPreviewToggle).toHaveBeenCalledWith(true);
    expect(monitor.previewContainer.hidden).toBe(false);
    monitor.element.querySelectorAll<HTMLButtonElement>(".operations-monitor-preview-controls button")[1]!.click();
    expect(onSwap).toHaveBeenCalledOnce();

    monitor.update(snapshot({ selected: { kind: "entity", id: "van-1" } }));
    expect(cockpit.disabled).toBe(true);
    expect(chase.disabled).toBe(false);
    expect(preview.disabled).toBe(true);
    expect(onPreviewToggle).toHaveBeenLastCalledWith(false);
    expect(monitor.previewContainer.hidden).toBe(true);
  });

  it("turns empty-map clicks and keyboard arrows into observation navigation, leaving cockpit visibly", () => {
    const onNavigate = vi.fn(), onCameraMode = vi.fn();
    const { monitor } = mount({ onNavigate, onCameraMode });
    const map = monitor.element.querySelector<SVGSVGElement>(".operations-monitor-map")!;
    setMapBox(map);
    const background = monitor.element.querySelector(".operations-monitor-map-background")!;
    pointer(background, "pointerdown", 320, 200);
    pointer(background, "pointerup", 320, 200);
    expect(onCameraMode).toHaveBeenCalledWith("free");
    expect(onNavigate).toHaveBeenCalledOnce();
    expect(onNavigate.mock.calls[0]![0]).toEqual(expect.objectContaining({ x: expect.any(Number), z: expect.any(Number) }));
    expect(monitor.element.querySelector('[data-camera-mode="free"]')?.getAttribute("aria-pressed")).toBe("true");

    map.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(onNavigate).toHaveBeenCalledTimes(2);
  });

  it("drags only the observation footprint, suppresses click after movement, and clears drag on pointercancel", () => {
    const onNavigate = vi.fn(), onCameraMode = vi.fn(), onSelect = vi.fn();
    const { monitor } = mount({ onNavigate, onCameraMode, onSelect });
    const map = monitor.element.querySelector<SVGSVGElement>(".operations-monitor-map")!;
    setMapBox(map);
    const footprint = monitor.element.querySelector(".operations-monitor-map-footprint")!;
    pointer(footprint, "pointerdown", 250, 180, 7);
    pointer(footprint, "pointermove", 253, 182, 7);
    expect(onNavigate).not.toHaveBeenCalled();
    pointer(footprint, "pointermove", 290, 200, 7);
    pointer(footprint, "pointerup", 290, 200, 7);
    expect(onCameraMode).toHaveBeenCalledTimes(1);
    expect(onCameraMode).toHaveBeenCalledWith("free");
    expect(onNavigate).toHaveBeenCalledOnce();
    expect(onSelect).not.toHaveBeenCalled();

    pointer(footprint, "pointerdown", 250, 180, 8);
    pointer(footprint, "pointercancel", 250, 180, 8);
    pointer(footprint, "pointermove", 320, 230, 8);
    expect(onNavigate).toHaveBeenCalledOnce();
  });

  it("suppresses an empty-map action when the pointer gesture crosses the drag threshold", () => {
    const onNavigate = vi.fn();
    const { monitor } = mount({ onNavigate });
    const map = monitor.element.querySelector<SVGSVGElement>(".operations-monitor-map")!;
    setMapBox(map);
    const background = monitor.element.querySelector(".operations-monitor-map-background")!;
    pointer(background, "pointerdown", 120, 120);
    pointer(background, "pointerup", 150, 140);
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("auto-fits once when an initially empty source receives its first spatial data", () => {
    const host = document.createElement("div"), monitor = mountOperationsMonitor(host);
    monitor.update(snapshot({
      objects: [], routes: [], polygons: [], events: [], selected: null,
      observer: { mode: "free", position: { x: 0, z: 0 } },
    }));
    const map = monitor.element.querySelector<SVGSVGElement>(".operations-monitor-map")!;
    const emptyGeneration = Number(map.dataset.fitGeneration);
    monitor.update(snapshot({ observer: { mode: "free", position: { x: 0, z: 0 } } }));
    expect(Number(map.dataset.fitGeneration)).toBe(emptyGeneration + 1);
    monitor.update(snapshot({ timeSeconds: 66, observer: { mode: "free", position: { x: 0, z: 0 } } }));
    expect(Number(map.dataset.fitGeneration)).toBe(emptyGeneration + 1);
    const fit = Array.from(monitor.element.querySelectorAll("button")).find(button => button.textContent === "适合区域")!;
    fit.click();
    expect(Number(map.dataset.fitGeneration)).toBe(emptyGeneration + 2);
  });

  it("keeps a draggable observation marker when the ground footprint is unavailable", () => {
    const onNavigate = vi.fn();
    const { monitor } = mount({ onNavigate });
    monitor.update(snapshot({ observer: { mode: "free", position: { x: 10, z: -20 }, headingRad: Math.PI / 4 } }));
    const map = monitor.element.querySelector<SVGSVGElement>(".operations-monitor-map")!;
    setMapBox(map);
    const marker = monitor.element.querySelector(".operations-monitor-map-observer")!;
    expect(marker.getAttribute("visibility")).toBe("visible");
    expect(marker.getAttribute("aria-label")).toContain("视锥未形成完整地面足迹");
    expect(monitor.element.querySelector('[data-state="unavailable"]')?.textContent).toBe("视锥足迹不可用");
    expect(monitor.element.querySelector(".operations-monitor-map-footprint")?.getAttribute("visibility")).toBe("hidden");
    pointer(marker, "pointerdown", 300, 180, 9);
    pointer(marker, "pointermove", 330, 205, 9);
    pointer(marker, "pointerup", 330, 205, 9);
    expect(onNavigate).toHaveBeenCalledOnce();
  });

  it("prioritizes unresolved exceptions and links event selection to its map location", () => {
    const onSelect = vi.fn(), onNavigate = vi.fn();
    const { monitor } = mount({ onSelect, onNavigate });
    const rows = Array.from(monitor.element.querySelectorAll<HTMLElement>(".operations-monitor-event[data-key]"));
    expect(rows.map(row => row.dataset.key)).toEqual(["event-critical", "event-info"]);
    expect(rows[0]?.querySelector(".operations-monitor-event-evidence")?.textContent).toContain("frame-58");
    expect(rows[0]?.querySelector(".operations-monitor-event-evidence")?.textContent).toContain("订单：order-9");
    expect(rows[0]?.querySelector(".operations-monitor-event-evidence")?.textContent).toContain("依赖：handover-2");
    expect(rows[0]?.textContent).toContain("未确认");
    expect(rows[0]?.textContent).toContain("持续中");
    rows[0]!.querySelector<HTMLButtonElement>(".operations-monitor-event-main")!.click();
    expect(onSelect).toHaveBeenCalledWith({ kind: "event", id: "event-critical" });
    expect(onNavigate).toHaveBeenCalledWith({ x: 35, z: 4 });
  });

  it("counts unknown resolution honestly and pages large histories without filling the live DOM", () => {
    const events = Array.from({ length: 100 }, (_, index) => ({
      id: `event-${index}`, label: `事件 ${index}`, severity: "unknown" as const, condition: "已记录",
      timeSeconds: index, objectIds: [] as string[], position: { x: index, z: index },
    }));
    const { monitor } = mount();
    monitor.update(snapshot({ events, selected: null }));
    expect(monitor.element.querySelector(".operations-monitor-events > summary")?.textContent)
      .toBe("事件 · 0 明确持续 / 100 解决状态未知 / 100 总计");
    expect(monitor.element.querySelectorAll(".operations-monitor-event[data-key]")).toHaveLength(12);
    const mapEvents = monitor.element.querySelector(".operations-monitor-map-events")!;
    expect(mapEvents.getAttribute("data-total")).toBe("100");
    expect(mapEvents.getAttribute("data-rendered")).toBe("80");
    expect(mapEvents.children).toHaveLength(80);
    expect(monitor.element.querySelector(".operations-monitor-map-event-state")?.textContent).toBe("事件点 80/100");

    const more = monitor.element.querySelector<HTMLButtonElement>(".operations-monitor-events-more")!;
    expect(more.textContent).toBe("加载更多事件 · 88");
    more.click();
    expect(monitor.element.querySelectorAll(".operations-monitor-event[data-key]")).toHaveLength(62);
    expect(more.textContent).toBe("加载更多事件 · 38");
    more.click();
    expect(monitor.element.querySelectorAll(".operations-monitor-event[data-key]")).toHaveLength(100);
    expect(more.textContent).toBe("收起到重点事件");
    more.click();
    expect(monitor.element.querySelectorAll(".operations-monitor-event[data-key]")).toHaveLength(12);
  });

  it("groups only explicitly keyed repetitions with matching condition and preserves occurrence evidence", () => {
    const onSelect = vi.fn();
    const events = [{
      id: "smoke-1", label: "烟雾", severity: "warning" as const, condition: "持续中", timeSeconds: 10,
      objectIds: ["uav-1"], evidence: ["frame-10"], resolved: false, groupKey: "roof-smoke",
    }, {
      id: "smoke-2", label: "烟雾", severity: "warning" as const, condition: "持续中", timeSeconds: 12,
      objectIds: ["uav-1"], evidence: ["frame-12"], resolved: false, groupKey: "roof-smoke",
    }, {
      id: "smoke-clear", label: "烟雾", severity: "warning" as const, condition: "已消散", timeSeconds: 14,
      objectIds: ["uav-1"], evidence: ["frame-14"], resolved: false, groupKey: "roof-smoke",
    }];
    const { monitor } = mount({ onSelect });
    monitor.update(snapshot({ events, selected: null }));
    const rows = monitor.element.querySelectorAll<HTMLElement>(".operations-monitor-event[data-key]");
    expect(rows).toHaveLength(2);
    const repeated = Array.from(rows).find(row => row.dataset.occurrences === "2")!;
    expect(repeated.textContent).toContain("重复：2 次");
    expect(repeated.textContent).toContain("smoke-1@00:00:10");
    expect(repeated.textContent).toContain("smoke-2@00:00:12");
    expect(repeated.textContent).toContain("frame-10");
    expect(repeated.textContent).toContain("frame-12");
    repeated.querySelector<HTMLButtonElement>(".operations-monitor-event-main")!.click();
    expect(onSelect).toHaveBeenCalledWith({ kind: "event", id: "smoke-2" });
  });

  it("shows honest disconnected, missing-sensor, and empty-event states", () => {
    const { monitor } = mount();
    monitor.update(snapshot({
      objects: [{
        id: "uav-1", kind: "uav", label: "巡检机 01", position: { x: 0, y: 0, z: 0 },
        camera: { source: "live_video", state: "disconnected", reason: "链路中断" },
      }],
      events: [], observer: { mode: "free" },
    }));
    expect(monitor.element.querySelector(".operations-monitor-preview-meta")?.textContent)
      .toBe("相机来源：实时视频 · 已断开 · 链路中断");
    expect(monitor.element.querySelector<HTMLButtonElement>('[data-camera-mode="cockpit"]')?.disabled).toBe(true);
    expect(monitor.element.querySelector(".operations-monitor-event-list")?.textContent).toContain("当前数据源没有事件");

    monitor.update(snapshot({
      objects: [{ id: "uav-1", kind: "uav", label: "巡检机 01", position: { x: 0, y: 0, z: 0 } }],
      events: [], observer: { mode: "free" },
    }));
    expect(monitor.element.querySelector(".operations-monitor-preview-meta")?.textContent).toBe("相机来源：未知");
  });

  it("rejects ambiguous coordinates and duplicate stable identities", () => {
    const host = document.createElement("div"), monitor = mountOperationsMonitor(host);
    expect(() => monitor.update(snapshot({ timeSeconds: Number.NaN }))).toThrow(/timeSeconds must be finite/);
    expect(() => monitor.update(snapshot({ objects: [snapshot().objects[0]!, snapshot().objects[0]!] })))
      .toThrow(/duplicate or empty object id/);
    expect(() => monitor.update(snapshot({ events: [snapshot().events![0]!, snapshot().events![0]!] })))
      .toThrow(/duplicate or empty event id/);
  });

  it("disposes without touching the camera element supplied by the application", () => {
    const { host, monitor } = mount();
    const camera = document.createElement("canvas"); monitor.previewContainer.append(camera);
    monitor.dispose();
    expect(host.childElementCount).toBe(0);
    expect(camera.isConnected).toBe(false);
    expect(() => monitor.update(snapshot())).toThrow(/disposed/);
    expect(() => monitor.dispose()).not.toThrow();
  });
});

it("aggregates P02 static facilities while retaining the selected carrier and route", () => {
  document.body.classList.add("p02-active");
  const { host, monitor } = mount();
  try {
    monitor.update(snapshot({ objects: [...snapshot().objects, ...Array.from({ length: 180 }, (_, index) => ({
      id: `static-${index}`, kind: "facility" as const, label: `building-${index}`,
      position: { x: index % 3, y: 0, z: index % 3 },
    }))] }));
    expect(host.querySelectorAll(".operations-monitor-map-marker")).toHaveLength(2);
    expect(host.querySelector('[data-object-id="uav-1"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(host.querySelector(".operations-monitor-map-route.is-selected")).not.toBeNull();
    expect(Number((host.querySelector(".operations-monitor-map-markers") as SVGElement).dataset.staticCellCount)).toBeLessThan(10);
  } finally { monitor.dispose(); document.body.classList.remove("p02-active"); }
});


describe("selected current-frame business and monitor language", () => {
  it("renders exact selected business separately from telemetry and clears it on seek", () => {
    const { monitor } = mount();
    const selectedBusiness: NonNullable<OperationsSnapshot["selectedBusiness"]> = {
      entityId: "uav-1", cursorTick: 12, sourceKind: "authored_business_fixture",
      orders: [{ orderId: "order.exact", parcelId: "parcel.exact", carrierEntityId: "uav-1",
        custodyId: "facility.exact", custodyKind: "facility", destinationId: "destination.exact",
        attempt: 2, status: "assigned", sourceRef: "business/exact.json", tick: 10, timeSeconds: 5 }],
      attributes: [], unboundEvents: [],
    };
    monitor.update(snapshot({ selectedBusiness }));
    const row = monitor.element.querySelector<HTMLElement>(".operations-monitor-business-row")!;
    expect(row.dataset.orderId).toBe("order.exact");
    expect(row.dataset.parcelId).toBe("parcel.exact");
    expect(row.dataset.cursorTick).toBe("12");
    expect(row.querySelector('[data-business-field="custody"]')?.textContent).toBe("facility.exact");
    expect(row.querySelector('[data-business-field="destination"]')?.textContent).toBe("destination.exact");
    expect(row.querySelector('[data-business-field="source"]')?.textContent).toBe("business/exact.json");
    expect(monitor.element.querySelector(".operations-monitor-business-source")?.textContent).toBe(t("p02.sourceAuthored"));
    const physical = monitor.element.querySelector(".operations-monitor-facts")!.textContent;
    expect(physical).not.toContain("order-9");
    expect(physical).not.toContain("屋顶 A");
    monitor.update(snapshot({ selectedBusiness: { ...selectedBusiness, cursorTick: 9, orders: [] } }));
    expect(monitor.element.querySelector(".operations-monitor-business-row")).toBeNull();
    expect(monitor.element.querySelector(".operations-monitor-business-empty")?.textContent).toContain("未知");
    monitor.update(snapshot({ selectedBusiness: null }));
    expect(monitor.element.querySelector<HTMLElement>(".operations-monitor-business")?.dataset.cursorTick).toBeUndefined();
    expect(() => monitor.update(snapshot({ selectedBusiness: { ...selectedBusiness, cursorTick: 9 } }))).toThrow(/future records/);
    monitor.dispose();
  });

  it("translates all monitor chrome, tooltips and accessible names after mounting", () => {
    const { monitor } = mount();
    const english = snapshot({ sourceLabel: "Replay public run", selectedBusiness: {
      entityId: "uav-1", cursorTick: 12, sourceKind: "authored_business_fixture", attributes: [], unboundEvents: [],
      orders: [{ orderId: "order.exact", parcelId: "parcel.exact", carrierEntityId: "uav-1", custodyKind: "facility",
        custodyId: "facility.exact", destinationId: "destination.exact", attempt: 2, status: "assigned",
        sourceRef: "business/exact.json", tick: 10, timeSeconds: 5 }],
    }, objects: [{ id: "uav-1", label: "uav-1", kind: "uav", phase: "HOLD", position: { x: 1, y: 42, z: -2 },
      telemetry: { altitudeM: 42, altitudeReference: "AGL", freshness: { state: "fresh" }, health: { label: "UNKNOWN", state: "unknown" } },
      camera: { source: "simulated_rgb", state: "ready" } }], routes: [], polygons: [], events: [{
      id: "event.exact", label: "event.exact", severity: "warning", condition: "declared", timeSeconds: 4, objectIds: ["uav-1"],
    }] });
    monitor.update(english);
    setLanguage("en");
    expect(monitor.element.textContent).not.toMatch(/[\u4e00-\u9fff]/);
    for (const node of monitor.element.querySelectorAll("[aria-label], [title]")) {
      expect(node.getAttribute("aria-label") ?? "").not.toMatch(/[\u4e00-\u9fff]/);
      expect(node.getAttribute("title") ?? "").not.toMatch(/[\u4e00-\u9fff]/);
    }
    expect(monitor.element.querySelector(".operations-monitor-clock-state")?.textContent).toBe("Playing");
    expect(monitor.element.querySelector(".operations-monitor-business-source")?.textContent).toBe(t("p02.sourceAuthored", "en"));
    expect(monitor.element.querySelector('[data-camera-mode="chase"]')?.textContent).toBe("External follow");
    setLanguage("zh");
    expect(monitor.element.querySelector('[data-camera-mode="chase"]')?.textContent).toBe("外部跟随");
    monitor.dispose();
  });
});


it("keeps follow and physics ahead of compact business rows with full inspectable identity", () => {
  const onCameraMode = vi.fn();
  const { monitor } = mount({ onCameraMode });
  const selectedBusiness: NonNullable<OperationsSnapshot["selectedBusiness"]> = {
    entityId: "uav-1", cursorTick: 12, sourceKind: "authored_business_fixture", attributes: [], unboundEvents: [],
    orders: [{ orderId: "order.inspection-overlay.1", parcelId: "parcel.order.inspection-overlay.1", carrierEntityId: "uav-1",
      custodyId: "facility.exact", custodyKind: "facility", destinationId: "destination.exact", attempt: 2,
      status: "assigned", sourceRef: "business/exact.json", tick: 10, timeSeconds: 5 }],
  };
  monitor.update(snapshot({ selectedBusiness }));
  const business = monitor.element.querySelector(".operations-monitor-business")!;
  const facts = monitor.element.querySelector(".operations-monitor-facts")!;
  const actions = monitor.element.querySelector(".operations-monitor-camera-actions")!;
  expect(actions.compareDocumentPosition(facts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(facts.compareDocumentPosition(business) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const row = monitor.element.querySelector<HTMLElement>(".operations-monitor-business-row")!;
  expect(row.querySelector(".operations-monitor-business-label")?.textContent).toBe("订单 inspection-overlay.1");
  expect(row.querySelector(".operations-monitor-business-label")?.getAttribute("title")).toBe("order.inspection-overlay.1");
  const details = row.querySelector<HTMLDetailsElement>("details")!;
  expect(details.open).toBe(false);
  expect(details.querySelector('[data-business-field="order"]')?.textContent).toBe("order.inspection-overlay.1");
  expect(details.querySelector('[data-business-field="sourceKind"]')?.textContent).toBe("authored_business_fixture");
  details.open = true;
  monitor.update(snapshot({ selectedBusiness }));
  expect(monitor.element.querySelector<HTMLDetailsElement>(".operations-monitor-business-details")?.open).toBe(true);
  monitor.element.querySelector<HTMLButtonElement>('[data-camera-mode="chase"]')!.click();
  expect(onCameraMode).toHaveBeenCalledWith("chase");
  const cameraEvidence = monitor.element.querySelector<HTMLDetailsElement>(".operations-monitor-camera-evidence")!;
  expect(cameraEvidence.open).toBe(false);
  expect(cameraEvidence.querySelector("code")?.dataset.sourceKind).toBe("simulated_rgb");
  setLanguage("en");
  expect(monitor.element.querySelector(".operations-monitor-preview-meta")?.textContent).toContain("Simulated RGB");
  expect(monitor.element.querySelector(".operations-monitor-camera-evidence summary")?.textContent).toBe("Raw camera source");
  monitor.dispose();
});
