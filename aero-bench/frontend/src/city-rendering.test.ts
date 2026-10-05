import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { cacheStaticTransforms, skipHiddenDynamicChildren, visiblePickObjects } from "./city-rendering";

describe("city render work", () => {
  it("reuses static matrices while preserving parent motion and moving light pools", () => {
    const parent = new THREE.Group(), root = new THREE.Group();
    const building = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
    const light = new THREE.PointLight();
    building.position.set(3, 2, 1);
    root.add(building, light); parent.add(root);
    cacheStaticTransforms(root, node => node === light);
    parent.matrixAutoUpdate = false;
    parent.updateMatrixWorld();
    const recompute = vi.spyOn(building, "updateMatrix");
    light.position.set(1, 5, 2);
    parent.updateMatrixWorld();
    expect(recompute).not.toHaveBeenCalled();
    expect(light.getWorldPosition(new THREE.Vector3()).toArray()).toEqual([1, 5, 2]);
    parent.position.x = 10; parent.updateMatrix(); parent.updateMatrixWorld();
    expect(building.getWorldPosition(new THREE.Vector3()).toArray()).toEqual([13, 2, 1]);
    expect(recompute).not.toHaveBeenCalled();
  });

  it("skips hidden dynamic children until shown and then matches the stock scene update", () => {
    const build = () => {
      const group = new THREE.Group(), record = new THREE.Group(), body = new THREE.Group(), lamp = new THREE.Group();
      const shown = new THREE.Group();
      record.position.set(1, 0, 2); body.position.y = 0.5; record.add(body);
      cacheStaticTransforms(body); lamp.position.z = 4; cacheStaticTransforms(lamp);
      shown.position.x = -3; group.add(record, lamp, shown);
      return { group, record, body, lamp, shown };
    };
    const tree = build(), stock = build();
    skipHiddenDynamicChildren(tree.group);
    for (const t of [tree, stock]) t.group.updateMatrixWorld();
    tree.record.visible = false; tree.lamp.visible = false;
    const bodyWorld = vi.spyOn(tree.body.matrixWorld, "multiplyMatrices");
    for (const t of [tree, stock]) { t.group.position.x = 10; t.record.position.z = 7; t.group.updateMatrixWorld(); }
    expect(bodyWorld).not.toHaveBeenCalled();
    expect(tree.shown.matrixWorld.elements).toEqual(stock.shown.matrixWorld.elements);
    expect(tree.lamp.matrixWorld.elements).toEqual(stock.lamp.matrixWorld.elements); // static: still follows the parent
    tree.record.visible = true;
    tree.group.updateMatrixWorld();
    expect(bodyWorld).toHaveBeenCalledTimes(1);
    for (const key of ["record", "body", "lamp", "shown"] as const) {
      expect(tree[key].matrixWorld.elements).toEqual(stock[key].matrixWorld.elements);
    }
  });

  it("does not pick a hidden signal or a mesh under a hidden building", () => {
    const root = new THREE.Group(), hidden = new THREE.Group();
    const near = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
    const far = near.clone();
    near.position.z = 2;
    hidden.add(near); hidden.visible = false;
    root.add(hidden, far); root.updateMatrixWorld(true);
    const ray = new THREE.Raycaster(new THREE.Vector3(0, 0, 5), new THREE.Vector3(0, 0, -1));
    expect(ray.intersectObjects(visiblePickObjects([root]), false)[0]?.object).toBe(far);
    far.visible = false;
    expect(ray.intersectObjects(visiblePickObjects([root]), false)).toHaveLength(0);
  });
});
