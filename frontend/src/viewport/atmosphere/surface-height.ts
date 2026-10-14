import * as T from 'three';

/** Exact display surface below an incident. A merged city's bounding box is only
 * a broad-phase bound: it includes the empty streets between its buildings. */
export class IncidentSurface {
  private meshes: T.Mesh[] = [];
  private bounds = new T.Box3();
  private box = new T.Box3();
  private ray = new T.Raycaster(new T.Vector3(), new T.Vector3(0, -1, 0));
  private hits: T.Intersection[] = [];
  private heights = new Map<string, number>();

  /** Refresh only when static geometry arrives or its floating origin changes. */
  setObjects(root: T.Object3D) {
    this.meshes.length = 0; this.bounds.makeEmpty(); this.heights.clear();
    root.updateWorldMatrix(true, true);
    root.traverseVisible(node => {
      if (!(node instanceof T.Mesh) || node.userData.displayDecoration) return;
      this.meshes.push(node);
      this.box.setFromObject(node); this.bounds.union(this.box);
    });
  }

  heightAt(x: number, z: number, groundY: number): number {
    const key = `${x}/${z}/${groundY}`, cached = this.heights.get(key);
    if (cached !== undefined) return cached;
    this.ray.ray.origin.set(x, Math.max(groundY, this.bounds.max.y) + 1, z);
    this.hits.length = 0;
    this.ray.intersectObjects(this.meshes, false, this.hits);
    // No city triangle below the point means the real render ground is exposed.
    const height = this.hits.length ? Math.max(groundY, this.hits[0].point.y) : groundY;
    this.heights.set(key, height);
    return height;
  }
}
