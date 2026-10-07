import * as THREE from "three";

/** Static local transforms still inherit future parent transforms and visibility changes. */
export function cacheStaticTransforms(root: THREE.Object3D,
                                      dynamic: (node: THREE.Object3D) => boolean = () => false): void {
  root.traverse(node => {
    if (dynamic(node)) return;
    node.updateMatrix();
    node.matrixAutoUpdate = false;
  });
}

/** Raycaster does not honor visibility, including a hidden object's descendants. */
export function visiblePickObjects(roots: readonly THREE.Object3D[]): THREE.Object3D[] {
  const objects: THREE.Object3D[] = [];
  for (const root of roots) root.traverseVisible(node => {
    if (node instanceof THREE.Mesh || node instanceof THREE.Line || node instanceof THREE.Sprite) {
      objects.push(node);
    }
  });
  return objects;
}

/**
 * Scene updates walk hidden subtrees too. A hidden child that composes its own matrix marks
 * itself dirty and recomputes its whole subtree whenever it is next shown, so its walk can wait.
 * Readers of a hidden child must update its matrices themselves (getWorldPosition and
 * Box3.setFromObject do) or reject it as hidden.
 */
export function skipHiddenDynamicChildren(group: THREE.Object3D): void {
  group.updateMatrixWorld = function (this: THREE.Object3D, force = false): void {
    if (this.matrixAutoUpdate) this.updateMatrix();
    if (this.matrixWorldNeedsUpdate || force) {
      if (this.matrixWorldAutoUpdate) {
        if (this.parent === null) this.matrixWorld.copy(this.matrix);
        else this.matrixWorld.multiplyMatrices(this.parent.matrixWorld, this.matrix);
      }
      this.matrixWorldNeedsUpdate = false;
      force = true;
    }
    const children = this.children;
    for (let i = 0; i < children.length; i++) {
      const child = children[i]!;
      if (!child.visible && child.matrixAutoUpdate) continue;
      child.updateMatrixWorld(force);
    }
  };
}
