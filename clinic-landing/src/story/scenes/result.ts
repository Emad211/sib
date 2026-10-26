import * as THREE from "three";
import { C, CSS, FONT, Panel, fa, geo, mat, mesh } from "../kit/kit";
import { drawTube } from "../kit/props";
import { LEAD, travel, type SceneBuilder } from "../rig";

/** ۷ — نتیجه: منحنی که به محدودهٔ هدف می‌نشیند، عددهای HbA1c و فشار، شادی رضا */
const result: SceneBuilder = (ctx, T, k) => {
  const { tl, labels, reza } = ctx;
  const st = ctx.world.stations[k];
  travel(ctx, k, T.t0 - LEAD, { x: -2.6, z: 0.85, yaw: 0.5 }, { ty: 1.55, r: 13 + 11 });

  const chart = new THREE.Group();
  chart.position.set(0.85, 0, -0.4);
  st.add(chart);
  chart.add(mesh(geo.rbox(3.5, 2.5, 0.12, 0.14), mat(C.white, { rough: 0.45 }), 0, 1.85, 0));
  for (const x of [-1.25, 1.25]) chart.add(mesh(geo.cyl(0.05, 0.07, 0.6, 12), mat(C.pine), x, 0.3, 0));
  const face = new Panel(700, 500, {}, (g, w, h) => {
    g.strokeStyle = "rgba(10,40,38,.08)";
    g.lineWidth = 2;
    for (let i = 1; i < 5; i++) {
      g.beginPath();
      g.moveTo(40, i * 90);
      g.lineTo(w - 40, i * 90);
      g.stroke();
    }
    g.fillStyle = CSS.muted;
    g.font = `500 26px ${FONT}`;
    g.textAlign = "right";
    g.fillText("ماه اول", w - 40, h - 22);
    g.textAlign = "left";
    g.fillText("ماه ششم", 40, h - 22);
    g.fillStyle = CSS.teal;
    g.font = `700 26px ${FONT}`;
    g.fillText("محدودهٔ هدف", 44, 292);
  });
  const fp = new THREE.Mesh(geo.plane(3.3, 2.36), new THREE.MeshBasicMaterial({ map: face.texture, transparent: true, toneMapped: false }));
  fp.position.set(0, 1.85, 0.065);
  chart.add(fp);

  // نوار محدودهٔ هدف
  const bandMat = new THREE.MeshStandardMaterial({ color: C.mint, transparent: true, opacity: 0.32, roughness: 0.4, emissive: C.mint, emissiveIntensity: 0 });
  const band = new THREE.Mesh(geo.rbox(3.2, 0.52, 0.04, 0.02), bandMat);
  band.position.set(0, 1.3, 0.08);
  chart.add(band);
  tl.to(bandMat, { opacity: 0.55, emissiveIntensity: 0.35, duration: 0.5, yoyo: true, repeat: 1 }, T.at("محدودهٔ"));

  // منحنی: از راست بالا (ماه اول) تا چپ پایین داخل محدوده
  const ys = [2.75, 2.55, 2.45, 2.05, 1.85, 1.5, 1.38, 1.28, 1.3];
  const pts = ys.map((y, i) => new THREE.Vector3(1.4 - i * 0.35, y, 0.14));
  const line = drawTube(pts, 0.055, C.teal, { emissive: true });
  chart.add(line.group);
  tl.fromTo(line.state, { p: 0 }, { p: 1, duration: 2.6, ease: "power1.inOut", onUpdate: line.apply, immediateRender: false }, T.at("شش") - 0.2);

  // کارت عددها با شمارندهٔ متحرک
  const a1c = labels.add(`<small>HbA1c</small><b data-v>۹٫۱</b><i>٪</i>`, chart, { offset: [0.95, 3.3, 0], cls: "l-stat" });
  const bp = labels.add(`<small>فشار خون</small><b data-v>۱۶۲/۹۸</b>`, chart, { offset: [-0.95, 3.3, 0], cls: "l-stat" });
  const n = { h: 9.1, s: 162, d: 98 };
  const v1 = a1c.querySelector<HTMLElement>("[data-v]")!, v2 = bp.querySelector<HTMLElement>("[data-v]")!;
  const write = () => {
    v1.textContent = fa(n.h.toFixed(1)).replace(".", "٫");
    v2.textContent = `${fa(Math.round(n.s))}/${fa(Math.round(n.d))}`;
  };
  ctx.showAt(T.at("قند") - 0.2, a1c);
  ctx.showAt(T.at("فشار") - 0.2, bp);
  tl.to(n, { h: 6.9, s: 128, d: 80, duration: 2.4, ease: "power2.inOut", onUpdate: write }, T.at("قند"));
  tl.to([a1c, bp], { "--ok": 1, duration: 0.4 }, T.at("محدودهٔ"));

  // شادی رضا + کاغذرنگی
  const joy = T.at("راحت") - 0.6;
  ctx.sfx("success", joy + 0.2, 0.75);
  tl.to(reza.pose, { happy: 1, duration: 0.4 }, joy);
  tl.to(reza.pose, { happy: 0, duration: 0.5 }, T.end - 0.4);
  const conf = confetti(36);
  conf.mesh.position.set(-2.6, 2.2, 0.85);
  st.add(conf.mesh);
  tl.fromTo(conf.state, { p: 0 }, { p: 1, duration: 2.6, ease: "none", onUpdate: conf.apply, immediateRender: false }, joy);

  ctx.hideAt(T.end - 0.5, a1c, bp);
};
export default result;

/** کاغذرنگی با حرکت پرتابی قطعی (برای جلو/عقب‌کردن دقیق) */
function confetti(count: number) {
  const g = new THREE.PlaneGeometry(0.09, 0.14);
  const m = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, toneMapped: false });
  const inst = new THREE.InstancedMesh(g, m, count);
  const colors = [C.mint, C.amber, C.teal, C.red, 0xffffff];
  const seeds = Array.from({ length: count }, (_, i) => {
    const a = (i / count) * Math.PI * 2 + Math.sin(i * 12.9) * 0.4;
    return { vx: Math.cos(a) * (1 + (i % 5) * 0.25), vz: Math.sin(a) * 0.8, vy: 4 + (i % 7) * 0.45, spin: 4 + (i % 3) * 3 };
  });
  const col = new THREE.Color();
  seeds.forEach((_, i) => inst.setColorAt(i, col.set(colors[i % colors.length])));
  const state = { p: 0 };
  const d = new THREE.Object3D();
  const apply = () => {
    const t = state.p * 2.2;
    seeds.forEach((s, i) => {
      d.position.set(s.vx * t, s.vy * t - 4.9 * t * t, s.vz * t);
      d.rotation.set(t * s.spin, t * s.spin * 0.7, 0);
      d.scale.setScalar(state.p > 0 && state.p < 1 ? 1 : 0);
      d.updateMatrix();
      inst.setMatrixAt(i, d.matrix);
    });
    inst.instanceMatrix.needsUpdate = true;
  };
  apply();
  inst.frustumCulled = false;
  return { mesh: inst, state, apply };
}
