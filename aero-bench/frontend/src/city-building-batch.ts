import * as THREE from "three";
import { DEPTH_FIELDS, registerCityShadowPass, renderShadowPrograms, type DepthSource } from "./city-shadow-pass";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { BuildingRenderBlock } from "./city-building-renders";
import type { TraceTarget } from "./state/target";

const WINDOW_BATCH_HOOKS = new WeakSet<THREE.Material["onBeforeCompile"]>();

interface Part {
  owner: THREE.Object3D;
  source: THREE.Mesh;
  materialIndex: number;
  stateKey: string;
  geometry: THREE.BufferGeometry;
  start: number;
  count: number;
  visible: boolean;
  bucket: Bucket;
  caster: Bucket | null;
  shadowStart: number;
}
interface Bucket {
  key: string;
  mesh: THREE.Mesh;
  parts: Part[];
  dirty: boolean;
  blockIndex: number;
  source: THREE.Mesh;
  depthState: unknown[];
  shadowKey: string | null;
}

const DEPTH_IDENTITIES = new WeakMap<object, number>();
// Buckets share about 30 materials, so depth inputs are read once per material per flush.
interface DepthRecord { values: unknown[]; epoch: number }
let nextDepthIdentity = 0;
function depthIdentity(value: unknown): unknown {
  if (value === null || typeof value !== "object") return value;
  let id = DEPTH_IDENTITIES.get(value);
  if (id === undefined) { id = ++nextDepthIdentity; DEPTH_IDENTITIES.set(value, id); }
  return id;
}
function positionOnly(material: DepthSource, source: THREE.Mesh): boolean {
  return source.customDepthMaterial === undefined && source.customDistanceMaterial === undefined
    && source.onBeforeShadow === THREE.Object3D.prototype.onBeforeShadow
    && source.onAfterShadow === THREE.Object3D.prototype.onAfterShadow
    && !((material.alphaTest > 0 || material.alphaToCoverage === true) && (material.map || material.alphaMap))
    && !(material.displacementMap && material.displacementScale !== 0);
}

/** Static colour surfaces by material identity; shadow surfaces by depth state. */
export class BuildingRenderBatches {
  readonly group = new THREE.Group();
  private readonly buckets = new Map<string, Bucket>();
  readonly shadowGroup = new THREE.Group();
  private readonly casters = new Map<string, Bucket>();
  private readonly parts: Part[] = [];
  private readonly shadowHooks = new Map<THREE.WebGLRenderer, () => void>();
  private readonly depthRecords = new WeakMap<THREE.Material, DepthRecord>();
  private depthEpoch = 0;
  private flushing = false;
  private warmingShadows = false;

  constructor(private readonly root: THREE.Group, private readonly blocks: readonly BuildingRenderBlock[]) {
    this.group.name = "Streamed building batches";
    this.group.userData.buildingRenderBatches = true;
    this.shadowGroup.name = "Streamed building shadow casters";
    this.shadowGroup.userData.buildingRenderBatches = true;
    // Both groups stay at their parent's origin; their batch meshes have static matrices.
    for (const group of [this.group, this.shadowGroup]) { group.updateMatrix(); group.matrixAutoUpdate = false; }
    const updateMatrixWorld = this.group.updateMatrixWorld.bind(this.group);
    // Reflection captures temporarily restore source materials before rendering.
    // Resolve those identities before Three builds either pass's render list.
    this.group.updateMatrixWorld = force => {
      this.flush();
      updateMatrixWorld(force);
    };
  }

  add(owner: THREE.Object3D, blockIndex: number): void {
    const block = this.blocks[blockIndex]!;
    const origin = new THREE.Vector3((block.min_e + block.max_e) / 2, 0,
      -(block.min_n + block.max_n) / 2);
    this.root.updateWorldMatrix(true, true);
    const inverse = this.root.matrixWorld.clone().invert();
    const meshes: THREE.Mesh[] = [];
    for (const child of owner.children) child.traverseVisible(node => {
      if (node instanceof THREE.Mesh) meshes.push(node);
    });
    for (const mesh of meshes) {
      const geometry = mesh.geometry;
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      const count = geometry.index?.count ?? geometry.getAttribute("position").count;
      const groups = Array.isArray(mesh.material) ? geometry.groups
        : [{ start: 0, count, materialIndex: 0 }];
      const transform = new THREE.Matrix4().makeTranslation(-origin.x, -origin.y, -origin.z)
        .multiply(inverse).multiply(mesh.matrixWorld);
      if (mesh instanceof THREE.SkinnedMesh || mesh instanceof THREE.InstancedMesh
          || Object.keys(geometry.morphAttributes).length > 0 || transform.determinant() <= 0
          || geometry.drawRange.start !== 0 || geometry.drawRange.count !== Infinity) {
        throw new Error(`Building batch requires static complete surfaces: ${owner.name}`);
      }
      let end = 0;
      for (const group of [...groups].sort((a, b) => a.start - b.start)) {
        if (group.start !== end || group.count <= 0 || group.count % 3 !== 0
            || materials[group.materialIndex ?? 0] === undefined) {
          throw new Error(`Building batch has invalid material groups: ${owner.name}`);
        }
        end += group.count;
      }
      if (end !== count || materials.some(material => material.transparent)) {
        throw new Error(`Building batch requires opaque partitioned surfaces: ${owner.name}`);
      }
      const layout = Object.keys(geometry.attributes).sort().map(name => {
        const attribute = geometry.getAttribute(name);
        return [name, attribute.itemSize, attribute.normalized, attribute.array.constructor.name,
          attribute instanceof THREE.BufferAttribute ? attribute.gpuType : null];
      });
      for (const group of groups) {
        const material = materials[group.materialIndex ?? 0]!;
        const stateKey = JSON.stringify([blockIndex, mesh.castShadow, mesh.receiveShadow,
          mesh.layers.mask, mesh.renderOrder, mesh.frustumCulled, layout,
          mesh.customDepthMaterial !== undefined || mesh.customDistanceMaterial !== undefined
            || mesh.onBeforeShadow !== THREE.Object3D.prototype.onBeforeShadow
            || mesh.onAfterShadow !== THREE.Object3D.prototype.onAfterShadow ? mesh.uuid : null]);
        const bucket = this.bucket(stateKey, material, mesh, origin, blockIndex);
        const fragment = geometry.index === null ? geometry.clone() : geometry.toNonIndexed();
        for (const name of Object.keys(fragment.attributes)) {
          const attribute = fragment.getAttribute(name) as THREE.BufferAttribute;
          const sliced = new THREE.BufferAttribute(attribute.array.slice(group.start * attribute.itemSize,
            (group.start + group.count) * attribute.itemSize), attribute.itemSize, attribute.normalized);
          sliced.gpuType = attribute.gpuType;
          fragment.setAttribute(name, sliced);
        }
        fragment.clearGroups(); fragment.applyMatrix4(transform);
        const modelOrigin = new THREE.Vector3().setFromMatrixPosition(transform);
        const origins = new Float32Array(group.count * 3);
        for (let i = 0; i < group.count; i++) modelOrigin.toArray(origins, i * 3);
        fragment.setAttribute("cityBuildingOrigin", new THREE.BufferAttribute(origins, 3));
        const part: Part = { owner, source: mesh, materialIndex: group.materialIndex ?? 0,
          stateKey, geometry: fragment, start: 0, count: group.count, visible: owner.visible,
          bucket, caster: null, shadowStart: 0 };
        bucket.parts.push(part); this.parts.push(part);
        bucket.dirty = true;
      }
      // Existing calibration and reflection consumers use instanceof Mesh. Three's
      // renderer uses isMesh, so the source remains a material/bounds proxy only.
      Reflect.set(mesh, "isMesh", false);
      mesh.raycast = () => {};
    }
  }

  private preserveWindowSeed(material: THREE.Material): void {
    if (material.userData.cityWindowLights !== true || WINDOW_BATCH_HOOKS.has(material.onBeforeCompile)) return;
    const hook = material.onBeforeCompile, key = material.customProgramCacheKey();
    material.onBeforeCompile = (shader, renderer) => {
      hook.call(material, shader, renderer);
      shader.vertexShader = shader.vertexShader
        .replace("#include <common>", "#include <common>\nattribute vec3 cityBuildingOrigin;")
        .replace("modelMatrix[3].xz", "(modelMatrix * vec4(cityBuildingOrigin, 1.0)).xz");
    };
    WINDOW_BATCH_HOOKS.add(material.onBeforeCompile);
    material.customProgramCacheKey = () => `${key}:building-batch-origin-v1`;
    material.needsUpdate = true;
  }

  private bucket(stateKey: string, material: THREE.Material, source: THREE.Mesh,
    origin: THREE.Vector3, blockIndex: number): Bucket {
    const key = `${stateKey}:${material.uuid}`;
    let bucket = this.buckets.get(key);
    if (bucket === undefined) {
      const mesh = new THREE.Mesh(new THREE.BufferGeometry(), material);
      mesh.name = `Building batch ${stateKey.split(",")[0]!.slice(1)}: ${material.name}`;
      mesh.position.copy(origin); mesh.updateMatrix(); mesh.matrixAutoUpdate = false;
      mesh.castShadow = source.castShadow; mesh.receiveShadow = source.receiveShadow;
      mesh.layers.mask = source.layers.mask; mesh.renderOrder = source.renderOrder;
      mesh.frustumCulled = source.frustumCulled; mesh.visible = false;
      mesh.customDepthMaterial = source.customDepthMaterial;
      mesh.customDistanceMaterial = source.customDistanceMaterial;
      mesh.onBeforeShadow = source.onBeforeShadow; mesh.onAfterShadow = source.onAfterShadow;
      bucket = { key, mesh, parts: [], dirty: true, source, blockIndex, depthState: [], shadowKey: null };
      this.updateDepthState(bucket); this.preserveWindowSeed(material);
      this.buckets.set(key, bucket);
      if (this.group.parent === null) this.root.add(this.group, this.shadowGroup);
      this.group.add(mesh);
    }
    return bucket;
  }

  /** The material's depth inputs; a new array identity exactly when one of them changed. */
  private depthValues(material: DepthSource): unknown[] {
    let record = this.depthRecords.get(material);
    if (record === undefined) {
      record = { values: DEPTH_FIELDS.map(field => material[field]), epoch: this.depthEpoch };
      this.depthRecords.set(material, record);
    } else if (record.epoch !== this.depthEpoch) {
      record.epoch = this.depthEpoch;
      for (let i = 0; i < DEPTH_FIELDS.length; i++) {
        if (record.values[i] !== material[DEPTH_FIELDS[i]!]) {
          record.values = DEPTH_FIELDS.map(field => material[field]);
          break;
        }
      }
    }
    return record.values;
  }

  private updateDepthState(bucket: Bucket): void {
    const material = bucket.mesh.material as DepthSource;
    const values = this.depthValues(material);
    if (bucket.depthState === values) return;
    bucket.depthState = values;
    // A colour map without alpha testing cannot affect the depth result.
    const state = bucket.depthState.map((value, i) => depthIdentity(
      DEPTH_FIELDS[i] === "map" && material.alphaTest === 0 && !material.alphaToCoverage ? null : value));
    bucket.shadowKey = positionOnly(material, bucket.source)
      && (bucket.source.castShadow || bucket.source.receiveShadow)
      ? JSON.stringify([bucket.blockIndex, bucket.source.layers.mask, bucket.source.frustumCulled,
        bucket.source.castShadow, bucket.source.receiveShadow, state]) : null;
  }

  private caster(bucket: Bucket): Bucket {
    const key = bucket.shadowKey!;
    let caster = this.casters.get(key);
    if (caster === undefined) {
      const mesh = new THREE.Mesh(new THREE.BufferGeometry(), bucket.mesh.material);
      mesh.name = `Building shadow caster ${bucket.blockIndex}`;
      mesh.userData.buildingShadowCaster = true;
      mesh.position.copy(bucket.mesh.position); mesh.updateMatrix(); mesh.matrixAutoUpdate = false;
      mesh.layers.mask = bucket.mesh.layers.mask; mesh.frustumCulled = bucket.mesh.frustumCulled;
      mesh.castShadow = bucket.source.castShadow; mesh.receiveShadow = bucket.source.receiveShadow;
      mesh.visible = false;
      // Raycaster deliberately ignores Object3D.visible.
      mesh.raycast = () => {};
      caster = { ...bucket, key, mesh, parts: [], dirty: true };
      this.casters.set(key, caster); this.shadowGroup.add(mesh);
    }
    return caster;
  }

  private rebuild(bucket: Bucket, shadow: boolean): void {
    const fragments = bucket.parts.map(part => shadow
      ? new THREE.BufferGeometry().setAttribute("position", part.geometry.getAttribute("position"))
      : part.geometry);
    const geometry = mergeGeometries(fragments);
    if (geometry === null) throw new Error(`Building batch cannot merge ${bucket.mesh.name}`);
    bucket.mesh.geometry.dispose(); bucket.mesh.geometry = geometry;
    let start = 0;
    for (const part of bucket.parts) {
      if (shadow) part.shadowStart = start; else part.start = start;
      start += part.count;
    }
    const indices: number[] = [];
    const ranges: { end: number; target: TraceTarget }[] = [];
    const bounds = new THREE.Box3();
    for (const part of bucket.parts) {
      if (!part.visible) continue;
      const first = shadow ? part.shadowStart : part.start;
      for (let i = 0; i < part.count; i++) indices.push(first + i);
      ranges.push({ end: indices.length / 3, target: part.owner.userData.target as TraceTarget });
      if (part.geometry.boundingBox === null) part.geometry.computeBoundingBox();
      bounds.union(part.geometry.boundingBox!);
    }
    geometry.setIndex(indices); geometry.boundingBox = bounds;
    geometry.boundingSphere = bounds.isEmpty() ? new THREE.Sphere(new THREE.Vector3(), 0)
      : bounds.getBoundingSphere(new THREE.Sphere());
    bucket.mesh.userData.ranges = ranges;
    bucket.mesh.visible = !shadow && indices.length > 0;
    bucket.dirty = false;
  }

  /** No-change frames scan identities, depth state and visibility without copying arrays. */
  flush(): void {
    if (this.flushing) return;
    this.flushing = true;
    this.depthEpoch++;
    try {
      for (const bucket of this.buckets.values()) {
        this.preserveWindowSeed(bucket.mesh.material as THREE.Material);
        this.updateDepthState(bucket);
      }
      for (const part of this.parts) {
        let bucket = part.bucket;
        const material = Array.isArray(part.source.material)
          ? part.source.material[part.materialIndex]! : part.source.material;
        if (material !== bucket.mesh.material) {
          bucket.parts.splice(bucket.parts.indexOf(part), 1); bucket.dirty = true;
          bucket = this.bucket(part.stateKey, material, part.source, bucket.mesh.position, bucket.blockIndex);
          bucket.parts.push(part); bucket.dirty = true; part.bucket = bucket;
        }
        if ((part.caster?.shadowKey ?? null) !== bucket.shadowKey) {
          if (part.caster !== null) {
            part.caster.parts.splice(part.caster.parts.indexOf(part), 1); part.caster.dirty = true;
          }
          part.caster = bucket.shadowKey === null ? null : this.caster(bucket);
          if (part.caster !== null) { part.caster.parts.push(part); part.caster.dirty = true; }
        }
        if (part.visible !== part.owner.visible) {
          part.visible = part.owner.visible; bucket.dirty = true;
          if (part.caster !== null) part.caster.dirty = true;
        }
      }
      for (const bucket of this.buckets.values()) {
        bucket.mesh.castShadow = bucket.shadowKey === null && bucket.source.castShadow;
        if (bucket.parts.length === 0) {
          bucket.mesh.geometry.dispose(); bucket.mesh.removeFromParent(); this.buckets.delete(bucket.key);
        } else if (bucket.dirty) this.rebuild(bucket, false);
      }
      for (const caster of this.casters.values()) {
        if (caster.parts.length === 0) {
          caster.mesh.geometry.dispose(); caster.mesh.removeFromParent(); this.casters.delete(caster.key);
        } else {
          caster.mesh.material = caster.parts[0]!.bucket.mesh.material;
          if (caster.dirty) this.rebuild(caster, true);
        }
      }
    } finally { this.flushing = false; }
  }

  /**
   * Three projects the main list before this instance method; cube captures use it too.
   * The group's updateMatrixWorld hook has already flushed for this render.
   */
  prepareRenderer(renderer: THREE.WebGLRenderer): void {
    if (this.shadowHooks.has(renderer)) return;
    const unregister = registerCityShadowPass(renderer, () => {
      for (const caster of this.casters.values()) {
        caster.mesh.visible = caster.mesh.geometry.index!.count > 0;
        if (this.warmingShadows) caster.mesh.frustumCulled = false;
      }
      // VSM also draws receivers. Covered colour buckets must not duplicate casters.
      for (const bucket of this.buckets.values()) {
        if (bucket.shadowKey !== null) bucket.mesh.receiveShadow = false;
        if (this.warmingShadows) bucket.mesh.frustumCulled = false;
      }
    }, () => {
      for (const caster of this.casters.values()) {
        caster.mesh.visible = false; caster.mesh.frustumCulled = caster.source.frustumCulled;
      }
      for (const bucket of this.buckets.values()) {
        bucket.mesh.receiveShadow = bucket.source.receiveShadow;
        bucket.mesh.frustumCulled = bucket.source.frustumCulled;
      }
    });
    this.shadowHooks.set(renderer, unregister);
  }

  /** Warm every loaded depth variant even before the sun frustum is focused. */
  warmShadowPrograms(renderer: THREE.WebGLRenderer, camera: THREE.Camera, scene: THREE.Scene): void {
    this.warmingShadows = true;
    try { renderShadowPrograms(renderer, scene, camera); }
    finally { this.warmingShadows = false; }
  }

  dispose(): void {
    for (const bucket of this.buckets.values()) {
      bucket.mesh.geometry.dispose();
      for (const part of bucket.parts) part.geometry.dispose();
    }
    for (const caster of this.casters.values()) caster.mesh.geometry.dispose();
    for (const unregister of this.shadowHooks.values()) unregister();
    this.shadowHooks.clear(); this.casters.clear(); this.parts.length = 0; this.buckets.clear();
    this.group.removeFromParent(); this.group.clear();
    this.shadowGroup.removeFromParent(); this.shadowGroup.clear();
  }
}
