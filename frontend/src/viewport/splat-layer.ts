import * as T from 'three';
import type { ScenePresentation } from '../scene/presentation';

type Splat = NonNullable<ScenePresentation['splats']>[number];
type SplatState = 'loading' | 'loaded' | 'error';

/** Numeric transform values are an untrusted display boundary: saved YAML/JSON may carry
 * exponent tokens such as `"5e-06"`, which `Quaternion.fromArray` turns into NaN matrices. */
function finiteNumber(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  throw Error(`splat transform: expected a finite number, received ${JSON.stringify(value)}`);
}

function vector(value: unknown, length: number): number[] {
  if (!Array.isArray(value) || value.length !== length) {
    throw Error(`splat transform: expected ${length} numbers, received ${JSON.stringify(value)}`);
  }
  return value.map(finiteNumber);
}

interface SplatTransform { position: number[]; quaternion: number[]; scale: number }

/** Absent properties take their documented defaults; present malformed properties are rejected. */
export function splatTransform(splat: Splat): SplatTransform {
  const position = splat.position === undefined ? [0, 0, 0] : vector(splat.position, 3);
  let quaternion = splat.quaternion === undefined ? [0, 0, 0, 1] : vector(splat.quaternion, 4);
  const rotation = new T.Quaternion(quaternion[0], quaternion[1], quaternion[2], quaternion[3]);
  if (!Number.isFinite(rotation.lengthSq()) || rotation.lengthSq() === 0) {
    throw Error('splat transform: quaternion must have a nonzero length');
  }
  rotation.normalize();
  quaternion = [rotation.x, rotation.y, rotation.z, rotation.w];
  const scale = splat.scale === undefined ? 1 : finiteNumber(splat.scale);
  if (!(scale > 0)) throw Error(`splat transform: scale must be positive, received ${scale}`);
  return { position, quaternion, scale };
}

export interface LoadSplatsOptions {
  renderer: T.WebGLRenderer;
  scene: T.Scene;
  signal: AbortSignal;
  /** Splat meshes join this display group; the SparkRenderer itself stays on the scene. */
  parent?: T.Object3D;
  /** Run viewport renders into a linear composer; the Studio preview renders straight to canvas. */
  encodeLinear?: boolean;
  onState?: (state: SplatState, detail?: string) => void;
  invalidate?: () => void;
}

/** Display-only Gaussian-splat overlays. Never picked, never occluding, never a terrain-height input.
 * Released on `signal` abort because the viewport aborts before disposing renderer resources. */
export async function loadSplats(splats: Splat[], options: LoadSplatsOptions): Promise<void> {
  const { renderer, scene, signal, parent = scene, encodeLinear = false, onState, invalidate } = options;
  if (!splats.length || signal.aborted) return;
  onState?.('loading');
  let SparkRenderer: typeof import('@sparkjsdev/spark').SparkRenderer;
  let SplatMesh: typeof import('@sparkjsdev/spark').SplatMesh;
  try {
    ({ SparkRenderer, SplatMesh } = await import('@sparkjsdev/spark'));
  } catch (error) {
    if (!signal.aborted) onState?.('error', `Spark unavailable: ${String(error)}`);
    return;
  }
  if (signal.aborted) return;
  const spark = new SparkRenderer({ renderer });
  spark.defaultView.encodeLinear = encodeLinear;
  scene.add(spark);
  const meshes: InstanceType<typeof SplatMesh>[] = [];
  let sparkReleased = false;
  const release = () => {
    if (sparkReleased) return;
    sparkReleased = true;
    for (const mesh of meshes) { mesh.removeFromParent(); try { mesh.dispose(); } catch { /* already disposed */ } }
    spark.removeFromParent();
    try { spark.defaultView.dispose(); } catch { /* version-specific */ }
    try { spark.material.dispose(); } catch { /* version-specific */ }
  };
  signal.addEventListener('abort', release, { once: true });
  // Spark sorts asynchronously and publishes its display buffer later. Without a nudge the paused
  // viewport/draft can finish its one redraw before the overlay exists. This wrapper only re-renders
  // when Spark actually prepares a viewpoint, so an idle view still settles.
  const prepare = spark.prepareViewpoint.bind(spark);
  spark.prepareViewpoint = (...args: Parameters<typeof spark.prepareViewpoint>) => {
    const result = prepare(...args);
    if (!signal.aborted) invalidate?.();
    return result;
  };
  await Promise.all(splats.map(async splat => {
    let mesh: InstanceType<typeof SplatMesh> | undefined;
    try {
      const transform = splatTransform(splat);
      mesh = new SplatMesh({ url: splat.url });
      meshes.push(mesh);
      mesh.name = 'splat-overlay';
      mesh.position.fromArray(transform.position);
      mesh.quaternion.fromArray(transform.quaternion);
      mesh.scale.setScalar(transform.scale);
      mesh.raycast = () => {};
      await mesh.initialized;
      if (signal.aborted) { mesh.removeFromParent(); try { mesh.dispose(); } catch { /* abort race */ } return; }
      parent.add(mesh);
      onState?.('loaded');
      invalidate?.();
    } catch (error) {
      if (mesh) { mesh.removeFromParent(); try { mesh.dispose(); } catch { /* abort race */ } }
      if (!signal.aborted) onState?.('error', `Splat overlay unavailable (${splat.url}): ${String(error)}`);
    }
  }));
  if (signal.aborted) release();
}
