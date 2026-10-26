import * as THREE from "three";
import gsap from "gsap";
import { Engine } from "./engine";
import type { N8AOPostPass } from "n8ao";
import type { Tier } from "./webgl";
import { World, R, frameAt, stationAngle, yawOut, type WorldAssets } from "./world";
import { Labels } from "./labels";
import { Character } from "./kit/character";
import { panels } from "./kit/kit";
import { Cue, type Timing } from "./types";
import { SCENES } from "./scenes";
import { SHOT, LEAD, MOVE, type Ctx, type Shot, type Walker } from "./rig";
import type { SfxCue } from "./audio";

export class Story {
  readonly engine: Engine;
  readonly tl: gsap.core.Timeline;
  readonly duration: number;
  /** افکت‌های صوتی که صحنه‌ها زمان‌بندی کرده‌اند */
  readonly cues: SfxCue[] = [];
  private ctx: Ctx;
  private frameFns: ((time: number) => void)[] = [];
  private tmp = frameAt(0);
  private look = new THREE.Vector3();

  constructor(canvas: HTMLCanvasElement, labelLayer: HTMLElement, overlay: HTMLElement, readonly timing: Timing, assets: WorldAssets & { tier: Tier; AO?: typeof N8AOPostPass }) {
    this.engine = new Engine(canvas, assets.tier, assets.AO);
    const world = new World(this.engine.scene, assets);
    world.setShadowSize(this.engine.tier === "high" ? 2048 : 1024);
    const labels = new Labels(labelLayer, this.engine.camera);
    const reza = new Character("reza");
    this.engine.scene.add(reza.root);

    this.tl = gsap.timeline({ paused: true, defaults: { ease: "power2.out" } });
    this.duration = timing.total;
    // تایم‌لاین را دقیقاً به طول روایت می‌رسانیم
    this.tl.set({}, {}, this.duration);

    const cam: Shot = SHOT.overview(stationAngle(0) - 0.9, { y: 26, r: R + 26 });
    const walker: Walker = { a: stationAngle(0), x: 0, z: 0.4, walk: 0, yaw: 0 };

    this.ctx = {
      engine: this.engine, world, labels, reza, walker, cam, tl: this.tl, overlay, cues: this.cues,
      onFrame: (fn) => this.frameFns.push(fn),
      sfx: (name, at, gain = 1) => this.cues.push({ name, at: Math.max(0, at), gain }),
      kt: (html, cls) => {
        const el = document.createElement("div");
        el.className = `kt ${cls}`;
        el.innerHTML = html;
        overlay.append(el);
        gsap.set(el, { autoAlpha: 0 });
        return el;
      },
      hideAt: (t, ...els) => els.forEach((el) => this.tl.to(el, { autoAlpha: 0, y: -8, duration: 0.45, ease: "power1.in" }, t)),
      showAt: (t, el, o = {}) =>
        this.tl.fromTo(el, { autoAlpha: 0, y: 14, scale: 0.92 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.6, ease: "back.out(1.6)", immediateRender: false, ...o }, t),
    };

    // صحنهٔ «قلاب» ایستگاه ندارد؛ بقیه به ترتیب روی ایستگاه‌های حلقه‌اند
    let station = 0;
    timing.scenes.forEach((s) => {
      const build = SCENES[s.id];
      if (!build) throw new Error(`صحنهٔ ${s.id} تعریف نشده`);
      if (s.id === "hook") return build(this.ctx, new Cue(s), -1);
      const k = station++;
      // پیشرفت حلقه: با شروع هر صحنه، مسیر تا ایستگاه آن پر می‌شود
      const start = k === 0 ? s.t0 - 0.4 : s.t0 - LEAD;
      this.tl.to(world.ringUniforms.uP, { value: (k + 0.02) / 8 + 0.0001, duration: k === 0 ? 0.8 : MOVE, ease: "power1.inOut" }, start);
      build(this.ctx, new Cue(s), k);
    });

    for (const p of panels) p.refresh(true);

    // اشیای صحنه‌ها و شخصیت‌ها سایهٔ واقعی می‌اندازند (به‌جز اجزای شفاف مثل موج‌ها و پرتوها)
    const shadowable = (o: THREE.Object3D) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh) return;
      const mats = Array.isArray(m.material) ? m.material : [m.material];
      if (mats.some((x) => x.transparent)) return;
      m.castShadow = true;
    };
    world.stations.forEach((st) => st.traverse(shadowable));
    reza.root.traverse(shadowable);
  }

  /** زمان تایم‌لاین را تنظیم می‌کند (منبع زمان: صدای راوی) */
  seek(t: number) {
    this.tl.time(Math.min(Math.max(0, t), this.duration), false);
  }

  /** یک فریم: اعمال وضعیت‌ها، دوربین، برچسب‌ها و رندر */
  frame(time: number) {
    const { cam, walker, reza, world, labels } = this.ctx;
    const camera = this.engine.camera;

    // دوربین + لرزش ملایم دستی
    camera.position.set(Math.cos(cam.a) * cam.r, cam.y, Math.sin(cam.a) * cam.r);
    camera.position.x += Math.sin(time * 0.37) * 0.05;
    camera.position.y += Math.sin(time * 0.53) * 0.04;
    this.look.set(Math.cos(cam.ta) * cam.tr, cam.ty, Math.sin(cam.ta) * cam.tr);
    camera.lookAt(this.look);
    // سایه‌ها همراه نقطهٔ توجه دوربین (قاب سایهٔ تنگ = سایهٔ تیز)
    world.focus(this.look);
    // در قاب‌های باریک‌تر از ۱۶:۹ (موبایل) میدان دید باز می‌شود تا صحنه بریده نشود
    const aspect = this.engine.width / this.engine.height;
    const widen = aspect < 16 / 9 ? Math.pow(16 / 9 / aspect, 0.85) : 1;
    const fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(cam.fov) / 2) * widen));
    if (Math.abs(camera.fov - fov) > 0.001) {
      camera.fov = fov;
      camera.updateProjectionMatrix();
    }

    // رضا روی حلقه
    const f = frameAt(walker.a, this.tmp);
    reza.root.position.copy(f.pos).addScaledVector(f.right, walker.x).addScaledVector(f.out, walker.z);
    const faceCam = yawOut(walker.a);
    const faceWalk = Math.atan2(-Math.sin(walker.a), Math.cos(walker.a));
    let d = faceWalk - faceCam;
    d = Math.atan2(Math.sin(d), Math.cos(d));
    reza.root.rotation.y = faceCam + d * walker.walk + walker.yaw;
    reza.pose.walk = walker.walk;
    reza.pose.phase = (walker.a * R + walker.x) * 3.1;

    for (const fn of this.frameFns) fn(time);
    reza.update(time);
    world.update(time);
    for (const p of panels) p.refresh();

    this.engine.render();
    labels.update(this.engine.width, this.engine.height);
  }

  resize(w: number, h: number) {
    this.engine.resize(w, h);
  }

  dispose() {
    this.tl.kill();
    this.engine.dispose();
  }
}
