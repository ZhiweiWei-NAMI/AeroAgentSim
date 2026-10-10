import { beforeEach, describe, expect, it } from "vitest";
import { TerminalDock, type TerminalDockSource } from "./terminal-dock";
import { MAX_LINES_PER_CHANNEL } from "./event-channels";
import { publicRunEvent, runTransitionStreamEvent } from "./testing/trace-v3-fixture";
import { initLanguage, t } from "./i18n";
import type { PublicRunEvent, RunExecutionTransition } from "./generated/aero-bench-contracts";

function event(overrides: Record<string, unknown> = {}): PublicRunEvent {
  return publicRunEvent(0, overrides) as unknown as PublicRunEvent;
}

function transition(sequence: number, phase: string): RunExecutionTransition {
  return (runTransitionStreamEvent(sequence, phase) as unknown as {
    transition: RunExecutionTransition;
  }).transition;
}

describe("TerminalDock", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    initLanguage();
  });

  it("renders an explicit not-provided state for channels without records", () => {
    const dock = new TerminalDock(document.createElement("div"));
    dock.update({ events: [], transitions: [], verifierReport: null });
    const text = dock["log"].textContent ?? "";
    expect(text).toContain(t("terminal.eventsEmpty"));
    dock.dispose();
  });

  it("shows not-provided for the MAVLink channel while public records never carry it", () => {
    const dock = new TerminalDock(document.createElement("div"));
    const events = [event()];
    dock.update({ events, transitions: [], verifierReport: null });
    dock["active"] = "mavlink";
    dock.update({ events, transitions: [], verifierReport: null });
    const text = dock["log"].textContent ?? "";
    expect(text).toContain(t("terminal.notProvided"));
    dock.dispose();
  });

  it("appends advancing replay lines without rebuilding retained rows, and rebuilds on seek back", () => {
    const dock = new TerminalDock(document.createElement("div"));
    const all = Array.from({ length: 5 }, (_, index) => publicRunEvent(index, {
      event_id: `event.dock-${index}`, at: { tick: index + 1, sim_time_ns: (index + 1) * 500_000_000 },
    }) as unknown as PublicRunEvent);
    const rows = () => Array.from(dock["log"].children);
    dock.update({ events: all.slice(0, 2), transitions: [], verifierReport: null, availableOnly: true });
    const first = rows();
    expect(first).toHaveLength(2);
    dock.update({ events: all.slice(0, 4), transitions: [], verifierReport: null, availableOnly: true });
    expect(rows()).toHaveLength(4);
    expect(rows().slice(0, 2)).toEqual(first);
    dock.update({ events: all.slice(0, 1), transitions: [], verifierReport: null, availableOnly: true });
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).not.toBe(first[0]);
    expect(rows()[0]?.textContent).toBe(first[0]?.textContent);
    dock.dispose();
  });

  it("keeps the newest bounded window when replay drops the oldest lines", () => {
    const dock = new TerminalDock(document.createElement("div"));
    const all = Array.from({ length: MAX_LINES_PER_CHANNEL + 3 }, (_, index) => publicRunEvent(index, {
      event_id: `event.window-${index}`, at: { tick: index + 1, sim_time_ns: (index + 1) * 500_000_000 },
    }) as unknown as PublicRunEvent);
    dock.update({ events: all.slice(0, MAX_LINES_PER_CHANNEL), transitions: [], verifierReport: null, availableOnly: true });
    const retained = Array.from(dock["log"].children).slice(3);
    dock.update({ events: all, transitions: [], verifierReport: null, availableOnly: true });
    const rows = Array.from(dock["log"].children);
    expect(rows).toHaveLength(MAX_LINES_PER_CHANNEL);
    expect(rows.slice(0, MAX_LINES_PER_CHANNEL - 3)).toEqual(retained);
    expect(rows.at(-1)?.textContent).toContain(`${MAX_LINES_PER_CHANNEL + 3} ·`);
    dock.dispose();
  });

  it("routes events into their channels and the events channel", () => {
    const dock = new TerminalDock(document.createElement("div"));
    const events = [event()];
    dock.update({ events, transitions: [], verifierReport: null });
    dock["active"] = "agent";
    dock.update({ events, transitions: [], verifierReport: null });
    const text = dock["log"].textContent ?? "";
    expect(text).toContain("agent.decision_summary.v1");
    expect(text).not.toContain(t("terminal.notProvided"));
    dock.dispose();
  });

  it("renders declared transitions and verifier report lines into their channels", () => {
    const dock = new TerminalDock(document.createElement("div"));
    dock.update({
      events: [],
      transitions: [transition(1, "running")],
      verifierReport: null,
    });
    dock["active"] = "events";
    dock.update({
      events: [],
      transitions: [transition(1, "running")],
      verifierReport: null,
    });
    expect(dock["log"].textContent).toContain("phase=running");
    dock.dispose();
  });

  it("keeps every node text-only", () => {
    const dock = new TerminalDock(document.createElement("div"));
    const events = [event()];
    dock.update({ events, transitions: [], verifierReport: null });
    const dockElement = dock.container;
    expect(dockElement.querySelector("script")).toBeNull();
    expect(dockElement.querySelectorAll("input,textarea,button[type='text']")).toHaveLength(0);
    dock.dispose();
  });

  it("uses the declared source identity for the source", () => {
    const dock = new TerminalDock(document.createElement("div"));
    const source: TerminalDockSource = { events: [event()], transitions: [], verifierReport: null };
    dock.update(source);
    dock.update(source);
    dock.dispose();
  });
});
