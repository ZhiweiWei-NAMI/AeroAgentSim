import type { PresentationBinding, RunHeader } from '../contracts/viewer-feed';

/** Convert a recorded position to east/north/up metres without loading Three.js. */
export function enuPosition(value: number[], frame: PresentationBinding['frame'], origin?: RunHeader['origin']): [number, number, number] {
  if (value.length !== 3 || !value.every(Number.isFinite)) throw Error('Position must be a finite 3-vector');
  const [a, b, c] = value;
  if (frame === 'enu') return [a, b, c];
  if (frame === 'ned') return [b, a, -c];
  if (!origin) throw Error('WGS84 presentation requires RunHeader.origin');
  if (Math.abs(a) >= 90 || Math.abs(b) > 180) throw Error('Invalid WGS84 position');
  const radius = 6378137, rad = Math.PI / 180;
  const ecef = (lat: number, lon: number, alt: number) => {
    const phi = lat * rad, lam = lon * rad, e2 = 6.69437999014e-3;
    const n = radius / Math.sqrt(1 - e2 * Math.sin(phi) ** 2);
    return [(n + alt) * Math.cos(phi) * Math.cos(lam), (n + alt) * Math.cos(phi) * Math.sin(lam), (n * (1 - e2) + alt) * Math.sin(phi)];
  };
  const point = ecef(a, b, c), base = ecef(origin.lat, origin.lon, origin.alt);
  const dx = point[0] - base[0], dy = point[1] - base[1], dz = point[2] - base[2];
  const phi = origin.lat * rad, lam = origin.lon * rad;
  const east = -Math.sin(lam) * dx + Math.cos(lam) * dy;
  const north = -Math.sin(phi) * Math.cos(lam) * dx - Math.sin(phi) * Math.sin(lam) * dy + Math.cos(phi) * dz;
  const up = Math.cos(phi) * Math.cos(lam) * dx + Math.cos(phi) * Math.sin(lam) * dy + Math.sin(phi) * dz;
  return [east, north, up];
}

