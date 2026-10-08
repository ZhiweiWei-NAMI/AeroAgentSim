import { Pass } from 'postprocessing';
import type { Scene, Camera } from 'three';
export class N8AOPostPass extends Pass {
  constructor(scene: Scene, camera: Camera, width: number, height: number);
  configuration: { aoRadius: number; intensity: number; distanceFalloff: number; halfRes: boolean; gammaCorrection: boolean; screenSpaceRadius: boolean };
  setQualityMode(mode: string): void;
}
