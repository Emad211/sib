import * as THREE from "three";
import { C, CSS, FONT, Panel, geo, mat, mesh, rr } from "../kit/kit";
import { LEAD, pop, travel, type SceneBuilder } from "../rig";

const ITEMS: { cell: number; text: string; sub: string; color: number; css: string }[] = [
  { cell: 0, text: "HbA1c", sub: "هر ۳ ماه", color: C.amber, css: CSS.amber },
  { cell: 2, text: "چربی خون", sub: "سالانه", color: C.teal, css: CSS.teal },
  { cell: 5, text: "کلیه", sub: "سالانه", color: C.red, css: CSS.red },
  { cell: 7, text: "معاینهٔ چشم", sub: "سالانه", color: C.pine, css: CSS.pine },
  { cell: 9, text: "معاینهٔ پا", sub: "هر ویزیت", color: C.amber, css: CSS.amber },
];

/** ۶ — تقویم سلامت: کاشی‌ها یکی‌یکی برمی‌گردند و آزمایش‌ها نوبت‌دهی می‌شوند؛ زنگ یادآوری */
const calendar: SceneBuilder = (ctx, T, k) => {
  const { tl, labels, reza } = ctx;
  const st = ctx.world.stations[k];
  travel(ctx, k, T.t0 - LEAD, { x: -2.55, z: 0.8, yaw: 0.55 }, { ty: 1.5, r: 13 + 8.6 });

  const cal = new THREE.Group();
  cal.position.set(0.75, 0, -0.4);
  st.add(cal);
  cal.add(mesh(geo.rbox(3.3, 2.65, 0.14, 0.14), mat(C.white, { rough: 0.45 }), 0, 1.85, 0));
  cal.add(mesh(geo.rbox(3.3, 0.55, 0.18, 0.12), mat(C.pine, { rough: 0.45 }), 0, 3.0, 0.01));
  for (const x of [-1.1, 1.1]) cal.add(mesh(geo.cyl(0.06, 0.06, 0.4, 12), mat(0x9aa7a4, { metal: 0.6, rough: 0.3 }), x, 3.3, 0.02));
  const head = new Panel(640, 100, {}, (g, w, h) => {
    g.fillStyle = CSS.mint;
    g.textAlign = "center";
    g.font = `700 52px ${FONT}`;
    g.fillText("تقویم سلامت آقا رضا", w / 2, h * 0.68);
  });
  const hp = new THREE.Mesh(geo.plane(2.9, 0.45), new THREE.MeshBasicMaterial({ map: head.texture, transparent: true, toneMapped: false }));
  hp.position.set(0, 3.0, 0.105);
  cal.add(hp);
  // ستون‌های پایه
  for (const x of [-1.2, 1.2]) cal.add(mesh(geo.cyl(0.05, 0.07, 0.6, 12), mat(C.pine), x, 0.3, 0));

  // ۱۲ کاشی (۴×۳)، از راست به چپ
  const tiles: THREE.Group[] = [];
  const front = mat(C.platform, { rough: 0.7 });
  for (let i = 0; i < 12; i++) {
    const col = i % 4, row = Math.floor(i / 4);
    const t = new THREE.Group();
    t.position.set(1.08 - col * 0.72, 2.32 - row * 0.62, 0.12);
    t.add(mesh(geo.rbox(0.64, 0.52, 0.07, 0.07), front));
    cal.add(t);
    tiles.push(t);
  }

  const flipAt = T.at("آزمایش‌ها") - 0.1;
  ITEMS.forEach((it, i) => {
    const p = new Panel(256, 208, {}, (g, w, h) => {
      g.fillStyle = it.css;
      rr(g, 0, 0, w, h, 28);
      g.fill();
      g.fillStyle = CSS.white;
      g.textAlign = "center";
      g.font = `800 ${it.text.length > 6 ? 40 : 50}px ${FONT}`;
      g.fillText(it.text, w / 2, h * 0.48);
      g.font = `500 32px ${FONT}`;
      g.globalAlpha = 0.85;
      g.fillText(it.sub, w / 2, h * 0.8);
    });
    const back = new THREE.Mesh(geo.plane(0.64, 0.52), new THREE.MeshStandardMaterial({ map: p.texture, roughness: 0.5 }));
    back.position.z = -0.04;
    back.rotation.x = Math.PI; // پشت کاشی، وارونه — بعد از چرخش ۱۸۰ درجه صاف دیده می‌شود
    const tile = tiles[it.cell];
    tile.add(back);
    const t = flipAt + i * 0.62;
    tl.to(tile.rotation, { x: Math.PI, duration: 0.75, ease: "back.out(1.4)" }, t);
    ctx.sfx("tick", t + 0.1, 1.5);
    tl.fromTo(tile.position, { z: 0.12 }, { z: 0.45, duration: 0.35, yoyo: true, repeat: 1, ease: "sine.out", immediateRender: false }, t);
  });

  // زنگ یادآوری
  const bell = new THREE.Group();
  bell.position.set(-1.75, 3.35, 0.3);
  const bellBody = mesh(geo.bell(), mat(C.amber, { rough: 0.3, metal: 0.3 }), 0, -0.35, 0);
  bell.add(bellBody, mesh(geo.sphere(0.1), mat(C.amber, { rough: 0.3 }), 0, -0.78, 0));
  cal.add(bell);
  bell.scale.setScalar(0.001);
  const ring = T.at("خودمون");
  pop(ctx, bell, ring - 0.5, 0.9);
  ctx.sfx("alert", ring - 0.05, 0.35);
  tl.fromTo(bell.rotation, { z: -0.45 }, { z: 0.45, duration: 0.18, yoyo: true, repeat: 9, ease: "sine.inOut", immediateRender: false }, ring - 0.1);
  tl.to(bell.rotation, { z: 0, duration: 0.3 }, ring + 1.75);
  const remind = labels.add("یک هفته قبل از هر نوبت، یادآوری", bell, { offset: [0, 0.55, 0], cls: "l-chip l-amber" });
  ctx.showAt(ring, remind);
  tl.to(reza.pose, { nod: 1, duration: 0.3 }, ring);
  tl.to(reza.pose, { nod: 0, duration: 0.3 }, ring + 1.0);
  ctx.hideAt(T.end - 0.5, remind);
};
export default calendar;
