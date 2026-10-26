import * as THREE from "three";
import { C } from "../kit/kit";
import { drawTube, visitPost } from "../kit/props";
import { LEAD, pop, travel, type SceneBuilder } from "../rig";

/** ۲ — فاصلهٔ بین دو ویزیت: نمودار قند/فشار که بی‌خبر بالا می‌رود */
const gap: SceneBuilder = (ctx, T, k) => {
  const { tl, labels } = ctx;
  const st = ctx.world.stations[k];
  travel(ctx, k, T.t0 - LEAD, { x: 2.75, z: 1.0, yaw: -0.5 });

  // ویزیت اول سمت راست (شروع زمان در خوانش فارسی)، ویزیت بعدی سمت چپ
  const a = visitPost(C.teal);
  a.position.set(1.55, 0, -0.6);
  const b = visitPost(0xb9ada0);
  b.position.set(-2.35, 0, -0.6);
  st.add(a, b);
  a.scale.setScalar(0.001);
  b.scale.setScalar(0.001);
  pop(ctx, a, T.at("دکتر") - 0.2);
  pop(ctx, b, T.at("ویزیت") - 0.2);
  const la = labels.add("ویزیت دکتر", a, { offset: [0, -0.35, 0.6], cls: "l-chip" });
  const lb = labels.add("ویزیت بعدی", b, { offset: [0, -0.35, 0.6], cls: "l-chip l-muted" });
  ctx.showAt(T.at("دکتر"), la);
  ctx.showAt(T.at("ویزیت"), lb);

  // نمودار: از ویزیت اول تا ویزیت بعد، نامنظم رو به بالا
  const ys = [0.95, 1.35, 1.05, 1.7, 1.4, 2.1, 1.85, 2.55];
  const pts = ys.map((y, i) => new THREE.Vector3(1.2 - i * 0.5, y, -0.45));
  const line = drawTube(pts, 0.05, C.red, { emissive: true });
  st.add(line.group);
  tl.fromTo(line.state, { p: 0 }, { p: 1, duration: T.after("بالا") - T.at("بین") + 0.4, ease: "sine.inOut", onUpdate: line.apply, immediateRender: false }, T.at("بین"));

  // علامت‌های سؤال روی قله‌ها
  const peaks = [3, 5, 7];
  const marks = peaks.map((i) => labels.add("؟", st, { offset: [pts[i].x, pts[i].y + 0.5, pts[i].z], cls: "l-q" }));
  marks.forEach((m, i) => ctx.showAt(T.at("هیچ") - 0.4 + i * 0.25, m));
  marks.forEach((_, i) => ctx.sfx("tick", T.at("هیچ") - 0.4 + i * 0.25, 1.4));
  const gapL = labels.add("۹۰ روز بی‌خبری", st, { offset: [-0.4, 0.55, 0.9], cls: "l-chip l-red l-xl" });
  ctx.showAt(T.at("خبر") - 0.2, gapL);
  ctx.sfx("alert", T.at("خبر") - 0.2, 0.3);

  ctx.onFrame((time) => {
    a.userData.badge.rotation.y = time * 0.8;
    b.userData.badge.rotation.y = -time * 0.8;
  });
  ctx.hideAt(T.end - 0.5, la, lb, gapL, ...marks);
};
export default gap;
