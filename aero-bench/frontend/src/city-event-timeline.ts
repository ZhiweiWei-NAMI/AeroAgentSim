import type { CityWorkspaceConfig } from "./city-workspace-config";

export type CityTimelineEvent = CityWorkspaceConfig["events"][number];
export type CityEventTimelineStatus = "past" | "current" | "scheduled";

export interface CityEventTimelineEntry {
  readonly event: CityTimelineEvent;
  readonly status: CityEventTimelineStatus;
}

export interface CityEventTimelineSnapshot {
  readonly timeS: number;
  /** Latest scheduled instant at or before the playhead. Null before the first event. */
  readonly currentAtS: number | null;
  readonly entries: readonly CityEventTimelineEntry[];
}

export interface CityEventTimelineHandle {
  update(events: readonly CityTimelineEvent[], timeS: number): void;
  dispose(): void;
}

const STATUS_LABEL: Readonly<Record<CityEventTimelineStatus, string>> = {
  past: "已越过（预览）",
  current: "当前计划点（预览）",
  scheduled: "待到达（预览）",
};

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function compareEvents(left: CityTimelineEvent, right: CityTimelineEvent): number {
  return left.atS - right.atS
    || compareText(left.id, right.id)
    || compareText(left.type, right.type)
    || compareText(left.targetId, right.targetId);
}

function validateInput(events: readonly CityTimelineEvent[], timeS: number): void {
  if (!Number.isFinite(timeS) || timeS < 0) {
    throw new RangeError("Event timeline time must be finite and non-negative");
  }
  const ids = new Set<string>();
  for (const event of events) {
    if (!event.id || ids.has(event.id) || !Number.isFinite(event.atS) || event.atS < 0) {
      throw new Error(`Event timeline input is invalid: ${event.id || "?"}`);
    }
    ids.add(event.id);
  }
}

/** Classify authored schedule rows at one preview playhead. `current` means the
 * latest scheduled instant already reached, not that a Provider accepted or
 * applied the event. Equal-time rows remain one deterministic current group. */
export function cityEventTimelineSnapshot(
  events: readonly CityTimelineEvent[], timeS: number,
): CityEventTimelineSnapshot {
  validateInput(events, timeS);
  const ordered = [...events].sort(compareEvents);
  let currentAtS: number | null = null;
  for (const event of ordered) {
    if (event.atS <= timeS) currentAtS = event.atS;
    else break;
  }
  return {
    timeS,
    currentAtS,
    entries: ordered.map(event => ({ event, status: event.atS > timeS ? "scheduled"
      : event.atS === currentAtS ? "current" : "past" })),
  };
}

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K, className: string, text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clock(seconds: number): string {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => compareText(left, right))
      .map(([key, entry]) => `${JSON.stringify(key)}:${stableJson(entry)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function render(root: HTMLElement, snapshot: CityEventTimelineSnapshot): void {
  const panel = element("section", "studio-card studio-event-timeline");
  panel.dataset.role = "event-timeline";
  panel.setAttribute("aria-label", "计划事件预览时间线");
  panel.append(element("h3", "", "计划事件时间线"));
  const note = element("p", "studio-note",
    "仅显示草稿的计划时刻。当前与已越过状态只跟随预览播放头，不表示 Provider 已接收、执行或产生正式遥测。");
  note.dataset.role = "event-timeline-disclaimer";
  panel.append(note);

  const counts = { past: 0, current: 0, scheduled: 0 };
  for (const entry of snapshot.entries) counts[entry.status]++;
  const summary = element("p", "studio-note",
    `播放头 ${clock(snapshot.timeS)} · 已越过 ${counts.past} · 当前 ${counts.current} · 待到达 ${counts.scheduled}`);
  summary.dataset.role = "event-timeline-summary";
  panel.append(summary);

  if (snapshot.entries.length === 0) {
    panel.append(element("p", "studio-empty", "当前草稿没有计划事件。"));
    root.replaceChildren(panel);
    return;
  }

  const list = element("ol", "studio-grid");
  list.dataset.role = "event-timeline-entries";
  for (const { event, status } of snapshot.entries) {
    const item = element("li", "studio-row");
    item.dataset.eventId = event.id;
    item.dataset.timelineStatus = status;
    const identity = element("span", "");
    identity.append(element("strong", "", `${clock(event.atS)} · ${event.type}`),
      element("span", "studio-note", `目标 ${event.targetId} · ${event.id}`),
      element("span", "studio-note", `负载 ${stableJson(event.payload)}`));
    const state = element("span", "studio-save-status", STATUS_LABEL[status]);
    state.dataset.state = status === "past" ? "saved" : status === "current" ? "dirty" : "";
    state.dataset.role = "event-timeline-status";
    item.append(identity, state);
    list.append(item);
  }
  panel.append(list);
  root.replaceChildren(panel);
}

/** Render and update the schedule beside the existing authoring playhead. The
 * caller owns the clock; this module does not advance time or trigger effects. */
export function renderCityEventTimeline(
  root: HTMLElement, events: readonly CityTimelineEvent[], timeS: number,
): CityEventTimelineHandle {
  let disposed = false;
  const update = (nextEvents: readonly CityTimelineEvent[], nextTimeS: number): void => {
    if (disposed) return;
    render(root, cityEventTimelineSnapshot(nextEvents, nextTimeS));
  };
  update(events, timeS);
  return {
    update,
    dispose: () => {
      if (disposed) return;
      disposed = true;
      root.replaceChildren();
    },
  };
}
