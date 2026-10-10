import type { PublicTrace, SceneState } from "../trace";
import { recordedTicks, simTimeAtTick } from "../view-model";
import { Observable, type Unsubscribe } from "./observable";

export const PLAY_SPEEDS = [1, 2, 4, 8, 16, 32] as const;
export type PlaySpeed = (typeof PLAY_SPEEDS)[number];
const PRESENTATION_INTERVAL_MS = 100;
export { recordedTicks };

/** Sample the recorded simulation timeline at 10 Hz; never fabricate intermediate states. */
export class ReplayState {
  private ticks: readonly number[] = [];
  private timesMs: readonly number[] = [];
  private index = -1;
  private firstFrameIndex = 0;
  private playheadMs = 0;
  private anchorSimMs = 0;
  private anchorWallMs = 0;
  private terminalShownAt: number | null = null;
  private readonly playing = new Observable(false);
  private readonly speed = new Observable<PlaySpeed>(1);
  private readonly changes = new Observable<null>(null);
  private timer: ReturnType<typeof setTimeout> | null = null;

  setSceneStates(states: readonly SceneState[]): void {
    this.setTimeline(states.map(state => state.at.tick), states.map(state => state.at.sim_time_ns), 0);
  }

  clear(): void { this.pause(); this.setTimeline([], [], 0); }

  setTrace(trace: PublicTrace): void {
    const ticks = recordedTicks(trace);
    const times = ticks.map(tick => {
      const time = simTimeAtTick(trace, tick);
      if (time === null) throw new Error(`Replay tick ${tick} has no recorded simulation time`);
      return time;
    });
    const firstFrame = trace.scene_states.length ? ticks.indexOf(trace.scene_states[0]!.at.tick) : 0;
    this.setTimeline(ticks, times, firstFrame);
  }

  private setTimeline(ticks: readonly number[], timesNs: readonly number[], firstFrame: number): void {
    for (let i = 0; i < ticks.length; i++) {
      if (!Number.isSafeInteger(timesNs[i]) || timesNs[i]! < 0 ||
          (i > 0 && (ticks[i]! <= ticks[i - 1]! || timesNs[i]! < timesNs[i - 1]!))) {
        throw new Error("Replay requires ordered ticks and nondecreasing recorded simulation times");
      }
    }
    const previous = this.current();
    this.ticks = [...ticks];
    this.timesMs = timesNs.map(time => time / 1_000_000);
    this.firstFrameIndex = firstFrame;
    this.index = !ticks.length ? -1 : previous === null ? firstFrame : this.clampIndexFor(previous);
    this.playheadMs = this.timesMs[this.index] ?? 0;
    this.reanchor();
    this.changes.set(null);
  }

  recorded(): readonly number[] { return this.ticks; }
  current(): number | null { return this.ticks[this.index] ?? null; }
  currentIndex(): number { return this.index; }
  isAuthoritative(): boolean { return this.index >= 0 && this.index === this.ticks.length - 1; }
  step(delta: number): void { this.goToIndex(this.index + delta); }
  first(): void { this.goToIndex(this.firstFrameIndex); }
  last(): void { this.goToIndex(this.ticks.length - 1); }
  goToTick(tick: number): void { const index = this.ticks.indexOf(tick); if (index !== -1) this.goToIndex(index); }
  seek(index: number): void { this.goToIndex(index); }

  private clampIndexFor(tick: number): number {
    let result = 0;
    for (const [index, value] of this.ticks.entries()) { if (value > tick) break; result = index; }
    return result;
  }

  private goToIndex(index: number): void {
    if (!this.ticks.length) return;
    const next = Math.max(0, Math.min(index, this.ticks.length - 1));
    this.playheadMs = this.timesMs[next]!;
    this.reanchor();
    this.present(next);
  }

  private present(index: number): void {
    if (index === this.index) return;
    this.index = index;
    this.changes.set(null);
  }

  isPlaying(): boolean { return this.playing.value; }
  pause(): void {
    if (this.playing.value) this.advance(performance.now());
    this.stopTimer();
    this.playing.set(false);
  }
  togglePlay(): void { if (this.playing.value) this.pause(); else this.play(); }
  play(): void {
    if (!this.ticks.length || this.playing.value) return;
    if (this.index >= this.ticks.length - 1) this.first();
    this.reanchor();
    this.playing.set(true);
    this.schedule();
  }
  speedValue(): PlaySpeed { return this.speed.value; }
  setSpeed(speed: PlaySpeed): void {
    if (!PLAY_SPEEDS.includes(speed)) throw new Error(`Unsupported replay speed: ${speed}`);
    if (this.playing.value) this.advance(performance.now());
    this.speed.set(speed);
    this.reanchor();
  }
  subscribe(listener: () => void): Unsubscribe {
    const unsubs = [this.changes.subscribe(listener), this.playing.subscribe(listener), this.speed.subscribe(listener)];
    return () => unsubs.forEach(unsub => unsub());
  }

  private reanchor(): void {
    this.anchorSimMs = this.playheadMs;
    this.anchorWallMs = performance.now();
    this.terminalShownAt = null;
  }

  private advance(now: number): void {
    if (!this.ticks.length) return;
    const last = this.timesMs.length - 1;
    // Keep the terminal physical state visible before looping, even at 32x.
    if (this.terminalShownAt !== null) {
      const finalInterval = last > 0 ? (this.timesMs[last]! - this.timesMs[last - 1]!) / this.speed.value : 0;
      if (now - this.terminalShownAt >= Math.max(PRESENTATION_INTERVAL_MS, finalInterval)) this.first();
      return;
    }
    this.playheadMs = Math.min(this.timesMs[last]!, this.anchorSimMs + (now - this.anchorWallMs) * this.speed.value);
    let low = 0, high = last;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (this.timesMs[middle]! <= this.playheadMs) low = middle; else high = middle - 1;
    }
    if (low === last) this.terminalShownAt = now;
    this.present(low);
  }

  private schedule(): void {
    this.stopTimer();
    if (!this.playing.value || !this.ticks.length) return;
    this.timer = setTimeout(() => { this.advance(performance.now()); this.schedule(); }, PRESENTATION_INTERVAL_MS);
  }
  private stopTimer(): void { if (this.timer !== null) { clearTimeout(this.timer); this.timer = null; } }
  dispose(): void { this.stopTimer(); }
}
