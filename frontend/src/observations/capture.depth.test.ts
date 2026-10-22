// @vitest-environment node
import { describe, expect, it } from 'vitest';
import * as T from 'three';
import type { RunHeader } from '../contracts/viewer-feed';
import { cloneModel } from '../viewport/assets';
import { resolveBinding } from '../viewport/bindings';
import {
  MAX_CAPTURE_DEPTH_PIXELS, captureCalibration, captureCameraPose, captureDepthBinding,
  captureDepthPixelCount, captureDepthProducts, captureMeshHeader, encodeCaptureDepth, staticSkinnedBinding,
} from './capture';

// Only data, matrices and CPU scene objects: no renderer, DOM, canvas or asset I/O.
const sourceCut = { index: 42, instant: [{ $integer: '9007199254740993' }, 2] };
const binding = { request_id: 'capture:registered-1', source_cut: sourceCut };
const request = { ...binding, camera: { depth: true } };
const rgba = (values: Array<[number, number]>) => new Float32Array(values.flatMap(([z,valid]) => [z,valid,0,1]));
const floats = (bytes: Uint8Array) => {
  const view = new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  return Array.from({length:bytes.length/4},(_,i) => view.getFloat32(i*4,true));
};
function close(actual: readonly number[], expected: readonly number[]) {
  expect(actual).toHaveLength(expected.length);
  actual.forEach((value,i) => expect(value).toBeCloseTo(expected[i],10));
}

describe('registered geometric depth wire helpers', () => {
  it('flips bottom-up readback rows once and writes explicit little-endian float32 metres', () => {
    const input = rgba([[4,1],[8,1],[1.25,1],[2.5,1]]), original = input.slice();
    const bytes = encodeCaptureDepth(input,2,2,1,8);
    expect(floats(bytes)).toEqual([1.25,2.5,4,8]);
    expect([...bytes]).toEqual([0,0,160,63,0,0,32,64,0,0,128,64,0,0,0,65]);
    expect(input).toEqual(original);
  });

  it('maps invalid flags, infinities and out-of-clip values to NaN, retaining clip boundaries', () => {
    const input = rgba([[NaN,1],[Infinity,1],[-Infinity,1],[0.5,1],[9,1],
      [4,0],[4,0.5],[4,NaN],[4,Infinity],[1,1],[8,1],[3,0.75]]);
    const bytes = encodeCaptureDepth(input,12,1,1,8), values = floats(bytes);
    values.slice(0,9).forEach(value => expect(Number.isNaN(value)).toBe(true));
    expect(values.slice(9)).toEqual([1,8,3]);
    const view = new DataView(bytes.buffer);
    // NaN is defined by IEEE exponent/mantissa, not one chosen payload/sign.
    for (let i=0;i<9;i++) {
      const bits = view.getUint32(i*4,true);
      expect(bits & 0x7f800000).toBe(0x7f800000);
      expect(bits & 0x007fffff).not.toBe(0);
    }
  });

  it('accepts every IEEE float32 NaN readback as missing regardless of payload/sign', () => {
    const input = rgba([[0,1],[0,1],[0,1],[0,1]]), view = new DataView(input.buffer);
    [0x7fc00001,0x7fffffff,0xffc00001,0x7f800001].forEach((bits,i) => view.setUint32(i*16,bits,true));
    floats(encodeCaptureDepth(input,4,1,1,8)).forEach(value => expect(Number.isNaN(value)).toBe(true));
  });

  it('enforces the receiver pixel bound before allocation and validates input shape/clip planes', () => {
    expect(MAX_CAPTURE_DEPTH_PIXELS).toBe(4_194_304);
    expect(captureDepthPixelCount(2048,2048)).toBe(MAX_CAPTURE_DEPTH_PIXELS);
    expect(captureDepthPixelCount(8192,512)).toBe(MAX_CAPTURE_DEPTH_PIXELS);
    for (const [width,height] of [[2049,2048],[8193,1],[0,1],[-1,1],[1.5,1],[NaN,1],[Infinity,1],[Number.MAX_SAFE_INTEGER,2]]) {
      expect(() => captureDepthPixelCount(width,height)).toThrow('4,194,304');
    }
    expect(() => encodeCaptureDepth(new Float32Array(3),1,1,1,8)).toThrow('size mismatch');
    for (const [near,far] of [[0,8],[2,2],[2,1],[NaN,8],[1,Infinity]]) {
      expect(() => encodeCaptureDepth(rgba([[2,1]]),1,1,near,far)).toThrow('clip planes');
    }
  });

  it('binds only opted-in depth to the exact request/cut and rejects text overlays', () => {
    expect(captureDepthBinding(request,2,2)).toEqual(binding);
    expect(captureDepthBinding(request,2,2)?.source_cut).toBe(sourceCut);
    expect(() => captureDepthBinding(request,2,2,'diagnostic')).toThrow('text overlay');
    expect(() => captureDepthBinding({...request,request_id:''},2,2)).toThrow('request ID');
    expect(() => captureDepthBinding({...request,request_id:17},2,2)).toThrow('request ID');
    expect(() => captureDepthBinding({...request,source_cut:[]},2,2)).toThrow('record');
    expect(() => captureDepthBinding(request,2049,2048)).toThrow('4,194,304');
    for (const depth of [null,0,1,'true',{}]) {
      expect(() => captureDepthBinding({...request,camera:{depth}},2,2)).toThrow('must be boolean');
    }
    // No new pixel limit, label restriction or metadata appears on RGB-only requests.
    expect(captureDepthBinding({camera:{}},8192,8192,'RGB label')).toBeUndefined();
    expect(captureDepthBinding({camera:{depth:false}},8192,8192,'RGB label')).toBeUndefined();
  });

  it('packs the archived contract with matching provenance on all three products', () => {
    const camera = new T.PerspectiveCamera(90,2,1,8);
    const result = captureDepthProducts(rgba([[1.25,1],[8,0]]),2,1,camera,binding);
    expect(result.depth).toEqual({...binding,encoding:'float32-le-metres',width:2,height:1,depth_convention:'optical_z',
      data_base64:result.depth.data_base64,near_m:1,far_m:8,vertical_origin:'top-left',invalid:'NaN',source:'same-camera-webgl-depth-buffer'});
    const decoded = Uint8Array.from(atob(result.depth.data_base64),character => character.charCodeAt(0));
    expect(decoded.byteLength).toBe(8);
    expect(floats(decoded)[0]).toBe(1.25); expect(Number.isNaN(floats(decoded)[1])).toBe(true);
    expect(result.calibration).toMatchObject({width:2,height:1,registered_rgb_depth:true,depth_convention:'optical_z',
      depth_min_m:1,depth_max_m:8,distortion:[],depth_semantics:'rendered geometry (optical z); not physical ranging or weather sensing'});
    expect(result.camera_pose.source).toBe('applied_committed_capture_camera');
    for (const product of [result.depth,result.calibration,result.camera_pose]) {
      expect(product.request_id).toBe(binding.request_id); expect(product.source_cut).toBe(sourceCut);
    }
    const other = {...binding,request_id:'capture:registered-2',source_cut:{index:43,instant:[100,0]}};
    const next = captureDepthProducts(rgba([[3,1]]),1,1,camera,other);
    for (const product of [next.depth,next.calibration,next.camera_pose]) expect(product).toMatchObject(other);
    expect(result.depth.request_id).toBe('capture:registered-1');
  });
});

describe('calibration and applied optical camera axes', () => {
  it('exports flat row-major centred K using outer-edge pixels, including half-pixel centres', () => {
    const camera = new T.PerspectiveCamera(90,2,0.5,100);
    const calibration = captureCalibration(camera,800,400,binding);
    close(calibration.K,[200,0,400,0,200,200,0,0,1]);
    const fx = calibration.K[0], cx = calibration.K[2], fy = calibration.K[4], cy = calibration.K[5];
    for (const [x,y] of [[0,0],[799,399],[315,82]]) {
      const optical = [(x+0.5-cx)/fx,(y+0.5-cy)/fy,1];
      const ndc = new T.Vector3(optical[0],-optical[1],-optical[2]).applyMatrix4(camera.projectionMatrix);
      close([(ndc.x+1)*800/2,(1-ndc.y)*400/2],[x+0.5,y+0.5]);
    }
    const odd = new T.PerspectiveCamera(90,5/3,0.5,100);
    close(captureCalibration(odd,5,3,binding).K,[1.5,0,2.5,0,1.5,1.5,0,0,1]);
  });

  it('converts default right/up/back render camera to optical right/down/forward in ENU', () => {
    const matrix = new T.Matrix4().makeTranslation(12,70,-34), original = matrix.clone();
    const pose = captureCameraPose(matrix,binding);
    pose.T_world_enu_from_camera_optical.forEach((row,i) => close(row,[
      [1,0,0,12],[0,0,1,34],[0,-1,0,70],[0,0,0,1],
    ][i]));
    expect(matrix.equals(original)).toBe(true);
  });

  it('uses the actual nadir rotation: optical right=east, down=south, forward=down', () => {
    const camera = new T.PerspectiveCamera();
    camera.position.set(12,70,-34); camera.up.set(0,0,-1); camera.lookAt(12,0,-34); camera.updateMatrixWorld(true);
    const rows = captureCameraPose(camera.matrixWorld,binding).T_world_enu_from_camera_optical;
    rows.forEach((row,i) => close(row,[[1,0,0,12],[0,-1,0,34],[0,0,-1,70],[0,0,0,1]][i]));
  });

  it('retains Three lookAt vertical-axis perturbation for explicit ENU cameras with default up', () => {
    // Explicit eye ENU [5,6,10], looking vertically down/up. Unlike a nadir
    // preset, these cameras retain default render-world up [0,1,0]. Three
    // resolves the parallel-axis degeneracy by adding 0.0001 to backward.z.
    const c = 1/Math.hypot(1,0.0001), s = 0.0001*c;
    for (const sign of [-1,1]) {
      const camera = new T.PerspectiveCamera();
      camera.position.set(5,10,-6); camera.lookAt(5,10-sign*10,-6); camera.updateMatrixWorld(true);
      const rows = captureCameraPose(camera.matrixWorld,binding).T_world_enu_from_camera_optical;
      rows.forEach((row,i) => close(row,[[1,0,0,5],[0,-sign*c,s,6],[0,-s,-sign*c,10],[0,0,0,1]][i]));
      expect(rows[1][2]).toBeGreaterThan(0); // Applied optical axis, not nominal vertical.
    }
  });

  it('maps an arbitrary applied camera axis and point, without substituting an actor pose', () => {
    const camera = new T.PerspectiveCamera(60,4/3,0.15,16000);
    camera.position.set(7,30,-11); camera.lookAt(20,0,-50); camera.updateMatrixWorld(true);
    const rows = captureCameraPose(camera.matrixWorld,binding).T_world_enu_from_camera_optical;
    const transform = new T.Matrix4().set(...rows.flat() as Parameters<T.Matrix4['set']>);
    const optical = new T.Vector3(2,3,10), render = new T.Vector3(2,-3,-10).applyMatrix4(camera.matrixWorld);
    close(optical.applyMatrix4(transform).toArray(),[render.x,-render.z,render.y]);
    close(new T.Vector3(0,0,1).transformDirection(transform).toArray(),[13,39,-30].map(v => v/Math.hypot(13,39,30)));
    expect(transform.determinant()).toBeCloseTo(1,10);
  });
});

function skinnedHeader(): RunHeader {
  return {contract:'aeroagentsim.viewer-feed/v1',runId:'run',registryDigest:'registry',start:{ns:'0',microstep:0},fields:[],
    types:[{typeId:'person',displayName:'Person',ancestors:[]},{typeId:'responder',displayName:'Responder',ancestors:['person']},
      {typeId:'vehicle',displayName:'Vehicle',ancestors:[]}],
    presentation:[{typeId:'person',positionField:'pose',orientationField:'rotation',frame:'enu',visual:{kind:'model',asset:'person.glb',scale:2,catalog:'uav'}},
      {typeId:'vehicle',positionField:'position',frame:'enu',visual:{kind:'model',asset:'car.glb'}}],
    modelCatalog:{person:{url:'person.glb',kind:'pedestrian',sizeM:[0.5,0.5,1.8],forward:'+z',up:'+y',skinned:true},
      car:{url:'car.glb',kind:'road_vehicle',sizeM:[4,2,1.5],forward:'+x',up:'+y'}},
  };
}

describe('static skinned bindings and cache-source cloning', () => {
  it('keeps ancestor binding/pose fields while removing only the skinned fallback mesh route', () => {
    const header = skinnedHeader(), before = JSON.stringify(header), meshHeader = captureMeshHeader(header);
    const selected = staticSkinnedBinding(header,'responder');
    expect(selected?.binding).toBe(header.presentation[0]); expect(selected?.model.url).toBe('person.glb');
    expect(resolveBinding(meshHeader,'responder')).toMatchObject({positionField:'pose',orientationField:'rotation',frame:'enu',visual:{kind:'label',asset:'person.glb',scale:2}});
    expect(resolveBinding(meshHeader,'responder')?.visual.catalog).toBeUndefined();
    expect(meshHeader.presentation[1]).toBe(header.presentation[1]);
    expect(staticSkinnedBinding(header,'vehicle')).toBeUndefined(); expect(staticSkinnedBinding(header,'unknown')).toBeUndefined();
    expect(JSON.stringify(header)).toBe(before);
  });

  it('respects a direct non-skinned binding rather than resurrecting the ancestor model', () => {
    const header = skinnedHeader();
    header.presentation.push({typeId:'responder',positionField:'other',frame:'enu',visual:{kind:'marker'}});
    expect(staticSkinnedBinding(header,'responder')).toBeUndefined();
    expect(resolveBinding(captureMeshHeader(header),'responder')).toBe(header.presentation[2]);
  });

  it('leaves ordinary RGB model cloning unchanged', () => {
    const root = new T.Group(), geometry = new T.BoxGeometry(), material = new T.MeshBasicMaterial();
    const mesh = new T.Mesh(geometry,material); mesh.name='rigid'; root.add(mesh);
    const cloned = cloneModel(root), copy = cloned.getObjectByName('rigid') as T.Mesh;
    expect(cloned).not.toBe(root); expect(copy).not.toBe(mesh);
    expect(copy.geometry).toBe(geometry); expect(copy.material).toBe(material);
    geometry.dispose(); material.dispose();
  });

  it('remaps skeleton bones inside each clone and preserves the authored static pose', () => {
    const root = new T.Group(), bone = new T.Bone(), tip = new T.Bone();
    bone.name='hip'; tip.name='tip'; tip.position.y=1; bone.add(tip);
    const geometry = new T.BufferGeometry(), material = new T.MeshBasicMaterial();
    const mesh = new T.SkinnedMesh(geometry,material); mesh.name='body'; root.add(bone,mesh); root.updateMatrixWorld(true);
    mesh.bind(new T.Skeleton([bone,tip]));
    tip.rotation.z=0.4; root.updateMatrixWorld(true); mesh.skeleton.update();
    const first = cloneModel(root,true), second = cloneModel(root,true);
    const firstMesh = first.getObjectByName('body') as T.SkinnedMesh, secondMesh = second.getObjectByName('body') as T.SkinnedMesh;
    expect(firstMesh.skeleton).not.toBe(mesh.skeleton); expect(firstMesh.skeleton).not.toBe(secondMesh.skeleton);
    expect(firstMesh.skeleton.bones[0]).toBe(first.getObjectByName('hip'));
    expect(firstMesh.skeleton.bones[1]).toBe(first.getObjectByName('tip'));
    expect(firstMesh.skeleton.bones[0]).not.toBe(bone);
    expect(firstMesh.skeleton.bones[1].rotation.z).toBeCloseTo(0.4);
    expect(firstMesh.geometry).toBe(geometry); expect(firstMesh.material).toBe(material);
    firstMesh.skeleton.bones[1].rotation.z=0.8;
    expect(tip.rotation.z).toBeCloseTo(0.4); expect(secondMesh.skeleton.bones[1].rotation.z).toBeCloseTo(0.4);
    expect(firstMesh.skeleton.boneMatrices).not.toBe(mesh.skeleton.boneMatrices);
    firstMesh.skeleton.dispose(); secondMesh.skeleton.dispose(); mesh.skeleton.dispose(); geometry.dispose(); material.dispose();
  });
});
