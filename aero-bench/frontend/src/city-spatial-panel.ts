import type { CityAirspace, CityFacility, CityWorkspaceConfig } from "./city-workspace-config";
import { FACILITY_MIN_DIMENSIONS } from "./city-facility-models";
import {
  evaluateSpatialRoadClearance,
  type SpatialRoadClearance,
} from "./city-spatial-road-clearance";
import {
  importAirspaceGeoJSON, localToWgs84, validateAirspacePolygon,
  validateFacilityPlacement,
  type PlacementBox, type PointXZ, type RoadPolygon,
} from "./city-workspace-geometry";

export interface SpatialMapData {
  readonly origin: { readonly latitude_deg: number; readonly longitude_deg: number };
  readonly extent: { readonly minX: number; readonly maxX: number; readonly minZ: number; readonly maxZ: number };
  readonly buildings: readonly PlacementBox[];
  readonly roads: readonly RoadPolygon[];
  /** Null is an explicit unavailable state and blocks every new ground facility. */
  readonly roadClearance: SpatialRoadClearance | null;
  readonly roadClearanceError?: string;
  /** Read-only regions declared by a selected native PublicScenario. */
  readonly sourceRegions?: readonly {
    readonly id: string;
    readonly kind: "geofence" | "no_fly" | "communications_shadow";
    readonly polygon: readonly PointXZ[];
  }[];
  readonly osmLandingSites?: readonly { readonly id: string; readonly name: string; readonly position: PointXZ }[];
}

type Tool = CityFacility["kind"] | "airspace" | null;
const SVG_NS = "http://www.w3.org/2000/svg";
const toolNames: Record<CityFacility["kind"], string> = {
  vertiport: "起降点", hub: "物流中转站", charger: "充电站",
};
const defaults: Record<CityFacility["kind"], Pick<CityFacility, "widthM" | "depthM" | "heightM" | "capacity" | "chargingPowerW">> = {
  vertiport: { widthM: 14, depthM: 10, heightM: 4.5, capacity: 1, chargingPowerW: 0 },
  hub: { widthM: 18, depthM: 14, heightM: 5.2, capacity: 10, chargingPowerW: 0 },
  charger: { widthM: 10, depthM: 8, heightM: 3.2, capacity: 1, chargingPowerW: 5000 },
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const result = document.createElement(tag);
  if (className !== undefined) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function svgElement<K extends keyof SVGElementTagNameMap>(tag: K, attributes: Record<string, string | number>): SVGElementTagNameMap[K] {
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

function numericField(label: string, value: number | null, action: (value: number | null) => void,
                      options: { step?: string; min?: number; nullable?: boolean } = {}): HTMLLabelElement {
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

function pathOf(points: readonly PointXZ[]): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x},${point.z}`).join(" ") + " Z";
}

function pointLabel(point: PointXZ, origin: SpatialMapData["origin"]): string {
  const position = localToWgs84(point, origin);
  const latitude = `${Math.abs(position.latitude_deg).toFixed(7)}° ${position.latitude_deg >= 0 ? "N" : "S"}`;
  const longitude = `${Math.abs(position.longitude_deg).toFixed(7)}° ${position.longitude_deg >= 0 ? "E" : "W"}`;
  return `X ${point.x.toFixed(2)} m · Z ${point.z.toFixed(2)} m · ${latitude}, ${longitude}`;
}

class SpatialPanel {
  private config: CityWorkspaceConfig;
  private onChange: (next: CityWorkspaceConfig) => void;
  private data: SpatialMapData;
  private tool: Tool = null;
  private zoom = 1;
  private center: PointXZ;
  private cursor: PointXZ;
  private vertices: PointXZ[] = [];
  private selectedFacilityId: string | null = null;
  private error: string | null = null;
  private ghost: CityFacility | null = null;
  private airspaceName = "手工禁飞区";
  private floorM = 0;
  private ceilingM = 120;
  private startsAtS = 0;
  private endsAtS: number | null = null;

  constructor(private readonly root: HTMLElement, config: CityWorkspaceConfig,
              onChange: (next: CityWorkspaceConfig) => void, data: SpatialMapData) {
    this.config = config;
    this.onChange = onChange;
    this.data = data;
    this.center = { x: (data.extent.minX + data.extent.maxX) / 2, z: (data.extent.minZ + data.extent.maxZ) / 2 };
    this.cursor = this.center;
  }

  update(config: CityWorkspaceConfig, onChange: (next: CityWorkspaceConfig) => void, data: SpatialMapData): void {
    const old = this.data;
    const changedScene = old.origin.latitude_deg !== data.origin.latitude_deg
      || old.origin.longitude_deg !== data.origin.longitude_deg
      || old.extent.minX !== data.extent.minX || old.extent.maxX !== data.extent.maxX
      || old.extent.minZ !== data.extent.minZ || old.extent.maxZ !== data.extent.maxZ;
    this.config = config;
    this.onChange = onChange;
    this.data = data;
    if (changedScene) {
      this.center = { x: (data.extent.minX + data.extent.maxX) / 2, z: (data.extent.minZ + data.extent.maxZ) / 2 };
      this.cursor = this.center;
      this.zoom = 1;
      this.vertices = [];
      this.selectedFacilityId = null;
      this.error = null;
      this.ghost = null;
    }
    this.render();
  }

  private commit(next: CityWorkspaceConfig): void {
    this.config = next;
    this.error = null;
    this.ghost = null;
    this.onChange(next);
    this.render();
  }

  private fail(message: string, ghost: CityFacility | null = null): void {
    this.error = message;
    this.ghost = ghost;
    this.render();
  }

  private nextId(prefix: string, current: readonly { id: string }[]): string {
    const known = new Set(current.map(item => item.id));
    let counter = 1;
    while (known.has(`${prefix}-${counter}`)) counter++;
    return `${prefix}-${counter}`;
  }

  private placeAt(point: PointXZ): void {
    this.cursor = point;
    if (this.tool === "airspace") {
      this.vertices.push(point);
      this.error = null;
      this.render();
      return;
    }
    if (this.tool === null) { this.render(); return; }
    const kind = this.tool;
    const facility: CityFacility = {
      id: this.nextId("facility", this.config.facilities),
      name: `${toolNames[kind]} ${this.config.facilities.filter(item => item.kind === kind).length + 1}`,
      kind, position: point, rotationDeg: 0, ...defaults[kind],
    };
    this.saveFacility(facility);
  }

  private saveFacility(facility: CityFacility): void {
    if (!facility.name.trim() || !Number.isSafeInteger(facility.capacity) || facility.capacity < 1
        || !Number.isFinite(facility.chargingPowerW) || facility.chargingPowerW < 0) {
      this.fail("设施需有名称、正整数容量和非负充电功率", facility);
      return;
    }
    const minimum = FACILITY_MIN_DIMENSIONS[facility.kind];
    if (facility.widthM < minimum.widthM || facility.depthM < minimum.depthM
        || facility.heightM < minimum.heightM) {
      this.fail(`${toolNames[facility.kind]}最小可容纳尺寸：宽 ${minimum.widthM} m、深 ${minimum.depthM} m、高 ${minimum.heightM} m；`
        + `当前为 ${facility.widthM} × ${facility.depthM} × ${facility.heightM} m`, facility);
      return;
    }
    const { minX, maxX, minZ, maxZ } = this.data.extent;
    if (facility.position.x < minX || facility.position.x > maxX
        || facility.position.z < minZ || facility.position.z > maxZ) {
      this.fail("设施坐标超出当前城市地图范围", facility);
      return;
    }
    const remaining = this.config.facilities.filter(item => item.id !== facility.id);
    const issues = validateFacilityPlacement(facility, this.data.buildings, [], remaining, this.config.airspace);
    const roadIssues = evaluateSpatialRoadClearance(facility, this.data.roadClearance)
      .filter(issue => issue.severity === "block");
    if (issues.length || roadIssues.length) {
      this.selectedFacilityId = facility.id;
      this.fail([...issues.map(issue => issue.message), ...roadIssues.map(issue => issue.message)].join("；"), facility);
      return;
    }
    const exists = this.config.facilities.some(item => item.id === facility.id);
    this.selectedFacilityId = facility.id;
    this.commit({ ...this.config, facilities: exists
      ? this.config.facilities.map(item => item.id === facility.id ? facility : item)
      : [...this.config.facilities, facility] });
  }

  private patchFacility(facility: CityFacility, patch: Partial<CityFacility>): void {
    const next = { ...facility, ...patch };
    if (!next.name.trim()) { this.fail("设施名称不能为空"); return; }
    this.saveFacility(next);
  }

  private finishPolygon(): void {
    const issue = validateAirspacePolygon({ polygon: this.vertices, floorM: this.floorM, ceilingM: this.ceilingM });
    if (issue.length) { this.fail(issue.map(item => item.message).join("；")); return; }
    if (!Number.isFinite(this.startsAtS) || this.startsAtS < 0 ||
        (this.endsAtS !== null && (!Number.isFinite(this.endsAtS) || this.endsAtS < this.startsAtS))) {
      this.fail("生效时间必须为非负秒，结束时间不得早于开始时间");
      return;
    }
    if (!this.airspaceName.trim()) { this.fail("禁飞区名称不能为空"); return; }
    const airspace: CityAirspace = {
      id: this.nextId("airspace", this.config.airspace), name: this.airspaceName.trim(),
      polygon: [...this.vertices], floorM: this.floorM, ceilingM: this.ceilingM,
      startsAtS: this.startsAtS, endsAtS: this.endsAtS,
      source: { kind: "manual", label: "用户手工标绘" },
    };
    const conflict = this.config.facilities.flatMap(facility =>
      validateFacilityPlacement(facility, this.data.buildings, this.data.roads,
        this.config.facilities.filter(item => item.id !== facility.id), [...this.config.airspace, airspace])
        .filter(item => item.code === "airspace_overlap"));
    if (conflict.length) { this.fail(conflict.map(item => item.message).join("；")); return; }
    this.vertices = [];
    this.commit({ ...this.config, airspace: [...this.config.airspace, airspace] });
  }

  private patchAirspace(current: CityAirspace, patch: Partial<CityAirspace>): void {
    const next = { ...current, ...patch };
    const issues = validateAirspacePolygon(next);
    if (issues.length) { this.fail(issues.map(item => item.message).join("；")); return; }
    if (!next.name.trim()) { this.fail("禁飞区名称不能为空"); return; }
    if (!Number.isFinite(next.startsAtS) || next.startsAtS < 0 ||
        (next.endsAtS !== null && (!Number.isFinite(next.endsAtS) || next.endsAtS < next.startsAtS))) {
      this.fail("生效时间必须为非负秒，结束时间不得早于开始时间"); return;
    }
    const conflict = this.config.facilities.flatMap(facility =>
      validateFacilityPlacement(facility, this.data.buildings, this.data.roads,
        this.config.facilities.filter(item => item.id !== facility.id),
        this.config.airspace.map(item => item.id === current.id ? next : item))
        .filter(item => item.code === "airspace_overlap"));
    if (conflict.length) { this.fail(conflict.map(item => item.message).join("；")); return; }
    this.commit({ ...this.config, airspace: this.config.airspace.map(item => item.id === current.id ? next : item) });
  }

  private importGeoJSON(value: unknown, label: string, uri?: string): void {
    try {
      const regions = importAirspaceGeoJSON(value, this.data.origin, label, {
        floorM: this.floorM, ceilingM: this.ceilingM, startsAtS: this.startsAtS, endsAtS: this.endsAtS,
      });
      if (!regions.length) throw new Error("GeoJSON 中没有可导入的 Polygon 或 MultiPolygon");
      const known = new Set(this.config.airspace.map(item => item.id));
      const fresh: CityAirspace[] = regions.filter(region => !known.has(region.id)).map(region => ({
        ...region, polygon: [...region.polygon], source: uri === undefined ? { ...region.source } : { ...region.source, uri },
      }));
      if (!fresh.length) throw new Error("这些 GeoJSON 禁飞区已导入");
      const conflict = this.config.facilities.flatMap(facility =>
        validateFacilityPlacement(facility, this.data.buildings, this.data.roads,
          this.config.facilities.filter(item => item.id !== facility.id), [...this.config.airspace, ...fresh])
          .filter(item => item.code === "airspace_overlap"));
      if (conflict.length) throw new Error(conflict.map(item => item.message).join("；"));
      this.commit({ ...this.config, airspace: [...this.config.airspace, ...fresh] });
    } catch (error) {
      this.fail(`GeoJSON 导入失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  private view(): { minX: number; minZ: number; width: number; height: number } {
    const { minX, maxX, minZ, maxZ } = this.data.extent;
    if (![minX, maxX, minZ, maxZ].every(Number.isFinite) || maxX <= minX || maxZ <= minZ) {
      throw new Error("城市地图范围无效");
    }
    const width = (maxX - minX) / this.zoom;
    const height = (maxZ - minZ) / this.zoom;
    const x = Math.max(minX + width / 2, Math.min(maxX - width / 2, this.center.x));
    const z = Math.max(minZ + height / 2, Math.min(maxZ - height / 2, this.center.z));
    return { minX: x - width / 2, minZ: z - height / 2, width, height };
  }

  private clickPoint(event: MouseEvent, svg: SVGSVGElement): PointXZ | null {
    const box = svg.getBoundingClientRect();
    const view = this.view();
    const scale = Math.min(box.width / view.width, box.height / view.height);
    if (!Number.isFinite(scale) || scale <= 0) return null;
    const left = box.left + (box.width - view.width * scale) / 2;
    const top = box.top + (box.height - view.height * scale) / 2;
    const x = (event.clientX - left) / scale;
    const z = (event.clientY - top) / scale;
    if (x < 0 || x > view.width || z < 0 || z > view.height) return null;
    return { x: view.minX + x, z: view.minZ + z };
  }

  private map(): SVGSVGElement {
    const view = this.view();
    const map = svgElement("svg", {
      viewBox: `${view.minX} ${view.minZ} ${view.width} ${view.height}`,
      role: "group", "aria-label": "当前城市道路、建筑、设施与禁飞区地图",
      preserveAspectRatio: "xMidYMid meet",
    });
    map.classList.add("studio-spatial-map");
    map.style.width = "100%";
    map.style.height = "min(56vh, 680px)";
    map.style.display = "block";
    map.style.background = "#12202b";
    map.style.cursor = "crosshair";
    const background = svgElement("rect", { x: view.minX, y: view.minZ, width: view.width, height: view.height, fill: "#12202b" });
    map.append(background);
    for (const region of this.data.sourceRegions ?? []) {
      const color = region.kind === "communications_shadow" ? "#a78bfa" : "#fb7185";
      const shape = svgElement("path", { d: pathOf(region.polygon), fill: `${color}33`, stroke: color,
        "stroke-width": 1.2, "stroke-dasharray": "3 2", "data-source-region-id": region.id });
      shape.append(svgElement("title", {}));
      shape.lastElementChild!.textContent = `原生场景区域：${region.id} · ${region.kind}`;
      map.append(shape);
    }
    const roadbed = this.data.roadClearance?.roadbed ?? this.data.roads;
    for (const road of roadbed) {
      const outline = pathOf(road.outline);
      const holes = road.holes.map(pathOf).join(" ");
      map.append(svgElement("path", { d: `${outline} ${holes}`, fill: "#53616b", "fill-rule": "evenodd", stroke: "#71808a", "stroke-width": 0.35 }));
    }
    for (const walkbed of this.data.roadClearance?.walkbed ?? []) {
      const outline = pathOf(walkbed.outline);
      const holes = walkbed.holes.map(pathOf).join(" ");
      map.append(svgElement("path", { d: `${outline} ${holes}`, fill: "#817b6d", "fill-rule": "evenodd",
        stroke: "#a79f8d", "stroke-width": 0.3 }));
    }
    for (const crossing of this.data.roadClearance?.crossings ?? []) {
      map.append(svgElement("path", { d: pathOf(crossing.outline), fill: "#d9d7ceaa",
        stroke: "#f3f2ed", "stroke-width": 0.25 }));
    }
    for (const fixture of this.data.roadClearance?.fixtures ?? []) {
      map.append(svgElement("rect", {
        x: fixture.x - fixture.widthM / 2, y: fixture.z - fixture.depthM / 2,
        width: fixture.widthM, height: fixture.depthM,
        transform: `rotate(${-fixture.rotationDeg} ${fixture.x} ${fixture.z})`,
        fill: "#f59e0b33", stroke: "#f59e0b", "stroke-width": 0.25,
      }));
    }
    for (const building of this.data.buildings) {
      map.append(svgElement("rect", {
        x: building.x - building.widthM / 2, y: building.z - building.depthM / 2,
        width: building.widthM, height: building.depthM,
        transform: `rotate(${-building.rotationDeg} ${building.x} ${building.z})`,
        fill: "#334756", stroke: "#70899a", "stroke-width": 0.5,
      }));
    }
    for (const region of this.config.airspace) {
      const shape = svgElement("path", { d: pathOf(region.polygon), fill: "#f0525255", stroke: "#fb7185", "stroke-width": 1.8,
        "stroke-dasharray": "5 3" });
      shape.append(svgElement("title", {}));
      shape.lastElementChild!.textContent = `${region.name} · ${region.floorM}–${region.ceilingM} m`;
      map.append(shape);
    }
    for (const site of this.data.osmLandingSites ?? []) {
      const marker = svgElement("circle", { cx: site.position.x, cy: site.position.z, r: 4, fill: "#22d3ee", stroke: "#082f49", "stroke-width": 1 });
      marker.append(svgElement("title", {}));
      marker.lastElementChild!.textContent = `OSM 起降点：${site.name}`;
      map.append(marker);
    }
    for (const facility of this.config.facilities) {
      const color = facility.kind === "vertiport" ? "#38bdf8" : facility.kind === "hub" ? "#fbbf24" : "#4ade80";
      const rect = svgElement("rect", {
        x: facility.position.x - facility.widthM / 2, y: facility.position.z - facility.depthM / 2,
        width: facility.widthM, height: facility.depthM,
        transform: `rotate(${-facility.rotationDeg} ${facility.position.x} ${facility.position.z})`,
        fill: `${color}77`, stroke: color, "stroke-width": this.selectedFacilityId === facility.id ? 3 : 1.5,
        tabindex: 0, role: "button", "aria-label": `选中${facility.name}`,
      });
      rect.addEventListener("click", event => {
        if (this.tool !== null) return;
        event.stopPropagation();
        this.selectedFacilityId = facility.id;
        this.cursor = facility.position;
        this.render();
      });
      rect.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.selectedFacilityId = facility.id; this.cursor = facility.position; this.render(); } });
      rect.append(svgElement("title", {}));
      rect.lastElementChild!.textContent = `${facility.name} · ${pointLabel(facility.position, this.data.origin)}`;
      map.append(rect);
    }
    if (this.ghost !== null) {
      const facility = this.ghost;
      map.append(svgElement("rect", {
        x: facility.position.x - facility.widthM / 2, y: facility.position.z - facility.depthM / 2,
        width: facility.widthM, height: facility.depthM,
        transform: `rotate(${-facility.rotationDeg} ${facility.position.x} ${facility.position.z})`,
        fill: "#ef444466", stroke: "#ef4444", "stroke-width": 3,
      }));
    }
    if (this.vertices.length) {
      const coordinates = this.vertices.map(point => `${point.x},${point.z}`).join(" ");
      map.append(svgElement("polyline", { points: coordinates, fill: "none", stroke: "#fb7185", "stroke-width": 2.5 }));
      for (const vertex of this.vertices) map.append(svgElement("circle", { cx: vertex.x, cy: vertex.z, r: 2.5, fill: "#fb7185" }));
    }
    map.append(svgElement("circle", { cx: this.cursor.x, cy: this.cursor.z, r: 2, fill: "#fff", stroke: "#0f172a", "stroke-width": 0.8,
      "pointer-events": "none" }));
    map.addEventListener("click", event => {
      const point = this.clickPoint(event, map);
      if (point !== null) this.placeAt(point);
    });
    return map;
  }

  private facilityList(): HTMLElement {
    const section = element("section", "studio-spatial-facilities");
    section.append(element("h3", "studio-section-title", `设施 (${this.config.facilities.length})`));
    if (!this.config.facilities.length) section.append(element("p", "studio-empty", "地图上选择工具后单击放置设施。"));
    for (const facility of this.config.facilities) {
      const card = element("article", "studio-spatial-card");
      card.dataset.facilityId = facility.id;
      card.append(element("h4", "studio-card-title", `${toolNames[facility.kind]} · ${facility.name}`));
      if (this.selectedFacilityId === facility.id && this.error !== null) {
        card.append(element("p", "studio-spatial-error", this.error));
      }
      card.append(element("p", "studio-coordinate", pointLabel(facility.position, this.data.origin)));
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
        numericField("容量", facility.capacity, capacity => this.patchFacility(facility, { capacity: capacity! }), { min: 1, step: "1" }),
        numericField("充电功率 / W", facility.chargingPowerW, chargingPowerW => this.patchFacility(facility, { chargingPowerW: chargingPowerW! }), { min: 0 }),
      );
      card.append(fields);
      card.append(button("删除设施", () => this.commit({ ...this.config,
        facilities: this.config.facilities.filter(item => item.id !== facility.id) }), "studio-button studio-danger"));
      section.append(card);
    }
    return section;
  }

  private airspaceList(): HTMLElement {
    const section = element("section", "studio-spatial-airspace");
    section.append(element("h3", "studio-section-title", `禁飞区 (${this.config.airspace.length})`));
    if (!this.config.airspace.length) section.append(element("p", "studio-empty", "手工标绘，或从用户提供的 GeoJSON 导入。"));
    for (const region of this.config.airspace) {
      const card = element("article", "studio-spatial-card");
      card.dataset.airspaceId = region.id;
      card.append(element("h4", "studio-card-title", region.name));
      card.append(element("p", "studio-source", `来源：${region.source.label} (${region.source.kind === "geojson" ? "用户/外部 GeoJSON" : "手工标绘"})`));
      const fields = element("div", "studio-field-grid");
      fields.append(
        textField("名称", region.name, name => this.patchAirspace(region, { name })),
        numericField("下限 / m", region.floorM, floorM => this.patchAirspace(region, { floorM: floorM! }), { min: 0 }),
        numericField("上限 / m", region.ceilingM, ceilingM => this.patchAirspace(region, { ceilingM: ceilingM! }), { min: 0 }),
        numericField("开始 / s", region.startsAtS, startsAtS => this.patchAirspace(region, { startsAtS: startsAtS! }), { min: 0 }),
        numericField("结束 / s（空为持续）", region.endsAtS, endsAtS => this.patchAirspace(region, { endsAtS }), { min: 0, nullable: true }),
      );
      card.append(fields);
      card.append(button("删除禁飞区", () => this.commit({ ...this.config,
        airspace: this.config.airspace.filter(item => item.id !== region.id) }), "studio-button studio-danger"));
      section.append(card);
    }
    return section;
  }

  render(): void {
    const panel = element("section", "studio-spatial-panel");
    panel.append(element("h2", "studio-panel-title", "设施与空域"));
    panel.append(element("p", "studio-help", "当前城市道路与建筑按米显示。建筑区域表示保守碰撞范围，可能包含内院。X 向东，Z 向南；地图选点会同步到主三维预览。"));
    const clearance = this.data.roadClearance;
    const clearanceStatus = element("p", clearance === null ? "studio-spatial-error" : "studio-note",
      clearance === null
        ? (this.data.roadClearanceError ?? "道路、人行铺装、人行横道与有效街道设施净空资料不可用；地面设施编辑已阻止。")
        : `编辑净空来源：${clearance.provenance.source === "canonical-road-v3" ? "摘要绑定的 road v3 与有效设施" : "选区静态道路"}`
          + ` · 机动车道 ${clearance.roadbed.length} · 人行铺装 ${clearance.walkbed.length}`
          + ` · 人行横道片段 ${clearance.crossings.length} · 有效设施 ${clearance.fixtures.length}`
          + ` · 表面摘要 ${clearance.provenance.displayedSurfaceSha256.slice(0, 12)}…`
          + "。这是编辑预览净空，不是正式物理验证。" );
    clearanceStatus.dataset.role = "spatial-road-clearance";
    panel.append(clearanceStatus);
    const tools = element("div", "studio-spatial-tools");
    for (const [tool, label] of [["vertiport", "放置起降点"], ["hub", "放置物流中转站"],
                                  ["charger", "放置充电站"], ["airspace", "绘制禁飞区"]] as const) {
      const control = button(label, () => { this.tool = tool; this.error = null; this.ghost = null; this.render(); });
      control.setAttribute("aria-pressed", String(this.tool === tool));
      tools.append(control);
    }
    tools.append(button("仅选点", () => { this.tool = null; this.error = null; this.ghost = null; this.render(); }));
    panel.append(tools);

    const mapSection = element("section", "studio-spatial-map-section");
    const zoom = element("div", "studio-spatial-zoom");
    zoom.append(
      button("放大", () => { this.zoom = Math.min(5, this.zoom * 1.5); this.center = this.cursor; this.render(); }),
      button("缩小", () => { this.zoom = Math.max(1, this.zoom / 1.5); this.render(); }),
      button("全图", () => { this.zoom = 1; this.center = { x: (this.data.extent.minX + this.data.extent.maxX) / 2,
        z: (this.data.extent.minZ + this.data.extent.maxZ) / 2 }; this.render(); }),
    );
    mapSection.append(zoom, this.map());
    mapSection.append(element("p", "studio-coordinate", pointLabel(this.cursor, this.data.origin)));
    const coordinates = element("div", "studio-field-grid studio-spatial-coordinates");
    coordinates.append(
      numericField("选点 X / m", this.cursor.x, x => {
        if (!Number.isFinite(x) || x! < this.data.extent.minX || x! > this.data.extent.maxX) {
          this.fail("X 坐标必须位于当前城市地图范围内"); return;
        }
        this.cursor = { ...this.cursor, x: x! }; this.render();
      }),
      numericField("选点 Z / m", this.cursor.z, z => {
        if (!Number.isFinite(z) || z! < this.data.extent.minZ || z! > this.data.extent.maxZ) {
          this.fail("Z 坐标必须位于当前城市地图范围内"); return;
        }
        this.cursor = { ...this.cursor, z: z! }; this.render();
      }),
      button(this.tool === "airspace" ? "添加顶点" : "在坐标处放置", () => this.placeAt(this.cursor)),
    );
    const status = element("p", "studio-spatial-error", this.error ?? "");
    status.setAttribute("role", "alert");
    status.hidden = this.error === null;
    mapSection.append(coordinates, status);
    panel.append(mapSection);

    const editor = element("section", "studio-spatial-polygon-editor");
    editor.append(element("h3", "studio-section-title", `禁飞区高度与时间${this.tool === "airspace" ? ` · ${this.vertices.length} 个顶点` : ""}`));
    const fields = element("div", "studio-field-grid");
    fields.append(
      textField("手工区域名称", this.airspaceName, name => { this.airspaceName = name; }),
      numericField("下限 / m", this.floorM, value => { this.floorM = value!; }, { min: 0 }),
      numericField("上限 / m", this.ceilingM, value => { this.ceilingM = value!; }, { min: 0 }),
      numericField("开始 / s", this.startsAtS, value => { this.startsAtS = value!; }, { min: 0 }),
      numericField("结束 / s（空为持续）", this.endsAtS, value => { this.endsAtS = value; }, { min: 0, nullable: true }),
    );
    editor.append(fields);
    if (this.tool === "airspace") {
      editor.append(button("完成多边形", () => this.finishPolygon()));
      editor.append(button("撤销顶点", () => { this.vertices.pop(); this.error = null; this.render(); }));
      editor.append(button("清空顶点", () => { this.vertices = []; this.error = null; this.render(); }));
    }
    panel.append(editor);

    const importSection = element("section", "studio-spatial-import");
    importSection.append(element("h3", "studio-section-title", "导入禁飞范围 GeoJSON"));
    importSection.append(element("p", "studio-help", "仅处理你选择的文件或输入的 URL。这些是用户/外部资料，不是监管审批依据；不会默认获取未知管制区。二维坐标使用上面的高度和生效时间。"));
    const file = element("input", "studio-input");
    file.type = "file";
    file.accept = ".geojson,.json,application/geo+json,application/json";
    file.setAttribute("aria-label", "选择 GeoJSON 文件");
    file.addEventListener("change", async () => {
      const selected = file.files?.[0];
      if (selected === undefined) return;
      try { this.importGeoJSON(JSON.parse(await selected.text()) as unknown, selected.name); }
      catch (error) { this.fail(`GeoJSON 文件读取失败：${error instanceof Error ? error.message : String(error)}`); }
    });
    importSection.append(file);
    const urlRow = element("div", "studio-spatial-url");
    const urlInput = element("input", "studio-input");
    urlInput.type = "url";
    urlInput.placeholder = "https://example.org/airspace.geojson";
    urlInput.setAttribute("aria-label", "GeoJSON URL");
    urlRow.append(urlInput, button("从 URL 导入", async () => {
      try {
        const url = new URL(urlInput.value.trim(), window.location.href);
        if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error("仅支持 HTTP 或 HTTPS URL");
        const response = await fetch(url.href);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        this.importGeoJSON(await response.json() as unknown, url.href, url.href);
      } catch (error) { this.fail(`GeoJSON URL 导入失败：${error instanceof Error ? error.message : String(error)}`); }
    }));
    importSection.append(urlRow);
    panel.append(importSection);
    panel.append(this.facilityList(), this.airspaceList());
    this.root.replaceChildren(panel);
  }
}

const panels = new WeakMap<HTMLElement, SpatialPanel>();

export function renderCitySpatialPanel(root: HTMLElement, config: CityWorkspaceConfig,
                                       onChange: (next: CityWorkspaceConfig) => void, data: SpatialMapData): void {
  let panel = panels.get(root);
  if (panel === undefined) {
    panel = new SpatialPanel(root, config, onChange, data);
    panels.set(root, panel);
  }
  panel.update(config, onChange, data);
}
