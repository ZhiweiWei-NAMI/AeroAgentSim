import { awaitRunConfiguration, type RunsApi, isTerminalStatus, waitingContext } from '../feeds/http';
import { stringifyLossless } from '../feeds/lossless-json';
import { mapping } from '../behaviours/model';
import { exactNanos, operatorTiming } from './operator-timing';

export interface OperatorSourceOptions {
  acknowledged?: string;
  onReceipt(ns: string): void;
  onError(error?: string): void;
}
export interface OperatorSourceControl {
  hold(): Promise<void>;
  resume(): void;
  dispose(): void;
}

/** One source algorithm, executed in its dedicated browser worker. */
export class OperatorSource implements OperatorSourceControl {
  private abort = new AbortController();
  private read?: AbortController;
  private timer?: ReturnType<typeof setTimeout>;
  private writing?: Promise<unknown>;
  private held = false;
  private generation = 0;
  private ready = false;
  private terminal = false;
  private step!: bigint;
  private end!: bigint;
  private acknowledged?: bigint;
  private lastPoll = performance.now();
  private credit = 0;
  private speedRead = -Infinity;
  private factor?: number | null;
  constructor(private api: Pick<RunsApi, 'request'>, private runId: string, private options: OperatorSourceOptions) {
    if (options.acknowledged !== undefined) this.acknowledged = exactNanos(options.acknowledged, 'Acknowledged operator watermark');
  }
  async start() {
    try {
      const configuration = await awaitRunConfiguration(this.api as RunsApi, this.runId, this.abort.signal);
      if (!mapping(configuration) || !mapping(configuration.scenario)) throw Error('Run configuration is malformed');
      const scenario = configuration.scenario;
      const stream = Array.isArray(scenario.ingress_streams) && scenario.ingress_streams.find(row => mapping(row) && row.id === 'operator');
      if (!stream || this.abort.signal.aborted) return;
      const { step, initial } = operatorTiming(scenario);
      this.step = step; this.end = exactNanos(mapping(scenario.run) ? scenario.run.until_ns : undefined, 'Run limit');
      if (initial >= this.end) { this.terminal = true; return; }
      this.ready = true; this.lastPoll = performance.now(); this.kick();
    } catch (problem) { if (!this.abort.signal.aborted) this.options.onError(String(problem)); }
  }
  async hold() {
    this.held = true; this.generation++; clearTimeout(this.timer); this.read?.abort(); this.read = undefined;
    // Informational reads never block a control. An already issued write must
    // settle before admission/pause/stop; its failure is surfaced by poll().
    if (this.writing) await Promise.allSettled([this.writing]);
  }
  resume() { this.held = false; this.generation++; this.speedRead = -Infinity; this.kick(); }
  dispose() { this.abort.abort(); this.read?.abort(); clearTimeout(this.timer); }
  private kick() {
    if (this.ready && !this.read && !this.writing && !this.held && !this.terminal && !this.abort.signal.aborted) void this.poll();
  }
  private async poll() {
    const generation = this.generation, read = new AbortController(); this.read = read;
    let writing: Promise<unknown> | undefined;
    let delay = 250, failed = false;
    try {
      const path = `/v1/runs/${encodeURIComponent(this.runId)}`;
      // The input boundary belongs to the current run, never to a recorded cut.
      // Reading the manifest also avoids repeatedly projecting the first commit.
      const [progress, state] = await Promise.all([
        this.api.request(`${path}/inputs`, { signal: read.signal, cache: 'no-store' }),
        this.api.request(path, { signal: read.signal, cache: 'no-store' }),
      ]);
      if (generation !== this.generation || this.abort.signal.aborted) return;
      if (!mapping(progress) || typeof progress.status !== 'string' || typeof progress.pause_requested !== 'boolean' || !mapping(progress.watermarks)) throw Error('Input source progress is malformed');
      if (!mapping(state) || typeof state.status !== 'string') throw Error('Input wait metadata is malformed');
      this.terminal = isTerminalStatus(progress.status) || isTerminalStatus(state.status);
      const published = exactNanos(progress.watermarks.operator, 'Operator source watermark');
      const current = this.acknowledged !== undefined && this.acknowledged > published ? this.acknowledged : published;
      const now = performance.now(), elapsed = Math.max(0, now - this.lastPoll); this.lastPoll = now;
      if (this.terminal || current >= this.end) { this.terminal = true; return; }
      if (progress.pause_requested || !['running', 'waiting_for_input'].includes(progress.status) || !['running', 'waiting_for_input'].includes(state.status)) { this.credit = 0; delay = 1000; return; }
      if (this.factor === undefined || now - this.speedRead >= 1000) {
        const speed = await this.api.request(`${path}/speed`, { signal: read.signal, cache: 'no-store' });
        if (generation !== this.generation || this.abort.signal.aborted) return;
        if (!mapping(speed) || speed.factor !== null && (typeof speed.factor !== 'number' || !Number.isFinite(speed.factor) || speed.factor <= 0)) throw Error('Simulation speed is malformed');
        this.factor = speed.factor as number | null; this.speedRead = now;
      }
      this.credit = this.factor === null ? Number(this.step) : Math.min(Number(this.step), this.credit + elapsed * 1_000_000 * this.factor);
      delay = this.factor === null ? 20 : Math.max(20, Math.min(100, Number(this.step) / 1_000_000 / this.factor));
      if (state.status !== 'waiting_for_input') return;
      const waiting = waitingContext(state.waiting);
      if (!waiting) throw Error('Input wait metadata is missing');
      if (!waiting.stream_ids.includes('operator')) return;
      const requested = exactNanos(waiting.at_ns, 'Requested input boundary');
      const next = [current + this.step, requested, this.end].reduce((a, b) => a < b ? a : b);
      if (next <= current) return;
      const cost = Number(next - current);
      if (this.factor !== null && this.credit < cost) { delay = Math.max(20, Math.min(1000, (cost - this.credit) / 1_000_000 / this.factor)); return; }
      if (generation !== this.generation || this.held || this.abort.signal.aborted) return;
      this.writing = writing = this.api.request(`${path}/watermark`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: this.abort.signal,
        body: stringifyLossless({ stream_id: 'operator', watermark_ns: { $integer: next.toString() }, expected_watermark_ns: { $integer: current.toString() } }),
      });
      const receipt = await writing;
      if (!mapping(receipt)) throw Error('Operator watermark receipt mismatch');
      const actual = exactNanos(receipt.watermark_ns, 'Operator source progress');
      if (receipt.advanced === false ? actual <= current : actual !== next) throw Error('Operator watermark receipt mismatch');
      this.acknowledged = actual; this.options.onReceipt(actual.toString()); this.terminal = actual >= this.end;
      if (receipt.advanced !== false) this.credit = Math.max(0, this.credit - cost);
      delay = this.factor === null || this.credit > 0 ? 0 : 20;
      this.options.onError(undefined);
    } catch (problem) {
      failed = true;
      if ((writing !== undefined || !read.signal.aborted) && !this.abort.signal.aborted) { this.options.onError(String(problem)); delay = 1000; }
    } finally {
      // A cancelled read may settle after resume has started a fresh poll. It
      // must neither clear that poll nor keep it blocked until the old read ends.
      if (this.writing === writing) this.writing = undefined;
      if (this.read === read) this.read = undefined;
      if (!failed && generation === this.generation && !read.signal.aborted && !this.abort.signal.aborted) this.options.onError(undefined);
      if (!this.abort.signal.aborted && !this.terminal && !this.held) {
        if (generation !== this.generation) { if (writing !== undefined) this.kick(); }
        else if (delay === 0) this.kick();
        else this.timer = setTimeout(() => this.kick(), delay);
      }
    }
  }
}
