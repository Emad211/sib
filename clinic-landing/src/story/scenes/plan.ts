import * as THREE from "three";
import { C, CSS, FONT, Panel, geo, mat, mesh, rr } from "../kit/kit";
import { Character } from "../kit/character";
import { LEAD, pop, travel, type SceneBuilder } from "../rig";

const ROWS = ["هدف قند ناشتا", "هدف فشار خون", "برنامهٔ دارو", "تقویم آزمایش‌ها"];

/** ۳ — عضویت و ویزیت اول: پزشک و تخته‌شاسیِ برنامهٔ اختصاصی که ردیف‌به‌ردیف پر می‌شود */
const plan: SceneBuilder = (ctx, T, k) => {
  const { tl } = ctx;
  const st = ctx.world.stations[k];
  travel(ctx, k, T.t0 - LEAD, { x: -1.85, z: 0.6, yaw: 0.55 }, { ty: 1.35 });

  const doc = new Character("nurse");
  doc.root.position.set(1.9, 0, 0.35);
  doc.root.rotation.y = -0.5;
  st.add(doc.root);
  doc.root.scale.setScalar(0.001);
  pop(ctx, doc.root, T.at("ویزیت") - 0.5);
  tl.to(doc.pose, { nod: 1, duration: 0.3 }, T.at("هدف"));
  tl.to(doc.pose, { nod: 0, duration: 0.3 }, T.at("هدف") + 1.2);
  tl.to(doc.pose, { hold: 0.6, duration: 0.5 }, T.at("هدف") - 0.3);
  ctx.onFrame((time) => doc.update(time));

  // تخته‌شاسی با متن واقعی فارسی روی کاغذ
  const panel = new Panel(560, 720, { r0: 0, r1: 0, r2: 0, r3: 0 }, (g, w, h, s) => {
    g.fillStyle = CSS.white;
    rr(g, 0, 0, w, h, 26);
    g.fill();
    g.fillStyle = CSS.pine;
    g.font = `700 46px ${FONT}`;
    g.textAlign = "center";
    g.fillText("برنامهٔ آقا رضا", w / 2, 108);
    g.fillStyle = CSS.amber;
    g.fillRect(w / 2 - 50, 132, 100, 6);
    const vals = [s.r0, s.r1, s.r2, s.r3];
    vals.forEach((v, i) => {
      if (v <= 0) return;
      const y = 220 + i * 128;
      g.save();
      g.globalAlpha = Math.min(1, v);
      g.translate((1 - Math.min(1, v)) * -40, 0);
      g.fillStyle = CSS.mintSoft;
      rr(g, w - 108, y - 34, 64, 64, 16);
      g.fill();
      if (v > 0.5) {
        g.strokeStyle = CSS.teal;
        g.lineWidth = 9;
        g.lineCap = "round";
        g.lineJoin = "round";
        g.beginPath();
        g.moveTo(w - 92, y);
        g.lineTo(w - 80, y + 13);
        g.lineTo(w - 58, y - 14);
        g.stroke();
      }
      g.fillStyle = CSS.ink;
      g.font = `500 36px ${FONT}`;
      g.textAlign = "right";
      g.fillText(ROWS[i], w - 132, y + 13);
      g.fillStyle = "rgba(10,40,38,.08)";
      g.fillRect(44, y + 58, w - 88, 2);
      g.restore();
    });
  });

  const board = new THREE.Group();
  board.position.set(0.05, 2.05, -0.35);
  board.add(mesh(geo.rbox(1.62, 2.12, 0.08, 0.1), mat(C.wood, { rough: 0.6 })));
  const paper = new THREE.Mesh(geo.plane(1.42, 1.83), new THREE.MeshStandardMaterial({ map: panel.texture, roughness: 0.9 }));
  paper.position.set(0, -0.06, 0.045);
  board.add(paper);
  board.add(mesh(geo.rbox(0.56, 0.2, 0.1, 0.05), mat(0x9aa7a4, { metal: 0.6, rough: 0.3 }), 0, 1.0, 0.05));
  st.add(board);
  board.scale.setScalar(0.001);
  pop(ctx, board, T.at("ویزیت"), 1, 0.9);
  tl.fromTo(board.rotation, { y: -0.9 }, { y: 0, duration: 1.1, ease: "power3.out", immediateRender: false }, T.at("ویزیت"));

  const cues = [T.at("هدف"), T.at("فشار"), T.at("دارو"), T.at("تقویم")];
  cues.forEach((t, i) => {
    tl.to(panel.state, { [`r${i}`]: 1, duration: 0.55, ease: "power2.out" }, t - 0.1);
    ctx.sfx("tick", t + 0.15, 1.3);
  });

  // «عضو برنامهٔ پیگیری»: نشان عضویت روی آقا رضا
  const member = ctx.labels.add("✓ عضو برنامهٔ پیگیری", ctx.reza.root, { offset: [0, 2.9, 0], cls: "l-chip l-ok" });
  ctx.showAt(T.at("عضو"), member);
  ctx.sfx("success", T.at("عضو"), 0.35);
  ctx.hideAt(T.at("ویزیت") + 0.6, member);

  ctx.onFrame((time) => {
    board.position.y = 2.05 + Math.sin(time * 1.1) * 0.05;
  });
};
export default plan;
