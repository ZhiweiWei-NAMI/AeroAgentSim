import * as THREE from "three";
import { DEPTH_FIELDS, registerCityShadowPass, renderShadowPrograms, type DepthSource } from "./city-shadow-pass";

interface Binding { vehicle: THREE.Group; source: THREE.Mesh; receiveShadow: boolean; inside: boolean }
interface Surface {
  path: number[];
  transform: THREE.Matrix4;
  mesh: THREE.InstancedMesh;
  bindings: Binding[];
  depthState: unknown[];
}
interface Template { surfaces: Surface[] }

function unsupported(mesh: THREE.Mesh): string | null {
  if (mesh instanceof THREE.SkinnedMesh) return "skinned";
  if (mesh instanceof THREE.InstancedMesh) return "already instanced";
  if (Object.keys(mesh.geometry.morphAttributes).length > 0) return "morph targets";
  if (mesh.customDepthMaterial !== undefined || mesh.customDistanceMaterial !== undefined) return "custom depth/distance material";
  if (mesh.onBeforeShadow !== THREE.Object3D.prototype.onBeforeShadow
      || mesh.onAfterShadow !== THREE.Object3D.prototype.onAfterShadow) return "custom shadow callbacks";
  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  for (const material of materials) {
    const m = material as DepthSource;
    if ((m.alphaTest! > 0 || m.alphaToCoverage === true) && (m.map || m.alphaMap)) return "alpha-tested texture";
    if (m.displacementMap && m.displacementScale !== 0) return "displacement";
  }
  return null;
}

function renders(source: THREE.Object3D, scene: THREE.Object3D, camera: THREE.Camera): boolean {
  if (!source.layers.test(camera.layers)) return false;
  let node: THREE.Object3D | null = source;
  while (node !== null) {
    if (!node.visible) return false;
    if (node === scene) return true;
    node = node.parent;
  }
  return false;
}

/** Static motor surfaces only; main meshes, materials, lamps and picking remain per vehicle. */
export class TrafficShadowCasters {
  readonly group = new THREE.Group();
  readonly uncovered: { template: string; mesh: string; reason: string }[] = [];
  private readonly templates = new Map<THREE.Group, Template>();
  private readonly surfaces: Surface[] = [];
  private readonly bindings: Binding[] = [];
  private readonly hooks = new Map<THREE.WebGLRenderer, () => void>();
  private readonly matrix = new THREE.Matrix4();
  private readonly frustums: THREE.Frustum[] = [];
  private warming = false;

  constructor(root: THREE.Group, templates: Readonly<Record<string, THREE.Group>>) {
    this.group.name = "Traffic shadow casters"; root.add(this.group);
    for (const [name, template] of Object.entries(templates)) {
      // Bicycle wheels and rider are animated by cloneSkeleton/animateCyclist.
      if (name === "bicycle") continue;
      template.updateWorldMatrix(true, true);
      const inverse = template.matrixWorld.clone().invert();
      const animated = new Set<THREE.Object3D>();
      template.traverse(node => {
        for (const clip of node.animations) for (const track of clip.tracks) {
          const binding = THREE.PropertyBinding.parseTrackName(track.name);
          const target = THREE.PropertyBinding.findNode(template, binding.nodeName);
          if (target instanceof THREE.Object3D) target.traverse(child => animated.add(child));
        }
      });
      const record: Template = { surfaces: [] }; this.templates.set(template, record);
      template.traverse(node => {
        if (!(node instanceof THREE.Mesh) || !node.castShadow) return;
        const transform = inverse.clone().multiply(node.matrixWorld);
        const reason = unsupported(node) ?? (animated.has(node) ? "animated transform" : null);
        if (reason !== null) { this.uncovered.push({ template: name, mesh: node.name, reason }); return; }
        const path: number[] = [];
        let current: THREE.Object3D = node;
        while (current !== template) {
          path.unshift(current.parent!.children.indexOf(current)); current = current.parent!;
        }
        const geometry = node.geometry, position = geometry.getAttribute("position");
        const total = geometry.index?.count ?? position.count;
        const groups = Array.isArray(node.material) ? geometry.groups
          : [{ start: 0, count: total, materialIndex: 0 }];
        // Each material group is a template surface. Preserve partial/overlapping groups
        // and drawRange exactly as Three's renderBufferDirect does.
        for (const group of groups) {
          const material = Array.isArray(node.material) ? node.material[group.materialIndex!] : node.material;
          if (material === undefined) continue;
          const start = Math.max(group.start, geometry.drawRange.start);
          const end = Math.min(total, group.start + group.count,
            geometry.drawRange.start + geometry.drawRange.count);
          if (end <= start) continue;
          const positions = new Float32Array((end - start) * 3);
          for (let i = start; i < end; i++) {
            const index = geometry.index?.getX(i) ?? i, offset = (i - start) * 3;
            positions[offset] = position.getX(index); positions[offset + 1] = position.getY(index);
            positions[offset + 2] = position.getZ(index);
          }
          const shadowGeometry = new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(positions, 3));
          // Bake into vehicle-template space. Mirrored source draws reverse the
          // renderer's front-face convention; reverse those triangles after baking
          // so positive vehicle instance matrices retain the same culled faces.
          shadowGeometry.applyMatrix4(transform);
          if (transform.determinant() < 0) {
            for (let vertex = 0; vertex + 2 < end - start; vertex += 3) {
              for (let component = 0; component < 3; component++) {
                const a = (vertex + 1) * 3 + component, b = (vertex + 2) * 3 + component;
                const value = positions[a]!; positions[a] = positions[b]!; positions[b] = value;
              }
            }
          }
          shadowGeometry.computeBoundingSphere();
          const mesh = new THREE.InstancedMesh(shadowGeometry, material, 1);
          mesh.name = `Traffic shadow ${name}: ${node.name}/${group.materialIndex}`;
          mesh.userData.trafficShadowCaster = true; mesh.castShadow = true; mesh.visible = false;
          // Instance matrices are world-space; avoid inheriting the traffic/scene transform.
          mesh.layers.enableAll();
          mesh.matrixAutoUpdate = false; mesh.matrixWorldAutoUpdate = false;
          mesh.boundingSphere = new THREE.Sphere(); mesh.count = 0;
          mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
          mesh.raycast = () => {};
          // The template-space surface matrix is identity after baking above.
          const surface: Surface = { path, transform: new THREE.Matrix4(), mesh, bindings: [],
            depthState: DEPTH_FIELDS.map(field => (material as DepthSource)[field]) };
          record.surfaces.push(surface); this.surfaces.push(surface); this.group.add(mesh);
        }
      });
    }
  }

  add(template: THREE.Group, vehicle: THREE.Group): void {
    const record = this.templates.get(template)!;
    const bindings = new Map<THREE.Mesh, Binding>();
    for (const surface of record.surfaces) {
      let node: THREE.Object3D = vehicle;
      for (const index of surface.path) node = node.children[index]!;
      const source = node as THREE.Mesh;
      let binding = bindings.get(source);
      if (binding === undefined) {
        binding = { vehicle, source, receiveShadow: source.receiveShadow, inside: false };
        bindings.set(source, binding); this.bindings.push(binding); source.castShadow = false;
      }
      surface.bindings.push(binding);
    }
  }

  prepareRenderer(renderer: THREE.WebGLRenderer): void {
    if (this.hooks.has(renderer)) return;
    this.hooks.set(renderer, registerCityShadowPass(renderer, (lights, scene, camera) => {
      // Cull each source against the shadow frustums as renderObject did before the source
      // stopped casting. updateMatrices is the renderer's own idempotent call for these lights.
      // Point-light cube faces are not culled here.
      let cull = lights.length > 0;
      this.frustums.length = 0;
      for (let i = 0; i < lights.length; i++) {
        const light = lights[i]!, shadow = (light as { shadow?: THREE.LightShadow }).shadow as
          (THREE.LightShadow & { isDirectionalLightShadow?: boolean; isSpotLightShadow?: boolean }) | undefined;
        if (shadow?.isDirectionalLightShadow === true || shadow?.isSpotLightShadow === true) {
          shadow.updateMatrices(light); this.frustums.push(shadow.getFrustum());
        } else cull = false;
      }
      for (let i = 0; i < this.bindings.length; i++) {
        const binding = this.bindings[i]!, source = binding.source;
        binding.receiveShadow = source.receiveShadow;
        source.receiveShadow = false; // VSM must not draw covered receivers twice.
        binding.inside = renders(source, scene, camera);
        if (binding.inside && cull && source.frustumCulled) {
          binding.inside = false;
          for (let f = 0; f < this.frustums.length && !binding.inside; f++) {
            binding.inside = this.frustums[f]!.intersectsObject(source);
          }
        }
      }
      for (let i = 0; i < this.surfaces.length; i++) {
        const surface = this.surfaces[i]!, mesh = surface.mesh;
        const material = mesh.material as DepthSource;
        // Each template surface shares its material across all vehicle clones.
        // Its full depth-state key stays attached to this caster and changes in
        // place; no strings, containers or geometry are created on steady frames.
        let depthChanged = false;
        for (let field = 0; field < DEPTH_FIELDS.length; field++) {
          if (surface.depthState[field] !== material[DEPTH_FIELDS[field]!]) { depthChanged = true; break; }
        }
        if (depthChanged) {
          if (((material.alphaTest! > 0 || material.alphaToCoverage === true) && (material.map || material.alphaMap))
              || (material.displacementMap && material.displacementScale !== 0)) {
            throw new Error(`Traffic position-only depth state changed to require vertex attributes: ${mesh.name}`);
          }
          for (let field = 0; field < DEPTH_FIELDS.length; field++) {
            surface.depthState[field] = material[DEPTH_FIELDS[field]!];
          }
        }
        const required = Math.max(1, surface.bindings.length);
        if (required > mesh.instanceMatrix.count) {
          // Release the old GPU instance buffer before replacing its attribute.
          mesh.dispose();
          mesh.instanceMatrix = new THREE.InstancedBufferAttribute(new Float32Array(required * 2 * 16), 16);
          mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        }
        mesh.count = 0;
        for (let j = 0; j < surface.bindings.length; j++) {
          const binding = surface.bindings[j]!;
          if (!binding.inside) continue;
          this.matrix.multiplyMatrices(binding.vehicle.matrixWorld, surface.transform);
          mesh.setMatrixAt(mesh.count++, this.matrix);
        }
        if (this.warming && mesh.count === 0) {
          mesh.setMatrixAt(0, surface.transform); mesh.count = 1;
        }
        if (mesh.count > 0) {
          mesh.instanceMatrix.needsUpdate = true;
          mesh.computeBoundingSphere();
        }
        mesh.frustumCulled = !this.warming; mesh.visible = mesh.count > 0;
      }
    }, () => {
      for (let i = 0; i < this.surfaces.length; i++) {
        const surface = this.surfaces[i]!;
        surface.mesh.visible = false; surface.mesh.frustumCulled = true;
      }
      for (let i = 0; i < this.bindings.length; i++) {
        const binding = this.bindings[i]!;
        binding.source.receiveShadow = binding.receiveShadow;
      }
    }));
  }

  warmShadowPrograms(renderer: THREE.WebGLRenderer, camera: THREE.Camera, scene: THREE.Scene): void {
    const parent = this.group.parent!;
    // prepareRenderer is also used before a replacement traffic group is mounted.
    scene.add(this.group); this.warming = true;
    try { renderShadowPrograms(renderer, scene, camera); }
    finally { this.warming = false; parent.add(this.group); }
  }

  dispose(): void {
    for (const unregister of this.hooks.values()) unregister();
    this.hooks.clear();
    for (const surface of this.surfaces) {
      surface.mesh.dispose(); surface.mesh.geometry.dispose();
    }
    this.group.removeFromParent(); this.group.clear();
    this.surfaces.length = 0; this.bindings.length = 0; this.templates.clear();
  }
}
