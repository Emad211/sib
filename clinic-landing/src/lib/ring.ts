/** مراحل حلقهٔ مراقبت؛ آیکن‌ها از اسپرایت index.html */
const STEPS = [
  { icon: "i-phone", t: "ثبت شماره", d: "شماره‌تان را همین‌جا می‌گذارید؛ کمتر از یک دقیقه وقت می‌گیرد." },
  { icon: "i-chat", t: "تماس ما", d: "برنامه را توضیح می‌دهیم و وقت ویزیت اول را هماهنگ می‌کنیم." },
  { icon: "i-steth", t: "ویزیت اول", d: "معاینه، بررسی آزمایش‌ها و تعیین هدف قند و فشار شما." },
  { icon: "i-chart", t: "اندازه‌گیری در خانه", d: "قند و فشار را در خانه می‌گیرید و برای تیم پیگیری می‌فرستید." },
  { icon: "i-bell", t: "تماس و تنظیم", d: "اگر عددی بیرون از محدوده باشد، با نظر پزشک برنامه تنظیم می‌شود." },
  { icon: "i-cal", t: "بازبینی دوره‌ای", d: "ویزیت‌ها و آزمایش‌های منظم؛ برنامه به‌روز می‌شود و حلقه از نو می‌چرخد." },
];
const fa = (n: number) => String(n).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[+d]);

/**
 * حلقهٔ «روال کار»: بخش هنگام اسکرول «می‌چسبد» و خودِ اسکرول، مراحل ۱ تا ۶ را جلو می‌برد.
 * کلیک روی هر مرحله، صفحه را به همان نقطه از اسکرول می‌برد تا هر دو همیشه هماهنگ بمانند.
 */
export function initRing() {
  const how = document.getElementById("how");
  const ring = document.getElementById("ring");
  const nodesEl = document.getElementById("ring-nodes");
  if (!how || !ring || !nodesEl) return;
  const section: HTMLElement = how;
  const prog = ring.querySelector<SVGCircleElement>(".ring-prog")!;
  const core = ring.querySelector<HTMLElement>(".ring-core")!;
  const nEl = core.querySelector(".ring-n")!, tEl = core.querySelector(".ring-t")!, dEl = core.querySelector(".ring-d")!;
  const list = Array.from(section.querySelectorAll<HTMLButtonElement>("#how-steps button"));
  const dots = Array.from(section.querySelectorAll<HTMLElement>(".how-dots i"));

  // ایستگاه‌ها روی دایره (r=160 از 400)، از بالا و ساعت‌گرد
  const nodes = STEPS.map((s, i) => {
    const a = -Math.PI / 2 + (i / STEPS.length) * Math.PI * 2;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ring-node";
    b.style.left = `${50 + 40 * Math.cos(a)}%`;
    b.style.top = `${50 + 40 * Math.sin(a)}%`;
    b.setAttribute("aria-label", `مرحلهٔ ${fa(i + 1)}: ${s.t}`);
    b.innerHTML = `<svg><use href="#${s.icon}"/></svg>`;
    b.addEventListener("click", () => jump(i));
    nodesEl.append(b);
    return b;
  });
  list.forEach((b) => b.addEventListener("click", () => jump(+b.dataset.i!)));

  let cur = -1;
  function show(i: number, frac: number) {
    // پیشرفت حلقه پیوسته با اسکرول پر می‌شود؛ متن مرکز با هر مرحله عوض می‌شود
    prog.style.strokeDasharray = `${Math.min(100, ((i + frac) / STEPS.length) * 100) + 0.01} 100`;
    if (i === cur) return;
    cur = i;
    const mark = (els: HTMLElement[]) =>
      els.forEach((n, j) => {
        n.classList.toggle("on", j === i);
        n.classList.toggle("done", j < i);
      });
    mark(nodes);
    mark(list);
    mark(dots);
    nEl.textContent = fa(i + 1);
    tEl.textContent = STEPS[i].t;
    dEl.textContent = STEPS[i].d;
    core.classList.remove("swap");
    void core.offsetWidth;
    core.classList.add("swap");
  }

  const pinned = () => getComputedStyle(section.querySelector(".how-pin")!).position === "sticky";
  /** پیشرفت اسکرول داخل بخش: ۰ تا ۱ */
  const progress = () => {
    const r = section.getBoundingClientRect();
    const span = r.height - innerHeight;
    return span > 0 ? Math.min(1, Math.max(0, -r.top / span)) : 0;
  };
  function onScroll() {
    if (!pinned()) return;
    const p = progress() * STEPS.length;
    const i = Math.min(STEPS.length - 1, Math.floor(p));
    show(i, i === STEPS.length - 1 ? 1 : p - i);
    section.classList.toggle("end", p >= STEPS.length - 0.6);
  }
  function jump(i: number) {
    if (!pinned()) return show(i, 1);
    const top = section.getBoundingClientRect().top + scrollY;
    const span = section.offsetHeight - innerHeight;
    scrollTo({ top: top + ((i + 0.5) / STEPS.length) * span, behavior: "smooth" });
  }

  let ticking = false;
  addEventListener(
    "scroll",
    () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        onScroll();
      });
    },
    { passive: true }
  );
  addEventListener("resize", onScroll);
  show(0, 0);
  onScroll();
}
