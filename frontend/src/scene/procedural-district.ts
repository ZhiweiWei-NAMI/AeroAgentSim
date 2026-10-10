import * as T from 'three';
import type {FeedStore} from '../viewport/feed-store';
/** Decorative context only. Its bounds follow recorded actors, not real geography. */
export class ProceduralDistrict {
 readonly root=new T.Group();
 bounds?:T.Box3;
 private ready=false;
 update(store:FeedStore,renderOrigin:T.Vector3){
  if(!this.ready){
   const points:T.Vector3[]=[];
   for(const entity of store.entities.values()){
    const position=entity.fields.get('lowalt.site_position')?.value??entity.fields.get('lowalt.position')?.value;
    if(position===undefined)continue;
    if(!Array.isArray(position)||position.length!==3||!position.every(value=>typeof value==='number'&&Number.isFinite(value)))throw Error('Recorded actor/site position must be a finite 3-vector');
    points.push(new T.Vector3(position[0],0,-position[1]));
   }
   if(!points.length)return;
   const bounds=new T.Box3().setFromPoints(points).expandByScalar(100),size=bounds.getSize(new T.Vector3());
   bounds.min.y=0;bounds.max.y=80;this.bounds=bounds;
   const center=bounds.getCenter(new T.Vector3());
   const floor=new T.Mesh(new T.PlaneGeometry(size.x,size.z),new T.MeshStandardMaterial({color:'#52616b',roughness:1}));floor.rotation.x=-Math.PI/2;floor.position.set(center.x,-0.04,center.z);floor.receiveShadow=true;this.root.add(floor);
   const roadMaterial=new T.MeshStandardMaterial({color:'#243744',roughness:1});
   const windowMaterial=new T.MeshStandardMaterial({color:'#1b4057',roughness:0.35,metalness:0.15});
   const wallMaterial=new T.MeshStandardMaterial({color:'#466372',roughness:0.65});
   for(let x=Math.floor(bounds.min.x/100)*100;x<bounds.max.x;x+=100){
    const road=new T.Mesh(new T.PlaneGeometry(12,size.z),roadMaterial);road.rotation.x=-Math.PI/2;road.position.set(x,0,center.z);this.root.add(road);
    for(let z=Math.floor(bounds.min.z/100)*100;z<bounds.max.z;z+=100){
     const at=new T.Vector3(x+42,0,z+42);
     // Keep the actual sites and initial actors visible; these blocks are scenery.
     if(points.some(point=>point.distanceToSquared(at)<45*45))continue;
     const height=14+Math.abs((x/100*17+z/100*31)%4)*7;
     const building=new T.Mesh(new T.BoxGeometry(30,height,32),wallMaterial);building.position.set(at.x,height/2,at.z);building.castShadow=true;building.receiveShadow=true;this.root.add(building);
     for(let level=4;level<height-1;level+=5){const strip=new T.Mesh(new T.BoxGeometry(30.1,1.8,32.1),windowMaterial);strip.position.set(at.x,level,at.z);this.root.add(strip);}
    }
   }
   for(let z=Math.floor(bounds.min.z/100)*100;z<bounds.max.z;z+=100){const road=new T.Mesh(new T.PlaneGeometry(size.x,12),roadMaterial);road.rotation.x=-Math.PI/2;road.position.set(center.x,0.01,z);this.root.add(road);}
   this.ready=true;
  }
  this.root.position.copy(renderOrigin).negate();
 }
 dispose(){const geometry=new Set<T.BufferGeometry>(),materials=new Set<T.Material>();this.root.traverse(node=>{if(node instanceof T.Mesh){geometry.add(node.geometry);for(const material of Array.isArray(node.material)?node.material:[node.material])materials.add(material);}});for(const item of geometry)item.dispose();for(const item of materials)item.dispose();this.root.clear();}
}
