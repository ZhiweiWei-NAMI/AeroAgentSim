/** Bounded authoring-only spatial panel for one verified selected city.
 *
 * This panel operates exclusively on CitySelectedScenario — never on the legacy
 * CityWorkspaceConfig or scenePath draft. It renders the verified building boxes
 * and motor road polygons supplied by the caller in local X=east / Z=south
 * metres, and lets an author place facilities, hand-draw no-fly polygons, and
 * edit or delete either.
 *
 * Acceptance is deliberately delegated: every mutation is passed to an async
 * onChange(next) that the caller gates with the verified placement validator;
 * this panel never claims collision or feasibility verification itself. A
 * non-null return value is shown verbatim as the editor error and the draft is
 * not changed locally, so colliding facilities are never silently shrunk,
 * moved or committed. User-supplied GeoJSON follows the same acceptance gate. */
import { CHARGER_SLOT_PITCH_M, FACILITY_MIN_DIMENSIONS, LANDING_PAD_LAYOUTS, maxLandingParkingSlots,
  type FacilityCharging } from "./city-facility-models";
import type {
  CitySelectedScenario,
  SelectedScenarioFacility,
  SelectedScenarioNoFlyZone,
} from "./city-selected-scenario";
import type { RoofSupport } from "./city-selected-placement";
import type { SourceBuildingTriangleRange } from "./city-building-shape";
import { proposeGroundSnap, proposeRoofSnap, type SnapCandidate, type SnapContext } from "./city-selected-snapping";
import {
  evaluateSpatialRoadClearance,
  spatialRoadBlockingPolygons,
  type SpatialRoadClearance,
} from "./city-spatial-road-clearance";
import {
  importAirspaceGeoJSON, validateAirspacePolygon,
  type AirspacePolygon,
  type PlacementBox,
  type PointXZ,
  type RoadPolygon,
} from "./city-workspace-geometry";

/** Verified map inputs for one selected scene. No geometry is invented here:
 * buildings and motor roads come from the verified presentation data. */
export interface SelectedSpatialMapData {
  readonly origin: { readonly latitude_deg: number; readonly longitude_deg: number };
  readonly extent: { readonly minX: number; readonly maxX: number; readonly minZ: number; readonly maxZ: number };
  readonly buildings: readonly PlacementBox[];
  readonly motorRoads: readonly RoadPolygon[];
  readonly roadClearance: SpatialRoadClearance;
  /** Measured street-asset collision boxes from the assembled scene, when available. */
  readonly staticObstacles?: readonly PlacementBox[];
  /** Complete verified building footprints eligible to support a rooftop site. */
  readonly roofSupports: readonly RoofSupport[];
  /** Source triangles of the exactly loaded selected scene, bound to the current
   * authoring map key; used to verify rooftop net volume before applying a snap. */
  readonly rooftopMesh?: ReadonlyMap<string, SourceBuildingTriangleRange[]>;
}

/** Async accept gate: the caller runs verified collision validation and saves
 * only edits that return null. A specific error string is surfaced as-is. */
export type SelectedSpatialChangeHandler = (next: CitySelectedScenario) => Promise<string | null>;

type Tool = SelectedScenarioFacility["kind"] | "nofly" | null;
type PlacementMode = "ground" | "rooftop";

const SVG_NS = "http://www.w3.org/2000/svg";

const toolNames: Record<SelectedScenarioFacility["kind"], string> = {
  vertiport: "起降点", hub: "物流中转站", charger: "充电站",
};

const toolLabels: Record<Exclude<Tool, null>, string> = {
  vertiport: "放置起降点", hub: "放置物流中转站",
  charger: "放置充电站", nofly: "绘制禁飞区",
};

type CapabilityDefaults = Pick<SelectedScenarioFacility,
  "widthM" | "depthM" | "heightM" | "landing" | "cargo" | "charging">;

function chargingCapability(slots: number, powerW: number): FacilityCharging {
  return { slots, powerW, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" };
}

/** Human-scale defaults above every kind's model minimum. Capabilities are explicit. */
const defaults: Record<SelectedScenarioFacility["kind"], CapabilityDefaults> = {
  vertiport: { widthM: 14, depthM: 10, heightM: 4.5,
    landing: { parkingSlots: 1, movementsPerHour: 30 }, cargo: null, charging: null },
  hub: { widthM: 18, depthM: 14, heightM: 5.2,
    landing: { parkingSlots: 2, movementsPerHour: 20 }, cargo: { storageCapacityKg: 500, throughputPerHourKg: 1000 },
    charging: null },
  charger: { widthM: 10, depthM: 8, heightM: 3.2, landing: null, cargo: null,
    charging: chargingCapability(3, 6000) },
};

const noflyDefaults = { name: "手工禁飞区", floorM: 0, ceilingM: 120, startsAtS: 0, endsAtS: null as number | null };

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K, className?: string, text?: string,
): HTMLElementTagNameMap[K] {
  const result = document.createElement(tag);
  if (className !== undefined) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function svgElement<K extends keyof SVGElementTagNameMap>(
  tag: K, attributes: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const result = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) result.setAttribute(key, String(value));
  return result;
}

function button(label: string, action: () => void, className = "studio-button"): HTMLButtonElement {
  const result = element("button", className, label);
  result.type = "button";
  result.addEventListener("click", action);
  return result;
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

function numericField(
  label: string, value: number | null, action: (value: number | null) => void,
  options: { step?: string; min?: number; nullable?: boolean } = {},
): HTMLLabelElement {
  const wrapper = element("label", "studio-field");
  const caption = element("span", "studio-field-label", label);
  const input = element("input", "studio-input");
  input.type = "number";
  input.step = options.step ?? "any";
  if (options.min !== undefined) input.min = String(options.min);
  input.value = value === null ? "" : String(value);
  input.addEventListener("change", () => {
    if (options.nullable && input.value.trim() === "") action(null);
    else action(input.value.trim() === "" ? NaN : input.valueAsNumber);
  });
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

function pathOf(points: readonly PointXZ[]): string {  return points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x},${point.z}`).join(" ") + " Z";
}

function pointLabel(point: PointXZ): string {
  return `X ${point.x.toFixed(2)} m · Z ${point.z.toFixed(2)} m`;
}

class SelectedSpatialPanel {
  private scenario: CitySelectedScenario;
  private onChange: SelectedSpatialChangeHandler;
  private data: SelectedSpatialMapData;
  private tool: Tool = null;
  private cursor: PointXZ;
  private vertices: PointXZ[] = [];
  private selectedFacilityId: string | null = null;
  private selectedZoneId: string | null = null;
  private error: string | null = null;
  private busy = false;
  private noFlyName = noflyDefaults.name;
  private floorM = noflyDefaults.floorM;
  private ceilingM = noflyDefaults.ceilingM;
  private startsAtS = noflyDefaults.startsAtS;
  private endsAtS: number | null = noflyDefaults.endsAtS;
  private placementMode: PlacementMode = "ground";
  private roofBuildingId: string | null = null;
  private pending: { facility: SelectedScenarioFacility; candidate: SnapCandidate } | null = null;
  private onFocus: ((facilityId: string) => boolean) | undefined;

  constructor(
    private readonly root: HTMLElement,
    scenario: CitySelectedScenario,
    onChange: SelectedSpatialChangeHandler,
    data: SelectedSpatialMapData,
    onFocus?: (facilityId: string) => boolean,
  ) {
    this.scenario = scenario;
    this.onChange = onChange;
    this.data = data;
    this.onFocus = onFocus;
    this.cursor = {
      x: (data.extent.minX + data.extent.maxX) / 2,
      z: (data.extent.minZ + data.extent.maxZ) / 2,
    };
  }

  update(scenario: CitySelectedScenario, onChange: SelectedSpatialChangeHandler, data: SelectedSpatialMapData,
         onFocus?: (facilityId: string) => boolean): void {
    const old = this.data;
    const changedScene = old.origin.latitude_deg !== data.origin.latitude_deg
      || old.origin.longitude_deg !== data.origin.longitude_deg
      || old.extent.minX !== data.extent.minX || old.extent.maxX !== data.extent.maxX
      || old.extent.minZ !== data.extent.minZ || old.extent.maxZ !== data.extent.maxZ;
    this.scenario = scenario;
    this.onChange = onChange;
    this.data = data;
    this.onFocus = onFocus;
    if (changedScene) {
      this.cursor = {
        x: (data.extent.minX + data.extent.maxX) / 2,
        z: (data.extent.minZ + data.extent.maxZ) / 2,
      };
      this.tool = null;
      this.vertices = [];
      this.selectedFacilityId = null;
      this.selectedZoneId = null;
      this.error = null;
      this.pending = null;
      this.roofBuildingId = null;
      this.placementMode = "ground";
    }
    this.render();
  }

  /** The single commit path: the caller decides via the async callback. */
  private async apply(next: CitySelectedScenario, onAccepted?: () => void): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    let error: string | null;
    try {
      error = await this.onChange(next);
    } catch (cause) {
      error = errorMessage(cause);
    }
    this.busy = false;
    this.error = error;
    if (error === null) {
      this.scenario = next;
      onAccepted?.();
    }
    if (this.root.querySelector(".studio-spatial-panel") !== null) this.render();
  }

  private nextId(prefix: string, current: readonly { readonly id: string }[]): string {
    const known = new Set(current.map(item => item.id));
    let counter = 1;
    while (known.has(`${prefix}-${counter}`)) counter++;
    return `${prefix}-${counter}`;
  }

  private facilityBasics(facility: SelectedScenarioFacility): string | null {
    if ([facility.position.x, facility.position.z, facility.rotationDeg,
      facility.widthM, facility.depthM, facility.heightM].some(value => !Number.isFinite(value))) {
      return "设施几何数值必须为有限数值";
    }
    if (!facility.name.trim()) return "设施名称不能为空";
    const minimum = FACILITY_MIN_DIMENSIONS[facility.kind];
    if (facility.widthM < minimum.widthM || facility.depthM < minimum.depthM
        || facility.heightM < minimum.heightM) {
      return `${toolNames[facility.kind]}最小可容纳尺寸：宽 ${minimum.widthM} m、深 ${minimum.depthM} m、`
        + `高 ${minimum.heightM} m；当前为 ${facility.widthM} × ${facility.depthM} × ${facility.heightM} m`;
    }
    if (facility.landing !== null) {
      if (!Number.isSafeInteger(facility.landing.parkingSlots) || facility.landing.parkingSlots < 1) {
        return "并行起降位数量必须为正整数";
      }
      if (facility.kind === "vertiport" || facility.kind === "hub") {
        const limit = maxLandingParkingSlots(facility.kind, facility.widthM);
        if (facility.landing.parkingSlots > limit) {
          const layout = LANDING_PAD_LAYOUTS[facility.kind];
          return `并行起降位 ${facility.landing.parkingSlots} 超出 ${facility.widthM}m 宽场地`
            + `（${layout.widthM}m 起降位按 ${layout.pitchM}m 间距单行布置，最多 ${limit} 个）`;
        }
      }
    }
    if (facility.charging !== null) {
      if (!Number.isSafeInteger(facility.charging.slots) || facility.charging.slots < 1) {
        return "充电位数量必须为正整数";
      }
      if (!Number.isFinite(facility.charging.powerW) || facility.charging.powerW <= 0) {
        return "充电功率必须为大于 0 的数值";
      }
      if (facility.charging.slots * CHARGER_SLOT_PITCH_M > facility.widthM + 1e-9) {
        return `充电位数量 ${facility.charging.slots} 超出 ${facility.widthM}m 宽的场地`;
      }
      if (facility.kind === "charger" && facility.charging.slots > 3) {
        return "独立充电站模型最多容纳 3 个充电位";
      }
    }
    if (facility.placement === "rooftop" && facility.supportHeightM === null) {
      return "屋顶放置必须具有核验支撑高度";
    }
    const { minX, maxX, minZ, maxZ } = this.data.extent;
    if (facility.position.x < minX || facility.position.x > maxX
        || facility.position.z < minZ || facility.position.z > maxZ) {
      return "设施坐标超出当前选中城市范围";
    }
    return null;
  }

  private snapContext(): SnapContext {
    const airspace: AirspacePolygon[] = [];
    for (const zone of this.scenario.noFlyZones) {
      if (validateAirspacePolygon(zone).length === 0) {
        airspace.push({ ...zone, source: { kind: zone.source.kind, label: zone.source.label,
          uri: zone.source.uri === null ? undefined : zone.source.uri } });
      }
    }
    return {
      extent: this.data.extent,
      buildings: this.data.buildings,
      staticObstacles: this.data.staticObstacles ?? [],
      roads: spatialRoadBlockingPolygons(this.data.roadClearance),
      facilities: this.scenario.facilities,
      airspace,
      roofSupports: this.data.roofSupports,
      rooftopMesh: this.data.rooftopMesh,
    };
  }

  private placeAt(point: PointXZ): void {
    this.cursor = point;
    if (this.tool === "nofly") {
      this.vertices.push(point);
      this.error = null;
      this.render();
      return;
    }
    if (this.tool === null) {
      this.render();
      return;
    }
    const kind = this.tool;
    const rooftop = kind === "vertiport" && this.placementMode === "rooftop";
    if (rooftop && this.roofBuildingId === null) {
      this.error = "屋顶放置前请先选择具有完整核验足迹的建筑";
      this.render();
      return;
    }
    const base: SelectedScenarioFacility = {
      id: this.nextId("facility", this.scenario.facilities),
      name: `${toolNames[kind]} ${this.scenario.facilities.filter(item => item.kind === kind).length + 1}`,
      kind,
      placement: rooftop ? "rooftop" : "ground",
      buildingId: rooftop ? this.roofBuildingId : null,
      supportHeightM: null,
      position: point,
      rotationDeg: 0,
      ...defaults[kind],
    };
    const candidate = rooftop
      ? proposeRoofSnap(base, this.snapContext(), this.roofBuildingId!, point)
      : proposeGroundSnap(base, this.snapContext(), point);
    this.pending = { facility: base, candidate };
    this.error = null;
    this.render();
  }

  private async confirmPending(): Promise<void> {
    const pending = this.pending;
    if (pending === null) return;
    if (!pending.candidate.legal) {
      this.error = `候选位置不可应用：${pending.candidate.reason}`;
      this.render();
      return;
    }
    const facility: SelectedScenarioFacility = {
      ...pending.facility,
      position: pending.candidate.position,
      rotationDeg: pending.candidate.rotationDeg,
      placement: pending.candidate.supportHeightM === null ? "ground" : "rooftop",
      supportHeightM: pending.candidate.supportHeightM,
      buildingId: pending.candidate.supportHeightM === null ? null : pending.facility.buildingId,
    };
    const issue = this.facilityBasics(facility);
    if (issue !== null) {
      this.error = issue;
      this.render();
      return;
    }
    const roadIssues = evaluateSpatialRoadClearance(facility, this.data.roadClearance)
      .filter(item => item.severity === "block");
    if (roadIssues.length > 0) {
      this.error = roadIssues.map(item => item.message).join("；");
      this.render();
      return;
    }
    await this.apply({ ...this.scenario, facilities: [...this.scenario.facilities, facility] },
      () => { this.pending = null; });
  }

  private patchFacility(facility: SelectedScenarioFacility, patch: Partial<SelectedScenarioFacility>): void {
    const next = { ...facility, ...patch };
    const issue = this.facilityBasics(next);
    if (issue !== null) {
      this.error = issue;
      this.render();
      return;
    }
    const roadIssues = evaluateSpatialRoadClearance(next, this.data.roadClearance)
      .filter(item => item.severity === "block");
    if (roadIssues.length > 0) {
      this.error = roadIssues.map(item => item.message).join("；");
      this.render();
      return;
    }
    void this.apply({
      ...this.scenario,
      facilities: this.scenario.facilities.map(item => item.id === facility.id ? next : item),
    });
  }

  private convertToRoof(facility: SelectedScenarioFacility): void {
    if (this.roofBuildingId === null) {
      this.error = "请先在工具区选择一处具有完整核验足迹的建筑";
      this.render();
      return;
    }
    const candidate = proposeRoofSnap({ ...facility, kind: "vertiport" }, this.snapContext(),
      this.roofBuildingId, facility.position);
    if (!candidate.legal) {
      this.error = candidate.reason;
      this.render();
      return;
    }
    this.patchFacility(facility, { placement: "rooftop", buildingId: this.roofBuildingId,
      supportHeightM: candidate.supportHeightM, position: candidate.position, rotationDeg: candidate.rotationDeg });
  }

  private finishPolygon(): void {
    const shapeIssue = validateAirspacePolygon({ polygon: this.vertices, floorM: this.floorM, ceilingM: this.ceilingM });
    if (shapeIssue.length) {
      this.error = shapeIssue.map(issue => issue.message).join("；");
      this.render();
      return;
    }
    if (!Number.isFinite(this.startsAtS) || this.startsAtS < 0
        || (this.endsAtS !== null && (!Number.isFinite(this.endsAtS) || this.endsAtS < this.startsAtS))) {
      this.error = "生效时间必须为非负秒，结束时间不得早于开始时间";
      this.render();
      return;
    }
    if (!this.noFlyName.trim()) {
      this.error = "禁飞区名称不能为空";
      this.render();
      return;
    }
    const zone: SelectedScenarioNoFlyZone = {
      id: this.nextId("nfz", this.scenario.noFlyZones),
      name: this.noFlyName.trim(),
      polygon: [...this.vertices],
      floorM: this.floorM,
      ceilingM: this.ceilingM,
      startsAtS: this.startsAtS,
      endsAtS: this.endsAtS,
      source: { kind: "manual", label: "用户手工标绘", uri: null },
    };
    void this.apply({ ...this.scenario, noFlyZones: [...this.scenario.noFlyZones, zone] }, () => {
      this.vertices = [];
    });
  }

  private patchZone(current: SelectedScenarioNoFlyZone, patch: Partial<SelectedScenarioNoFlyZone>): void {
    const next = { ...current, ...patch };
    const shapeIssue = validateAirspacePolygon(next);
    if (shapeIssue.length) {
      this.error = shapeIssue.map(issue => issue.message).join("；");
      this.render();
      return;
    }
    if (!next.name.trim()) {
      this.error = "禁飞区名称不能为空";
      this.render();
      return;
    }
    if (!Number.isFinite(next.startsAtS) || next.startsAtS < 0
        || (next.endsAtS !== null && (!Number.isFinite(next.endsAtS) || next.endsAtS < next.startsAtS))) {
      this.error = "生效时间必须为非负秒，结束时间不得早于开始时间";
      this.render();
      return;
    }
    void this.apply({
      ...this.scenario,
      noFlyZones: this.scenario.noFlyZones.map(item => item.id === current.id ? next : item),
    });
  }

  private importGeoJSON(value: unknown, label: string, uri: string | null): void {
    try {
      const regions = importAirspaceGeoJSON(value, this.data.origin, label, {
        floorM: this.floorM, ceilingM: this.ceilingM,
        startsAtS: this.startsAtS, endsAtS: this.endsAtS,
      });
      if (!regions.length) throw new Error("GeoJSON 中没有可导入的 Polygon 或 MultiPolygon");
      const known = new Set(this.scenario.noFlyZones.map(zone => zone.id));
      const fresh: SelectedScenarioNoFlyZone[] = regions.filter(region => !known.has(region.id))
        .map(region => ({
          ...region, polygon: [...region.polygon],
          source: { kind: "geojson", label: region.source.label, uri },
        }));
      if (!fresh.length) throw new Error("这些 GeoJSON 禁飞区已导入");
      void this.apply({ ...this.scenario, noFlyZones: [...this.scenario.noFlyZones, ...fresh] });
    } catch (cause) {
      this.error = `GeoJSON 导入失败：${errorMessage(cause)}`;
      this.render();
    }
  }

  private clickPoint(event: MouseEvent, svg: SVGSVGElement): PointXZ | null {
    const box = svg.getBoundingClientRect();
    const viewWidth = this.data.extent.maxX - this.data.extent.minX;
    const viewHeight = this.data.extent.maxZ - this.data.extent.minZ;
    const scale = Math.min(box.width / viewWidth, box.height / viewHeight);
    if (!Number.isFinite(scale) || scale <= 0) return null;
    const left = box.left + (box.width - viewWidth * scale) / 2;
    const top = box.top + (box.height - viewHeight * scale) / 2;
    const x = (event.clientX - left) / scale;
    const z = (event.clientY - top) / scale;
    if (x < 0 || x > viewWidth || z < 0 || z > viewHeight) return null;
    return { x: this.data.extent.minX + x, z: this.data.extent.minZ + z };
  }

  private map(): SVGSVGElement {
    const { minX, maxX, minZ, maxZ } = this.data.extent;
    if (![minX, maxX, minZ, maxZ].every(Number.isFinite) || maxX <= minX || maxZ <= minZ) {
      throw new Error("选中城市地图范围无效");
    }
    const width = maxX - minX;
    const height = maxZ - minZ;
    const map = svgElement("svg", {
      viewBox: `${minX} ${minZ} ${width} ${height}`,
      role: "group",
      "aria-label": "当前选中城市道路、建筑、设施与禁飞区地图",
      preserveAspectRatio: "xMidYMid meet",
    });
    map.classList.add("studio-spatial-map");
    map.style.width = "100%";
    map.style.height = "min(56vh, 680px)";
    map.style.display = "block";
    map.style.background = "#12202b";
    map.style.cursor = "crosshair";
    map.append(svgElement("rect", { x: minX, y: minZ, width, height, fill: "#12202b" }));

    for (const road of this.data.roadClearance.roadbed) {
      const outline = pathOf(road.outline);
      const holes = road.holes.map(pathOf).join(" ");
      map.append(svgElement("path", {
        d: `${outline} ${holes}`, fill: "#53616b", "fill-rule": "evenodd",
        stroke: "#71808a", "stroke-width": 0.35,
      }));
    }
    for (const walkbed of this.data.roadClearance.walkbed) {
      const outline = pathOf(walkbed.outline);
      const holes = walkbed.holes.map(pathOf).join(" ");
      map.append(svgElement("path", {
        d: `${outline} ${holes}`, fill: "#817b6d", "fill-rule": "evenodd",
        stroke: "#a79f8d", "stroke-width": 0.3,
      }));
    }
    for (const crossing of this.data.roadClearance.crossings) {
      map.append(svgElement("path", { d: pathOf(crossing.outline), fill: "#d9d7ceaa",
        stroke: "#f3f2ed", "stroke-width": 0.25 }));
    }
    for (const fixture of this.data.roadClearance.fixtures) {
      map.append(svgElement("rect", {
        x: fixture.x - fixture.widthM / 2, y: fixture.z - fixture.depthM / 2,
        width: fixture.widthM, height: fixture.depthM,
        transform: `rotate(${-fixture.rotationDeg} ${fixture.x} ${fixture.z})`,
        fill: "#f59e0b33", stroke: "#f59e0b", "stroke-width": 0.25,
      }));
    }
    for (const building of this.data.buildings) {
      map.append(svgElement("rect", {
        x: building.x - building.widthM / 2,
        y: building.z - building.depthM / 2,
        width: building.widthM,
        height: building.depthM,
        transform: `rotate(${-building.rotationDeg} ${building.x} ${building.z})`,
        fill: "#334756", stroke: "#70899a", "stroke-width": 0.5,
      }));
    }
    for (const zone of this.scenario.noFlyZones) {
      const shape = svgElement("path", {
        d: pathOf(zone.polygon), fill: "#f0525255", stroke: "#fb7185",
        "stroke-width": this.selectedZoneId === zone.id ? 3 : 1.8,
        "stroke-dasharray": "5 3", tabindex: 0, role: "button",
        "aria-label": `选中${zone.name}`,
      });
      shape.addEventListener("click", event => {
        if (this.tool !== null) return;
        event.stopPropagation();
        this.selectedZoneId = zone.id;
        this.selectedFacilityId = null;
        this.cursor = zone.polygon[0] ?? this.cursor;
        this.render();
      });
      shape.append(svgElement("title", {}));
      shape.lastElementChild!.textContent = `${zone.name} · ${zone.floorM}–${zone.ceilingM} m`;
      map.append(shape);
    }
    for (const facility of this.scenario.facilities) {
      const color = facility.kind === "vertiport" ? "#38bdf8" : facility.kind === "hub" ? "#fbbf24" : "#4ade80";
      const rect = svgElement("rect", {
        x: facility.position.x - facility.widthM / 2,
        y: facility.position.z - facility.depthM / 2,
        width: facility.widthM,
        height: facility.depthM,
        transform: `rotate(${-facility.rotationDeg} ${facility.position.x} ${facility.position.z})`,
        fill: `${color}77`, stroke: color,
        "stroke-width": this.selectedFacilityId === facility.id ? 3 : 1.5,
        tabindex: 0, role: "button",
        "aria-label": `选中${facility.name}`,
      });
      rect.addEventListener("click", event => {
        if (this.tool !== null) return;
        event.stopPropagation();
        this.selectedFacilityId = facility.id;
        this.selectedZoneId = null;
        this.cursor = facility.position;
        this.render();
      });
      rect.append(svgElement("title", {}));
      rect.lastElementChild!.textContent = `${facility.name} · ${pointLabel(facility.position)}`;
      map.append(rect);
    }
    if (this.pending !== null) {
      const candidate = this.pending.candidate;
      map.append(svgElement("rect", {
        x: candidate.position.x - this.pending.facility.widthM / 2,
        y: candidate.position.z - this.pending.facility.depthM / 2,
        width: this.pending.facility.widthM, height: this.pending.facility.depthM,
        transform: `rotate(${-candidate.rotationDeg} ${candidate.position.x} ${candidate.position.z})`,
        fill: candidate.legal ? "#38bdf855" : "#f0525244",
        stroke: candidate.legal ? "#a5f3fc" : "#fb7185",
        "stroke-width": 2, "stroke-dasharray": "4 3", "pointer-events": "none",
      }));
      map.append(svgElement("line", {
        x1: this.cursor.x, y1: this.cursor.z, x2: candidate.position.x, y2: candidate.position.z,
        stroke: "#e2e8f0", "stroke-width": 1, "stroke-dasharray": "2 2", "pointer-events": "none",
      }));
    }
    if (this.vertices.length) {
      const coordinates = this.vertices.map(point => `${point.x},${point.z}`).join(" ");
      map.append(svgElement("polyline", {
        points: coordinates, fill: "none", stroke: "#fb7185", "stroke-width": 2.5,
      }));
      for (const vertex of this.vertices) {
        map.append(svgElement("circle", { cx: vertex.x, cy: vertex.z, r: 2.5, fill: "#fb7185" }));
      }
    }
    map.append(svgElement("circle", {
      cx: this.cursor.x, cy: this.cursor.z, r: 2, fill: "#fff",
      stroke: "#0f172a", "stroke-width": 0.8, "pointer-events": "none",
    }));
    map.addEventListener("click", event => {
      const point = this.clickPoint(event, map);
      if (point !== null) this.placeAt(point);
    });
    return map;
  }

  private facilityList(): HTMLElement {
    const section = element("section", "studio-spatial-facilities");
    section.append(element("h3", "studio-section-title", `设施 (${this.scenario.facilities.length})`));
    if (!this.scenario.facilities.length) {
      section.append(element("p", "studio-empty", "选择工具后单击地图放置设施。"));
    }
    for (const facility of this.scenario.facilities) {
      const card = element("article", "studio-spatial-card");
      card.dataset.facilityId = facility.id;
      card.append(element("h4", "studio-card-title", `${toolNames[facility.kind]} · ${facility.name}`));
      if (this.selectedFacilityId === facility.id && this.error !== null) {
        card.append(element("p", "studio-spatial-error", this.error));
      }
      card.append(element("p", "studio-coordinate", pointLabel(facility.position)));
      card.append(element("p", "studio-source",
        `放置：${facility.placement === "rooftop" ? `屋顶（建筑 ${facility.buildingId ?? "?"}，支撑 ${facility.supportHeightM} m）` : "地面"}`));
      const clearance = evaluateSpatialRoadClearance(facility, this.data.roadClearance);
      const clearanceNote = element("p", clearance.some(issue => issue.severity === "block")
        ? "studio-spatial-error" : "studio-note", clearance.map(issue => issue.message).join("；"));
      clearanceNote.dataset.role = "facility-road-clearance";
      card.append(clearanceNote);
      const fields = element("div", "studio-field-grid");
      const minimum = FACILITY_MIN_DIMENSIONS[facility.kind];
      fields.append(
        textField("名称", facility.name, name => this.patchFacility(facility, { name })),
        numericField("X / m", facility.position.x, x => this.patchFacility(facility, { position: { ...facility.position, x: x! } })),
        numericField("Z / m", facility.position.z, z => this.patchFacility(facility, { position: { ...facility.position, z: z! } })),
        numericField("朝向 / °", facility.rotationDeg, rotationDeg => this.patchFacility(facility, { rotationDeg: rotationDeg! })),
        numericField("宽 / m", facility.widthM, widthM => this.patchFacility(facility, { widthM: widthM! }), { min: minimum.widthM }),
        numericField("深 / m", facility.depthM, depthM => this.patchFacility(facility, { depthM: depthM! }), { min: minimum.depthM }),
        numericField("高 / m", facility.heightM, heightM => this.patchFacility(facility, { heightM: heightM! }), { min: minimum.heightM }),
      );
      if (facility.landing !== null) {
        fields.append(
          numericField("并行起降位数", facility.landing.parkingSlots,
            parkingSlots => this.patchFacility(facility,
              { landing: { ...facility.landing!, parkingSlots: parkingSlots! } }), { min: 1, step: "1" }),
          numericField("每个起降位处理能力 / (架次·小时⁻¹)", facility.landing.movementsPerHour,
            movementsPerHour => this.patchFacility(facility,
              { landing: { ...facility.landing!, movementsPerHour: movementsPerHour! } }), { min: 0 }),
        );
      }
      if (facility.cargo !== null) {
        fields.append(
          numericField("货站存储容量 / kg", facility.cargo.storageCapacityKg,
            storageCapacityKg => this.patchFacility(facility,
              { cargo: { ...facility.cargo!, storageCapacityKg: storageCapacityKg! } }), { min: 0 }),
          numericField("货站处理能力 / (kg·小时⁻¹)", facility.cargo.throughputPerHourKg,
            throughputPerHourKg => this.patchFacility(facility,
              { cargo: { ...facility.cargo!, throughputPerHourKg: throughputPerHourKg! } }), { min: 0 }),
        );
      }
      if (facility.charging !== null) {
        const charging = facility.charging;
        fields.append(
          numericField("充电位数", charging.slots,
            slots => this.patchFacility(facility, { charging: { ...charging, slots: slots! } }), { min: 1, step: "1" }),
          numericField("充电功率 / W", charging.powerW,
            powerW => this.patchFacility(facility, { charging: { ...charging, powerW: powerW! } }), { min: 0 }),
          numericField("充电价格 · 每 kWh", charging.priceAmount,
            priceAmount => this.patchFacility(facility, { charging: { ...charging, priceAmount: priceAmount! } }), { min: 0 }),
          textField("价格货币（三位）", charging.priceCurrency,
            priceCurrency => this.patchFacility(facility, { charging: { ...charging, priceCurrency } })),
        );
      }
      card.append(fields);
      if (facility.kind === "vertiport") {
        if (facility.placement === "ground") {
          card.append(button(this.data.roofSupports.length === 0
            ? "屋顶放置当前不支持：来源无法证明平坦屋顶"
            : "改为屋顶放置（需先选建筑）", () => this.convertToRoof(facility)));
        } else {
          card.append(button("改为地面放置", () => this.patchFacility(facility,
            { placement: "ground", buildingId: null, supportHeightM: null })));
        }
      }
      if (this.onFocus !== undefined) card.append(button("在三维视图定位", () => {
        if (!this.onFocus?.(facility.id)) {
          this.error = `设施 ${facility.name} 的三维实例尚未就绪`;
          this.selectedFacilityId = facility.id;
          this.render();
        }
      }));
      card.append(button("删除设施", () => void this.apply({
        ...this.scenario,
        facilities: this.scenario.facilities.filter(item => item.id !== facility.id),
      }), "studio-button studio-danger"));
      section.append(card);
    }
    return section;
  }

  private zoneList(): HTMLElement {
    const section = element("section", "studio-spatial-airspace");
    section.append(element("h3", "studio-section-title", `禁飞区 (${this.scenario.noFlyZones.length})`));
    if (!this.scenario.noFlyZones.length) {
      section.append(element("p", "studio-empty", "选择“绘制禁飞区”后单击地图收集顶点。"));
    }
    for (const zone of this.scenario.noFlyZones) {
      const card = element("article", "studio-spatial-card");
      card.dataset.zoneId = zone.id;
      card.append(element("h4", "studio-card-title", zone.name));
      card.append(element("p", "studio-source",
        `来源：${zone.source.label}（${zone.source.kind === "geojson" ? "用户/外部 GeoJSON" : "手工标绘"}）`));
      if (this.selectedZoneId === zone.id && this.error !== null) {
        card.append(element("p", "studio-spatial-error", this.error));
      }
      const fields = element("div", "studio-field-grid");
      fields.append(
        textField("名称", zone.name, name => this.patchZone(zone, { name })),
        numericField("下限 / m", zone.floorM, floorM => this.patchZone(zone, { floorM: floorM! }), { min: 0 }),
        numericField("上限 / m", zone.ceilingM, ceilingM => this.patchZone(zone, { ceilingM: ceilingM! }), { min: 0 }),
        numericField("开始 / s", zone.startsAtS, startsAtS => this.patchZone(zone, { startsAtS: startsAtS! }), { min: 0 }),
        numericField("结束 / s（空为持续）", zone.endsAtS, endsAtS => this.patchZone(zone, { endsAtS }), { min: 0, nullable: true }),
      );
      card.append(fields);
      card.append(button("删除禁飞区", () => void this.apply({
        ...this.scenario,
        noFlyZones: this.scenario.noFlyZones.filter(item => item.id !== zone.id),
      }), "studio-button studio-danger"));
      section.append(card);
    }
    return section;
  }

  render(): void {
    const panel = element("section", "studio-spatial-panel");
    panel.append(element("h2", "studio-panel-title", "设施与空域"));
    panel.append(element("p", "studio-help",
      "当前选中城市按实际米显示：建筑与机动车道路来自已验证呈现资料。X 向东，Z 向南；"
      + "每次修改都需通过位置校验后才保存，冲突不会被静默缩小或吸附。"));
    const clearance = this.data.roadClearance;
    const clearanceStatus = element("p", "studio-note",
      `编辑净空来源：选区静态道路与已测量街道设施 · 机动车道 ${clearance.roadbed.length}`
      + ` · 人行铺装 ${clearance.walkbed.length} · 人行横道片段 ${clearance.crossings.length}`
      + ` · 有效设施 ${clearance.fixtures.length} · 表面摘要 ${clearance.provenance.displayedSurfaceSha256.slice(0, 12)}…。`
      + "这是编辑预览净空，不是正式物理验证。" );
    clearanceStatus.dataset.role = "spatial-road-clearance";
    panel.append(clearanceStatus);

    const tools = element("div", "studio-spatial-tools");
    for (const [tool, label] of [
      ["vertiport", toolLabels.vertiport], ["hub", toolLabels.hub],
      ["charger", toolLabels.charger], ["nofly", toolLabels.nofly],
    ] as const) {
      const control = button(label, () => {
        this.tool = tool;
        this.error = null;
        this.render();
      });
      control.setAttribute("aria-pressed", String(this.tool === tool));
      control.disabled = this.busy;
      tools.append(control);
    }
    tools.append(button("仅选点", () => {
      this.tool = null;
      this.error = null;
      this.render();
    }));
    panel.append(tools);

    if (this.tool === "vertiport") {
      const placement = element("div", "studio-field-grid studio-spatial-placement");
      placement.append(
        selectField("起降点放置", this.placementMode,
          [["ground", "地面"], ["rooftop", "屋顶（仅限核验建筑）"]] as const, mode => {
            this.placementMode = mode;
            this.pending = null;
            this.render();
          }),
      );
      if (this.placementMode === "rooftop") {
        const options = this.data.roofSupports.map(support =>
          [support.buildingId, `${support.buildingId}（顶面 ${support.topY} m）`] as const);
        if (options.length === 0) {
          placement.append(element("p", "studio-note",
            "屋顶放置当前不可用：建筑放置源只有完整碰撞包络，包络顶部是最高点而不是平坦屋顶表面，"
            + "来源无法证明支撑面；本版本不虚构屋顶高度或拓扑，也不把包络顶标成已核验屋顶。"));
        } else {
          placement.append(selectField("核验支撑建筑", this.roofBuildingId ?? options[0]![0], options,
            buildingId => { this.roofBuildingId = buildingId; this.pending = null; this.render(); }));
        }
      }
      panel.append(placement);
    }

    if (this.pending !== null) {
      const pendingSection = element("section", "studio-spatial-pending");
      pendingSection.append(element("h3", "studio-section-title", "候选位置（确认后应用）"));
      pendingSection.append(element("p", "studio-note",
        `位移 ${this.pending.candidate.displacementM.toFixed(2)} m · 朝向 ${this.pending.candidate.rotationDeg}° · `
        + `${this.pending.candidate.legal ? "合法" : "不合法"}：${this.pending.candidate.reason}`));
      if (this.pending.candidate.issues.length) {
        pendingSection.append(element("p", "studio-spatial-error", this.pending.candidate.issues.join("；")));
      }
      const actions = element("div", "studio-row");
      actions.append(
        button("确认放置", () => void this.confirmPending(), "studio-button studio-button-primary"),
        button("取消", () => { this.pending = null; this.error = null; this.render(); }),
      );
      pendingSection.append(actions);
      panel.append(pendingSection);
    }

    const mapSection = element("section", "studio-spatial-map-section");
    mapSection.append(this.map());

    const coordinates = element("div", "studio-field-grid studio-spatial-coordinates");
    coordinates.append(
      numericField("选点 X / m", this.cursor.x, value => {
        if (!Number.isFinite(value) || value! < this.data.extent.minX || value! > this.data.extent.maxX) {
          this.error = "X 坐标必须位于当前选中城市范围内";
          this.render();
          return;
        }
        this.cursor = { ...this.cursor, x: value! };
        this.render();
      }),
      numericField("选点 Z / m", this.cursor.z, value => {
        if (!Number.isFinite(value) || value! < this.data.extent.minZ || value! > this.data.extent.maxZ) {
          this.error = "Z 坐标必须位于当前选中城市范围内";
          this.render();
          return;
        }
        this.cursor = { ...this.cursor, z: value! };
        this.render();
      }),
      button(this.tool === "nofly" ? "添加顶点" : "在坐标处放置", () => this.placeAt(this.cursor)),
    );
    const status = element("p", "studio-spatial-error", this.error ?? "");
    status.setAttribute("role", "alert");
    status.hidden = this.error === null;
    mapSection.append(coordinates, status);
    panel.append(mapSection);

    const editor = element("section", "studio-spatial-polygon-editor");
    editor.append(element("h3", "studio-section-title",
      `禁飞区高度与时间${this.tool === "nofly" ? ` · ${this.vertices.length} 个顶点` : ""}`));
    const fields = element("div", "studio-field-grid");
    fields.append(
      textField("手工区域名称", this.noFlyName, name => { this.noFlyName = name; }),
      numericField("下限 / m", this.floorM, value => { this.floorM = value!; }, { min: 0 }),
      numericField("上限 / m", this.ceilingM, value => { this.ceilingM = value!; }, { min: 0 }),
      numericField("开始 / s", this.startsAtS, value => { this.startsAtS = value!; }, { min: 0 }),
      numericField("结束 / s（空为持续）", this.endsAtS, value => { this.endsAtS = value; }, { min: 0, nullable: true }),
    );
    editor.append(fields);
    if (this.tool === "nofly") {
      editor.append(button("完成多边形", () => this.finishPolygon()));
      editor.append(button("撤销末点", () => {
        this.vertices.pop();
        this.error = null;
        this.render();
      }));
      editor.append(button("取消绘制", () => {
        this.vertices = [];
        this.tool = null;
        this.error = null;
        this.render();
      }));
    }
    panel.append(editor);

    const importSection = element("section", "studio-spatial-import");
    importSection.append(element("h3", "studio-section-title", "导入禁飞区 GeoJSON"));
    importSection.append(element("p", "studio-help",
      "仅读取你选择的文件或填写的 URL；WGS84 坐标转为当前选区的米制坐标。"
      + "高度和时间使用上方设置，超出选区或与起降设施冲突会被拒绝。外部范围不代表监管审批。"));
    const file = element("input", "studio-input");
    file.type = "file";
    file.accept = ".geojson,.json,application/geo+json,application/json";
    file.setAttribute("aria-label", "选择禁飞区 GeoJSON 文件");
    file.addEventListener("change", async () => {
      const selected = file.files?.[0];
      if (selected === undefined) return;
      try { this.importGeoJSON(JSON.parse(await selected.text()) as unknown, selected.name, null); }
      catch (cause) {
        this.error = `GeoJSON 文件读取失败：${errorMessage(cause)}`;
        this.render();
      }
    });
    const urlRow = element("div", "studio-spatial-url");
    const urlInput = element("input", "studio-input");
    urlInput.type = "url";
    urlInput.placeholder = "https://example.org/airspace.geojson";
    urlInput.setAttribute("aria-label", "禁飞区 GeoJSON URL");
    urlRow.append(urlInput, button("从 URL 导入", async () => {
      try {
        const url = new URL(urlInput.value.trim());
        if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error("仅支持 HTTP 或 HTTPS URL");
        const response = await fetch(url.href);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        this.importGeoJSON(await response.json() as unknown, url.href, url.href);
      } catch (cause) {
        this.error = `GeoJSON URL 导入失败：${errorMessage(cause)}`;
        this.render();
      }
    }));
    importSection.append(file, urlRow);
    panel.append(importSection);

    panel.append(this.facilityList(), this.zoneList());
    this.root.replaceChildren(panel);
  }
}

const panels = new WeakMap<HTMLElement, SelectedSpatialPanel>();

/** Render the authoring spatial panel for one verified selected city.
 * Re-invoking with the accepted scenario re-renders in place and keeps the
 * active tool and selection across parent rerenders. */
export function renderSelectedSpatialPanel(
  root: HTMLElement,
  scenario: CitySelectedScenario,
  onChange: SelectedSpatialChangeHandler,
  data: SelectedSpatialMapData,
  onFocus?: (facilityId: string) => boolean,
): void {
  let panel = panels.get(root);
  if (panel === undefined) {
    panel = new SelectedSpatialPanel(root, scenario, onChange, data, onFocus);
    panels.set(root, panel);
  }
  panel.update(scenario, onChange, data, onFocus);
}
