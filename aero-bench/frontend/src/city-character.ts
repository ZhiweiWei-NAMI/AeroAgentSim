import * as THREE from "three";

/** Keep the proportions of a skinned person whose bind pose extends both arms sideways. */
export function fitCityCharacter(source: THREE.Object3D, height: number): THREE.Group {
  const root = new THREE.Group();
  const fit = new THREE.Group();
  fit.add(source);
  root.add(fit);
  root.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(root);
  if (bounds.isEmpty() || !Number.isFinite(height) || height <= 0) {
    throw new Error("City character has invalid geometry or height");
  }
  const sourceHeight = bounds.getSize(new THREE.Vector3()).y;
  if (sourceHeight <= 0) throw new Error("City character has no vertical extent");
  fit.scale.setScalar(height / sourceHeight);
  root.updateMatrixWorld(true);
  bounds.setFromObject(root);
  const center = bounds.getCenter(new THREE.Vector3());
  fit.position.set(-center.x, -bounds.min.y, -center.z);
  root.traverse(node => {
    if (node instanceof THREE.Mesh) { node.castShadow = true; node.receiveShadow = true; }
  });
  return root;
}
