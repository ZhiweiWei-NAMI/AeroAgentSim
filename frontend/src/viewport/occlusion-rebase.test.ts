import { describe, expect, it, vi } from 'vitest';
import * as T from 'three';
import { StaticOcclusion } from './occlusion';

describe('floating-origin occlusion translation',()=>{
  it('moves cached transformed instances without rebuilding triangle geometry',()=>{
    const root=new T.Group(),mesh=new T.InstancedMesh(new T.BoxGeometry(2,2,2),new T.MeshBasicMaterial({side:T.DoubleSide}),2);
    mesh.setMatrixAt(0,new T.Matrix4().makeTranslation(0,0,0));
    mesh.setMatrixAt(1,new T.Matrix4().makeTranslation(4,0,0));
    root.add(mesh);root.position.set(2500,3,0);root.rotation.y=.3;root.scale.set(2,1,1.5);
    const read=vi.spyOn(mesh,'getVertexPosition'),index=new StaticOcclusion();index.setObjects([root]);
    const reads=read.mock.calls.length;
    for(const shift of [new T.Vector3(-2500,0,0),new T.Vector3(400,0,-600),new T.Vector3(-400,0,600)]){
      root.position.add(shift);root.updateMatrixWorld(true);index.translate(shift);
      for(let i=0;i<2;i++){
        const matrix=new T.Matrix4();mesh.getMatrixAt(i,matrix);matrix.premultiply(mesh.matrixWorld);
        const center=new T.Vector3().setFromMatrixPosition(matrix),from=center.clone().add(new T.Vector3(0,0,10)),to=center.clone();
        const ray=new T.Raycaster(from,to.clone().sub(from).normalize(),0,from.distanceTo(to));
        expect(index.blocked(from,to)).toBe(ray.intersectObject(root,true).length>0);
      }
    }
    // Raycaster itself reads vertices, so isolate only the translation operation.
    read.mockClear();index.translate(new T.Vector3(1,0,0));expect(read).not.toHaveBeenCalled();expect(reads).toBeGreaterThan(0);
    index.dispose();read.mockRestore();
  });
});
