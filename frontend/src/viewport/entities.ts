import * as T from 'three';
import type { EntityKey, PresentationBinding, RunHeader } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { resolveBinding, entityId } from './bindings';
import { Assets } from './assets';
import { worldOrientation, worldPosition } from './coordinates';

interface Batch { meshes: T.InstancedMesh[]; transforms: T.Matrix4[]; keys: EntityKey[]; capacity: number; loaded: boolean; far?: { mesh: T.InstancedMesh; keys: EntityKey[] }; outline?: T.Mesh[]; assetBasis?: T.Quaternion }
export class EntityLayer {
  readonly root = new T.Group();
  readonly positions = new Map<string, T.Vector3>();
  readonly orientations = new Map<string, T.Quaternion>();
  readonly ring: T.Mesh;
  readonly selection: T.Object3D[]=[];
  private batches = new Map<PresentationBinding, Batch>();
  private labels = new Map<string, HTMLButtonElement>();
  private trail: T.Line;
  private dummy = new T.Object3D();
  private disposed = false;
  private raycaster = new T.Raycaster();
  private matrix = new T.Matrix4();
  private lastTrail = '';
  constructor(private header: RunHeader, private assets: Assets, private overlay: HTMLElement,
    private onSelect: (key: EntityKey) => void, private onError: (error: unknown) => void,
    private status: (message: string) => void = () => {}) {
    this.ring = new T.Mesh(new T.TorusGeometry(0.8, 0.025, 8, 48), new T.MeshBasicMaterial({ color: '#9ce9ff', depthTest: false, transparent: true, opacity: 0.9 }));
    this.ring.rotation.x = -Math.PI / 2; this.ring.visible = false; this.ring.renderOrder=5; this.root.add(this.ring);
    this.trail = new T.Line(new T.BufferGeometry(), new T.LineBasicMaterial({ color: '#30bdf0', transparent: true, opacity: 0.85 }));
    this.trail.frustumCulled=false; this.root.add(this.trail);
  }
  private createBatch(binding: PresentationBinding, capacity: number) {
    const geometry = new T.IcosahedronGeometry(0.85, 1);
    const material = new T.MeshStandardMaterial({ color: binding.visual.color ?? '#39bdf0', roughness: 0.35, metalness: 0.2 });
    const marker = new T.InstancedMesh(geometry, material, capacity);
    marker.castShadow = true; marker.frustumCulled = false; marker.count=0; this.root.add(marker);
    const batch: Batch = { meshes: [marker], transforms: [new T.Matrix4()], capacity, keys: [], loaded: binding.visual.kind !== 'model' };
    this.batches.set(binding, batch);
    if (binding.visual.kind === 'model') {
      if (!binding.visual.asset) throw Error(`Model binding ${binding.typeId} has no asset URL`);
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
        batch.far={mesh:batch.meshes[0],keys:[]};
        batch.meshes=meshes;batch.transforms=transforms;batch.loaded=true;
        batch.outline=meshes.map(instance=>{const mesh=new T.Mesh(instance.geometry,instance.material);mesh.layers.set(10);mesh.matrixAutoUpdate=false;mesh.visible=false;this.root.add(mesh);return mesh;});
        for(const mesh of meshes)this.root.add(mesh);
        this.status(`Models loaded · ${binding.visual.asset}`);
      }).catch(error=>{ if(!this.disposed) this.status(`Model unavailable: ${String(error)} · markers active`); });
    }
    return batch;
  }
  update(store: FeedStore, ns: string, camera: T.Camera, selected?: EntityKey, showTrails = true,
    renderOrigin = new T.Vector3(), occluders: T.Object3D[] = []) {
    this.positions.clear(); this.orientations.clear();this.selection.length=0;for(const batch of this.batches.values())for(const mesh of batch.outline ?? [])mesh.visible=false;
    for (const batch of this.batches.values()) { batch.keys = []; for(const mesh of batch.meshes)mesh.count=0; if(batch.far){batch.far.keys=[];batch.far.mesh.count=0;} }
    for (const label of this.labels.values()) label.hidden = true;
    const candidates: Array<{ id: string; key: EntityKey; position: T.Vector3; selected: boolean; forced: boolean }> = [];
    for (const [id, entity] of store.entities) {
      const binding = resolveBinding(this.header, entity.typeId); if (!binding) continue;
      const sample = store.sample(entity.key, binding.positionField, ns); if (!sample) continue;
      const position = worldPosition(sample, binding.frame, this.header.origin).sub(renderOrigin); this.positions.set(id, position);
      const orientation = binding.orientationField ? store.sample(entity.key, binding.orientationField, ns, true) : undefined;
      this.dummy.position.copy(position);
      this.dummy.quaternion.copy(orientation ? worldOrientation(orientation, binding.frame) : new T.Quaternion());
      if (orientation) this.orientations.set(id, this.dummy.quaternion.clone());
      this.dummy.scale.setScalar(binding.visual.scale ?? 1);
      if (binding.visual.kind !== 'label') {
        const batch = this.batches.get(binding) ?? this.createBatch(binding, Math.max(64, store.entities.size));
        const isSelected=!!selected&&entityId(selected)===id;
        const far=batch.far&&!isSelected&&(position.distanceTo(camera.position)>100||batch.keys.length>=48)?batch.far:undefined;
        if(far){
          if(far.keys.length>=far.mesh.instanceMatrix.count){const old=far.mesh;far.mesh=new T.InstancedMesh(old.geometry,old.material,old.instanceMatrix.count*2);far.mesh.frustumCulled=false;for(let i=0;i<far.keys.length;i++){old.getMatrixAt(i,this.matrix);far.mesh.setMatrixAt(i,this.matrix);}old.removeFromParent();old.dispose();this.root.add(far.mesh);}
          this.dummy.scale.setScalar(0.65);this.dummy.updateMatrix();far.mesh.setMatrixAt(far.keys.length,this.dummy.matrix);far.keys.push(entity.key);far.mesh.count=far.keys.length;
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
        batch.meshes.forEach((mesh,i)=>{this.matrix.multiplyMatrices(this.dummy.matrix,batch.transforms[i]);mesh.setMatrixAt(batch.keys.length,this.matrix);mesh.count=batch.keys.length+1;});
        batch.keys.push(entity.key);
        if(isSelected)batch.outline?.forEach((mesh,i)=>{mesh.matrix.multiplyMatrices(this.dummy.matrix,batch.transforms[i]);mesh.matrixWorldNeedsUpdate=true;mesh.visible=true;this.selection.push(mesh);});
        }
      }
      const isSelected=!!selected && entityId(selected)===id;
      candidates.push({id,key:entity.key,position,selected:isSelected,forced:binding.visual.kind==='label'});
    }
    for(const batch of this.batches.values()){for(const mesh of batch.meshes)mesh.instanceMatrix.needsUpdate=true;if(batch.far)batch.far.mesh.instanceMatrix.needsUpdate=true;}
    camera.updateMatrixWorld();
    const occupied: Array<[number,number]>=[];let labelAttempts=0;
    candidates.sort((a,b)=>Number(b.selected)-Number(a.selected)||a.position.distanceToSquared(camera.position)-b.position.distanceToSquared(camera.position));
    for(const item of candidates) {
      if(!item.selected&&!item.forced&&(occupied.length>=12||labelAttempts>=32))continue;
      const world=item.position.clone().add(new T.Vector3(0,2,0)), projected=world.clone().project(camera);
      if(projected.z<-1||projected.z>1||Math.abs(projected.x)>0.96||Math.abs(projected.y)>0.96)continue;
      const x=(projected.x+1)*this.overlay.clientWidth/2,y=(1-projected.y)*this.overlay.clientHeight/2;
      if(!item.selected&&occupied.some(([px,py])=>Math.abs(px-x)<110&&Math.abs(py-y)<32))continue;
      // Selected labels remain readable; nonselected labels do not shine through buildings.
      if(!item.selected&&occluders.length) {
        labelAttempts++;
        const direction=world.clone().sub(camera.position);this.raycaster.set(camera.position,direction.clone().normalize());this.raycaster.far=direction.length()-0.2;
        if(this.raycaster.intersectObjects(occluders,true).length)continue;
      }
      let label=this.labels.get(item.id);
      if(!label){label=document.createElement('button');label.className='entity-label';label.textContent=item.key.id;label.onclick=()=>this.onSelect(item.key);this.overlay.append(label);this.labels.set(item.id,label);}
      label.hidden=false;label.style.left=`${x}px`;label.style.top=`${y}px`;label.classList.toggle('selected',item.selected);occupied.push([x,y]);
    }
    const focus=selected?this.positions.get(entityId(selected)):undefined;
    this.ring.visible=!!focus;if(focus)this.ring.position.copy(focus).add(new T.Vector3(0,-0.2,0));
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
  pick(raycaster:T.Raycaster):EntityKey|undefined {
    for(const hit of raycaster.intersectObject(this.root,true))for(const batch of this.batches.values()){if(batch.meshes.includes(hit.object as T.InstancedMesh)&&hit.instanceId!==undefined)return batch.keys[hit.instanceId];if(hit.object===batch.far?.mesh&&hit.instanceId!==undefined)return batch.far.keys[hit.instanceId];}
    return undefined;
  }
  dispose(){this.disposed=true;for(const label of this.labels.values())label.remove();}
}
