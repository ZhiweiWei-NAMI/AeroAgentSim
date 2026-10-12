import { expect, it } from 'vitest';
import * as T from 'three';
import { Viewport, FOLLOW_CAMERA_OFFSET, GROUND_CAMERA_OFFSET, followCameraOffset } from './viewport';
import { StaticOcclusion } from './occlusion';

it('keeps rooftops and surrounding streets in view when following a high aircraft', () => {
  const focus = new T.Vector3(0, 145, 0);
  const camera = new T.PerspectiveCamera(48, 1.25, 0.15, 16000);
  camera.position.copy(focus).add(new T.Vector3(...FOLLOW_CAMERA_OFFSET));
  camera.lookAt(focus); camera.updateMatrixWorld();
  for (const point of [new T.Vector3(0, 70, 0), new T.Vector3(-80, 0, -80)]) {
    const projected = point.clone().project(camera);
    expect(Math.abs(projected.x)).toBeLessThan(1);
    expect(Math.abs(projected.y)).toBeLessThan(1);
    expect(projected.z).toBeGreaterThan(-1); expect(projected.z).toBeLessThan(1);
  }
  const topRay = new T.Vector3(0, 1, 0).unproject(camera).sub(camera.position);
  expect(topRay.y).toBeLessThan(0); // The top of the view points at the city, too.
});

it('keeps nearby buildings and streets visible when following a grounded drone', () => {
  const focus = new T.Vector3(0, 0, 0), offset = followCameraOffset(0);
  const camera = new T.PerspectiveCamera(48, 1.25, 0.15, 16000);
  camera.position.copy(focus).add(offset); camera.lookAt(focus); camera.updateMatrixWorld();
  expect(offset.y).toBeGreaterThanOrEqual(100);
  expect(Math.hypot(offset.x, offset.z)).toBeGreaterThan(100);
  for (const point of [new T.Vector3(-30, 0, -30), new T.Vector3(30, 0, 30), new T.Vector3(0, 35, 0)]) {
    const projected = point.clone().project(camera);
    expect(Math.abs(projected.x)).toBeLessThan(1);
    expect(Math.abs(projected.y)).toBeLessThan(1);
    expect(projected.z).toBeGreaterThan(-1); expect(projected.z).toBeLessThan(1);
  }
  expect(followCameraOffset(100).toArray()).toEqual([...FOLLOW_CAMERA_OFFSET]);
  // A recorded takeoff/landing changes framing continuously, without a mode jump.
  expect(followCameraOffset(99.99).distanceTo(followCameraOffset(100))).toBeLessThan(.01);
});

it('retains a minimum oblique focus distance even for a small framed object', () => {
  const camera = new T.PerspectiveCamera(48, 1.25, 0.15, 16000), controls = { target: new T.Vector3(), enabled: true };
  const viewport = Object.assign(Object.create(Viewport.prototype), {
    camera, controls, container: document.createElement('div'), origin: new T.Vector3(), occlusion: new StaticOcclusion(), revision: 0,
  }) as Viewport;
  viewport.focusPosition([10, 20, 0], 1, true);
  expect(controls.target.toArray()).toEqual([10, 0, -20]);
  expect(camera.position.clone().sub(controls.target).toArray()).toEqual([...GROUND_CAMERA_OFFSET]);
  viewport.focusPosition([10, 20, 0], 300, true);
  expect(camera.position.distanceTo(controls.target)).toBeGreaterThan(300);
});
