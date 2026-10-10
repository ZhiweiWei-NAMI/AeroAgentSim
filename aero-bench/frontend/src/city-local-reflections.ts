import * as THREE from "three";

// The supplied city contains 411–507 building parts in roughly one square kilometre.
// The production default remains one street-block probe. A measurement can create a second
// owner over a disjoint building set without changing that production policy.
export const CITY_LOCAL_REFLECTION_PROFILE = Object.freeze({
  probeSizePx: 256,
  reflectiveRadiusM: 75,
  captureFarM: 140,
  maxReflectiveBuildings: 12,
  cubeFacesPerRefresh: 6,
});

export interface CityLocalReflectionOptions {
  /** Building roots already owned by another probe. Used by the two-probe cost measurement. */
  readonly excludeBuildings?: ReadonlySet<THREE.Object3D>;
}

const LOCAL_REFLECTION_SOURCE = new WeakMap<THREE.Material, THREE.Material>();

interface ReflectedMesh {
  readonly owner: CityLocalReflections;
  readonly mesh: THREE.Mesh;
  readonly original: THREE.Material | THREE.Material[];
  readonly enhanced: THREE.Material | THREE.Material[];
}

const ACTIVE_REFLECTED_MESHES = new Set<ReflectedMesh>();

/** A static street-block probe for the marked glass facades in a loaded BigCity group. */
export class CityLocalReflections {
  static readonly profile = CITY_LOCAL_REFLECTION_PROFILE;

  private buildings: THREE.Group | null = null;
  private readonly focus = new THREE.Vector3();
  private enabled = false;
  private dirty = false;
  private disposed = false;
  private target: THREE.WebGLCubeRenderTarget | null = null;
  private camera: THREE.CubeCamera | null = null;
  private pmrem: THREE.PMREMGenerator | null = null;
  private filtered: THREE.WebGLRenderTarget | null = null;
  private readonly meshes: ReflectedMesh[] = [];
  private readonly reflectedBuildings: THREE.Object3D[] = [];
  private readonly clones = new Map<THREE.MeshStandardMaterial, THREE.MeshStandardMaterial>();
  private readonly excludedBuildings: ReadonlySet<THREE.Object3D>;

  constructor(private readonly renderer: THREE.WebGLRenderer, private readonly scene: THREE.Scene,
              options: CityLocalReflectionOptions = {}) {
    this.excludedBuildings = new Set(options.excludeBuildings ?? []);
  }

  /** The focus must be a world-space point on a street, not inside a building. */
  configure(buildings: THREE.Group, focus: THREE.Vector3, enabled: boolean): void {
    this.assertActive();
    const sameConfiguration = this.buildings === buildings && this.focus.equals(focus) && this.enabled === enabled;
    // Streamed facades may register their source materials after the first configuration.
    // Retry an enabled owner that selected nothing; otherwise the production probe stays empty.
    if (sameConfiguration && (!enabled || this.meshes.length > 0)) return;
    this.release(!enabled);
    this.buildings = buildings;
    this.focus.copy(focus);
    this.enabled = enabled;
    if (!enabled) return;

    try {
      if (![focus.x, focus.y, focus.z].every(Number.isFinite)) {
        throw new Error("City reflection focus must have finite world coordinates");
      }
      let root: THREE.Object3D = buildings;
      while (root.parent !== null) root = root.parent;
      if (root !== this.scene) throw new Error("City reflection buildings must be attached to the capture scene");
      const sourceMaterials = new Set<THREE.Material>(
        Array.isArray(buildings.userData.windowMaterials) ? buildings.userData.windowMaterials as THREE.Material[] : [],
      );
      if (sourceMaterials.size === 0) return;
      buildings.updateWorldMatrix(true, false);
      const bounds = new THREE.Box3();
      const selected = buildings.children.filter(child => child.userData.collisionBox !== undefined
          && !this.excludedBuildings.has(child))
        .map(child => {
          bounds.setFromObject(child);
          return { child, distance: bounds.distanceToPoint(focus) };
        })
        .filter(candidate => candidate.distance <= CITY_LOCAL_REFLECTION_PROFILE.reflectiveRadiusM)
        .sort((left, right) => left.distance - right.distance)
        .slice(0, CITY_LOCAL_REFLECTION_PROFILE.maxReflectiveBuildings);
      for (const { child } of selected) {
        const meshCountBefore = this.meshes.length;
        child.traverse(node => {
          if (!(node instanceof THREE.Mesh)) return;
          const original = node.material;
          const reflected = (Array.isArray(original) ? original : [original]).map(material => {
            const ownedSource = LOCAL_REFLECTION_SOURCE.get(material);
            const source = ownedSource ?? material;
            if (!(source instanceof THREE.MeshStandardMaterial)
                || source.userData.glassFacade !== true || !sourceMaterials.has(source)) return material;
            if (ownedSource !== undefined) {
              throw new Error("City reflection facade is already owned by another probe");
            }
            const standardMaterial = material as THREE.MeshStandardMaterial;
            let clone = this.clones.get(standardMaterial);
            if (clone === undefined) {
              clone = standardMaterial.clone();
              clone.name = `${standardMaterial.name} · local street reflection`;
              // Keep shader hooks such as per-window lighting on the probe clone.
              clone.onBeforeCompile = standardMaterial.onBeforeCompile;
              clone.customProgramCacheKey = standardMaterial.customProgramCacheKey;
              this.clones.set(standardMaterial, clone);
              LOCAL_REFLECTION_SOURCE.set(clone, standardMaterial);
            }
            return clone;
          });
          if (reflected.every((material, index) => material === (Array.isArray(original) ? original[index] : original))) return;
          const enhanced = Array.isArray(original) ? reflected : reflected[0]!;
          const reflectedMesh = { owner: this, mesh: node, original, enhanced };
          this.meshes.push(reflectedMesh);
          ACTIVE_REFLECTED_MESHES.add(reflectedMesh);
          node.material = enhanced;
        });
        if (this.meshes.length > meshCountBefore) this.reflectedBuildings.push(child);
      }

      if (this.meshes.length === 0) return;
      if (this.target === null) this.target = new THREE.WebGLCubeRenderTarget(
        CITY_LOCAL_REFLECTION_PROFILE.probeSizePx, {
        type: THREE.HalfFloatType,
        generateMipmaps: true,
        minFilter: THREE.LinearMipmapLinearFilter,
        depthBuffer: true,
      });
      if (this.camera === null) this.camera = new THREE.CubeCamera(
        1, CITY_LOCAL_REFLECTION_PROFILE.captureFarM, this.target);
      this.camera.layers.set(0); // Existing city meshes and lights use the default render layer.
      this.camera.position.copy(focus);
      this.camera.position.y += 5;
      this.dirty = true;
    } catch (error) {
      this.release();
      this.enabled = false;
      throw error;
    }
  }

  /** Call after a time-of-day or weather setter has changed the scene. */
  invalidate(): void {
    this.assertActive();
    if (this.enabled && this.camera !== null) this.dirty = true;
  }

  /** A copy of the building roots currently owned by this probe. */
  selectedBuildings(): readonly THREE.Object3D[] {
    this.assertActive();
    return Object.freeze([...this.reflectedBuildings]);
  }

  /** Captures six faces once while dirty. Repeated calls without invalidation do no work. */
  refresh(): boolean {
    this.assertActive();
    if (!this.dirty || this.camera === null || this.target === null) return false;

    const target = this.target;
    for (const [source, clone] of this.clones) {
      const wasLocal = this.filtered !== null && clone.envMap === this.filtered.texture;
      clone.copy(source);
      clone.name = `${source.name} · local street reflection`;
      if (wasLocal) clone.envMap = this.filtered!.texture;
    }
    const oldTarget = this.renderer.getRenderTarget();
    const oldFace = this.renderer.getActiveCubeFace();
    const oldMipmap = this.renderer.getActiveMipmapLevel();
    const oldXr = this.renderer.xr.enabled;
    const oldAutoClear = this.renderer.autoClear;
    const oldShadowAutoUpdate = this.renderer.shadowMap.autoUpdate;
    const oldGenerateMipmaps = target.texture.generateMipmaps;
    const otherProbeMaterials: Array<{ mesh: THREE.Mesh; material: THREE.Material | THREE.Material[] }> = [];
    try {
      // Source materials keep the glass geometry and attached lights in the capture,
      // while preventing the probe from sampling its own previous texture.
      for (const item of this.meshes) item.mesh.material = item.original;
      // A two-probe measurement has another disjoint facade set in the same scene. Strip
      // every other local probe clone too, so neither cubemap captures a prior local PMREM.
      for (const item of ACTIVE_REFLECTED_MESHES) {
        if (item.owner === this || item.owner.scene !== this.scene) continue;
        otherProbeMaterials.push({ mesh: item.mesh, material: item.mesh.material });
        item.mesh.material = item.original;
      }
      this.renderer.autoClear = true;
      this.renderer.shadowMap.autoUpdate = false; // Reuse the main pass shadow map.
      this.camera.update(this.renderer, this.scene);
      // Own the filtered target explicitly: Three does not evict render-target PMREMs
      // on cube target disposal. Reuse both allocations across camera relocation.
      this.pmrem ??= new THREE.PMREMGenerator(this.renderer);
      this.filtered = this.pmrem.fromCubemap(target.texture, this.filtered);
      for (const clone of this.clones.values()) if (clone.envMap !== this.filtered.texture) {
        clone.envMap = this.filtered.texture;
        clone.needsUpdate = true;
      }
      this.dirty = false;
      return true;
    } catch (error) {
      for (const [source, clone] of this.clones) if (this.filtered !== null && clone.envMap === this.filtered.texture) {
        clone.envMap = source.envMap;
        clone.needsUpdate = true;
      }
      throw error;
    } finally {
      for (const item of otherProbeMaterials) item.mesh.material = item.material;
      for (const item of this.meshes) item.mesh.material = item.enhanced;
      target.texture.generateMipmaps = oldGenerateMipmaps;
      this.renderer.setRenderTarget(oldTarget, oldFace, oldMipmap);
      this.renderer.xr.enabled = oldXr;
      this.renderer.autoClear = oldAutoClear;
      this.renderer.shadowMap.autoUpdate = oldShadowAutoUpdate;
    }
  }

  /** Restores shared source materials before the building group is disposed. */
  dispose(): void {
    if (this.disposed) return;
    this.release();
    this.buildings = null;
    this.enabled = false;
    this.disposed = true;
  }

  private release(disposeResources = true): void {
    for (const item of this.meshes) {
      item.mesh.material = item.original;
      ACTIVE_REFLECTED_MESHES.delete(item);
    }
    this.meshes.length = 0;
    this.reflectedBuildings.length = 0;
    for (const clone of this.clones.values()) clone.dispose();
    this.clones.clear();
    if (disposeResources) {
      this.target?.dispose();
      this.filtered?.dispose();
      this.pmrem?.dispose();
      this.target = null;
      this.filtered = null;
      this.pmrem = null;
      this.camera = null;
    }
    this.dirty = false;
  }

  private assertActive(): void {
    if (this.disposed) throw new Error("City local reflections have been disposed");
  }
}
