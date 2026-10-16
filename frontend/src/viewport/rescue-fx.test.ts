import { describe, expect, it, vi } from 'vitest';
import * as T from 'three';
import type { FeedCommit, RunHeader } from '../contracts/viewer-feed';
import { FeedStore } from './feed-store';
import { semanticFlooding, semanticMarkers, semanticLinks } from './semantic';
import { SemanticLayer } from './semantic-layer';

vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
  beginPath() {}, closePath() {}, arc() {}, fill() {}, stroke() {}, moveTo() {}, lineTo() {}, bezierCurveTo() {}, fillRect() {},
} as unknown as ReturnType<HTMLCanvasElement['getContext']>);

const at = { ns: '0', microstep: 0 };
const key = (id: string, typeId: string) => ({ id, generation: 0, typeId });
const header: RunHeader = {
  contract: 'aeroagentsim.viewer-feed/v1', runId: 'flood', registryDigest: 'test', start: at,
  types: [
    { typeId: 'lowalt:Environment', displayName: 'Environment', ancestors: [] },
    { typeId: 'lowalt:Site', displayName: 'Site', ancestors: [] },
    { typeId: 'lowalt:UAV', displayName: 'UAV', ancestors: [] },
  ],
  fields: [], presentation: [{ typeId: 'lowalt:UAV', positionField: 'position', frame: 'enu', visual: { kind: 'marker' } }],
};

function store(fields: Record<string, { id: string; typeId: string; values: Record<string, unknown> }>): FeedStore {
  const feed = new FeedStore(header);
  const created: FeedCommit['created'] = [], facts: FeedCommit['facts'] = [];
  for (const [id, entry] of Object.entries(fields)) {
    created.push(key(entry.id ?? id, entry.typeId));
    for (const [fieldId, value] of Object.entries(entry.values)) facts.push({ entity: { id: entry.id, generation: 0 }, fieldId, value, producer: 'recorded', validFrom: at });
  }
  feed.ingest({ commitIndex: 1, at, created, removed: [], facts, retracted: [], edges: [], messages: [], receipts: [] });
  feed.seek('0');
  return feed;
}

const origin = new T.Vector3();

function floodStore() {
  return store({
    env: { id: 'env', typeId: 'lowalt:Environment', values: {
      'rescue.flood_bounds': [-40, -40, 0, 40, 40, 1.5],
      'rescue.flooded_roads': [{ start: [50, 0, 0], end: [80, 0, 0] }, { start: [0, 0, 0], end: [10, 0, 0] }],
      'rescue.markers': [
        { id: 'inspect-1', kind: 'inspect', position: [60, 0, 20] },
        { id: 'relay-1', kind: 'relay', position: [0, 40, 0] },
        { id: 'command', kind: 'command', position: [0, 0, 0] },
      ],
      'rescue.links': [
        { id: 'l1', from: 'uav-2', to: 'centre', kind: 'report', state: 'sending' },
        { id: 'l2', from: 'uav-relay-1', to: 'centre', kind: 'relay_coverage', state: 'holding' },
      ],
    } },
    incident: { id: 'incident-1', typeId: 'lowalt:Site', values: { 'rescue.incident_state': 'confirmed', 'lowalt.site_position': [20, 12, 30] } },
    uav2: { id: 'uav-2', typeId: 'lowalt:UAV', values: { position: [55, 30, 10] } },
    centre: { id: 'centre', typeId: 'lowalt:Site', values: { 'lowalt.site_position': [0, 0, 0] } },
  });
}

describe('flood and rescue visuals from C1 fields', () => {
  it('renders water planes without any outline LineLoop', () => {
    const feed = floodStore(), layer = new SemanticLayer(header, { locale: 'en-US' });
    try {
      layer.update(feed, '0', origin);
      const water = layer.flood.children.filter(child => child instanceof T.Mesh && (child as T.Mesh).material instanceof T.ShaderMaterial);
      expect(water.length).toBe(1); // one batch: area plane plus out-of-area road ribbon
      for (const object of layer.flood.children) expect(object).not.toBeInstanceOf(T.LineLoop);
      const plane = water[0] as T.Mesh, material = plane.material as T.ShaderMaterial;
      expect(material.transparent).toBe(true);
      expect(material.depthWrite).toBe(false);
      expect('fog' in material && material.fog).toBe(true);
      // Water sits at the area's top height.
      expect(plane.position.y).toBeCloseTo(1.5);
    } finally { layer.dispose(); }
  });
  it('preserves the flood palette and premultiplied transparency for the shared water', () => {
    const feed = floodStore(), layer = new SemanticLayer(header, {});
    try {
      layer.update(feed, '0', origin);
      const mesh = layer.flood.children.find(child => child instanceof T.Mesh) as T.Mesh<T.BufferGeometry, T.ShaderMaterial>;
      const material = mesh.material;
      expect(material.uniforms.uBody.value.getHexString()).toBe('3e5654');
      expect(material.uniforms.uShallow.value.getHexString()).toBe('6f7a6a');
      expect(material.uniforms.uFoam.value.getHexString()).toBe('d8d2bc');
      expect(material.uniforms.uSky.value.getHexString()).toBe('2b3440');
      expect(material.premultipliedAlpha).toBe(true);
      expect(material.blending).toBe(T.NormalBlending);
    } finally { layer.dispose(); }
  });
  it('keeps the shared water material across every patch', () => {
    const feed = floodStore(), layer = new SemanticLayer(header, {});
    try {
      layer.update(feed, '0', origin);
      const materials = new Set(layer.flood.children.map(child => (child as T.Mesh).material));
      expect(materials.size).toBe(1);
      // Sim time drives the shader, including seeks while playback is paused.
      layer.setSimTime(4, true); const running = (materials.values().next().value as T.ShaderMaterial).uniforms.uTime.value;
      expect(running).toBe(4);
      layer.setSimTime(9, false);
      expect((materials.values().next().value as T.ShaderMaterial).uniforms.uTime.value).toBe(9);
    } finally { layer.dispose(); }
  });
  it('replaces the incident ring/beacon with a screen-space pin and adds the active pulse ring', () => {
    const feed = floodStore(), layer = new SemanticLayer(header, {});
    try {
      layer.update(feed, '0', origin);
      expect([...layer.flood.children]).toHaveLength(1); // merged water only; no beacon cone or ring
      const pins = layer.rescue.children.filter(child => child instanceof T.Sprite && !child.userData.rescueMarker);
      expect(pins).toHaveLength(1);
      const sprite = pins[0] as T.Sprite;
      expect(sprite.material.sizeAttenuation).toBe(false);
      expect(sprite.material.depthTest).toBe(false);
      // The pin lifts off the roof and a leader line drops back to it.
      expect(sprite.position.y).toBeGreaterThan(0);
      const incidentLeaders = layer.rescue.children.filter(child => child instanceof T.Line && !(child as T.Line).userData.rescueMarker);
      expect(incidentLeaders).toHaveLength(1);
      layer.setPinScale(900);
      expect(sprite.scale.x).toBeCloseTo(26 / 900 * 2);
      // Pin scale > 0 right after update with a viewport height: the pin is visible.
      expect(sprite.scale.x).toBeGreaterThan(0);
      // The active roof pulse ring: radius 6 m, thin line.
      const pulse = layer.rescue.children.find(child => child instanceof T.Mesh && !(child as T.Mesh).userData.rescueMarker) as T.Mesh;
      expect(pulse.geometry).toBeInstanceOf(T.RingGeometry);
      expect((pulse.geometry as T.RingGeometry).parameters.innerRadius).toBeCloseTo(6, 1);
      const rings = layer.rescue.children.filter(child => child instanceof T.Mesh && !child.userData.rescueMarker);
      expect(rings).toHaveLength(1); // pulse ring, active state 'confirmed'
      expect(layer.incidentLabels.has('incident-1:0')).toBe(true);
    } finally { layer.dispose(); }
  });
  it('skips the pulse ring for resolved incidents', () => {
    const feed = floodStore();
    const resolved = store({
      incident: { id: 'incident-1', typeId: 'lowalt:Site', values: { 'rescue.incident_state': 'delivered', 'lowalt.site_position': [20, 12, 30] } },
    });
    void feed;
    const layer = new SemanticLayer(header, {});
    try {
      layer.update(resolved, '0', origin);
      expect(layer.rescue.children.filter(child => child instanceof T.Mesh)).toHaveLength(0);
    } finally { layer.dispose(); }
  });
  it('draws inspect/relay markers as 18 px screen sprites with leaders to the ground', () => {
    const feed = floodStore(), layer = new SemanticLayer(header, {});
    try {
      layer.update(feed, '0', origin, new Map([['uav-2:0', new T.Vector3(55, 30, 10)]]));
      // command sits at [0,0,0] where the centre site is registered -> skipped
      expect([...layer.glyphSprites.values()].map(sprite => sprite.userData.rescueMarkerKind)).toEqual(['inspect', 'relay']);
      for (const sprite of layer.glyphSprites.values()) {
        expect(sprite.material.sizeAttenuation).toBe(false);
        expect(sprite.material.depthTest).toBe(false);
      }
      layer.setPinScale(900);
      // 18 CSS px glyphs: 18/26 of the pin's NDC scale.
      for (const sprite of layer.glyphSprites.values()) expect(sprite.scale.x).toBeCloseTo(26 / 900 * 2 * 18 / 26);
      // One leader per drawn marker, down to the ground.
      const leaders = layer.rescue.children.filter(child => child instanceof T.Line && (child as T.Line).userData.rescueMarker);
      expect(leaders).toHaveLength(2);
      for (const leader of leaders) expect(((leader as T.Line).material as T.LineBasicMaterial).depthTest).toBe(false);
      // No world-space diamond ring mesh remains.
      expect(layer.rescue.children.filter(child => child instanceof T.Mesh && (child as T.Mesh).userData.rescueMarker)).toHaveLength(0);
    } finally { layer.dispose(); }
  });
  it('draws all links as ONE screen-space ribbon with width/halo uniforms and flowing dashes', () => {
    const feed = floodStore(), layer = new SemanticLayer(header, {});
    const live = new Map([['uav-2:0', new T.Vector3(55, 30, 10)], ['centre:0', new T.Vector3(0, 0, 0)]]);
    try {
      layer.update(feed, '0', origin, live);
      expect(layer.linkLines.size).toBe(2);
      // Exactly one draw: one merged ribbon mesh in the links group.
      const meshes = layer.links.children.filter(child => child instanceof T.Mesh);
      expect(meshes).toHaveLength(1);
      const mesh = meshes[0] as T.Mesh<T.BufferGeometry, T.ShaderMaterial>;
      const material = mesh.material;
      // Screen-space width comes from uniforms, not per-link world geometry.
      expect(material.uniforms.uResolution.value).toBeInstanceOf(T.Vector2);
      expect(material.uniforms.uPixelRatio.value).toBeGreaterThan(0);
      const width = mesh.geometry.getAttribute('aStyle') as T.BufferAttribute;
      const from = mesh.geometry.getAttribute('aFrom') as T.BufferAttribute;
      const before = from.array.slice();
      live.get('uav-2:0')!.set(60, 32, 12);
      layer.update(feed, '0', origin, live);
      expect(from.array).not.toEqual(before);
      // 3 CSS px for sending/holding, 2 px delivered; the 6 px halo only on sending.
      const states = [...layer.linkLines.values()];
      expect(states.map(entry => entry.state).sort()).toEqual(['holding', 'sending']);
      const sendingIndex = states.findIndex(entry => entry.state === 'sending');
      expect(width.getY(sendingIndex * 6)).toBe(3);
      expect(width.getZ(sendingIndex * 6)).toBe(3); // 6 px total halo => 3 half-width
      const holdingIndex = states.findIndex(entry => entry.state === 'holding');
      expect(width.getY(holdingIndex * 6)).toBe(3);
      expect(width.getZ(holdingIndex * 6)).toBe(0);
      // Flow flag is set on sending, static otherwise; dash params live in the shader.
      expect(width.getW(sendingIndex * 6)).toBe(1);
      expect(width.getW(holdingIndex * 6)).toBe(0);
      expect(layer.animates()).toBe(true);
    } finally { layer.dispose(); }
  });
  it('renders a 2 px delivered link without halo or flow', () => {
    const feed = store({
      centre: { id: 'centre', typeId: 'lowalt:Site', values: { 'lowalt.site_position': [0, 0, 0] } },
      uav2: { id: 'uav-2', typeId: 'lowalt:UAV', values: { position: [55, 30, 10] } },
      env: { id: 'env', typeId: 'lowalt:Environment', values: {
        'rescue.links': [{ id: 'ack', from: 'uav-2', to: 'centre', kind: 'delivery_ack', state: 'delivered' }],
      } },
    });
    const live = new Map([['uav-2:0', new T.Vector3(55, 30, 10)], ['centre:0', new T.Vector3(0, 0, 0)]]);
    const layer = new SemanticLayer(header, {});
    try {
      layer.update(feed, '0', origin, live);
      const mesh = layer.links.children.find(child => child instanceof T.Mesh) as T.Mesh<T.BufferGeometry, T.ShaderMaterial>;
      const width = mesh.geometry.getAttribute('aStyle') as T.BufferAttribute;
      expect(width.getY(0)).toBe(2);
      expect(width.getZ(0)).toBe(0);
      expect(width.getW(0)).toBe(0);
      expect(layer.animates()).toBe(false);
    } finally { layer.dispose(); }
  });
  it('keeps submerged-road ribbons outside the flood areas only', () => {
    const feed = floodStore(), layer = new SemanticLayer(header, {});
    try {
      layer.update(feed, '0', origin);
      // The in-area road [0,0,0]->[10,0,0] must not be drawn: only the area
      // plane and the out-of-area ribbon reach the geometry, merged into one.
      const water = layer.flood.children.filter(child => child instanceof T.Mesh && (child as T.Mesh).material instanceof T.ShaderMaterial);
      expect(water).toHaveLength(1);
      // Count area + out-of-area patches from the merged geometry's kind attribute.
      const kinds = (water[0] as T.Mesh).geometry.getAttribute('aKind') as T.BufferAttribute;
      let kind1 = 0, kind0 = 0;
      for (let i = 0; i < kinds.count; i++) (kinds.getX(i) ? kind1++ : kind0++);
      expect(kind0).toBeGreaterThan(0); // the area plane survives
      expect(kind1).toBeGreaterThan(0); // exactly one out-of-area road ribbon
      // A road fully inside a second area is dropped entirely.
      const inside = store({
        env: { id: 'env', typeId: 'lowalt:Environment', values: {
          'rescue.flood_bounds': [-40, -40, 0, 40, 40, 1.5],
          'rescue.flooded_roads': [{ start: [0, 0, 0], end: [10, 0, 0] }],
        } },
      });
      const layer2 = new SemanticLayer(header, {});
      try {
        layer2.update(inside, '0', origin);
        const only = layer2.flood.children.filter(child => child instanceof T.Mesh);
        expect(only).toHaveLength(1);
        const onlyKinds = (only[0] as T.Mesh).geometry.getAttribute('aKind') as T.BufferAttribute;
        for (let i = 0; i < onlyKinds.count; i++) expect(onlyKinds.getX(i)).toBe(0); // area plane only, no ribbon
      } finally { layer2.dispose(); }
    } finally { layer.dispose(); }
  });
  it('produces the bilingual flood legend', () => {
    const feed = floodStore(), flooding = semanticFlooding(feed);
    const markers = semanticMarkers(feed), links = semanticLinks(feed);
    const en = SemanticLayer.floodLegend({ areas: flooding.areas.length, roads: flooding.roads.length, incidents: flooding.incidents.length, markers, links });
    const zh = SemanticLayer.floodLegend({ areas: flooding.areas.length, roads: flooding.roads.length, incidents: flooding.incidents.length, markers, links }, 'zh-CN');
    const enLabels = en.map(entry => entry[0]);
    expect(enLabels).toEqual(expect.arrayContaining(['Flood water', 'Submerged road', 'Rooftop survivor', 'Inspection point', 'Relay', 'Report link']));
    expect(en.find(entry => entry[0] === 'Acknowledgement link')).toBeUndefined(); // no ack link in the fixture
    expect(zh.map(entry => entry[0])).toEqual(expect.arrayContaining(['积水', '被淹道路', '屋顶求援', '复查点', '中继', '报告链路']));
    expect(en.find(entry => entry[0] === 'Report link')![1]).toBe('#f0a020');
  });
});
