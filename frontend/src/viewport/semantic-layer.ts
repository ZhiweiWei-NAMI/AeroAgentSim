import * as T from 'three';
import type { EntityKey, RunHeader } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { worldPosition } from './coordinates';
import { activeZones, humanizeId, semanticSites, type SemanticZone } from './semantic';
import { translate } from '../i18n/translate';
import {chargingPadAssignments} from './operational-loads';

/**
 * Newcomer semantic overlay: active airspace zones and site markers rendered
 * from actual committed lowalt.* fields. Display-only: never mutates
 * simulation state, never touches city materials. All positions pass through
 * worldPosition/ENU and the caller's renderOrigin, exactly like EntityLayer.
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

export class SemanticLayer {
  readonly root = new T.Group();
  readonly pickables: T.Object3D[] = [];
  /** Entity id -> world position (render-origin-relative) for hover labels. */
  readonly markerPositions = new Map<string, T.Vector3>();
  readonly markerKeys = new Map<string, EntityKey>();
  private zones = new T.Group();
  private markers = new T.Group();
  private zoneBoxes: T.Mesh[] = [];
  private zoneLines: T.LineSegments[] = [];
  private markerGlyphs = new Map<string, T.Mesh>();
  private padGlows:T.Mesh[]=[];
  get chargingPadCount(){return this.padGlows.length;}
  private origin = new T.Vector3();
  private disposed = false;
  private lastStamp = '';

  constructor(private header: RunHeader, private options: SemanticLayerOptions = {}) {
    this.root.add(this.zones, this.markers);
  }

  setLocale(locale: string) {
    if (this.options.locale === locale) return;
    this.options.locale = locale;
    this.lastStamp = '';
  }

  /** Rebuild zone/site visuals when the committed cut actually changed. */
  update(store: FeedStore, ns: string, renderOrigin: T.Vector3) {
    if (this.disposed) return;
    const stamp = `${store.revision}/${renderOrigin.toArray().join(',')}`;
    const originChanged = !this.origin.equals(renderOrigin);
    if (stamp === this.lastStamp && !originChanged) return;
    this.lastStamp = stamp;
    this.origin.copy(renderOrigin);
    this.rebuildZones(store, renderOrigin);
    this.rebuildMarkers(store, renderOrigin);
  }

  private rebuildZones(store: FeedStore, renderOrigin: T.Vector3) {
    for (const mesh of this.zoneBoxes) { mesh.removeFromParent(); mesh.geometry.dispose(); (mesh.material as T.Material).dispose(); }
    for (const line of this.zoneLines) { line.removeFromParent(); line.geometry.dispose(); (line.material as T.Material).dispose(); }
    this.zoneBoxes = []; this.zoneLines = [];
    const zones = activeZones(this.header, store);
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
        box.position.copy(center); box.renderOrder = 2;
        this.zones.add(box); this.zoneBoxes.push(box);
      }
      // Restriction edges: a wire outline of the volume, or corridor centerline.
      const edges = zone.kind === 'corridor'
        ? centerlineGeometry(min, max)
        : new T.EdgesGeometry(new T.BoxGeometry(size.x, size.y, size.z));
      const line = new T.LineSegments(edges, new T.LineBasicMaterial({ color: style.color, transparent: true, opacity: 0.75 }));
      if (zone.kind !== 'corridor') line.position.copy(center);
      this.zones.add(line); this.zoneLines.push(line);
    }
  }

  private rebuildMarkers(store: FeedStore, renderOrigin: T.Vector3) {
    for (const mesh of this.markerGlyphs.values()) { mesh.removeFromParent(); mesh.geometry.dispose(); (mesh.material as T.Material).dispose(); }
    for(const glow of this.padGlows){glow.removeFromParent();glow.geometry.dispose();(glow.material as T.Material).dispose();}this.padGlows=[];
    this.markerGlyphs.clear(); this.markerPositions.clear(); this.markerKeys.clear(); this.pickables.length = 0;
    const chargingPads=new Set(chargingPadAssignments(store).values());
    for (const site of semanticSites(this.header, store)) {
      if (!site.isVertiport && !site.isPad) continue;
      const world = worldPosition(site.position, 'enu', this.header.origin).sub(renderOrigin);
      const charging=site.isPad&&chargingPads.has(site.id);
      const color = site.isVertiport ? 0x009e73 : charging?0x35ffd1:0xe69f00;
      if(charging){const glow=new T.Mesh(new T.RingGeometry(3.2,7,40),new T.MeshBasicMaterial({color:0x35ffd1,transparent:true,opacity:.65,side:T.DoubleSide,depthWrite:false,blending:T.AdditiveBlending}));glow.rotation.x=-Math.PI/2;glow.position.copy(world).add(new T.Vector3(0,.15,0));glow.userData.chargingPad=site.id;this.markers.add(glow);this.padGlows.push(glow);}
      const geometry = site.isVertiport ? new T.CylinderGeometry(6, 6, 1.2, 24) : new T.CylinderGeometry(3, 3, 1, 16);
      const mesh = new T.Mesh(geometry, new T.MeshBasicMaterial({ color, transparent: true, opacity: 0.9 }));
      mesh.position.copy(world).add(new T.Vector3(0, 0.6, 0));
      mesh.userData.semanticEntityId = `${site.key.id}:${site.key.generation}`;
      mesh.userData.charging=charging;
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

  dispose() {
    this.disposed = true;
    for (const mesh of this.zoneBoxes) { mesh.geometry.dispose(); (mesh.material as T.Material).dispose(); }
    for (const line of this.zoneLines) { line.geometry.dispose(); (line.material as T.Material).dispose(); }
    for (const mesh of this.markerGlyphs.values()) { mesh.geometry.dispose(); (mesh.material as T.Material).dispose(); }
    for(const glow of this.padGlows){glow.geometry.dispose();(glow.material as T.Material).dispose();}this.padGlows=[];
    this.zoneBoxes = []; this.zoneLines = []; this.markerGlyphs.clear();
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
