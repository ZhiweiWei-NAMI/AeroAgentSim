import { Quaternion, Vector3 } from 'three';
import type { PresentationBinding, RunHeader } from '../contracts/viewer-feed';

/** Renderer axes: east, up, south (metres). Geodetic values: lat, lon, alt. */
export function worldPosition(value: number[], frame: PresentationBinding['frame'], origin?: RunHeader['origin']): Vector3 {
  if (value.length !== 3 || !value.every(Number.isFinite)) throw Error('Position must be a finite 3-vector');
  const [a, b, c] = value;
  if (frame === 'enu') return new Vector3(a, c, -b);
  if (frame === 'ned') return new Vector3(b, -c, -a);
  if (!origin) throw Error('WGS84 presentation requires RunHeader.origin');
  if (Math.abs(a) >= 90 || Math.abs(b) > 180) throw Error('Invalid WGS84 position');
  const radius = 6378137, rad = Math.PI / 180;
  const ecef = (lat: number, lon: number, alt: number) => {
    const phi = lat * rad, lam = lon * rad, e2 = 6.69437999014e-3;
    const n = radius / Math.sqrt(1 - e2 * Math.sin(phi) ** 2);
    return new Vector3((n + alt) * Math.cos(phi) * Math.cos(lam), (n + alt) * Math.cos(phi) * Math.sin(lam), (n * (1 - e2) + alt) * Math.sin(phi));
  };
  const d = ecef(a, b, c).sub(ecef(origin.lat, origin.lon, origin.alt));
  const phi = origin.lat * rad, lam = origin.lon * rad;
  const east = -Math.sin(lam) * d.x + Math.cos(lam) * d.y;
  const north = -Math.sin(phi) * Math.cos(lam) * d.x - Math.sin(phi) * Math.sin(lam) * d.y + Math.cos(phi) * d.z;
  const up = Math.cos(phi) * Math.cos(lam) * d.x + Math.cos(phi) * Math.sin(lam) * d.y + Math.sin(phi) * d.z;
  return new Vector3(east, up, -north);
}

export function worldOrientation(value: number[], frame: PresentationBinding['frame']): Quaternion {
  if (value.length !== 4 || !value.every(Number.isFinite) || Math.hypot(...value) === 0) throw Error('Invalid quaternion');
  const basis = frame === 'ned'
    ? new Quaternion().setFromAxisAngle(new Vector3(1, 1, -1).normalize(), 2 * Math.PI / 3)
    : new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), -Math.PI / 2);
  return basis.multiply(new Quaternion(...value as [number, number, number, number]).normalize());
}
