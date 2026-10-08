import type { PointerEvent } from 'react';
import { useRef } from 'react';
import type { CityScene } from './api';

export function RegionMap({ source, bounds, onChange, scene }: { source: number[]; bounds: number[]; onChange: (b: number[]) => void; scene?: CityScene }) {
  const anchor = useRef<number[]>();
  const point = (event: PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    const y = Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
    return [source[0] + x * (source[2] - source[0]), source[3] - y * (source[3] - source[1])];
  };
  const xy = ([lon, lat]: number[]) => [(lon - source[0]) / (source[2] - source[0]) * 600, (source[3] - lat) / (source[3] - source[1]) * 220];
  const [x, y] = xy([bounds[0], bounds[3]]), [x2, y2] = xy([bounds[2], bounds[1]]);
  return <svg className="studio-region-map" viewBox="0 0 600 220" preserveAspectRatio="none" role="img" aria-label="Region selector map"
    onPointerDown={e => { anchor.current = point(e); e.currentTarget.setPointerCapture(e.pointerId); }}
    onPointerUp={e => { if (!anchor.current) return; const end = point(e), start = anchor.current; anchor.current = undefined;
      if (Math.abs(end[0] - start[0]) > 1e-8 && Math.abs(end[1] - start[1]) > 1e-8) onChange([Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])]); }}>
    <rect width="600" height="220" fill="#edf2ec" />
    {scene?.geojson.features.map((feature: any, i) => {
      if (feature.geometry.type === 'LineString') return <polyline key={i} points={feature.geometry.coordinates.map((c: number[]) => xy(c).join(',')).join(' ')} fill="none" stroke="#a2adb7" />;
      if (feature.geometry.type === 'Polygon') return <polygon key={i} points={feature.geometry.coordinates[0].map((c: number[]) => xy(c).join(',')).join(' ')} fill="#c6cdd1" stroke="#9fa9b2" strokeWidth="0.5" />;
      return null;
    })}
    <rect x={x} y={y} width={x2 - x} height={y2 - y} fill="#1677ff22" stroke="#1677ff" strokeWidth="2" />
    <text x="12" y="20" fill="#4a596a">WGS84 · drag to select / 拖动选区</text>
    <text x="12" y="207" fill="#4a596a">© OpenStreetMap contributors · ODbL</text>
  </svg>;
}
