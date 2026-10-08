import * as T from 'three';
import { clone } from 'three/addons/utils/SkeletonUtils.js';
import type { EntityKey, PresentationBinding, RunHeader } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { resolveBinding, entityId } from './bindings';
import { Assets } from './assets';
import { worldOrientation, worldPosition } from './coordinates';

interface MarkerBatch { mesh: T.InstancedMesh; keys: EntityKey[]; binding: PresentationBinding }
export class EntityLayer {
  readonly root = new T.Group();
  readonly positions = new Map<string, T.Vector3>();
  readonly orientations = new Map<string, T.Quaternion>();
  private batches = new Map<PresentationBinding, MarkerBatch>();
  private models = new Map<string, T.Group>();
  private labels = new Map<string, HTMLButtonElement>();
  private trail: T.Line;
  private ring: T.Mesh;
  private dummy = new T.Object3D();
  private disposed = false;
  constructor(private header: RunHeader, private assets: Assets, private overlay: HTMLElement,
    private onSelect: (key: EntityKey) => void, private onError: (error: unknown) => void) {
    this.ring = new T.Mesh(new T.TorusGeometry(1.9, 0.09, 8, 48), new T.MeshBasicMaterial({ color: '#ffffff' }));
    this.ring.rotation.x = -Math.PI / 2; this.ring.visible = false; this.root.add(this.ring);
    this.trail = new T.Line(new T.BufferGeometry(), new T.LineBasicMaterial({ color: '#2b6b83', transparent: true, opacity: 0.65 }));
    this.root.add(this.trail);
  }
  update(store: FeedStore, ns: string, camera: T.Camera, selected?: EntityKey, showTrails = true) {
    this.positions.clear(); this.orientations.clear();
    for (const batch of this.batches.values()) { batch.keys = []; batch.mesh.count = 0; }
    for (const model of this.models.values()) model.visible = false;
    for (const label of this.labels.values()) label.hidden = true;
    for (const [id, entity] of store.entities) {
      const binding = resolveBinding(this.header, entity.typeId);
      if (!binding) continue;
      const sample = store.sample(entity.key, binding.positionField, ns);
      if (!sample) continue;
      const position = worldPosition(sample, binding.frame, this.header.origin);
      this.positions.set(id, position);
      const orientation = binding.orientationField ? store.sample(entity.key, binding.orientationField, ns, true) : undefined;
      this.dummy.position.copy(position);
      this.dummy.quaternion.copy(orientation ? worldOrientation(orientation, binding.frame) : new T.Quaternion());
      if (orientation) this.orientations.set(id, this.dummy.quaternion.clone());
      this.dummy.scale.setScalar(binding.visual.scale ?? 1);
      if (binding.visual.kind === 'marker') {
        let batch = this.batches.get(binding);
        if (!batch) {
          const geometry = new T.IcosahedronGeometry(1.15, 1);
          const material = new T.MeshStandardMaterial({ color: binding.visual.color ?? '#248faa', roughness: 0.3, metalness: 0.18 });
          const capacity = Math.max(64, store.commits.reduce((sum, commit) => sum + commit.created.length, 0));
          const mesh = new T.InstancedMesh(geometry, material, capacity);
          mesh.castShadow = true; mesh.receiveShadow = true; mesh.frustumCulled = false;
          batch = { mesh, keys: [], binding }; this.batches.set(binding, batch); this.root.add(mesh);
        }
        if (batch.keys.length >= batch.mesh.instanceMatrix.count) {
          // Live feeds can grow beyond the initial replay inventory.
          const old = batch.mesh;
          batch.mesh = new T.InstancedMesh(old.geometry, old.material, old.instanceMatrix.count * 2);
          batch.mesh.castShadow = true; batch.mesh.receiveShadow = true; batch.mesh.frustumCulled = false;
          for (let i = 0; i < batch.keys.length; i++) { const matrix = new T.Matrix4(); old.getMatrixAt(i, matrix); batch.mesh.setMatrixAt(i, matrix); }
          old.removeFromParent(); old.dispose(); this.root.add(batch.mesh);
        }
        this.dummy.updateMatrix(); batch.mesh.setMatrixAt(batch.keys.length, this.dummy.matrix);
        batch.keys.push(entity.key); batch.mesh.count = batch.keys.length; batch.mesh.instanceMatrix.needsUpdate = true;
      } else if (binding.visual.kind === 'model') {
        if (!binding.visual.asset) throw Error(`Model binding ${binding.typeId} has no asset URL`);
        let holder = this.models.get(id);
        if (!holder) {
          holder = new T.Group(); holder.userData.entity = entity.key; this.models.set(id, holder); this.root.add(holder);
          const target = holder;
          void this.assets.model(binding.visual.asset).then(model => {
            if (!this.disposed) target.add(clone(model));
          }).catch(this.onError);
        }
        holder.visible = true; holder.position.copy(position); holder.quaternion.copy(this.dummy.quaternion); holder.scale.copy(this.dummy.scale);
      }
      const isSelected = selected && entityId(selected) === id;
      if (binding.visual.kind === 'label' || isSelected || this.positions.size <= 8) {
        let label = this.labels.get(id);
        if (!label) {
          label = document.createElement('button'); label.className = 'entity-label'; label.textContent = entity.key.id;
          label.onclick = () => this.onSelect(entity.key); this.overlay.append(label); this.labels.set(id, label);
        }
        const projected = position.clone().add(new T.Vector3(0, 2.3, 0)).project(camera);
        label.hidden = projected.z < -1 || projected.z > 1 || Math.abs(projected.x) > 1 || Math.abs(projected.y) > 1;
        label.style.left = `${(projected.x + 1) * 50}%`; label.style.top = `${(1 - projected.y) * 50}%`;
        label.classList.toggle('selected', !!isSelected);
      }
    }
    const selectedId = selected ? entityId(selected) : undefined;
    const focus = selectedId ? this.positions.get(selectedId) : undefined;
    this.ring.visible = !!focus; if (focus) this.ring.position.copy(focus).add(new T.Vector3(0, -0.75, 0));
    this.trail.visible = false;
    if (showTrails && selected && focus) {
      const entity = store.entities.get(entityId(selected))!;
      const binding = resolveBinding(this.header, entity.typeId)!;
      const points: T.Vector3[] = [];
      for (let i = 40; i >= 0; i--) {
        const at = (BigInt(ns) - BigInt(i) * 150_000_000n).toString();
        const point = store.sample(selected, binding.positionField, at);
        if (!point) { if (points.length) points.length = 0; continue; }
        points.push(worldPosition(point, binding.frame, this.header.origin));
      }
      this.trail.geometry.dispose(); this.trail.geometry = new T.BufferGeometry().setFromPoints(points);
      this.trail.visible = points.length > 1;
    }
  }
  pick(raycaster: T.Raycaster): EntityKey | undefined {
    for (const hit of raycaster.intersectObject(this.root, true)) {
      for (const batch of this.batches.values()) if (hit.object === batch.mesh && hit.instanceId !== undefined) return batch.keys[hit.instanceId];
      let object: T.Object3D | null = hit.object;
      while (object) { if (object.userData.entity) return object.userData.entity; object = object.parent; }
    }
    return undefined;
  }
  dispose() { this.disposed = true; for (const label of this.labels.values()) label.remove(); }
}
