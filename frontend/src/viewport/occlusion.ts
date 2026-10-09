import * as T from 'three';

interface Triangle { a: number; b: number; c: number; side: T.Side }
interface Node { box: T.Box3; left?: Node; right?: Node; items?: number[] }
interface GeometryIndex { vertices: T.Vector3[]; triangles: Triangle[]; tree: Node }
interface Occluder { geometry: GeometryIndex; matrix: T.Matrix4; inverse: T.Matrix4; box: T.Box3 }

/** A static triangle BVH, built on attachment; rebases translate cached bounds and transforms.
 * Queries use Three's own local-space triangle test and material winding rules.
 * Invisible subtrees do not occlude labels; unlike Raycaster, this follows rendering.
 */
export class StaticOcclusion {
  private objects: Occluder[] = [];
  private tree?: Node;
  private worldRay = new T.Ray();
  private localRay = new T.Ray();
  private direction = new T.Vector3();
  private localEnd = new T.Vector3();
  private boxHit = new T.Vector3();
  private hit = new T.Vector3();
  private worldHit = new T.Vector3();
  private localFar = 0;
  private far = 0;

  setObjects(objects: T.Object3D[]) {
    this.dispose();
    for (const root of objects) {
      root.updateWorldMatrix(true, true);
      root.traverseVisible(object => {
        if (!(object instanceof T.Mesh) || object.userData.displayDecoration) return;
        const geometry = this.index(object);
        if (!geometry) return;
        const count = object instanceof T.InstancedMesh ? object.count : 1;
        for (let i=0;i<count;i++) {
          const matrix = object.matrixWorld.clone();
          if(object instanceof T.InstancedMesh){const instance=new T.Matrix4();object.getMatrixAt(i,instance);matrix.multiply(instance);}
          if(matrix.determinant()===0) continue; // Singular display meshes have no ray-testable surface.
          this.objects.push({geometry,matrix,inverse:matrix.clone().invert(),box:geometry.tree.box.clone().applyMatrix4(matrix)});
        }
      });
    }
    if(this.objects.length)this.tree=build(this.objects.map(o=>o.box));
  }

  private index(mesh: T.Mesh): GeometryIndex | undefined {
    const source=mesh.geometry,position=source.getAttribute('position');
    if(!position)return;
    const vertices=Array.from({length:position.count},(_,i)=>mesh.getVertexPosition(i,new T.Vector3()));
    const index=source.getIndex(),count=index?index.count:position.count;
    const start=Math.max(0,source.drawRange.start),end=Math.min(count,start+source.drawRange.count);
    const triangles: Triangle[]=[];
    const add=(first:number,last:number,material:T.Material|undefined)=>{
      if(!material||!material.visible)return;
      for(let i=first;i+2<last;i+=3)triangles.push({a:index?index.getX(i):i,b:index?index.getX(i+1):i+1,c:index?index.getX(i+2):i+2,side:material.side});
    };
    if(Array.isArray(mesh.material))for(const group of source.groups)add(Math.max(start,group.start),Math.min(end,group.start+group.count),mesh.material[group.materialIndex ?? 0]);
    else add(start,end,mesh.material);
    if(!triangles.length)return;
    const boxes=triangles.map(t=>new T.Box3().setFromPoints([vertices[t.a],vertices[t.b],vertices[t.c]]));
    return {vertices,triangles,tree:build(boxes)};
  }

  blocked(from: T.Vector3, to: T.Vector3) {
    this.direction.subVectors(to,from);this.far=this.direction.length();
    if(!this.tree||this.far===0)return false;
    this.worldRay.set(from,this.direction.divideScalar(this.far));
    return this.visitObjects(this.tree,to);
  }
  private intersects(ray:T.Ray,box:T.Box3,far:number) {
    if(box.containsPoint(ray.origin))return true;
    return ray.intersectBox(box,this.boxHit)!==null&&this.boxHit.distanceToSquared(ray.origin)<=far*far;
  }
  private visitObjects(node:Node,to:T.Vector3):boolean {
    if(!this.intersects(this.worldRay,node.box,this.far))return false;
    if(node.items) {
      for(const i of node.items){
        const object=this.objects[i];
        if(!this.intersects(this.worldRay,object.box,this.far))continue;
        this.localRay.copy(this.worldRay).applyMatrix4(object.inverse);
        this.localEnd.copy(to).applyMatrix4(object.inverse);
        // Widen only the BVH bound; accepted hits still use Three's exact world distance.
        this.localFar=this.localEnd.distanceTo(this.localRay.origin)*(1+1e-12);
        if(this.visitTriangles(object.geometry.tree,object))return true;
      }
      return false;
    }
    return !!node.left&&this.visitObjects(node.left,to)||!!node.right&&this.visitObjects(node.right,to);
  }
  private visitTriangles(node:Node,object:Occluder):boolean {
    if(!this.intersects(this.localRay,node.box,this.localFar))return false;
    if(node.items){
      const {vertices,triangles}=object.geometry;
      for(const i of node.items){
        const triangle=triangles[i],a=vertices[triangle.a],b=vertices[triangle.b],c=vertices[triangle.c];
        const point=triangle.side===T.BackSide?this.localRay.intersectTriangle(c,b,a,true,this.hit):this.localRay.intersectTriangle(a,b,c,triangle.side!==T.DoubleSide,this.hit);
        if(point&&this.worldHit.copy(point).applyMatrix4(object.matrix).distanceTo(this.worldRay.origin)<=this.far)return true;
      }
      return false;
    }
    return !!node.left&&this.visitTriangles(node.left,object)||!!node.right&&this.visitTriangles(node.right,object);
  }
  /** A floating-origin shift preserves local triangles and avoids a full rebuild. */
  translate(offset:T.Vector3){
    for(const object of this.objects){
      const e=object.matrix.elements;e[12]+=offset.x;e[13]+=offset.y;e[14]+=offset.z;
      object.inverse.copy(object.matrix).invert();object.box.translate(offset);
    }
    const move=(node:Node)=>{node.box.translate(offset);if(node.left)move(node.left);if(node.right)move(node.right);};
    if(this.tree)move(this.tree);
  }
  dispose(){this.objects.length=0;this.tree=undefined;}
}

function build(boxes:T.Box3[]):Node {
  const centers=boxes.map(box=>box.getCenter(new T.Vector3()));
  const partition=(items:number[]):Node=>{
    const box=new T.Box3();for(const i of items)box.union(boxes[i]);
    if(items.length<=8)return {box,items};
    const span=box.getSize(new T.Vector3()),axis=span.x>=span.y&&span.x>=span.z?'x':span.y>=span.z?'y':'z';
    items.sort((a,b)=>centers[a][axis]-centers[b][axis]);
    const middle=items.length>>>1;
    return {box,left:partition(items.slice(0,middle)),right:partition(items.slice(middle))};
  };
  return partition(boxes.map((_,i)=>i));
}
