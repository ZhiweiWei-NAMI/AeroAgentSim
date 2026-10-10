import { describe, expect, it, vi } from "vitest";
import * as THREE from "three";
import { registerCityShadowPass, renderShadowPrograms } from "./city-shadow-pass";
import { BuildingRenderBatches } from "./city-building-batch";
import { TrafficShadowCasters } from "./city-traffic-shadow";

describe("shared city shadow pass", () => {
  it.each(["buildings first", "traffic first"])("restores one original method: %s", order => {
    const original = vi.fn();
    const renderer = { shadowMap: { render: original } } as unknown as THREE.WebGLRenderer;
    const root = new THREE.Group(), buildings = new BuildingRenderBatches(root, []);
    const traffic = new TrafficShadowCasters(root, {});
    buildings.prepareRenderer(renderer);
    const wrapper = renderer.shadowMap.render;
    traffic.prepareRenderer(renderer); traffic.prepareRenderer(renderer); buildings.prepareRenderer(renderer);
    expect(renderer.shadowMap.render).toBe(wrapper);
    renderer.shadowMap.render([], new THREE.Scene(), new THREE.PerspectiveCamera());
    expect(original).toHaveBeenCalledOnce();
    if (order === "buildings first") { buildings.dispose(); expect(renderer.shadowMap.render).toBe(wrapper); traffic.dispose(); }
    else { traffic.dispose(); expect(renderer.shadowMap.render).toBe(wrapper); buildings.dispose(); }
    expect(renderer.shadowMap.render).toBe(original);
    buildings.dispose(); traffic.dispose();
  });

  it("fails explicitly on external replacement without overwriting it", () => {
    const renderer = { shadowMap: { render() {} } } as unknown as THREE.WebGLRenderer;
    const a = registerCityShadowPass(renderer, () => {}, () => {});
    const b = registerCityShadowPass(renderer, () => {}, () => {});
    const wrapper = renderer.shadowMap.render, replacement = () => {};
    renderer.shadowMap.render = replacement;
    expect(a).toThrow("replaced before disposal"); expect(b).toThrow("replaced before disposal");
    expect(() => registerCityShadowPass(renderer, () => {}, () => {})).toThrow("replaced before registration");
    expect(renderer.shadowMap.render).toBe(replacement);
    renderer.shadowMap.render = wrapper; a(); b();
  });

  it.each(["before", "render", "after"])("runs all entered after callbacks in finally when %s throws", stage => {
    const calls: string[] = [], failure = new Error(stage);
    const renderer = { shadowMap: { render() { calls.push("render"); if (stage === "render") throw failure; } } } as unknown as THREE.WebGLRenderer;
    const a = registerCityShadowPass(renderer, () => { calls.push("before a"); }, () => { calls.push("after a"); });
    const b = registerCityShadowPass(renderer, () => {
      calls.push("before b"); if (stage === "before") throw failure;
    }, () => { calls.push("after b"); if (stage === "after") throw failure; });
    expect(() => renderer.shadowMap.render([], new THREE.Scene(), new THREE.PerspectiveCamera())).toThrow(failure);
    expect(calls).toEqual(stage === "before" ? ["before a", "before b", "after b", "after a"]
      : ["before a", "before b", "render", "after b", "after a"]);
    a(); b();
  });

  it("renders shadow programs through an empty view with the scene's lights and layers", () => {
    const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(50, 1.6, 0.5, 20000);
    camera.layers.enable(3); camera.position.set(0, 420, 380); camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
    const sun = new THREE.DirectionalLight(); sun.castShadow = true; scene.add(sun, sun.target);
    const city = new THREE.Mesh(new THREE.BoxGeometry(20000, 1000, 20000)); city.position.y = 500;
    const sky = new THREE.Mesh(new THREE.SphereGeometry(1)); sky.frustumCulled = false;
    scene.add(city, sky); scene.updateMatrixWorld(true);
    const lists: { lights: THREE.Light[]; drawn: THREE.Object3D[]; layers: number }[] = [];
    // Three collects lights regardless of the frustum and culls meshes against the main view.
    const renderer = { render(target: THREE.Scene, view: THREE.Camera) {
      view.updateMatrixWorld();
      const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4()
        .multiplyMatrices(view.projectionMatrix, view.matrixWorldInverse));
      const lights: THREE.Light[] = [], drawn: THREE.Object3D[] = [];
      target.traverseVisible(node => {
        if (!node.layers.test(view.layers)) return;
        if (node instanceof THREE.Light && node.castShadow) lights.push(node);
        if (node instanceof THREE.Mesh && (!node.frustumCulled || frustum.intersectsObject(node))) drawn.push(node);
      });
      lists.push({ lights, drawn, layers: view.layers.mask });
    } } as unknown as THREE.WebGLRenderer;
    renderer.render(scene, camera);
    renderShadowPrograms(renderer, scene, camera);
    expect(lists[0]!.drawn).toEqual([city, sky]);
    expect(lists[1]).toEqual({ lights: [sun], drawn: [sky], layers: camera.layers.mask });
  });
});
