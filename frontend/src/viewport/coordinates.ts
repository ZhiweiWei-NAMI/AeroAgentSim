import { Quaternion, Vector3 } from 'three';
import { enuPosition } from './position';
import type { PresentationBinding, RunHeader } from '../contracts/viewer-feed';

/** Renderer axes: east, up, south (metres). */
export function worldPosition(value: number[], frame: PresentationBinding['frame'], origin?: RunHeader['origin']): Vector3 {
  const [east, north, up] = enuPosition(value, frame, origin);
  return new Vector3(east, up, -north);
}

export function worldOrientation(value: number[], frame: PresentationBinding['frame']): Quaternion {
  if (value.length !== 4 || !value.every(Number.isFinite) || Math.hypot(...value) === 0) throw Error('Invalid quaternion');
  const basis = frame === 'ned'
    ? new Quaternion().setFromAxisAngle(new Vector3(1, 1, -1).normalize(), 2 * Math.PI / 3)
    : new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), -Math.PI / 2);
  return basis.multiply(new Quaternion(...value as [number, number, number, number]).normalize());
}
