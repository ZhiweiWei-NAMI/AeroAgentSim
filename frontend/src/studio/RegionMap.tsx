import type { KeyboardEvent, PointerEvent } from 'react';
import { useId, useRef } from 'react';
import type { CityScene } from './api';
import { useI18n } from '../i18n/I18nProvider';

export function RegionMap({ source, bounds, onChange, scene }: { source: number[]; bounds: number[]; onChange: (b: number[]) => void; scene?: CityScene }) {
  const { t } = useI18n();
  const anchor = useRef<number[]>();
  const hintId = useId();
  const valid = (b: number[]) => b.length === 4 && b.every(Number.isFinite) && b[0] < b[2] && b[1] < b[3];
  if (!valid(source) || !valid(bounds)) return <p role="alert">{t('The region map needs valid west, south, east and north bounds.')}</p>;
  const point = (event: PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    if (!box.width || !box.height) return undefined;
    const x = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    const y = Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
    return [source[0] + x * (source[2] - source[0]), source[3] - y * (source[3] - source[1])];
  };
  const xy = ([lon, lat]: number[]) => [(lon - source[0]) / (source[2] - source[0]) * 600, (source[3] - lat) / (source[3] - source[1]) * 220];
  const [x, y] = xy([bounds[0], bounds[3]]), [x2, y2] = xy([bounds[2], bounds[1]]);
  const keyboard = (event: KeyboardEvent<SVGSVGElement>) => {
    if (event.key === 'Escape') { anchor.current = undefined; return; }
    if (event.key === 'Home') { event.preventDefault(); onChange([...source]); return; }
    const horizontal = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
    const vertical = event.key === 'ArrowDown' ? -1 : event.key === 'ArrowUp' ? 1 : 0;
    if (!horizontal && !vertical) return;
    event.preventDefault();
    const dx = horizontal * (source[2] - source[0]) / 100, dy = vertical * (source[3] - source[1]) / 100;
    const next = [...bounds];
    if (event.shiftKey) {
      next[2] = Math.max(next[0] + (source[2] - source[0]) / 1000, Math.min(source[2], next[2] + dx));
      next[3] = Math.max(next[1] + (source[3] - source[1]) / 1000, Math.min(source[3], next[3] + dy));
    } else {
      const east = Math.max(source[0] - bounds[0], Math.min(source[2] - bounds[2], dx));
      const north = Math.max(source[1] - bounds[1], Math.min(source[3] - bounds[3], dy));
      next[0] += east; next[2] += east; next[1] += north; next[3] += north;
    }
    onChange(next);
  };
  return <>
    <p id={hintId}>{t('Drag a rectangle to select a region. Keyboard: arrow keys move it, Shift + arrows resize it, and Home selects the whole extract.')}</p>
    <svg className="studio-region-map" viewBox="0 0 600 220" preserveAspectRatio="none" role="img" tabIndex={0} aria-label={t('Region selector map')} aria-describedby={hintId} onKeyDown={keyboard}
      onPointerDown={event => { if (event.button !== 0) return; anchor.current = point(event); if (anchor.current) event.currentTarget.setPointerCapture?.(event.pointerId); }}
      onPointerCancel={() => { anchor.current = undefined; }} onLostPointerCapture={() => { anchor.current = undefined; }}
      onPointerUp={event => {
        if (!anchor.current) return;
        const end = point(event), start = anchor.current; anchor.current = undefined;
        if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        if (end && Math.abs(end[0] - start[0]) > 1e-8 && Math.abs(end[1] - start[1]) > 1e-8) onChange([Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])]);
      }}>
      <title>{t('Region selector map')}</title>
      <rect width="600" height="220" fill="#edf2ec" />
      {scene?.geojson?.features.map((feature: any, i) => {
        if (feature.geometry?.type === 'LineString') return <polyline key={i} points={feature.geometry.coordinates.map((c: number[]) => xy(c).join(',')).join(' ')} fill="none" stroke="#a2adb7" />;
        if (feature.geometry?.type === 'Polygon') return <polygon key={i} points={feature.geometry.coordinates[0].map((c: number[]) => xy(c).join(',')).join(' ')} fill="#c6cdd1" stroke="#9fa9b2" strokeWidth="0.5" />;
        return null;
      })}
      <rect x={x} y={y} width={x2 - x} height={y2 - y} fill="#1677ff22" stroke="#1677ff" strokeWidth="2" />
      <text x="12" y="20" fill="#4a596a">{t('WGS84 · drag to select')}</text>
      <text x="12" y="207" fill="#4a596a">{t('© OpenStreetMap contributors · ODbL')}</text>
    </svg>
  </>;
}
