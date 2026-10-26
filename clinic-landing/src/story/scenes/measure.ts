import * as THREE from "three";
import { C, CSS, Panel } from "../kit/kit";
import { dataStream, glucometer, phone } from "../kit/props";
import { LEAD, pop, travel, type SceneBuilder } from "../rig";
import { button, card, screenFrame } from "./screens";

/** ۴ — اندازه‌گیری در خانه و ارسال: ذرات داده از گوشی تا درمانگاه پرواز می‌کنند */
const measure: SceneBuilder = (ctx, T, k) => {
  const { tl, labels, reza, world } = ctx;
  const st = ctx.world.stations[k];
  travel(ctx, k, T.t0 - LEAD, { x: 1.55, z: 0.7, yaw: -0.55 });

  const panel = new Panel(480, 960, { g: 0, b: 0, s: 0 }, (g, w, h, s) => {
    screenFrame(g, w, h, "پیگیری سلامت");
    card(g, w, 200, "قند ناشتا", "۱۲۸", "mg/dL", s.g);
    card(g, w, 380, "فشار خون", "۱۳۲/۸۴", "mmHg", s.b);
    button(g, w, 600, s.s >= 1 ? "ارسال شد ✓" : "ارسال برای درمانگاه", s.s > 0 ? 1 : s.b, s.s >= 1 ? CSS.teal : CSS.pine);
  });
  const ph = phone(panel);
  ph.position.set(-1.0, 1.9, 0.1);
  ph.rotation.y = 0.28;
  st.add(ph);
  ph.scale.setScalar(0.001);
  pop(ctx, ph, T.t0 - 0.3);
  ctx.onFrame((time) => {
    ph.position.y = 1.9 + Math.sin(time * 1.2) * 0.06;
    ph.rotation.z = Math.sin(time * 0.8) * 0.03;
  });

  // دستگاه قند در دست رضا
  const gm = glucometer();
  gm.rotation.set(-0.3, 0, 0);
  gm.position.set(0, -0.1, 0.1);
  reza.handR.add(gm);
  gm.scale.setScalar(0.001);
  pop(ctx, gm, T.at("قند") - 0.3);
  tl.to(reza.pose, { hold: 1, duration: 0.6, ease: "power2.inOut" }, T.at("قند") - 0.4);
  tl.to(reza.pose, { hold: 0, duration: 0.6 }, T.end - 0.6);
  tl.to(gm.scale, { x: 0.001, y: 0.001, z: 0.001, duration: 0.3 }, T.end - 0.5);

  tl.to(panel.state, { g: 1, duration: 0.6 }, T.at("قند"));
  tl.to(panel.state, { b: 1, duration: 0.6 }, T.at("فشارش"));
  const send = T.at("برامون");
  ctx.sfx("send", send - 0.45, 0.7);
  const weekly = labels.add("هر هفته", reza.root, { offset: [0, 2.85, 0], cls: "l-chip l-dark" });
  ctx.showAt(T.at("هفته") - 0.2, weekly);
  ctx.hideAt(send, weekly);
  tl.to(panel.state, { s: 1, duration: 0.25 }, send - 0.5);

  // ذرات: از گوشی، قوس‌دار، تا تابلوی درمانگاه
  world.root.updateMatrixWorld(true);
  const from = ph.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0, 0.9, 0));
  const to = world.hubPort.clone();
  const mid = from.clone().lerp(to, 0.5).setY(8.5);
  const curve = new THREE.CatmullRomCurve3([from, from.clone().lerp(mid, 0.5).setY(5.5), mid, to]);
  const ds = dataStream(12, C.mint, C.amber);
  ds.setCurve(curve);
  world.root.add(ds.mesh);
  tl.fromTo(ds.state, { p: 0 }, { p: 1, duration: 2.2, ease: "none", onUpdate: ds.apply, immediateRender: false }, send);
  tl.to(world.hubPulse, { v: 1, duration: 0.25, yoyo: true, repeat: 3, ease: "sine.inOut" }, send + 1.0);

  // دوربین کمی عقب می‌رود تا مسیر داده تا درمانگاه دیده شود
  tl.to(ctx.cam, { r: 13 + 13.5, y: 5.2, ty: 2.6, fov: 36, duration: 1.8, ease: "power2.inOut" }, send - 0.4);

  const teamL = labels.add("تیم پیگیری درمانگاه", world.hubSign, { offset: [0, 2.1, 0], cls: "l-chip l-dark" });
  ctx.showAt(send + 1.0, teamL);
  ctx.hideAt(T.end - 0.4, teamL);
};
export default measure;
