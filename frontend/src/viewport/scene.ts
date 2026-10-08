import * as T from 'three';
import { Sky } from 'three/addons/objects/Sky.js';

/** Display art direction only; never interpreted as weather measurements. */
export function setupScene(renderer: T.WebGLRenderer, scene: T.Scene) {
  scene.background = new T.Color('#b8d0dc');
  scene.fog = new T.Fog('#b8d0dc', 130, 650);
  const sky = new Sky();
  sky.scale.setScalar(4000);
  const uniforms = sky.material.uniforms;
  uniforms.turbidity.value = 4;
  uniforms.rayleigh.value = 1.4;
  uniforms.mieCoefficient.value = 0.005;
  uniforms.mieDirectionalG.value = 0.8;
  uniforms.sunPosition.value.set(0.5, 0.55, -0.8);
  const generator = new T.PMREMGenerator(renderer);
  const skyScene = new T.Scene(); skyScene.add(sky);
  const environment = generator.fromScene(skyScene, 0.04);
  scene.environment = environment.texture;
  scene.environmentIntensity = 0.55;
  scene.add(sky);
  generator.dispose();
  scene.add(new T.HemisphereLight('#c5dcf3', '#736958', 1.4));
  const sun = new T.DirectionalLight('#ffecd6', 3.6);
  sun.position.set(70, 100, -60); sun.castShadow = true;
  Object.assign(sun.shadow.camera, { left: -100, right: 100, top: 100, bottom: -100, near: 1, far: 280 });
  sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.06;
  scene.add(sun);
  const ground = new T.Mesh(new T.PlaneGeometry(3000, 3000), new T.MeshStandardMaterial({ color: '#b2bab9', roughness: 0.94 }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.04; ground.receiveShadow = true;
  scene.add(ground);
  const grid = new T.GridHelper(240, 48, '#778f96', '#a0afb2');
  grid.material.transparent = true; grid.material.opacity = 0.35; scene.add(grid);
  return { sun, dispose: () => environment.dispose() };
}
