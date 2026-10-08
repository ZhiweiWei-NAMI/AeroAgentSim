import { fraction, nanos } from './time';

interface Sample { ns: bigint; index: number; value?: number[] }
export function slerp(a: number[], b: number[], amount: number): number[] | undefined {
  if (a.length !== 4 || b.length !== 4) return undefined;
  const la = Math.hypot(...a), lb = Math.hypot(...b);
  if (la === 0 || lb === 0) return undefined;
  const left = a.map(value => value / la), right = b.map(value => value / lb);
  let dot = left.reduce((sum, value, index) => sum + value * right[index], 0);
  if (dot < 0) { dot = -dot; for (let i = 0; i < 4; i++) right[i] = -right[i]; }
  if (dot > 0.9995) {
    const result = left.map((value, i) => value + (right[i] - value) * amount);
    const length = Math.hypot(...result); return result.map(value => value / length);
  }
  const angle = Math.acos(Math.min(1, dot)), denominator = Math.sin(angle);
  return left.map((value, i) => (value * Math.sin((1 - amount) * angle) + right[i] * Math.sin(amount * angle)) / denominator);
}

/** Append ordered available samples; a tombstone is a discontinuity/invalidity barrier. */
export class SampleBuffer {
  private events: Sample[] = [];
  push(ns: string, index: number, value: unknown) {
    const numeric = Array.isArray(value) && value.length > 0 && value.every(component => typeof component === 'number' && Number.isFinite(component));
    this.events.push({ ns: nanos(ns), index, value: numeric ? [...value] : undefined });
  }
  retract(ns: string, index: number) { this.events.push({ ns: nanos(ns), index }); }
  sample(ns: string, quaternion = false, equalTimeCut?: number): number[] | undefined {
    const at = nanos(ns);
    let low = 0, high = this.events.length;
    while (low < high) {
      const mid = (low + high) >>> 1, event = this.events[mid];
      if (event.ns < at || (event.ns === at && (equalTimeCut === undefined || event.index <= equalTimeCut))) low = mid + 1;
      else high = mid;
    }
    const left = this.events[low - 1], right = this.events[low];
    if (!left?.value) return undefined;
    if (quaternion && (left.value.length !== 4 || Math.hypot(...left.value) === 0)) return undefined;
    if (quaternion && right?.value && (right.value.length !== 4 || Math.hypot(...right.value) === 0)) return [...left.value];
    if (!right?.value || at === left.ns || right.ns === left.ns) return [...left.value];
    if (left.value.length !== right.value.length) return [...left.value];
    const alpha = fraction(at, left.ns, right.ns);
    return quaternion ? slerp(left.value, right.value, alpha) : left.value.map((value, i) => value + (right.value![i] - value) * alpha);
  }
}
