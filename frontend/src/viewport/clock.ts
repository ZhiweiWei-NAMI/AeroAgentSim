import { nanos, secondsDelta } from './time';

/** update accepts monotonic elapsed wall seconds, never a simulation timestamp. */
export class PlaybackClock {
  private position: bigint;
  private start: bigint;
  private frontier: bigint;
  private anchorWall?: number;
  private anchorPosition: bigint;
  playing = false;
  speed = 1;
  constructor(start: string, end: string) {
    this.start = this.position = this.anchorPosition = nanos(start);
    this.frontier = nanos(end);
    if (this.frontier < this.start) throw Error('Playback end precedes start');
  }
  get ns() { return this.position.toString(); }
  get end() { return this.frontier.toString(); }
  set end(value: string) {
    const frontier = nanos(value);
    if (frontier < this.start) throw Error('Playback end precedes start');
    this.frontier = frontier;
    if (this.position > frontier) this.seek(value);
  }
  play() { this.playing = true; this.reanchor(); }
  pause() { this.playing = false; this.reanchor(); }
  seek(value: string) {
    const at = nanos(value);
    this.position = at < this.start ? this.start : at > this.frontier ? this.frontier : at;
    this.reanchor();
  }
  setSpeed(speed: number) {
    if (!Number.isFinite(speed) || speed <= 0) throw Error('Playback speed must be positive');
    this.speed = speed; this.reanchor();
  }
  private reanchor() { this.anchorWall = undefined; this.anchorPosition = this.position; }
  update(elapsedSeconds: number) {
    if (!Number.isFinite(elapsedSeconds)) throw Error('Wall clock must be finite');
    if (!this.playing) return this.ns;
    if (this.anchorWall === undefined || elapsedSeconds < this.anchorWall) { this.anchorWall = elapsedSeconds; this.anchorPosition = this.position; }
    const next = this.anchorPosition + secondsDelta((elapsedSeconds - this.anchorWall) * this.speed);
    this.position = next > this.frontier ? this.frontier : next;
    return this.ns;
  }
}
