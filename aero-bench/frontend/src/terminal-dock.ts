import {
  TERMINAL_CHANNEL_IDS,
  ChannelBuffer,
  channelOfEvent,
  eventLine,
  transitionLine,
  type TerminalChannelId,
} from "./event-channels";
import type { PublicRunEvent, PublicVerificationReport, RunExecutionTransition } from "./generated/aero-bench-contracts";
import { currentLanguage, t, type I18nKey } from "./i18n";
import { formatSimTime, shortDigest } from "./view-model";

export interface TerminalDockSource {
  readonly availableOnly?: boolean;
  /** Authorized public events, ascending sequence (live buffer or sealed trace). */
  readonly events: readonly PublicRunEvent[];
  /** Declared run transitions (live stream); empty in sealed replay. */
  readonly transitions: readonly RunExecutionTransition[];
  /** The sealed public verifier report, when the trace carries one. */
  readonly verifierReport: PublicVerificationReport | null;
}

const CHANNEL_LABEL_KEYS: Readonly<Record<TerminalChannelId, I18nKey>> = {
  agent: "terminal.channel.agent",
  io: "terminal.channel.io",
  px4: "terminal.channel.px4",
  mavlink: "terminal.channel.mavlink",
  ns3: "terminal.channel.ns3",
  sumo: "terminal.channel.sumo",
  verifier: "terminal.channel.verifier",
  events: "terminal.channel.events",
};

/**
 * Channels whose records never exist in the authorized public
 * vocabulary unless a matching declared event actually arrives. When a
 * channel holds no records the dock renders an explicit
 * "not provided by control stream" state instead of fixture text.
 */
const NOT_PROVIDED_KEYS: Readonly<Record<TerminalChannelId, I18nKey>> = {
  agent: "terminal.notProvided",
  io: "terminal.notProvided",
  px4: "terminal.notProvided",
  mavlink: "terminal.notProvided",
  ns3: "terminal.notProvided",
  sumo: "terminal.notProvided",
  verifier: "terminal.notProvided",
  events: "terminal.eventsEmpty",
};

function element(tag: string, className?: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  if (className !== undefined) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

/**
 * Read-only terminal dock. It renders only actual public stream or
 * sealed-trace records, line composition is deterministic from declared
 * fields, every node is text-only (no HTML injection path), and the
 * dock offers no input of any kind.
 */
export class TerminalDock {
  readonly container: HTMLElement;
  private readonly tabs = new Map<TerminalChannelId, HTMLButtonElement>();
  private readonly buffers = new Map<TerminalChannelId, ChannelBuffer>();
  private readonly log: HTMLElement;
  private active: TerminalChannelId = "events";
  private lastSource: TerminalDockSource | null = null;
  /** Keys and owner of the rows currently in the log, for incremental replay updates. */
  private rendered: { readonly channel: TerminalChannelId; readonly lang: string; readonly keys: string[] } | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    this.container.classList.add("terminal-dock");
    for (const id of TERMINAL_CHANNEL_IDS) {
      this.buffers.set(id, new ChannelBuffer());
    }
    const tabBar = element("div", "terminal-tabs");
    tabBar.setAttribute("role", "tablist");
    tabBar.setAttribute("aria-label", t("terminal.aria"));
    for (const id of TERMINAL_CHANNEL_IDS) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "terminal-tab";
      button.textContent = t(CHANNEL_LABEL_KEYS[id]);
      button.setAttribute("role", "tab");
      button.dataset.channel = id;
      button.addEventListener("click", () => {
        this.active = id;
        this.renderActive();
      });
      this.tabs.set(id, button);
      tabBar.append(button);
    }
    this.log = element("div", "terminal-log");
    this.log.setAttribute("role", "log");
    this.log.setAttribute("aria-readonly", "true");
    this.log.setAttribute("aria-label", t("terminal.aria"));
    this.container.replaceChildren(tabBar, this.log);
    this.renderActive();
  }

  /** Recompute the bounded channel buffers from the source records. */
  update(source: TerminalDockSource): void {
    if (source === this.lastSource) {
      this.renderActive();
      return;
    }
    this.lastSource = source;
    for (const buffer of this.buffers.values()) {
      buffer.clear();
    }
    const eventBuffer = this.buffers.get("events");
    for (const transition of source.transitions) {
      eventBuffer?.append(transitionLine(transition));
    }
    for (const event of source.events) {
      const line = eventLine(event);
      this.buffers.get("events")?.append(line);
      this.buffers.get(channelOfEvent(event))?.append(line);
    }
    const verifier = this.buffers.get("verifier");
    if (verifier !== undefined && source.verifierReport !== null) {
      verifier.append(this.verifierLine(source.verifierReport));
      for (const goal of source.verifierReport.goals) {
        verifier.append({
          key: `verifier.goal.${goal.goal_id}`,
          tick: 0,
          simTimeNs: 0,
          tone: goal.passed ? "ok" : "bad",
          text: `goal=${goal.goal_id} passed=${String(goal.passed)}${
            goal.failure_class === null ? "" : ` failure=${goal.failure_class}`
          } metrics=${goal.metrics.length}`,
        });
      }
    }
    this.renderActive();
  }

  private verifierLine(report: PublicVerificationReport) {
    return {
      key: `verifier.${report.run_id}`,
      tick: 0,
      simTimeNs: 0,
      tone: report.status === "passed" ? ("ok" as const) : ("bad" as const),
      text: `verifier=${shortDigest(report.run_id)} status=${report.status} coverage=${String(report.coverage_complete)} goals=${report.goals.length}`,
    };
  }

  private renderActive(): void {
    const lang = currentLanguage();
    const availableOnly = this.lastSource?.availableOnly === true;
    if (availableOnly && (this.buffers.get(this.active)?.size ?? 0) === 0) {
      this.active = TERMINAL_CHANNEL_IDS.find(id => (this.buffers.get(id)?.size ?? 0) > 0) ?? "events";
    }
    for (const [id, button] of this.tabs) {
      const active = id === this.active;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      const count = this.buffers.get(id)?.size ?? 0;
      button.hidden = availableOnly && count === 0;
      const label = t(CHANNEL_LABEL_KEYS[id], lang);
      button.textContent = count > 0 ? `${label} · ${count}` : label;
    }
    const buffer = this.buffers.get(this.active);
    if (buffer === undefined || buffer.size === 0) {
      this.rendered = null;
      this.log.replaceChildren();
      if (availableOnly) return;
      this.log.append(
        element("div", "terminal-line terminal-muted", t(NOT_PROVIDED_KEYS[this.active], lang)),
      );
      return;
    }
    const lines = buffer.snapshot;
    const keys = lines.map(line => line.key);
    // Replay advances by appending lines and dropping the oldest beyond the bound. Keep the rows
    // that remain and only add new ones; anything else (seek back, channel or language change)
    // rebuilds the log.
    const previous = this.rendered?.channel === this.active && this.rendered.lang === lang ? this.rendered.keys : null;
    const dropped = previous === null ? -1 : previous.indexOf(keys[0]!);
    const retained = dropped < 0 ? 0 : previous!.length - dropped;
    const incremental = dropped >= 0 && retained <= keys.length
      && previous!.every((key, index) => index < dropped || key === keys[index - dropped]);
    if (incremental && dropped === 0 && retained === keys.length) return;
    if (incremental) {
      for (let index = 0; index < dropped; index++) this.log.firstElementChild?.remove();
    } else {
      this.log.replaceChildren();
    }
    for (const line of lines.slice(incremental ? retained : 0)) {
      const row = element("div", `terminal-line tone-${line.tone}`);
      const time = element("span", "terminal-time", `${line.tick} · ${formatSimTime(line.simTimeNs)}`);
      time.setAttribute("aria-hidden", "true");
      row.append(time, element("span", "terminal-text", line.text));
      this.log.append(row);
    }
    this.rendered = { channel: this.active, lang, keys };
    this.log.scrollTop = this.log.scrollHeight;
  }

  dispose(): void {
    this.rendered = null;
    this.container.replaceChildren();
    this.buffers.clear();
    this.tabs.clear();
    this.lastSource = null;
  }
}
