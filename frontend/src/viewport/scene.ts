import * as T from 'three';
import { Sky } from 'three/addons/objects/Sky.js';

/** Art direction only. Sun, haze and lighting are never measured weather. */
export function setupScene(renderer: T.WebGLRenderer, scene: T.Scene) {
  const horizon = new T.Color('#72b1e8');
  scene.background = horizon;
  scene.fog = new T.Fog(horizon, 500, 2000);
  const sky = new Sky(); sky.scale.setScalar(10000);
  sky.material.uniforms.turbidity.value = 2.1;
  sky.material.uniforms.rayleigh.value = 1.1;
  sky.material.uniforms.mieCoefficient.value = 0.003;
  sky.material.uniforms.mieDirectionalG.value = 0.8;
  const sunDirection = new T.Vector3(0.8, 0.9, 0.3).normalize();
  sky.material.uniforms.sunPosition.value.copy(sunDirection);
  const generator = new T.PMREMGenerator(renderer), skyScene = new T.Scene(); skyScene.add(sky);
  const environment = generator.fromScene(skyScene, 0.04); generator.dispose();
  scene.environment = environment.texture; scene.environmentIntensity = 0.95; scene.add(sky);
  const fill = new T.HemisphereLight('#dceeff', '#b8b4a3', 1.45); scene.add(fill);
  const sun = new T.DirectionalLight('#fff1dc', 3.1); sun.castShadow = true;
  sun.position.copy(sunDirection).multiplyScalar(650); scene.add(sun, sun.target);
  Object.assign(sun.shadow.camera, { left: -250, right: 250, top: 250, bottom: -250, near: 1, far: 1600 });
  sun.shadow.bias = -0.0001; sun.shadow.normalBias = 0.1;
  // A procedural display dome gives a clean blue horizon; the licensed HDRI
  // remains the city's image-based lighting/reflection source after loading.
  const daySky = new T.Mesh(new T.SphereGeometry(10000, 32, 16), new T.ShaderMaterial({
    side: T.BackSide, depthWrite: false, fog: false,
    uniforms: { zenith: { value: new T.Color('#438ee0') }, horizon: { value: new T.Color('#72b1e8') }, sunDirection: { value: sunDirection } },
    vertexShader: 'varying vec3 skyDirection;void main(){skyDirection=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
    fragmentShader: `uniform vec3 zenith;uniform vec3 horizon;uniform vec3 sunDirection;varying vec3 skyDirection;
      void main(){vec3 direction=normalize(skyDirection);float height=smoothstep(-0.02,0.6,direction.y);
      vec3 color=mix(horizon,zenith,height);float sun=pow(max(dot(direction,sunDirection),0.0),1500.0);
      gl_FragColor=vec4(color+vec3(1.0,0.86,0.64)*sun*2.0,1.0);}`,
  }));
  daySky.name = 'display-day-sky'; scene.add(daySky); sky.visible = false;
  renderer.toneMappingExposure = 1.12;
  const groundMaterial = new T.MeshStandardMaterial({ color: '#d3c7b3', roughness: 0.96 });
  groundMaterial.onBeforeCompile = shader => {
    shader.vertexShader = 'varying vec2 displayGroundXZ;\n' + shader.vertexShader;
    shader.vertexShader = shader.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\ndisplayGroundXZ = (modelMatrix * vec4(position,1.0)).xz;');
    shader.fragmentShader = 'varying vec2 displayGroundXZ;\n' + shader.fragmentShader;
    shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', `#include <color_fragment>
      float grain = fract(sin(dot(floor(displayGroundXZ * 3.0), vec2(12.9898,78.233))) * 43758.5453);
      vec2 paving = displayGroundXZ / 4.0;
      vec2 joints = abs(fract(paving) - 0.5);
      float joint = smoothstep(0.488-max(fwidth(paving.x),fwidth(paving.y)),0.498,max(joints.x,joints.y));
      diffuseColor.rgb *= 0.98 + 0.04 * grain - joint * 0.06;`);
  };
  const ground = new T.Mesh(new T.PlaneGeometry(12000, 12000), groundMaterial);
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.1; ground.receiveShadow = true; ground.name = 'display-ground'; scene.add(ground);
  const grid = new T.GridHelper(800, 80, '#9aa9af', '#9aa9af');
  grid.material.transparent = true; grid.material.opacity = 0.09; grid.name = 'display-grid'; scene.add(grid);
  return { sun, grid, ground, setDusk: (value: boolean) => {
    sunDirection.set(value ? -0.8 : 0.8, value ? 0.16 : 0.9, value ? -0.35 : 0.3).normalize();
    sky.visible=value; daySky.visible=!value; renderer.toneMappingExposure=value?1:1.12;
    sky.material.uniforms.sunPosition.value.copy(sunDirection);
    sky.material.uniforms.turbidity.value = value ? 3.5 : 2.1;
    sun.color.set(value ? '#ffba79' : '#fff1dc'); sun.intensity = value ? 1.5 : 3.1;
    fill.color.set(value ? '#91a8d5' : '#dceeff'); fill.intensity = value ? 0.35 : 1.45;
    scene.environmentIntensity = value ? 0.22 : 0.95;
    horizon.set(value ? '#7f91aa' : '#72b1e8');
    fill.groundColor.set(value?'#777c70':'#b8b4a3');
    groundMaterial.color.set(value?'#bbbcb4':'#d3c7b3');
    if(scene.fog instanceof T.Fog){scene.fog.near=value?850:500;scene.fog.far=value?4200:2000;scene.fog.color.copy(horizon);}
  }, update: (target: T.Vector3) => {
    const texel = 500 / sun.shadow.mapSize.x;
    sun.target.position.set(Math.round(target.x / texel) * texel, target.y, Math.round(target.z / texel) * texel);
    sun.position.copy(sun.target.position).addScaledVector(sunDirection, 650);
    sky.position.copy(target); daySky.position.copy(target); ground.position.x=target.x; ground.position.z=target.z;
  }, dispose: () => environment.dispose() };
}
