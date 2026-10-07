import * as THREE from "three";

// The complete source-material inputs read by Three 0.185.1 getDepthMaterial.
export const DEPTH_FIELDS = ["visible", "wireframe", "side", "shadowSide", "alphaMap", "alphaTest",
  "alphaToCoverage", "map", "clipShadows", "clippingPlanes", "clipIntersection",
  "displacementMap", "displacementScale", "displacementBias", "wireframeLinewidth", "linewidth"] as const;
export type DepthSource = THREE.Material & Partial<THREE.MeshStandardMaterial> & { linewidth?: number };

type ShadowRender = THREE.WebGLShadowMap["render"];
interface Hook { before: ShadowRender; after: () => void }
interface Pass { original: ShadowRender; wrapper: ShadowRender; hooks: Hook[] }
const passes = new WeakMap<THREE.WebGLRenderer, Pass>();

/**
 * Renders the scene once so shadow passes compile their depth programs, through a view that
 * contains no geometry. Lights and program keys come from the real scene and layers, but the
 * main pass draws only objects that opt out of culling, so it uploads no further textures.
 */
export function renderShadowPrograms(renderer: THREE.WebGLRenderer, scene: THREE.Scene,
  camera: THREE.Camera): void {
  const view = new THREE.PerspectiveCamera(1, 1, 1, 2);
  view.layers.mask = camera.layers.mask;
  view.position.set(0, 1e6, 0); view.updateMatrixWorld();
  renderer.render(scene, view);
}

/** One shadow traversal wrapper per renderer; all entered hooks unwind even on failure. */
export function registerCityShadowPass(renderer: THREE.WebGLRenderer,
  before: ShadowRender, after: () => void): () => void {
  let pass = passes.get(renderer);
  if (pass === undefined) {
    const hooks: Hook[] = [], original = renderer.shadowMap.render;
    const wrapper: ShadowRender = function (this: THREE.WebGLShadowMap, lights, scene, camera) {
      let entered = 0;
      try {
        while (entered < hooks.length) hooks[entered++]!.before.call(this, lights, scene, camera);
        original.call(this, lights, scene, camera);
      } finally {
        let failure: unknown, failed = false;
        while (entered > 0) {
          try { hooks[--entered]!.after(); }
          catch (error) { failure = error; failed = true; }
        }
        if (failed) throw failure;
      }
    };
    pass = { original, wrapper, hooks };
    passes.set(renderer, pass); renderer.shadowMap.render = wrapper;
  } else if (renderer.shadowMap.render !== pass.wrapper) {
    throw new Error("City shadow wrapper was replaced before registration");
  }
  const registered = pass, hook = { before, after };
  registered.hooks.push(hook);
  let active = true;
  return () => {
    if (!active) return;
    if (renderer.shadowMap.render !== registered.wrapper) {
      throw new Error("City shadow wrapper was replaced before disposal");
    }
    registered.hooks.splice(registered.hooks.indexOf(hook), 1); active = false;
    if (registered.hooks.length === 0) {
      renderer.shadowMap.render = registered.original; passes.delete(renderer);
    }
  };
}
