import * as THREE from "three";

/**
 * هندسهٔ فشردهٔ لوگوی سیب (ساخته‌شده با scripts/bake-logo.mjs):
 * سرآیند JSON + مختصات ۱۶ بیتی + نرمال ۸ بیتی + ایندکس. خروجی: [بدنهٔ سبز، بخش روشن].
 * مختصات به واحد SVG است (حدود ۱۲۵۰×۱۹۴۰) و محور y رو به پایین؛ هنگام استفاده با scale(s, -s, s) برگردانید.
 */
let cache: Promise<THREE.BufferGeometry[]> | null = null;

export function loadLogo(url = `${import.meta.env.BASE_URL}hero-logo.bin`) {
  return (cache ??= fetch(url)
    .then((r) => r.arrayBuffer())
    .then(parse));
}

function parse(buf: ArrayBuffer) {
  const hl = new DataView(buf).getUint32(0, true);
  const header: { count: number; indices: number; wide: boolean; min: number[]; max: number[] }[] = JSON.parse(
    new TextDecoder().decode(new Uint8Array(buf, 4, hl))
  );
  const pad4 = (x: number) => (x + 3) & ~3;
  let o = 4 + pad4(hl);
  return header.map((h) => {
    const q = new Int16Array(buf, o, h.count * 3);
    o += pad4(q.byteLength);
    const n = new Int8Array(buf, o, h.count * 3);
    o += pad4(n.byteLength);
    const I = h.wide ? new Uint32Array(buf, o, h.indices) : new Uint16Array(buf, o, h.indices);
    o += pad4(I.byteLength);
    // مختصات ۱۶ بیتی (۳۲۷۶۷- تا ۳۲۷۶۷) به جعبهٔ اصلی برمی‌گردد
    const pos = new Float32Array(h.count * 3);
    for (let i = 0; i < pos.length; i++) {
      const a = i % 3, lo = h.min[a], hi = h.max[a];
      pos[i] = (lo + hi) / 2 + (q[i] / 32767) * ((hi - lo) / 2);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("normal", new THREE.BufferAttribute(new Int8Array(n), 3, true));
    g.setIndex(new THREE.BufferAttribute(I.slice(), 1));
    return g;
  });
}
