/**
 * نمایشگر استوری به سبک اینستاگرام برای بخش «با عضویت چه دریافت می‌کنید؟».
 * - هر اسلاید data-dur میلی‌ثانیه نمایش داده می‌شود و خودکار جلو می‌رود.
 * - ضربهٔ کوتاه: سمت چپ (۶۰٪) بعدی، سمت راست قبلی (چیدمان راست‌به‌چپ).
 * - نگه داشتن انگشت/موس: توقف؛ رها کردن: ادامه. کشیدن افقی هم جابه‌جا می‌کند.
 * - فقط وقتی دیده می‌شود پخش می‌شود. عناصر با data-x (نظرسنجی، کوییز، لینک) ضربه را مصرف نمی‌کنند.
 */
export function initStories(root: HTMLElement) {
  const ig = root.querySelector<HTMLElement>("#ig")!;
  const slides = [...ig.querySelectorAll<HTMLElement>(".st")];
  const bars = [...ig.querySelectorAll<HTMLElement>(".ig-bars i")];
  const hls = [...root.querySelectorAll<HTMLButtonElement>(".pst-hl button")];
  const n = slides.length;

  let i = 0;
  let t = 0;
  let manual = false; // دکمهٔ توقف
  let held = false; // انگشت روی صفحه
  let visible = false;
  let last = 0;
  let raf = 0;

  const dur = (k: number) => Number(slides[k].dataset.dur) || 6000;
  const playing = () => visible && !manual && !held && !document.hidden;

  function paint() {
    bars.forEach((b, k) => b.style.setProperty("--p", k < i ? "1" : k > i ? "0" : String(Math.min(1, t / dur(i)))));
  }

  function go(k: number) {
    k = ((k % n) + n) % n;
    const s = slides[k];
    slides.forEach((x) => x.classList.toggle("on", x === s));
    // بازپخش انیمیشن‌ها وقتی همان اسلاید دوباره نمایش داده می‌شود
    s.classList.remove("on");
    void s.offsetWidth;
    s.classList.add("on");
    i = k;
    t = 0;
    hls.forEach((h, j) => {
      h.classList.toggle("on", j === k);
      if (j === k) h.classList.add("seen");
      h.setAttribute("aria-current", j === k ? "true" : "false");
    });
    paint();
  }

  function sync() {
    ig.classList.toggle("paused", !playing());
    if (playing() && !raf) {
      last = performance.now();
      raf = requestAnimationFrame(loop);
    }
  }

  function loop(now: number) {
    raf = 0;
    if (!playing()) return;
    t += Math.min(100, now - last);
    last = now;
    if (t >= dur(i)) go(i + 1);
    else paint();
    raf = requestAnimationFrame(loop);
  }

  // ---- لمس و کلیک ----
  let downAt = 0, x0 = 0, y0 = 0, holdTimer = 0, onSticker = false;
  ig.addEventListener("pointerdown", (e) => {
    onSticker = !!(e.target as HTMLElement).closest("[data-x], button, a");
    if (onSticker || e.button > 0) return;
    downAt = performance.now();
    x0 = e.clientX;
    y0 = e.clientY;
    holdTimer = window.setTimeout(() => {
      held = true;
      ig.classList.add("held");
      sync();
    }, 200);
  });
  const release = (e: PointerEvent, cancel = false) => {
    if (onSticker || !downAt) return;
    clearTimeout(holdTimer);
    const quick = performance.now() - downAt < 220;
    const dx = e.clientX - x0, dy = e.clientY - y0;
    downAt = 0;
    const wasHeld = held;
    held = false;
    ig.classList.remove("held");
    if (!cancel && Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
      go(dx > 0 ? i + 1 : i - 1); // راست‌به‌چپ: کشیدن به راست = بعدی
    } else if (!cancel && quick && !wasHeld) {
      const r = ig.getBoundingClientRect();
      const fromRight = (r.right - e.clientX) / r.width;
      fromRight < 0.4 ? (t > 900 ? go(i) : go(i - 1)) : go(i + 1);
    }
    sync();
  };
  ig.addEventListener("pointerup", (e) => release(e));
  ig.addEventListener("pointercancel", (e) => release(e, true));
  ig.addEventListener("pointerleave", (e) => downAt && release(e, true));
  ig.addEventListener("contextmenu", (e) => e.preventDefault());

  ig.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") go(i + 1);
    else if (e.key === "ArrowRight") go(i - 1);
    else if (e.key === " ") {
      e.preventDefault();
      manual = !manual;
    } else return;
    sync();
  });

  ig.querySelector(".ig-pp")!.addEventListener("click", () => {
    manual = !manual;
    sync();
  });
  root.querySelector(".pst-arrow.next")?.addEventListener("click", () => (go(i + 1), sync()));
  root.querySelector(".pst-arrow.prev")?.addEventListener("click", () => (go(i - 1), sync()));
  hls.forEach((h) => h.addEventListener("click", () => (go(Number(h.dataset.go)), sync())));

  // ---- استیکرهای تعاملی: بعد از رأی، زمان اسلاید از نو شروع می‌شود تا نتیجه دیده شود ----
  ig.querySelectorAll<HTMLElement>(".poll").forEach((p) =>
    p.addEventListener("click", (e) => {
      if (!(e.target as HTMLElement).closest("button") || p.classList.contains("done")) return;
      p.classList.add("done");
      t = Math.min(t, 1500);
    })
  );
  ig.querySelectorAll<HTMLElement>(".quiz").forEach((q) =>
    q.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest("button");
      if (!b || q.classList.contains("done")) return;
      q.classList.add("done");
      if (!b.hasAttribute("data-ok")) b.classList.add("bad");
      const note = document.createElement("p");
      note.className = "quiz-note";
      note.textContent = b.hasAttribute("data-ok") ? "درست است! این‌ها را در ویزیت‌ها و تماس‌ها با هم مرور می‌کنیم." : "پاسخ درست: کمتر از ۱۳۰. نگران نباشید؛ این‌ها را با هم یاد می‌گیریم.";
      q.append(note);
      t = Math.min(t, 1500);
    })
  );

  // ---- فقط وقتی در دید است پخش شود؛ بار اول راهنمای ضربه نشان داده می‌شود ----
  let hinted = false;
  new IntersectionObserver(
    ([en]) => {
      visible = en.isIntersecting;
      if (visible && !hinted) {
        hinted = true;
        ig.classList.add("hint");
        setTimeout(() => ig.classList.remove("hint"), 1600);
      }
      sync();
    },
    { threshold: 0.55 }
  ).observe(ig);
  document.addEventListener("visibilitychange", sync);

  go(0);
  sync();
}
