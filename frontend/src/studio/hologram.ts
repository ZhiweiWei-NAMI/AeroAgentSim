import type { PreviewOverlay } from './plan-overlay';

export type HologramMode = 'blueprint' | 'sweep' | 'real';
export const SWEEP_DURATION_MS = 1600;

export function sweepRadius(elapsedMs: number, durationMs: number, maxRadius: number): number {
  const progress = durationMs <= 0 ? 1 : Math.max(0, Math.min(1, elapsedMs / durationMs));
  return maxRadius * (1 - Math.pow(1 - progress, 3));
}

export function hologramMode(overlay: PreviewOverlay | undefined, playedKeys: ReadonlySet<string>): HologramMode {
  if (overlay?.tone === 'proposed') return 'blueprint';
  return overlay?.reveal && !playedKeys.has(overlay.reveal.key) ? 'sweep' : 'real';
}
