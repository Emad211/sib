import "./styles/main.css";
import "./story/story.css";
import { CONFIG } from "./config";
import { bindLeadForms } from "./lib/lead";
import { initRing } from "./lib/ring";
import { initStories } from "./lib/stories";

const faDigits = (s: string | number) => String(s).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[+d]);

// ---------- اطلاعات درمانگاه ----------
document.querySelectorAll(".js-name").forEach((e) => (e.textContent = CONFIG.clinicName));
document.querySelectorAll<HTMLElement>(".js-tel").forEach((e) => {
  e.textContent = CONFIG.phoneDisplay;
  if (e instanceof HTMLAnchorElement) e.href = `tel:${CONFIG.phoneTel}`;
});
document.querySelectorAll<HTMLAnchorElement>(".js-tel-btn").forEach((e) => (e.href = `tel:${CONFIG.phoneTel}`));
document.title = `${CONFIG.clinicName} | پیگیری دیابت و فشار خون`;

bindLeadForms();

// ---------- هیرو سه‌بعدی: بلافاصله بعد از اولین نمایش صفحه (هندسه از قبل پخته شده و سبک است) ----------
const h3 = document.getElementById("hero3d");
if (h3) {
  const go = () => import("./hero3d").then((m) => m.mountHero(h3)).catch(() => {});
  setTimeout(go, 0);
}

// ---------- هدر و نوار ثابت موبایل ----------
const hdr = document.getElementById("hdr")!;
const mbar = document.getElementById("mbar")!;
// نوار تماس پایین موبایل روی این‌ها نمی‌آید: فرم عضویت، گوشیِ قاب‌ها (روی نوار پیامش می‌افتاد)، نقشه و فوتر
const blockers = ["#join", "#program .phone", "#map", "footer"].map((q) => document.querySelector<HTMLElement>(q)).filter((e): e is HTMLElement => !!e);
const inView = (e: HTMLElement) => {
  const r = e.getBoundingClientRect();
  return r.top < innerHeight - 40 && r.bottom > 80;
};
addEventListener(
  "scroll",
  () => {
    hdr.classList.toggle("scrolled", scrollY > 8);
    mbar.classList.toggle("show", scrollY > 500 && !blockers.some(inView));
  },
  { passive: true }
);

// ---------- شمارنده‌های عددی (۳۷٪ و ...) ----------
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
function countUp(el: HTMLElement) {
  const to = +el.dataset.count!, fmt = el.dataset.fmt;
  const show = (v: number) => (el.textContent = fmt === "ratio" ? `${faDigits(v)} از ۲` : `${faDigits(v)}٪`);
  if (reduce || fmt === "ratio") return show(to);
  const t0 = performance.now(), dur = 1400;
  const step = (now: number) => {
    const p = Math.min(1, (now - t0) / dur);
    show(Math.round(to * (1 - Math.pow(1 - p, 3))));
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ---------- ظاهرشدن بخش‌ها ----------
const io = new IntersectionObserver(
  (es) =>
    es.forEach((e) => {
      if (e.isIntersecting) {
        e.target.classList.add("in");
        e.target.querySelectorAll<HTMLElement>("[data-count]").forEach(countUp);
        io.unobserve(e.target);
      }
    }),
  { threshold: 0.12 }
);
document.querySelectorAll(".rv").forEach((el) => io.observe(el));

// ---------- حلقهٔ مراقبت (روال کار) ----------
initRing();
const pg = document.getElementById("program");
if (pg) initStories(pg);

// ---------- نقشهٔ سه‌بعدی: فقط نزدیک پایین صفحه بارگذاری می‌شود ----------
const mapBox = document.getElementById("map-box");
if (mapBox)
  new IntersectionObserver(
    (es, obs) => {
      if (!es.some((e) => e.isIntersecting)) return;
      obs.disconnect();
      import("./lib/map").then((m) => m.mountMap(mapBox)).catch(() => mapBox.classList.add("map-fail"));
    },
    { rootMargin: "600px 0px" }
  ).observe(mapBox);

// ---------- انیمیشن سه‌بعدی: فقط وقتی کاربر به آن نزدیک شد بارگذاری می‌شود ----------
const sp = document.getElementById("sp");
if (sp) {
  let loading: Promise<unknown> | null = null;
  const load = (autoplay = false) =>
    (loading ??= import("./story/player")
      .then(({ mountPlayer }) => mountPlayer(sp, { autoplay }))
      .then((p) => {
        if (import.meta.env.DEV) Object.assign(window, { __player: p });
        return p;
      }));
  new IntersectionObserver(
    (es, obs) => {
      if (es.some((e) => e.isIntersecting)) {
        obs.disconnect();
        load();
      }
    },
    { rootMargin: "700px 0px" }
  ).observe(sp);
  // اگر قبل از آماده‌شدن روی «پخش» زد
  sp.querySelector(".sp-poster")?.addEventListener(
    "click",
    () => {
      if (!loading) load(true);
      else sp.dataset.wantPlay = "1";
    },
    { once: true }
  );
}
