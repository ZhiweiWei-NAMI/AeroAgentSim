import * as T from 'three';
import { Sky } from 'three/addons/objects/Sky.js';

/** Viewer art direction; independent of measured weather and physical state. */
export function setupScene(renderer: T.WebGLRenderer, scene: T.Scene) {
  const horizon = new T.Color('#c2d4df');
  scene.background = horizon;
  scene.fog = new T.Fog(horizon, 700, 3400);
  const sky = new Sky(); sky.scale.setScalar(10000);
  Object.assign(sky.material.uniforms.turbidity, { value: 2.6 });
  sky.material.uniforms.rayleigh.value = 1.35;
  sky.material.uniforms.mieCoefficient.value = 0.004;
  sky.material.uniforms.mieDirectionalG.value = 0.78;
  const sunDirection = new T.Vector3(-0.65, 0.6, -0.4).normalize();
  sky.material.uniforms.sunPosition.value.copy(sunDirection);
  const generator = new T.PMREMGenerator(renderer), skyScene = new T.Scene(); skyScene.add(sky);
  const environment = generator.fromScene(skyScene, 0.03); generator.dispose();
  scene.environment = environment.texture; scene.environmentIntensity = 0.6; scene.add(sky);
  scene.add(new T.HemisphereLight('#c5ddfa', '#5b6255', 0.65));
  const sun = new T.DirectionalLight('#fff1db', 2.5); sun.castShadow = true;
  sun.position.copy(sunDirection).multiplyScalar(450); scene.add(sun, sun.target);
  Object.assign(sun.shadow.camera, { left: -160, right: 160, top: 160, bottom: -160, near: 1, far: 1000 });
  sun.shadow.bias = -0.00015; sun.shadow.normalBias = 0.12;
  const ground = new T.Mesh(new T.PlaneGeometry(12000, 12000), new T.MeshStandardMaterial({ color: '#718379', roughness: 0.98 }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.08; ground.receiveShadow = true; ground.name = 'display-ground'; scene.add(ground);
  const grid = new T.GridHelper(800, 80, '#8fafa7', '#92aaa0');
  grid.material.transparent = true; grid.material.opacity = 0.13; grid.name = 'display-grid'; scene.add(grid);
  return { sun, grid, ground, update: (target: T.Vector3) => {
    // Snap the single shadow cascade to texels to avoid shimmering while following.
    const texel = 320 / sun.shadow.mapSize.x;
    sun.target.position.set(Math.round(target.x / texel) * texel, target.y, Math.round(target.z / texel) * texel);
    sun.position.copy(sun.target.position).addScaledVector(sunDirection, 450);
    sky.position.copy(target); ground.position.x=target.x; ground.position.z=target.z;
  }, dispose: () => environment.dispose() };
}
