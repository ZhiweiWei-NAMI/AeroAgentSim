import * as T from 'three';
import type { EntityKey, PresentationBinding, RunHeader } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { resolveBinding, entityId } from './bindings';
import { modelGlyph } from './model-glyph';
import { proceduralModel } from './procedural-traffic';
import { Assets } from './assets';
import { placeLabel, type LabelRect } from './label-placement';
import type { StaticOcclusion } from './occlusion';
import { worldOrientation, worldPosition } from './coordinates';
import type { EntityState } from './feed-store';
import { selectCatalogModel, fitModel, yawFromVelocity, type CatalogModel, type EntityClassInfo, type ModelCatalog } from './model-catalog';
import { semanticVehicle, businessStyle, semanticLabel, labelInterest, taskByVehicle, activeZones } from './semantic';
import {PayloadLayer} from './operational-loads';
import { RecentActivity } from './recent-activity';
import type { Conditions } from './conditions';

/** A task counts as active unless its committed state says it is finished. */
function isFinishedTask(task: { fields: Map<string, { value: unknown }> }): boolean {
  const state = task.fields.get('lowalt.task_state')?.value;
  return state === 'completed' || state === 'failed' || state === 'canceled' || state === 'unreleased';
}

interface Batch { meshes: T.InstancedMesh[]; transforms: T.Matrix4[]; keys: EntityKey[]; capacity: number; loaded: boolean; far?: { mesh: T.InstancedMesh; keys: EntityKey[] }; outline?: T.Mesh[]; assetBasis?: T.Quaternion; farDistance?: number }
/** One catalogued display model, optionally the LOD1 mesh and/or a business tint. */
interface CatalogSpec { id: string; model: CatalogModel; lod: boolean; tint?: string }
/** UAV LOD1 mesh beyond this distance (m) or once this many full-detail copies are drawn. */
const UAV_LOD_DISTANCE = 80, UAV_LOD_COUNT = 10;
// Candidate horizontal-velocity fields (ENU) used to face catalogued aircraft along their motion.
const VELOCITY_FIELDS = ['lowalt.velocity', 'traffic.uav.velocity_enu_mps'];
// Generic accent for uncoloured bound glyphs: a warm coral complementary to the
// blue city glass, so unstyled entities read against the cool modern skyline.
// Explicit visual.color always wins; this default never touches styled bindings.
const UNCOLOURED_GLYPH = '#ff8a5c';
const UNCOLOURED_GLYPH_EMISSIVE = '#8a3d1f';
export class EntityLayer {
  readonly root = new T.Group();
  readonly positions = new Map<string, T.Vector3>();
  readonly orientations = new Map<string, T.Quaternion>();
  readonly ring: T.Mesh;
  private loads=new PayloadLayer();
  get payloadCount(){return this.semantic?this.loads.markers.size:0;}
  /** Capture waits for batches requested by its current state/camera, not unrelated catalog assets. */
  get modelsReady() { return [...this.batches.values()].every(batch => batch.loaded); }
  labelsMs = 0;
  transformsChanged = false;
  readonly selection: T.Object3D[]=[];
  /** Entities emphasised together (for example by a cause trace), in addition to the single selection. */
  private highlighted = new Set<string>();
  private highlightRings: T.Mesh[] = [];
  /** Number of highlight rings drawn in the latest frame (diagnostic). */
  highlightedShown = 0;
  setHighlights(ids: ReadonlySet<string>) { this.highlighted = new Set(ids); }
  /** Semantic overlay policy (newcomer mode); inert defaults keep Expert behavior. */
  semantic = false;
  showAllLabels = false;
  /** Incidents get the atmosphere pin/ring instead of the plain marker; labels stay readable. */
  hideIncidentMarkers = false;
  locale = 'en-US';
  private hoverId?: string;
  private semanticTasks = new Map<string, EntityState>();
  private semanticInvolved = new Set<string>();
  private semanticAlerts = new Set<string>();
  private semanticStamp = '';
  private recentActivity = new RecentActivity();
  private batches = new Map<PresentationBinding, Batch>();
  private semanticBindings=new Map<PresentationBinding,Map<string,PresentationBinding>>();
  private glyphs=new WeakMap<PresentationBinding,string>();
  private labels = new Map<string, HTMLButtonElement>();
  private trail: T.Line;
  private dummy = new T.Object3D();
  private disposed = false;
  private matrix = new T.Matrix4();
  private labelWorld = new T.Vector3();
  private labelProjected = new T.Vector3();
  private labelEndpoint = new T.Vector3();
  private labelDirection = new T.Vector3();
  private lastTrail = '';
  /** Display-model catalog of the installed pack; empty keeps the procedural/single-asset display. */
  private catalog: ModelCatalog = {};
  private catalogVariants = new Map<PresentationBinding, Map<string, PresentationBinding>>();
  private catalogSpecs = new WeakMap<PresentationBinding, CatalogSpec>();
  private catalogFailed = new Set<string>();
  private catalogUsage = new Map<string, number>();
  private tinted = new Map<string, T.Material>();
  private headings = new Map<string, number>();
  private entityMeta?: Map<string, { vehicle_class?: string }>;
  constructor(private header: RunHeader, private assets: Assets, private overlay: HTMLElement,
    private onSelect: (key: EntityKey) => void, private onError: (error: unknown) => void,
    private status: (message: string) => void = () => {}) {
    this.ring = new T.Mesh(new T.TorusGeometry(0.8, 0.025, 8, 48), new T.MeshBasicMaterial({ color: '#9ce9ff', depthTest: false, transparent: true, opacity: 0.9 }));
    this.ring.rotation.x = -Math.PI / 2; this.ring.visible = false; this.ring.renderOrder=5; this.root.add(this.ring);
    this.trail = new T.Line(new T.BufferGeometry(), new T.LineBasicMaterial({ color: '#30bdf0', transparent: true, opacity: 0.85 }));
    this.trail.frustumCulled=false; this.root.add(this.trail,this.loads.root);
  }
  private createBatch(binding: PresentationBinding, capacity: number) {
    const glyph=this.glyphs.get(binding);
    const geometry = glyph==='package'?new T.BoxGeometry(1.4,1.0,1.4):glyph==='cross'?new T.OctahedronGeometry(1):glyph==='eye'?new T.TorusGeometry(0.75,0.3,6,12).rotateX(Math.PI/2):glyph==='person'?new T.ConeGeometry(0.8,1.6,8):binding.visual.kind==='model'&&binding.orientationField?new T.ConeGeometry(0.65,1.8,4).rotateX(Math.PI/2):new T.IcosahedronGeometry(0.85, 1);
    // The per-batch accent stays readable at marker and far-LOD sizes.
    // Full model assets retain their source materials.
    const styled = binding.visual.color != null;
    const material = new T.MeshStandardMaterial({
      color: styled ? binding.visual.color : UNCOLOURED_GLYPH,
      roughness: styled ? 0.4 : 0.32,
      metalness: 0.15,
      emissive: styled ? binding.visual.color : UNCOLOURED_GLYPH_EMISSIVE,
      emissiveIntensity: styled ? 0.22 : 0.12,
    });
    const marker: T.InstancedMesh = new T.InstancedMesh(geometry, material, capacity);
    marker.castShadow = false; marker.frustumCulled = false; marker.count=0; this.root.add(marker);
    const batch: Batch = { meshes: [marker], transforms: [new T.Matrix4()], capacity, keys: [], loaded: binding.visual.kind !== 'model' };
    this.batches.set(binding, batch);
    const catalogSpec = this.catalogSpecs.get(binding);
    if (catalogSpec) { this.loadCatalogModel(binding, batch, catalogSpec); return batch; }
    if (binding.visual.kind === 'model') {
      if (!binding.visual.asset) throw Error(`Model binding ${binding.typeId} has no asset URL`);
      // Reserved procedural asset URLs build mesh-free stand-ins in-process;
      // no fetch, no GLB load, and no status text (nothing can be unavailable).
      const procedural = proceduralModel(binding.visual.asset);
      if (procedural) {
        procedural.updateMatrixWorld(true);
        batch.meshes = [];
        batch.transforms = [];
        procedural.traverse(node => {
          if (!(node instanceof T.Mesh)) return;
          const mesh = new T.InstancedMesh(node.geometry, node.material, batch.capacity);
          mesh.count = 0; mesh.castShadow = true; mesh.receiveShadow = true; mesh.frustumCulled = false;
          this.root.add(mesh);
          batch.meshes.push(mesh);
          batch.transforms.push(node.matrixWorld.clone());
        });
        if (!batch.meshes.length) throw Error('Procedural model has no renderable geometry');
        const glyph = modelGlyph(procedural);
        marker.geometry.dispose();
        marker.geometry = glyph;
        batch.far = { mesh: marker, keys: [] };
        batch.outline = batch.meshes.map(instance => { const mesh = new T.Mesh(instance.geometry, instance.material); mesh.layers.set(10); mesh.matrixAutoUpdate = false; mesh.visible = false; this.root.add(mesh); return mesh; });
        batch.loaded = true;
        return batch;
      }
      this.status(`Loading model: ${binding.visual.asset} · markers active`);
      void this.assets.model(binding.visual.asset).then(model => {
        if (this.disposed) return;
        const meshes: T.InstancedMesh[]=[], transforms: T.Matrix4[]=[];
        model.updateMatrixWorld(true);model.traverse(node=>{const q=node.userData.bodyToAssetQuaternion;if(Array.isArray(q)&&q.length===4)batch.assetBasis=new T.Quaternion(...q as [number,number,number,number]).invert();});
        model.traverse(node=>{
          if (!(node instanceof T.Mesh)) return;
          if (node instanceof T.SkinnedMesh) throw Error('Skinned models require an animated entity layer');
          const mesh=new T.InstancedMesh(node.geometry,node.material,batch.capacity);
          mesh.count=0;mesh.castShadow=true;mesh.receiveShadow=true;mesh.frustumCulled=false;
          meshes.push(mesh);transforms.push(node.matrixWorld.clone());
        });
        if (!meshes.length) throw Error('Model has no renderable geometry');
        const glyph=modelGlyph(model);batch.meshes[0].geometry.dispose();batch.meshes[0].geometry=glyph;
        batch.far={mesh:batch.meshes[0],keys:[]};
        batch.meshes=meshes;batch.transforms=transforms;batch.loaded=true;
        batch.outline=meshes.map(instance=>{const mesh=new T.Mesh(instance.geometry,instance.material);mesh.layers.set(10);mesh.matrixAutoUpdate=false;mesh.visible=false;this.root.add(mesh);return mesh;});
        for(const mesh of meshes)this.root.add(mesh);
        this.status(`Models loaded · ${binding.visual.asset}`);
      }).catch(error=>{ if(!this.disposed) this.status(`Model unavailable: ${String(error)} · markers active`); });
    }
    return batch;
  }
  /** Enable catalogued display models (console viewport only; capture keeps its pinned assets). */
  setCatalog(catalog: ModelCatalog) { this.catalog = catalog; }
  private classInfo(entity: EntityState): EntityClassInfo {
    if (!this.entityMeta) this.entityMeta = new Map((this.header.entities ?? []).map(row => [row.id, { vehicle_class: row.vehicle_class }]));
    const text = (id: string) => { const value = entity.fields.get(id)?.value; return typeof value === 'string' && value ? value : undefined; };
    return { id: entity.key.id, vehicleClass: text('lowalt.vehicle_class') ?? this.entityMeta.get(entity.key.id)?.vehicle_class, role: text('traffic.actor.role') };
  }
  /** Shared-material copy with a business accent; textures stay shared with the source material. */
  private tintMaterial(material: T.Material, color: string): T.Material {
    const key = `${material.uuid}|${color}`;
    let clone = this.tinted.get(key);
    if (!clone) {
      clone = material.clone();
      const standard = clone as T.MeshStandardMaterial;
      if (standard.color) standard.color.lerp(new T.Color(color), 0.4);
      this.tinted.set(key, clone);
    }
    return clone;
  }
  private loadCatalogModel(binding: PresentationBinding, batch: Batch, spec: CatalogSpec) {
    const url = spec.lod ? spec.model.lodUrl! : spec.model.url;
    batch.farDistance = spec.model.kind === 'uav' ? 700 : 350;
    this.status(`Loading model: ${spec.id}${spec.lod ? ' (LOD1)' : ''} · stand-ins active`);
    void this.assets.model(url).then(model => {
      if (this.disposed) return;
      model.updateMatrixWorld(true);
      const fit = fitModel(new T.Box3().setFromObject(model), spec.model);
      const meshes: T.InstancedMesh[] = [], transforms: T.Matrix4[] = [];
      model.traverse(node => {
        if (!(node instanceof T.Mesh)) return;
        if (node instanceof T.SkinnedMesh) throw Error('Skinned models require an animated entity layer');
        const material = spec.tint
          ? Array.isArray(node.material) ? node.material.map(item => this.tintMaterial(item, spec.tint!)) : this.tintMaterial(node.material, spec.tint)
          : node.material;
        const mesh = new T.InstancedMesh(node.geometry, material, batch.capacity);
        mesh.count = 0; mesh.castShadow = true; mesh.receiveShadow = true; mesh.frustumCulled = false;
        meshes.push(mesh); transforms.push(fit.matrix.clone().multiply(node.matrixWorld));
      });
      if (!meshes.length) throw Error('Model has no renderable geometry');
      // Far glyph shares the fitted frame so its heading matches the full model.
      const holder = new T.Object3D(); holder.matrixAutoUpdate = false; holder.matrix.copy(fit.matrix); holder.add(model.clone());
      const glyph = modelGlyph(holder); batch.meshes[0].geometry.dispose(); batch.meshes[0].geometry = glyph;
      batch.far = { mesh: batch.meshes[0], keys: [] };
      batch.meshes = meshes; batch.transforms = transforms; batch.loaded = true;
      batch.outline = meshes.map(instance => { const mesh = new T.Mesh(instance.geometry, instance.material); mesh.layers.set(10); mesh.matrixAutoUpdate = false; mesh.visible = false; this.root.add(mesh); return mesh; });
      for (const mesh of meshes) this.root.add(mesh);
      this.status(`Models loaded · ${spec.id}`);
    }).catch(error => {
      if (this.disposed) return;
      this.catalogFailed.add(spec.id);
      this.status(`Model unavailable: ${spec.id}: ${String(error)} · stand-ins active`);
    });
  }
  /**
   * Catalogued display variant of a binding for one entity, or undefined to keep the binding's own
   * stand-in (no catalog, class not catalogued, model loading, or load failed). Variants are batches
   * keyed by model, LOD tier and tint, so every GLB is loaded once and instanced.
   */
  private catalogBinding(binding: PresentationBinding, entity: EntityState, id: string, store: FeedStore, distance: number): PresentationBinding | undefined {
    const kind = binding.visual.catalog;
    if (!kind || binding.frame !== 'enu' || !Object.keys(this.catalog).length) return undefined;
    const pick = selectCatalogModel(kind, this.classInfo(entity), this.catalog);
    if (!pick || this.catalogFailed.has(pick.id)) return undefined;
    const model = this.catalog[pick.id];
    let tint: string | undefined;
    if (this.semantic && kind === 'uav' && entity.typeId === 'lowalt:UAV') {
      const style = businessStyle(semanticVehicle(entity, this.semanticTasks.get(id)));
      if (style.label) tint = style.color;
    }
    const used = this.catalogUsage.get(pick.id) ?? 0;
    this.catalogUsage.set(pick.id, used + 1);
    const wantLod = !!model.lodUrl && (distance > UAV_LOD_DISTANCE || used >= UAV_LOD_COUNT) && kind === 'uav';
    for (const lod of wantLod ? [true, false] : [false, true]) {
      if(lod&&!model.lodUrl)continue;
      const key = `${pick.id}|${lod ? 1 : 0}|${tint ?? ''}`;
      let variants = this.catalogVariants.get(binding);
      if (!variants) { variants = new Map(); this.catalogVariants.set(binding, variants); }
      let variant = variants.get(key);
      if (!variant) {
        variant = { ...binding, visual: { ...binding.visual, kind: 'model', asset: `catalog:${key}`, scale: 1, color: tint ?? binding.visual.color } };
        variants.set(key, variant);
        this.catalogSpecs.set(variant, { id: pick.id, model, lod, tint });
      }
      // Request only the tier needed at this distance. The already loaded other
      // tier can stay visible while the requested tier decodes.
      const batch = this.batches.get(variant) ?? (lod===wantLod ? this.createBatch(variant, Math.max(64, store.entities.size)) : undefined);
      if (batch?.loaded) return variant;
    }
    return undefined;
  }
  /** Facing of an aircraft that has no orientation field: along its committed horizontal velocity. */
  private velocityHeading(store: FeedStore, entity: EntityState, id: string, ns: string): number[] | undefined {
    for (const field of VELOCITY_FIELDS) {
      const velocity = store.sample(entity.key, field, ns);
      if (!velocity) continue;
      const yaw = yawFromVelocity(velocity);
      if (yaw !== undefined) this.headings.set(id, yaw);
      const held = this.headings.get(id);
      return held === undefined ? undefined : [0, 0, Math.sin(held / 2), Math.cos(held / 2)];
    }
    return undefined;
  }
  private writeInstance(mesh: T.InstancedMesh, index: number, matrix: T.Matrix4) {
    const array = mesh.instanceMatrix.array;
    let changed = false;
    for (let i=0;i<16;i++) if (array[index*16+i] !== Math.fround(matrix.elements[i])) { changed=true;break; }
    if(changed){mesh.setMatrixAt(index,matrix);mesh.userData.viewportChanged=true;}
  }
  update(store: FeedStore, ns: string, camera: T.Camera, selected?: EntityKey, showTrails = true,
    renderOrigin = new T.Vector3(), occlusion?: StaticOcclusion, aimCamera?: () => void, incidents?: Conditions['incidents']) {
    this.transformsChanged=false;this.catalogUsage.clear();
    if(this.semantic){
      const stamp=`${store.revision}/${ns}`;
      if(stamp!==this.semanticStamp){this.semanticStamp=stamp;this.semanticTasks=taskByVehicle(store);
        const known=(store as FeedStore & {viewCursor?:{knownAt:number}}).viewCursor?.knownAt;
        const activity=this.recentActivity.collect(store,ns,10_000_000_000n,known);
        this.semanticInvolved=activity.involved;this.semanticAlerts=activity.alerts;}
    }
    for(const batch of this.batches.values())for(const mesh of [...batch.meshes,...(batch.far?[batch.far.mesh]:[])]){mesh.userData.viewportCount=mesh.count;mesh.userData.viewportChanged=false;}
    this.positions.clear(); this.orientations.clear();this.selection.length=0;for(const batch of this.batches.values())for(const mesh of batch.outline ?? [])mesh.visible=false;
    for (const batch of this.batches.values()) { batch.keys = []; for(const mesh of batch.meshes)mesh.count=0; if(batch.far){batch.far.keys=[];batch.far.mesh.count=0;} }
    const width=this.overlay.clientWidth,height=this.overlay.clientHeight;
    const visibleLabels=new Set<string>();
    const candidates: Array<{ id: string; key: EntityKey; position: T.Vector3; selected: boolean; forced: boolean; interest?: boolean; incident?: Conditions['incidents'][number] }> = [];
    for (const [id, entity] of store.entities) {
      let binding = resolveBinding(this.header, entity.typeId); if (!binding) continue;
      if(this.semantic&&entity.typeId==='lowalt:UAV'&&binding.visual.kind==='marker'){
        const style=businessStyle(semanticVehicle(entity,this.semanticTasks.get(id)));
        if(style.label){let options=this.semanticBindings.get(binding);if(!options){options=new Map();this.semanticBindings.set(binding,options);}let styled=options.get(style.label);if(!styled){styled={...binding,visual:{...binding.visual,color:style.color}};options.set(style.label,styled);this.glyphs.set(styled,style.glyph);}binding=styled;}
      }
      const sample = store.sample(entity.key, binding.positionField, ns); if (!sample) continue;
      const position = worldPosition(sample, binding.frame, this.header.origin).sub(renderOrigin); this.positions.set(id, position);
      const catalogued = this.catalogBinding(binding, entity, id, store, position.distanceTo(camera.position));
      if (catalogued) binding = catalogued;
      let orientation = binding.orientationField ? store.sample(entity.key, binding.orientationField, ns, true) : undefined;
      // Catalogued bodies are fitted in the ENU body frame, so they always take the body->render basis.
      if (!orientation && catalogued) orientation = this.velocityHeading(store, entity, id, ns) ?? [0, 0, 0, 1];
      this.dummy.position.copy(position);
      if(orientation)this.dummy.quaternion.copy(worldOrientation(orientation,binding.frame));else this.dummy.quaternion.identity();
      if (orientation) this.orientations.set(id, this.dummy.quaternion.clone());
      this.dummy.scale.setScalar(binding.visual.scale ?? 1);
      if (binding.visual.kind !== 'label' && !(this.hideIncidentMarkers && entity.typeId === 'lowalt:Incident')) {
        const batch = this.batches.get(binding) ?? this.createBatch(binding, Math.max(64, store.entities.size));
        const isSelected=!!selected&&entityId(selected)===id||this.highlighted.has(id);
        const far=batch.far&&!isSelected&&(position.distanceTo(camera.position)>(batch.farDistance??100)||batch.keys.length>=(batch.farDistance?80:48))?batch.far:undefined;
        if(far){
          if(far.keys.length>=far.mesh.instanceMatrix.count){const old=far.mesh;far.mesh=new T.InstancedMesh(old.geometry,old.material,old.instanceMatrix.count*2);far.mesh.frustumCulled=false;far.mesh.castShadow=old.castShadow;far.mesh.receiveShadow=old.receiveShadow;for(let i=0;i<far.keys.length;i++){old.getMatrixAt(i,this.matrix);far.mesh.setMatrixAt(i,this.matrix);}old.removeFromParent();old.dispose();this.root.add(far.mesh);}
          // Distance glyphs have a minimum projected size; exact pose remains in the inspector.
          if(orientation&&batch.assetBasis)this.dummy.quaternion.multiply(batch.assetBasis);
          this.dummy.scale.setScalar(Math.max(0.8,position.distanceTo(camera.position)*0.009));this.dummy.updateMatrix();this.writeInstance(far.mesh,far.keys.length,this.dummy.matrix);far.keys.push(entity.key);far.mesh.count=far.keys.length;
        }else{
        if (batch.keys.length >= batch.capacity) {
          batch.capacity *= 2;
          batch.meshes = batch.meshes.map(old => {
            const mesh=new T.InstancedMesh(old.geometry,old.material,batch.capacity);
            mesh.castShadow=old.castShadow;mesh.receiveShadow=old.receiveShadow;mesh.frustumCulled=false;
            for(let i=0;i<batch.keys.length;i++){old.getMatrixAt(i,this.matrix);mesh.setMatrixAt(i,this.matrix);}
            old.removeFromParent();old.dispose();this.root.add(mesh);return mesh;
          });
        }
        if(orientation&&batch.assetBasis)this.dummy.quaternion.multiply(batch.assetBasis);
        this.dummy.updateMatrix();
        batch.meshes.forEach((mesh,i)=>{this.matrix.multiplyMatrices(this.dummy.matrix,batch.transforms[i]);this.writeInstance(mesh,batch.keys.length,this.matrix);mesh.count=batch.keys.length+1;});
        batch.keys.push(entity.key);
        if(isSelected)batch.outline?.forEach((mesh,i)=>{mesh.matrix.multiplyMatrices(this.dummy.matrix,batch.transforms[i]);mesh.matrixWorldNeedsUpdate=true;mesh.visible=true;this.selection.push(mesh);});
        }
      }
      const isSelected=!!selected && entityId(selected)===id;
      const incident = incidents?.find(row => row.id === entity.key.id);
      if (incident?.phase === 'pending') continue;
      const labelPosition = incident ? position.clone() : position;
      if (incident) labelPosition.y = Math.max(position.y, occlusion?.liftCamera(position, 0).y ?? position.y) + 14;
      candidates.push({id,key:entity.key,position:labelPosition,selected:isSelected,forced:!!incident||binding.visual.kind==='label'||this.highlighted.has(id),interest:!!incident,incident});
    }
    // Camera follow uses these exact sampled poses; labels must project through
    // that same final camera transform, rather than the previous frame's view.
    aimCamera?.();
    this.loads.root.visible=this.semantic;if(this.semantic)this.loads.update(store,this.positions);
    // Semantic label policy (newcomer mode): selected / task-active /
    // recently event-involved entities always label; others only on real
    // raycast hover or when show-all is on. Expert mode keeps forced labels.
    const entityHover=this.hoverId!==undefined&&this.positions.has(this.hoverId)?this.hoverId:undefined;
    for(const item of candidates){
      if (item.incident) continue;
      if(this.semantic&&!item.selected){
        const entity=store.entities.get(item.id)!;
        const task=this.semanticTasks.get(item.id);
        const interested=labelInterest(entity,selected,this.semanticInvolved,!!task&&!isFinishedTask(task),this.semanticAlerts);
        if(!interested&&item.id!==entityHover&&!this.showAllLabels)continue;
      }
      if(this.semantic)item.interest=true;
    }
    for(const batch of this.batches.values())for(const mesh of [...batch.meshes,...(batch.far?[batch.far.mesh]:[])]){
      if(mesh.userData.viewportChanged)mesh.instanceMatrix.needsUpdate=true;
      if(mesh.userData.viewportChanged||mesh.count!==mesh.userData.viewportCount)this.transformsChanged=true;
    }
    const labelsStarted = performance.now();
    camera.updateMatrixWorld();
    const occupied: Array<{ left: number; right: number; top: number; bottom: number }>=[];let labelAttempts=0;
    const wb = this.overlay.closest('.wb'), overlayRect = this.overlay.getBoundingClientRect();
    const relative = (rect: DOMRect): LabelRect => ({ left: rect.left - overlayRect.left, right: rect.right - overlayRect.left, top: rect.top - overlayRect.top, bottom: rect.bottom - overlayRect.top });
    const frame = wb?.querySelector('.wb-scene-frame')?.getBoundingClientRect();
    const safe = frame && frame.width > 0 ? relative(frame) : { left: 8, right: width - 8, top: 8, bottom: height - 8 };
    const obstacles = wb ? [...wb.querySelectorAll('.wb-panel, .wb-camera, .wb-graph, .wb-history, .wb-waiting')].map(node => relative(node.getBoundingClientRect())) : [];
    candidates.sort((a,b)=>Number(b.selected)-Number(a.selected)||a.position.distanceToSquared(camera.position)-b.position.distanceToSquared(camera.position));
    for(const item of candidates) {
      if(this.semantic&&!item.interest)continue;
      if(!item.interest&&!item.selected&&!item.forced&&(occupied.length>=12||labelAttempts>=32))continue;
      const world=this.labelWorld.copy(item.position);world.y+=2;
      const projected=this.labelProjected.copy(world).project(camera);
      if(projected.z<-1||projected.z>1||Math.abs(projected.x)>0.96||Math.abs(projected.y)>0.96)continue;
      const x=(projected.x+1)*width/2,y=(1-projected.y)*height/2;
      // Selected labels remain readable; nonselected labels do not shine through buildings.
      if(!item.selected&&!item.interest&&occlusion) {
        labelAttempts++;
        const direction=this.labelDirection.subVectors(world,camera.position);
        const endpoint=this.labelEndpoint.copy(world).addScaledVector(direction.normalize(),-0.2);
        if(world.distanceToSquared(camera.position)>0.04&&occlusion.blocked(camera.position,endpoint))continue;
      }
      let label=this.labels.get(item.id);
      if(!label){label=document.createElement('button');label.className='entity-label';label.dataset.entityId=item.key.id;label.onclick=()=>this.onSelect(item.key);this.overlay.append(label);this.labels.set(item.id,label);}
      let semanticText=this.semantic?semanticLabel(store.entities.get(item.id)!,this.semanticTasks.get(item.id),this.header,this.locale):item.key.id;
      if (item.incident) {
        const row = item.incident, zh = this.locale.startsWith('zh'), total = Math.floor(row.sinceS);
        const clock = `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
        const label = zh ? store.entities.get(item.id)?.authoredLabel?.label_zh ?? row.label : row.label;
        semanticText = row.phase === 'ended' ? `${row.kind === 'fire' ? zh ? '火情' : 'Fire' : zh ? '人员跌倒' : 'Person fell'} · ${zh ? '已结束' : 'ended'}` : `${label} · ${zh ? '已发生' : 'Active'} ${clock}`;
      }
      if(this.semantic){const style=businessStyle(semanticVehicle(store.entities.get(item.id)!,this.semanticTasks.get(item.id)));label.dataset.glyph=style.glyph;label.style.borderColor=style.color;}
      else {delete label.dataset.glyph;label.style.borderColor='';}
      if(label.textContent!==semanticText)label.textContent=semanticText;
      if(label.hidden)label.hidden=false;
      // Business-interest labels used to bypass collision avoidance entirely;
      // colocated task-active UAVs obscured each other. Measure the actual label.
      label.style.maxWidth = `${Math.max(0, safe.right - safe.left)}px`;
      const placed = placeLabel(x, y, label.offsetWidth, label.offsetHeight, safe, [...obstacles, ...occupied], item.selected);
      if (!placed) continue;
      visibleLabels.add(item.id);
      const left=`${placed.x}px`,top=`${placed.y}px`;
      if(label.style.left!==left)label.style.left=left;if(label.style.top!==top)label.style.top=top;
      label.classList.toggle('selected',item.selected);occupied.push(placed.box);
    }
    for(const [id,label] of this.labels)if(!visibleLabels.has(id)&&!label.hidden)label.hidden=true;
    // Keep a small reusable label pool; retired generations cannot accumulate DOM.
    for(const [id,label] of this.labels)if(!this.positions.has(id)||(this.labels.size>64&&label.hidden)){label.remove();this.labels.delete(id);}
    this.labelsMs = performance.now() - labelsStarted;
    let ringed = 0;
    for (const id of this.highlighted) {
      const spot = this.positions.get(id); if (!spot) continue;
      let ring = this.highlightRings[ringed];
      if (!ring) {
        ring = new T.Mesh(new T.TorusGeometry(1.1, 0.09, 8, 48), new T.MeshBasicMaterial({ color: '#ff8a1f', depthTest: false, transparent: true, opacity: 0.95 }));
        ring.rotation.x = -Math.PI / 2; ring.renderOrder = 5; this.root.add(ring); this.highlightRings.push(ring);
      }
      ring.visible = true; ring.position.copy(spot); ring.position.y -= 0.2; ring.scale.setScalar(Math.max(1.6,spot.distanceTo(camera.position)*0.022)); ringed++;
    }
    for (let i = ringed; i < this.highlightRings.length; i++) this.highlightRings[i].visible = false;
    this.highlightedShown = ringed;
    const focus=selected?this.positions.get(entityId(selected)):undefined;
    this.ring.visible=!!focus;if(focus){this.ring.position.copy(focus);this.ring.position.y-=0.2;this.ring.scale.setScalar(Math.max(1,focus.distanceTo(camera.position)*0.012));}
    const stamp=`${selected?entityId(selected):''}/${ns}/${renderOrigin.toArray()}/${showTrails}`;
    this.trail.visible=!!showTrails&&!!selected&&!!focus;
    if(this.trail.visible&&stamp!==this.lastTrail){
      const entity=store.entities.get(entityId(selected!))!,binding=resolveBinding(this.header,entity.typeId)!;
      const points:T.Vector3[]=[];
      for(let i=40;i>=0;i--){const at=(BigInt(ns)-BigInt(i)*150_000_000n).toString(),point=store.sample(selected!,binding.positionField,at);
        if(!point){points.length=0;continue;}points.push(worldPosition(point,binding.frame,this.header.origin).sub(renderOrigin));}
      this.trail.geometry.dispose();this.trail.geometry=new T.BufferGeometry().setFromPoints(points);this.trail.visible=points.length>1;this.lastTrail=stamp;
    }
  }
  /** Raycast hover from the viewport; only a real hover shows a label. */
  setHover(id?: string) { this.hoverId = id; }
  /** Legend entries from actual business kinds on committed vehicles. */
  businessLegend(store: FeedStore): Array<[string, string]> {
    const entries = new Map<string, string>();
    for (const entity of store.entities.values()) {
      if (entity.typeId !== 'lowalt:UAV') continue;
      const style = businessStyle(semanticVehicle(entity, this.semanticTasks.get(`${entity.key.id}:${entity.key.generation}`)), this.locale);
      if (style.label && !entries.has(style.label)) entries.set(style.label, style.color);
    }
    return [...entries];
  }
  /** Active zone count from committed facts; malformed zones surface in the 3D path, not here. */
  activeZoneCount(store: FeedStore): number {
    return activeZones(this.header, store).length;
  }
  pick(raycaster:T.Raycaster):EntityKey|undefined {
    for(const hit of raycaster.intersectObject(this.root,true))for(const batch of this.batches.values()){if(batch.meshes.includes(hit.object as T.InstancedMesh)&&hit.instanceId!==undefined)return batch.keys[hit.instanceId];if(hit.object===batch.far?.mesh&&hit.instanceId!==undefined)return batch.far.keys[hit.instanceId];}
    return undefined;
  }
  dispose(){this.disposed=true;this.loads.dispose();for(const label of this.labels.values())label.remove();for(const material of this.tinted.values())material.dispose();this.tinted.clear();}
}
