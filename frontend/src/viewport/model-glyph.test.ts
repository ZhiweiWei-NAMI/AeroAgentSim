import * as T from 'three';
import { describe, expect, it } from 'vitest';
import { modelGlyph } from './model-glyph';

describe('actual asset distance silhouette',()=>{
  it('keeps separate transformed parts and normalizes display size',()=>{
    const model=new T.Group();
    const body=new T.Mesh(new T.BoxGeometry(1,0.5,0.5),new T.MeshBasicMaterial());
    const wing=new T.Mesh(new T.BoxGeometry(1,0.1,0.2),body.material);wing.position.x=2;
    model.add(body,wing);
    const glyph=modelGlyph(model);glyph.computeBoundingBox();
    const bounds=glyph.boundingBox!;
    expect(bounds.getSize(new T.Vector3()).x).toBeCloseTo(1.6,5);
    expect(bounds.max.x).toBeGreaterThan(1);
    const points=glyph.getAttribute('position');
    expect(Array.from({length:points.count},(_,i)=>points.getX(i)).some(x=>x>0.9)).toBe(true);
    expect(Array.from({length:points.count},(_,i)=>points.getX(i)).some(x=>x<0)).toBe(true);
    expect(glyph.getIndex()!.count).toBeGreaterThan(0);
    glyph.dispose();body.geometry.dispose();wing.geometry.dispose();body.material.dispose();
  });
  it('does not fabricate a glyph for a missing source mesh',()=>{
    expect(()=>modelGlyph(new T.Group())).toThrow(/no silhouette geometry/);
  });
});
