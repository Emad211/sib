declare module "n8ao" {
  import type { Pass } from "postprocessing";
  import type { Camera, Color, Scene } from "three";
  /** گذر سایهٔ محیطی (ambient occlusion) سازگار با کتابخانهٔ postprocessing */
  export class N8AOPostPass extends Pass {
    constructor(scene: Scene, camera: Camera, width?: number, height?: number);
    configuration: { aoRadius: number; distanceFalloff: number; intensity: number; color: Color; halfRes: boolean; gammaCorrection: boolean; aoSamples: number; denoiseSamples: number; denoiseRadius: number; screenSpaceRadius: boolean; transparencyAware: boolean };
    setQualityMode(mode: "Performance" | "Low" | "Medium" | "High" | "Ultra"): void;
  }
}
