// لوگوی سه‌بعدی هیرو را یک بار (در زمان توسعه) از logo3d.svg می‌سازد و هندسهٔ فشرده را در public/hero-logo.bin می‌نویسد.
// مرورگر دیگر SVG را پارس و اکسترود نمی‌کند؛ فقط چند صد کیلوبایت عدد را مستقیم به GPU می‌دهد.
// اجرا: node scripts/bake-logo.mjs
import { readFileSync, writeFileSync } from "node:fs";
import * as THREE from "three";
import { SVGLoader } from "three/examples/jsm/loaders/SVGLoader.js";
import { mergeGeometries, mergeVertices } from "three/examples/jsm/utils/BufferGeometryUtils.js";

const svg = readFileSync("public/logo3d.svg", "utf8");
const C0 = { x: 895, y: 1200 }; // مرکز لوگو در مختصات SVG
const MIN_AREA = 900; // تکه‌های ریزِ حاصل از ردیابی تصویر حذف می‌شوند

function shapePath(d, tx, ty) {
  const sp = new THREE.ShapePath();
  sp.userData = { style: { fillRule: "nonzero" } };
  const nums = (s) => s.trim().split(/[\s,]+/).filter(Boolean).map(Number);
  for (const [, cmd, rest] of d.matchAll(/([MCZ])([^MCZ]*)/g)) {
    const n = nums(rest);
    if (cmd === "M") sp.moveTo(n[0] + tx, n[1] + ty);
    else if (cmd === "C") for (let i = 0; i + 5 < n.length; i += 6) sp.bezierCurveTo(n[i] + tx, n[i + 1] + ty, n[i + 2] + tx, n[i + 3] + ty, n[i + 4] + tx, n[i + 5] + ty);
    else if (cmd === "Z") sp.currentPath?.closePath?.();
  }
  return sp;
}

/** ساده‌سازی خط دور (Douglas–Peucker): مسیر ردیابی‌شده بسیار پرنقطه است */
function rdp(pts, eps) {
  if (pts.length < 4) return pts;
  const a = pts[0], b = pts[pts.length - 1];
  let idx = -1, max = 0;
  const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
  for (let i = 1; i < pts.length - 1; i++) {
    const d = Math.abs(dy * pts[i].x - dx * pts[i].y + b.x * a.y - b.y * a.x) / len;
    if (d > max) (max = d), (idx = i);
  }
  if (max <= eps) return [a, b];
  return [...rdp(pts.slice(0, idx + 1), eps).slice(0, -1), ...rdp(pts.slice(idx), eps)];
}
const simplify = (path, eps) => {
  const p = path.getPoints(6);
  // حلقهٔ بسته: از دو نیمه ساده می‌کنیم تا نقطهٔ شروع حذف نشود
  const h = p.length >> 1;
  return [...rdp(p.slice(0, h + 1), eps).slice(0, -1), ...rdp(p.slice(h), eps)];
};
const EPS = 1.6;
console.warn = () => {}; // هشدار منسوخ‌شدن createShapes

const dark = [], light = [];
let dropped = 0;
for (const m of svg.matchAll(/<path d="([^"]+)" transform="translate\(([-\d.]+),([-\d.]+)\)"( fill="#FFFFFF")?/g)) {
  const isLight = !!m[4];
  for (const sh of SVGLoader.createShapes(shapePath(m[1], +m[2], +m[3]))) {
    const area = Math.abs(THREE.ShapeUtils.area(sh.getPoints(8)));
    if (!isLight && area < MIN_AREA) { dropped++; continue; }
    const simple = new THREE.Shape(simplify(sh, EPS));
    simple.holes = sh.holes.map((hp) => new THREE.Path(simplify(hp, EPS)));
    const g = new THREE.ExtrudeGeometry(simple, isLight
      ? { depth: 4, bevelEnabled: false }
      : { depth: 90, bevelEnabled: true, bevelThickness: 12, bevelSize: 9, bevelSegments: 3 });
    g.deleteAttribute("uv");
    g.translate(-C0.x, -C0.y, isLight ? 106 : 0);
    (isLight ? light : dark).push(g);
  }
}

/** هندسه را ایندکس‌دار و با دقت ۱۶ بیتی ذخیره می‌کند */
function pack(geos) {
  const g = mergeVertices(mergeGeometries(geos, false), 0.5);
  g.computeBoundingBox();
  const pos = g.attributes.position, nor = g.attributes.normal, idx = g.index;
  const b = g.boundingBox;
  const q = new Int16Array(pos.count * 3), n = new Int8Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++)
    for (let a = 0; a < 3; a++) {
      const lo = b.min.getComponent(a), hi = b.max.getComponent(a);
      q[i * 3 + a] = Math.round(((pos.array[i * 3 + a] - lo) / (hi - lo || 1)) * 65534 - 32767);
      n[i * 3 + a] = Math.round(nor.array[i * 3 + a] * 127);
    }
  const I = pos.count > 65535 ? new Uint32Array(idx.array) : new Uint16Array(idx.array);
  return { count: pos.count, indices: I.length, wide: I instanceof Uint32Array, min: b.min.toArray(), max: b.max.toArray(), q, n, I };
}

const parts = [pack(dark), pack(light)];
// قالب فایل: سرآیند JSON (طول ۴ بایت) + بلوک‌های هم‌تراز
const header = parts.map(({ count, indices, wide, min, max }) => ({ count, indices, wide, min, max }));
const hj = Buffer.from(JSON.stringify(header));
const pad4 = (x) => (x + 3) & ~3;
const blocks = [];
for (const p of parts) blocks.push(p.q, p.n, p.I);
let size = 4 + pad4(hj.length);
for (const b of blocks) size += pad4(b.byteLength);
const out = Buffer.alloc(size);
out.writeUInt32LE(hj.length, 0);
hj.copy(out, 4);
let o = 4 + pad4(hj.length);
for (const b of blocks) {
  Buffer.from(b.buffer, b.byteOffset, b.byteLength).copy(out, o);
  o += pad4(b.byteLength);
}
writeFileSync("public/hero-logo.bin", out);
console.log(`dropped ${dropped} tiny fragments · dark ${parts[0].count} verts / ${parts[0].indices / 3} tris · light ${parts[1].count} verts · ${(size / 1024).toFixed(0)} KB`);
