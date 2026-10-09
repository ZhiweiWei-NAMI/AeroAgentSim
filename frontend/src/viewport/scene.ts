import * as T from 'three';
import { Sky } from 'three/addons/objects/Sky.js';

/** Art direction only. Sun, haze and lighting are never measured weather. */
export function setupScene(renderer: T.WebGLRenderer, scene: T.Scene) {
  const horizon = new T.Color('#c6d8e5');
  scene.background = horizon;
  scene.fog = new T.Fog(horizon, 850, 4200);
  const sky = new Sky(); sky.scale.setScalar(10000);
  sky.material.uniforms.turbidity.value = 2.1;
  sky.material.uniforms.rayleigh.value = 1.1;
  sky.material.uniforms.mieCoefficient.value = 0.003;
  sky.material.uniforms.mieDirectionalG.value = 0.8;
  const sunDirection = new T.Vector3(-0.55, 0.8, -0.35).normalize();
  sky.material.uniforms.sunPosition.value.copy(sunDirection);
  const generator = new T.PMREMGenerator(renderer), skyScene = new T.Scene(); skyScene.add(sky);
  const environment = generator.fromScene(skyScene, 0.04); generator.dispose();
  scene.environment = environment.texture; scene.environmentIntensity = 0.55; scene.add(sky);
  const fill = new T.HemisphereLight('#d8e8f7', '#777c70', 0.7); scene.add(fill);
  const sun = new T.DirectionalLight('#fff5e7', 2.8); sun.castShadow = true;
  sun.position.copy(sunDirection).multiplyScalar(650); scene.add(sun, sun.target);
  Object.assign(sun.shadow.camera, { left: -250, right: 250, top: 250, bottom: -250, near: 1, far: 1600 });
  sun.shadow.bias = -0.0001; sun.shadow.normalBias = 0.1;
  const groundMaterial = new T.MeshStandardMaterial({ color: '#bbbcb4', roughness: 0.96 });
  groundMaterial.onBeforeCompile = shader => {
    shader.vertexShader = 'varying vec2 displayGroundXZ;\n' + shader.vertexShader;
    shader.vertexShader = shader.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\ndisplayGroundXZ = (modelMatrix * vec4(position,1.0)).xz;');
    shader.fragmentShader = 'varying vec2 displayGroundXZ;\n' + shader.fragmentShader;
    shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', `#include <color_fragment>
      float grain = fract(sin(dot(floor(displayGroundXZ * 3.0), vec2(12.9898,78.233))) * 43758.5453);
      diffuseColor.rgb *= 0.97 + 0.06 * grain;`);
  };
  const ground = new T.Mesh(new T.PlaneGeometry(12000, 12000), groundMaterial);
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.1; ground.receiveShadow = true; ground.name = 'display-ground'; scene.add(ground);
  const grid = new T.GridHelper(800, 80, '#9aa9af', '#9aa9af');
  grid.material.transparent = true; grid.material.opacity = 0.09; grid.name = 'display-grid'; scene.add(grid);
  return { sun, grid, ground, setDusk: (value: boolean) => {
    sunDirection.set(value ? -0.8 : -0.55, value ? 0.16 : 0.8, -0.35).normalize();
    sky.material.uniforms.sunPosition.value.copy(sunDirection);
    sky.material.uniforms.turbidity.value = value ? 3.5 : 2.1;
    sun.color.set(value ? '#ffba79' : '#fff5e7'); sun.intensity = value ? 1.5 : 2.8;
    fill.color.set(value ? '#91a8d5' : '#d8e8f7'); fill.intensity = value ? 0.35 : 0.7;
    scene.environmentIntensity = value ? 0.22 : 0.55;
    horizon.set(value ? '#7f91aa' : '#c6d8e5');
  }, update: (target: T.Vector3) => {
    const texel = 500 / sun.shadow.mapSize.x;
    sun.target.position.set(Math.round(target.x / texel) * texel, target.y, Math.round(target.z / texel) * texel);
    sun.position.copy(sun.target.position).addScaledVector(sunDirection, 650);
    sky.position.copy(target); ground.position.x=target.x; ground.position.z=target.z;
  }, dispose: () => environment.dispose() };
}
