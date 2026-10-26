import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";

/** پالت رنگ — هم‌خانواده با سایت حلقه */
export const C = {
  bg: 0xf1ebdf,
  floor: 0xf4efe5,
  platform: 0xe9e0cc,
  ink: 0x0b2a1c,
  forest: 0x08331f,
  pine: 0x0f5a38,
  teal: 0x1b9157,
  mint: 0x7fe3a6,
  mintSoft: 0xc9efe6,
  amber: 0xe6ab3c,
  red: 0xd0573d,
  white: 0xfffdf8,
  paper: 0xf7f3ea,
  skin: 0xf0c7a2,
  skinShade: 0xdca783,
  hairGray: 0x8f8c86,
  hairDark: 0x3b3330,
  navy: 0x24405e,
  trouser: 0x3c4656,
  shoe: 0x2a2e35,
  sweater: 0x2f8f7c,
  wood: 0xc9965a,
  leaf: 0x93cdb0,
  leaf2: 0x6db494,
  trunk: 0xa47c58,
} as const;

export const CSS = {
  ink: "#0b2a1c",
  forest: "#08331f",
  pine: "#0f5a38",
  teal: "#1b9157",
  mint: "#7fe3a6",
  mintSoft: "#e0f4e6",
  amber: "#e6ab3c",
  amberSoft: "#fbefd4",
  red: "#d0573d",
  redSoft: "#f8e1da",
  muted: "#4d6a58",
  paper: "#f7f3ea",
  white: "#fffdf8",
};

// ---------- متریال‌ها (کش‌شده؛ هر رنگ یک متریال) ----------
const matCache = new Map<string, THREE.MeshStandardMaterial>();
export function mat(
  color: number,
  o: { rough?: number; metal?: number; emissive?: number; ei?: number; opacity?: number; side?: THREE.Side } = {}
) {
  const key = JSON.stringify([color, o]);
  let m = matCache.get(key);
  if (!m) {
    m = new THREE.MeshStandardMaterial({
      color,
      roughness: o.rough ?? 0.62,
      metalness: o.metal ?? 0,
      emissive: o.emissive ?? 0x000000,
      emissiveIntensity: o.ei ?? 1,
      transparent: o.opacity !== undefined,
      opacity: o.opacity ?? 1,
      side: o.side ?? THREE.FrontSide,
    });
    matCache.set(key, m);
  }
  return m;
}

// ---------- هندسه‌ها (کش‌شده) ----------
const geoCache = new Map<string, THREE.BufferGeometry>();
function cached<T extends THREE.BufferGeometry>(key: string, make: () => T): T {
  let g = geoCache.get(key) as T | undefined;
  if (!g) {
    g = make();
    geoCache.set(key, g);
  }
  return g;
}

export const geo = {
  rbox: (w: number, h: number, d: number, r = 0.08, seg = 3) =>
    cached(`rb${w},${h},${d},${r},${seg}`, () => new RoundedBoxGeometry(w, h, d, seg, Math.min(r, w / 2, h / 2, d / 2))),
  sphere: (r: number, ws = 32, hs = 20) => cached(`sp${r},${ws}`, () => new THREE.SphereGeometry(r, ws, hs)),
  capsule: (r: number, l: number) => cached(`cp${r},${l}`, () => new THREE.CapsuleGeometry(r, l, 8, 20)),
  cyl: (rt: number, rb: number, h: number, s = 40) => cached(`cy${rt},${rb},${h},${s}`, () => new THREE.CylinderGeometry(rt, rb, h, s)),
  torus: (r: number, t: number, arc = Math.PI * 2) => cached(`to${r},${t},${arc}`, () => new THREE.TorusGeometry(r, t, 12, 64, arc)),
  plane: (w: number, h: number) => cached(`pl${w},${h}`, () => new THREE.PlaneGeometry(w, h)),

  /** قطرهٔ خون (Lathe) */
  drop: () =>
    cached("drop", () => {
      const pts: THREE.Vector2[] = [];
      for (let i = 0; i <= 32; i++) {
        const t = Math.PI - (i / 32) * Math.PI;
        pts.push(new THREE.Vector2(Math.sin(t) * Math.sin(t / 2) * 0.42, Math.cos(t) * 0.5));
      }
      return new THREE.LatheGeometry(pts, 40);
    }),

  heart: () =>
    cached("heart", () => {
      const s = new THREE.Shape();
      s.moveTo(0, -0.45);
      s.bezierCurveTo(-0.05, -0.38, -0.5, -0.12, -0.5, 0.15);
      s.bezierCurveTo(-0.5, 0.38, -0.32, 0.5, -0.2, 0.5);
      s.bezierCurveTo(-0.08, 0.5, -0.02, 0.42, 0, 0.32);
      s.bezierCurveTo(0.02, 0.42, 0.08, 0.5, 0.2, 0.5);
      s.bezierCurveTo(0.32, 0.5, 0.5, 0.38, 0.5, 0.15);
      s.bezierCurveTo(0.5, -0.12, 0.05, -0.38, 0, -0.45);
      const g = new THREE.ExtrudeGeometry(s, { depth: 0.16, bevelEnabled: true, bevelThickness: 0.06, bevelSize: 0.05, bevelSegments: 5, curveSegments: 24 });
      g.center();
      return g;
    }),

  cross: () =>
    cached("cross", () => {
      const a = 0.17, b = 0.5;
      const s = new THREE.Shape();
      s.moveTo(-a, b); s.lineTo(a, b); s.lineTo(a, a); s.lineTo(b, a); s.lineTo(b, -a); s.lineTo(a, -a);
      s.lineTo(a, -b); s.lineTo(-a, -b); s.lineTo(-a, -a); s.lineTo(-b, -a); s.lineTo(-b, a); s.lineTo(-a, a);
      s.closePath();
      const g = new THREE.ExtrudeGeometry(s, { depth: 0.14, bevelEnabled: true, bevelThickness: 0.05, bevelSize: 0.045, bevelSegments: 4 });
      g.center();
      return g;
    }),

  bell: () =>
    cached("bell", () => {
      const p = [
        [0, 0.46], [0.07, 0.45], [0.09, 0.38], [0.2, 0.31], [0.27, 0.12], [0.3, -0.12],
        [0.38, -0.26], [0.43, -0.32], [0.42, -0.36], [0, -0.36],
      ].map(([x, y]) => new THREE.Vector2(x, y));
      return new THREE.LatheGeometry(p, 40);
    }),
};

// ---------- سایهٔ نرم زیر اجسام (به‌جای shadow map — سبک و زیبا) ----------
let blobTex: THREE.Texture | null = null;
function blobTexture() {
  if (blobTex) return blobTex;
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, "rgba(20,40,38,0.55)");
  grd.addColorStop(0.45, "rgba(20,40,38,0.25)");
  grd.addColorStop(1, "rgba(20,40,38,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  blobTex = new THREE.CanvasTexture(c);
  return blobTex;
}
export function blobShadow(rx: number, rz = rx, opacity = 0.5) {
  const m = new THREE.Mesh(
    geo.plane(1, 1),
    new THREE.MeshBasicMaterial({ map: blobTexture(), transparent: true, depthWrite: false, opacity })
  );
  m.rotation.x = -Math.PI / 2;
  m.scale.set(rx * 2, rz * 2, 1);
  m.position.y = 0.012;
  m.renderOrder = -1;
  return m;
}

export function mesh(g: THREE.BufferGeometry, m: THREE.Material, x = 0, y = 0, z = 0) {
  const o = new THREE.Mesh(g, m);
  o.position.set(x, y, z);
  return o;
}

// ---------- پنل بوم: متن فارسی واقعی روی سطوح سه‌بعدی ----------
export const FONT = "Vazirmatn, Tahoma, sans-serif";
export type PanelState = Record<string, number>;

/**
 * یک canvas که به‌عنوان تکسچر روی اجسام می‌نشیند. مقدارهای state با GSAP انیمیت می‌شوند
 * و هر وقت تغییر کنند، پنل دوباره کشیده می‌شود.
 */
export class Panel<S extends PanelState = PanelState> {
  readonly canvas = document.createElement("canvas");
  readonly ctx: CanvasRenderingContext2D;
  readonly texture: THREE.CanvasTexture;
  private last = "";

  constructor(
    readonly w: number,
    readonly h: number,
    readonly state: S,
    private painter: (g: CanvasRenderingContext2D, w: number, h: number, s: S) => void
  ) {
    this.canvas.width = w;
    this.canvas.height = h;
    this.ctx = this.canvas.getContext("2d")!;
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.texture.anisotropy = 4;
    panels.add(this as unknown as Panel);
  }

  /** فقط وقتی state عوض شده باشد دوباره می‌کشد */
  refresh(force = false) {
    // GSAP روی شیء هدف یک کلید داخلی (_gsap) می‌گذارد؛ فقط عددها را می‌سنجیم
    let key = "";
    for (const v of Object.values(this.state)) if (typeof v === "number") key += v.toFixed(3) + ",";
    if (!force && key === this.last) return;
    this.last = key;
    const g = this.ctx;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, this.w, this.h);
    g.direction = "rtl";
    this.painter(g, this.w, this.h, this.state);
    this.texture.needsUpdate = true;
  }
}
export const panels = new Set<Panel>();

/** مستطیل گوشه‌گرد روی canvas */
export function rr(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  g.beginPath();
  g.roundRect(x, y, w, h, r);
}

export const fa = (s: string | number) => String(s).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[+d]);

/** عدد تصادفی قابل تکرار برای پخش‌کردن درخت‌ها و ... */
export function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
