import gsap from "gsap";
import type { SceneBuilder } from "../rig";

// خط نوار قلب از راست به چپ (جهت خواندن فارسی): دو ضربان
const ECG =
  "M1000 100 L770 100 L752 88 L736 100 L706 100 L694 120 L676 18 L658 168 L645 100 L606 100 L584 78 L562 100 L384 100 L372 120 L354 18 L336 168 L323 100 L284 100 L262 78 L240 100 L0 100";

const PEOPLE = `<span class="kt-ppl" aria-hidden="true">
  <svg class="p1" viewBox="0 0 40 64"><circle cx="20" cy="12" r="10"/><path d="M4 62V40a16 16 0 0 1 32 0v22z"/></svg>
  <svg class="p2" viewBox="0 0 40 64"><circle cx="20" cy="12" r="10"/><path d="M4 62V40a16 16 0 0 1 32 0v22z"/></svg>
</span>`;

/** ۰ — قلاب: شب، نوار قلب، تایپوگرافی درشت؛ «درد ندارند» و «۱ از ۲ خبر ندارد» */
const hook: SceneBuilder = (ctx, T) => {
  const { tl, world } = ctx;

  // شب است؛ پیش از آمدن آقا رضا هوا روشن می‌شود
  tl.fromTo(world.mood, { v: 0 }, { v: 1, duration: 2.8, ease: "sine.inOut", immediateRender: true }, T.end - 1.0);
  // دوربین آرام دور حلقهٔ تاریک می‌چرخد
  tl.to(ctx.cam, { a: "+=0.42", y: 19, r: 13 + 22, duration: Math.max(1, T.end - 1.5), ease: "sine.inOut" }, 0);

  const shade = ctx.kt("", "kt-shade");
  tl.fromTo(shade, { autoAlpha: 1 }, { autoAlpha: 1, duration: 0.01, immediateRender: true }, 0);
  tl.to(shade, { autoAlpha: 0, duration: 1.4, ease: "sine.inOut" }, T.end - 0.9);

  // نوار قلب
  const ecg = ctx.kt(
    `<svg viewBox="0 0 1000 200" preserveAspectRatio="none"><path class="g" pathLength="1" d="${ECG}"/><path pathLength="1" d="${ECG}"/></svg>`,
    "kt-ecg"
  );
  const paths = ecg.querySelectorAll("path");
  tl.set(ecg, { autoAlpha: 1 }, 0.02);
  tl.fromTo(paths, { strokeDashoffset: 1 }, { strokeDashoffset: 0, duration: 2.6, ease: "none", immediateRender: true }, 0.05);
  ctx.sfx("heart", 0.12, 0.9);

  // «قند و فشارِ بالا» ... «درد ندارند.»
  const eyebrow = ctx.kt("قند و فشارِ بالا", "kt-eyebrow");
  const big = ctx.kt("درد ندارن.", "kt-big");
  ctx.showAt(T.at("قند") - 0.05, eyebrow, { duration: 0.5, ease: "power3.out" });
  const pain = T.at("درد");
  tl.fromTo(big, { autoAlpha: 0, scale: 1.35, filter: "blur(14px)" }, { autoAlpha: 1, scale: 1, filter: "blur(0px)", duration: 0.55, ease: "expo.out", immediateRender: false }, pain - 0.08);
  ctx.sfx("heart", pain - 0.1, 0.7);
  tl.to(ecg, { autoAlpha: 0.25, duration: 0.6 }, pain);

  // «۱ از ۲» — نیمی از مبتلایان خبر ندارند
  const half = T.at("نصف");
  tl.to([eyebrow, big], { autoAlpha: 0, y: -24, duration: 0.4, ease: "power2.in" }, half - 0.45);
  tl.to(ecg, { autoAlpha: 0, duration: 0.4 }, half - 0.45);
  const stat = ctx.kt(`${PEOPLE}<b>۱ از ۲</b><span class="kt-sub">مبتلا، از بیماری‌اش خبر ندارد.</span>`, "kt-stat");
  const sub = stat.querySelector<HTMLElement>(".kt-sub")!;
  const p2 = stat.querySelector<SVGElement>(".p2")!;
  gsap.set(sub, { autoAlpha: 0 });
  tl.fromTo(stat, { autoAlpha: 0, scale: 0.8 }, { autoAlpha: 1, scale: 1, duration: 0.6, ease: "back.out(1.7)", immediateRender: false }, half - 0.05);
  ctx.sfx("pop", half - 0.05, 0.6);
  const unaware = T.at("خبر");
  tl.fromTo(p2, { opacity: 1 }, { opacity: 0.22, duration: 0.5, ease: "power1.in", immediateRender: false }, unaware - 0.3);
  tl.fromTo(sub, { autoAlpha: 0, y: 10 }, { autoAlpha: 1, y: 0, duration: 0.45, ease: "power2.out", immediateRender: false }, unaware - 0.3);
  tl.to(stat, { autoAlpha: 0, scale: 1.06, duration: 0.5, ease: "power2.in" }, T.end - 0.55);
};
export default hook;
