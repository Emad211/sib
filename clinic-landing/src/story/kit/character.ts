import * as THREE from "three";
import { C, blobShadow } from "./kit";

export type Kind = "reza" | "nurse";

// ---------- متریال‌ها: پوست نرم، پارچه با براقی ملایم (sheen) ----------
const skinMat = (color: number) =>
  new THREE.MeshPhysicalMaterial({ color, roughness: 0.55, sheen: 0.35, sheenRoughness: 0.6, sheenColor: new THREE.Color(0xffc9b0) });
const cloth = (color: number, rough = 0.85) =>
  new THREE.MeshPhysicalMaterial({ color, roughness: rough, sheen: 1, sheenRoughness: 0.75, sheenColor: new THREE.Color(color).lerp(new THREE.Color(0xffffff), 0.35) });
const plain = (color: number, rough = 0.5, metal = 0) => new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal });

/** پروفایل چرخشی (Lathe) از جفت‌های [شعاع، ارتفاع] با درون‌یابی نرم */
function lathe(profile: [number, number][], seg = 40, z = 1) {
  const curve = new THREE.SplineCurve(profile.map(([r, y]) => new THREE.Vector2(r, y)));
  const g = new THREE.LatheGeometry(curve.getPoints(48), seg);
  g.scale(1, 1, z);
  return g;
}
const capsule = (r: number, l: number) => new THREE.CapsuleGeometry(r, l, 10, 24);
const sphere = (r: number, w = 32, h = 24) => new THREE.SphereGeometry(r, w, h);

/**
 * شخصیت سه‌بعدی با جزئیات (چشم با پلک، بینی، گوش، دست با شست، لباس با پارچهٔ نرم) و اسکلت مفصلی ساده.
 * همهٔ حالت‌ها در `pose` هستند تا GSAP بتواند آن‌ها را انیمیت کند و جلو/عقب‌کردن تایم‌لاین دقیق بماند.
 * مقنعهٔ پرستار دو تکه است: کلاهک (فرزند سر) و شنل روی شانه (فرزند بدن)؛ با تکان سر، پارچه در بدن فرو نمی‌رود.
 */
export class Character {
  readonly root = new THREE.Group();
  readonly body = new THREE.Group();
  readonly head = new THREE.Group();
  readonly armL = new THREE.Group();
  readonly armR = new THREE.Group();
  readonly legL = new THREE.Group();
  readonly legR = new THREE.Group();
  /** دستِ راست — برای نگه‌داشتن گوشی و ... */
  readonly handR = new THREE.Group();
  private mouth: THREE.Mesh;
  private lids: THREE.Mesh[] = [];

  /** walk: شدت راه‌رفتن، phase: فاز گام، wave: دست تکان‌دادن، hold: بالا آوردن دست، happy: شادی، nod: سر تکان‌دادن */
  pose = { walk: 0, phase: 0, wave: 0, hold: 0, happy: 0, nod: 0, look: 0 };

  constructor(readonly kind: Kind) {
    const nurse = kind === "nurse";
    const skin = skinMat(nurse ? 0xe9bf9c : C.skin);
    const top = nurse ? cloth(0xfbfaf6, 0.7) : cloth(C.sweater);
    const pants = cloth(nurse ? 0x2c5d78 : C.trouser);
    const shoe = plain(nurse ? 0xf4f4f2 : 0x3a2c24, 0.45);
    const dark = plain(0x1d2422, 0.35);
    const add = (parent: THREE.Object3D, g: THREE.BufferGeometry, m: THREE.Material, x = 0, y = 0, z = 0) => {
      const o = new THREE.Mesh(g, m);
      o.position.set(x, y, z);
      o.castShadow = true;
      parent.add(o);
      return o;
    };

    this.root.add(blobShadow(0.6, 0.48, 0.35));
    this.root.add(this.body);

    // ---------- پاها و کفش ----------
    for (const [g, x] of [[this.legL, -0.16], [this.legR, 0.16]] as const) {
      g.position.set(x, 0.74, 0);
      const leg = add(g, lathe([[0.1, -0.66], [0.115, -0.5], [0.13, -0.25], [0.15, 0], [0.155, 0.06]], 24), pants);
      leg.scale.z = 0.95;
      const s = add(g, sphere(0.13), shoe, 0, -0.67, 0.07);
      s.scale.set(0.95, 0.55, 1.55);
      add(g, new THREE.BoxGeometry(0.22, 0.04, 0.4), plain(0x2a2420, 0.7), 0, -0.735, 0.07);
      this.body.add(g);
    }

    // ---------- بالاتنه ----------
    if (nurse) {
      // روپوش سفید بلند تا زانو
      add(this.body, lathe([[0.4, 0.42], [0.39, 0.62], [0.37, 0.82], [0.38, 1.05], [0.4, 1.3], [0.4, 1.48], [0.33, 1.6], [0.16, 1.7]], 48, 0.8), top);
      // لبه‌های جلوی روپوش + دکمه
      for (const x of [-0.035, 0.035]) add(this.body, new THREE.BoxGeometry(0.02, 1.05, 0.02), plain(0xe6e3dc, 0.6), x, 0.95, 0.315);
      for (const y of [0.78, 1.0, 1.22]) add(this.body, sphere(0.022, 12, 8), plain(0xd8d4cc, 0.4), 0.06, y, 0.325);
      // جیب و کارت شناسایی
      add(this.body, new THREE.BoxGeometry(0.16, 0.14, 0.02), plain(0xf0eee8, 0.7), -0.2, 1.16, 0.31);
      add(this.body, new THREE.BoxGeometry(0.15, 0.1, 0.012), plain(C.teal, 0.4), 0.2, 1.33, 0.315);
    } else {
      // پلیور با شکم کمی برآمده (۵۸ ساله)
      add(this.body, lathe([[0.36, 0.7], [0.4, 0.84], [0.445, 1.0], [0.44, 1.2], [0.42, 1.42], [0.36, 1.57], [0.17, 1.68]], 48, 0.8), top);
      // کمربند و سگک
      const belt = add(this.body, new THREE.TorusGeometry(0.365, 0.03, 10, 48), plain(0x3b2a20, 0.4), 0, 0.73, 0);
      belt.rotation.x = Math.PI / 2;
      belt.scale.set(1, 0.8, 1);
      add(this.body, new THREE.BoxGeometry(0.08, 0.06, 0.02), plain(0xc9a45c, 0.25, 0.8), 0, 0.73, 0.3);
      // یقهٔ پیراهن سفید از زیر پلیور (حلقهٔ دور گردن + دو لبهٔ یقه)
      const collar = add(this.body, new THREE.CylinderGeometry(0.135, 0.15, 0.08, 28, 1, true), plain(0xf7f6f2, 0.6), 0, 1.66, 0.01);
      collar.scale.z = 0.92;
      for (const sd of [-1, 1]) {
        const tip = add(this.body, new THREE.BoxGeometry(0.11, 0.012, 0.075), plain(0xf7f6f2, 0.6), sd * 0.065, 1.635, 0.135);
        tip.rotation.set(0.5, sd * 0.35, sd * -0.45);
      }
      // نوار بافت پایین پلیور
      const rib = add(this.body, new THREE.TorusGeometry(0.39, 0.035, 10, 48), cloth(0x277e6c), 0, 0.8, 0);
      rib.rotation.x = Math.PI / 2;
      rib.scale.set(1, 0.8, 1);
    }
    // گردن
    add(this.body, new THREE.CylinderGeometry(0.1, 0.12, 0.22, 20), skin, 0, 1.73, 0);

    // ---------- دست‌ها (محور چرخش روی شانه) ----------
    for (const [g, x] of [[this.armL, -0.46], [this.armR, 0.46]] as const) {
      g.position.set(x, 1.48, 0);
      add(g, capsule(0.105, 0.28), top, 0, -0.2, 0);
      add(g, capsule(0.09, 0.26), top, 0, -0.5, 0.02);
      const cuff = add(g, new THREE.TorusGeometry(0.088, 0.02, 8, 24), nurse ? plain(0xe6e3dc, 0.6) : cloth(0x277e6c), 0, -0.64, 0.02);
      cuff.rotation.x = Math.PI / 2;
      const palm = add(g, sphere(0.085, 24, 16), skin, 0, -0.72, 0.03);
      palm.scale.set(0.85, 1.15, 0.6);
      const thumb = add(g, capsule(0.027, 0.06), skin, x < 0 ? 0.05 : -0.05, -0.69, 0.07);
      thumb.rotation.z = x < 0 ? -0.6 : 0.6;
      g.rotation.z = x < 0 ? -0.1 : 0.1;
      this.body.add(g);
    }
    this.handR.position.set(0, -0.74, 0.05);
    this.armR.add(this.handR);

    // ---------- سر ----------
    this.head.position.set(0, 1.94, 0);
    this.body.add(this.head);
    const skull = add(this.head, sphere(0.32, 48, 36), skin);
    skull.scale.set(0.98, 1.08, 1);
    const jaw = add(this.head, sphere(0.25, 40, 28), skin, 0, -0.12, 0.05);
    jaw.scale.set(1, 0.85, 0.95);
    const nose = add(this.head, sphere(0.055, 24, 16), skinMat(nurse ? 0xe3b18c : C.skinShade), 0, -0.02, 0.335);
    nose.scale.set(0.8, 1.15, 1.05);
    // گوش‌ها
    for (const s of [-1, 1]) {
      const ear = add(this.head, sphere(0.07, 20, 14), skin, s * 0.31, -0.01, -0.01);
      ear.scale.set(0.45, 1, 0.8);
    }
    // چشم: سفیدی، عنبیه، مردمک، برق و پلک (برای پلک‌زدن)
    const white = plain(0xfbfaf7, 0.3), iris = plain(nurse ? 0x3d2b1f : 0x4a3424, 0.35), shine = new THREE.MeshBasicMaterial({ color: 0xffffff });
    for (const x of [-0.11, 0.11]) {
      const eye = new THREE.Group();
      eye.position.set(x, 0.045, 0.27);
      this.head.add(eye);
      add(eye, sphere(0.05, 24, 18), white);
      add(eye, sphere(0.03, 20, 14), iris, 0, 0, 0.032);
      add(eye, sphere(0.016, 14, 10), dark, 0, 0, 0.048);
      add(eye, sphere(0.007, 8, 6), shine, 0.01, 0.012, 0.058);
      const lid = add(eye, new THREE.SphereGeometry(0.056, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), skin);
      lid.rotation.x = -0.35;
      this.lids.push(lid);
    }
    // ابرو
    for (const x of [-0.115, 0.115]) {
      const brow = add(this.head, capsule(0.018, 0.08), plain(nurse ? 0x2b1d16 : 0x8d8a84, 0.9), x, 0.14, 0.29);
      brow.rotation.set(0.2, 0, Math.PI / 2 + (x < 0 ? -0.12 : 0.12));
    }
    // دهان
    this.mouth = add(this.head, new THREE.TorusGeometry(0.06, 0.013, 8, 24, Math.PI), plain(nurse ? 0xa8574a : 0x7a3e34, 0.5), 0, -0.135, 0.29);
    this.mouth.rotation.z = Math.PI;

    if (nurse) this.hijab(add);
    else this.rezaHead(add);

    this.body.traverse((o) => ((o as THREE.Mesh).receiveShadow = false));
  }

  /** موی جوگندمیِ کم‌پشت، سبیل و عینک آقا رضا */
  private rezaHead(add: (p: THREE.Object3D, g: THREE.BufferGeometry, m: THREE.Material, x?: number, y?: number, z?: number) => THREE.Mesh) {
    const hair = cloth(0x9d9a94, 0.95);
    // دور سر و پشت سر (وسط سر کم‌پشت است)
    const ring = add(this.head, new THREE.SphereGeometry(0.335, 48, 24, Math.PI * 0.62, Math.PI * 1.76, Math.PI * 0.18, Math.PI * 0.42), hair, 0, 0.01, -0.01);
    ring.scale.set(1, 1.08, 1.02);
    const top = add(this.head, new THREE.SphereGeometry(0.33, 40, 16, 0, Math.PI * 2, 0, Math.PI * 0.2), hair, 0, 0.02, -0.03);
    top.scale.set(0.95, 1.05, 0.95);
    // خط شقیقه‌ها
    for (const s of [-1, 1]) {
      const side = add(this.head, sphere(0.09, 20, 14), hair, s * 0.27, 0.06, 0.05);
      side.scale.set(0.5, 1.3, 1);
    }
    // سبیل پرپشت جوگندمی
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-0.12, -0.115, 0.25), new THREE.Vector3(-0.06, -0.075, 0.31), new THREE.Vector3(0, -0.068, 0.322),
      new THREE.Vector3(0.06, -0.075, 0.31), new THREE.Vector3(0.12, -0.115, 0.25),
    ]);
    add(this.head, new THREE.TubeGeometry(curve, 32, 0.03, 12), cloth(0x8a8780, 0.95));
    // عینک
    const frame = plain(0x6b5a3e, 0.3, 0.7);
    for (const x of [-0.115, 0.115]) {
      const lens = add(this.head, new THREE.TorusGeometry(0.068, 0.009, 8, 32), frame, x, 0.04, 0.325);
      lens.scale.set(1.15, 0.85, 1);
      const glass = add(this.head, new THREE.CircleGeometry(0.068, 32), new THREE.MeshPhysicalMaterial({ color: 0xffffff, transmission: 0.9, roughness: 0.05, transparent: true, opacity: 0.18 }), x, 0.04, 0.322);
      glass.scale.set(1.15, 0.85, 1);
      const temple = add(this.head, new THREE.BoxGeometry(0.008, 0.008, 0.3), frame, x < 0 ? -0.3 : 0.3, 0.05, 0.17);
      temple.rotation.y = x < 0 ? 0.12 : -0.12;
    }
    const bridge = add(this.head, new THREE.TorusGeometry(0.03, 0.007, 6, 12, Math.PI), frame, 0, 0.045, 0.33);
    bridge.rotation.z = 0;
  }

  /** مقنعهٔ سرمه‌ای: کلاهک و دورصورت (فرزند سر) + شنل روی شانه (فرزند بدن) */
  private hijab(add: (p: THREE.Object3D, g: THREE.BufferGeometry, m: THREE.Material, x?: number, y?: number, z?: number) => THREE.Mesh) {
    const fabric = cloth(0x24405e, 0.9);
    fabric.side = THREE.DoubleSide;
    // کلاهک: کل بالای سر تا پیشانی
    const cap = add(this.head, new THREE.SphereGeometry(0.365, 48, 20, 0, Math.PI * 2, 0, Math.PI * 0.34), fabric, 0, 0.02, -0.01);
    cap.scale.set(1, 1.07, 1.03);
    // دور صورت: کناره‌ها و پشت سر، جلو برای صورت باز است؛ لبهٔ پایین بالاتر از شانه
    const wrap = add(this.head, new THREE.SphereGeometry(0.372, 48, 24, Math.PI / 2 + 0.98, Math.PI * 2 - 1.96, Math.PI * 0.33, Math.PI * 0.5), fabric, 0, 0.02, -0.012);
    wrap.scale.set(1, 1.07, 1.03);
    // زیر چانه
    const chin = add(this.head, new THREE.TorusGeometry(0.235, 0.075, 14, 40, Math.PI), fabric, 0, -0.2, 0.02);
    chin.rotation.set(-0.15, 0, Math.PI);
    chin.scale.set(1.08, 0.7, 1.05);
    // لبهٔ تزئینی دور صورت
    const trim = add(this.head, new THREE.TorusGeometry(0.255, 0.014, 8, 48, Math.PI * 1.25), plain(0x1b3149, 0.8), 0, 0.02, 0.24);
    trim.rotation.z = -Math.PI * 0.125 + Math.PI / 2;
    trim.scale.set(0.95, 1.18, 1);
    // شنل: از گردن تا روی سینه و شانه — فرزند بدن تا با تکان سر، در بدن فرو نرود
    const cape = add(this.body, lathe([[0.14, 1.83], [0.19, 1.74], [0.3, 1.64], [0.45, 1.52], [0.47, 1.4]], 48, 0.82), fabric);
    cape.scale.set(1, 1, 1);
    // گوشی پزشکی روی گردن
    const steth = add(this.body, new THREE.TorusGeometry(0.24, 0.016, 8, 40, Math.PI * 1.15), plain(0x2f3b45, 0.35), 0, 1.36, 0.25);
    steth.rotation.set(0.35, 0, Math.PI * 0.93);
    add(this.body, new THREE.CylinderGeometry(0.045, 0.045, 0.02, 20), plain(0xb7c6c3, 0.25, 0.8), 0.13, 1.12, 0.32).rotation.x = Math.PI / 2;
  }

  /** هر فریم: تبدیل pose به چرخش مفصل‌ها. time = زمان واقعی برای حرکت‌های محیطی (نفس‌کشیدن، پلک) */
  update(time: number) {
    const p = this.pose;
    const s = Math.sin(p.phase);
    const w = p.walk;

    this.legL.rotation.x = s * 0.6 * w;
    this.legR.rotation.x = -s * 0.6 * w;

    // دست چپ: تاب‌خوردن هنگام راه‌رفتن
    this.armL.rotation.x = -s * 0.5 * w;
    this.armL.rotation.z = -0.1 - p.happy * 2.2;

    // دست راست: راه‌رفتن + دست‌تکان + نگه‌داشتن
    const wave = p.wave;
    this.armR.rotation.x = s * 0.5 * w * (1 - p.hold) - p.hold * 1.15;
    this.armR.rotation.z = 0.1 + wave * 2.5 + Math.sin(time * 9) * 0.28 * wave + p.happy * 2.2 + p.hold * 0.25;

    const bob = Math.abs(Math.sin(p.phase)) * 0.06 * w;
    const hop = Math.max(0, Math.sin(time * 7)) * 0.12 * p.happy;
    const breathe = Math.sin(time * 2.1) * 0.006;
    this.body.position.y = bob + hop + breathe;
    this.body.rotation.z = Math.sin(p.phase) * 0.035 * w;

    this.head.rotation.x = Math.sin(time * 1.3) * 0.025 + p.nod * Math.sin(time * 8) * 0.16;
    this.head.rotation.y = p.look + Math.sin(time * 0.6) * 0.05;
    this.head.rotation.z = Math.sin(time * 0.9) * 0.03;

    // پلک‌زدن: پلک بالا پایین می‌آید
    const blink = (time % 3.7) < 0.13 ? 1 : 0;
    for (const l of this.lids) l.rotation.x = blink ? 0.75 : -0.35;
    this.mouth.scale.set(1 + p.happy * 0.35, 1 + p.happy * 0.8, 1);
  }
}
