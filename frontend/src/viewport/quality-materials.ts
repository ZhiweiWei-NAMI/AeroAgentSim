import * as T from 'three';

/** Per-view diffuse materials for low quality; source assets remain shared and
 * untouched, so returning to balanced/high restores the exact original finish. */
export class QualityMaterials {
  private originals = new Map<T.Mesh, T.Material | T.Material[]>();
  private simplified = new Map<T.MeshStandardMaterial, T.MeshLambertMaterial>();

  apply(root: T.Object3D, low: boolean) {
    if (!low) {
      for (const [mesh, material] of this.originals) mesh.material = material;
      this.originals.clear();
      return;
    }
    root.traverse(node => {
      if (!(node instanceof T.Mesh) || this.originals.has(node)) return;
      const source = node.material;
      const simplify = (material: T.Material): T.Material => {
        // Custom facade/hologram shaders own their appearance. They keep their
        // shader; only ordinary source-model PBR materials take this path.
        if (!(material instanceof T.MeshStandardMaterial) || material.onBeforeCompile !== T.Material.prototype.onBeforeCompile && !material.userData.lambertCompatible) return material;
        let cheap = this.simplified.get(material);
        if (!cheap) {
          cheap = new T.MeshLambertMaterial();
          T.Material.prototype.copy.call(cheap, material);
          // Textures are borrowed from the source, never owned by this view.
          cheap.color = material.color;
          cheap.map = material.map;
          cheap.emissive.copy(material.emissive);
          cheap.emissiveIntensity = material.emissiveIntensity;
          cheap.emissiveMap = material.emissiveMap;
          cheap.alphaMap = material.alphaMap;
          cheap.vertexColors = material.vertexColors;
          cheap.fog = material.fog;
          cheap.flatShading = material.flatShading;
          cheap.wireframe = material.wireframe;
          if (material.userData.lambertCompatible) {
            cheap.onBeforeCompile = material.onBeforeCompile;
            cheap.customProgramCacheKey = material.customProgramCacheKey;
          }
          this.simplified.set(material, cheap);
        }
        return cheap;
      };
      const replacement = Array.isArray(source) ? source.map(simplify) : simplify(source);
      if (replacement !== source) { this.originals.set(node, source); node.material = replacement; }
    });
  }

  dispose() {
    for (const [mesh, material] of this.originals) mesh.material = material;
    this.originals.clear();
    for (const material of this.simplified.values()) material.dispose();
    this.simplified.clear();
  }
}
