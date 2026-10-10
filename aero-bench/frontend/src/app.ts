import { renderUnboundMissionRecords } from "./p02-mission-records";
import { LoadingProgressView, type ProgressListener } from "./loading-progress";
import { ReplayEvents } from "./replay-events";
import { showAvailableContent } from "./available-content";
import { PublicTraceMap, type MapScene } from "./map";
import { studioConfigurationUrl } from "./p02-studio-navigation";
import type { PublicScenario, PublicTrafficLightFrame, SceneState,
  ReplayAccessRequest } from "./generated/aero-bench-contracts";
import type { OsmBuildingKind as BuildingType, OsmTrafficSignal as TrafficLight } from "./osm2world/source";
import {
  PublicTraceStore,
  parsePublicTrace,
  type PublicTrace,
  type PublicNetworkEvent,
  type PublicRunEvent,
  type VerifiedPublicTracePayload,
} from "./trace";
import { AssetResolutionError, AssetResolver } from "./asset-resolver";
import { jsonValuesEqual, SealedReplayError, SealedReplayLoader,
  MAX_MANIFEST_BYTES as CONTROL_REPLAY_MANIFEST_MAX_BYTES } from "./replay-loader";
import { controlReplayBaseHref, controlReplayFetch, type ControlReplaySource } from "./control-replay-source";
import { RunStartIdentityStore } from "./run-start-identity";
import { assertNotAborted, readBoundedResponse } from "./verified-bytes";
import { parseStrictJson } from "./strict-json";
import { assertPublicReplayManifest, assertReplayAccessRequest,
  assertReplayAccessResponse } from "./generated/contract-validators";
import { loadScenarioMeshPack } from "./osm2world/assets";
import type { LoadedMeshPack } from "./osm2world/pack-loader";
import { inspectNativeCityPresentation, loadNativeCityPresentation,
  type LoadedNativeCityPresentation } from "./native-city-presentation";
import type { OsmJson } from "./osm2world/source";
import {
  fetchWorkspaceTraceCatalog,
  loadableWorkspaceTraces,
  type WorkspaceTraceCatalog,
  type WorkspaceTraceEntry,
} from "./workspace-traces";
import { ControlClient, ControlProtocolError, controlErrorFromResponse,
  type BootstrapCredentials, type RunCredentials } from "./run-control";
import { RunSession, type RunSessionState } from "./run-store";
import { AgentConsole } from "./agent-console";
import { InteractionTimeline } from "./interaction-timeline";
import { TelemetryPanel } from "./telemetry-panel";
import { TerminalDock } from "./terminal-dock";
import { TelemetryHud } from "./telemetry-hud";
import { placeSourceMessage, type SourceMessageRect } from "./source-message-placement";
import { CameraState } from "./state/camera";
import { ContextMenuState, type ContextMenu } from "./state/context-menu";
import { currentLanguage, initLanguage, setLanguage, subscribeLanguage, t, tf, type I18nKey } from "./i18n";
import { HoverState } from "./state/hover";
import { LAYER_TREE, LayerState, type LayerId } from "./state/layers";
import { PLAY_SPEEDS, ReplayState, type PlaySpeed } from "./state/replay";
import { SelectionState } from "./state/selection";
import { sameTarget, type TraceTarget } from "./state/target";
import {
  displayMeters,
  displayValue,
  formatBytes,
  formatSimTime,
  integrityForTracePhase,
  livePhaseKey,
  networkFrameAtSceneState,
  recordedTicks,
  runtimePhaseKey,
  sceneStateAtTick,
  sampleAtTick,
  shortDigest,
  simTimeAtTick,
  statusTone,
  tracePhaseKey,
  trajectoriesFromSceneStates,
} from "./view-model";
import {
  actionButton,
  append,
  element,
  emptyRow,
  keyValue,
  metricCard,
  pill,
  sectionTitle,
  statusValue,
  textBlock,
} from "./ui";
import {
  buildRunIndex,
  selectedFrameBusinessView,
  type SelectedFrameBusinessView,
  entityKindsOf,
  formatRunClock,
  p02OrderSelection,
  p02OverviewPose,
  type BusinessIdentityEnvelope,
  type OverviewPoseEnu,
  type RunIndex,
} from "./p02-entity-overlays";
import "./p02-view.css";

export type AppMode = "live" | "replay";
type InfoTab = "overview" | "telemetry" | "agent" | "interactions" | "network" | "evidence" | "boundary";

/** Same-origin sealed replay bundle directory (`assets/<sha256>`). */
export const DEFAULT_REPLAY_ASSET_BASE = "./replay/";

/** Mint read-only access to an already sealed run; never start or reconstruct a live session. */
export async function requestRegisteredReplaySource(client: ControlClient,
    bootstrap: BootstrapCredentials, runId: string, signal?: AbortSignal): Promise<ControlReplaySource> {
  assertNotAborted(signal);
  for (const [label, value] of [["run_id", runId], ["bootstrap operator token", bootstrap.operatorToken],
    ["bootstrap CSRF token", bootstrap.csrfToken]] as const) {
    if (!/^[0-9a-f]{64}$/.test(value)) {
      throw new ControlProtocolError(`${label} must be a 64-character lowercase digest`);
    }
  }
  const request: ReplayAccessRequest = { schema_version: "aero-bench.replay-access-request/v1" };
  assertReplayAccessRequest(request);
  const response = await client.fetchImpl(`${client.serviceOrigin}/v1/runs/${runId}/replay-access`, {
    method: "POST", redirect: "error", signal,
    headers: { Authorization: `Bearer ${bootstrap.operatorToken}`, "X-Aero-Bench-CSRF": bootstrap.csrfToken,
      "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw await controlErrorFromResponse(response);
  let value: unknown;
  try { value = await response.json(); }
  catch { throw new ControlProtocolError("replay access response carried invalid JSON"); }
  assertNotAborted(signal);
  try { assertReplayAccessResponse(value); }
  catch (error) {
    throw new ControlProtocolError(`replay access response violated its contract: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (value.credentials.run_id !== runId) {
    throw new ControlProtocolError("replay access credentials belong to another run");
  }
  const credentials: RunCredentials = { runId, operatorToken: value.credentials.operator_token,
    csrfToken: value.credentials.csrf_token };
  return { runId,
    manifest: signal => client.publicReplayManifest(credentials, signal),
    trace: signal => client.publicTrace(credentials, signal),
    asset: (digest, signal) => client.publicAsset(credentials, digest, signal),
  };
}

const INFO_TABS: readonly InfoTab[] = [
  "overview",
  "telemetry",
  "agent",
  "interactions",
  "network",
  "evidence",
  "boundary",
];
const TAB_SECTION_KEYS: Readonly<Record<InfoTab, I18nKey>> = {
  overview: "tab.overview",
  telemetry: "tab.telemetry",
  agent: "tab.agent",
  interactions: "tab.interactions",
  network: "tab.network",
  evidence: "tab.evidence",
  boundary: "tab.boundary",
};

const LAYER_GROUP_KEYS: Readonly<Record<string, I18nKey>> = {
  base: "layerGroup.base",
  overlay: "layerGroup.overlay",
  operational: "layerGroup.operational",
};
const LAYER_LABEL_KEYS: Readonly<Record<LayerId, I18nKey>> = {
  imagery: "layer.imagery",
  terrain: "layer.terrain",
  buildings: "layer.buildings",
  roads: "layer.roads",
  weather: "layer.weather",
  regions: "layer.regions",
  uav: "layer.uav",
  ugv: "layer.ugv",
  pedestrian: "layer.pedestrian",
  static_assets: "layer.static_assets",
  trajectories: "layer.trajectories",
  network_links: "layer.network_links",
};
const KIND_DOT_COLORS: Readonly<Record<string, string>> = {
  uav: "#4fd8f5",
  ugv: "#7ee08a",
  pedestrian: "#e8cf7a",
  static_asset: "#8ba7b6",
  undeclared: "#b7c7d4",
};

/** Bootstrap credentials held in memory only, never rendered or persisted. */
interface BootstrapMemory {
  readonly operatorToken: string;
  readonly csrfToken: string;
}

/** Resolution state of one declared replay asset. */
interface AssetResolution {
  readonly assetId: string;
  readonly replayPath: string;
  readonly sha256: string;
  readonly state: "resolved" | "failed";
  readonly detail: string;
}

type SealedReplayStatus = "idle" | "loading" | "ready" | "failed";

interface SealedReplaySourceRequest {
  readonly traceUrl: URL;
  readonly fetch?: typeof fetch;
  fetchTrace(signal: AbortSignal, onProgress: ProgressListener): Promise<VerifiedPublicTracePayload>;
  verifyTrace?(payload: VerifiedPublicTracePayload, signal: AbortSignal): Promise<void>;
}

function assertIndexedSceneStateMatchesTrace(
  trace: PublicTrace,
  state: SceneState,
): void {
  const expected = sceneStateAtTick(trace, state.at.tick);
  if (expected === null || !jsonValuesEqual(expected, state)) {
    throw new SealedReplayError(
      `indexed SceneState at tick ${state.at.tick} differs from the sealed public trace`,
    );
  }
}

interface AppShell {
  readonly root: HTMLElement;
  readonly modePill: HTMLElement;
  readonly connPill: HTMLElement;
  readonly phasePill: HTMLElement;
  readonly integrityPill: HTMLElement;
  readonly clock: HTMLElement;
  readonly traceSelect: HTMLSelectElement;
  readonly loadTraceButton: HTMLButtonElement;
  readonly refreshTracesButton: HTMLButtonElement;
  readonly controlPanel: HTMLElement;
  readonly endpointInput: HTMLInputElement;
  readonly tokenInput: HTMLInputElement;
  readonly csrfInput: HTMLInputElement;
  readonly catalogButton: HTMLButtonElement;
  readonly disconnectButton: HTMLButtonElement;
  readonly catalogBody: HTMLElement;
  readonly startButton: HTMLButtonElement;
  readonly registeredReplayButton: HTMLButtonElement;
  readonly pauseButton: HTMLButtonElement;
  readonly resumeButton: HTMLButtonElement;
  readonly stepButton: HTMLButtonElement;
  readonly stopButton: HTMLButtonElement;
  readonly sessionStatus: HTMLElement;
  readonly left: HTMLElement;
  readonly entityTree: HTMLElement;
  readonly layerTree: HTMLElement;
  readonly weatherList: HTMLElement;
  readonly map: HTMLElement;
  readonly mapMessage: HTMLElement;
  readonly followChip: HTMLButtonElement;
  readonly hudNote: HTMLElement;
  readonly mapStats: HTMLElement;
  readonly langZh: HTMLButtonElement;
  readonly langEn: HTMLButtonElement;
  readonly right: HTMLElement;
  readonly inspector: HTMLElement;
  readonly tabBar: HTMLElement;
  readonly tabPanels: HTMLElement;
  readonly eventList: HTMLElement;
  readonly transport: HTMLElement;
  readonly scrubber: HTMLInputElement;
  readonly scrubberMarks: HTMLElement;
  readonly scrubberLabel: HTMLElement;
  readonly metricsGrid: HTMLElement;
  readonly terminalContainer: HTMLElement;
  readonly contextMenu: HTMLElement;
  readonly sourceMessage: HTMLElement;
  readonly telemetryHudContainer: HTMLElement;
  /** P02 map-first view switch (运行/配置). Absent nodes are allowed: tests mount partial shells. */
  readonly p02ViewSwitch: HTMLElement | null;
  /** P02 运行 business dock floating over the map (orders + selected-cargo inspector). */
  readonly p02RunDock: HTMLElement | null;
  /** P02 配置 panel body; lives in the left sidebar below the existing panels. */
  readonly p02ConfigBody: HTMLElement | null;
}

export class PublicTraceApp {
  private readonly mode: AppMode;
  private readonly shell: AppShell;
  private readonly store = new PublicTraceStore();
  private readonly loadingProgress = new LoadingProgressView();
  private replayEvents = new ReplayEvents([]);
  private readonly selection = new SelectionState();
  private readonly hover = new HoverState();
  private readonly contextMenu = new ContextMenuState();
  private readonly layers = new LayerState();
  private readonly camera = new CameraState();
  private readonly replay = new ReplayState();
  private readonly map: PublicTraceMap;
  private readonly telemetryHud: TelemetryHud;
  /** Keeps a visible toast out of the HUD's rectangle (plus gap) as the HUD moves or resizes. */
  private readonly hudLayoutObserver: ResizeObserver | null = typeof ResizeObserver === "undefined"
    ? null : new ResizeObserver(() => this.placeSourceMessage());
  private readonly agentConsole: AgentConsole;
  private readonly interactionTimeline: InteractionTimeline;
  private readonly telemetryPanel: TelemetryPanel;
  private readonly terminalDock: TerminalDock;
  private trace: Readonly<PublicTrace> | null = null;
  private mapAvailable = false;
  private tabButtons = new Map<InfoTab, HTMLButtonElement>();
  private activeTab: InfoTab = "overview";
  private unsubscribeLanguage: () => void;
  private playButton: HTMLButtonElement | null = null;
  private session: RunSession | null = null;
  private bootstrap: BootstrapMemory | null = null;
  private controlClient: ControlClient | null = null;
  private replayAccessAbort: AbortController | null = null;
  private assetResolver: AssetResolver | null = null;
  private osmScene: OsmJson | null = null;
  private meshScene: LoadedMeshPack | null = null;
  private nativePresentation: LoadedNativeCityPresentation | null = null;
  private nativePresentationAbort: AbortController | null = null;
  private liveMeshResolver: AssetResolver | null = null;
  private liveAssetScenario = "";
  private modelUrls: ReadonlyMap<string, string> = new Map();
  private assetResolutions: readonly AssetResolution[] = [];
  private replayAssetBase: string | null = null;
  /** Authenticated fetch for a Control run's sealed files; null for same-origin endpoints. */
  private replayAssetFetch: typeof fetch | null = null;
  private sessionUnsubscribe: (() => void) | null = null;
  private assetResolveGeneration = 0;
  private workspaceCatalog: WorkspaceTraceCatalog | null = null;
  private catalogRequest: AbortController | null = null;
  private sealedReplayLoader: SealedReplayLoader | null = null;
  private sealedReplayRequired = false;
  private sealedReplayStatus: SealedReplayStatus = "idle";
  private endpointLoadGeneration = 0;
  private endpointAbortController: AbortController | null = null;
  private replacementApp: PublicTraceApp | null = null;
  private disposed = false;

  // ------------------------------------------------------------------
  // P02 Run/Configuration views. Compiled once per trace load; every
  // per-tick render is a Map join over these tables (no rescans).
  // ------------------------------------------------------------------
  private p02RunIndex: RunIndex | null = null;
  private p02View: "run" | "config" = "run";
  private p02SelectedOrderId: string | null = null;
  /** Business-identity sidecar fetch generation; only the latest binds. */
  private p02IdentityGeneration = 0;
  private p02IdentityAbort: AbortController | null = null;
  /** Trace whose business overview the map has already framed (one-shot). */
  private p02OverviewFramed: PublicTrace | null = null;

  private readonly keydown = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      if (this.contextMenu.get() !== null) {
        this.contextMenu.close();
        return;
      }
      if (this.selection.get() !== null) {
        this.selection.clear();
        return;
      }
      if (this.camera.followed() !== null) {
        this.camera.releaseFollow();
      }
      return;
    }
    if (!event.ctrlKey && !event.metaKey && !event.altKey && !isTextEntryTarget(event.target)) {
      if (event.key === " ") {
        event.preventDefault();
        if (this.mode === "replay" && !this.sealedReplayReady()) return;
        this.replay.togglePlay();
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        if (this.mode === "replay" && !this.sealedReplayReady()) return;
        this.replay.step(-1);
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        if (this.mode === "replay" && !this.sealedReplayReady()) return;
        this.replay.step(1);
        return;
      }
      if (event.key === "f" && this.mapAvailable) {
        const selected = this.selection.get();
        if (selected !== null && selected.kind === "entity") {
          if (this.camera.isFollowing(selected)) {
            this.camera.releaseFollow();
          } else {
            this.camera.follow(selected);
          }
        }
      }
    }
  };
  private readonly pointerDown = (event: PointerEvent) => {
    const menu = this.contextMenu.get();
    if (menu === null || !(event.target instanceof Node)) {
      return;
    }
    if (!this.shell.contextMenu.contains(event.target)) {
      this.contextMenu.close();
    }
  };

  constructor(root: HTMLElement, mode: AppMode) {
    initLanguage();
    this.mode = mode;
    this.shell = buildShell(root, mode);
    this.shell.map.append(this.loadingProgress.root);
    for (const button of this.shell.tabBar.querySelectorAll<HTMLButtonElement>(".tab-button")) {
      const tab = button.dataset.tab as InfoTab | undefined;
      if (tab !== undefined) {
        this.tabButtons.set(tab, button);
        button.addEventListener("click", () => {
          this.activeTab = tab;
          this.renderRightRail();
        });
      }
    }
    this.map = new PublicTraceMap(this.shell.map.querySelector("#city-map") as HTMLElement, {
      onObservationModeChange: mode => this.telemetryHud?.setCameraModeSelect(mode),
      onPick: (target) => {
        this.contextMenu.close();
        if (target === null) {
          this.selection.clear();
        } else {
          this.selection.select(target);
        }
      },
      onHover: (target) => {
        if (target === null) {
          this.hover.clear();
        } else {
          this.hover.hover(target);
        }
      },
      onContextMenu: (target, x, y) => {
        this.contextMenu.open(x, y, target);
      },
      onUnavailable: () => this.handleMapUnavailable(),
      onBasemapNote: (note) => this.showBasemapNote(note),
      onSceneStatus: (digest, status, detail) => {
        // The selected city asset can finish after the trace's first render.
        // Fit only after its real coordinate authority and GLBs are ready.
        if (status === "ready" && this.mode === "replay") {
          this.p02OverviewFramed = null;
          this.p02FrameOverview();
        }
        if (this.mode !== "replay" || digest !== this.trace?.scenario_digest) return;
        this.loadingProgress.update({ stage: status, detail });
        if (status === "failed") this.replay.pause();
      },
    });
    this.mapAvailable = this.map.isAvailable;
    // Test doubles and read-only map adapters may omit camera controls.
    this.map.setCameraMode?.("free");

    this.telemetryHud = new TelemetryHud({
      onCameraModeChange: (mode) => {
        this.map.setCameraMode(mode);
        this.telemetryHud.setCameraModeSelect(this.map.getCameraMode());
        this.renderMap();
      },
      onUavSelect: (entityId) => {
        const target = { kind: "entity", id: entityId } as const;
        this.selection.select(target);
        this.map.setFollow(target);
        this.renderMap();
      },
      onLayoutChange: () => {
        this.placeSourceMessage();
      },
    });
    this.telemetryHud.setCameraModeSelect("free");
    this.shell.telemetryHudContainer.append(this.telemetryHud.root);

    this.agentConsole = new AgentConsole(element("div"));
    this.interactionTimeline = new InteractionTimeline(element("div"));
    this.telemetryPanel = new TelemetryPanel(element("div"));
    this.terminalDock = new TerminalDock(this.shell.terminalContainer);

    this.shell.langZh.addEventListener("click", () => setLanguage("zh"));
    this.shell.langEn.addEventListener("click", () => setLanguage("en"));
    this.shell.followChip.addEventListener("click", () => this.camera.releaseFollow());
    // Configuration opens the actual editable Studio, rather than hiding Run
    // business panels over the same replay canvas.
    this.shell.p02ViewSwitch?.querySelectorAll<HTMLButtonElement>(".p02-view-button").forEach(button => {
      button.addEventListener("click", () => {
        const requested = button.dataset.p02View === "config" ? "config" : "run";
        if (requested === "config") {
          window.open(studioConfigurationUrl(new URL(window.location.href)).href, "_self");
          return;
        }
        if (requested !== this.p02View) {
          this.p02View = requested;
          this.applyP02View();
        }
      });
    });
    // The switch is rendered from static text; bind the accessible name and
    // the data view in one pass so the buttons are keyboard/screen-reader
    // usable before any interaction.
    if (this.shell.p02ViewSwitch !== null) {
      for (const button of this.shell.p02ViewSwitch.querySelectorAll<HTMLButtonElement>(".p02-view-button")) {
        button.type = "button";
        const requested = button === this.shell.p02ViewSwitch.querySelector(".p02-view-button:last-child") ? "config" : "run";
        button.dataset.p02View = requested;
        if (button.getAttribute("aria-label") === null) {
          button.setAttribute("aria-label", t(requested === "config" ? "p02.switchConfig" : "p02.switchRun"));
        }
        button.setAttribute("aria-controls", "p02-run-dock, p02-config-section");
      }
    }
    this.unsubscribeLanguage = subscribeLanguage(() => this.applyLanguageChange());
    this.applyLanguageChange();

    this.store.subscribe((trace) => {
      this.trace = trace;
      this.replayEvents = new ReplayEvents(trace.events);
      this.sealedReplayRequired = trace.scenario.replay_mode === "indexed";
      if (!this.sealedReplayRequired) this.sealedReplayStatus = "ready";
      this.replay.setTrace(trace);
      // P02: compile the Run index once per trace. Per-tick renders join
      // these tables by key; the trace documents are never rescanned.
      this.p02RunIndex = buildRunIndex({ trace, entityKinds: entityKindsOf(trace.scenario) });
      this.p02SelectedOrderId = null;
      this.applyDeclaredLayerDefaults(trace.scenario);
      this.renderAll();
      this.loadP02BusinessIdentities(trace);
      if (this.mode === "replay" && this.replayAssetBase !== null) {
        void this.resolveDeclaredAssets(trace, this.replayAssetBase);
      }
    });
    this.store.onError((error) => this.renderSourceError(error));
    this.layers.subscribe(() => {
      this.syncLayerTree();
      this.renderMap();
    });
    this.selection.subscribe((selected) => {
      if (selected !== null) {
        const tab = this.tabForTarget(selected);
        if (tab !== this.activeTab) {
          this.activeTab = tab;
        }
      }
      this.syncEntityTreeSelection();
      this.renderInspector();
      this.renderRightRail();
      this.renderMap();
      this.renderP02RunDock();
      this.telemetryPanel.update(this.telemetrySource());
      this.updateTelemetryHud();
    });
    this.hover.subscribe(() => this.renderMap());
    this.contextMenu.subscribe((menu) => this.renderContextMenu(menu));
    this.camera.subscribeFocus((target) => {
      if (target !== null && this.mapAvailable) {
        const scene = this.currentMapScene();
        if (scene !== null && !this.map.focus(target, scene)) {
          this.showSourceNote(`${target.id} · ${t("source.noPosition")}`);
        }
      }
    });
    this.camera.subscribeFollow((target) => {
      this.map.setFollow(target);
      this.renderInspector();
      this.renderFollowChip(target);
    });
    this.replay.subscribe(() => {
      this.updateReplayInteractions();
      this.syncTimelineCursor();
      this.renderEntityTree();
      this.renderEventFeed();
      this.renderInspector();
      this.renderRightRail();
      this.renderMapMessage();
      this.renderMetrics();
      this.renderMap();
      this.renderP02RunDock();
      // Parcel labels follow the same replay cursor as the dock rows —
      // rebuild them whenever the cursor moves.
      this.renderP02MapOverlay();
      this.renderP02Config();
      this.telemetryPanel.update(this.telemetrySource());
      this.updateTelemetryHud();
    });

    this.shell.scrubber.addEventListener("input", () => {
      if (this.mode === "replay" && !this.sealedReplayReady()) return;
      this.replay.seek(Number(this.shell.scrubber.value));
    });
    if (this.mode === "replay") {
      this.shell.loadTraceButton.addEventListener("click", () => {
        void this.loadSelectedWorkspaceTrace();
      });
      this.shell.refreshTracesButton.addEventListener("click", () => {
        void this.refreshWorkspaceTraces();
      });
      this.shell.traceSelect.addEventListener("change", () => {
        this.syncWorkspaceTraceButtons();
      });
    } else {
      this.bindLiveControls();
    }
    document.addEventListener("keydown", this.keydown);
    document.addEventListener("pointerdown", this.pointerDown);
    // A visible toast must follow the HUD box: content-box changes (dragged
    // left/top assignments, minimize width swaps) fire here, drag end and
    // visibility flips arrive through the HUD's own layout notification.
    this.hudLayoutObserver?.observe(this.telemetryHud.root);
    this.renderAll();
    // P02 initial state: apply the view now (not only on switch) so the very
    // first frame is map-first without a body scrollbar, and the view
    // buttons carry their real pressed state.
    this.applyP02View();
  }

  /** Accept one caller-provided public-trace/v3 document (validated at the boundary). */
  async loadDocument(value: unknown): Promise<void> {
    this.endpointLoadGeneration += 1;
    this.endpointAbortController?.abort();
    this.endpointAbortController = null;
    this.clearReplaySource();
    // An entity id in another source names a different object; a new source starts unselected.
    this.selection.clear();
    this.clearSourceMessage();
    try {
      const trace = parsePublicTrace(value);
      if (trace.scenario.replay_mode === "indexed") {
        throw new SealedReplayError("indexed replay must be loaded from a sealed replay endpoint");
      }
      this.store.set(trace);
    } catch (error) {
      this.clearReplaySource();
      this.sealedReplayRequired = true;
      this.sealedReplayStatus = "failed";
      this.renderAll();
      this.renderSourceError(error);
      throw error;
    }
  }

  /** Load one public trace from a same-origin relative endpoint (sealed replay). */
  async loadEndpoint(selector: string, signal?: AbortSignal): Promise<void> {
    await this.loadSealedReplay({
      traceUrl: new URL(selector, window.location.href),
      fetchTrace: (requestSignal, onProgress) => this.store.fetchRelative(selector, requestSignal, onProgress),
    }, signal);
  }

  /**
   * Load the sealed replay of a finished Control API run over its authenticated routes. The
   * sealed replay manifest must bind the exact trace bytes before the trace is accepted.
   */
  async loadControlReplay(source: ControlReplaySource, signal?: AbortSignal): Promise<void> {
    const baseHref = controlReplayBaseHref(source.runId, window.location.origin);
    const fetchImpl = controlReplayFetch(source, baseHref);
    await this.loadSealedReplay({
      traceUrl: new URL("public-trace.json", baseHref),
      fetch: fetchImpl,
      fetchTrace: (requestSignal, onProgress) =>
        this.store.fetchFrom(traceSignal => source.trace(traceSignal), requestSignal, onProgress),
      verifyTrace: async (candidate, requestSignal) => {
        const response = await source.manifest(requestSignal);
        const bytes = await readBoundedResponse(response, CONTROL_REPLAY_MANIFEST_MAX_BYTES, "replay manifest", requestSignal);
        const manifest = parseStrictJson(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
        assertPublicReplayManifest(manifest);
        const traceFile = manifest.files.find(file => file.relative_path === "public-trace.json");
        if (manifest.run_id !== source.runId || candidate.trace.run_id !== source.runId
          || manifest.scenario_digest !== candidate.trace.scenario_digest
          || manifest.event_chain_root !== candidate.trace.event_chain_root
          || manifest.trace_sha256 !== candidate.sourcePayloadSha256 || traceFile === undefined
          || traceFile.sha256 !== candidate.sourcePayloadSha256
          || traceFile.size_bytes !== candidate.sourcePayloadSizeBytes) {
          throw new SealedReplayError("sealed replay manifest does not bind the run's public trace bytes");
        }
      },
    }, signal);
  }

  private async loadSealedReplay(source: SealedReplaySourceRequest, signal?: AbortSignal): Promise<void> {
    const generation = ++this.endpointLoadGeneration;
    this.endpointAbortController?.abort();
    const controller = new AbortController();
    this.endpointAbortController = controller;
    const abortFromCaller = () => controller.abort();
    signal?.addEventListener("abort", abortFromCaller, { once: true });
    if (signal?.aborted) controller.abort();
    const isCurrent = () => generation === this.endpointLoadGeneration;
    const assertCurrent = () => {
      if (!isCurrent() || controller.signal.aborted) {
        throw new DOMException("superseded endpoint load", "AbortError");
      }
    };
    this.clearReplaySource();
    this.selection.clear();
    this.clearSourceMessage();
    this.sealedReplayRequired = true;
    this.sealedReplayStatus = "loading";
    this.loadingProgress.update({ stage: "request" });
    this.renderAll();
    let pendingLoader: SealedReplayLoader | null = null;
    try {
      const traceUrl = source.traceUrl;
      this.replayAssetBase = new URL(".", traceUrl).href;
      this.replayAssetFetch = source.fetch ?? null;
      const candidate = await source.fetchTrace(controller.signal, progress => {
        if (isCurrent() && !controller.signal.aborted) this.loadingProgress.update(progress);
      });
      assertCurrent();
      await source.verifyTrace?.(candidate, controller.signal);
      assertCurrent();
      const trace = candidate.trace;
      this.sealedReplayRequired = trace.scenario.replay_mode === "indexed";
      if (this.sealedReplayRequired) {
        const manifestUrl = new URL("./replay/replay-manifest.json", traceUrl);
        this.replayAssetBase = new URL(".", manifestUrl).href;
        const manifestSelector = `${manifestUrl.pathname}${manifestUrl.search}${manifestUrl.hash}`;
        const historyArtifact = trace.runtime_artifacts.find(
          artifact => artifact.artifact_id === trace.scene_state_history_artifact.artifact_id,
        );
        if (historyArtifact === undefined || historyArtifact.artifact_type !== "scene.state-history"
          || historyArtifact.sha256 !== trace.scene_state_history_artifact.digest
          || historyArtifact.selector !== trace.scene_state_history_artifact.selector
          || historyArtifact.replay_path !== `artifacts/${historyArtifact.sha256}`) {
          throw new SealedReplayError("public trace does not declare its sealed SceneState history artifact");
        }
        this.loadingProgress.update({ stage: "index" });
        pendingLoader = await SealedReplayLoader.open(
          manifestSelector,
          {
            ...(source.fetch === undefined ? {} : { fetch: source.fetch }),
            expectedTraceSha256: candidate.sourcePayloadSha256,
            expectedTraceSizeBytes: candidate.sourcePayloadSizeBytes,
            expectedSceneStateHistorySha256: trace.scene_state_history_artifact.digest,
            expectedSceneStateHistorySizeBytes: historyArtifact.size_bytes,
            expectedSceneStates: trace.scene_states,
            onProgress: (completed, total) => {
              if (isCurrent() && !controller.signal.aborted) this.loadingProgress.update({ stage: "index", completed, total });
            },
          },
          controller.signal,
        );
        assertCurrent();
        if (
          pendingLoader.manifest.run_id !== trace.run_id
          || pendingLoader.manifest.scenario_digest !== trace.scenario_digest
          || pendingLoader.manifest.event_chain_root !== trace.event_chain_root
          || pendingLoader.index.scene_state_count !== trace.scene_states.length
          || pendingLoader.index.last_tick !== trace.time.tick
        ) {
          throw new SealedReplayError("sealed replay index does not close over the public trace");
        }
      }
      assertCurrent();
      if (pendingLoader !== null) {
        this.sealedReplayLoader = pendingLoader;
      }
      this.sealedReplayStatus = "ready";
      this.loadingProgress.update({ stage: "scene" });
      this.store.acceptFetched(candidate);
      assertCurrent();
      pendingLoader = null;
      this.renderAll();
    } catch (error) {
      pendingLoader?.dispose();
      if (isCurrent()) {
        this.clearReplaySource();
        this.sealedReplayRequired = true;
        this.sealedReplayStatus = "failed";
        this.renderAll();
        this.renderSourceError(error);
        this.loadingProgress.update({ stage: "failed", detail: error instanceof Error ? error.message : String(error) });
      }
      throw error;
    } finally {
      signal?.removeEventListener("abort", abortFromCaller);
      if (isCurrent()) this.endpointAbortController = null;
    }
  }

  /** Build the shell, then in replay mode list worktree public traces. */
  async start(): Promise<void> {
    this.renderAll();
    if (this.mode === "replay") {
      await this.refreshWorkspaceTraces();
    }
  }

  private async refreshWorkspaceTraces(): Promise<void> {
    this.catalogRequest?.abort();
    const request = new AbortController();
    this.catalogRequest = request;
    const lang = currentLanguage();
    this.shell.traceSelect.disabled = true;
    this.shell.loadTraceButton.disabled = true;
    try {
      const catalog = await fetchWorkspaceTraceCatalog(request.signal);
      if (request.signal.aborted || this.catalogRequest !== request) return;
      this.workspaceCatalog = catalog;
      this.populateWorkspaceTraceSelect(catalog);
      const loadable = loadableWorkspaceTraces(catalog);
      const requestedPath = new URLSearchParams(window.location.search).get("trace");
      const requested = requestedPath === null ? undefined : loadable.find(entry => entry.relative_path === requestedPath);
      // A ?trace= request that matches no loadable entry is a missing input: surface it and
      // never auto-load a different trace in its place.
      const initial = requestedPath !== null && requested === undefined
        ? undefined
        : (requested ?? (loadable.length === 1 ? loadable[0] : undefined));
      if (initial !== undefined) {
        this.shell.traceSelect.value = initial.url;
        this.syncWorkspaceTraceButtons();
        await this.loadSelectedWorkspaceTrace();
      } else {
        this.syncWorkspaceTraceButtons();
        if (requestedPath !== null && requested === undefined) {
          this.showSourceNote(tf("workspaceTraces.requestedMissing", { path: requestedPath }, lang));
        } else if (catalog.traces.length === 0) {
          this.showSourceNote(t("workspaceTraces.empty", lang));
        }
      }
    } catch (error) {
      if (request.signal.aborted || this.catalogRequest !== request) return;
      this.workspaceCatalog = null;
      this.populateWorkspaceTraceSelect(null);
      this.renderSourceError(error instanceof Error ? error : new Error(t("workspaceTraces.scanFailed", lang)));
    }
  }

  private populateWorkspaceTraceSelect(catalog: WorkspaceTraceCatalog | null): void {
    const lang = currentLanguage();
    const select = this.shell.traceSelect;
    const previous = select.value;
    select.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = t("workspaceTraces.placeholder", lang);
    select.append(placeholder);
    const entries = catalog === null ? [] : loadableWorkspaceTraces(catalog);
    if (entries.length === 0) {
      select.disabled = true;
      return;
    }
    select.disabled = false;
    for (const entry of entries) {
      const option = document.createElement("option");
      option.value = entry.url;
      option.textContent = workspaceTraceLabel(entry, lang);
      select.append(option);
    }
    if (previous !== "" && entries.some((item) => item.url === previous)) {
      select.value = previous;
    }
  }

  private syncWorkspaceTraceButtons(): void {
    const url = this.shell.traceSelect.value;
    const entry = this.workspaceCatalog?.traces.find((item) => item.url === url);
    this.shell.loadTraceButton.disabled = entry === undefined || !entry.loadable;
    this.shell.refreshTracesButton.disabled = false;
    this.shell.traceSelect.disabled = this.workspaceCatalog === null;
  }

  private async loadSelectedWorkspaceTrace(): Promise<void> {
    const url = this.shell.traceSelect.value;
    const entry = this.workspaceCatalog?.traces.find((item) => item.url === url);
    if (entry === undefined || !entry.loadable) {
      return;
    }
    try {
      await this.loadEndpoint(entry.url);
    } catch {
      // loadEndpoint already reports the current source error.
    }
  }

  dispose(): void {
    this.replacementApp?.dispose();
    this.replacementApp = null;
    if (this.disposed) return;
    this.disposed = true;
    this.catalogRequest?.abort();
    this.catalogRequest = null;
    this.endpointLoadGeneration += 1;
    this.endpointAbortController?.abort();
    this.endpointAbortController = null;
    document.removeEventListener("keydown", this.keydown);
    document.removeEventListener("pointerdown", this.pointerDown);
    this.unsubscribeLanguage();
    this.sessionUnsubscribe?.();
    this.session?.dispose();
    this.session = null;
    this.replayAccessAbort?.abort(); this.replayAccessAbort = null;
    this.controlClient = null;
    this.liveAssetScenario = "";
    this.osmScene = null;
    this.bootstrap = null;
    this.assetResolveGeneration += 1;
    this.assetResolver?.dispose();
    this.assetResolver = null;
    this.nativePresentationAbort?.abort(); this.nativePresentationAbort = null;
    this.nativePresentation?.dispose(); this.nativePresentation = null;
    this.meshScene?.dispose(); this.meshScene = null;
    this.liveMeshResolver?.dispose(); this.liveMeshResolver = null;
    this.replayAssetFetch = null;
    this.p02IdentityAbort?.abort();
    this.p02IdentityAbort = null;
    this.p02IdentityGeneration += 1;
    this.sealedReplayLoader?.dispose();
    this.sealedReplayLoader = null;
    this.sealedReplayRequired = false;
    this.sealedReplayStatus = "idle";
    this.replay.dispose();
    this.store.dispose();
    this.agentConsole.dispose();
    this.interactionTimeline.dispose();
    this.telemetryPanel.dispose();
    this.terminalDock.dispose();
    this.hudLayoutObserver?.disconnect();
    this.telemetryHud.dispose();
    this.map.destroy();
  }

  /** Replace the terminal live console with a read-only sealed replay; credentials stay in memory. */
  async replayFinishedRun(): Promise<void> {
    if (this.mode !== "live" || this.session === null) {
      throw new Error("a terminal live run is required for Control replay");
    }
    const source = this.session.sealedReplaySource();
    await this.replaceWithControlReplay(source);
  }

  /** Open a catalog's sealed run through replay-access, without POST /v1/runs or SSE. */
  async replayRegisteredRun(): Promise<void> {
    const client = this.controlClient, bootstrap = this.bootstrap, session = this.session;
    const runId = session?.currentState.selectedRunId;
    if (this.mode !== "live" || client === null || bootstrap === null || session === null
        || runId === null || runId === undefined || session.currentState.hasRunCredentials) {
      throw new ControlProtocolError("select a catalog run with bootstrap credentials for read-only replay");
    }
    this.replayAccessAbort?.abort();
    const controller = new AbortController(); this.replayAccessAbort = controller;
    this.renderRunControls(session.currentState);
    try {
      const source = await requestRegisteredReplaySource(client, bootstrap, runId, controller.signal);
      if (this.disposed || controller.signal.aborted || this.controlClient !== client
          || this.bootstrap !== bootstrap || this.session !== session
          || session.currentState.selectedRunId !== runId || session.currentState.hasRunCredentials) {
        throw new DOMException("superseded replay access", "AbortError");
      }
      this.replayAccessAbort = null;
      await this.replaceWithControlReplay(source);
    } finally {
      if (this.replayAccessAbort === controller) {
        this.replayAccessAbort = null;
        if (!this.disposed && this.session !== null) this.renderRunControls(this.session.currentState);
      }
    }
  }

  private async replaceWithControlReplay(source: ControlReplaySource): Promise<void> {
    this.dispose();
    const replacement = new PublicTraceApp(this.shell.root, "replay");
    this.replacementApp = replacement;
    await replacement.loadControlReplay(source);
  }

  // ------------------------------------------------------------------
  // Live control wiring
  // ------------------------------------------------------------------

  private bindLiveControls(): void {
    this.shell.catalogButton.addEventListener("click", () => {
      void this.connectControlService();
    });
    this.shell.disconnectButton.addEventListener("click", () => this.disconnectControlService());
    this.shell.startButton.addEventListener("click", () => {
      void this.startSelectedRun();
    });
    this.shell.registeredReplayButton.addEventListener("click", () => {
      void this.replayRegisteredRun().catch(error => {
        if (!this.disposed && !(error instanceof DOMException && error.name === "AbortError")) {
          this.renderSessionError(error instanceof Error ? error.message : String(error));
        }
      });
    });
    for (const [button, operation] of [
      [this.shell.pauseButton, "pause"],
      [this.shell.resumeButton, "resume"],
      [this.shell.stepButton, "step"],
      [this.shell.stopButton, "stop"],
    ] as const) {
      button.addEventListener("click", () => {
        void this.session?.control(operation);
      });
    }
  }

  private async connectControlService(): Promise<void> {
    const endpoint = this.shell.endpointInput.value.trim();
    const operatorToken = this.shell.tokenInput.value.trim();
    const csrfToken = this.shell.csrfInput.value.trim();
    // Credentials are consumed immediately; the password fields are cleared.
    this.shell.tokenInput.value = "";
    this.shell.csrfInput.value = "";
    if (endpoint === "" || operatorToken === "" || csrfToken === "") {
      this.renderSessionError(t("control.credentialNote"));
      return;
    }
    let client: ControlClient;
    let startIdentities: RunStartIdentityStore;
    try {
      client = new ControlClient({ baseUrl: endpoint });
      const compilationId = new URLSearchParams(window.location.search).get("compilation");
      startIdentities = new RunStartIdentityStore(window.localStorage, client.serviceOrigin, compilationId);
    } catch (error) {
      this.renderSessionError(error instanceof Error ? error.message : String(error));
      return;
    }
    this.bootstrap = { operatorToken, csrfToken };
    this.replayAccessAbort?.abort(); this.replayAccessAbort = null;
    this.controlClient = client;
    this.sessionUnsubscribe?.();
    this.session?.dispose();
    this.liveAssetScenario = "";
    this.osmScene = null;
    this.session = new RunSession(client, { startIdentities });
    this.sessionUnsubscribe = this.session.subscribe((state) => this.renderSession(state));
    await this.session.loadCatalog(operatorToken);
    const requestedRun = new URLSearchParams(window.location.search).get("run");
    if (requestedRun !== null) {
      try {
        this.session.selectRun(requestedRun);
      } catch (error) {
        this.renderSessionError(error instanceof Error ? error.message : String(error));
      }
    }
  }

  private disconnectControlService(): void {
    this.replayAccessAbort?.abort(); this.replayAccessAbort = null;
    this.controlClient = null;
    this.sessionUnsubscribe?.();
    this.sessionUnsubscribe = null;
    this.session?.dispose();
    this.session = null;
    this.liveAssetScenario = "";
    this.osmScene = null;
    this.bootstrap = null;
    // The disconnected service's catalog and run controls must not remain actionable.
    this.shell.catalogBody.replaceChildren();
    for (const button of [this.shell.startButton, this.shell.registeredReplayButton, this.shell.pauseButton,
      this.shell.resumeButton, this.shell.stepButton, this.shell.stopButton]) button.disabled = true;
    this.renderSessionError("");
    this.renderAll();
  }

  private async startSelectedRun(): Promise<void> {
    if (this.session === null || this.bootstrap === null) {
      this.renderSessionError(t("control.startRequired"));
      return;
    }
    await this.session.start(this.bootstrap);
    // Per-run credentials now live in the session; drop the bootstrap pair.
    this.bootstrap = null;
  }

  private renderSessionError(message: string): void {
    this.shell.sessionStatus.replaceChildren();
    if (message !== "") {
      this.shell.sessionStatus.append(element("div", "control-error", message));
    }
  }

  private renderSession(state: RunSessionState): void {
    const lang = currentLanguage();
    this.shell.connPill.textContent = t(`conn.${state.connection}` as I18nKey, lang);
    this.shell.connPill.className = `pill tone-${
      state.connection === "connected" || state.connection === "closed-terminal"
        ? "ok"
        : state.connection === "error"
          ? "bad"
          : "muted"
    }`;
    // The mode pill already reads "实时 · 未连接"; an idle connection pill would repeat it.
    this.shell.connPill.hidden = state.connection === "idle";
    this.renderCatalog(state);
    this.renderRunControls(state);
    if (state.snapshot !== null) {
      this.shell.phasePill.textContent = t(livePhaseKey(state.snapshot.phase), lang);
      this.shell.phasePill.className = `pill tone-${statusTone(state.snapshot.phase)}`;
      this.shell.phasePill.title = state.snapshot.phase;
      this.shell.integrityPill.textContent = t("integrity.streaming", lang);
      this.shell.integrityPill.className = "pill tone-warn";
    }
    // Phase and integrity pills appear only once a snapshot gives them content.
    this.shell.phasePill.hidden = state.snapshot === null;
    this.shell.integrityPill.hidden = state.snapshot === null;
    this.renderEntityTree();
    this.renderEventFeed();
    this.renderMetrics();
    this.agentConsole.update({ events: state.events });
    this.interactionTimeline.update({ events: state.events });
    this.terminalDock.update({ events: state.events, transitions: state.transitions, verifierReport: null });
    this.telemetryPanel.update(this.telemetrySource());
    if (state.scenario !== null) {
      if (this.liveAssetScenario !== state.scenario.scenario_digest) {
        this.liveAssetScenario = state.scenario.scenario_digest;
        this.applyDeclaredLayerDefaults(state.scenario);
        this.renderLayerTree();
        void this.resolveLiveScene(state.scenario);
      }
    }
    if (this.activeTab === "overview") {
      this.renderOverviewTab(this.shell.tabPanels.querySelector<HTMLElement>('[data-tab="overview"]'));
    }
    if (state.sceneStates.length > 0) {
      this.replay.setSceneStates(state.sceneStates);
      if (this.replay.isPlaying()) {
        this.replay.last();
      }
    }
    this.renderTimeline();
    this.renderMap();
    this.updateTelemetryHud();
    this.renderMapMessage();
  }

  private renderCatalog(state: RunSessionState): void {
    const lang = currentLanguage();
    this.shell.catalogBody.replaceChildren();
    if (state.catalog === null) {
      this.shell.catalogBody.append(emptyRow(state.catalogError ?? t("catalog.empty", lang)));
      return;
    }
    const catalog = state.catalog;
    this.shell.catalogBody.append(
      keyValue(t("catalog.suite", lang), `${catalog.suite_id} · ${shortDigest(catalog.suite_sha256)}`),
    );
    const select = document.createElement("select");
    select.className = "run-select";
    select.setAttribute("aria-label", t("control.selectedRun", lang));
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = t("control.noRunSelected", lang);
    select.append(placeholder);
    for (const run of catalog.runs) {
      const option = document.createElement("option");
      option.value = run.run_id;
      option.textContent = `${run.case_id} · ${t("catalog.seed", lang)} ${run.seed} · ${run.launch_site_id} · ${shortDigest(run.run_id)}`;
      if (run.run_id === state.selectedRunId) {
        option.selected = true;
      }
      select.append(option);
    }
    select.addEventListener("change", () => {
      try {
        this.session?.selectRun(select.value === "" ? null : select.value);
      } catch {
        select.value = state.selectedRunId ?? "";
      }
    });
    this.shell.catalogBody.append(select);
  }

  private renderRunControls(state: RunSessionState): void {
    const hasRun = state.selectedRunId !== null;
    const started = state.hasRunCredentials;
    this.shell.startButton.disabled = !hasRun || started || state.pendingControl !== null || this.replayAccessAbort !== null;
    this.shell.registeredReplayButton.disabled = !hasRun || started || this.bootstrap === null
      || this.controlClient === null || this.replayAccessAbort !== null;
    this.shell.pauseButton.disabled = !started || state.pendingControl !== null;
    this.shell.resumeButton.disabled = !started || state.pendingControl !== null;
    this.shell.stepButton.disabled = !started || state.pendingControl !== null;
    this.shell.stopButton.disabled = !started || state.pendingControl !== null;
    const lang = currentLanguage();
    this.shell.sessionStatus.replaceChildren();
    if (state.sessionError !== null) {
      this.shell.sessionStatus.append(element("div", "control-error", state.sessionError));
      return;
    }
    const snapshot = state.snapshot;
    if (snapshot === null) {
      return;
    }
    const control = state.runtimeControl;
    this.shell.sessionStatus.append(
      statusValue(t("control.statusPhase", lang), t(livePhaseKey(snapshot.phase), lang), statusTone(snapshot.phase)),
    );
    if (control !== null) {
      this.shell.sessionStatus.append(
        statusValue(t("control.runtimePhase", lang), t(runtimePhaseKey(control.phase), lang), statusTone(control.phase)),
        keyValue(
          `${t("label.tick", lang)} / ${t("label.simTime", lang)}`,
          `${control.current.tick} · ${formatSimTime(control.current.sim_time_ns)}`,
        ),
        keyValue(t("control.stepBudget", lang), String(control.step_budget)),
        keyValue(t("control.tickInProgress", lang), displayValue(control.tick_in_progress)),
      );
    }
    if (state.pendingControl !== null) {
      this.shell.sessionStatus.append(
        element("div", "control-pending", `${t("control.controlPending", lang)} · ${state.pendingControl}`),
      );
    }
    if (this.session?.isTerminal && state.hasRunCredentials) {
      const replayButton = document.createElement("button");
      replayButton.type = "button";
      replayButton.className = "control-button";
      replayButton.dataset.role = "load-control-replay";
      replayButton.textContent = lang === "zh" ? "加载封存回放" : "Load sealed replay";
      replayButton.addEventListener("click", () => {
        void this.replayFinishedRun().catch(error => {
          // The replacement viewer reports its verification error without publishing a partial trace.
          if (this.replacementApp === null) this.renderSessionError(String(error));
        });
      });
      this.shell.sessionStatus.append(replayButton);
    }
  }

  // ------------------------------------------------------------------
  // Language and chrome
  // ------------------------------------------------------------------

  private applyLanguageChange(): void {
    this.loadingProgress.refresh();
    const lang = currentLanguage();
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    this.shell.registeredReplayButton.textContent = lang === "zh" ? "打开已封存回放" : "Open sealed replay";
    this.shell.langZh.classList.toggle("active", lang === "zh");
    this.shell.langEn.classList.toggle("active", lang === "en");
    for (const node of this.shell.root.querySelectorAll<HTMLElement>("[data-i18n]")) {
      const key = node.dataset.i18n as I18nKey | undefined;
      if (key !== undefined) {
        node.textContent = t(key, lang);
      }
    }
    this.shell.p02ViewSwitch?.setAttribute("aria-label", t("p02.viewSwitch", lang));
    for (const button of this.shell.p02ViewSwitch?.querySelectorAll<HTMLButtonElement>(".p02-view-button") ?? []) {
      const config = button.dataset.p02View === "config";
      button.textContent = t(config ? "p02.config" : "p02.run", lang);
      button.setAttribute("aria-label", t(config ? "p02.switchConfig" : "p02.switchRun", lang));
    }
    this.shell.p02RunDock?.setAttribute("aria-label", t("p02.dockAria", lang));
    const dockTitle = this.shell.p02RunDock?.querySelector(".p02-dock-title");
    if (dockTitle) dockTitle.textContent = t("p02.dockTitle", lang);
    this.shell.p02RunDock?.querySelector(".p02-orders-body")?.setAttribute("aria-label", t("p02.ordersAria", lang));
    this.shell.p02RunDock?.querySelector(".p02-cargo-body")?.setAttribute("aria-label", t("p02.selectedAria", lang));
    const configSection = this.shell.p02ConfigBody?.parentElement;
    configSection?.setAttribute("aria-label", t("p02.configAria", lang));
    const configTitle = configSection?.querySelector(".p02-section-title");
    if (configTitle) configTitle.textContent = t("p02.config", lang);
    this.shell.loadTraceButton.textContent = t("workspaceTraces.load", lang);
    this.shell.refreshTracesButton.textContent = t("workspaceTraces.refresh", lang);
    this.populateWorkspaceTraceSelect(this.workspaceCatalog);
    this.syncWorkspaceTraceButtons();
    for (const [tab, button] of this.tabButtons) {
      button.textContent = t(TAB_SECTION_KEYS[tab], lang);
    }
    this.renderLegendTexts();
    this.agentConsole.refresh();
    this.interactionTimeline.refresh();
    this.telemetryPanel.refresh();
    this.renderAll();
  }

  private renderLegendTexts(): void {
    const lang = currentLanguage();
    const legend = this.shell.map.querySelector(".map-legend");
    if (legend === null) {
      return;
    }
    const kinds = legend.querySelectorAll(".legend-item");
    const keys: readonly string[] = ["uav", "ugv", "pedestrian", "static_asset", "undeclared"];
    keys.forEach((key, index) => {
      const item = kinds[index];
      if (item !== undefined) {
        item.lastChild?.replaceWith(document.createTextNode(t(`kind.${key}` as I18nKey, lang)));
      }
    });
    const notes = legend.querySelectorAll(".legend-note");
    notes[0]?.replaceChildren(document.createTextNode(t("hud.legend.symbolNote", lang)));
    notes[1]?.replaceChildren(document.createTextNode(t("hud.legend.noInterp", lang)));
    notes[2]?.replaceChildren(document.createTextNode(t("hud.legend.buildings", lang)));
    legend.querySelector<HTMLElement>(".attribution-note")?.replaceChildren(document.createTextNode(t("hud.legend.attribution", lang)));
    const typeItems = legend.querySelectorAll<HTMLElement>(".legend-building-types .legend-item");
    const buildingTypes = ["residential", "commercial", "office", "industrial", "public", "logistics"] as const;
    buildingTypes.forEach((type, index) => {
      const item = typeItems[index];
      if (item !== undefined) item.lastChild?.replaceWith(document.createTextNode(t(`buildingType.${type}`, lang)));
    });
    const signalItem = legend.querySelector<HTMLElement>(".legend-item[data-legend='traffic-signal']");
    signalItem?.lastChild?.replaceWith(document.createTextNode(t("kind.traffic_signal", lang)));
  }

  private renderFollowChip(target: TraceTarget | null): void {
    const chip = this.shell.followChip;
    if (target === null) {
      chip.hidden = true;
      return;
    }
    const lang = currentLanguage();
    chip.replaceChildren();
    chip.append(
      element("strong", undefined, t("hud.following", lang)),
      textBlock(target.id),
      element("span", "hud-chip-hint", `Esc · ${t("hud.exitFollow", lang)}`),
    );
    chip.setAttribute("aria-label", `${t("hud.following", lang)} ${target.id} · ${t("hud.exitFollow", lang)}`);
    chip.hidden = false;
  }

  private showBasemapNote(note?: string): void {
    this.shell.hudNote.textContent = note ?? t("hud.basemapNote");
    this.shell.hudNote.hidden = false;
  }

  // ------------------------------------------------------------------
  // Data plumbing
  // ------------------------------------------------------------------

  /** The exact SceneState the map and panels display. */
  private currentSceneState() {
    if (this.mode === "replay") {
      const trace = this.trace;
      if (trace === null) {
        return null;
      }
      const tick = this.replay.current() ?? trace.time.tick;
      if (!this.sealedReplayReady()) return null;
      if (this.sealedReplayLoader === null) {
        return sceneStateAtTick(trace, tick);
      }
      return this.sealedSceneStateAtTick(tick);
    }
    const states = this.session?.currentState.sceneStates ?? [];
    if (states.length === 0) {
      return null;
    }
    const tick = this.replay.current();
    if (tick !== null) {
      const match = states.find((s) => s.at.tick === tick);
      if (match !== undefined) {
        return match;
      }
    }
    return states[states.length - 1]!;
  }

  private sealedSceneStateAtTick(tick: number): SceneState | null {
    if (!this.sealedReplayReady()) return null;
    const loader = this.sealedReplayLoader;
    if (loader === null || tick < 1 || tick > loader.index.last_tick) return null;
    try {
      // open() already checked every shard against the complete sealed history
      // and these exact frozen trace frames. Playback never waits on a re-fetch.
      const state = loader.verifiedSceneState(tick);
      if (this.trace === null) return null;
      assertIndexedSceneStateMatchesTrace(this.trace, state);
      return state;
    } catch (error) {
      this.clearReplaySource();
      this.sealedReplayRequired = true;
      this.sealedReplayStatus = "failed";
      this.renderAll();
      this.renderSourceError(error);
      this.loadingProgress.update({ stage: "failed", detail: error instanceof Error ? error.message : String(error) });
      return null;
    }
  }

  private sealedReplayReady(): boolean {
    return !this.sealedReplayRequired
      || (this.sealedReplayStatus === "ready" && this.sealedReplayLoader !== null);
  }

  private currentTick(): number | null {
    if (this.mode === "replay") {
      return this.replay.current() ?? this.trace?.time.tick ?? null;
    }
    return this.currentSceneState()?.at.tick ?? null;
  }

  private currentTrafficLightFrame(): PublicTrafficLightFrame | null {
    const tick = this.currentTick();
    if (this.mode === "replay") {
      if (!this.sealedReplayReady()) return null;
      const frames = this.trace?.traffic_light_frames ?? [];
      return tick === null ? frames.at(-1) ?? null : frames.find((frame) => frame.at.tick === tick) ?? null;
    }
    return this.session?.currentState.trafficLightFrame ?? null;
  }

  private currentMapScene(): MapScene | null {
    if (this.mode === "replay") {
      const trace = this.trace;
      if (trace === null) {
        return { scenario: null, sceneState: null, trajectories: [], networkFrame: null, tick: null };
      }
      const tick = this.replay.current() ?? trace.time.tick;
      const sceneState = this.currentSceneState();
      const replayReady = this.sealedReplayReady();
      return {
        operationContext: { sourceLabel: `记录回放 ${trace.run_id.slice(0, 8)} · 只读观察`, sourceKind: "replay", runId: trace.run_id, timeSeconds: (simTimeAtTick(trace, tick) ?? 0) / 1e9, clockState: this.replay.isPlaying() ? "playing" : "paused", events: this.eventsSource() },
        scenario: trace.scenario,
        osm: this.osmScene,
        pack: this.meshScene,
        nativePresentation: this.nativePresentation,
        sceneState,
        trajectories: replayReady ? trace.trajectories : [],
        networkFrame: replayReady ? trace.network_frames.find((frame) => frame.at.tick === tick) ?? null : null,
        trafficLightFrame: replayReady ? (trace.traffic_light_frames ?? []).find((frame) => frame.at.tick === tick) ?? null : null,
        tick,
        trafficLights: declaredTrafficLights(trace.scenario),
      };
    }
    const session = this.session?.currentState;
    const states = session?.sceneStates ?? [];
    return {
      operationContext: { sourceLabel: `Provider ${session?.snapshot?.run_id.slice(0, 8) ?? "未连接"} · 观察暂停不停止系统`, sourceKind: "live", runId: session?.snapshot?.run_id, timeSeconds: (this.currentSceneState()?.at.sim_time_ns ?? 0) / 1e9, connection: session?.connection, clockState: states.length === 0 ? "stopped" : this.replay.isPlaying() ? "playing" : "paused", events: this.eventsSource() },
      scenario: session?.scenario ?? null,
      osm: this.osmScene,
      pack: this.meshScene,
      nativePresentation: this.nativePresentation,
      sceneState: this.currentSceneState(),
      trajectories: trajectoriesFromSceneStates(states),
      networkFrame: networkFrameAtSceneState(session?.scenario ?? null, this.currentSceneState()),
      trafficLightFrame: session?.trafficLightFrame ?? null,
      trafficLights: session?.scenario === null || session?.scenario === undefined ? undefined : declaredTrafficLights(session.scenario),
    };
  }

  private eventsSource(): readonly PublicRunEvent[] {
    if (this.mode === "replay") {
      return this.trace?.events ?? [];
    }
    return this.session?.currentState.events ?? [];
  }

  private telemetrySource() {
    const trace = this.mode === "replay" ? this.trace : null;
    const tick = this.currentTick();
    const selected = this.selection.get();
    const sceneState = this.currentSceneState();
    const entityId = selected?.kind === "entity" ? selected.id : null;
    return {
      selectedEntityId: entityId,
      sample: entityId === null ? null : sampleAtTick(sceneState, entityId),
      sampleTick: tick,
      missionStatus:
        trace === null
          ? null
          : tick === null
            ? trace.mission_status
            : ([...trace.mission_status_history].filter((status) => status.at.tick <= tick).at(-1) ?? null),
      missionEvents:
        trace === null
          ? []
          : tick === null
            ? trace.mission_events
            : trace.mission_events.filter((event) => event.at.tick <= tick),
      networkFrame:
        trace === null
          ? networkFrameAtSceneState(this.session?.currentState.scenario ?? null, sceneState)
          : tick === null
            ? null
            : (trace.network_frames.find((frame) => frame.at.tick === tick) ?? null),
      networkEvents: this.networkEvents(),
      regions: trace?.scenario.regions ?? [],
      verifier: trace?.verifier_public ?? null,
      trace,
    };
  }

  private networkEvents(): readonly PublicRunEvent[] {
    if (this.mode === "replay") {
      return networkEventProjections(this.trace?.events ?? []);
    }
    return (this.session?.currentState.events ?? []).filter(
      (event) => event.event_type === "public.network-link",
    );
  }

  private applyDeclaredLayerDefaults(scenario: PublicScenario): void {
    const defaults = new Map<LayerId, boolean>();
    for (const layer of scenario.layers) {
      const layerId = layerKindToLayerId(layer.kind);
      if (layerId !== null) {
        defaults.set(layerId, layer.default_visible);
      }
    }
    for (const layer of scenario.base_layers) {
      defaults.set(layer.kind === "imagery" ? "imagery" : "terrain", layer.default_visible);
    }
    this.layers.applyDeclaredDefaults(defaults);
  }

  private mapView() {
    return {
      layers: this.layers.visibility(),
      hiddenEntities: this.layers.hiddenEntityKeys(),
      hiddenTrajectories: this.layers.hiddenTrajectoryIds(),
      isolate: this.layers.isolateTarget(),
      selected: this.selection.get(),
      hovered: this.hover.get(),
    };
  }

  private renderMap(): void {
    if (!this.mapAvailable) {
      return;
    }
    const scene = this.currentMapScene();
    if (scene === null) {
      return;
    }
    this.shell.telemetryHudContainer.hidden = false;
    this.renderMapStats(scene.scenario);
    this.map.render(scene, this.mapView());
    this.map.setP02BusinessFrame?.(this.selectedP02BusinessFrame());
    this.renderP02MapOverlay();
    this.p02FrameOverview();
  }

  /**
   * P02: persistent map labels for business parcels. One sprite per carrier
   * entity that a business record explicitly binds at trace load; the label
   * is the authored parcel id — never a raw dynamic entity id. The
   * declared-set is rebuilt only when the trace or its identity binding
   * changes; the map itself just copies positions per frame.
   */
  private renderP02MapOverlay(): void {
    if (!this.map.setP02CargoLabels) return;
    const trace = this.trace;
    if (trace === null || this.p02View !== "run") {
      this.map.setP02CargoLabels(new Map(), false);
      return;
    }
    // ONE selector: labels are read at the same replay cursor the dock and
    // the inspector consume — never at the trace's final tick. The declared
    // record set is tiny, so it is rebuilt per render instead of caching a
    // second, tick-stale copy.
    const tick = this.currentTick() ?? trace.time.tick;
    const labels = new Map<string, string>();
    const records = this.p02RunIndex?.identity?.allAt(tick) ?? [];
    for (const record of records) {
      if (record.carrierEntityId !== undefined && record.parcelId !== undefined
        && !labels.has(record.carrierEntityId)) {
        labels.set(record.carrierEntityId, record.parcelId);
      }
    }
    this.map.setP02CargoLabels(labels, true);
  }

  /**
   * P02: elevated oblique overview pose fitted to the valid, source-backed
   * business geometry at the current cursor — the selected order's carrier
   * current SceneState pose. Facility ids have no scene entity in the trace, so
   * they contribute no position and stay UNKNOWN; nothing is invented. The
   * frame happens at most once per accepted trace; the cursor keeps authority
   * and no follow is engaged.
   */
  private p02FrameOverview(): void {
    if (this.p02View !== "run") return;
    if (this.shell.map.querySelector<HTMLElement>("#city-map")?.dataset.sceneReady !== "true") return;
    const trace = this.trace;
    if (trace === null || this.p02OverviewFramed === trace) return;
    const tick = this.currentTick() ?? trace.time.tick;
    const identity = this.p02RunIndex?.identity;
    const records = identity?.allAt(tick) ?? [];
    const selectedId = this.p02SelectedOrderId !== null
      ? records.find((record) => record.orderId === this.p02SelectedOrderId)
      : undefined;
    const selected = this.selection.get();
    const carrierId = selected?.kind === "entity" ? selected.id
      : (selectedId ?? records[records.length - 1])?.carrierEntityId;
    const points: { east: number; north: number; up: number }[] = [];
    const state = this.currentSceneState();
    if (state !== null) {
      // Before an order is assigned, frame the current observed aircraft.
      // This camera-only scope does not invent a carrier/custody binding.
      const currentAircraft = new Set(trace.scenario?.entities
        .filter(entity => entity.kind === "uav").map(entity => entity.entity_id) ?? []);
      for (const sample of state.samples) {
        if ((carrierId === undefined ? !currentAircraft.has(sample.entity_id)
          : sample.entity_id !== carrierId) || sample.at.tick > tick) continue;
        const enu = sample.pose.position.enu;
        points.push({ east: enu.east_m, north: enu.north_m, up: enu.up_m });
      }
    }
    const pose: OverviewPoseEnu | null = points.length === 0 ? null : p02OverviewPose(points);
    if (this.map.frameP02Overview?.(pose, state) === true) this.p02OverviewFramed = trace;
  }

  private renderMapStats(scenario: PublicScenario | null): void {
    const lang = currentLanguage();
    const city = this.shell.mapStats.querySelector<HTMLElement>(".map-city-badge");
    const scale = this.shell.mapStats.querySelector<HTMLElement>(".map-scale-badge");
    // Without a scenario the badges would only repeat the header's waiting state.
    this.shell.mapStats.hidden = scenario === null;
    if (scenario === null) return;
    const extent = scenario.frame_authority.spatial_extent;
    const width = extent.max_east_m - extent.min_east_m;
    const depth = extent.max_north_m - extent.min_north_m;
    if (city !== null) city.textContent = `${scenario.world_id} · OSM2World`;
    if (scale !== null) scale.textContent = `${width.toFixed(0)} × ${depth.toFixed(0)} m · ${t("metrics.uavs", lang)} ${scenario.entities.filter((entity) => entity.kind === "uav").length}`;
  }

  // ------------------------------------------------------------------
  // P02 Run / Configuration views. The replay cursor stays the single
  // authority: every number below is read at this.currentTick() from the
  // compiled Run index, never from a secondary clock or a rescan.
  // ------------------------------------------------------------------

  /**
   * Fetch the explicit business-identity sidecar once per trace load and
   * recompile the index with it. The sidecar binds only the exact loaded
   * run id; a missing file or a foreign run leaves every business fact
   * UNKNOWN — it never degrades the trace itself.
   */
  private loadP02BusinessIdentities(trace: PublicTrace): void {
    this.p02IdentityAbort?.abort();
    const controller = new AbortController();
    this.p02IdentityAbort = controller;
    const generation = ++this.p02IdentityGeneration;
    void (async () => {
      try {
        const response = await fetch("./p02-business-identities/demo.json", {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return; // no sidecar published: stay UNKNOWN
        const envelope = parseStrictJson(
          new TextDecoder("utf-8", { fatal: true }).decode(await readBoundedResponse(
            response, 4_000_000, "business identity sidecar", controller.signal))) as BusinessIdentityEnvelope;
        if (controller.signal.aborted || generation !== this.p02IdentityGeneration) return;
        if (envelope === null || typeof envelope !== "object" || envelope.scene_run_id !== trace.run_id) return;
        // Recompile the index once with the accepted sidecar; per-tick
        // rendering keeps joining prebuilt maps only.
        this.p02RunIndex = buildRunIndex({
          trace, entityKinds: entityKindsOf(trace.scenario), identity: envelope,
        });
        this.renderP02RunDock();
        this.renderInspector();
        this.renderP02MapOverlay();
        // The selector now resolves a carrier for the first time; the one-shot
        // overview may have been skipped earlier for lack of valid positions.
        this.p02OverviewFramed = null;
        this.p02FrameOverview();
      } catch {
        // Aborted/invalid sidecar: business facts stay UNKNOWN.
      }
    })();
  }

  /** Show one view; the map and its frame cursor stay mounted in both. */
  private applyP02View(): void {
    const run = this.p02View === "run";
    document.body.classList.add("p02-active");
    document.body.classList.toggle("p02-view-config", !run);
    for (const button of this.shell.p02ViewSwitch?.querySelectorAll<HTMLButtonElement>(".p02-view-button") ?? []) {
      const isCurrent = (button.dataset.p02View === "config") === !run;
      button.setAttribute("aria-pressed", String(isCurrent));
      button.tabIndex = isCurrent ? 0 : -1;
    }
    this.renderP02RunDock();
    this.renderP02Config();
  }

  /** Order list + selected-cargo inspector, scoped to the current cursor tick. */
  private renderP02RunDock(): void {
    const dock = this.shell.p02RunDock;
    if (dock === null) return;
    const trace = this.trace;
    const index = this.p02RunIndex;
    if (trace === null || index === null) {
      dock.hidden = true;
      return;
    }
    dock.hidden = this.p02View !== "run";
    const lang = currentLanguage();
    const unknown = t("p02.unknown", lang);
    const tick = this.currentTick() ?? trace.time.tick;
    const source = this.p02DockSource();
    const sourceNode = dock.querySelector<HTMLElement>(".p02-dock-source");
    if (sourceNode !== null) {
      sourceNode.textContent = source;
      sourceNode.hidden = source.length === 0;
    }

    const ordersBody = dock.querySelector<HTMLElement>(".p02-orders-body");
    if (ordersBody !== null) {
      ordersBody.replaceChildren();
      const identity = index.identity;
      const records = identity?.allAt(tick) ?? [];
      const sourceRef = identity?.source.source_ref ?? null;
      const head = element("div", "p02-orders-head",
        `${t("p02.orders", lang)} · ${records.length === 0 ? unknown : String(records.length)}` +
        (sourceRef === null ? ` · ${t("p02.identityUnknown", lang)}` : ` · ${sourceRef}`));
      ordersBody.append(head);
      if (records.length === 0) {
        ordersBody.append(element("p", "p02-orders-empty", t("p02.noOrders", lang)));
      }
      dock.querySelector(".p02-mission-records")?.remove();
      const missionRecords = renderUnboundMissionRecords(document, index.unboundEventsUpTo(tick), lang, formatRunClock);
      if (missionRecords !== null) ordersBody.after(missionRecords);
      for (const record of records) {
        const row = element("button", "p02-order-row");
        row.type = "button";
        row.setAttribute("role", "listitem");
        const selected = this.p02SelectedOrderId === record.orderId;
        row.classList.toggle("is-selected", selected);
        row.setAttribute("aria-pressed", String(selected));
        // Carrier/custody come only from the authored record.
        const target = p02OrderSelection(record);
        row.append(
          element("strong", "p02-order-id", record.orderId),
          element("span", "p02-order-stage", record.status ?? unknown),
          element("span", "p02-order-meta",
            `${t("p02.parcel", lang)} ${record.parcelId ?? unknown}` +
            ` · ${t("p02.carrier", lang)} ${record.carrierEntityId ?? unknown}` +
            ` · ${t("p02.destination", lang)} ${record.destinationId ?? unknown}` +
            ` · tick ${record.tick} · ${formatRunClock(record.timeSeconds)}`),
          // Raw paths / repeated identifiers stay collapsed by default.
          (() => {
            const details = element("details", "p02-order-details");
            const summary = element("summary", undefined, t("p02.details", lang));
            const line = element("span", "p02-order-detail-line",
              `${t("p02.custody", lang)} ${record.custodyId ?? `${unknown}(${record.custodyKind ?? unknown})`}` +
              ` · ${t("p02.attempt", lang)} ${record.attempt?.toString() ?? unknown}` +
              ` · ${t("p02.source", lang)} ${record.sourceRef}` +
              (record.availabilityReason === undefined ? "" : ` · ${record.availabilityReason}`));
            details.append(summary, line);
            return details;
          })(),
        );
        row.addEventListener("click", () => {
          this.p02SelectedOrderId = selected ? null : record.orderId;
          if (target !== null) {
            // One authoritative selection: the record's carrier feeds the
            // dock, the inspector and the map. Following stays explicit via
            // the cargo view's 聚焦跟随 button; an unresolved position shows
            // 位置未知 instead of moving the camera.
            this.selection.select(target);
            this.p02OverviewFramed = null;
            this.p02FrameOverview();
          }
          this.renderP02RunDock();
        });
        ordersBody.append(row);
      }
    }

    const cargoBody = dock.querySelector<HTMLElement>(".p02-cargo-body");
    if (cargoBody !== null) {
      cargoBody.replaceChildren();
      const selected = this.selection.get();
      if (selected === null || selected.kind !== "entity") {
        cargoBody.append(element("p", "p02-cargo-empty", t("p02.selectEntity", lang)));
        return;
      }
      const cargo = this.selectedP02BusinessFrame();
      if (cargo === null) return;
      const heading = element("div", "p02-cargo-head");
      heading.append(element("h3", "p02-cargo-title", `${t("p02.cargo", lang)} · ${cargo.entityId}`));
      const focusButton = document.createElement("button");
      focusButton.type = "button";
      focusButton.className = "p02-cargo-focus";
      focusButton.textContent = t("p02.follow", lang);
      focusButton.setAttribute("aria-pressed",
        String(this.camera.isFollowing({ kind: "entity", id: cargo.entityId })));
      focusButton.addEventListener("click", () => {
        if (this.camera.isFollowing({ kind: "entity", id: cargo.entityId })) {
          this.camera.releaseFollow();
        } else {
          this.camera.follow({ kind: "entity", id: cargo.entityId });
        }
        this.renderP02RunDock();
      });
      heading.append(focusButton);
      cargoBody.append(heading);
      cargoBody.append(element("p", "p02-cargo-kind",
        `${t("p02.entityKind", lang)} ${cargo.entityKind ?? unknown} · ${t("p02.phase", lang)} ${cargo.phase ?? unknown}`));
      this.renderP02BusinessRecords(cargoBody, cargo);
      if (cargo.attributes.length > 0) {
        const attributes = element("dl", "p02-cargo-attributes");
        for (const attribute of cargo.attributes.slice(0, 8)) {
          const term = element("dt", undefined, attribute.name);
          const description = element("dd", undefined,
            String(attribute.value));
          attributes.append(term, description);
        }
        cargoBody.append(attributes);
      }
    }
  }

  private selectedP02BusinessFrame(): SelectedFrameBusinessView | null {
    const selected = this.selection.get();
    const trace = this.trace;
    const index = this.p02RunIndex;
    const tick = this.currentTick();
    if (selected?.kind !== "entity" || trace === null || index === null || tick === null) return null;
    return selectedFrameBusinessView(index, trace, selected.id, tick,
      sampleAtTick(this.currentSceneState(), selected.id));
  }

  private renderP02BusinessRecords(body: HTMLElement, cargo: SelectedFrameBusinessView): void {
    const lang = currentLanguage();
    const unknown = t("p02.unknown", lang);
    body.append(element("p", "p02-business-source", t(cargo.sourceKind === "authored_business_fixture"
      ? "p02.sourceAuthored" : cargo.sourceKind === "business_provider"
        ? "p02.sourceProvider" : "p02.sourceUnknown", lang)));
    if (cargo.orders.length === 0) body.append(element("p", "p02-cargo-empty", t("p02.noAssociation", lang)));
    for (const order of cargo.orders) {
      const row = element("div", "p02-cargo-order");
      row.dataset.orderId = order.orderId;
      row.dataset.cursorTick = String(cargo.cursorTick);
      row.textContent = `${t("p02.order", lang)} ${order.orderId} · ${order.status ?? unknown}` +
        ` · ${t("p02.parcel", lang)} ${order.parcelId ?? unknown}` +
        ` · ${t("p02.carrier", lang)} ${order.carrierEntityId ?? unknown}` +
        ` · ${t("p02.custody", lang)} ${order.custodyId ?? unknown} (${order.custodyKind ?? unknown})` +
        ` · ${t("p02.destination", lang)} ${order.destinationId ?? unknown}` +
        ` · tick ${order.tick} · ${formatRunClock(order.timeSeconds)}`;
      const details = element("details", "p02-order-details");
      details.append(element("summary", undefined, t("p02.details", lang)),
        element("span", "p02-order-detail-line",
          `${t("p02.attempt", lang)} ${order.attempt?.toString() ?? unknown}` +
          ` · ${t("p02.source", lang)} ${order.sourceRef}` +
          (order.availabilityReason === undefined ? "" : ` · ${order.availabilityReason}`)));
      row.append(details);
      body.append(row);
    }
  }

  /** Source label for the dock header: replay cursor vs live session, from live state only. */
  private p02DockSource(): string {
    const lang = currentLanguage();
    const businessSource = this.p02RunIndex?.identity?.source.source_kind;
    const businessLabel = ` · ${t(businessSource === "authored_business_fixture"
      ? "p02.sourceAuthored" : businessSource === "business_provider" ? "p02.sourceProvider" : "p02.sourceUnknown", lang)}`;
    if (this.mode === "replay") {
      const trace = this.trace;
      return trace === null ? "" : `${t("p02.replayCursor", lang)} ${this.currentTick() ?? trace.time.tick} · ${t("p02.readOnly", lang)}${businessLabel}`;
    }
    const session = this.session?.currentState;
    return session?.snapshot === undefined || session.snapshot === null
      ? t("mode.liveDisconnected", lang) : `${t("p02.live", lang)} ${session.snapshot.run_id.slice(0, 8)}${businessLabel}`;
  }

  /**
   * 配置 view: the SAME configuration the Run view consumes, with real
   * save. City presentation is the one existing config authority exposed to
   * this console: selecting a presentation saves it into the location (?city=)
   * and the map reloads through loadPackedScene; the visible round-trip
   * (dataset.sceneSource / sceneReady + onBasemapNote) is the readback.
   * Entity visibility is saved through the existing LayerState authority
   * (layers.toggleEntity) — no parallel fake configuration.
   */
  private renderP02Config(): void {
    const body = this.shell.p02ConfigBody;
    if (body === null) return;
    if (this.p02View !== "config") {
      body.replaceChildren();
      return;
    }
    body.replaceChildren();
    const lang = currentLanguage();
    const unknown = t("p02.unknown", lang);
    const trace = this.trace;
    const mapContainer = this.shell.map.querySelector<HTMLElement>("#city-map");
    const sceneReady = mapContainer?.dataset.sceneReady === "true";
    const rows: [string, string][] = [
      [t("p02.entry", lang), "frontend/index.html → src/main.ts → PublicTraceApp → PublicTraceMap"],
      [t("p02.frameCursor", lang), this.mode === "replay"
        ? tf("p02.replayAuthority", { tick: this.currentTick() ?? this.trace?.time.tick ?? unknown }, lang)
        : t("p02.liveAuthority", lang)],
      [t("p02.dataSource", lang), this.mode === "replay"
        ? (trace === null ? unknown : `${t("p02.publicTrace", lang)} ${trace.run_id.slice(0, 12)} · ${trace.scenario.replay_mode}`)
        : `Control ${this.session === null ? t("conn.idle", lang) : t("conn.connected", lang)}`],
      [t("p02.entities", lang), trace === null ? unknown : `${trace.scenario.entities.length} (${t("p02.scenarioDeclared", lang)})`],
      [t("p02.orders", lang), this.p02RunIndex?.identity === null || this.p02RunIndex === null ? unknown
        : `${this.p02RunIndex.identity.allAt(this.currentTick() ?? trace?.time.tick ?? 0).length} · ${this.p02RunIndex.identity.source.source_ref}`],
      [t("p02.stageChain", lang), this.p02RunIndex === null || this.p02RunIndex.stageChain().length === 0
        ? unknown : this.p02RunIndex.stageChain().map(stage => stage === "未知" ? unknown : stage).join(" → ")],
    ];
    const table = element("dl", "p02-config-table");
    for (const [name, value] of rows) {
      table.append(element("dt", undefined, name), element("dd", undefined, value));
    }
    body.append(table);

    // City presentation: the actual configuration input consumed by the map.
    // building-render-scene-v1 / default-scene-v1 bind the shanghai-huangpu-east
    // mesh world that the V8 trace's frame authority declares; the jingan
    // previews are a different world (offered for authoring comparisons).
    const presentations = [
      "/city-presentation/building-render-scene-v1.json",
      "/city-presentation/jingan-engineering-preview-v3.json",
      "/city-presentation/jingan-engineering-preview-v2.json",
      "/city-presentation/default-scene-v1.json",
    ];
    const current = new URLSearchParams(window.location.search).get("city")
      ?? "/city-presentation/default-scene-v1.json";
    const label = element("div", "p02-config-label", t("p02.cityPresentation", lang));
    const select = document.createElement("select");
    select.className = "p02-config-select";
    select.setAttribute("aria-label", t("p02.cityPresentation", lang));
    for (const path of presentations) {
      const option = document.createElement("option");
      option.value = path;
      option.textContent = path;
      option.selected = path === current;
      select.append(option);
    }
    const status = element("span", "p02-config-status",
      sceneReady ? `${t("p02.applied", lang)} · ${this.map.sceneSourceLabel()}` : t("p02.waitingScene", lang));
    const saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.className = "p02-config-save";
    saveButton.textContent = t("p02.saveApply", lang);
    saveButton.addEventListener("click", () => {
      if (select.value === current) {
        status.textContent = t("p02.unchanged", lang);
        return;
      }
      const url = new URL(window.location.href);
      url.searchParams.set("city", select.value);
      // Persist through the location — the same authority the map reads on
      // load; the map reloads the pack and the readback line confirms.
      window.location.assign(url);
    });
    const presentationRow = element("div", "p02-config-row");
    presentationRow.append(label, select, saveButton, status);
    body.append(presentationRow);

    // Entity visibility: existing LayerState authority, saved immediately.
    const layers = this.layers.visibility();
    const visibility = element("div", "p02-config-row");
    visibility.append(element("div", "p02-config-label", t("p02.entityVisibility", lang)));
    const hidden = [...this.layers.hiddenEntityKeys()];
    visibility.append(element("span", "p02-config-status",
      hidden.length === 0 ? t("p02.allVisible", lang) : tf("p02.hiddenCount", { count: hidden.length }, lang)));
    body.append(visibility);
    body.append(element("p", "p02-config-note",
      `${t("p02.layerVisibility", lang)}: ${(Object.entries(layers) as [string, boolean][]).map(([id, visible]) =>
        `${id}=${visible ? t("p02.on", lang) : t("p02.off", lang)}`).join(" · ")}`));
    body.append(element("p", "p02-config-note",
      t("p02.configNote", lang)));
  }

  private renderAll(): void {    this.updateReplayInteractions();
    const lang = currentLanguage();
    if (this.mode === "replay") {
      this.shell.modePill.textContent = t("mode.replay", lang);
      this.shell.modePill.className = "pill";
      const trace = this.trace;
      if (trace === null) {
        this.shell.connPill.hidden = true;
        this.shell.phasePill.hidden = false;
        this.shell.phasePill.textContent = t("empty.sealedRequired", lang);
        this.shell.phasePill.className = "pill tone-muted";
        this.shell.integrityPill.hidden = this.sealedReplayStatus !== "failed" && this.sealedReplayStatus !== "loading";
        this.shell.integrityPill.textContent = this.sealedReplayStatus === "failed" ? "FAILED" : "LOADING";
        this.shell.integrityPill.className = `pill tone-${this.sealedReplayStatus === "failed" ? "bad" : "warn"}`;
      } else {
        this.shell.connPill.hidden = false;
        this.shell.phasePill.hidden = false;
        this.shell.integrityPill.hidden = false;
        this.shell.connPill.textContent = t("conn.closed-terminal", lang);
        this.shell.connPill.className = "pill tone-ok";
        this.shell.phasePill.textContent = t(tracePhaseKey(trace.phase), lang);
        this.shell.phasePill.className = `pill tone-${statusTone(trace.phase)}`;
        this.shell.phasePill.title = trace.phase;
        if (this.sealedReplayRequired && this.sealedReplayStatus !== "ready") {
          this.shell.integrityPill.textContent = t("empty.sealedRequired", lang);
          this.shell.integrityPill.className = `pill tone-${this.sealedReplayStatus === "failed" ? "bad" : "warn"}`;
        } else {
          const integrity = integrityForTracePhase(trace.phase);
          this.shell.integrityPill.textContent = t(integrity.key, lang);
          this.shell.integrityPill.className = `pill tone-${integrity.tone}`;
        }
      }
    } else {
      this.shell.modePill.textContent =
        this.session !== null ? t("mode.live", lang) : t("mode.liveDisconnected", lang);
      this.shell.modePill.className = "pill tone-warn";
    }
    this.shell.modePill.classList.add("provenance-chip");
    this.shell.modePill.dataset.provenance = this.mode === "replay" && this.trace !== null
      ? "recorded" : "unknown";
    this.shell.phasePill.classList.add("provenance-chip");
    this.shell.phasePill.dataset.provenance = this.trace?.phase === "verified" && this.sealedReplayReady()
      ? "verified" : "unknown";
    this.renderEntityTree();
    this.renderLayerTree();
    this.renderWeather();
    this.renderInspector();
    this.renderRightRail();
    this.renderTimeline();
    this.renderEventFeed();
    this.renderMetrics();
    this.renderMapMessage();
    this.renderFollowChip(this.camera.followed());
    this.renderMap();
    this.renderP02RunDock();
    this.renderP02Config();
    this.telemetryPanel.update(this.telemetrySource());
    this.updateTelemetryHud();
    if (this.mode === "replay" && this.trace !== null) {
      const kinds = new Set(this.trace.scenario.entities.map(entity => entity.kind as string));
      for (const item of this.shell.map.querySelectorAll<HTMLElement>(".legend-item[data-entity-kind]")) {
        item.hidden = !kinds.has(item.dataset.entityKind!);
      }
      showAvailableContent(this.shell.weatherList);
      showAvailableContent(this.shell.tabPanels);
    }
  }

  private updateTelemetryHud(): void {
    const selected = this.selection.get();
    const sceneState = this.currentSceneState();
    let targetInfo: { name: string; distanceM: number | null } | undefined;
    if (selected !== null && selected.kind === "entity" && sceneState !== null) {
      const uav = sceneState.samples.find((sample) => sample.entity_id.includes("uav"));
      const target = sampleAtTick(sceneState, selected.id);
      if (uav !== undefined && target !== null && uav.entity_id !== selected.id) {
        const left = uav.pose.position.ecef;
        const right = target.pose.position.ecef;
        targetInfo = {
          name: selected.id,
          distanceM: Math.hypot(left.x_m - right.x_m, left.y_m - right.y_m, left.z_m - right.z_m),
        };
      }
    }
    const selectedUavId = selected?.kind === "entity" && selected.id.toLowerCase().includes("uav") ? selected.id : null;
    this.telemetryHud.update(sceneState, targetInfo, selectedUavId);
  }

  private updateReplayInteractions(): void {
    if (this.mode !== "replay") return;
    const events = this.trace === null ? [] : this.replayEvents.at(this.replay.current() ?? 0);
    this.agentConsole.update({ events });
    this.interactionTimeline.update({ events, availableOnly: true });
    this.terminalDock.update({ events, transitions: [], verifierReport: this.trace?.verifier_public ?? null, availableOnly: true });
  }

  private handleMapUnavailable(): void {
    this.mapAvailable = false;
    this.contextMenu.close();
    if (this.camera.followed() !== null) {
      this.camera.releaseFollow();
    }
    this.syncMapOnlyControls();
    this.renderMapMessage();
  }

  private mapOnlyControl<T extends HTMLButtonElement | HTMLInputElement>(control: T): T {
    control.dataset.mapOnly = "true";
    control.disabled = !this.mapAvailable;
    if (this.mapAvailable) {
      control.removeAttribute("title");
    } else {
      control.title = t("map.unavailableTitle");
    }
    return control;
  }

  private syncMapOnlyControls(): void {
    for (const control of this.shell.root.querySelectorAll<HTMLButtonElement | HTMLInputElement>(
      "[data-map-only='true']",
    )) {
      this.mapOnlyControl(control);
    }
  }

  private clearSourceMessage(): void {
    this.shell.sourceMessage.replaceChildren();
    this.shell.sourceMessage.className = "source-message";
    this.shell.sourceMessage.style.removeProperty("right");
    this.shell.sourceMessage.style.removeProperty("bottom");
    this.shell.sourceMessage.hidden = true;
  }

  private renderSourceError(error: unknown): void {
    const detail = error instanceof Error ? error.message : "unknown data source error";
    this.shell.sourceMessage.textContent = `${t("source.errorPrefix")} · ${detail}`;
    this.shell.sourceMessage.className = "source-message error";
    this.shell.sourceMessage.hidden = false;
    this.placeSourceMessage();
  }

  private showSourceNote(note: string): void {
    this.shell.sourceMessage.textContent = note;
    this.shell.sourceMessage.className = "source-message";
    this.shell.sourceMessage.hidden = false;
    this.placeSourceMessage();
  }

  /**
   * Keep a visible toast clear of the HUD box: measure both client rects,
   * let the pure placement decide, and apply the CSS offsets. Dragging,
   * minimize and visibility flips notify through the HUD; ResizeObserver
   * covers the remaining content-box changes; this is event-driven, never
   * a timer or per-frame poll.
   */
  private placeSourceMessage(): void {
    if (this.shell.sourceMessage.hidden) return;
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    const hudRect = this.telemetryHudRect();
    const toast = this.shell.sourceMessage.getBoundingClientRect();
    const placement = placeSourceMessage({
      viewport,
      toast: { width: toast.width, height: toast.height },
      hud: hudRect,
    });
    this.shell.sourceMessage.style.right = `${placement.right}px`;
    this.shell.sourceMessage.style.bottom = `${placement.bottom}px`;
  }

  /** HUD client rect, or null when the HUD is hidden or detached. */
  private telemetryHudRect(): SourceMessageRect | null {
    const root = this.telemetryHud.root;
    if (root.hidden || !root.isConnected) return null;
    const rect = root.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom };
  }

  private renderMapMessage(): void {
    const lang = currentLanguage();
    if (!this.mapAvailable) {
      this.shell.map.dataset.mapState = "unavailable";
      this.shell.mapMessage.hidden = false;
      renderMapMessage(this.shell.mapMessage, t("map.unavailableTitle", lang), t("map.unavailableDetail", lang));
      return;
    }
    this.shell.map.dataset.mapState = "available";
    if (this.trace === null && this.session === null) {
      // The offline OSM2World source is the configured map before a run starts.
      this.shell.mapMessage.hidden = true;
      return;
    }
    const sceneState = this.currentSceneState();
    const hasGeometry = sceneState !== null && sceneState.samples.length > 0;
    this.shell.mapMessage.hidden = hasGeometry;
    if (!hasGeometry) {
      renderMapMessage(this.shell.mapMessage, t("map.noGeometryTitle", lang), t("map.noGeometryDetail", lang));
    } else {
      this.shell.mapMessage.replaceChildren();
    }
  }

  // ------------------------------------------------------------------
  // Left sidebar
  // ------------------------------------------------------------------

  private entityEntries(): readonly { entityId: string; kind: string | null }[] {
    const sceneState = this.currentSceneState();
    if (sceneState === null) {
      return [];
    }
    const definitions = this.trace?.scenario.entities;
    return sceneState.samples.map((sample) => ({
      entityId: sample.entity_id,
      kind: definitions?.find((definition) => definition.entity_id === sample.entity_id)?.kind ?? null,
    }));
  }

  private renderEntityTree(): void {
    const lang = currentLanguage();
    const entries = this.entityEntries();
    const summary = this.shell.left.querySelector<HTMLElement>(".fleet-summary");
    const scenario = this.trace?.scenario ?? this.session?.currentState.scenario ?? null;
    const declaredEntities = scenario?.entities ?? [];
    const count = (kind: string) => declaredEntities.filter((entity) => entity.kind === kind).length;
    if (summary !== null) {
      summary.replaceChildren(
        fleetStat("UAV", count("uav"), "uav"),
        fleetStat("UGV", count("ugv"), "ugv"),
        fleetStat("PED", count("pedestrian"), "pedestrian"),
      );
      summary.title = `${count("uav")} UAV · ${count("ugv")} UGV · ${count("pedestrian")} pedestrians`;
    }
    this.shell.entityTree.replaceChildren();
    if (entries.length === 0) {
      this.shell.entityTree.append(emptyRow(t("empty.entities", lang)));
      return;
    }
    const groups = new Map<string, { entityId: string; kind: string | null }[]>();
    for (const entry of entries) {
      const key = entry.kind ?? "undeclared";
      const list = groups.get(key) ?? [];
      list.push(entry);
      groups.set(key, list);
    }
    for (const key of ["uav", "ugv", "pedestrian", "static_asset", "undeclared"]) {
      const groupEntries = groups.get(key);
      if (groupEntries === undefined) {
        continue;
      }
      const group = element("div", "entity-group");
      const header = element("div", "entity-group-head");
      const dot = element("i", "legend-dot");
      dot.style.background = KIND_DOT_COLORS[key] ?? "#8ba7b6";
      append(
        header,
        dot,
        textBlock(t(`kind.${key}` as I18nKey, lang)),
        element("span", "entity-count", String(groupEntries.length)),
      );
      group.append(header);
      for (const entry of groupEntries) {
        const row = element("div", "entity-row");
        const selectButton = document.createElement("button");
        selectButton.type = "button";
        selectButton.className = "entity-select";
        selectButton.textContent = entry.entityId;
        selectButton.setAttribute(
          "aria-label",
          tf("aria.selectEntity", { kind: t(`kind.${key}` as I18nKey, lang), id: entry.entityId }, lang),
        );
        selectButton.addEventListener("click", () => {
          this.selection.select({ kind: "entity", id: entry.entityId });
        });
        const eye = document.createElement("button");
        eye.type = "button";
        eye.className = "entity-eye";
        eye.textContent = "◉";
        eye.setAttribute(
          "aria-pressed",
          String(!this.layers.isEntityHidden({ kind: "entity", id: entry.entityId })),
        );
        eye.setAttribute("aria-label", tf("aria.toggleEntity", { id: entry.entityId }, lang));
        this.mapOnlyControl(eye);
        eye.addEventListener("click", () => {
          this.layers.toggleEntity({ kind: "entity", id: entry.entityId });
          eye.setAttribute(
            "aria-pressed",
            String(!this.layers.isEntityHidden({ kind: "entity", id: entry.entityId })),
          );
        });
        row.append(selectButton, eye);
        const selected = this.selection.get();
        if (selected !== null && selected.kind === "entity" && selected.id === entry.entityId) {
          row.classList.add("selected");
        }
        group.append(row);
      }
      this.shell.entityTree.append(group);
    }
  }

  private syncEntityTreeSelection(): void {
    const selection = this.selection.get();
    for (const row of this.shell.entityTree.querySelectorAll<HTMLElement>(".entity-row")) {
      const button = row.querySelector<HTMLButtonElement>(".entity-select");
      row.classList.toggle(
        "selected",
        selection !== null && button !== null && button.textContent === selection.id,
      );
    }
  }

  private layerDeclared(layer: LayerId): boolean {
    const trace = this.trace;
    const scenario = trace?.scenario ?? this.session?.currentState.scenario;
    if (layer === "buildings" || layer === "roads" || layer === "terrain") return scenario == null || scenario.layers.some(item => item.kind === "osm_scene") || (layer === "buildings" ? scenario.buildings.length > 0 : layer === "roads" ? scenario.roads.length > 0 : false);
    switch (layer) {
      case "imagery":
        return trace?.scenario.base_layers.some((item) => item.kind === "imagery") ?? false;
      case "regions":
        return (trace?.scenario.regions.length ?? 0) > 0;
      case "weather":
        return (trace?.scenario.weather.length ?? 0) > 0;
      case "network_links":
        return this.networkEvents().length > 0;
      case "trajectories":
        return (trace?.trajectories.length ?? 0) > 0;
      default: {
        const kind = layer === "static_assets" ? "static_asset" : layer;
        return this.entityEntries().some((entry) => entry.kind === kind);
      }
    }
  }

  private renderLayerTree(): void {
    const lang = currentLanguage();
    this.shell.layerTree.replaceChildren();
    let currentGroup = "";
    for (const node of LAYER_TREE) {
      const declared = this.layerDeclared(node.id);
      if (this.mode === "replay" && this.trace !== null && !declared) continue;
      if (node.group !== currentGroup) {
        currentGroup = node.group;
        this.shell.layerTree.append(element("div", "layer-group", t(LAYER_GROUP_KEYS[node.group]!, lang)));
      }
      const row = element("label", "layer-row");
      if (!declared) {
        row.classList.add("is-undeclared");
        row.title = t("layer.undeclaredTitle", lang);
      }
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = this.layers.isLayerVisible(node.id);
      checkbox.dataset.layer = node.id;
      checkbox.setAttribute(
        "aria-label",
        tf("aria.toggleLayer", { name: t(LAYER_LABEL_KEYS[node.id], lang) }, lang),
      );
      this.mapOnlyControl(checkbox);
      if (!declared) {
        checkbox.disabled = true;
      }
      checkbox.addEventListener("change", () => {
        this.layers.setLayerVisible(node.id, checkbox.checked);
      });
      row.append(checkbox, element("span", "layer-label", t(LAYER_LABEL_KEYS[node.id], lang)));
      this.shell.layerTree.append(row);
    }
  }

  private syncLayerTree(): void {
    for (const checkbox of this.shell.layerTree.querySelectorAll<HTMLInputElement>("input[data-layer]")) {
      const layer = checkbox.dataset.layer as LayerId | undefined;
      if (layer !== undefined) {
        checkbox.checked = this.layers.isLayerVisible(layer);
      }
    }
  }

  private renderWeather(): void {
    const lang = currentLanguage();
    this.shell.weatherList.replaceChildren();
    const weather = this.trace?.scenario.weather ?? [];
    const absent = this.mode === "replay" && this.trace !== null && weather.length === 0;
    this.shell.weatherList.hidden = absent;
    const heading = this.shell.weatherList.previousElementSibling;
    if (heading instanceof HTMLElement) heading.hidden = absent;
    if (weather.length === 0) {
      this.shell.weatherList.append(emptyRow(t("empty.weather", lang)));
      return;
    }
    if (!this.layers.isLayerVisible("weather")) {
      return;
    }
    for (const sample of weather) {
      const row = element("div", "weather-row");
      append(
        row,
        element("strong", undefined, sample.sample_id),
        element(
          "small",
          "case-sub",
          `${sample.precipitation} · ${sample.precipitation_rate_mm_per_h.toFixed(1)} mm/h · ${sample.visibility_m.toFixed(0)} m`,
        ),
        element(
          "small",
          "case-sub",
          `${sample.wind.east_mps.toFixed(1)} / ${sample.wind.north_mps.toFixed(1)} / ${sample.wind.up_mps.toFixed(1)} m/s · ${sample.temperature_c.toFixed(1)} °C`,
        ),
      );
      this.shell.weatherList.append(row);
    }
  }

  // ------------------------------------------------------------------
  // Right rail
  // ------------------------------------------------------------------

  private tabForTarget(target: TraceTarget): InfoTab {
    switch (target.kind) {
      case "entity":
        return "telemetry";
      case "network_link":
        return "network";
      case "event":
      case "goal":
      case "metric":
        return "evidence";
      default:
        return "overview";
    }
  }

  private renderRightRail(): void {
    const lang = currentLanguage();
    const trace = this.mode === "replay" ? this.trace : null;
    const hasAgentEvents = trace?.events.some(event => event.agent_id !== null) ?? true;
    const absentTabs = new Set<InfoTab>();
    if (!hasAgentEvents) { absentTabs.add("agent"); absentTabs.add("interactions"); }
    if (trace !== null && trace.network_frames.length === 0) absentTabs.add("network");
    if (trace !== null && trace.scene_states.length === 0) absentTabs.add("telemetry");
    if (absentTabs.has(this.activeTab)) this.activeTab = "overview";
    for (const [tab, button] of this.tabButtons) {
      button.hidden = absentTabs.has(tab);
      button.setAttribute("aria-selected", String(tab === this.activeTab));
      button.classList.toggle("active", tab === this.activeTab);
    }
    const panel = element("div", "tab-panel");
    panel.setAttribute("role", "tabpanel");
    panel.dataset.tab = this.activeTab;
    panel.append(sectionTitle(t(TAB_SECTION_KEYS[this.activeTab], lang)));
    switch (this.activeTab) {
      case "overview":
        this.renderOverviewTab(panel);
        break;
      case "telemetry":
        panel.append(this.telemetryPanel.container);
        break;
      case "agent":
        panel.append(this.agentConsole.container);
        break;
      case "interactions":
        panel.append(this.interactionTimeline.container);
        break;
      case "network":
        this.renderNetworkTab(panel);
        break;
      case "evidence":
        this.renderEvidenceTab(panel);
        break;
      case "boundary":
        this.renderBoundaryTab(panel);
        break;
    }
    this.shell.tabPanels.replaceChildren(panel);
    if (this.mode === "replay" && this.trace !== null) showAvailableContent(panel);
  }

  private renderOverviewTab(panel: HTMLElement | null): void {
    if (panel === null) {
      return;
    }
    const lang = currentLanguage();
    panel.replaceChildren(sectionTitle(t(TAB_SECTION_KEYS.overview, lang)));
    const runPanel = element("section", "panel");
    runPanel.append(element("div", "panel-head", t("panel.run", lang)));
    const trace = this.trace;
    const snapshot = this.mode === "live" ? (this.session?.currentState.snapshot ?? null) : null;
    if (trace !== null) {
      runPanel.append(
        keyValue(t("label.runId", lang), shortDigest(trace.run_id)),
        keyValue(t("label.suite", lang), trace.suite_id),
        keyValue(t("label.case", lang), trace.case_id),
        keyValue(t("label.schema", lang), trace.schema_version),
        keyValue(
          t("label.scope", lang),
          t(trace.execution_scope === "formal_benchmark" ? "scope.formal_benchmark" : "scope.executor_validation", lang),
        ),
        keyValue(t("label.chainRoot", lang), shortDigest(trace.event_chain_root)),
        keyValue(t("label.scenarioDigest", lang), shortDigest(trace.scenario_digest)),
        statusValue(t("label.state", lang), t(tracePhaseKey(trace.phase), lang), statusTone(trace.phase)),
        keyValue(
          `${t("label.tick", lang)} / ${t("label.simTime", lang)}`,
          `${trace.time.tick} · ${formatSimTime(trace.time.sim_time_ns)}`,
        ),
        keyValue(
          t("label.terminal", lang),
          `${trace.terminal.kind}${trace.terminal.failure_class === null ? "" : ` · ${trace.terminal.failure_class}`}`,
        ),
      );
    } else if (snapshot !== null) {
      runPanel.append(
        keyValue(t("label.runId", lang), shortDigest(snapshot.run_id)),
        statusValue(t("control.statusPhase", lang), t(livePhaseKey(snapshot.phase), lang), statusTone(snapshot.phase)),
      );
      const runtimeControl = this.session?.currentState.runtimeControl ?? null;
      if (runtimeControl !== null) {
        runPanel.append(
          keyValue(
            `${t("label.tick", lang)} / ${t("label.simTime", lang)}`,
            `${runtimeControl.current.tick} · ${formatSimTime(runtimeControl.current.sim_time_ns)}`,
          ),
        );
      }
    } else {
      runPanel.append(emptyRow(t("empty.awaitTrace", lang)));
    }
    panel.append(runPanel);

    const providerPanel = element("section", "panel");
    providerPanel.append(element("div", "panel-head", t("section.providers", lang)));
    providerPanel.append(element("div", "control-note", t("section.providersNote", lang)));
    const providers = trace?.provider_status ?? [];
    if (providers.length === 0) {
      providerPanel.append(
        emptyRow(this.mode === "live" ? t("boundary.liveNoTrace", lang) : t("empty.awaitTrace", lang)),
      );
    } else {
      for (const provider of providers) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "provider-button";
        button.setAttribute("aria-label", tf("aria.focusProvider", { id: provider.provider_id }, lang));
        const heading = element("div", "provider-heading");
        append(
          heading,
          element("strong", undefined, provider.provider_id),
          element("span", `status-chip tone-${statusTone(provider.state)}`, provider.state),
        );
        button.append(heading, element("small", "case-sub", `v${provider.version}`));
        if (provider.implementation_kind !== null) {
          button.append(element("small", "case-sub", provider.implementation_kind));
        }
        button.addEventListener("click", () => this.selection.select({ kind: "provider", id: provider.provider_id }));
        providerPanel.append(button);
      }
    }
    panel.append(providerPanel);

    const operationPanel = element("section", "panel operation-panel");
    operationPanel.append(element("div", "panel-head", t("mission.deliveryTitle", lang)));
    const operationScenario = trace?.scenario ?? this.session?.currentState.scenario ?? null;
    if (operationScenario === null) {
      operationPanel.append(emptyRow(t("mission.awaitScene", lang)));
    } else {
      const requirements = operationScenario.mission_requirements;
      operationPanel.append(
        keyValue(t("mission.trafficFlow", lang), operationScenario.sumo === null ? t("label.undeclared", lang) : "TraCI authoritative"),
        keyValue(t("mission.deliveryUavs", lang), String(operationScenario.entities.filter((entity) => entity.kind === "uav").length)),
        keyValue(t("mission.deliveryVehicles", lang), String(operationScenario.entities.filter((entity) => entity.kind === "ugv").length)),
        keyValue(t("mission.pedestrianFlow", lang), String(operationScenario.entities.filter((entity) => entity.kind === "pedestrian").length)),
        keyValue(t("mission.steps", lang), String(requirements.length)),
      );
    }
    panel.append(operationPanel);

    const scenePanel = element("section", "panel");
    scenePanel.append(element("div", "panel-head", t("panel.scene", lang)));
    const scenario = trace?.scenario ?? this.session?.currentState.scenario ?? null;
    if (scenario !== null) {
      const extent = scenario.frame_authority.spatial_extent;
      const width = extent.max_east_m - extent.min_east_m;
      const depth = extent.max_north_m - extent.min_north_m;
      scenePanel.append(
        element("div", "scene-badge", `${scenario.world_id} · ${width.toFixed(0)} × ${depth.toFixed(0)} m`),
      );
      const typeCounts = new Map<string, number>();
      for (const building of scenario.buildings) {
        const type = declaredBuildingType(building);
        typeCounts.set(type, (typeCounts.get(type) ?? 0) + 1);
      }
      const typeGrid = element("div", "building-type-grid");
      for (const [type, count] of [...typeCounts.entries()].sort(([left], [right]) => left.localeCompare(right))) {
        const row = element("button", "building-type-row");
        row.type = "button";
        row.setAttribute("aria-label", `${t("telemetry.buildingType", lang)} ${buildingTypeLabel(type, lang)}`);
        append(row, element("i", `building-type-swatch type-${type}`), element("span", undefined, buildingTypeLabel(type, lang)), element("strong", undefined, String(count)));
        row.addEventListener("click", () => {
          const building = scenario.buildings.find((item) => declaredBuildingType(item) === type);
          if (building !== undefined) this.selection.select({ kind: "building", id: building.building_id });
        });
        typeGrid.append(row);
      }
      if (typeGrid.childElementCount > 0) {
        scenePanel.append(element("div", "subsection-label", t("telemetry.buildingType", lang)), typeGrid);
      }
      const lights = declaredTrafficLights(scenario);
      const signalPanel = element("div", "signal-summary");
      append(signalPanel, element("div", "subsection-label", `${t("kind.traffic_signal", lang)} · ${lights.length}`));
      if (lights.length === 0) {
        signalPanel.append(element("small", "case-sub", t("boundary.signals.title", lang)));
      } else {
        const trafficLightFrame = this.currentTrafficLightFrame();
        for (const light of lights.slice(0, 8)) {
          const row = element("button", "signal-row");
          row.type = "button";
          row.setAttribute("aria-label", `${t("kind.traffic_signal", lang)} ${light.id}`);
          const observed = trafficLightFrame?.states.find((state) => state.signal_id === light.id);
          const signalState = observed === undefined ? "unknown" : signalAspect(observed.state);
          const lamp = element("i", `signal-lamp signal-${signalState}`);
          append(row, lamp, element("strong", undefined, light.id), element("span", "case-sub", `${observed?.state ?? "telemetry missing"} · ${light.cycle_s}s · ${light.phases.length} ${t("telemetry.signalProgram", lang)}`));
          row.addEventListener("click", () => this.selection.select({ kind: "traffic_signal", id: light.id }));
          signalPanel.append(row);
        }
        if (lights.length > 8) signalPanel.append(element("small", "case-sub", `+${lights.length - 8}`));
      }
      if (lights.length > 0) scenePanel.append(signalPanel);
      const sumo = scenario.sumo;
      if (sumo !== null) {
        const vehicles = scenario.entities.filter((entity) => entity.kind === "ugv").length;
        const pedestrians = scenario.entities.filter((entity) => entity.kind === "pedestrian").length;
        scenePanel.append(
          element("div", "sumo-summary", `SUMO · ${sumo.provider_id} · ${vehicles} ${t("metrics.vehicles", lang)} · ${pedestrians} ${t("metrics.pedestrians", lang)} · ${lights.length} ${t("metrics.trafficLights", lang)}`),
        );
      }
    }
    if (scenario === null) {
      scenePanel.append(emptyRow(this.mode === "live" ? t("boundary.liveNoScenario", lang) : t("empty.awaitTrace", lang)));
    } else {
      let sceneRows = 0;
      for (const region of scenario.regions ?? []) {
        scenePanel.append(keyValue(region.region_id, t(`regionKind.${region.kind}` as I18nKey, lang)));
        sceneRows += 1;
      }
      for (const building of scenario.buildings ?? []) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "scene-row";
        const resolved = this.modelUrls.has(building.render_asset_id);
        button.append(
          element("strong", undefined, building.building_id),
          element(
            "span",
            resolved ? "tone-ok" : "tone-muted",
            resolved ? t("label.assetResolved", lang) : t("label.assetUnresolved", lang),
          ),
        );
        button.addEventListener("click", () => this.selection.select({ kind: "building", id: building.building_id }));
        scenePanel.append(button);
        sceneRows += 1;
      }
      if (sceneRows === 0) {
        scenePanel.append(emptyRow(t("boundary.regions.title", lang)));
      }
    }
    panel.append(scenePanel);
  }

  private networkEventRows(events: readonly PublicNetworkEvent[]): HTMLElement {
    const lang = currentLanguage();
    const eventPanel = element("section", "panel");
    eventPanel.append(element("div", "panel-head", t("section.timeline", lang)));
    if (events.length === 0) {
      eventPanel.append(emptyRow(t("boundary.events.title", lang)));
      return eventPanel;
    }
    for (const event of events.slice(-20)) {
      const row = element("div", "provider-row");
      const heading = element("div", "provider-heading");
      append(
        heading,
        element("strong", undefined, event.link_id),
        element("span", `tone-${statusTone(event.state)}`, event.state),
      );
      row.append(heading);
      row.append(
        element(
          "small",
          "case-sub",
          `${t("clock.tick", lang)} ${event.at.tick} · ${event.source_entity_id} → ${event.target_entity_id}`,
        ),
      );
      for (const property of event.properties) {
        row.append(keyValue(property.name, displayValue(property.value)));
      }
      eventPanel.append(row);
    }
    return eventPanel;
  }

  private networkRunEventRows(events: readonly PublicRunEvent[]): HTMLElement {
    const lang = currentLanguage();
    const eventPanel = element("section", "panel");
    eventPanel.append(element("div", "panel-head", t("section.timeline", lang)));
    if (events.length === 0) {
      eventPanel.append(emptyRow(t("boundary.events.title", lang)));
      return eventPanel;
    }
    for (const event of events.slice(-20)) {
      const row = element("div", "provider-row");
      const heading = element("div", "provider-heading");
      append(
        heading,
        element("strong", undefined, event.entity_id ?? event.source),
        element("span", "tone-muted", event.event_type),
      );
      row.append(heading);
      row.append(
        element(
          "small",
          "case-sub",
          `${t("clock.tick", lang)} ${event.at.tick} · ${event.source_kind}:${event.source}`,
        ),
      );
      for (const attribute of event.public_payload) {
        row.append(keyValue(attribute.name, displayValue(attribute.value)));
      }
      eventPanel.append(row);
    }
    return eventPanel;
  }

  private renderNetworkTab(panel: HTMLElement): void {
    const lang = currentLanguage();
    const trace = this.trace;
    if (this.mode === "replay" && trace !== null) {
      if (trace.network_frames.length === 0) {
        panel.append(emptyRow(t("network.noneDeclared", lang)));
        return;
      }
      const tick = this.currentTick();
      const frame = tick === null ? null : (trace.network_frames.find((item) => item.at.tick === tick) ?? null);
      if (frame !== null) {
        const framePanel = element("section", "panel");
        framePanel.append(
          element("div", "panel-head", `${t("clock.tick", lang)} ${frame.at.tick}`),
          keyValue(t("telemetry.nodes", lang), String(frame.nodes.length)),
          keyValue(t("telemetry.links", lang), String(frame.links.length)),
        );
        panel.append(framePanel);
      }
      const tickForEvents = tick;
      const events =
        tickForEvents === null
          ? trace.network_events
          : trace.network_events.filter((event) => event.at.tick <= tickForEvents);
      panel.append(this.networkEventRows(events));
      return;
    }
    panel.append(this.networkRunEventRows(this.networkEvents()));
  }

  private renderEvidenceTab(panel: HTMLElement): void {
    const lang = currentLanguage();
    const trace = this.trace;
    const registryPanel = element("section", "panel");
    registryPanel.append(element("div", "panel-head", t("evidence.artifactRegistry", lang)));
    if (trace === null) {
      registryPanel.append(emptyRow(t("boundary.liveNoTrace", lang)));
      panel.append(registryPanel);
    } else {
      const seen = new Set<string>();
      let registryRows = 0;
      for (const artifact of trace.runtime_artifacts) {
        const key = `${artifact.artifact_id}\u0000${artifact.replay_path}`;
        if (seen.has(key)) {
          continue;
        }
        seen.add(key);
        const row = element("div", "evidence-row");
        row.append(
          element("strong", undefined, artifact.artifact_id),
          element(
            "small",
            "case-sub",
            `${artifact.artifact_type} · ${artifact.replay_path} · ${formatBytes(artifact.size_bytes)}`,
          ),
          element("code", undefined, shortDigest(artifact.sha256)),
          element("span", "integrity-badge", t("evidence.sealedReference", lang)),
        );
        registryPanel.append(row);
        registryRows += 1;
      }
      if (registryRows === 0) {
        registryPanel.append(emptyRow(t("empty.awaitTrace", lang)));
      }
      panel.append(registryPanel);
    }

    const assetPanel = element("section", "panel");
    assetPanel.append(
      element("div", "panel-head", t("evidence.replayAssets", lang)),
      element("div", "control-note", t("evidence.replayAssetsNote", lang)),
    );
    if (trace === null) {
      assetPanel.append(emptyRow(t("boundary.liveNoTrace", lang)));
      panel.append(assetPanel);
    } else {
      const declared = declaredReplayModelAssets(trace);
      if (declared.length === 0) {
        assetPanel.append(emptyRow(t("empty.awaitTrace", lang)));
      } else {
        for (const asset of declared) {
          const resolution = this.assetResolutions.find((item) => item.assetId === asset.asset_id);
          assetPanel.append(
            keyValue(
              asset.asset_id,
              resolution === undefined
                ? t("label.assetUnresolved", lang)
                : resolution.state === "resolved"
                  ? t("label.assetResolved", lang)
                  : `${t("label.assetFailed", lang)} · ${resolution.detail}`,
            ),
          );
        }
      }
      panel.append(assetPanel);
      if (this.mode === "replay") {
        panel.append(this.assetResolutionControls(trace));
      }
    }

    const verifier = trace?.verifier_public ?? null;
    const verdictPanel = element("section", "panel");
    verdictPanel.append(element("div", "panel-head", t("detection.verifierStatus", lang)));
    if (verifier === null) {
      verdictPanel.append(emptyRow(t("boundary.verifier.title", lang)));
    } else {
      const verdictRow = element("div", "verdict-row");
      verdictRow.append(
        element(
          "span",
          `verdict-chip tone-${verifier.status === "passed" ? "ok" : "bad"}`,
          verifier.status === "passed" ? t("result.pass", lang) : verifier.status === "failed" ? t("result.fail", lang) : verifier.status,
        ),
        element(
          "span",
          `chip ${verifier.coverage_complete ? "chip-ok" : "chip-warn"}`,
          verifier.coverage_complete ? t("coverage.complete", lang) : t("coverage.incomplete", lang),
        ),
      );
      verdictPanel.append(verdictRow);
      for (const goal of verifier.goals) {
        const row = element("div", "goal");
        row.append(
          element("b", undefined, goal.goal_id),
          element("span", `tone-${goal.passed ? "ok" : "bad"}`, goal.passed ? t("result.pass", lang) : t("result.fail", lang)),
        );
        for (const metric of goal.metrics) {
          row.append(
            keyValue(
              metric.metric_id,
              metric.unit === null ? String(metric.value) : `${metric.value} ${metric.unit}`,
            ),
          );
        }
        verdictPanel.append(row);
      }
    }
    panel.append(verdictPanel);
  }

  private assetResolutionControls(trace: Readonly<PublicTrace>): HTMLElement {
    const lang = currentLanguage();
    const controls = element("section", "panel");
    const label = element("div", "panel-head", t("evidence.resolve", lang));
    controls.append(label);
    const baseInput = document.createElement("input");
    baseInput.type = "text";
    baseInput.className = "credential-input";
    baseInput.placeholder = DEFAULT_REPLAY_ASSET_BASE;
    baseInput.setAttribute("aria-label", t("evidence.resolve", lang));
    baseInput.value = this.replayAssetBase ?? DEFAULT_REPLAY_ASSET_BASE;
    const resolveButton = actionButton(t("evidence.resolve", lang), () => {
      void this.resolveDeclaredAssets(trace, baseInput.value);
    });
    controls.append(baseInput, resolveButton);
    return controls;
  }

  /**
   * Resolve the declared entity model assets through the verified
   * resolver. Each declared byte size and SHA-256 must match exactly
   * before a Blob URL exists; failures are recorded per asset and
   * never silently substituted.
   */
  private async resolveDeclaredAssets(trace: Readonly<PublicTrace>, baseValue: string): Promise<void> {
    const lang = currentLanguage();
    let resolver: AssetResolver;
    let baseHref: string;
    let fetchImpl: typeof fetch | undefined;
    try {
      const base = baseValue.trim() === "" ? DEFAULT_REPLAY_ASSET_BASE : baseValue.trim();
      baseHref = new URL(base, window.location.href).href;
      if (new URL(baseHref).origin !== window.location.origin) {
        throw new AssetResolutionError("the asset base must resolve to the same origin as the viewer");
      }
      // Only the run's own virtual directory goes through its authenticated routes.
      fetchImpl = this.replayAssetFetch !== null && this.replayAssetBase !== null
        && baseHref.startsWith(this.replayAssetBase) ? this.replayAssetFetch : undefined;
      resolver = new AssetResolver({ baseHref, ...(fetchImpl === undefined ? {} : { fetch: fetchImpl }) });
    } catch (error) {
      this.showSourceNote(`${t("source.errorPrefix")} · ${error instanceof Error ? error.message : String(error)}`);
      return;
    }
    const generation = ++this.assetResolveGeneration;
    const previous = this.assetResolver;
    this.assetResolver = resolver;
    previous?.dispose();
    this.meshScene?.dispose();
    this.meshScene = null;
    this.nativePresentationAbort?.abort();
    const nativeAbort = new AbortController();
    this.nativePresentationAbort = nativeAbort;
    this.nativePresentation?.dispose(); this.nativePresentation = null;
    this.renderMap(); // Invalidate any pending render, including same-scenario retries.
    // Bind the scene before optional per-entity model preloads. The map uses
    // recorded-state glyphs; hundreds of model files must not block UAV replay.
    const nativeVerifiedAssets = new Set<string>();
    try {
      const route = inspectNativeCityPresentation(trace.scenario);
      if (route.kind === "unavailable") throw new Error(`Scene publication has no renderable native presentation: ${route.reason}`);
      this.loadingProgress.update({ stage: "scene" });
      if (route.kind === "native-city") {
        const native = await loadNativeCityPresentation(route.plan, {
          baseHref, fetch: fetchImpl, signal: nativeAbort.signal,
          onProgress: progress => {
            if (generation === this.assetResolveGeneration) {
              this.loadingProgress.update({ stage: "scene", completed: progress.completed, total: progress.total });
            }
          },
        });
        if (generation !== this.assetResolveGeneration || this.disposed) { native.dispose(); return; }
        this.nativePresentation = native;
        for (const binding of route.plan.buildings) nativeVerifiedAssets.add(binding.asset.asset_id);
      } else {
        const pack = await loadScenarioMeshPack(trace.scenario, resolver, (completed, total) => {
          if (generation === this.assetResolveGeneration) this.loadingProgress.update({ stage: "scene", completed, total });
        });
        if (generation !== this.assetResolveGeneration || this.disposed) { pack.dispose(); return; }
        this.meshScene = pack;
      }
      this.loadingProgress.update({ stage: "render" });
      this.renderMap();
    } catch (error) {
      if (generation !== this.assetResolveGeneration) return;
      this.loadingProgress.update({ stage: "failed", detail: error instanceof Error ? error.message : String(error) });
      this.replay.pause();
      this.showSourceNote(`Native scene asset failed: ${error instanceof Error ? error.message : String(error)}`);
      return;
    }
    const declared = declaredReplayModelAssets(trace);
    const urls = new Map<string, string>();
    const resolutions: AssetResolution[] = [];
    for (const asset of declared) {
      if (generation !== this.assetResolveGeneration) return;
      if (nativeVerifiedAssets.has(asset.asset_id)) {
        resolutions.push({ assetId: asset.asset_id, replayPath: asset.replay_path,
          sha256: asset.sha256, state: "resolved", detail: "Verified and rendered by the native-city asset loader" });
        continue;
      }
      try {
        const resolved = await resolver.fetchVerified(asset.replay_path, {
          sha256: asset.sha256,
          sizeBytes: asset.size_bytes,
          mediaType: asset.media_type,
        });
        urls.set(asset.asset_id, resolved.url);
        resolutions.push({ assetId: asset.asset_id, replayPath: asset.replay_path, sha256: asset.sha256, state: "resolved", detail: "" });
      } catch (error) {
        resolutions.push({
          assetId: asset.asset_id,
          replayPath: asset.replay_path,
          sha256: asset.sha256,
          state: "failed",
          detail: error instanceof AssetResolutionError ? error.message : String(error),
        });
      }
    }
    if (generation !== this.assetResolveGeneration) {
      resolver.dispose();
      return;
    }
    this.modelUrls = urls;
    this.assetResolutions = resolutions;
    this.renderRightRail();
    this.renderMap();
    void lang;
  }

  private async resolveLiveScene(scenario: PublicScenario): Promise<void> {
    const generation = ++this.assetResolveGeneration;
    this.osmScene = null;
    this.meshScene?.dispose(); this.meshScene = null;
    this.liveMeshResolver?.dispose(); this.liveMeshResolver = null;
    this.nativePresentationAbort?.abort();
    const nativeAbort = new AbortController();
    this.nativePresentationAbort = nativeAbort;
    this.nativePresentation?.dispose(); this.nativePresentation = null;
    const session = this.session;
    if (session === null) return;
    const baseHref = window.location.origin + "/";
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
        const digest = new URL(String(input)).pathname.split("/").at(-1)!;
        try { return await session.publicAsset(digest, init?.signal ?? undefined); }
        finally {
          if (this.session === session) {
            this.shell.map.dataset.controlAssetDiagnostics = JSON.stringify(session.assetDiagnostics);
            this.shell.map.dataset.controlRunId = session.currentState.snapshot?.run_id ?? "";
            this.shell.map.dataset.controlLatestTick = String(session.currentState.latestTick ?? "");
          }
        }
      }) as typeof fetch;
    const resolver = new AssetResolver({ baseHref, fetch: fetchImpl });
    this.renderMap();
    try {
      const route = inspectNativeCityPresentation(scenario);
      if (route.kind === "unavailable") throw new Error(`Scene publication has no renderable native presentation: ${route.reason}`);
      if (route.kind === "native-city") {
        const native = await loadNativeCityPresentation(route.plan, { baseHref, fetch: fetchImpl, signal: nativeAbort.signal });
        if (generation !== this.assetResolveGeneration || this.session !== session
            || this.liveAssetScenario !== scenario.scenario_digest || this.disposed) {
          native.dispose(); resolver.dispose(); return;
        }
        this.nativePresentation = native;
        resolver.dispose();
      } else {
        const pack = await loadScenarioMeshPack(scenario, resolver);
        if (generation !== this.assetResolveGeneration || this.session !== session
            || this.liveAssetScenario !== scenario.scenario_digest || this.disposed) {
          pack.dispose(); resolver.dispose(); return;
        }
        this.meshScene = pack; this.liveMeshResolver = resolver;
      }
      this.renderMap();
    } catch (error) {
      resolver.dispose();
      if (generation !== this.assetResolveGeneration || this.session !== session || this.disposed) return;
      this.showSourceNote(`Native scene asset failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  private clearReplaySource(): void {
    this.loadingProgress.clear();
    this.replayEvents = new ReplayEvents([]);
    this.replayAssetBase = null;
    this.replayAssetFetch = null;
    this.trace = null;
    this.store.clear();
    this.resetAssetResolution();
    this.replay.pause();
    this.replay.clear();
  }

  private resetAssetResolution(): void {
    this.osmScene = null;
    this.meshScene?.dispose(); this.meshScene = null;
    this.nativePresentationAbort?.abort(); this.nativePresentationAbort = null;
    this.nativePresentation?.dispose(); this.nativePresentation = null;
    this.liveMeshResolver?.dispose(); this.liveMeshResolver = null;
    this.assetResolveGeneration += 1;
    this.assetResolver?.dispose();
    this.assetResolver = null;
    this.sealedReplayLoader?.dispose();
    this.sealedReplayLoader = null;
    this.sealedReplayRequired = false;
    this.sealedReplayStatus = "idle";
    this.modelUrls = new Map();
    this.assetResolutions = [];
  }

  private renderOsmProperties(body: HTMLElement, id: string): void {
    const item = this.osmScene?.elements.find(element => `${element.type[0]}${element.id}` === id);
    const tags = item?.tags ?? this.map.osmProperties(id);
    if (tags === null || tags === undefined) { body.append(emptyRow(t("label.undeclared"))); return; }
    body.append(keyValue("OSM", id));
    for (const [key, value] of Object.entries(tags)) body.append(keyValue(key, value));
  }

  private renderBoundaryTab(panel: HTMLElement): void {
    const lang = currentLanguage();
    const trace = this.trace;
    const items: { title: string; detail?: string }[] = [];
    if (trace?.scenario.sumo !== null && trace?.scenario.sumo !== undefined) {
      items.push({
        title: lang === "zh" ? "道路与交通几何" : "Road and traffic geometry",
        detail: lang === "zh"
          ? "OSM2World 展示 OSM 道路；SUMO 独立生成车道、人行道和路口，因此两者边缘不保证重合。车辆与行人显示实际记录坐标，不向视觉道路吸附。"
          : "OSM2World renders OSM roads; SUMO derives lanes, sidewalks and junctions separately. Their edges can differ. Vehicles and pedestrians retain recorded positions without snapping to visual roads.",
      });
    }
    if (this.mode === "live") {
      if (this.session?.currentState.scenario === null) {
        items.push({ title: t("boundary.liveNoScenario", lang) });
      }
      items.push({ title: t("boundary.liveNoTrace", lang) });
    }
    if (trace === null) {
      if (this.mode === "replay") {
        panel.append(emptyRow(t("empty.noSelectionNoTrace", lang)));
        return;
      }
    } else if (this.mode !== "replay") {
      if (trace.scenario.buildings.length === 0) {
        items.push({ title: t("boundary.buildings.title", lang) });
      }
      if (trace.scenario.weather.length === 0) {
        items.push({ title: t("boundary.weather.title", lang) });
      }
      if (trace.scenario.regions.length === 0) {
        items.push({ title: t("boundary.regions.title", lang) });
      }
      if (trace.network_frames.length === 0) {
        items.push({ title: t("boundary.network.title", lang) });
      }
      if (trace.events.length === 0) {
        items.push({ title: t("boundary.events.title", lang) });
      }
      if (trace.verifier_public === null) {
        items.push({ title: t("boundary.verifier.title", lang), detail: t("boundary.verifier.detail", lang) });
      }
      if (trace.mission_status === null) {
        items.push({ title: t("boundary.status.title", lang), detail: t("boundary.status.detail", lang) });
      }
    }
    if (items.length === 0) {
      panel.append(emptyRow(t("boundary.none", lang)));
      return;
    }
    const list = element("div", "boundary-list");
    for (const item of items) {
      const row = element("div", "boundary-item");
      append(
        row,
        element("strong", undefined, item.title),
        item.detail === undefined ? null : element("small", "case-sub", item.detail),
      );
      list.append(row);
    }
    panel.append(list);
  }

  // ------------------------------------------------------------------
  // Inspector
  // ------------------------------------------------------------------

  private renderInspector(): void {
    const trace = this.trace;
    const selected = this.selection.get();
    this.shell.inspector.replaceChildren();
    if (selected === null) {
      this.shell.inspector.hidden = true;
      return;
    }
    const lang = currentLanguage();
    this.shell.inspector.hidden = false;
    const inspector = element("section", "panel");
    const head = element("div", "panel-head inspector-head");
    head.append(element("strong", undefined, `${t(`kind.${selected.kind}` as I18nKey, lang)} ${selected.id}`));
    inspector.append(head);
    const body = element("div", "inspector-body");
    body.setAttribute("role", "region");
    body.setAttribute("aria-label", `${t(`kind.${selected.kind}` as I18nKey, lang)} ${selected.id}`);
    switch (selected.kind) {
      case "entity": {
        const sceneState = this.currentSceneState();
        const tick = this.currentTick();
        const sample = sampleAtTick(sceneState, selected.id);
        const business = this.selectedP02BusinessFrame();
        if (business !== null) this.renderP02BusinessRecords(body, business);
        if (sample === null) {
          body.append(emptyRow(t("telemetry.noSample", lang)));
          break;
        }
        const exact = tick !== null && sample.at.tick === tick;
        body.append(
          statusValue(
            t("cursor.current", lang),
            exact ? t("cursor.current", lang) : t("cursor.historical", lang),
            exact ? "ok" : "warn",
          ),
          keyValue(t("label.sampleKind", lang), sample.sample_kind),
          keyValue(t("label.stage", lang), t(`stage.${sample.stage}` as I18nKey, lang)),
          keyValue(t("label.authority", lang), sample.provider_id),
          keyValue(
            t("telemetry.enu", lang),
            `${sample.pose.position.enu.east_m.toFixed(2)} / ${sample.pose.position.enu.north_m.toFixed(2)} / ${sample.pose.position.enu.up_m.toFixed(2)} m`,
          ),
          keyValue(t("telemetry.agl", lang), displayMeters(sample.pose.position.agl_m)),
          keyValue(
            t("telemetry.velocityEnu", lang),
            `${sample.linear_velocity_enu.east_mps.toFixed(2)} / ${sample.linear_velocity_enu.north_mps.toFixed(2)} / ${sample.linear_velocity_enu.up_mps.toFixed(2)} m/s`,
          ),
          keyValue(t("telemetry.sampleDigest", lang), shortDigest(sample.sample_digest)),
        );
        break;
      }
      case "region": {
        const region = trace?.scenario.regions.find((item) => item.region_id === selected.id);
        if (region === undefined) {
          body.append(emptyRow(t("empty.noSelectionNoTrace", lang)));
          break;
        }
        body.append(
          keyValue(t("label.kind", lang), t(`regionKind.${region.kind}` as I18nKey, lang)),
          keyValue(t("telemetry.vertices", lang), String(region.lower_vertices.length)),
          keyValue(
            t("telemetry.geoid", lang),
            region.communications_shadow_attenuation_db === null
              ? t("label.undeclared", lang)
              : `${region.communications_shadow_attenuation_db} dB`,
          ),
        );
        break;
      }
      case "building": {
        const building = (trace?.scenario ?? this.session?.currentState.scenario)?.buildings.find((item) => item.building_id === selected.id);
        if (building === undefined) {
          this.renderOsmProperties(body, selected.id);
          break;
        }
        const resolution = this.assetResolutions.find((item) => item.assetId === building.render_asset_id);
        body.append(
          keyValue(t("telemetry.buildingType", lang), buildingTypeLabel(declaredBuildingType(building), lang)),
          keyValue(t("label.entityId", lang), building.entity_id),
          keyValue(t("telemetry.vertices", lang), `${building.base_vertices.length} / ${building.top_vertices.length}`),
          keyValue(t("label.modelAsset", lang), building.render_asset_id),
          keyValue(
            t("label.renderAsset", lang),
            resolution === undefined
              ? t("label.assetUnresolved", lang)
              : resolution.state === "resolved"
                ? t("label.assetResolved", lang)
                : `${t("label.assetFailed", lang)} · ${resolution.detail}`,
          ),
        );
        break;
      }
      case "traffic_signal": {
        const light = trace === null ? undefined : declaredTrafficLights(trace.scenario).find((item) => item.id === selected.id);
        if (light === undefined) {
          body.append(emptyRow(t("boundary.signals.title", lang)));
          break;
        }
        body.append(
          keyValue(t("label.kind", lang), t("kind.traffic_signal", lang)),
          keyValue(t("label.entityId", lang), light.junction_id),
          keyValue(t("telemetry.signalProgram", lang), `${light.cycle_s}s · offset ${light.phase_offset_s}s`),
          keyValue(t("telemetry.controlledRoads", lang), light.controlled_road_ids.join(", ")),
        );
        for (const phase of light.phases) {
          body.append(keyValue(phase.state, `${phase.duration_s}s`));
        }
        break;
      }
      case "road": {
        const road = (trace?.scenario ?? this.session?.currentState.scenario)?.roads.find((item) => item.road_id === selected.id);
        if (road === undefined) {
          this.renderOsmProperties(body, selected.id);
          break;
        }
        body.append(
          keyValue(t("label.kind", lang), road.kind),
          keyValue("宽度", `${road.width_m.toFixed(1)} m`),
          keyValue(t("telemetry.vertices", lang), String(road.terrain_points.length)),
        );
        break;
      }
      case "network_link": {
        const tick = this.currentTick();
        const frame =
          trace !== null && tick !== null
            ? (trace.network_frames.find((item) => item.at.tick === tick) ?? null)
            : null;
        const link = frame?.links.find((item) => item.link_id === selected.id);
        if (link === undefined) {
          body.append(emptyRow(t("network.noneDeclared", lang)));
          break;
        }
        body.append(
          keyValue(t("network.linkState", lang), `${link.source_node_id} → ${link.destination_node_id}`),
          keyValue(t("label.authority", lang), `${link.source_pose.position.enu.east_m.toFixed(1)}, ${link.source_pose.position.enu.north_m.toFixed(1)}`),
        );
        break;
      }
      case "event": {
        const event = this.eventsSource().find((item) => item.event_id === selected.id);
        if (event === undefined) {
          body.append(emptyRow(t("empty.noSelectionNoTrace", lang)));
          break;
        }
        body.append(
          keyValue(t("label.kind", lang), `${event.source_kind}:${event.source}`),
          keyValue(t("label.tick", lang), String(event.at.tick)),
          keyValue(t("label.simTime", lang), formatSimTime(event.at.sim_time_ns)),
          keyValue(t("agent.correlation", lang), event.correlation_id),
          keyValue(t("agent.digest", lang), shortDigest(event.payload_digest)),
        );
        for (const attribute of event.public_payload) {
          body.append(keyValue(attribute.name, displayValue(attribute.value)));
        }
        break;
      }
      case "goal":
      case "metric": {
        const verifier = trace?.verifier_public ?? null;
        if (verifier === null) {
          body.append(emptyRow(t("boundary.verifier.title", lang)));
          break;
        }
        if (selected.kind === "goal") {
          const goal = verifier.goals.find((item) => item.goal_id === selected.id);
          if (goal === undefined) {
            body.append(emptyRow(t("empty.noSelectionNoTrace", lang)));
            break;
          }
          body.append(
            statusValue(
              t("label.result", lang),
              goal.passed ? t("result.pass", lang) : t("result.fail", lang),
              goal.passed ? "ok" : "bad",
            ),
            keyValue(t("label.failureClass", lang), displayValue(goal.failure_class)),
          );
          for (const metric of goal.metrics) {
            body.append(
              keyValue(
                metric.metric_id,
                metric.unit === null ? String(metric.value) : `${metric.value} ${metric.unit}`,
              ),
            );
          }
        } else {
          const metric = verifier.goals
            .flatMap((goal) => goal.metrics)
            .find((item) => item.metric_id === selected.id);
          if (metric === undefined) {
            body.append(emptyRow(t("empty.noSelectionNoTrace", lang)));
            break;
          }
          body.append(
            keyValue(t("label.value", lang), metric.unit === null ? String(metric.value) : `${metric.value} ${metric.unit}`),
            keyValue(
              t("label.provenance", lang),
              metric.evidence.map((artifact) => artifact.artifact_id).join(", "),
            ),
          );
        }
        break;
      }
      case "provider": {
        const provider = trace?.provider_status.find((item) => item.provider_id === selected.id);
        if (provider === undefined) {
          body.append(emptyRow(t("empty.noSelectionNoTrace", lang)));
          break;
        }
        body.append(
          statusValue(t("label.providerState", lang), provider.state, statusTone(provider.state)),
          keyValue(t("label.version", lang), displayValue(provider.version)),
          keyValue(t("label.implementation", lang), displayValue(provider.implementation_kind)),
        );
        break;
      }
    }
    inspector.append(body);
    inspector.append(this.inspectorActions(selected));
    this.shell.inspector.append(inspector);
  }

  private inspectorActions(selected: TraceTarget): HTMLElement {
    const lang = currentLanguage();
    const actions = element("div", "inspector-actions");
    actions.setAttribute("role", "toolbar");
    actions.setAttribute("aria-label", `${t(`kind.${selected.kind}` as I18nKey, lang)} ${selected.id}`);
    const addMap = (label: string, onClick: () => void, pressed?: boolean) =>
      append(actions, this.mapOnlyControl(actionButton(label, onClick, pressed)));
    if (
      selected.kind === "entity" ||
      selected.kind === "region" ||
      selected.kind === "building" ||
      selected.kind === "network_link" ||
      selected.kind === "road" ||
      selected.kind === "traffic_signal"
    ) {
      addMap(t("action.focus", lang), () => this.camera.focus(selected));
      addMap(
        sameTarget(this.layers.isolateTarget(), selected) ? t("action.showAll", lang) : t("action.isolate", lang),
        () => this.layers.toggleIsolate(selected),
        sameTarget(this.layers.isolateTarget(), selected),
      );
      const hidden = this.layers.isEntityHidden(selected);
      addMap(
        hidden ? t("action.showEntity", lang) : t("action.hideEntity", lang),
        () => this.layers.toggleEntity(selected),
        !hidden,
      );
    }
    if (selected.kind === "entity") {
      const trajectoryHidden = this.layers.isTrajectoryHidden(selected.id);
      addMap(
        trajectoryHidden ? t("action.showTrajectory", lang) : t("action.hideTrajectory", lang),
        () => this.layers.toggleTrajectory(selected.id),
        !trajectoryHidden,
      );
      const following = this.camera.isFollowing(selected);
      const followButton = this.mapOnlyControl(
        actionButton(
          following ? t("action.stopFollow", lang) : t("action.follow", lang),
          () => {
            if (this.camera.isFollowing(selected)) {
              this.camera.releaseFollow();
            } else {
              this.camera.follow(selected);
            }
          },
          following,
        ),
      );
      followButton.setAttribute(
        "aria-label",
        `${following ? t("action.stopFollow", lang) : t("action.follow", lang)} ${selected.id}`,
      );
      followButton.setAttribute("aria-keyshortcuts", "f");
      actions.append(followButton);
    }
    if (selected.kind === "event") {
      const event = this.eventsSource().find((item) => item.event_id === selected.id);
      if (event !== undefined) {
        append(actions, actionButton(t("action.jumpTick", lang), () => {
          if (this.mode === "replay" && !this.sealedReplayReady()) return;
          this.replay.goToTick(event.at.tick);
        }));
        if (event.entity_id !== null) {
          const entityId = event.entity_id;
          append(
            actions,
            actionButton(t("action.selectRefEntity", lang), () => {
              this.selection.select({ kind: "entity", id: entityId });
            }),
          );
        }
      }
    }
    append(actions, actionButton(t("action.clearSelection", lang), () => this.selection.clear()));
    return actions;
  }

  // ------------------------------------------------------------------
  // Timeline, transport, metrics
  // ------------------------------------------------------------------

  private renderTimeline(): void {
    const lang = currentLanguage();
    this.shell.transport.replaceChildren();
    const replayActive = this.mode === "replay" && this.trace !== null && this.sealedReplayReady();
    const liveStates = this.session?.currentState.sceneStates ?? [];
    const liveActive = this.mode === "live" && liveStates.length > 0;
    const controlsActive = replayActive || liveActive;

    const add = (label: string, onClick: () => void, pressed?: boolean) => {
      const button = actionButton(label, onClick, pressed);
      button.disabled = !controlsActive;
      this.shell.transport.append(button);
      return button;
    };
    add(t("transport.first", lang), () => this.replay.first());
    add(t("transport.prev", lang), () => this.replay.step(-1));
    this.playButton = add(
      this.replay.isPlaying() ? t("transport.pause", lang) : t("transport.play", lang),
      () => this.replay.togglePlay(),
      this.replay.isPlaying(),
    );
    add(t("transport.next", lang), () => this.replay.step(1));
    add(t("transport.last", lang), () => this.replay.last());
    const speed = document.createElement("select");
    speed.className = "speed-select";
    speed.setAttribute("aria-label", t("transport.speed", lang));
    speed.disabled = !replayActive;
    for (const value of PLAY_SPEEDS) {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = `${value}×`;
      if (this.replay.speedValue() === value) {
        option.selected = true;
      }
      speed.append(option);
    }
    speed.addEventListener("change", () => {
      this.replay.setSpeed(Number(speed.value) as PlaySpeed);
    });
    this.shell.transport.append(speed);

    const recorded =
      this.mode === "replay"
        ? (this.trace === null ? [] : recordedTicks(this.trace))
        : liveStates.map((s) => s.at.tick);

    this.shell.scrubber.min = "0";
    this.shell.scrubber.max = String(Math.max(recorded.length - 1, 0));
    this.shell.scrubber.disabled = !controlsActive || recorded.length === 0;
    this.renderScrubberMarks();
    this.syncTimelineCursor();
  }

  private renderScrubberMarks(): void {
    const trace = this.trace;
    if (trace === null || !this.sealedReplayReady()) {
      this.shell.scrubberMarks.replaceChildren();
      return;
    }
    const lang = currentLanguage();
    const recorded = recordedTicks(trace);
    this.shell.scrubberMarks.replaceChildren();
    if (recorded.length < 2) {
      return;
    }
    const eventTicks = new Set<number>();
    for (const event of trace.events) {
      eventTicks.add(event.at.tick);
    }
    for (const [index, tick] of recorded.entries()) {
      if (!eventTicks.has(tick)) {
        continue;
      }
      const mark = element("button", "tick-mark");
      mark.type = "button";
      mark.style.left = `${(index / (recorded.length - 1)) * 100}%`;
      mark.setAttribute("aria-label", tf("aria.jumpMarker", { n: tick }, lang));
      mark.addEventListener("click", () => this.replay.goToTick(tick));
      this.shell.scrubberMarks.append(mark);
    }
  }

  private syncTimelineCursor(): void {
    const lang = currentLanguage();
    if (this.mode === "replay") {
      const trace = this.trace;
      const tick = this.replay.current();
      if (trace === null || tick === null) {
        this.shell.clock.textContent = t("empty.awaitTrace", lang);
        this.shell.scrubberLabel.replaceChildren();
        this.shell.scrubber.value = "0";
        this.shell.scrubber.setAttribute("aria-valuenow", "0");
        this.shell.scrubber.removeAttribute("aria-valuetext");
        return;
      }
      const recorded = recordedTicks(trace);
      const index = this.replay.currentIndex();
      this.shell.scrubber.value = String(index);
      this.shell.scrubber.setAttribute("aria-valuemin", "0");
      this.shell.scrubber.setAttribute("aria-valuemax", String(Math.max(recorded.length - 1, 0)));
      this.shell.scrubber.setAttribute("aria-valuenow", String(index));
      this.shell.scrubber.setAttribute(
        "aria-valuetext",
        `${t("label.recordedTicks", lang)} ${tick} / ${recorded.length}`,
      );
      const recordedSimTime = simTimeAtTick(trace, tick);
      this.shell.clock.textContent =
        recordedSimTime === null
          ? `${t("clock.tick", lang)} ${String(tick).padStart(5, "0")} · ${t("tick.unavailable", lang)}`
          : `${t("clock.tick", lang)} ${String(tick).padStart(5, "0")} · ${formatSimTime(recordedSimTime)}`;
      this.shell.scrubberLabel.replaceChildren(
        element(
          "span",
          "play-indicator",
          this.replay.isPlaying() ? t("indicator.playing", lang) : t("indicator.replay", lang),
        ),
        textBlock(`${t("clock.tick", lang)} ${String(tick).padStart(5, "0")}`),
        textBlock(
          recordedSimTime === null
            ? `${t("label.simTime", lang)} ${t("simTime.unavailable", lang)}`
            : `${t("label.simTime", lang)} ${formatSimTime(recordedSimTime)}`,
        ),
        textBlock(`${t("label.recordedTicks", lang)} · ${recorded.length}`),
      );
    } else {
      const states = this.session?.currentState.sceneStates ?? [];
      if (states.length === 0) {
        return;
      }
      const recorded = this.replay.recorded();
      const tick = this.replay.current() ?? states[states.length - 1]?.at.tick ?? null;
      if (tick === null) {
        return;
      }
      const index = this.replay.currentIndex();
      this.shell.scrubber.value = String(Math.max(0, index));
      this.shell.scrubber.setAttribute("aria-valuemin", "0");
      this.shell.scrubber.setAttribute("aria-valuemax", String(Math.max(recorded.length - 1, 0)));
      this.shell.scrubber.setAttribute("aria-valuenow", String(index));
      this.shell.clock.textContent = `${t("clock.tick", lang)} ${String(tick).padStart(5, "0")} · LIVE`;
      this.shell.scrubberLabel.replaceChildren(
        element(
          "span",
          "play-indicator",
          this.replay.isPlaying() ? "LIVE" : "INSPECT",
        ),
        textBlock(`${t("clock.tick", lang)} ${String(tick).padStart(5, "0")}`),
        textBlock(`BUFFER · ${states.length}`),
      );
    }
    if (this.playButton !== null) {
      this.playButton.setAttribute("aria-pressed", String(this.replay.isPlaying()));
      this.playButton.textContent = this.replay.isPlaying()
        ? t("transport.pause", lang)
        : t("transport.play", lang);
    }
  }

  private renderEventFeed(): void {
    const lang = currentLanguage();
    this.shell.eventList.replaceChildren(sectionTitle(t("section.timeline", lang)));
    const selected = this.selection.get();
    const events = this.eventsSource();
    const transitions = this.mode === "live" ? (this.session?.currentState.transitions ?? []) : [];
    if (events.length === 0 && transitions.length === 0) {
      this.shell.eventList.append(emptyRow(t("boundary.events.title", lang)));
      return;
    }
    for (const event of events.slice(-50)) {
      const row = element("button", "event event-button");
      row.type = "button";
      const isSelected = selected !== null && selected.kind === "event" && selected.id === event.event_id;
      if (isSelected) {
        row.classList.add("selected");
      }
      row.setAttribute("aria-pressed", String(isSelected));
      row.setAttribute("aria-label", tf("aria.eventAtTick", { n: event.at.tick }, lang));
      append(
        row,
        element("time", undefined, `${t("feed.tick", lang)} ${String(event.at.tick).padStart(5, "0")}`),
        element("span", "event-severity tone-muted", event.interaction_type ?? event.event_type),
      );
      const detail = event.agent_id ?? event.provider_id ?? event.entity_id ?? event.source;
      row.append(element("small", "event-detail", `${event.event_id} · ${detail}`));
      row.addEventListener("click", () => {
        this.selection.select({ kind: "event", id: event.event_id });
      });
      this.shell.eventList.append(row);
    }
    for (const transition of transitions.slice(-10).reverse()) {
      const row = element("div", "event");
      append(
        row,
        element("time", undefined, `seq ${transition.sequence}`),
        element("span", `event-severity tone-${statusTone(transition.phase)}`, t(livePhaseKey(transition.phase), lang)),
      );
      row.append(element("small", "event-detail", transition.event_type));
      this.shell.eventList.append(row);
    }
  }

  private renderMetrics(): void {
    const lang = currentLanguage();
    const trace = this.trace;
    const sceneState = this.currentSceneState();
    const states = this.mode === "live" ? (this.session?.currentState.sceneStates ?? []) : (trace?.scene_states ?? []);
    const scenario = trace?.scenario ?? this.session?.currentState.scenario ?? null;
    const entities = scenario?.entities ?? [];
    const uavs = entities.filter((entity) => entity.kind === "uav").length;
    const vehicles = entities.filter((entity) => entity.kind === "ugv").length;
    const pedestrians = entities.filter((entity) => entity.kind === "pedestrian").length;
    const trafficLights = scenario === null ? 0 : declaredTrafficLights(scenario).length;
    const extent = scenario?.frame_authority.spatial_extent;
    this.shell.metricsGrid.replaceChildren(
      metricCard(t("metrics.entities", lang), String(sceneState?.samples.length ?? 0)),
      metricCard(t("metrics.sceneStates", lang), String(states.length)),
      metricCard(
        t("metrics.trajectories", lang),
        String(
          this.mode === "live"
            ? trajectoriesFromSceneStates(states).length
            : (trace?.trajectories.length ?? 0),
        ),
      ),
      metricCard(t("metrics.events", lang), String(this.eventsSource().length)),
      metricCard(t("metrics.uavs", lang), String(uavs)),
      metricCard(t("metrics.vehicles", lang), String(vehicles)),
      metricCard(t("metrics.pedestrians", lang), String(pedestrians)),
      metricCard(t("metrics.buildings", lang), String(scenario?.buildings.length ?? 0)),
      metricCard(t("metrics.trafficLights", lang), String(trafficLights)),
      metricCard(
        t("metrics.mapExtent", lang),
        extent === undefined ? "—" : `${(extent.max_east_m - extent.min_east_m).toFixed(0)}×${(extent.max_north_m - extent.min_north_m).toFixed(0)}m`,
      ),
    );
  }

  // ------------------------------------------------------------------
  // Context menu
  // ------------------------------------------------------------------

  private renderContextMenu(menu: ContextMenu | null): void {
    const node = this.shell.contextMenu;
    const lang = currentLanguage();
    if (menu === null) {
      node.hidden = true;
      node.replaceChildren();
      return;
    }
    node.replaceChildren();
    const target = menu.target;
    if (target === null) {
      node.append(this.menuItem(t("action.clearSelection", lang), () => this.selection.clear()));
      return;
    }
    const title = element(
      "div",
      "context-menu-title",
      `${t(`kind.${target.kind}` as I18nKey, lang)} ${target.id}`,
    );
    node.append(title);
    if (
      target.kind === "entity" ||
      target.kind === "region" ||
      target.kind === "building" ||
      target.kind === "network_link" ||
      target.kind === "road" ||
      target.kind === "traffic_signal"
    ) {
      node.append(this.menuItem(t("action.focus", lang), () => this.camera.focus(target), true));
      node.append(
        this.menuItem(
          sameTarget(this.layers.isolateTarget(), target) ? t("action.showAll", lang) : t("action.isolate", lang),
          () => this.layers.toggleIsolate(target),
          true,
        ),
      );
      node.append(
        this.menuItem(
          this.layers.isEntityHidden(target) ? t("action.showEntity", lang) : t("action.hideEntity", lang),
          () => this.layers.toggleEntity(target),
          true,
        ),
      );
    }
    if (target.kind === "entity") {
      node.append(
        this.menuItem(
          this.camera.isFollowing(target) ? t("action.stopFollow", lang) : t("action.follow", lang),
          () => {
            if (this.camera.isFollowing(target)) {
              this.camera.releaseFollow();
            } else {
              this.camera.follow(target);
            }
          },
          true,
        ),
      );
      node.append(
        this.menuItem(
          this.layers.isTrajectoryHidden(target.id)
            ? t("action.showTrajectory", lang)
            : t("action.hideTrajectory", lang),
          () => this.layers.toggleTrajectory(target.id),
          true,
        ),
      );
    }
    if (target.kind === "event") {
      node.append(
        this.menuItem(t("action.jumpTick", lang), () => {
          const event = this.eventsSource().find((item) => item.event_id === target.id);
          if (event !== undefined) {
            if (this.mode === "replay" && !this.sealedReplayReady()) return;
            this.replay.goToTick(event.at.tick);
          }
        }),
      );
    }
    node.append(this.menuItem(t("action.clearSelection", lang), () => this.selection.clear()));
    node.style.left = `${menu.x}px`;
    node.style.top = `${menu.y}px`;
    node.hidden = false;
    const first = node.querySelector<HTMLButtonElement>("button");
    first?.focus();
  }

  private menuItem(label: string, onClick: () => void, mapOnly = false): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "context-menu-item";
    button.textContent = label;
    button.setAttribute("role", "menuitem");
    button.addEventListener("click", () => {
      onClick();
      this.contextMenu.close();
    });
    return mapOnly ? this.mapOnlyControl(button) : button;
  }
}

/** Public network-link projections, taken only from validated public events. */
function networkEventProjections(events: readonly PublicRunEvent[]): readonly PublicRunEvent[] {
  return events.filter((event) => event.event_type === "public.network-link");
}

function layerKindToLayerId(kind: string): LayerId | null {
  switch (kind) {
    case "buildings":
      return "buildings";
    case "roads":
      return "roads";
    case "regions":
      return "regions";
    case "weather":
      return "weather";
    default:
      return null;
  }
}

const BUILDING_TYPES: readonly BuildingType[] = [
  "residential",
  "commercial",
  "office",
  "industrial",
  "public",
  "logistics",
];

function declaredBuildingType(building: unknown): string {
  const record = building !== null && typeof building === "object" ? (building as Record<string, unknown>) : null;
  const declared = record?.building_type;
  return typeof declared === "string" && BUILDING_TYPES.includes(declared as BuildingType) ? declared : "undeclared";
}

function buildingTypeLabel(type: string, lang: ReturnType<typeof currentLanguage>): string {
  return BUILDING_TYPES.includes(type as BuildingType)
    ? t(`buildingType.${type}` as I18nKey, lang)
    : t("label.undeclared", lang);
}

function declaredTrafficLights(scenario: PublicScenario): TrafficLight[] {
  const raw = scenario as unknown as Record<string, unknown>;
  const values = raw.traffic_lights;
  if (!Array.isArray(values)) return [];
  return values.flatMap((value) => {
    if (value === null || typeof value !== "object") return [];
    const record = value as Record<string, unknown>;
    const id = typeof record.id === "string" ? record.id : typeof record.signal_id === "string" ? record.signal_id : null;
    const junctionId = typeof record.junction_id === "string" ? record.junction_id : null;
    const east = typeof record.east_m === "number" ? record.east_m : null;
    const north = typeof record.north_m === "number" ? record.north_m : null;
    const cycle = typeof record.cycle_s === "number" ? record.cycle_s : null;
    const offset = typeof record.phase_offset_s === "number" ? record.phase_offset_s : null;
    const controlled = Array.isArray(record.controlled_road_ids)
      ? record.controlled_road_ids.filter((item): item is string => typeof item === "string")
      : [];
    const phases = Array.isArray(record.phases)
      ? record.phases.flatMap((phase) => {
          if (phase === null || typeof phase !== "object") return [];
          const item = phase as Record<string, unknown>;
          return typeof item.state === "string" && typeof item.duration_s === "number"
            ? [{ state: item.state, duration_s: item.duration_s }]
            : [];
        })
      : [];
    return id !== null && junctionId !== null && east !== null && north !== null && cycle !== null && offset !== null && controlled.length > 0 && phases.length > 0
      ? [{ id, junction_id: junctionId, east_m: east, north_m: north, cycle_s: cycle, phase_offset_s: offset, controlled_road_ids: controlled, phases }]
      : [];
  });
}

function signalAspect(state: string): "red" | "amber" | "green" {
  const normalized = state.toLowerCase();
  return normalized.includes("g") ? "green" : normalized.includes("y") ? "amber" : "red";
}

interface DeclaredModelAsset {
  readonly asset_id: string;
  readonly replay_path: string;
  readonly sha256: string;
  readonly size_bytes: number;
  readonly media_type: string | null;
}

/**
 * Entity models, building renders, and declared imagery/terrain tiles.
 * GeoJSON overlays stay out of the OSM2World geometry resolver.
 */
export function declaredReplayModelAssets(trace: Readonly<PublicTrace>): readonly DeclaredModelAsset[] {
  const modelAssetIds = new Set<string>();
  for (const entity of trace.scenario.entities) {
    if (entity.model_asset_id !== null) {
      modelAssetIds.add(entity.model_asset_id);
    }
  }
  for (const building of trace.scenario.buildings) {
    modelAssetIds.add(building.render_asset_id);
  }
  for (const layer of trace.scenario.base_layers ?? []) {
    modelAssetIds.add(layer.asset_id);
  }
  return trace.scenario.assets
    .filter((asset) => modelAssetIds.has(asset.asset_id))
    .map((asset) => ({
      asset_id: asset.asset_id,
      replay_path: asset.replay_path,
      sha256: asset.sha256,
      size_bytes: asset.size_bytes,
      media_type: asset.media_type,
    }));
}

function workspaceTraceLabel(entry: WorkspaceTraceEntry, lang: ReturnType<typeof currentLanguage>): string {
  const schema = entry.schema_version ?? "unknown";
  const identity = entry.run_id !== null ? `${entry.run_id.slice(0, 8)}…` : entry.relative_path;
  const phase = entry.phase ?? "";
  const suffix = entry.loadable ? "" : ` · ${t("workspaceTraces.unloadable", lang)}`;
  return `${entry.relative_path} · ${schema} · ${identity} ${phase}${suffix}`.replaceAll("  ", " ").trim();
}

function renderMapMessage(node: HTMLElement, title: string, detail: string): void {
  node.replaceChildren();
  const icon = element("span", "empty-map-icon", "◇");
  icon.setAttribute("aria-hidden", "true");
  append(node, icon, element("strong", undefined, title), element("small", undefined, detail));
}

function fleetStat(label: string, value: number, kind: string): HTMLElement {
  const node = element("div", `fleet-stat fleet-stat-${kind}`);
  append(node, element("small", undefined, label), element("strong", undefined, String(value)));
  return node;
}

function isTextEntryTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLSelectElement ||
    target instanceof HTMLTextAreaElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}

// ---------------------------------------------------------------------------
// Shell construction
// ---------------------------------------------------------------------------

function buildShell(root: HTMLElement, mode: AppMode): AppShell {
  const lang = currentLanguage();
  root.replaceChildren();
  const app = element("div", "app");
  const header = element("header", "topbar");
  const brand = element("div", "brand");
  append(brand, element("span", "brand-mark", "◆"), element("span", "brand-name", "AERO-BENCH"));
  const subtitle = element("span", "brand-sub");
  subtitle.dataset.i18n = "app.subtitle";
  brand.append(subtitle);
  const meta = element("div", "run-meta");
  const modePill = pill(mode === "replay" ? t("mode.replay", lang) : t("mode.liveDisconnected", lang));
  modePill.id = "mode-pill";
  const connPill = pill(t(mode === "live" ? "conn.idle" : "empty.awaitTrace", lang), "muted");
  const phasePill = pill(t("empty.awaitTrace", lang), "muted");
  const integrityPill = pill(t("empty.awaitTrace", lang), "muted");
  // Until a session or trace supplies content, only the mode pill speaks for the state.
  connPill.hidden = mode === "live";
  phasePill.hidden = true;
  integrityPill.hidden = true;
  const publicOnlyPill = pill(t("badge.publicOnly", lang), "ok");
  publicOnlyPill.dataset.i18n = "badge.publicOnly";
  const liveLink = document.createElement("a");
  liveLink.className = "mode-link";
  liveLink.href = "./";
  liveLink.dataset.i18n = "mode.link.live";
  liveLink.textContent = t("mode.link.live", lang);
  if (mode === "live") {
    liveLink.classList.add("active");
    liveLink.setAttribute("aria-current", "page");
  }
  const replayLink = document.createElement("a");
  replayLink.className = "mode-link";
  replayLink.href = "./?view=replay";
  replayLink.dataset.i18n = "mode.link.replay";
  replayLink.textContent = t("mode.link.replay", lang);
  if (mode === "replay") {
    replayLink.classList.add("active");
    replayLink.setAttribute("aria-current", "page");
  }
  const libraryLink = document.createElement("a");
  libraryLink.className = "mode-link";
  libraryLink.href = "./asset-library.html";
  libraryLink.dataset.i18n = "mode.link.assets";
  libraryLink.textContent = t("mode.link.assets", lang);
  // P02 map-first view switch: 运行 (map + business dock) and 配置 (actual
  // console state). Buttons, not links: they toggle in-page views without
  // reloading the trace or the scene.
  const p02ViewSwitch = element("div", "p02-view-switch");
  p02ViewSwitch.setAttribute("role", "group");
  p02ViewSwitch.setAttribute("aria-label", "视图切换：运行 / 配置");
  const p02RunButton = document.createElement("button");
  p02RunButton.type = "button";
  p02RunButton.className = "p02-view-button";
  p02RunButton.textContent = "运行";
  p02RunButton.setAttribute("aria-pressed", "true");
  const p02ConfigButton = document.createElement("button");
  p02ConfigButton.type = "button";
  p02ConfigButton.className = "p02-view-button";
  p02ConfigButton.textContent = "配置";
  p02ConfigButton.setAttribute("aria-pressed", "false");
  p02ViewSwitch.append(p02RunButton, p02ConfigButton);
  append(meta, modePill, connPill, phasePill, integrityPill, publicOnlyPill, p02ViewSwitch, liveLink, replayLink, libraryLink);
  if (mode === "live") {
    const controlsToggle = document.createElement("button");
    controlsToggle.type = "button";
    controlsToggle.className = "action-button p02-controls-toggle";
    controlsToggle.dataset.i18n = "control.title";
    controlsToggle.textContent = t("control.title", lang);
    controlsToggle.setAttribute("aria-controls", "p02-live-controls");
    controlsToggle.setAttribute("aria-expanded", "false");
    controlsToggle.addEventListener("click", () => {
      const opened = document.body.classList.toggle("p02-controls-open");
      controlsToggle.setAttribute("aria-expanded", String(opened));
    });
    meta.append(controlsToggle);
  }
  const langGroup = element("div", "lang-switch");
  langGroup.setAttribute("role", "group");
  langGroup.setAttribute("aria-label", t("lang.switch", lang));
  const langZh = document.createElement("button");
  langZh.type = "button";
  langZh.className = "lang-button";
  langZh.textContent = "中文";
  langZh.setAttribute("aria-label", t("aria.languageZh", lang));
  const langEn = document.createElement("button");
  langEn.type = "button";
  langEn.className = "lang-button";
  langEn.textContent = "EN";
  langEn.setAttribute("aria-label", t("aria.languageEn", lang));
  append(langGroup, langZh, langEn);
  const clock = element("div", "clock");

  const traceSelect = document.createElement("select");
  traceSelect.id = "workspace-trace-select";
  traceSelect.className = "workspace-trace-select";
  traceSelect.setAttribute("aria-label", t("workspaceTraces.placeholder", lang));
  traceSelect.setAttribute("aria-describedby", "public-trace-file-help");
  const loadTraceButton = actionButton(t("workspaceTraces.load", lang), () => {});
  loadTraceButton.className = "action-button";
  const refreshTracesButton = actionButton(t("workspaceTraces.refresh", lang), () => {});
  refreshTracesButton.className = "action-button";
  const fileHelp = element("span", "visually-hidden", t("action.loadTraceHelp", lang));
  fileHelp.id = "public-trace-file-help";
  const sourceControl = element("div", "trace-source-control");
  append(sourceControl, traceSelect, loadTraceButton, refreshTracesButton, fileHelp);
  if (mode !== "replay") {
    sourceControl.hidden = true;
  }
  append(header, brand, meta, langGroup, sourceControl, clock);

  const main = element("main", "main");

  const left = element("aside", "sidebar");
  left.setAttribute("aria-label", t("section.entities", lang));
  const controlPanel = element("section", "control-panel");
  controlPanel.id = "p02-live-controls";
  if (mode !== "live") {
    controlPanel.hidden = true;
  }
  const controlTitle = sectionTitle(t("control.title", lang));
  const endpointInput = document.createElement("input");
  endpointInput.type = "text";
  endpointInput.className = "credential-input";
  endpointInput.placeholder = t("control.endpointHint", lang);
  endpointInput.setAttribute("aria-label", t("control.endpoint", lang));
  endpointInput.autocomplete = "off";
  endpointInput.spellcheck = false;
  const tokenInput = document.createElement("input");
  tokenInput.type = "password";
  tokenInput.className = "credential-input";
  tokenInput.setAttribute("aria-label", t("control.token", lang));
  tokenInput.autocomplete = "off";
  const csrfInput = document.createElement("input");
  csrfInput.type = "password";
  csrfInput.className = "credential-input";
  csrfInput.setAttribute("aria-label", t("control.csrf", lang));
  csrfInput.autocomplete = "off";
  const catalogButton = actionButton(t("control.loadCatalog", lang), () => {});
  const disconnectButton = actionButton(t("control.disconnect", lang), () => {});
  const catalogBody = element("div", "catalog-body");
  const startButton = actionButton(t("control.start", lang), () => {});
  const registeredReplayButton = actionButton(lang === "zh" ? "打开已封存回放" : "Open sealed replay", () => {});
  registeredReplayButton.dataset.role = "open-registered-replay";
  registeredReplayButton.disabled = true;
  const controlButtons = element("div", "control-buttons");
  const pauseButton = actionButton(t("control.pause", lang), () => {});
  const resumeButton = actionButton(t("control.resume", lang), () => {});
  const stepButton = actionButton(t("control.step", lang), () => {});
  const stopButton = actionButton(t("control.stop", lang), () => {});
  append(controlButtons, startButton, pauseButton, resumeButton, stepButton, stopButton);
  const credentialNote = element("div", "control-note", t("control.credentialNote", lang));
  const sessionStatus = element("div", "session-status");
  // Visible labels: placeholders vanish on input and the password fields had none.
  const field = (input: HTMLInputElement, key: I18nKey): HTMLLabelElement => {
    const label = document.createElement("label");
    label.className = "control-field";
    const caption = element("span", "control-field-label", t(key, lang));
    caption.dataset.i18n = key;
    label.append(caption, input);
    input.removeAttribute("aria-label");
    return label;
  };
  append(
    controlPanel,
    controlTitle,
    field(endpointInput, "control.endpoint"),
    field(tokenInput, "control.token"),
    field(csrfInput, "control.csrf"),
    credentialNote,
    catalogButton,
    disconnectButton,
    catalogBody,
    registeredReplayButton,
    controlButtons,
    sessionStatus,
  );

  const entityTree = element("div", "entity-tree");
  entityTree.setAttribute("role", "group");
  entityTree.setAttribute("aria-label", t("section.entities", lang));
  const layerTree = element("div", "layer-tree");
  layerTree.setAttribute("role", "group");
  layerTree.setAttribute("aria-label", t("section.layers", lang));
  const weatherList = element("div", "weather-list");
  const entitiesTitle = sectionTitle(t("section.entities", lang));
  entitiesTitle.dataset.i18n = "section.entities";
  const layersTitle = sectionTitle(t("section.layers", lang));
  layersTitle.dataset.i18n = "section.layers";
  const weatherTitle = sectionTitle(t("section.weather", lang));
  weatherTitle.dataset.i18n = "section.weather";
  const fleetSummary = element("div", "fleet-summary");
  // P02 Configuration view: echoes the actual, live config state of this
  // console (frame source, scene, layers, replay cursor authority). Every
  // value is read back from the running application state; nothing here is
  // an editable placeholder.
  const p02ConfigSection = element("section", "p02-config-section");
  p02ConfigSection.id = "p02-config-section";
  p02ConfigSection.setAttribute("aria-label", "配置 · 实际控制台状态");
  p02ConfigSection.hidden = true;
  const p02ConfigTitle = element("h2", "p02-section-title", "配置");
  const p02ConfigBody = element("div", "p02-config-body");
  p02ConfigSection.append(p02ConfigTitle, p02ConfigBody);
  append(left, controlPanel, entitiesTitle, fleetSummary, entityTree, layersTitle, layerTree, weatherTitle, weatherList,
    p02ConfigSection);

  const map = element("section", "map");
  map.setAttribute("aria-label", t("section.overview", lang));
  const mapContainer = element("div", "city-map");
  mapContainer.id = "city-map";
  const hud = element("div", "map-hud");
  const hudTopLeft = element("div", "hud-top-left");
  const followChip = document.createElement("button");
  followChip.type = "button";
  followChip.className = "hud-chip hud-follow-chip";
  followChip.hidden = true;
  hudTopLeft.append(followChip);
  const hudNote = element("div", "hud-note");
  hudNote.setAttribute("role", "status");
  const mapStats = element("div", "map-stats");
  mapStats.setAttribute("role", "status");
  mapStats.setAttribute("aria-live", "polite");
  mapStats.append(
    element("span", "map-city-badge", "上海中心城区 · OSM2World"),
    element("span", "map-scale-badge", "OSM · WGS84"),
  );
  const legend = element("div", "map-legend");
  const legendTitle = element("div", "legend-title", t("hud.legend.title", lang));
  const legendKinds = element("div", "legend-kinds");
  for (const key of ["uav", "ugv", "pedestrian", "static_asset", "undeclared"]) {
    const item = element("span", "legend-item");
    item.dataset.entityKind = key;
    const dot = element("i", "legend-dot");
    dot.style.background = KIND_DOT_COLORS[key] ?? "#8ba7b6";
    item.append(dot, textBlock(t(`kind.${key}` as I18nKey, lang)));
    legendKinds.append(item);
  }
  const attributionNote = element("div", "attribution-note", t("hud.legend.attribution", lang));
  append(legend, legendTitle, legendKinds, attributionNote);
  append(hud, hudTopLeft, mapStats, hudNote, legend);

  // P02 map-first business dock. The 3D map owns the viewport; this dock is
  // the only business surface over it and scrolls locally, never the body.
  const p02RunDock = element("section", "p02-run-dock");
  p02RunDock.id = "p02-run-dock";
  p02RunDock.setAttribute("aria-label", "运行态势 · 业务面板");
  p02RunDock.hidden = true;
  const p02DockHead = element("header", "p02-dock-head");
  const p02DockTitle = element("h2", "p02-dock-title", "业务运行");
  const p02DockSource = element("span", "p02-dock-source");
  p02DockSource.setAttribute("role", "status");
  p02DockSource.hidden = true;
  p02DockHead.append(p02DockTitle, p02DockSource);
  const p02OrdersBody = element("div", "p02-orders-body");
  p02OrdersBody.setAttribute("role", "list");
  p02OrdersBody.setAttribute("aria-label", "业务订单与任务");
  const p02CargoBody = element("section", "p02-cargo-body");
  p02CargoBody.id = "p02-cargo-inspector";
  p02CargoBody.setAttribute("aria-label", "所选对象业务视图");
  p02RunDock.append(p02DockHead, p02OrdersBody, p02CargoBody);
  map.append(p02RunDock);
  const mapMessage = element("div", "map-message");
  mapMessage.setAttribute("role", "status");
  mapMessage.setAttribute("aria-live", "polite");
  const telemetryHudContainer = element("div", "telemetry-hud-container");
  append(map, mapContainer, hud, telemetryHudContainer, mapMessage);

  const right = element("aside", "rightbar");
  right.setAttribute("aria-label", t("section.overview", lang));
  const inspector = element("div", "inspector");
  inspector.hidden = true;
  const tabBar = element("div", "tab-bar");
  tabBar.setAttribute("role", "tablist");
  tabBar.setAttribute("aria-label", t("tabs.aria", lang));
  for (const tab of INFO_TABS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tab-button";
    button.textContent = t(TAB_SECTION_KEYS[tab], lang);
    button.setAttribute("role", "tab");
    button.dataset.tab = tab;
    tabBar.append(button);
  }
  const tabPanels = element("div", "tab-panels");
  append(right, inspector, tabBar, tabPanels);

  const timeline = element("footer", "timeline");
  const eventList = element("div", "event-list");
  const timelineMain = element("div", "timeline-main");
  const transport = element("div", "transport");
  transport.setAttribute("role", "group");
  transport.setAttribute("aria-label", t("aria.scrubber", lang));
  const scrubberLabel = element("div", "timeline-head");
  const scrubberWrap = element("div", "scrubber-wrap");
  const scrubberMarks = element("div", "scrubber-marks");
  scrubberMarks.setAttribute("aria-hidden", "true");
  const scrubber = document.createElement("input");
  scrubber.type = "range";
  scrubber.className = "scrubber";
  scrubber.min = "0";
  scrubber.max = "0";
  scrubber.step = "1";
  scrubber.value = "0";
  scrubber.setAttribute("aria-label", t("aria.scrubber", lang));
  scrubberWrap.append(scrubberMarks, scrubber);
  timelineMain.append(scrubberLabel, transport, scrubberWrap);
  const metricsGrid = element("div", "metric-grid");
  const metrics = element("div", "metrics");
  const metricsTitle = sectionTitle(t("section.metrics", lang));
  metricsTitle.dataset.i18n = "section.metrics";
  metrics.append(metricsTitle, metricsGrid);
  const terminalPane = element("div", "terminal-pane");
  const terminalContainer = element("div", "terminal-container");
  const terminalTitle = sectionTitle(t("terminal.title", lang));
  terminalTitle.dataset.i18n = "terminal.title";
  append(terminalPane, terminalTitle, terminalContainer);
  append(timeline, terminalPane, eventList, timelineMain, metrics);

  const contextMenu = element("div", "context-menu");
  contextMenu.setAttribute("role", "menu");
  contextMenu.setAttribute("aria-label", t("aria.contextMenu", lang));
  contextMenu.hidden = true;

  const sourceMessage = element("div", "source-message");
  sourceMessage.hidden = true;
  sourceMessage.setAttribute("role", "status");
  sourceMessage.setAttribute("aria-live", "polite");

  append(main, left, map, right);
  append(app, header, main, timeline, contextMenu, sourceMessage);
  append(root, app);
  return {
    root,
    modePill,
    connPill,
    phasePill,
    integrityPill,
    clock,
    traceSelect,
    loadTraceButton,
    refreshTracesButton,
    controlPanel,
    endpointInput,
    tokenInput,
    csrfInput,
    catalogButton,
    disconnectButton,
    catalogBody,
    startButton,
    registeredReplayButton,
    pauseButton,
    resumeButton,
    stepButton,
    stopButton,
    sessionStatus,
    left,
    entityTree,
    layerTree,
    weatherList,
    map,
    mapMessage,
    followChip,
    hudNote,
    mapStats,
    langZh,
    langEn,
    right,
    inspector,
    tabBar,
    tabPanels,
    eventList,
    transport,
    scrubber,
    scrubberMarks,
    scrubberLabel,
    metricsGrid,
    terminalContainer,
    contextMenu,
    sourceMessage,
    telemetryHudContainer,
    p02ViewSwitch,
    p02RunDock,
    p02ConfigBody,
  };
}

export function viewModeFromLocation(location: Pick<Location, "search"> = window.location): AppMode {
  return new URLSearchParams(location.search).get("view") === "replay" ? "replay" : "live";
}
