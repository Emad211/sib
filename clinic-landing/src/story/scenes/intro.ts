import { C } from "../kit/kit";
import { badge, pulseRing } from "../kit/props";
import { SHOT, pop, type SceneBuilder } from "../rig";

/** ۱ — معرفی آقا رضا: هوا روشن می‌شود، فرود دوربین، دو کاشی دیابت و فشار خون، «حالم خوبه» */
const intro: SceneBuilder = (ctx, T, k) => {
  const { tl, labels, reza } = ctx;
  const st = ctx.world.stations[k];

  // فرود دوربین: از بالای حلقهٔ تاریک به ایستگاه اول، هم‌زمان با روشن‌شدن هوا
  const dive = T.t0 - 1.7;
  tl.to(ctx.cam, { ...SHOT.station(k), duration: 3.0, ease: "power3.inOut" }, dive);
  ctx.sfx("whoosh", dive + 0.3, 0.7);

  const hi = T.at("رضاست") + 0.5; // بعد از فرود دوربین
  tl.to(reza.pose, { wave: 1, duration: 0.4 }, hi - 0.1);
  tl.to(reza.pose, { wave: 0, duration: 0.5 }, hi + 1.8);
  const title = labels.add(`<b>آقا رضا</b><small>۵۸ ساله</small>`, reza.root, { offset: [0, 2.85, 0], cls: "l-title" });
  ctx.showAt(hi, title);

  const drop = badge("drop", C.amber);
  drop.position.set(-1.85, 2.05, 0.1);
  const heart = badge("heart", C.red);
  heart.position.set(1.85, 1.8, 0.1);
  st.add(drop, heart);
  drop.scale.setScalar(0.001);
  heart.scale.setScalar(0.001);
  pop(ctx, drop, T.at("دیابت"));
  pop(ctx, heart, T.at("فشار"));
  const l1 = labels.add("دیابت نوع ۲", drop, { offset: [0, -0.88, 0], cls: "l-chip l-amber" });
  const l2 = labels.add("فشار خون بالا", heart, { offset: [0, -0.88, 0], cls: "l-chip l-red" });
  ctx.showAt(T.at("دیابت") + 0.15, l1);
  ctx.showAt(T.at("فشار") + 0.15, l2);

  // «حالش خوب است...»
  const fine = labels.add("«حالم خوبه!»", reza.root, { offset: [0.9, 2.55, 0], cls: "l-think", align: "left" });
  ctx.showAt(T.at("خوب") - 0.1, fine);

  // «...یا فکر می‌کند»: موج‌های قرمز آرام زیر پای آقا رضا
  const rings = [0, 1, 2].map(() => pulseRing(C.red, 0.9));
  rings.forEach((r, i) => {
    r.mesh.rotation.x = -Math.PI / 2;
    r.mesh.position.set(0, 0.03, 0.4);
    st.add(r.mesh);
    tl.fromTo(r.state, { t: 0 }, { t: 2, duration: 3.6, ease: "none", onUpdate: r.apply, immediateRender: false }, T.at("فکر") - 0.3 + i * 0.6);
    tl.set(r.state, { t: 0, onComplete: r.apply }, T.end);
  });
  const silent = labels.add("بی‌صدا، بالا می‌رود", reza.root, { offset: [0, 0.2, 1.4], cls: "l-ghost" });
  ctx.showAt(T.at("فکر"), silent);
  ctx.sfx("heart", T.at("فکر") - 0.1, 0.6);
  tl.to(fine, { "--crack": 1, duration: 0.4 }, T.at("فکر"));

  ctx.onFrame((time) => {
    drop.userData.inner.rotation.y = Math.sin(time * 0.9) * 0.4;
    drop.userData.inner.position.y = Math.sin(time * 1.3) * 0.08;
    heart.userData.inner.rotation.y = Math.sin(time * 0.8 + 1) * 0.4;
    heart.userData.inner.position.y = Math.sin(time * 1.2 + 1) * 0.08;
    // تپش قلب
    const beat = Math.pow(Math.max(0, Math.sin(time * 5.2)), 12) * 0.08;
    heart.userData.inner.scale.setScalar(1 + beat);
  });

  ctx.hideAt(T.end - 0.5, title, l1, l2, silent, fine);
};
export default intro;
