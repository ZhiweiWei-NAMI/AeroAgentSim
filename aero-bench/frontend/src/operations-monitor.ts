import "./operations-monitor.css";
import { subscribeLanguage, t, tf } from "./i18n";
import type { SelectedFrameBusinessView } from "./p02-entity-overlays";
import { sameTarget, type TraceTarget } from "./state/target";

export type OperationsClockState = "playing" | "paused" | "stepping" | "seeking" | "stopped" | "notrun";
export type OperationsCameraMode = "free" | "chase" | "cockpit";
export type OperationsObjectKind = "uav" | "ugv" | "pedestrian" | "facility";
export type OperationsSeverity = "critical" | "warning" | "info" | "unknown";
export type OperationsCameraSource = "simulated_rgb" | "live_video" | "recorded_video" | "unavailable";
export type OperationsCameraState = "ready" | "loading" | "stale" | "disconnected" | "unavailable";

export interface OperationsPoint2 { readonly x: number; readonly z: number }
export interface OperationsPoint3 extends OperationsPoint2 { readonly y: number }

export interface OperationsStatusValue {
  readonly label: string;
  readonly state?: "normal" | "warning" | "critical" | "unknown";
}

export interface OperationsFreshness {
  readonly state: "fresh" | "stale" | "unknown";
  readonly ageSeconds?: number;
  readonly label?: string;
}

export interface OperationsFact {
  readonly label: string;
  readonly value?: string | number;
  readonly unit?: string;
  readonly state?: "normal" | "warning" | "critical" | "unknown";
}

export interface OperationsTelemetry {
  readonly altitudeM?: number;
  readonly altitudeReference?: string;
  readonly speedMps?: number;
  readonly batteryPercent?: number;
  readonly payload?: string;
  readonly nextDestination?: string;
  readonly activity?: OperationsStatusValue;
  readonly connectivity?: OperationsStatusValue;
  readonly health?: OperationsStatusValue;
  readonly freshness?: OperationsFreshness;
  readonly observedAtSeconds?: number;
  readonly facts?: readonly OperationsFact[];
}

export interface OperationsTask {
  readonly id: string;
  readonly label?: string;
  readonly phase?: string;
  readonly orderId?: string;
  readonly missionId?: string;
  readonly facilityId?: string;
  readonly dependencies?: readonly string[];
}

export interface OperationsCamera {
  readonly source: OperationsCameraSource;
  readonly state: OperationsCameraState;
  readonly label?: string;
  readonly frameTimeSeconds?: number;
  readonly reason?: string;
}

export interface OperationsObject {
  readonly id: string;
  readonly kind: OperationsObjectKind;
  readonly label: string;
  readonly position: OperationsPoint3;
  /** Clockwise radians from local north (-z). */
  readonly headingRad?: number;
  readonly phase?: string;
  readonly telemetry?: OperationsTelemetry;
  readonly task?: OperationsTask;
  /** The currently supported rigid onboard source. Gimbal control is not inferred from this field. */
  readonly camera?: OperationsCamera;
}

export interface OperationsRoute {
  readonly id: string;
  readonly points: readonly OperationsPoint2[];
  readonly objectId?: string;
  readonly label?: string;
}

export interface OperationsPolygon {
  readonly id: string;
  readonly points: readonly OperationsPoint2[];
  readonly kind?: "restricted" | "facility" | "operating_area" | "other";
  readonly label?: string;
}

/**
 * One aggregated static-building square for the minimap background. The
 * map compiles these from the scenario's declared building anchors (grid
 * deduped); they are context only and never drive the minimap fit or any
 * business highlight.
 */
export interface OperationsBuildingSquare {
  /** Grid cell id ("cell:e:n"). */
  readonly id: string;
  readonly x: number;
  readonly z: number;
  /** Edge length in metres. */
  readonly sizeM: number;
}

export interface OperationsEvent {
  readonly id: string;
  readonly label: string;
  readonly severity: OperationsSeverity;
  readonly condition: string;
  readonly timeSeconds: number;
  readonly objectIds: readonly string[];
  readonly position?: OperationsPoint2;
  readonly locationLabel?: string;
  readonly missionId?: string;
  readonly orderId?: string;
  readonly facilityId?: string;
  readonly dependencies?: readonly string[];
  readonly evidence?: readonly string[];
  readonly acknowledged?: boolean;
  readonly resolved?: boolean;
  /** Upstream may supply a stable key for repeated reports of the same condition. */
  readonly groupKey?: string;
}

export interface OperationsObserver {
  readonly mode: OperationsCameraMode;
  readonly position?: OperationsPoint2;
  /** Clockwise radians from local north (-z). */
  readonly headingRad?: number;
  readonly footprint?: readonly OperationsPoint2[];
}

export interface OperationsSnapshot {
  readonly sourceKey: string;
  readonly sourceLabel: string;
  readonly timeSeconds: number;
  readonly clockState: OperationsClockState;
  readonly selected: TraceTarget | null;
  /** Exact current-frame association supplied by the application selector. */
  readonly selectedBusiness?: SelectedFrameBusinessView | null;
  readonly objects: readonly OperationsObject[];
  readonly routes?: readonly OperationsRoute[];
  readonly polygons?: readonly OperationsPolygon[];
  readonly events?: readonly OperationsEvent[];
  readonly observer?: OperationsObserver;
  /** Static building context squares; excluded from fit and from highlights. */
  readonly buildings?: readonly OperationsBuildingSquare[];
}

export interface OperationsMonitorCallbacks {
  readonly onSelect?: (target: TraceTarget) => void;
  readonly onNavigate?: (position: OperationsPoint2) => void;
  readonly onCameraMode?: (mode: OperationsCameraMode) => void;
  readonly onPreviewToggle?: (shown: boolean) => void;
  readonly onSwap?: () => void;
}

export interface OperationsMonitorHandle {
  readonly element: HTMLElement;
  /** Stable slot. The application may replace its placeholder with a camera canvas or video element. */
  readonly previewContainer: HTMLElement;
  update(snapshot: OperationsSnapshot): void;
  dispose(): void;
}

interface Bounds { minX: number; maxX: number; minZ: number; maxZ: number }
interface MapPoint { x: number; y: number }
interface DragState {
  pointerId: number;
  startClient: MapPoint;
  startWorld: OperationsPoint2;
  observerStart: OperationsPoint2;
  dragged: boolean;
  leftCockpit: boolean;
}
interface OperationsEventGroup {
  readonly key: string;
  readonly event: OperationsEvent;
  readonly occurrences: readonly OperationsEvent[];
}
interface FleetDisplayEntry {
  readonly object: OperationsObject;
  readonly group: "uav" | "selected" | "ugv" | "pedestrian" | "facility";
  readonly groupLabel: string;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const MAP_WIDTH = 640;
const MAP_HEIGHT = 400;
const MAP_PADDING = 32;
const DRAG_THRESHOLD_PX = 6;
const INITIAL_EVENT_GROUP_LIMIT = 12;
const EVENT_GROUP_PAGE_SIZE = 50;
const MAX_MINIMAP_EVENTS = 80;
const SEVERITY_ORDER: Readonly<Record<OperationsSeverity, number>> = {
  critical: 0, warning: 1, info: 2, unknown: 3,
};

function assertFinite(value: number, field: string): void {
  if (!Number.isFinite(value)) throw new Error(`${field} must be finite`);
}

function validatePoint(point: OperationsPoint2, field: string): void {
  assertFinite(point.x, `${field}.x`);
  assertFinite(point.z, `${field}.z`);
}

function validateSnapshot(snapshot: OperationsSnapshot): void {
  if (snapshot.sourceKey.length === 0) throw new Error("sourceKey must not be empty");
  if (snapshot.sourceLabel.length === 0) throw new Error("sourceLabel must not be empty");
  assertFinite(snapshot.timeSeconds, "timeSeconds");
  if (snapshot.timeSeconds < 0) throw new Error("timeSeconds must be non-negative");
  if (snapshot.selectedBusiness != null) {
    if (snapshot.selected?.kind !== "entity" || snapshot.selected.id !== snapshot.selectedBusiness.entityId) {
      throw new Error("selectedBusiness must match the selected entity");
    }
    if (snapshot.selectedBusiness.orders.some(order => order.tick > snapshot.selectedBusiness!.cursorTick)) {
      throw new Error("selectedBusiness must not contain future records");
    }
  }
  const objectIds = new Set<string>();
  for (const object of snapshot.objects) {
    if (object.id.length === 0 || objectIds.has(object.id)) throw new Error(`duplicate or empty object id: ${object.id}`);
    objectIds.add(object.id);
    validatePoint(object.position, `objects.${object.id}.position`);
    assertFinite(object.position.y, `objects.${object.id}.position.y`);
    if (object.headingRad !== undefined) assertFinite(object.headingRad, `objects.${object.id}.headingRad`);
  }
  const eventIds = new Set<string>();
  for (const event of snapshot.events ?? []) {
    if (event.id.length === 0 || eventIds.has(event.id)) throw new Error(`duplicate or empty event id: ${event.id}`);
    eventIds.add(event.id);
    assertFinite(event.timeSeconds, `events.${event.id}.timeSeconds`);
    if (event.position !== undefined) validatePoint(event.position, `events.${event.id}.position`);
  }
  for (const route of snapshot.routes ?? []) for (const point of route.points) validatePoint(point, `routes.${route.id}`);
  for (const polygon of snapshot.polygons ?? []) for (const point of polygon.points) validatePoint(point, `polygons.${polygon.id}`);
  if (snapshot.observer?.position !== undefined) validatePoint(snapshot.observer.position, "observer.position");
  if (snapshot.observer?.headingRad !== undefined) assertFinite(snapshot.observer.headingRad, "observer.headingRad");
  for (const point of snapshot.observer?.footprint ?? []) validatePoint(point, "observer.footprint");
}

function el<K extends keyof HTMLElementTagNameMap>(document: Document, tag: K, className?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className !== undefined) node.className = className;
  return node;
}

function svg<K extends keyof SVGElementTagNameMap>(document: Document, tag: K, className?: string): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG_NS, tag);
  if (className !== undefined) node.setAttribute("class", className);
  return node;
}

function targetForObject(object: OperationsObject): TraceTarget {
  return { kind: "entity", id: object.id };
}

function objectForTarget(snapshot: OperationsSnapshot, target: TraceTarget | null): OperationsObject | undefined {
  if (target?.kind !== "entity") return undefined;
  return snapshot.objects.find(object => object.id === target.id);
}

function eventForTarget(snapshot: OperationsSnapshot, target: TraceTarget | null): OperationsEvent | undefined {
  return target?.kind === "event" ? snapshot.events?.find(event => event.id === target.id) : undefined;
}

function formatClock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function clockLabel(state: OperationsClockState): string {
  return ({ playing: t("monitor.clockPlaying"), paused: t("monitor.clockPaused"), stepping: t("monitor.clockStepping"), seeking: t("monitor.clockSeeking"), stopped: t("monitor.clockStopped"), notrun: t("monitor.clockNotRun") } as const)[state];
}

function objectKindLabel(kind: OperationsObjectKind): string {
  return ({ uav: t("monitor.uav"), ugv: t("monitor.ugv"), pedestrian: t("monitor.pedestrian"), facility: t("monitor.facility") } as const)[kind];
}

function sourceLabel(source: OperationsCameraSource): string {
  return ({
    simulated_rgb: t("monitor.simulatedRgb"), live_video: t("monitor.liveVideo"), recorded_video: t("monitor.recordedVideo"), unavailable: t("monitor.noVideo"),
  } as const)[source];
}

function cameraStateLabel(state: OperationsCameraState): string {
  return ({ ready: t("monitor.ready"), loading: t("monitor.loading"), stale: t("monitor.staleImage"), disconnected: t("monitor.disconnected"), unavailable: t("monitor.unavailable") } as const)[state];
}

function valueText(value: string | number | undefined, unit = ""): string {
  if (value === undefined || value === "" || value === "未知") return t("p02.unknown");
  return `${typeof value === "number" ? Number(value.toFixed(2)) : value}${unit}`;
}

function statusClass(state: string | undefined): string {
  return state === "normal" || state === "fresh" || state === "ready" ? "is-normal"
    : state === "warning" || state === "stale" || state === "loading" ? "is-warning"
      : state === "critical" || state === "disconnected" ? "is-critical" : "is-unknown";
}

/** Test/authority readback: the spatial fit computed for a snapshot. */
export function boundsFor(snapshot: OperationsSnapshot, focusSelection = false): Bounds {
  const points: OperationsPoint2[] = [];
  const selected = focusSelection ? objectForTarget(snapshot, snapshot.selected) : undefined;
  if (selected !== undefined) {
    points.push(selected.position);
    for (const route of snapshot.routes ?? []) if (route.objectId === selected.id) points.push(...route.points);
  } else {
    points.push(...snapshot.objects.map(object => object.position));
    for (const route of snapshot.routes ?? []) points.push(...route.points);
    for (const polygon of snapshot.polygons ?? []) points.push(...polygon.points);
    for (const event of snapshot.events ?? []) if (event.position !== undefined) points.push(event.position);
    if (snapshot.observer?.position !== undefined) points.push(snapshot.observer.position);
    points.push(...(snapshot.observer?.footprint ?? []));
  }
  // Building squares are background context; they never stretch the fit.
  if (points.length === 0 && (snapshot.buildings?.length ?? 0) > 0) {
    points.push(...(snapshot.buildings ?? []).map(square => ({ x: square.x, z: square.z })));
  }
  if (points.length === 0) return { minX: -50, maxX: 50, minZ: -50, maxZ: 50 };
  const xs = points.map(point => point.x), zs = points.map(point => point.z);
  let minX = Math.min(...xs), maxX = Math.max(...xs), minZ = Math.min(...zs), maxZ = Math.max(...zs);
  const width = Math.max(maxX - minX, selected === undefined ? 20 : 140), height = Math.max(maxZ - minZ, selected === undefined ? 20 : 140);
  const padding = Math.max(width, height) * 0.08;
  const xMiddle = (minX + maxX) / 2, zMiddle = (minZ + maxZ) / 2;
  minX = xMiddle - width / 2 - padding; maxX = xMiddle + width / 2 + padding;
  minZ = zMiddle - height / 2 - padding; maxZ = zMiddle + height / 2 + padding;
  const contentAspect = (maxX - minX) / (maxZ - minZ), mapAspect = (MAP_WIDTH - MAP_PADDING * 2) / (MAP_HEIGHT - MAP_PADDING * 2);
  if (contentAspect > mapAspect) {
    const needed = (maxX - minX) / mapAspect, add = (needed - (maxZ - minZ)) / 2;
    minZ -= add; maxZ += add;
  } else {
    const needed = (maxZ - minZ) * mapAspect, add = (needed - (maxX - minX)) / 2;
    minX -= add; maxX += add;
  }
  return { minX, maxX, minZ, maxZ };
}

function hasSceneSpatialData(snapshot: OperationsSnapshot): boolean {
  return snapshot.objects.length > 0
    || (snapshot.routes ?? []).some(route => route.points.length > 0)
    || (snapshot.polygons ?? []).some(polygon => polygon.points.length > 0)
    || (snapshot.events ?? []).some(event => event.position !== undefined);
}

function mapPoint(point: OperationsPoint2, bounds: Bounds): MapPoint {
  return {
    x: MAP_PADDING + (point.x - bounds.minX) / (bounds.maxX - bounds.minX) * (MAP_WIDTH - MAP_PADDING * 2),
    // Renderer +z is south, so local north (-z) stays at the top of the minimap.
    y: MAP_PADDING + (point.z - bounds.minZ) / (bounds.maxZ - bounds.minZ) * (MAP_HEIGHT - MAP_PADDING * 2),
  };
}

function worldPoint(clientX: number, clientY: number, box: DOMRect, bounds: Bounds): OperationsPoint2 {
  const px = Math.max(MAP_PADDING, Math.min(MAP_WIDTH - MAP_PADDING, (clientX - box.left) / Math.max(box.width, 1) * MAP_WIDTH));
  const py = Math.max(MAP_PADDING, Math.min(MAP_HEIGHT - MAP_PADDING, (clientY - box.top) / Math.max(box.height, 1) * MAP_HEIGHT));
  return {
    x: bounds.minX + (px - MAP_PADDING) / (MAP_WIDTH - MAP_PADDING * 2) * (bounds.maxX - bounds.minX),
    z: bounds.minZ + (py - MAP_PADDING) / (MAP_HEIGHT - MAP_PADDING * 2) * (bounds.maxZ - bounds.minZ),
  };
}

function pathData(points: readonly OperationsPoint2[], bounds: Bounds, close: boolean): string {
  const mapped = points.map(point => mapPoint(point, bounds));
  return mapped.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ")
    + (close && mapped.length > 0 ? " Z" : "");
}

function niceScale(metresAcross: number): number {
  const desired = metresAcross / 4;
  const power = 10 ** Math.floor(Math.log10(Math.max(desired, 1e-9)));
  return [1, 2, 5, 10].map(value => value * power).find(value => value >= desired) ?? 10 * power;
}

function setText(node: Element, text: string): void {
  if (node.textContent !== text) node.textContent = text;
}

function reconcileKeyed<T>(
  parent: HTMLElement | SVGElement,
  values: readonly T[],
  key: (value: T) => string,
  create: (value: T) => Element,
  update: (node: Element, value: T, index: number) => void,
): void {
  const existing = new Map(Array.from(parent.children).map(node => [node.getAttribute("data-key") ?? "", node]));
  const ordered: Element[] = [];
  values.forEach((value, index) => {
    const itemKey = key(value);
    const node = existing.get(itemKey) ?? create(value);
    node.setAttribute("data-key", itemKey);
    update(node, value, index);
    existing.delete(itemKey);
    ordered.push(node);
  });
  for (const node of existing.values()) node.remove();
  for (const node of ordered) if (node !== parent.children.item(ordered.indexOf(node))) parent.append(node);
}

/**
 * Mounts a DOM-only operational monitor. It never sends vehicle, mission, or gimbal commands.
 * Its callbacks select trace targets or move the observation camera.
 */
export function mountOperationsMonitor(
  host: HTMLElement, callbacks: OperationsMonitorCallbacks = {},
): OperationsMonitorHandle {
  const document = host.ownerDocument;
  const root = el(document, "section", "operations-monitor");
  root.setAttribute("aria-label", t("monitor.root"));

  const header = el(document, "header", "operations-monitor-header");
  const source = el(document, "span", "operations-monitor-source");
  const clock = el(document, "time", "operations-monitor-clock");
  const clockState = el(document, "span", "operations-monitor-clock-state");
  header.append(source, clock, clockState);

  const body = el(document, "div", "operations-monitor-body");
  const sidebar = el(document, "aside", "operations-monitor-sidebar");
  const fleetDisclosure = el(document, "details", "operations-monitor-disclosure");
  fleetDisclosure.open = true;
  const fleetSummary = el(document, "summary");
  fleetSummary.textContent = t("monitor.fleet");
  const fleetList = el(document, "div", "operations-monitor-fleet");
  fleetList.setAttribute("role", "list");
  fleetDisclosure.append(fleetSummary, fleetList);

  const detail = el(document, "section", "operations-monitor-detail");
  detail.setAttribute("aria-label", t("monitor.detail"));
  const detailTitle = el(document, "h3");
  const detailKind = el(document, "p", "operations-monitor-detail-kind");
  const business = el(document, "section", "operations-monitor-business");
  const physicalTitle = el(document, "h4", "operations-monitor-physical-title");
  const statusGrid = el(document, "div", "operations-monitor-status-grid");
  const facts = el(document, "dl", "operations-monitor-facts");
  const cameraActions = el(document, "div", "operations-monitor-camera-actions");
  cameraActions.setAttribute("aria-label", t("monitor.cameraModes"));
  const modeButtons = new Map<OperationsCameraMode, HTMLButtonElement>();
  const cameraSpecs: readonly [OperationsCameraMode, string][] = [
    ["free", t("monitor.free")], ["chase", t("monitor.chase")], ["cockpit", t("monitor.cockpit")],
  ];
  for (const [mode, label] of cameraSpecs) {
    const button = el(document, "button");
    button.type = "button";
    button.textContent = label;
    button.dataset.cameraMode = mode;
    button.addEventListener("click", () => requestCameraMode(mode));
    modeButtons.set(mode, button);
    cameraActions.append(button);
  }
  const gimbalButton = el(document, "button");
  gimbalButton.type = "button";
  gimbalButton.textContent = t("monitor.gimbal");
  gimbalButton.disabled = true;
  gimbalButton.title = t("monitor.gimbalUnavailable");
  cameraActions.append(gimbalButton);

  const previewControls = el(document, "div", "operations-monitor-preview-controls");
  const previewToggle = el(document, "button");
  previewToggle.type = "button";
  previewToggle.textContent = t("monitor.openPreview");
  previewToggle.setAttribute("aria-expanded", "false");
  const swapButton = el(document, "button");
  swapButton.type = "button";
  swapButton.textContent = t("monitor.swap");
  swapButton.hidden = callbacks.onSwap === undefined;
  previewControls.append(previewToggle, swapButton);
  const previewMeta = el(document, "p", "operations-monitor-preview-meta");
  const cameraEvidence = el(document, "details", "operations-monitor-camera-evidence");
  const cameraEvidenceSummary = el(document, "summary");
  const cameraEvidenceValue = el(document, "code");
  cameraEvidence.append(cameraEvidenceSummary, cameraEvidenceValue);
  const previewContainer = el(document, "div", "operations-monitor-preview");
  previewContainer.hidden = true;
  previewContainer.dataset.role = "camera-preview-slot";
  const previewPlaceholder = el(document, "span", "operations-monitor-preview-placeholder");
  previewPlaceholder.textContent = t("monitor.previewSlot");
  previewContainer.append(previewPlaceholder);
  // The preview dock stays pinned to the bottom of the scrolling sidebar, so an opened
  // preview is visible and remains inside the sidebar's own bounds.
  const cameraDock = el(document, "div", "operations-monitor-camera-dock");
  cameraDock.append(previewControls, previewMeta, cameraEvidence, previewContainer);
  detail.append(detailTitle, detailKind, cameraActions, physicalTitle, statusGrid, facts, business, cameraDock);
  sidebar.append(fleetDisclosure, detail);

  const spatial = el(document, "section", "operations-monitor-spatial");
  const mapHeader = el(document, "div", "operations-monitor-map-header");
  const mapTitle = el(document, "h3"); mapTitle.textContent = t("monitor.minimap");
  const footprintState = el(document, "span", "operations-monitor-footprint-state");
  const mapEventState = el(document, "span", "operations-monitor-map-event-state");
  const fitButton = el(document, "button");
  fitButton.type = "button"; fitButton.textContent = t("monitor.fit"); fitButton.title = t("monitor.fitTitle");
  mapHeader.append(mapTitle, footprintState, mapEventState, fitButton);
  const mapSvg = svg(document, "svg", "operations-monitor-map");
  mapSvg.setAttribute("viewBox", `0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`);
  mapSvg.setAttribute("role", "application");
  mapSvg.setAttribute("aria-label", t("monitor.mapAria"));
  mapSvg.setAttribute("tabindex", "0");
  const mapBackground = svg(document, "rect", "operations-monitor-map-background");
  mapBackground.setAttribute("x", "0"); mapBackground.setAttribute("y", "0");
  mapBackground.setAttribute("width", String(MAP_WIDTH)); mapBackground.setAttribute("height", String(MAP_HEIGHT));
  const polygonLayer = svg(document, "g", "operations-monitor-map-polygons");
  const buildingLayer = svg(document, "g", "operations-monitor-map-buildings");
  const routeLayer = svg(document, "g", "operations-monitor-map-routes");
  const footprintLayer = svg(document, "g", "operations-monitor-map-footprint-layer");
  const footprint = svg(document, "path", "operations-monitor-map-footprint");
  footprint.setAttribute("role", "button"); footprint.setAttribute("tabindex", "0");
  footprint.setAttribute("aria-label", t("monitor.footprintDrag"));
  const observerMarker = svg(document, "g", "operations-monitor-map-observer");
  observerMarker.setAttribute("role", "button"); observerMarker.setAttribute("tabindex", "0");
  const observerRing = svg(document, "circle"); observerRing.setAttribute("r", "8");
  const observerDirection = svg(document, "path");
  observerDirection.setAttribute("class", "operations-monitor-map-observer-direction");
  observerDirection.setAttribute("d", "M0,-15 L5,-7 L0,-9 L-5,-7 Z");
  observerMarker.append(observerRing, observerDirection);
  footprintLayer.append(footprint, observerMarker);
  const eventLayer = svg(document, "g", "operations-monitor-map-events");
  const markerLayer = svg(document, "g", "operations-monitor-map-markers");
  const overlayLayer = svg(document, "g", "operations-monitor-map-overlay");
  const north = svg(document, "text", "operations-monitor-map-north");
  north.setAttribute("x", "20"); north.setAttribute("y", "28"); north.textContent = "N ↑";
  const scaleLine = svg(document, "line", "operations-monitor-map-scale");
  const scaleText = svg(document, "text", "operations-monitor-map-scale-label");
  overlayLayer.append(north, scaleLine, scaleText);
  mapSvg.append(mapBackground, polygonLayer, buildingLayer, routeLayer, footprintLayer, eventLayer, markerLayer, overlayLayer);
  const legend = el(document, "p", "operations-monitor-map-legend");
  legend.textContent = t("monitor.legend");
  spatial.append(mapHeader, mapSvg, legend);
  body.append(sidebar, spatial);

  const eventsDisclosure = el(document, "details", "operations-monitor-events");
  eventsDisclosure.open = false;
  const eventsSummary = el(document, "summary");
  const eventsList = el(document, "div", "operations-monitor-event-list");
  const eventsMore = el(document, "button", "operations-monitor-events-more");
  eventsMore.type = "button";
  eventsDisclosure.append(eventsSummary, eventsList, eventsMore);
  root.append(header, body, eventsDisclosure);
  host.append(root);

  let current: OperationsSnapshot | null = null;
  let currentBounds: Bounds = { minX: -50, maxX: 50, minZ: -50, maxZ: 50 };
  let previewShown = false;
  let drag: DragState | null = null;
  let disposed = false;
  let requestedMode: OperationsCameraMode | null = null;
  let fitGeneration = 0;
  let fittedFirstSpatialData = false;
  let eventGroupLimit = INITIAL_EVENT_GROUP_LIMIT;

  function activeMode(): OperationsCameraMode {
    return requestedMode ?? current?.observer?.mode ?? "free";
  }

  function requestCameraMode(mode: OperationsCameraMode): void {
    if (disposed || current === null) return;
    requestedMode = mode;
    updateModeButtons(objectForTarget(current, current.selected));
    callbacks.onCameraMode?.(mode);
  }

  function leaveCockpitForNavigation(): void {
    if (activeMode() === "cockpit") requestCameraMode("free");
  }

  function navigate(position: OperationsPoint2): void {
    leaveCockpitForNavigation();
    callbacks.onNavigate?.(position);
  }

  function updateModeButtons(selected: OperationsObject | undefined): void {
    const mode = activeMode();
    for (const [candidate, button] of modeButtons) {
      const enabled = candidate === "free" || (candidate === "chase" && selected !== undefined && selected.kind !== "facility")
        || (candidate === "cockpit" && selected?.kind === "uav" && selected.camera !== undefined
          && selected.camera.state !== "unavailable" && selected.camera.state !== "disconnected");
      button.disabled = !enabled;
      button.setAttribute("aria-pressed", String(candidate === mode));
    }
  }

  function closePreview(): void {
    if (!previewShown) return;
    previewShown = false;
    previewContainer.hidden = true;
    previewToggle.textContent = t("monitor.openPreview");
    previewToggle.setAttribute("aria-expanded", "false");
    swapButton.disabled = true;
    callbacks.onPreviewToggle?.(false);
  }

  previewToggle.addEventListener("click", () => {
    previewShown = !previewShown;
    previewContainer.hidden = !previewShown;
    previewToggle.textContent = previewShown ? t("monitor.closePreview") : t("monitor.openPreview");
    previewToggle.setAttribute("aria-expanded", String(previewShown));
    swapButton.disabled = !previewShown;
    callbacks.onPreviewToggle?.(previewShown);
  });
  swapButton.addEventListener("click", () => callbacks.onSwap?.());
  eventsMore.addEventListener("click", () => {
    if (current === null) return;
    const groups = prioritizedEventGroups(current);
    eventGroupLimit = eventGroupLimit < groups.length
      ? Math.min(groups.length, eventGroupLimit + EVENT_GROUP_PAGE_SIZE)
      : INITIAL_EVENT_GROUP_LIMIT;
    renderEvents(current);
  });

  fitButton.addEventListener("click", () => {
    if (current === null) return;
    currentBounds = boundsFor(current, document.body.classList.contains("p02-active"));
    fitGeneration++;
    mapSvg.dataset.fitGeneration = String(fitGeneration);
    renderMap(current);
    mapSvg.focus();
  });

  function mapBox(): DOMRect { return mapSvg.getBoundingClientRect(); }
  function pointerWorld(event: PointerEvent): OperationsPoint2 {
    return worldPoint(event.clientX, event.clientY, mapBox(), currentBounds);
  }
  function pointerStart(event: PointerEvent): void {
    if (current === null || event.button !== 0) return;
    const startWorld = pointerWorld(event);
    const observer = current.observer?.position ?? startWorld;
    drag = {
      pointerId: event.pointerId, startClient: { x: event.clientX, y: event.clientY },
      startWorld, observerStart: observer, dragged: false, leftCockpit: false,
    };
    const target = event.currentTarget;
    if (target instanceof SVGElement && "setPointerCapture" in target) {
      try { target.setPointerCapture(event.pointerId); } catch { /* Browsers may reject a synthetic pointer. */ }
    }
    event.preventDefault();
  }
  function footprintMove(event: PointerEvent): void {
    if (drag === null || drag.pointerId !== event.pointerId) return;
    const distance = Math.hypot(event.clientX - drag.startClient.x, event.clientY - drag.startClient.y);
    if (!drag.dragged && distance < DRAG_THRESHOLD_PX) return;
    drag.dragged = true;
    if (!drag.leftCockpit) { leaveCockpitForNavigation(); drag.leftCockpit = true; }
    const at = pointerWorld(event);
    callbacks.onNavigate?.({
      x: drag.observerStart.x + at.x - drag.startWorld.x,
      z: drag.observerStart.z + at.z - drag.startWorld.z,
    });
    event.preventDefault();
  }
  function footprintEnd(event: PointerEvent): void {
    if (drag?.pointerId !== event.pointerId) return;
    drag = null;
    event.preventDefault();
  }
  footprint.addEventListener("pointerdown", pointerStart);
  footprint.addEventListener("pointermove", footprintMove);
  footprint.addEventListener("pointerup", footprintEnd);
  footprint.addEventListener("pointercancel", footprintEnd);
  observerMarker.addEventListener("pointerdown", pointerStart);
  observerMarker.addEventListener("pointermove", footprintMove);
  observerMarker.addEventListener("pointerup", footprintEnd);
  observerMarker.addEventListener("pointercancel", footprintEnd);
  const focusMapForKeyboardNavigation = (event: KeyboardEvent): void => {
    if (event.key === "Enter" || event.key === " ") { leaveCockpitForNavigation(); mapSvg.focus(); event.preventDefault(); }
  };
  footprint.addEventListener("keydown", focusMapForKeyboardNavigation);
  observerMarker.addEventListener("keydown", focusMapForKeyboardNavigation);

  let backgroundPointer: { id: number; start: MapPoint } | null = null;
  mapBackground.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    backgroundPointer = { id: event.pointerId, start: { x: event.clientX, y: event.clientY } };
    event.preventDefault();
  });
  mapBackground.addEventListener("pointerup", event => {
    if (backgroundPointer?.id !== event.pointerId) return;
    const moved = Math.hypot(event.clientX - backgroundPointer.start.x, event.clientY - backgroundPointer.start.y);
    backgroundPointer = null;
    if (moved < DRAG_THRESHOLD_PX) navigate(pointerWorld(event));
    event.preventDefault();
  });
  mapBackground.addEventListener("pointercancel", () => { backgroundPointer = null; });
  mapSvg.addEventListener("keydown", event => {
    if (current === null) return;
    if (event.key === "Home") { fitButton.click(); event.preventDefault(); return; }
    const direction = ({ ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] } as const)[event.key];
    if (direction === undefined) return;
    const centre = current.observer?.position ?? {
      x: (currentBounds.minX + currentBounds.maxX) / 2, z: (currentBounds.minZ + currentBounds.maxZ) / 2,
    };
    navigate({
      x: centre.x + direction[0] * (currentBounds.maxX - currentBounds.minX) * 0.05,
      z: centre.z + direction[1] * (currentBounds.maxZ - currentBounds.minZ) * 0.05,
    });
    event.preventDefault();
  });

  function createFleetRow(): HTMLButtonElement {
    const button = el(document, "button", "operations-monitor-fleet-row");
    button.type = "button"; button.setAttribute("role", "listitem");
    const symbol = el(document, "span", "operations-monitor-fleet-symbol");
    const copy = el(document, "span", "operations-monitor-fleet-copy");
    const label = el(document, "strong");
    const meta = el(document, "small");
    copy.append(label, meta); button.append(symbol, copy);
    return button;
  }

  function fleetEntries(snapshot: OperationsSnapshot): FleetDisplayEntry[] {
    const selected = objectForTarget(snapshot, snapshot.selected);
    const compare = (left: OperationsObject, right: OperationsObject): number =>
      Number(right.id === selected?.id) - Number(left.id === selected?.id)
      || left.label.localeCompare(right.label, "zh-CN") || left.id.localeCompare(right.id);
    const uavs = snapshot.objects.filter(object => object.kind === "uav").sort(compare)
      .map(object => ({ object, group: "uav" as const, groupLabel: t("monitor.uav") }));
    const selectedOther = selected === undefined || selected.kind === "uav" ? [] : [{
      object: selected, group: "selected" as const, groupLabel: t("monitor.selected"),
    }];
    const remainder = snapshot.objects.filter(object => object.kind !== "uav" && object.id !== selectedOther[0]?.object.id);
    const grouped = ([
      ["ugv", t("monitor.ugv")], ["pedestrian", t("monitor.pedestrian")], ["facility", t("monitor.facility")],
    ] as const).flatMap(([kind, label]) => remainder.filter(object => object.kind === kind).sort(compare)
      .map(object => ({ object, group: kind, groupLabel: label })));
    return [...uavs, ...selectedOther, ...grouped];
  }

  function renderFleet(snapshot: OperationsSnapshot): void {
    setText(fleetSummary, `${t("monitor.fleet")} · ${snapshot.objects.length}`);
    const entries = fleetEntries(snapshot);
    reconcileKeyed(fleetList, entries, entry => entry.object.id, createFleetRow, (raw, entry, index) => {
      const object = entry.object;
      const button = raw as HTMLButtonElement;
      const target = targetForObject(object), selected = sameTarget(snapshot.selected, target);
      button.dataset.objectId = object.id; button.dataset.kind = object.kind;
      const groupStart = index === 0 || entries[index - 1]?.group !== entry.group;
      button.dataset.groupStart = String(groupStart);
      button.dataset.groupLabel = entry.groupLabel;
      button.setAttribute("aria-pressed", String(selected));
      button.setAttribute("aria-label", tf("monitor.selectObject", { kind: objectKindLabel(object.kind), label: object.label }));
      setText(button.querySelector(".operations-monitor-fleet-symbol")!, ({ uav: "△", ugv: "◇", pedestrian: "○", facility: "□" } as const)[object.kind]);
      setText(button.querySelector("strong")!, object.label);
      const phase = object.phase ?? object.task?.phase ?? t("monitor.statusUnknown");
      const freshness = object.telemetry?.freshness?.label ?? (object.telemetry?.freshness?.state === "stale" ? t("monitor.staleTelemetry") : undefined);
      setText(button.querySelector("small")!, [phase, freshness].filter(value => value !== undefined).join(" · "));
      button.onclick = () => callbacks.onSelect?.(target);
    });
  }

  function appendFact(label: string, value: string, state?: string): void {
    const term = el(document, "dt"); term.textContent = label;
    const description = el(document, "dd"); description.textContent = value;
    if (state !== undefined) description.classList.add(statusClass(state));
    facts.append(term, description);
  }

  function statusChip(label: string, value: OperationsStatusValue | undefined): HTMLElement {
    const chip = el(document, "span", `operations-monitor-status ${statusClass(value?.state)}`);
    chip.append(Object.assign(el(document, "span", "operations-monitor-status-label"), { textContent: `${label} ` }),
      Object.assign(el(document, "strong", "operations-monitor-status-value"), {
        textContent: value?.label === "未知" ? t("p02.unknown") : value?.label ?? t("p02.unknown"),
      }));
    return chip;
  }

  function renderBusiness(snapshot: OperationsSnapshot): void {
    const expanded = new Set(Array.from(business.querySelectorAll<HTMLDetailsElement>(".operations-monitor-business-details[open]"))
      .map(details => details.parentElement!.dataset.orderId));
    business.replaceChildren();
    delete business.dataset.entityId;
    delete business.dataset.cursorTick;
    business.hidden = snapshot.selected?.kind !== "entity" || snapshot.selectedBusiness === undefined;
    physicalTitle.hidden = business.hidden;
    physicalTitle.textContent = t("monitor.physicalTelemetry");
    if (business.hidden) return;
    business.append(Object.assign(el(document, "h4"), { textContent: t("monitor.businessAssociation") }));
    const selectedBusiness = snapshot.selectedBusiness;
    const source = el(document, "p", "operations-monitor-business-source");
    source.textContent = t(selectedBusiness?.sourceKind === "authored_business_fixture" ? "p02.sourceAuthored"
      : selectedBusiness?.sourceKind === "business_provider" ? "p02.sourceProvider" : "p02.sourceUnknown");
    business.append(source);
    if (selectedBusiness === null || selectedBusiness === undefined || selectedBusiness.orders.length === 0) {
      business.append(Object.assign(el(document, "p", "operations-monitor-business-empty"), { textContent: t("p02.noAssociation") }));
    }
    if (selectedBusiness === null || selectedBusiness === undefined) return;
    business.dataset.entityId = selectedBusiness.entityId;
    business.dataset.cursorTick = String(selectedBusiness.cursorTick);
    business.append(Object.assign(el(document, "p", "operations-monitor-business-cursor"), {
      textContent: `${t("monitor.cursor")} ${selectedBusiness.cursorTick}`,
    }));
    for (const order of selectedBusiness.orders) {
      const row = el(document, "section", "operations-monitor-business-row");
      row.dataset.orderId = order.orderId;
      row.dataset.parcelId = order.parcelId ?? "";
      row.dataset.cursorTick = String(selectedBusiness.cursorTick);
      const compactId = (id: string): string => id.length <= 24 ? id : id.split(".").slice(-2).join(".");
      const label = el(document, "strong", "operations-monitor-business-label");
      label.textContent = `${t("p02.order")} ${compactId(order.orderId)}`;
      label.title = order.orderId;
      const parcel = el(document, "span", "operations-monitor-business-parcel");
      parcel.textContent = `${t("p02.parcel")} ${order.parcelId === undefined ? t("p02.unknown") : compactId(order.parcelId)}`;
      if (order.parcelId !== undefined) parcel.title = order.parcelId;
      const details = el(document, "details", "operations-monitor-business-details");
      details.open = expanded.has(order.orderId);
      details.append(Object.assign(el(document, "summary"), { textContent: t("p02.details") }));
      const fieldsList = el(document, "dl", "operations-monitor-business-fields");
      row.append(label, parcel, details);
      const fields: readonly [string, string, string][] = [
        ["order", t("p02.order"), order.orderId],
        ["parcel", t("p02.parcel"), order.parcelId ?? t("p02.unknown")],
        ["carrier", t("p02.carrier"), order.carrierEntityId ?? t("p02.unknown")],
        ["custody", t("p02.custody"), order.custodyId ?? t("p02.unknown")],
        ["custodyKind", t("monitor.custodyKind"), order.custodyKind ?? t("p02.unknown")],
        ["destination", t("p02.destination"), order.destinationId ?? t("p02.unknown")],
        ["attempt", t("p02.attempt"), order.attempt?.toString() ?? t("p02.unknown")],
        ["status", t("p02.phase"), order.status ?? t("p02.unknown")],
        ["source", t("p02.source"), order.sourceRef],
        ["sourceKind", t("monitor.sourceKind"), selectedBusiness.sourceKind ?? t("p02.unknown")],
        ["cursor", t("monitor.cursor"), String(selectedBusiness.cursorTick)],
        ["recordTick", "tick", String(order.tick)],
      ];
      for (const [field, label, value] of fields) {
        const term = el(document, "dt"); term.textContent = label;
        const description = el(document, "dd"); description.textContent = value;
        description.dataset.businessField = field;
        fieldsList.append(term, description);
      }
      details.append(fieldsList);
      business.append(row);
    }
  }

  function renderDetail(snapshot: OperationsSnapshot): void {
    // Studio can collapse an unselected inspector without hiding a selected
    // object whose measurements are explicitly unavailable.
    detail.dataset.selectionState = snapshot.selected === null ? "empty" : "selected";
    const selected = objectForTarget(snapshot, snapshot.selected);
    const selectedEvent = eventForTarget(snapshot, snapshot.selected);
    facts.replaceChildren(); statusGrid.replaceChildren();
    renderBusiness(snapshot);
    cameraEvidenceSummary.textContent = t("monitor.rawSource");
    cameraEvidence.hidden = true;
    cameraEvidenceValue.textContent = "";
    delete cameraEvidenceValue.dataset.sourceKind;
    delete cameraEvidenceValue.dataset.sourceState;
    if (selected === undefined && selectedEvent === undefined) {
      detailTitle.textContent = snapshot.selected === null ? t("monitor.noSelection") : snapshot.selected.id;
      detailKind.textContent = snapshot.selected === null ? t("monitor.choose") : t("monitor.noMonitorData");
      appendFact(t("monitor.position"), t("p02.unknown"));
      previewMeta.textContent = t("monitor.cameraUnknown");
      closePreview();
      previewToggle.disabled = true; swapButton.disabled = true;
      updateModeButtons(undefined);
      return;
    }
    if (selectedEvent !== undefined) {
      detailTitle.textContent = selectedEvent.label;
      detailKind.textContent = `${t("monitor.event")} · ${selectedEvent.condition}`;
      statusGrid.append(statusChip(t("monitor.severity"), {
        label: severityLabel(selectedEvent.severity),
        state: selectedEvent.severity === "info" ? "normal" : selectedEvent.severity === "unknown" ? "unknown" : selectedEvent.severity,
      }));
      appendFact(t("monitor.eventTime"), formatClock(selectedEvent.timeSeconds));
      appendFact(t("monitor.position"), selectedEvent.locationLabel ?? (selectedEvent.position === undefined ? t("p02.unknown") : `E ${valueText(selectedEvent.position.x, " m")} / S ${valueText(selectedEvent.position.z, " m")}`));
      appendFact(t("monitor.affected"), selectedEvent.objectIds.length === 0 ? t("p02.unknown") : selectedEvent.objectIds.join("、"));
      appendFact(t("monitor.task"), selectedEvent.missionId ?? t("p02.unknown"));
      appendFact(t("p02.order"), selectedEvent.orderId ?? t("p02.unknown"));
      appendFact(t("monitor.facility"), selectedEvent.facilityId ?? t("p02.unknown"));
      appendFact(t("monitor.dependencies"), selectedEvent.dependencies?.join("、") ?? t("p02.unknown"));
      appendFact(t("monitor.acknowledgment"), selectedEvent.acknowledged === undefined ? t("p02.unknown") : selectedEvent.acknowledged ? t("monitor.acknowledged") : t("monitor.unacknowledged"));
      appendFact(t("monitor.resolution"), selectedEvent.resolved === undefined ? t("p02.unknown") : selectedEvent.resolved ? t("monitor.resolved") : t("monitor.ongoing"));
      previewMeta.textContent = t("monitor.cameraUnknown");
      closePreview();
      previewToggle.disabled = true; swapButton.disabled = true;
      updateModeButtons(undefined);
      return;
    }
    const object = selected!;
    const telemetry = object.telemetry;
    detailTitle.textContent = object.label;
    detailKind.textContent = `${objectKindLabel(object.kind)} · ${object.phase ?? object.task?.phase ?? t("monitor.phaseUnknown")}`;
    statusGrid.append(
      statusChip(t("monitor.activity"), telemetry?.activity), statusChip(t("monitor.connectivity"), telemetry?.connectivity), statusChip(t("monitor.health"), telemetry?.health),
    );
    appendFact(t("monitor.position"), `E ${valueText(object.position.x, " m")} / N ${valueText(-object.position.z, " m")}`);
    appendFact(t("monitor.altitude"), valueText(telemetry?.altitudeM, telemetry?.altitudeReference === undefined ? ` m (${t("monitor.altitudeUnknown")})` : ` m ${telemetry.altitudeReference}`));
    appendFact(t("monitor.heading"), object.headingRad === undefined ? t("p02.unknown") : `${Number((object.headingRad * 180 / Math.PI).toFixed(1))}° (${t("monitor.trueNorth")})`);
    appendFact(t("monitor.speed"), valueText(telemetry?.speedMps, " m/s"));
    appendFact(t("monitor.battery"), valueText(telemetry?.batteryPercent, "%"));
    appendFact(t("monitor.payload"), telemetry?.payload ?? t("p02.unknown"));
    if (snapshot.selectedBusiness === undefined) {
      appendFact(t("monitor.nextDestination"), telemetry?.nextDestination ?? t("p02.unknown"));
      appendFact(t("monitor.task"), object.task?.label ?? object.task?.id ?? t("p02.unknown"));
      appendFact(t("p02.order"), object.task?.orderId ?? t("p02.unknown"));
    }
    const freshness = telemetry?.freshness;
    appendFact(t("monitor.freshness"), freshness?.label ?? (freshness === undefined ? t("p02.unknown") : `${freshness.state === "fresh" ? t("monitor.fresh") : freshness.state === "stale" ? t("monitor.stale") : t("p02.unknown")}${freshness.ageSeconds === undefined ? "" : ` · ${valueText(freshness.ageSeconds, " s")}`}`), freshness?.state);
    appendFact(t("monitor.sampleTime"), telemetry?.observedAtSeconds === undefined ? t("p02.unknown") : formatClock(telemetry.observedAtSeconds));
    for (const fact of telemetry?.facts ?? []) appendFact(fact.label, valueText(fact.value, fact.unit === undefined ? "" : ` ${fact.unit}`), fact.state);
    const camera = object.camera;
    if (camera !== undefined) {
      cameraEvidence.hidden = false;
      cameraEvidenceValue.textContent = `${camera.source} · ${camera.state}`;
      cameraEvidenceValue.dataset.sourceKind = camera.source;
      cameraEvidenceValue.dataset.sourceState = camera.state;
    }
    previewMeta.textContent = camera === undefined ? t("monitor.cameraUnknown")
      : `${t("monitor.cameraSource")}${t("monitor.colon")}${sourceLabel(camera.source)} · ${cameraStateLabel(camera.state)}${camera.reason === undefined ? "" : ` · ${camera.reason}`}`;
    const cameraAvailable = object.kind === "uav" && camera !== undefined && camera.state !== "unavailable" && camera.state !== "disconnected";
    previewToggle.disabled = !cameraAvailable;
    if (!cameraAvailable) closePreview();
    swapButton.disabled = !previewShown || !cameraAvailable;
    updateModeButtons(object);
  }

  function renderMap(snapshot: OperationsSnapshot): void {
    const p02 = document.body.classList.contains("p02-active");
    const markers = p02 ? snapshot.objects.filter(object => object.kind !== "facility"
      || object.task !== undefined || sameTarget(snapshot.selected, targetForObject(object))) : snapshot.objects;
    const staticCells = new Map<string, OperationsBuildingSquare>();
    if (p02) for (const object of snapshot.objects) {
      if (object.kind !== "facility" || markers.includes(object)) continue;
      const point = mapPoint(object.position, currentBounds);
      const id = `static:${Math.floor(point.x / 10)}:${Math.floor(point.y / 10)}`;
      if (!staticCells.has(id)) staticCells.set(id, { id, x: object.position.x, z: object.position.z,
        sizeM: Math.max(4, (currentBounds.maxX - currentBounds.minX) * 0.012) });
    }
    markerLayer.dataset.staticObjectCount = String(snapshot.objects.length - markers.length);
    markerLayer.dataset.staticCellCount = String(staticCells.size);
    const contextSquares = [...(snapshot.buildings ?? []), ...staticCells.values()];
    const contextCells = new Map<string, OperationsBuildingSquare>();
    if (p02) for (const square of contextSquares) {
      const point = mapPoint(square, currentBounds);
      const id = `context:${Math.floor(point.x / 8)}:${Math.floor(point.y / 8)}`;
      if (!contextCells.has(id)) contextCells.set(id, { ...square, id });
    }
    // Static building squares: context only — appended under the routes and
    // never included in the fit bounds. One square per grid cell; cells move
    // with the fit so squares keep their metre size at every zoom.
    reconcileKeyed(buildingLayer, p02 ? [...contextCells.values()] : contextSquares, item => item.id,
      () => svg(document, "rect", "operations-monitor-map-building"), (raw, item) => {
        const node = raw as SVGRectElement;
        const half = item.sizeM / 2;
        const from = mapPoint({ x: item.x - half, z: item.z - half }, currentBounds);
        const to = mapPoint({ x: item.x + half, z: item.z + half }, currentBounds);
        node.setAttribute("x", from.x.toFixed(2));
        node.setAttribute("y", from.y.toFixed(2));
        node.setAttribute("width", Math.max(1, to.x - from.x).toFixed(2));
        node.setAttribute("height", Math.max(1, to.y - from.y).toFixed(2));
      });
    reconcileKeyed(polygonLayer, snapshot.polygons ?? [], item => item.id,
      () => svg(document, "path", "operations-monitor-map-polygon"), (raw, item) => {
        const node = raw as SVGPathElement; node.setAttribute("d", pathData(item.points, currentBounds, true));
        node.dataset.kind = item.kind ?? "other"; node.setAttribute("aria-label", item.label ?? item.id);
      });
    reconcileKeyed(routeLayer, snapshot.routes ?? [], item => item.id,
      () => svg(document, "path", "operations-monitor-map-route"), (raw, item) => {
        const node = raw as SVGPathElement; node.setAttribute("d", pathData(item.points, currentBounds, false));
        node.setAttribute("aria-label", item.label ?? item.id);
        node.classList.toggle("is-selected", item.objectId !== undefined && snapshot.selected?.id === item.objectId);
      });
    const visibleFootprint = snapshot.observer?.footprint !== undefined && snapshot.observer.footprint.length >= 3;
    footprint.setAttribute("d", visibleFootprint ? pathData(snapshot.observer!.footprint!, currentBounds, true) : "");
    footprint.setAttribute("visibility", visibleFootprint ? "visible" : "hidden");
    footprint.setAttribute("aria-disabled", String(!visibleFootprint));
    footprint.setAttribute("tabindex", visibleFootprint ? "0" : "-1");
    const observerPosition = snapshot.observer?.position;
    const observerVisible = observerPosition !== undefined;
    observerMarker.setAttribute("visibility", observerVisible ? "visible" : "hidden");
    observerMarker.setAttribute("tabindex", observerVisible ? "0" : "-1");
    if (observerPosition !== undefined) {
      const point = mapPoint(observerPosition, currentBounds);
      observerMarker.setAttribute("transform", `translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})`);
      observerDirection.setAttribute("transform", snapshot.observer?.headingRad === undefined
        ? "" : `rotate(${(snapshot.observer.headingRad * 180 / Math.PI).toFixed(2)})`);
    }
    const footprintMessage = !observerVisible ? t("monitor.centreUnknown")
      : visibleFootprint ? t("monitor.footprintAvailable") : t("monitor.footprintUnavailable");
    footprintState.textContent = footprintMessage;
    footprintState.dataset.state = !observerVisible ? "unknown" : visibleFootprint ? "available" : "unavailable";
    observerMarker.setAttribute("aria-label", visibleFootprint
      ? t("monitor.observerAria")
      : t("monitor.observerIncompleteAria"));

    const allPositionedEvents = prioritizedEventGroups(snapshot).map(group => group.event)
      .filter(event => event.position !== undefined);
    const positionedEvents = allPositionedEvents.slice(0, MAX_MINIMAP_EVENTS);
    eventLayer.dataset.total = String(allPositionedEvents.length);
    eventLayer.dataset.rendered = String(positionedEvents.length);
    mapEventState.textContent = allPositionedEvents.length > positionedEvents.length
      ? tf("monitor.eventPoints", { shown: positionedEvents.length, total: allPositionedEvents.length }) : "";
    mapEventState.hidden = allPositionedEvents.length <= positionedEvents.length;
    reconcileKeyed(eventLayer, positionedEvents, event => event.id, () => {
      const button = svg(document, "g", "operations-monitor-map-event");
      button.setAttribute("role", "button"); button.setAttribute("tabindex", "0");
      const shape = svg(document, "path"); shape.setAttribute("d", "M0,-9 L8,7 L-8,7 Z");
      const mark = svg(document, "text"); mark.setAttribute("x", "0"); mark.setAttribute("y", "5"); mark.textContent = "!";
      button.append(shape, mark); return button;
    }, (raw, event) => {
      const node = raw as SVGGElement, point = mapPoint(event.position!, currentBounds);
      node.setAttribute("transform", `translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})`);
      node.dataset.severity = event.severity;
      node.setAttribute("aria-label", tf("monitor.locateSeverityEvent", { label: event.label, severity: severityLabel(event.severity) }));
      const activate = (): void => { callbacks.onSelect?.({ kind: "event", id: event.id }); navigate(event.position!); };
      node.onclick = activate;
      (node as unknown as { onkeydown: ((event: KeyboardEvent) => void) | null }).onkeydown = keyEvent => {
        if (keyEvent.key === "Enter" || keyEvent.key === " ") { activate(); keyEvent.preventDefault(); }
      };
    });

    const showAllLabels = markers.length <= 12;
    reconcileKeyed(markerLayer, markers, object => object.id, () => {
      const button = svg(document, "g", "operations-monitor-map-marker");
      button.setAttribute("role", "button"); button.setAttribute("tabindex", "0");
      const halo = svg(document, "circle", "operations-monitor-map-marker-halo"); halo.setAttribute("r", "11");
      const shape = svg(document, "path", "operations-monitor-map-marker-shape");
      const label = svg(document, "text", "operations-monitor-map-marker-label"); label.setAttribute("x", "13"); label.setAttribute("y", "4");
      button.append(halo, shape, label); return button;
    }, (raw, object) => {
      const node = raw as SVGGElement, point = mapPoint(object.position, currentBounds), target = targetForObject(object);
      const selected = sameTarget(snapshot.selected, target);
      node.setAttribute("transform", `translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})`);
      node.dataset.objectId = object.id; node.dataset.kind = object.kind;
      node.dataset.x = String(object.position.x); node.dataset.z = String(object.position.z);
      node.classList.toggle("is-selected", selected);
      node.setAttribute("aria-pressed", String(selected));
      node.setAttribute("aria-label", tf("monitor.selectObject", { kind: objectKindLabel(object.kind), label: object.label }));
      const shape = node.querySelector(".operations-monitor-map-marker-shape")!;
      shape.setAttribute("d", object.kind === "uav" ? "M0,-9 L7,7 L0,4 L-7,7 Z"
        : object.kind === "ugv" ? "M0,-8 L8,0 L0,8 L-8,0 Z"
          : object.kind === "facility" ? "M-7,-7 H7 V7 H-7 Z" : "M0,-7 A7,7 0 1,1 -0.01,-7 Z");
      shape.setAttribute("transform", object.headingRad === undefined || object.kind === "facility" || object.kind === "pedestrian"
        ? "" : `rotate(${(object.headingRad * 180 / Math.PI).toFixed(2)})`);
      const label = node.querySelector(".operations-monitor-map-marker-label")!;
      setText(label, object.label);
      const labelOnLeft = point.x > MAP_WIDTH / 2;
      label.setAttribute("x", labelOnLeft ? "-13" : "13");
      label.setAttribute("text-anchor", labelOnLeft ? "end" : "start");
      label.setAttribute("visibility", showAllLabels || selected ? "visible" : "hidden");
      node.dataset.decluttered = String(!showAllLabels && !selected);
      const activate = (): void => callbacks.onSelect?.(target);
      node.onclick = activate;
      (node as unknown as { onkeydown: ((event: KeyboardEvent) => void) | null }).onkeydown = keyEvent => {
        if (keyEvent.key === "Enter" || keyEvent.key === " ") { activate(); keyEvent.preventDefault(); }
      };
    });
    const scaleMetres = niceScale((currentBounds.maxX - currentBounds.minX) * 0.25);
    const scalePixels = scaleMetres / (currentBounds.maxX - currentBounds.minX) * (MAP_WIDTH - MAP_PADDING * 2);
    scaleLine.setAttribute("x1", String(MAP_WIDTH - MAP_PADDING - scalePixels));
    scaleLine.setAttribute("x2", String(MAP_WIDTH - MAP_PADDING));
    scaleLine.setAttribute("y1", String(MAP_HEIGHT - 19)); scaleLine.setAttribute("y2", String(MAP_HEIGHT - 19));
    scaleText.setAttribute("x", String(MAP_WIDTH - MAP_PADDING)); scaleText.setAttribute("y", String(MAP_HEIGHT - 23));
    scaleText.setAttribute("text-anchor", "end"); scaleText.textContent = `${Number(scaleMetres.toFixed(2))} m`;
  }

  function severityLabel(severity: OperationsSeverity): string {
    return ({ critical: t("monitor.critical"), warning: t("monitor.warning"), info: t("monitor.info"), unknown: t("monitor.severityUnknown") } as const)[severity];
  }

  function prioritizedEvents(snapshot: OperationsSnapshot): OperationsEvent[] {
    return [...(snapshot.events ?? [])].sort((left, right) =>
      resolutionRank(left) - resolutionRank(right)
      || SEVERITY_ORDER[left.severity] - SEVERITY_ORDER[right.severity]
      || right.timeSeconds - left.timeSeconds || left.id.localeCompare(right.id));
  }

  function resolutionRank(event: OperationsEvent): number {
    return event.resolved === false ? 0 : event.resolved === undefined ? 1 : 2;
  }

  function resolutionKey(event: OperationsEvent): string {
    return event.resolved === false ? "active" : event.resolved === true ? "resolved" : "unknown";
  }

  function prioritizedEventGroups(snapshot: OperationsSnapshot): OperationsEventGroup[] {
    const groups = new Map<string, { event: OperationsEvent; occurrences: OperationsEvent[] }>();
    for (const event of prioritizedEvents(snapshot)) {
      const key = event.groupKey === undefined ? event.id : JSON.stringify([
        "group", event.groupKey, event.condition, event.severity, resolutionKey(event), [...event.objectIds].sort(),
      ]);
      const group = groups.get(key);
      if (group === undefined) groups.set(key, { event, occurrences: [event] });
      else group.occurrences.push(event);
    }
    return [...groups].map(([key, group]) => ({ key, ...group }));
  }

  function createEventRow(): HTMLElement {
    const article = el(document, "article", "operations-monitor-event");
    const button = el(document, "button", "operations-monitor-event-main"); button.type = "button";
    const severity = el(document, "span", "operations-monitor-event-severity");
    const copy = el(document, "span", "operations-monitor-event-copy");
    const label = el(document, "strong"), meta = el(document, "small"); copy.append(label, meta); button.append(severity, copy);
    const evidence = el(document, "details", "operations-monitor-event-evidence");
    const summary = el(document, "summary"); summary.textContent = t("monitor.evidenceLinks");
    const content = el(document, "p"); evidence.append(summary, content); article.append(button, evidence); return article;
  }

  function renderEvents(snapshot: OperationsSnapshot): void {
    const allEvents = prioritizedEvents(snapshot);
    const groups = prioritizedEventGroups(snapshot);
    const visibleGroups = groups.slice(0, eventGroupLimit);
    const active = allEvents.filter(event => event.resolved === false).length;
    const unknown = allEvents.filter(event => event.resolved === undefined).length;
    eventsSummary.textContent = tf("monitor.eventSummary", { active, unknown, total: allEvents.length });
    reconcileKeyed(eventsList, visibleGroups, group => group.key, createEventRow, (raw, group, index) => {
      const event = group.event;
      const article = raw as HTMLElement, button = article.querySelector<HTMLButtonElement>(".operations-monitor-event-main")!;
      article.dataset.severity = event.severity; article.dataset.resolution = resolutionKey(event);
      article.dataset.eventId = event.id; article.dataset.occurrences = String(group.occurrences.length);
      button.setAttribute("aria-label", tf("monitor.locateEvent", { label: event.label }));
      button.setAttribute("aria-pressed", String(group.occurrences.some(occurrence =>
        sameTarget(snapshot.selected, { kind: "event", id: occurrence.id }))));
      setText(article.querySelector(".operations-monitor-event-severity")!, severityLabel(event.severity));
      setText(article.querySelector("strong")!, event.label);
      setText(article.querySelector("small")!, `${formatClock(event.timeSeconds)} · ${event.condition}${event.locationLabel === undefined ? "" : ` · ${event.locationLabel}`}${event.acknowledged ? ` · ${t("monitor.acknowledged")}` : ""}${event.resolved ? ` · ${t("monitor.resolved")}` : ""}`);
      const evidence = article.querySelector<HTMLDetailsElement>(".operations-monitor-event-evidence")!;
      if (!evidence.dataset.initialized) { evidence.open = index === 0 && event.severity === "critical" && !event.resolved; evidence.dataset.initialized = "true"; }
      const unique = (values: readonly (string | undefined)[]): string[] => [...new Set(values.filter((value): value is string => value !== undefined))];
      const objectIds = unique(group.occurrences.flatMap(occurrence => occurrence.objectIds));
      const missions = unique(group.occurrences.map(occurrence => occurrence.missionId));
      const orders = unique(group.occurrences.map(occurrence => occurrence.orderId));
      const facilities = unique(group.occurrences.map(occurrence => occurrence.facilityId));
      const dependencies = unique(group.occurrences.flatMap(occurrence => occurrence.dependencies ?? []));
      const evidenceItems = unique(group.occurrences.flatMap(occurrence => occurrence.evidence ?? []));
      const links = [
        objectIds.length === 0 ? `${t("monitor.affected")}${t("monitor.colon")}${t("p02.unknown")}` : `${t("monitor.affected")}${t("monitor.colon")}${objectIds.join("、")}`,
        `${t("monitor.task")}${t("monitor.colon")}${missions.join("、") || t("p02.unknown")}`, `${t("p02.order")}${t("monitor.colon")}${orders.join("、") || t("p02.unknown")}`,
        `${t("monitor.facility")}${t("monitor.colon")}${facilities.join("、") || t("p02.unknown")}`, `${t("monitor.dependencies")}${t("monitor.colon")}${dependencies.join("、") || t("p02.unknown")}`,
        `${t("monitor.evidence")}${t("monitor.colon")}${evidenceItems.join("；") || t("p02.unknown")}`,
        `${t("monitor.acknowledgment")}${t("monitor.colon")}${event.acknowledged === undefined ? t("p02.unknown") : event.acknowledged ? t("monitor.acknowledged") : t("monitor.unacknowledged")}`,
        `${t("monitor.resolution")}${t("monitor.colon")}${event.resolved === undefined ? t("p02.unknown") : event.resolved ? t("monitor.resolved") : t("monitor.ongoing")}`,
      ];
      if (group.occurrences.length > 1) links.push(tf("monitor.repeat", { count: group.occurrences.length, records: group.occurrences
        .map(occurrence => `${occurrence.id}@${formatClock(occurrence.timeSeconds)}`).join("、") }));
      setText(evidence.querySelector("p")!, links.join(" · "));
      button.onclick = () => {
        callbacks.onSelect?.({ kind: "event", id: event.id });
        if (event.position !== undefined) navigate(event.position);
      };
    });
    if (allEvents.length === 0) {
      const empty = el(document, "p", "operations-monitor-empty"); empty.textContent = t("monitor.noEvents"); eventsList.append(empty);
    }
    const remaining = Math.max(0, groups.length - visibleGroups.length);
    eventsMore.hidden = groups.length <= INITIAL_EVENT_GROUP_LIMIT;
    if (!eventsMore.hidden) {
      eventsMore.textContent = remaining > 0 ? tf("monitor.moreEvents", { count: remaining }) : t("monitor.collapseEvents");
      eventsMore.setAttribute("aria-expanded", String(remaining === 0));
    }
  }

  function refreshLabels(): void {
    root.setAttribute("aria-label", t("monitor.root"));
    detail.setAttribute("aria-label", t("monitor.detail"));
    cameraActions.setAttribute("aria-label", t("monitor.cameraModes"));
    modeButtons.get("free")!.textContent = t("monitor.free");
    modeButtons.get("chase")!.textContent = t("monitor.chase");
    modeButtons.get("cockpit")!.textContent = t("monitor.cockpit");
    gimbalButton.textContent = t("monitor.gimbal");
    gimbalButton.title = t("monitor.gimbalUnavailable");
    previewToggle.textContent = t(previewShown ? "monitor.closePreview" : "monitor.openPreview");
    swapButton.textContent = t("monitor.swap");
    previewPlaceholder.textContent = t("monitor.previewSlot");
    mapTitle.textContent = t("monitor.minimap");
    fitButton.textContent = t("monitor.fit");
    fitButton.title = t("monitor.fitTitle");
    mapSvg.setAttribute("aria-label", t("monitor.mapAria"));
    footprint.setAttribute("aria-label", t("monitor.footprintDrag"));
    legend.textContent = t("monitor.legend");
    for (const summary of eventsList.querySelectorAll(".operations-monitor-event-evidence summary")) {
      summary.textContent = t("monitor.evidenceLinks");
    }
  }

  function update(snapshot: OperationsSnapshot): void {
    if (disposed) throw new Error("Operations monitor is disposed");
    validateSnapshot(snapshot);
    refreshLabels();
    const sourceChanged = current?.sourceKey !== snapshot.sourceKey;
    const selectionChanged = !sameTarget(current?.selected ?? null, snapshot.selected);
    const p02 = document.body.classList.contains("p02-active");
    current = snapshot;
    const sceneHasSpatialData = hasSceneSpatialData(snapshot);
    if (sourceChanged) {
      fittedFirstSpatialData = sceneHasSpatialData;
      eventGroupLimit = INITIAL_EVENT_GROUP_LIMIT;
      currentBounds = boundsFor(snapshot, p02); fitGeneration++; mapSvg.dataset.fitGeneration = String(fitGeneration);
    }
    if (!sourceChanged && !fittedFirstSpatialData && sceneHasSpatialData) {
      fittedFirstSpatialData = true;
      currentBounds = boundsFor(snapshot, p02); fitGeneration++; mapSvg.dataset.fitGeneration = String(fitGeneration);
    }
    if (!sourceChanged && p02 && selectionChanged && objectForTarget(snapshot, snapshot.selected) !== undefined) {
      currentBounds = boundsFor(snapshot, true); fitGeneration++; mapSvg.dataset.fitGeneration = String(fitGeneration);
    }
    // A snapshot is authoritative. This also clears an optimistic mode when the owner refused it.
    requestedMode = null;
    source.textContent = snapshot.sourceLabel; source.title = snapshot.sourceKey;
    source.dataset.sourceKey = snapshot.sourceKey;
    clock.textContent = formatClock(snapshot.timeSeconds); clock.dateTime = `PT${snapshot.timeSeconds}S`;
    clockState.textContent = clockLabel(snapshot.clockState); clockState.dataset.clockState = snapshot.clockState;
    renderFleet(snapshot); renderDetail(snapshot); renderMap(snapshot); renderEvents(snapshot);
  }

  const unsubscribeLanguage = subscribeLanguage(() => {
    if (current !== null) update(current); else refreshLabels();
  });

  return {
    element: root, previewContainer,
    update,
    dispose(): void {
      if (disposed) return;
      disposed = true; unsubscribeLanguage(); drag = null; backgroundPointer = null; root.remove();
    },
  };
}
