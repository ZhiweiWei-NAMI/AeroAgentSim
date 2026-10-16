import * as T from 'three';
import { Sky } from 'three/addons/objects/Sky.js';

/** Exponential density at which contrast reaches 5 % at the authored visibility. */
export const fogDensity = (visibilityM: number) => Math.sqrt(-Math.log(0.05)) / visibilityM;
export interface AtmosphereState { dusk: boolean; rain: number; visibilityM: number | null; observerOffsetM?: number }
const FOG_DAY = new T.Color('#c3c9cf'), FOG_DUSK = new T.Color('#6f6a73'), FILL_RAIN = new T.Color('#aebfd4');
// The analytic Sky PMREM carries HDR radiance, not a unit-strength ambient colour.
// Keep it as a small reflection/fill contribution alongside the sun and hemisphere;
// otherwise PBR concrete saturates while the low-tier Lambert finish stays beige.
const DAY = { exposure: 0.9, sun: 2.5, fill: 1.05, environment: 0.025, ground: '#aea99f' };

// Operator fog starts near the observed region; UAV captures keep the physical camera origin.
export const observerFogOffset = (camera: T.Camera, target: T.Vector3) => Math.max(0, camera.position.distanceTo(target) - 150);

/** Keep whichever fog reaches the visibility threshold sooner, including the observer offset. */
export function visibilityFog(artFog: T.Scene['fog'], weatherFog: T.Fog, visibilityM: number | null, offset = 0): T.Scene['fog'] {
  const artVisibility = artFog instanceof T.Fog ? artFog.near + 0.95 * (artFog.far - artFog.near)
    : artFog instanceof T.FogExp2 ? Math.sqrt(-Math.log(0.05)) / artFog.density : Infinity;
  if (visibilityM === null || offset + visibilityM >= artVisibility) return artFog;
  weatherFog.near = offset; weatherFog.far = offset + visibilityM / 0.95;
  return weatherFog;
}

/** Art direction only. Sun, haze and lighting are never measured weather. */
export function setupScene(renderer: T.WebGLRenderer, scene: T.Scene) {
  const horizon = new T.Color('#72b1e8');
  scene.background = horizon;
  const artFog = new T.Fog(horizon, 1400, 4000), weatherFog = new T.Fog(0xffffff), neutralHaze = new T.Color();
  scene.fog = artFog;
  const sky = new Sky(); sky.scale.setScalar(10000);
  sky.material.uniforms.turbidity.value = 2.1;
  sky.material.uniforms.rayleigh.value = 1.1;
  sky.material.uniforms.mieCoefficient.value = 0.003;
  sky.material.uniforms.mieDirectionalG.value = 0.8;
  const sunDirection = new T.Vector3(0.8, 0.9, 0.3).normalize();
  sky.material.uniforms.sunPosition.value.copy(sunDirection);
  const generator = new T.PMREMGenerator(renderer), skyScene = new T.Scene(); skyScene.add(sky);
  const environment = generator.fromScene(skyScene, 0.04); generator.dispose();
  scene.environment = environment.texture; scene.environmentIntensity = DAY.environment; scene.add(sky);
  const fill = new T.HemisphereLight('#dceeff', '#b8b4a3', DAY.fill); scene.add(fill);
  const sun = new T.DirectionalLight('#fff1dc', DAY.sun); sun.castShadow = true;
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
  renderer.toneMappingExposure = DAY.exposure;
  const groundMaterial = new T.MeshStandardMaterial({ color: DAY.ground, roughness: 0.96 });
  groundMaterial.userData.lambertCompatible = true;
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
  let current: AtmosphereState | undefined;
  // Recompute from the exact day/dusk presets, never from previously modified lighting.
  const setAtmosphere = (state: AtmosphereState) => {
    if (current && current.dusk === state.dusk && current.rain === state.rain && current.visibilityM === state.visibilityM
      && (state.visibilityM === null || Math.abs((current.observerOffsetM ?? 0) - (state.observerOffsetM ?? 0)) <= 5)) return;
    current = { ...state };
    const dusk = state.dusk, rain = T.MathUtils.clamp(state.rain, 0, 1);
    sunDirection.set(dusk ? -0.8 : 0.8, dusk ? 0.16 : 0.9, dusk ? -0.35 : 0.3).normalize();
    sun.position.copy(sun.target.position).addScaledVector(sunDirection, 650);
    sky.visible=dusk; daySky.visible=!dusk;
    sky.material.uniforms.sunPosition.value.copy(sunDirection);
    sky.material.uniforms.turbidity.value = (dusk ? 3.5 : 2.1) + rain * 3;
    sky.material.uniforms.rayleigh.value = 1.1 * (1 - 0.5 * rain);
    sun.color.set(dusk ? '#ffba79' : '#fff1dc'); sun.intensity = (dusk ? 1.5 : DAY.sun) * (1 - 0.45 * rain);
    fill.color.set(dusk ? '#91a8d5' : '#dceeff').lerp(FILL_RAIN, rain * 0.35);
    fill.intensity = dusk ? 0.35 : DAY.fill;
    scene.environmentIntensity = dusk ? 0.22 : DAY.environment;
    horizon.set(dusk ? '#7f91aa' : '#72b1e8');
    fill.groundColor.set(dusk?'#777c70':'#b8b4a3');
    groundMaterial.color.set(dusk ? '#bbbcb4' : DAY.ground);
    renderer.toneMappingExposure = (dusk ? 1 : DAY.exposure) * (1 - 0.15 * rain);
    // Preserve local contrast at the operator's overview distance. Recorded
    // visibility still takes precedence through visibilityFog below.
    artFog.near = dusk ? 850 : 1400; artFog.far = dusk ? 4200 : 4000; artFog.color.copy(horizon);
    if (!dusk) {
      const luminance = 0.2126 * horizon.r + 0.7152 * horizon.g + 0.0722 * horizon.b;
      artFog.color.lerp(neutralHaze.setRGB(luminance, luminance, luminance), 0.12);
    }
    weatherFog.color.copy(artFog.color).lerp(dusk ? FOG_DUSK : FOG_DAY, 0.35);
    scene.fog = visibilityFog(artFog, weatherFog, state.visibilityM, state.observerOffsetM);
    daySky.material.uniforms.horizon.value.copy(scene.fog!.color);
    scene.background = scene.fog!.color;
    scene.userData.weatherWet = rain;
    scene.userData.dusk = dusk;
  };
  scene.userData.lighting = { setAtmosphere };
  setAtmosphere({ dusk: false, rain: 0, visibilityM: null });
  return { sun, fill, grid, ground, sky, daySky, horizon, setAtmosphere,
    setDusk: (dusk: boolean) => setAtmosphere({ ...current!, dusk }), update: (target: T.Vector3) => {
    const texel = 500 / sun.shadow.mapSize.x;
    sun.target.position.set(Math.round(target.x / texel) * texel, target.y, Math.round(target.z / texel) * texel);
    sun.position.copy(sun.target.position).addScaledVector(sunDirection, 650);
    sky.position.copy(target); daySky.position.copy(target); ground.position.x=target.x; ground.position.z=target.z;
  }, dispose: () => environment.dispose() };
}

/** Existing city display hooks share one reversible wet-material adjustment. */
export function applyWeatherMaterials(object: T.Object3D, wet: number, dusk: boolean) {
  object.traverse(node => { if (node instanceof T.Mesh) for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
    const uniform = material.userData.displayDusk as { value: number } | undefined;
    if (uniform) uniform.value = dusk ? 1 : 0;
    const standard = material as T.MeshStandardMaterial;
    if (!(standard.color instanceof T.Color)) continue;
    // Material.copy/clone JSON-copies userData. A Color there becomes a hex number;
    // Color.copy(number) then produces NaNs that fog and tone mapping propagate.
    // Store the exact linear RGB components so quality changes preserve the dry finish.
    const base = standard.userData.weatherBase as { color: [number, number, number]; roughness?: number } | undefined;
    if (wet <= 0) {
      if (base) { standard.color.fromArray(base.color); if (base.roughness !== undefined) standard.roughness = base.roughness; delete standard.userData.weatherBase; }
      continue;
    }
    const source = base ?? (standard.userData.weatherBase = { color: standard.color.toArray(), roughness: standard.roughness });
    standard.color.fromArray(source.color).multiplyScalar(1 - 0.08 * wet);
    if (source.roughness !== undefined) standard.roughness = Math.max(0, source.roughness - 0.1 * wet);
  } });
}
