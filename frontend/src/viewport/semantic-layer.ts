import * as T from 'three';
import type { EntityKey, RunHeader } from '../contracts/viewer-feed';
import type { EntityState, FeedStore } from './feed-store';
import { worldPosition } from './coordinates';
import { activeZones, humanizeId, semanticSites, semanticFlooding, semanticMarkers, semanticLinks, rescueLabel, rescueIncidentColor, rescueLinkColor, type SemanticZone, type SemanticSite, type SemanticMarker, type SemanticLink } from './semantic';
import { translate } from '../i18n/translate';
import { chargingPadAssignments } from './operational-loads';
import { waterMaterial, waterGeometry, roadWaterGeometry, rescuePinTexture, markerGlyphTexture, screenSprite, pulseMaterial, leaderMaterial, linkLineMaterial, linkRibbonGeometry, mergeWater, type LinkRibbon } from './rescue-fx';

/**
 * Newcomer semantic overlay: active airspace zones, site markers and
 * flood-rescue visuals rendered from actual committed lowalt.* and rescue.*
 * fields. Display-only: never mutates simulation state, never touches city
 * materials. All positions pass through worldPosition/ENU and the caller's
 * renderOrigin, exactly like EntityLayer.
 */

export interface SemanticLayerOptions {
  locale?: string;
  onSelect?: (key: EntityKey) => void;
}

const ZONE_STYLE: Record<SemanticZone['kind'], { color: number; boxOpacity: number; label: string }> = {
  no_fly: { color: 0xe4572e, boxOpacity: 0.14, label: 'No-fly zone' },
  restricted: { color: 0xe69f00, boxOpacity: 0.1, label: 'Restricted zone' },
  altitude_band: { color: 0x56b4e9, boxOpacity: 0.08, label: 'Altitude band' },
  corridor: { color: 0x009e73, boxOpacity: 0, label: 'Corridor' },
};

const INCIDENT_ACTIVE_STATES = new Set(['suspected', 'observing', 'confirmed', 'delivering']);
/** Role colours from the same palette businessStyle uses, so the legend stays coherent. */
const MARKER_COLORS: Record<SemanticMarker['kind'], string> = { inspect: '#56b4e9', relay: '#cc79a7', command: '#8fa3b8' };
const LINK_WIDTH_PX: Record<SemanticLink['state'], number> = { sending: 3, delivered: 2, failed: 3, holding: 3 };
const LINK_HALO_PX = 3; // 6 px total soft halo behind `sending` hops.
/** Survivor pin lifts 14 m above the incident point (the roof), like the fire pin. */
const PIN_LIFT = 14, GLYPH_LIFT = 10;

interface LinkEntry {
  state: SemanticLink['state']; link: SemanticLink; from: LinkEndpoint; to: LinkEndpoint;
  slot: number; visible: boolean; lastFrom: T.Vector3; lastTo: T.Vector3; alpha: number; dirty: boolean;
}

interface LinkEndpoint { id: string; key?: string; liveKey?: string; supplied?: T.Vector3; entity?: EntityState; position: T.Vector3 }

function findEndpointEntity(this: LinkEndpoint, entity: EntityState) {
  if (!this.entity && entity.key.id === this.id) {
    this.entity = entity;
    this.key = `${entity.key.id}:${entity.key.generation}`;
  }
}

function findEndpointPosition(this: LinkEndpoint, position: T.Vector3, key: string) {
  if (!this.supplied && key.startsWith(this.id) && key.lastIndexOf(':') === this.id.length) {
    this.liveKey = key;
    this.supplied = position;
  }
}

export class SemanticLayer {
  readonly root = new T.Group();
  readonly pickables: T.Object3D[] = [];
  /** Entity id -> world position (render-origin-relative) for hover labels. */
  readonly markerPositions = new Map<string, T.Vector3>();
  readonly markerKeys = new Map<string, EntityKey>();
  readonly incidentLabels = new Map<string, { key: EntityKey; position: T.Vector3; text: string; color: string }>();
  private zones = new T.Group();
  private markers = new T.Group();
  /** Visible to tests that assert the flood look. */
  readonly flood = new T.Group();
  readonly rescue = new T.Group();
  readonly links = new T.Group();
  flooding?: ReturnType<typeof semanticFlooding>;
  rescueMarkers: SemanticMarker[] = [];
  activeZones: SemanticZone[] = [];
  sites: SemanticSite[] = [];
  private zoneBoxes: T.Mesh[] = [];
  private zoneLines: T.LineSegments[] = [];
  private markerGlyphs = new Map<string, T.Mesh>();
  private padGlows: T.Mesh[] = [];
  get chargingPadCount() { return this.padGlows.length; }
  private origin = new T.Vector3();
  private disposed = false;
  private lastRevision = -1;
  private localeChanged = false;
  private chargingPads = new Set<string>();
  private chargingInputs: unknown[] = [];
  private chargingIndex = 0;
  private chargingChanged = false;
  private anyCharging = false;
  private lastChargingMessage?: FeedStore['messages'][number];
  /** Live entity positions (render-origin-relative), filled per frame by update. */
  private live = new Map<string, T.Vector3>();
  linkLines = new Map<string, LinkEntry>();
  glyphSprites = new Map<string, T.Sprite>();
  private pins = new Map<string, T.Sprite>();
  private pulses: T.Mesh[] = [];
  private ribbon?: LinkRibbon;
  private ribbonMesh?: T.Mesh;
  /** Link entries in draw order, rebuilt only with the committed cut. */
  private orderedLinks: LinkEntry[] = [];
  private linkMaterial = linkLineMaterial();
  private flowingLinks = false;
  private pinScale = 26 / 900 * 2;
  private pixelRatio = 1;
  private pulseMaterial?: T.ShaderMaterial;
  private water?: T.ShaderMaterial;
  private leaderMaterial = leaderMaterial();
  private spriteMaterials = new Map<T.Texture, T.SpriteMaterial>();

  constructor(private header: RunHeader, private options: SemanticLayerOptions = {}) {
    // Water renders first; screen-space pins/links draw over everything.
    this.flood.renderOrder = 2;
    this.root.add(this.zones, this.markers, this.flood, this.rescue, this.links);
    this.links.renderOrder = 10;
    this.rescue.renderOrder = 12;
  }

  setLocale(locale: string) {
    if (this.options.locale === locale) return;
    this.options.locale = locale;
    this.localeChanged = true;
  }

  /** Sky tone for fresnel reflections, from the scene fog or background colour. */
  setSky(color: T.Color | number | string | undefined) {
    const material = this.waterMaterial();
    if (color === undefined) return;
    if (color instanceof T.Color) material.uniforms.uSky.value.copy(color);
    else material.uniforms.uSky.value.set(color as string);
  }

  /** Viewport size drives the screen-space link ribbon widths. */
  setSize(widthCssPx: number, heightCssPx: number, pixelRatio = 1) {
    this.pixelRatio = Math.max(pixelRatio, 0.001);
    const uniforms = this.linkMaterial.uniforms;
    (uniforms.uResolution.value as T.Vector2).set(Math.max(1, widthCssPx), Math.max(1, heightCssPx));
    uniforms.uPixelRatio.value = this.pixelRatio;
  }

  private waterMaterial(): T.ShaderMaterial {
    this.water ??= waterMaterial();
    return this.water;
  }

  /** Simulation time also follows paused seeks; a paused clock itself stays fixed. */
  setSimTime(seconds: number, _running: boolean) {
    if (this.water) this.water.uniforms.uTime.value = seconds;
    if (this.pulseMaterial) this.pulseMaterial.uniforms.uTime.value = seconds;
    this.linkMaterial.uniforms.uTime.value = seconds;
  }

  /** True while anything here animates in simulation time: water, pulse or flowing dashes. */
  animates(): boolean {
    // Only `sending` dashes flow; failed/holding/delivered links are static.
    return this.flood.children.length > 0 || this.pulses.length > 0 || this.flowingLinks;
  }

  /** Rebuild zone/site visuals when the committed cut actually changed. */
  update(store: FeedStore, ns: string, renderOrigin: T.Vector3, livePositions?: Map<string, T.Vector3>) {
    if (this.disposed) return;
    const originChanged = !this.origin.equals(renderOrigin);
    this.origin.copy(renderOrigin);
    if (livePositions) this.live = livePositions;
    if (store !== this.currentStore || store.revision !== this.lastRevision || originChanged || this.localeChanged) {
      this.currentStore = store;
      const zones = activeZones(this.header, store);
      if (originChanged || zones !== this.activeZones) this.rebuildZones(zones, renderOrigin);
      this.activeZones = zones;
      const sites = semanticSites(this.header, store);
      this.anyCharging = this.chargingChanged = false;
      this.chargingIndex = 0;
      store.entities.forEach(this.collectCharging, this);
      this.chargingChanged = this.chargingInputs.length !== this.chargingIndex || this.chargingChanged;
      this.chargingInputs.length = this.chargingIndex;
      const message = store.messages[store.messages.length - 1];
      let padsChanged = false;
      if (this.anyCharging && (this.chargingChanged || message !== this.lastChargingMessage || sites !== this.sites)) {
        const pads = new Set(chargingPadAssignments(store).values());
        padsChanged = pads.size !== this.chargingPads.size;
        for (const pad of pads) if (!this.chargingPads.has(pad)) padsChanged = true;
        this.chargingPads = pads;
      } else if (!this.anyCharging && this.chargingPads.size) { this.chargingPads.clear(); padsChanged = true; }
      this.lastChargingMessage = message;
      if (originChanged || sites !== this.sites || padsChanged || this.localeChanged) {
        this.rebuildMarkers(sites.filter(site => site.isVertiport || site.isPad), this.chargingPads, renderOrigin);
      }
      const flooding = semanticFlooding(store);
      if (originChanged || flooding !== this.flooding || this.localeChanged) this.rebuildFlood(flooding, renderOrigin);
      this.flooding = flooding;
      const markers = semanticMarkers(store);
      if (originChanged || markers !== this.rescueMarkers || sites !== this.sites || this.localeChanged) this.rebuildRescue(markers, renderOrigin);
      this.rescueMarkers = markers;
      this.sites = sites;
      const links = semanticLinks(store);
      if (links !== this.currentLinks) this.rebuildLinks(links);
      this.lastRevision = store.revision;
      this.localeChanged = false;
    }
    this.moveLinks();
  }

  private collectCharging(entity: EntityState) {
    const value = entity.fields.get('lowalt.charging')?.value;
    this.chargingChanged = this.chargingInputs[this.chargingIndex] !== entity || this.chargingChanged;
    this.chargingInputs[this.chargingIndex++] = entity;
    this.chargingChanged = this.chargingInputs[this.chargingIndex] !== value || this.chargingChanged;
    this.chargingInputs[this.chargingIndex++] = value;
    if (value === true) this.anyCharging = true;
  }

  private clearFlood() {
    this.flood.traverse(object => {
      if (object instanceof T.Mesh || object instanceof T.Line) object.geometry.dispose();
    });
    this.flood.clear();
    this.incidentLabels.clear();
    for (const child of [...this.rescue.children]) if (child.userData.rescueIncident) {
      child.removeFromParent();
      if (child instanceof T.Mesh || child instanceof T.Line) child.geometry.dispose();
    }
    this.pins.clear();
    this.pulses.length = 0;
  }

  private rebuildFlood(flooding: ReturnType<typeof semanticFlooding>, renderOrigin: T.Vector3) {
    this.clearFlood();
    const material = this.waterMaterial();
    const toWorld = (point: [number, number, number]) => worldPosition(point, 'enu', this.header.origin).sub(renderOrigin);
    for (const area of flooding.areas) {
      const lo = toWorld(area.lower), hi = toWorld(area.upper);
      // One plane at the area's top height: buildings rise out of the water
      // and the waterline comes for free from depth. No outline LineLoop.
      const width = Math.abs(hi.x - lo.x), depth = Math.abs(hi.z - lo.z);
      const water = new T.Mesh(waterGeometry(width, depth, 0), material);
      water.position.set((lo.x + hi.x) / 2, hi.y, (lo.z + hi.z) / 2);
      water.renderOrder = 2;
      this.flood.add(water);
    }
    const areas = flooding.areas.map(area => {
      const lo = toWorld(area.lower), hi = toWorld(area.upper);
      return new T.Box3(new T.Vector3(Math.min(lo.x, hi.x), Math.min(lo.y, hi.y), Math.min(lo.z, hi.z)),
        new T.Vector3(Math.max(lo.x, hi.x), Math.max(lo.y, hi.y), Math.max(lo.z, hi.z)));
    });
    for (const road of flooding.roads) {
      const start = toWorld(road.start), end = toWorld(road.end);
      if (!Math.hypot(end.x - start.x, end.z - start.z)) continue;
      const geometry = roadWaterGeometry(start, end, areas);
      if (!geometry) continue;
      const ribbon = new T.Mesh(geometry, material);
      ribbon.position.set((start.x + end.x) / 2, Math.max(start.y, end.y) + 0.3, (start.z + end.z) / 2);
      ribbon.renderOrder = 2;
      this.flood.add(ribbon);
    }
    mergeWater(this.flood);
    this.rebuildIncidents(flooding, renderOrigin);
  }

  private rebuildIncidents(flooding: ReturnType<typeof semanticFlooding>, renderOrigin: T.Vector3) {
    void renderOrigin;
    for (const incident of flooding.incidents) {
      const position = worldPosition(incident.position, 'enu', this.header.origin).sub(this.origin), color = rescueIncidentColor(incident.state);
      // Screen-space pin replaces the old ring + beacon: fixed 26 CSS px,
      // drawn over the water (depthTest off), lifted 14 m with a leader line
      // down to the roof — exactly like the fire pin in the atmosphere layer.
      const sprite = this.sprite(rescuePinTexture(color), '#ffffff', this.pinScale);
      sprite.position.copy(position).y += PIN_LIFT;
      sprite.userData.rescueIncident = incident.key.id;
      this.rescue.add(sprite);
      this.pins.set(`${incident.key.id}:${incident.key.generation}`, sprite);
      const leader = new T.Line(new T.BufferGeometry().setFromPoints([position.clone(), position.clone().setY(position.y + PIN_LIFT)]), this.leaderMaterial);
      leader.renderOrder = 11;
      leader.frustumCulled = false;
      leader.userData.rescueIncident = incident.key.id;
      this.rescue.add(leader);
      if (INCIDENT_ACTIVE_STATES.has(incident.state)) {
        this.pulseMaterial ??= pulseMaterial(this.linkMaterial.uniforms.uResolution.value);
        // Roof pulse ring: radius 6 m, ~2 px line.
        const pulse = new T.Mesh(new T.RingGeometry(5.99, 6, 48), this.pulseMaterial);
        pulse.rotation.x = -Math.PI / 2;
        pulse.position.copy(position);
        pulse.renderOrder = 3;
        pulse.userData.rescueIncident = incident.key.id;
        this.rescue.add(pulse);
        this.pulses.push(pulse);
      }
      this.incidentLabels.set(`${incident.key.id}:${incident.key.generation}`, {
        key: incident.key, position: position.clone().add(new T.Vector3(0, PIN_LIFT + 2, 0)), color,
        text: `${this.options.locale?.startsWith('zh') ? '屋顶求援' : 'Rooftop rescue'} · ${rescueLabel(incident.state, this.options.locale)}`,
      });
    }
  }

  /** Pins/glyph sprites resize in screen space: fixed CSS px at any distance.
   * Same NDC math as the atmosphere pin: 26 / viewport height × 2. */
  setPinScale(heightCssPx: number, verticalProjection = 1) {
    const scale = 26 / Math.max(1, heightCssPx) * 2 / verticalProjection;
    if (scale === this.pinScale) return;
    this.pinScale = scale;
    for (const sprite of this.pins.values()) sprite.scale.set(this.pinScale, this.pinScale, 1);
    for (const sprite of this.glyphSprites.values()) sprite.scale.set(this.pinScale * 18 / 26, this.pinScale * 18 / 26, 1);
    // Keep the ribbon width uniforms live even when only the height is known.
    const resolution = this.linkMaterial.uniforms.uResolution.value as T.Vector2;
    resolution.y = Math.max(1, heightCssPx);
    if (resolution.x <= 1) resolution.x = Math.max(1, heightCssPx);
  }

  private rebuildRescue(markers: SemanticMarker[], renderOrigin: T.Vector3) {
    for (const child of [...this.rescue.children]) {
      if (child.userData.rescueMarker) {
        child.removeFromParent();
        if (child instanceof T.Line) child.geometry.dispose();
      }
      if (child instanceof T.Mesh && child.userData.rescueMarker) {
        child.removeFromParent();
        child.geometry.dispose();
      }
    }
    this.glyphSprites.clear();
    if (this.pickables.some(object => object.userData.rescueMarker)) this.pickables.splice(0, this.pickables.length, ...this.pickables.filter(object => !object.userData.rescueMarker));
    const toWorld = (point: [number, number, number]) => worldPosition(point, 'enu', this.header.origin).sub(renderOrigin);
    // Command markers yield to any committed site already at that position.
    const sitePositions = semanticSites(this.header, this.currentStore!).map(site => toWorld(site.position));
    for (const marker of markers) {
      const position = toWorld(marker.position);
      // All three marker kinds are 18 CSS px screen sprites with depthTest
      // off (visible over the water) and a thin leader down to the ground.
      if (marker.kind === 'command' && sitePositions.some(site => site.distanceTo(position) < 1)) continue;
      const color = MARKER_COLORS[marker.kind];
      const sprite = this.sprite(markerGlyphTexture(marker.kind), color, this.pinScale * 18 / 26);
      sprite.position.copy(position).y += GLYPH_LIFT;
      sprite.userData.rescueMarker = marker.id;
      sprite.userData.rescueMarkerKind = marker.kind;
      this.rescue.add(sprite);
      this.glyphSprites.set(marker.id, sprite);
      const ground = toWorld([marker.position[0], marker.position[1], 0]);
      const leader = new T.Line(new T.BufferGeometry().setFromPoints([sprite.position.clone(), ground]), this.leaderMaterial);
      leader.renderOrder = 11;
      leader.frustumCulled = false;
      leader.userData.rescueMarker = marker.id;
      this.rescue.add(leader);
    }
  }

  private currentStore?: FeedStore;
  currentLinks: SemanticLink[] = [];

  /** Link endpoints follow the live UAV/centre positions every frame. */
  private rebuildLinks(links: SemanticLink[]) {
    this.currentLinks = links;
    this.flowingLinks = false;
    // One draw for all links: a single preallocated ribbon of quads, grown
    // only when a larger committed cut arrives.
    if (!this.ribbon || this.ribbon.capacity < links.length) {
      this.disposeRibbon();
      this.ribbon = linkRibbonGeometry(Math.max(links.length, 64));
      this.ribbonMesh = new T.Mesh(this.ribbon.geometry, this.linkMaterial);
      this.ribbonMesh.frustumCulled = false;
      this.ribbonMesh.renderOrder = 10;
      this.links.add(this.ribbonMesh);
    }
    const seen = new Set<string>();
    this.orderedLinks.length = 0;
    for (const link of links) {
      if (link.state === 'sending') this.flowingLinks = true;
      seen.add(link.id);
      let entry = this.linkLines.get(link.id);
      if (!entry) {
        entry = { state: link.state, link, from: this.endpoint(link.from), to: this.endpoint(link.to),
          slot: 0, visible: false, lastFrom: new T.Vector3(), lastTo: new T.Vector3(), alpha: 0, dirty: true };
        this.linkLines.set(link.id, entry);
      }
      entry.state = link.state;
      if (entry.link.from !== link.from) entry.from = this.endpoint(link.from);
      if (entry.link.to !== link.to) entry.to = this.endpoint(link.to);
      entry.link = link;
      entry.slot = this.orderedLinks.length;
      entry.visible = false;
      entry.dirty = true;
      entry.alpha = link.state === 'sending' || link.state === 'failed' ? 0.9 : link.state === 'holding' ? 0.35 : 0.2;
      this.ribbon.style(entry.slot, rescueLinkColor(link), 0, LINK_WIDTH_PX[link.state],
        link.state === 'sending' ? LINK_HALO_PX : 0, link.state === 'sending' ? 1 : link.state === 'failed' ? -1 : 0);
      this.orderedLinks.push(entry);
    }
    for (const id of this.linkLines.keys()) if (!seen.has(id)) this.linkLines.delete(id);
    this.ribbon.setVisible(links.length);
  }

  private sprite(texture: T.Texture, color: string, scale: number): T.Sprite {
    let material = this.spriteMaterials.get(texture);
    if (!material) {
      const sprite = screenSprite(texture, color, scale);
      this.spriteMaterials.set(texture, sprite.material);
      return sprite;
    }
    const sprite = new T.Sprite(material);
    sprite.renderOrder = 12;
    sprite.scale.set(scale, scale, 1);
    return sprite;
  }

  private endpoint(id: string): LinkEndpoint { return { id, position: new T.Vector3() }; }

  /** Resolve generation/site once; moving UAV vectors stay owned by EntityLayer. */
  private livePosition(endpoint: LinkEndpoint): T.Vector3 | undefined {
    const direct = this.live.get(endpoint.id);
    if (direct) return direct;
    const entities = this.currentStore!.entities;
    if (!endpoint.key || entities.get(endpoint.key) !== endpoint.entity) {
      endpoint.entity = undefined;
      endpoint.key = undefined;
      entities.forEach(findEndpointEntity, endpoint);
    }
    const live = endpoint.key ? this.live.get(endpoint.key) : undefined;
    if (live) return live;
    // Some callers supply display positions without the matching entity row.
    const supplied = endpoint.liveKey ? this.live.get(endpoint.liveKey) : undefined;
    if (supplied) return supplied;
    endpoint.supplied = undefined;
    this.live.forEach(findEndpointPosition, endpoint);
    if (endpoint.supplied) return endpoint.supplied;
    const site = endpoint.entity?.fields.get('lowalt.site_position')?.value;
    if (Array.isArray(site)) return worldPosition(site, 'enu', this.header.origin, endpoint.position).sub(this.origin);
    return undefined;
  }


  private moveLinks() {
    if (!this.ribbon) return;
    for (let i = 0; i < this.orderedLinks.length; i++) {
      const entry = this.orderedLinks[i];
      const from = this.livePosition(entry.from), to = this.livePosition(entry.to);
      if (!from || !to) {
        if (entry.visible) this.ribbon.setAlpha(entry.slot, 0);
        entry.visible = false;
        continue;
      }
      if (!entry.visible) this.ribbon.setAlpha(entry.slot, entry.alpha);
      if (entry.dirty || !entry.lastFrom.equals(from) || !entry.lastTo.equals(to)) {
        this.ribbon.move(entry.slot, from, to);
        entry.lastFrom.copy(from); entry.lastTo.copy(to); entry.dirty = false;
      }
      entry.visible = true;
    }
  }

  private rebuildZones(zones: SemanticZone[], renderOrigin: T.Vector3) {
    for (const mesh of this.zoneBoxes) { mesh.removeFromParent(); mesh.geometry.dispose(); (mesh.material as T.Material).dispose(); }
    for (const line of this.zoneLines) { line.removeFromParent(); line.geometry.dispose(); (line.material as T.Material).dispose(); }
    this.zoneBoxes = []; this.zoneLines = [];
    for (const zone of zones) {
      const style = ZONE_STYLE[zone.kind];
      const toWorld = (value: [number, number, number]) => worldPosition(value, 'enu', this.header.origin).sub(renderOrigin);
      const lo = toWorld(zone.lower), hi = toWorld(zone.upper);
      const min = new T.Vector3(Math.min(lo.x, hi.x), Math.min(lo.y, hi.y), Math.min(lo.z, hi.z));
      const max = new T.Vector3(Math.max(lo.x, hi.x), Math.max(lo.y, hi.y), Math.max(lo.z, hi.z));
      const size = max.clone().sub(min);
      if (size.x <= 0 || size.y <= 0 || size.z <= 0) throw Error(`Zone ${zone.id} has non-positive bounds extent`);
      const center = min.clone().add(max).multiplyScalar(0.5);
      if (style.boxOpacity > 0) {
        const box = new T.Mesh(
          new T.BoxGeometry(size.x, size.y, size.z),
          new T.MeshBasicMaterial({ color: style.color, transparent: true, opacity: style.boxOpacity, depthWrite: false }),
        );
        box.position.copy(center); box.renderOrder = 3;
        this.zones.add(box); this.zoneBoxes.push(box);
      }
      // Restriction edges: a wire outline of the volume, or corridor centerline.
      let edges: T.BufferGeometry;
      if (zone.kind === 'corridor') edges = centerlineGeometry(min, max);
      else {
        const box = new T.BoxGeometry(size.x, size.y, size.z);
        edges = new T.EdgesGeometry(box);
        box.dispose();
      }
      const line = new T.LineSegments(edges, new T.LineBasicMaterial({ color: style.color, transparent: true, opacity: 0.75 }));
      if (zone.kind !== 'corridor') line.position.copy(center);
      this.zones.add(line); this.zoneLines.push(line);
    }
  }

  private rebuildMarkers(sites: SemanticSite[], chargingPads: Set<string>, renderOrigin: T.Vector3) {
    for (const mesh of this.markerGlyphs.values()) { mesh.removeFromParent(); mesh.geometry.dispose(); (mesh.material as T.Material).dispose(); }
    for (const glow of this.padGlows) { glow.removeFromParent(); glow.geometry.dispose(); (glow.material as T.Material).dispose(); } this.padGlows = [];
    this.markerGlyphs.clear(); this.markerPositions.clear(); this.markerKeys.clear(); this.pickables.length = 0;
    for (const site of sites) {
      const world = worldPosition(site.position, 'enu', this.header.origin).sub(renderOrigin);
      const charging = site.isPad && chargingPads.has(site.id);
      const color = site.isVertiport ? 0x009e73 : charging ? 0x35ffd1 : 0xe69f00;
      if (charging) {
        const glow = new T.Mesh(new T.RingGeometry(3.2, 7, 40), new T.MeshBasicMaterial({ color: 0x35ffd1, transparent: true, opacity: .65, side: T.DoubleSide, depthWrite: false, blending: T.AdditiveBlending }));
        glow.rotation.x = -Math.PI / 2; glow.position.copy(world).add(new T.Vector3(0, .15, 0)); glow.userData.chargingPad = site.id; this.markers.add(glow); this.padGlows.push(glow);
      }
      const geometry = site.isVertiport ? new T.CylinderGeometry(6, 6, 1.2, 24) : new T.CylinderGeometry(3, 3, 1, 16);
      const mesh = new T.Mesh(geometry, new T.MeshBasicMaterial({ color, transparent: true, opacity: 0.9 }));
      mesh.position.copy(world).add(new T.Vector3(0, 0.6, 0));
      mesh.userData.semanticEntityId = `${site.key.id}:${site.key.generation}`;
      mesh.userData.charging = charging;
      mesh.userData.semanticLabel = site.isVertiport ? humanSiteName(site.id, 'Vertiport', this.options.locale) : humanSiteName(site.id, 'Charging pad', this.options.locale);
      this.markers.add(mesh);
      this.markerGlyphs.set(mesh.userData.semanticEntityId, mesh);
      this.pickables.push(mesh);
      this.markerPositions.set(mesh.userData.semanticEntityId, world);
      this.markerKeys.set(mesh.userData.semanticEntityId, site.key);
    }
  }

  /** Legend colors for zone kinds, from the registered schema mapping. */
  static zoneLegend(locale = 'en-US'): Array<[string, string]> {
    const labels = {
      no_fly: translate(locale, 'No-fly zone'), restricted: translate(locale, 'Restricted zone'),
      altitude_band: translate(locale, 'Altitude band'), corridor: translate(locale, 'Corridor'),
    };
    return (Object.keys(ZONE_STYLE) as SemanticZone['kind'][]).map(kind => [labels[kind], `#${ZONE_STYLE[kind].color.toString(16).padStart(6, '0')}`] as [string, string]);
  }

  /** Flood-rescue legend entries, for the runs whose data carries them. */
  static floodLegend(present: { areas: number; roads: number; incidents: number; markers: SemanticMarker[]; links: SemanticLink[] }, locale = 'en-US'): Array<[string, string]> {
    const zh = locale.startsWith('zh'), entries: Array<[string, string]> = [];
    if (present.areas) entries.push([zh ? '积水' : 'Flood water', '#3e5654']);
    if (present.roads) entries.push([zh ? '被淹道路' : 'Submerged road', '#6f7a6a']);
    if (present.incidents) entries.push([zh ? '屋顶求援' : 'Rooftop survivor', '#ffb74b']);
    if (present.markers.some(marker => marker.kind === 'inspect')) entries.push([zh ? '复查点' : 'Inspection point', MARKER_COLORS.inspect]);
    if (present.markers.some(marker => marker.kind === 'relay')) entries.push([zh ? '中继' : 'Relay', MARKER_COLORS.relay]);
    if (present.links.some(link => link.kind === 'report')) entries.push([zh ? '报告链路' : 'Report link', rescueLinkColor({ kind: 'report', state: 'sending' })]);
    if (present.links.some(link => link.kind === 'delivery_ack')) entries.push([zh ? '回执链路' : 'Acknowledgement link', '#3fb67a']);
    return entries;
  }

  /** Best semantic marker under the ray, or undefined. */
  pick(raycaster: T.Raycaster): { key: EntityKey; label: string } | undefined {
    const hits = raycaster.intersectObjects(this.pickables, false);
    if (!hits.length) return undefined;
    const hit = hits[0].object as T.Mesh;
    const key = this.markerKeys.get(hit.userData.semanticEntityId);
    if (!key) return undefined;
    return { key, label: hit.userData.semanticLabel as string };
  }

  /** Hover label text for a semantic marker entity id. */
  labelFor(id: string): string | undefined {
    return this.markerGlyphs.get(id)?.userData.semanticLabel as string | undefined;
  }

  private disposeRibbon() {
    this.ribbon?.dispose(); this.ribbon = undefined;
    this.ribbonMesh?.removeFromParent(); this.ribbonMesh = undefined;
  }

  dispose() {
    this.disposed = true;
    this.clearFlood();
    for (const mesh of this.zoneBoxes) { mesh.geometry.dispose(); (mesh.material as T.Material).dispose(); }
    for (const line of this.zoneLines) { line.geometry.dispose(); (line.material as T.Material).dispose(); }
    for (const mesh of this.markerGlyphs.values()) { mesh.geometry.dispose(); (mesh.material as T.Material).dispose(); }
    for (const glow of this.padGlows) { glow.geometry.dispose(); (glow.material as T.Material).dispose(); } this.padGlows = [];
    this.zoneBoxes = []; this.zoneLines = []; this.markerGlyphs.clear();
    this.water?.dispose(); this.water = undefined;
    this.pulseMaterial?.dispose(); this.pulseMaterial = undefined;
    this.linkLines.clear();
    this.disposeRibbon();
    this.flowingLinks = false;
    for (const child of [...this.rescue.children]) {
      if (child instanceof T.Mesh || child instanceof T.Line) child.geometry.dispose();
      child.removeFromParent();
    }
    this.glyphSprites.clear();
    this.leaderMaterial.dispose();
    for (const material of this.spriteMaterials.values()) material.dispose();
    this.spriteMaterials.clear();
    this.linkMaterial.dispose();
  }
}

/** A corridor line runs along the longest horizontal axis of its bounds. */
function centerlineGeometry(min: T.Vector3, max: T.Vector3): T.BufferGeometry {
  const center = min.clone().add(max).multiplyScalar(0.5);
  const size = max.clone().sub(min);
  const axis = size.x >= size.z ? 0 : 2;
  const a = center.clone(), b = center.clone();
  const half = axis === 0 ? size.x / 2 : size.z / 2;
  if (axis === 0) { a.x -= half; b.x += half; } else { a.z -= half; b.z += half; }
  return new T.BufferGeometry().setFromPoints([a, b]);
}

/** `vertiport-1` → `Vertiport 1`; a north/cardinal word becomes `Vertiport North`. */
function humanSiteName(id: string, kind: 'Vertiport' | 'Charging pad', locale = 'en-US'): string {
  const kindWords = kind.toLowerCase().split(' ');
  const words = id.split(/[^a-z0-9]+/i).filter(Boolean).filter(word => !kindWords.includes(word.toLowerCase()));
  const label = kind === 'Vertiport' ? translate(locale, 'Vertiport') : translate(locale, 'Charging pad');
  if (!words.length) return label;
  // Keep authored identity words; localize only standard site/direction words.
  const name = humanizeId(`${words.join('-')}-0`, 'Site', locale).replace(/ 0$/, '');
  return `${label} ${name}`;
}
