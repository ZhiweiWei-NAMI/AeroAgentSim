import type { Instant } from '../contracts/viewer-feed';

export function nanos(value: string): bigint {
  if (!/^(0|[1-9]\d*|-[1-9]\d*)$/.test(value)) throw Error(`Noncanonical nanoseconds: ${value}`);
  const result = BigInt(value);
  if (result < -(1n << 63n) || result >= (1n << 63n)) throw Error('Nanoseconds exceed int64');
  return result;
}
export function compareInstant(a: Instant, b: Instant): number {
  const delta = nanos(a.ns) - nanos(b.ns);
  return delta < 0n ? -1 : delta > 0n ? 1 : Math.sign(a.microstep - b.microstep);
}
/** Subtract before conversion: epoch magnitude never reduces interpolation precision. */
export function fraction(at: bigint, left: bigint, right: bigint): number {
  if (right <= left) throw Error('Interpolation requires a positive interval');
  if (at <= left) return 0; if (at >= right) return 1;
  return Number((at - left) * (1n << 53n) / (right - left)) / 2 ** 53;
}
export function secondsDelta(seconds: number): bigint {
  if (!Number.isFinite(seconds)) throw Error('Elapsed seconds must be finite');
  const whole = Math.trunc(seconds);
  return BigInt(whole) * 1_000_000_000n + BigInt(Math.round((seconds - whole) * 1e9));
}
