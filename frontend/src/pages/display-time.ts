/** Display-only rounding. The temporal store retains exact nanoseconds. */
export function displayTime(ns: string, origin = '0'): string {
  const delta = BigInt(ns) - BigInt(origin), absolute = delta < 0n ? -delta : delta;
  const tenths = (absolute + 50_000_000n) / 100_000_000n;
  return `${delta < 0n ? '−' : ''}${(tenths / 600n).toString().padStart(2,'0')}:${(tenths % 600n / 10n).toString().padStart(2,'0')}.${tenths % 10n}`;
}
