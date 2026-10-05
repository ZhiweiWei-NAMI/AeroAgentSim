import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parsePublicTrace, type PublicTrace } from "../trace";
import { identifier, publicRunEvent, publicTrace, sceneState, stateSample } from "../testing/trace-v3-fixture";
import { ReplayState, recordedTicks } from "./replay";

function traceWithTicks(ticks: readonly number[]): PublicTrace {
  const entityId = identifier("uav");
  const sceneStates = ticks.map((tick) =>
    sceneState(tick, [entityId], [stateSample(tick, entityId)]),
  );
  const lastTick = ticks[ticks.length - 1] ?? 1;
  return parsePublicTrace(
    publicTrace({
      time: { tick: lastTick, sim_time_ns: lastTick * 1_000_000_000 },
      scene_states: sceneStates,
      events: [],
    }),
  );
}

describe("recordedTicks", () => {
  it("unions trace time, scene-state, and event ticks in ascending order", () => {
    const trace = traceWithTicks([100, 200, 300, 400, 482]);
    expect(recordedTicks(trace)).toEqual([100, 200, 300, 400, 482]);
  });

  it("includes the authoritative tick even when nothing else records it", () => {
    const trace = parsePublicTrace(
      publicTrace({
        events: [],
        scene_states: [],
        time: { tick: 482, sim_time_ns: 482_000_000_000 },
      }),
    );
    expect(recordedTicks(trace)).toEqual([482]);
  });
});

describe("ReplayState", () => {
  let state: ReplayState;

  beforeEach(() => {
    state = new ReplayState();
  });

  afterEach(() => {
    state.dispose();
  });

  it("starts and restarts on the first physical frame while keeping initialization events seekable", () => {
    const trace = traceWithTicks([1, 2]);
    trace.events = [publicRunEvent(0, { at: { tick: 0, sim_time_ns: 0 } })] as unknown as PublicTrace["events"];
    state.setTrace(trace);
    expect(state.recorded()).toEqual([0, 1, 2]);
    expect(state.current()).toBe(1);
    state.goToTick(0);
    expect(state.current()).toBe(0);
    state.first();
    expect(state.current()).toBe(1);
    state.last();
    state.play();
    expect(state.current()).toBe(1);
  });

  it("starts at the first recorded tick of a fresh document", () => {
    const trace = traceWithTicks([100, 200, 300, 400, 482]);
    state.setTrace(trace);
    expect(state.recorded()).toEqual([100, 200, 300, 400, 482]);
    expect(state.current()).toBe(100);
    expect(state.isAuthoritative()).toBe(false);

    state.goToTick(300);
    expect(state.current()).toBe(300);
    expect(state.isAuthoritative()).toBe(false);
    state.last();
    expect(state.isAuthoritative()).toBe(true);
  });

  it("snaps only to recorded ticks", () => {
    state.setTrace(traceWithTicks([100, 200, 300, 400, 482]));
    state.goToTick(301);
    expect(state.current()).toBe(100);

    state.goToTick(200);
    expect(state.current()).toBe(200);
  });

  it("steps and clamps within the recorded range", () => {
    state.setTrace(traceWithTicks([100, 200, 300, 400, 482]));
    state.first();
    expect(state.current()).toBe(100);
    state.step(-1);
    expect(state.current()).toBe(100);
    state.step(1);
    expect(state.current()).toBe(200);
    state.last();
    expect(state.current()).toBe(482);
    expect(state.currentIndex()).toBe(4);
    state.step(1);
    expect(state.current()).toBe(482);
  });

  it("keeps the cursor tick when a new snapshot still records it, otherwise clamps", () => {
    state.setTrace(traceWithTicks([100, 200, 300, 400, 482]));
    state.goToTick(300);

    state.setTrace(traceWithTicks([100, 200, 300, 400, 482]));
    expect(state.current()).toBe(300);

    const shrunk = parsePublicTrace(
      publicTrace({
        events: [],
        scene_states: [sceneState(150, [identifier("uav")], [stateSample(150, identifier("uav"))])],
        time: { tick: 150, sim_time_ns: 150_000_000_000 },
      }),
    );
    state.setTrace(shrunk);
    expect(state.recorded()).toEqual([150]);
    expect(state.current()).toBe(150);
  });

  it("uses real irregular simulation intervals and holds the terminal frame before looping", () => {
    vi.useFakeTimers();
    try {
      state.setTrace(traceWithTicks([1, 2, 4]));
      state.play();
      vi.advanceTimersByTime(900);
      expect(state.current()).toBe(1);
      vi.advanceTimersByTime(100);
      expect(state.current()).toBe(2);
      vi.advanceTimersByTime(1900);
      expect(state.current()).toBe(2);
      vi.advanceTimersByTime(100);
      expect(state.current()).toBe(4);
      vi.advanceTimersByTime(1900);
      expect(state.current()).toBe(4);
      vi.advanceTimersByTime(100);
      expect(state.current()).toBe(1);
    } finally { vi.useRealTimers(); }
  });

  it("samples at 32x without emitting skipped ticks and keeps every frame seekable", () => {
    vi.useFakeTimers();
    try {
      const ticks = Array.from({ length: 50 }, (_, i) => i + 1);
      state.setTrace(traceWithTicks(ticks));
      state.setSpeed(32);
      const shown: number[] = [];
      state.subscribe(() => { shown.push(state.current()!); });
      state.play();
      vi.advanceTimersByTime(100);
      expect(state.current()).toBe(4);
      vi.advanceTimersByTime(900);
      expect(state.current()).toBe(33);
      expect(shown).not.toContain(2);
      expect(shown).not.toContain(3);
      expect(state.recorded()).toEqual(ticks);
      state.pause();
      state.goToTick(2);
      expect(state.current()).toBe(2);
    } finally { vi.useRealTimers(); }
  });

  it("preserves fractional elapsed time across speed changes and pause/resume", () => {
    vi.useFakeTimers();
    try {
      state.setTrace(traceWithTicks([1, 2, 3, 4, 5]));
      state.play();
      vi.advanceTimersByTime(500);
      state.setSpeed(2);
      vi.advanceTimersByTime(300);
      expect(state.current()).toBe(2);
      state.pause();
      vi.advanceTimersByTime(10000);
      expect(state.current()).toBe(2);
      state.play();
      vi.advanceTimersByTime(400);
      expect(state.current()).toBe(2);
      vi.advanceTimersByTime(100);
      expect(state.current()).toBe(3);
      state.goToTick(1);
      vi.advanceTimersByTime(500);
      expect(state.current()).toBe(2);
    } finally { vi.useRealTimers(); }
  });

  it("rejects unsupported speeds and a live timeline with backward simulation time", () => {
    expect(() => state.setSpeed(3 as never)).toThrow("Unsupported replay speed");
    const states = [sceneState(1, [], []), { ...sceneState(2, [], []), at: { tick: 2, sim_time_ns: 0 } }];
    expect(() => state.setSceneStates(states as never)).toThrow("nondecreasing recorded simulation times");
  });
});
