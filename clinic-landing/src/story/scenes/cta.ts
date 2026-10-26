import { Character } from "../kit/character";
import { stationAngle } from "../world";
import { LEAD, SHOT, pop, travel, type SceneBuilder } from "../rig";

/** ۸ — پایان: آقا رضا و پرستار کنار هم، دوربین بالا می‌رود و کل حلقهٔ مراقبت دیده می‌شود؛ کارت پایانی برند */
const cta: SceneBuilder = (ctx, T, k) => {
  const { tl, reza, world } = ctx;
  const st = ctx.world.stations[k];
  travel(ctx, k, T.t0 - LEAD, { x: -0.85, z: 0.7, yaw: 0.15 });

  const nurse = new Character("nurse");
  nurse.root.position.set(0.9, 0, 0.7);
  nurse.root.rotation.y = -0.15;
  st.add(nurse.root);
  nurse.root.scale.setScalar(0.001);
  pop(ctx, nurse.root, T.t0 - 0.4);
  ctx.onFrame((time) => nurse.update(time));

  const hi = T.at("تنها");
  tl.to([reza.pose, nurse.pose], { wave: 1, duration: 0.4 }, hi);
  tl.to([reza.pose, nurse.pose], { wave: 0, duration: 0.5 }, hi + 2.2);

  // بالا رفتن دوربین تا نمای کامل حلقه + کامل‌شدن مسیر
  const crane = T.at("شماره") - 0.9;
  tl.to(ctx.cam, { ...SHOT.overview(stationAngle(k) + 0.45, { y: 16.5, r: 13 + 16, ty: 1.5 }), duration: 5.2, ease: "power2.inOut" }, crane - 0.3);
  tl.to(world.ringUniforms.uP, { value: 1, duration: 3.6, ease: "power1.inOut" }, crane);
  tl.to(world.hubPulse, { v: 1, duration: 0.3, yoyo: true, repeat: 5, ease: "sine.inOut" }, crane + 3.2);

  // کارت پایانی: لوگو، شعار و دعوت به اقدام
  const end = ctx.kt(
    `<span class="kt-logo" aria-hidden="true"></span><b>بین دو ویزیت،<br><em>حواسمان به شما هست.</em></b><a href="#join" class="kt-btn">درخواست تماس</a><button type="button" class="kt-replay" data-act="replay">دوباره ببینید</button>`,
    "kt-end"
  );
  const show = T.at("شماره") - 0.3;
  tl.fromTo(end, { autoAlpha: 0, y: 26, scale: 0.94 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.9, ease: "expo.out", immediateRender: false }, show);
  ctx.sfx("success", show + 0.1, 0.5);
};
export default cta;
