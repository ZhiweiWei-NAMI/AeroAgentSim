import { describe, expect, it, vi } from 'vitest';
import * as T from 'three';
import { QualityMaterials } from './quality-materials';

describe('per-view low quality materials', () => {
  it('keeps source textures and restores the exact normal-quality material', () => {
    const texture = new T.Texture(), source = new T.MeshStandardMaterial({ color: '#c3b495', map: texture, transparent: true, opacity: 0.8, side: T.DoubleSide });
    source.userData.sharedAsset = true;
    const mesh = new T.Mesh(new T.BoxGeometry(), source), secondView = mesh.clone();
    const sourceDispose = vi.spyOn(source, 'dispose'), textureDispose = vi.spyOn(texture, 'dispose');
    const quality = new QualityMaterials(); quality.apply(mesh, true);
    const cheap = mesh.material;
    expect(cheap).toBeInstanceOf(T.MeshLambertMaterial);
    expect(cheap.map).toBe(texture); expect(cheap.color).toEqual(source.color);
    expect(cheap.opacity).toBe(0.8); expect(cheap.side).toBe(T.DoubleSide);
    expect(secondView.material).toBe(source);
    const dispose = vi.spyOn(cheap, 'dispose');
    quality.apply(mesh, false); expect(mesh.material).toBe(source);
    quality.apply(mesh, true); expect(mesh.material).toBe(cheap);
    quality.dispose(); expect(mesh.material).toBe(source); expect(dispose).toHaveBeenCalledOnce();
    expect(sourceDispose).not.toHaveBeenCalled(); expect(textureDispose).not.toHaveBeenCalled();
  });

  it('shares simplified materials across streamed meshes and leaves custom shaders intact', () => {
    const source = new T.MeshStandardMaterial(), custom = new T.MeshStandardMaterial();
    custom.onBeforeCompile = () => {};
    const first = new T.Mesh(new T.BoxGeometry(), source), next = first.clone();
    const shaderMesh = new T.Mesh(new T.BoxGeometry(), custom), group = new T.Group();
    group.add(first, shaderMesh);
    const quality = new QualityMaterials(); quality.apply(group, true);
    group.add(next); quality.apply(group, true);
    expect(next.material).toBe(first.material); expect(shaderMesh.material).toBe(custom);
    quality.dispose(); expect(first.material).toBe(source); expect(next.material).toBe(source);
  });

  it('retains the ground shader and follows its day/dusk colour changes', () => {
    const source = new T.MeshStandardMaterial();
    source.userData.lambertCompatible = true;
    source.onBeforeCompile = vi.fn();
    const mesh = new T.Mesh(new T.PlaneGeometry(), source), quality = new QualityMaterials();
    quality.apply(mesh, true);
    expect(mesh.material.onBeforeCompile).toBe(source.onBeforeCompile);
    source.color.set('#bbbcb4');
    expect(mesh.material.color.getHexString()).toBe('bbbcb4');
    quality.dispose(); expect(mesh.material).toBe(source);
  });
});
