import * as T from 'three';
import { expect, it, vi } from 'vitest';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { IncidentSurface } from './surface-height';

it('uses real roofs and leaves gaps between merged buildings at render-ground height', () => {
  const geometry = mergeGeometries([
    new T.BoxGeometry(10, 40, 10).translate(-20, 20, 0),
    new T.BoxGeometry(10, 120, 10).translate(20, 60, 0),
  ]);
  const root = new T.Group(), city = new T.Mesh(geometry, new T.MeshBasicMaterial());
  root.add(city);
  const surface = new IncidentSurface(); surface.setObjects(root);
  expect(surface.heightAt(-20, 0, -0.1)).toBeCloseTo(40);
  expect(surface.heightAt(20, 0, -0.1)).toBeCloseTo(120);
  // All three points lie inside the same merged bounding box.
  expect(surface.heightAt(0, 0, -0.1)).toBe(-0.1);
  const raycast = vi.spyOn(city, 'raycast');
  surface.heightAt(0, 0, -0.1); surface.heightAt(-20, 0, -0.1);
  expect(raycast).not.toHaveBeenCalled();
  // Streaming/rebasing invalidates the cached heights and uses current transforms.
  root.position.set(-2000, -10, 2000); surface.setObjects(root);
  expect(surface.heightAt(-2020, 2000, -10.1)).toBeCloseTo(30);
  expect(surface.heightAt(-2000, 2000, -10.1)).toBe(-10.1);
  expect(raycast).toHaveBeenCalled();
  geometry.dispose(); city.material.dispose();
});

it('uses the exposed render ground when no city geometry is present', () => {
  const surface = new IncidentSurface(); surface.setObjects(new T.Group());
  expect(surface.heightAt(17, 32, -0.1)).toBe(-0.1);
});
