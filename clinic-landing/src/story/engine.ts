import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import {
  BloomEffect,
  EffectComposer,
  EffectPass,
  RenderPass,
  SMAAEffect,
  SMAAPreset,
  ToneMappingEffect,
  ToneMappingMode,
  VignetteEffect,
} from "postprocessing";
import type { N8AOPostPass } from "n8ao";
import type { Tier } from "./webgl";

/**
 * رندرر، صحنه، دوربین و زنجیرهٔ پس‌پردازش (pmndrs/postprocessing):
 * سایهٔ محیطی N8AO، درخشش ملایم، ضدپله SMAA و تون‌مپینگ سینمایی.
 * کیفیت تطبیقی: اگر دستگاه کند باشد، اول سایهٔ محیطی خاموش می‌شود و بعد رزولوشن پایین می‌آید.
 */
export class Engine {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(32, 16 / 9, 0.1, 400);
  readonly composer: EffectComposer;
  width = 1;
  height = 1;
  private dpr: number;
  private samples: number[] = [];
  private ao: N8AOPostPass | null = null;

  /** AO: کلاس N8AOPostPass که فقط روی دسکتاپ (سطح high) بارگذاری می‌شود */
  constructor(readonly canvas: HTMLCanvasElement, readonly tier: Tier, AO?: typeof N8AOPostPass) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: false, stencil: false, powerPreference: "high-performance" });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.NoToneMapping; // تون‌مپینگ در زنجیرهٔ پس‌پردازش
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.dpr = Math.min(window.devicePixelRatio || 1, tier === "high" ? 2 : 1.75);

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.5;
    pmrem.dispose();

    this.composer = new EffectComposer(this.renderer, { frameBufferType: THREE.HalfFloatType });
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    if (tier === "high" && AO) {
      const ao = (this.ao = new AO(this.scene, this.camera, 1, 1));
      ao.setQualityMode("Medium");
      Object.assign(ao.configuration, { aoRadius: 1.6, distanceFalloff: 0.6, intensity: 2.2, halfRes: true, gammaCorrection: false });
      this.composer.addPass(ao);
    }
    this.composer.addPass(
      new EffectPass(
        this.camera,
        new BloomEffect({ intensity: 0.45, luminanceThreshold: 0.97, luminanceSmoothing: 0.08, mipmapBlur: true, radius: 0.6 }),
        new VignetteEffect({ offset: 0.32, darkness: 0.42 }),
        new ToneMappingEffect({ mode: ToneMappingMode.NEUTRAL })
      )
    );
    this.composer.addPass(new EffectPass(this.camera, new SMAAEffect({ preset: tier === "high" ? SMAAPreset.HIGH : SMAAPreset.MEDIUM })));
  }

  resize(w: number, h: number) {
    this.width = Math.max(1, w);
    this.height = Math.max(1, h);
    this.renderer.setPixelRatio(this.dpr);
    this.renderer.setSize(this.width, this.height, false);
    this.composer.setSize(this.width, this.height, false);
    this.camera.aspect = this.width / this.height;
    this.camera.updateProjectionMatrix();
  }

  render() {
    this.composer.render();
  }

  /** زمان هر فریم را می‌گیرد و در صورت کندی، کیفیت را پایین می‌آورد (فقط رو به پایین، تا نوسان نکند). */
  sample(dtMs: number) {
    if (dtMs > 250) return; // تب پس‌زمینه یا توقف
    this.samples.push(dtMs);
    if (this.samples.length < 90) return;
    const avg = this.samples.reduce((a, b) => a + b, 0) / this.samples.length;
    this.samples.length = 0;
    if (avg <= 24) return;
    if (this.ao?.enabled) {
      this.ao.enabled = false;
    } else if (this.dpr > 1) {
      this.dpr = Math.max(1, this.dpr - 0.25);
      this.resize(this.width, this.height);
    }
  }

  dispose() {
    this.composer.dispose();
    this.renderer.dispose();
    this.scene.traverse((o) => {
      const m = o as THREE.Mesh;
      m.geometry?.dispose?.();
    });
  }
}

