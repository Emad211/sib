import timingJson from "./timing.json";
import type { Timing } from "./types";
import { hasWebGL, pickTier } from "./webgl";
import { fa } from "./kit/kit";
import { Soundtrack } from "./audio";

const timing = timingJson as Timing;

/**
 * پخش‌کنندهٔ داستان: صدای راوی «ساعت اصلی» است و تایم‌لاین سه‌بعدی، زیرنویس و نوار پیشرفت
 * هر فریم با آن هماهنگ می‌شوند. اگر صدا در دسترس نباشد، ساعت داخلی جایگزین می‌شود.
 * با اسکرول صفحه پخش قطع نمی‌شود: رندر سه‌بعدی بیرون از دید متوقف می‌شود و یک پخش‌کنندهٔ کوچک شناور ظاهر می‌شود.
 */
export async function mountPlayer(root: HTMLElement, opts: { autoplay?: boolean } = {}) {
  const $ = <T extends HTMLElement>(s: string) => root.querySelector<T>(s)!;
  const stage = $(".sp-stage"), canvas = $<HTMLCanvasElement>(".sp-canvas"), labelLayer = $(".sp-labels"), overlay = $(".sp-kt");
  const capText = $(".sp-cap-text"), poster = $(".sp-poster"), toggle = $(".sp-toggle"), track = $(".sp-track");
  const fill = $(".sp-fill"), timeEl = $(".sp-time"), chapterEl = $(".sp-chapter"), muteBtn = $(".sp-mute"), ccBtn = $(".sp-cc");
  const total = timing.total;

  const setState = (s: string) => {
    root.dataset.state = s;
    mini.dataset.state = s;
  };

  // ---------- پخش‌کنندهٔ کوچک شناور (وقتی کاربر از بخش انیمیشن دور شده) ----------
  const mini = document.createElement("div");
  mini.className = "sp-mini";
  mini.setAttribute("role", "region");
  mini.setAttribute("aria-label", "پخش‌کنندهٔ داستان");
  mini.innerHTML = `<button type="button" class="spm-toggle" aria-label="توقف"><svg class="i-play"><use href="#i-play"/></svg><svg class="i-pause"><use href="#i-pause"/></svg></button>
    <button type="button" class="spm-info" aria-label="بازگشت به انیمیشن"><b>داستان آقا رضا</b><span class="spm-ch"></span></button>
    <span class="spm-eq" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="spm-prog" aria-hidden="true"><i></i></span>`;
  document.body.append(mini);
  const miniCh = mini.querySelector<HTMLElement>(".spm-ch")!, miniFill = mini.querySelector<HTMLElement>(".spm-prog i")!;
  setState("loading");

  // ---------- صدا ----------
  const base = import.meta.env.BASE_URL;
  const sound = new Soundtrack(
    `${base}${timing.audio}`,
    timing.scenes.map((s) => s.audio),
    timing.scenes.map((s) => [s.t0, s.t1] as [number, number]),
    total,
    `${base}story/fx/`
  );

  // ---------- صحنهٔ سه‌بعدی (اگر WebGL باشد) ----------
  let story: import("./story").Story | null = null;
  if (hasWebGL()) {
    // متن‌های روی پنل‌ها با فونت وزیرمتن کشیده می‌شوند؛ منتظر فونت می‌مانیم (حداکثر ۲٫۵ ثانیه)
    await Promise.race([
      Promise.all(["500 30px Vazirmatn", "700 30px Vazirmatn", "800 30px Vazirmatn"].map((f) => document.fonts.load(f))),
      new Promise((r) => setTimeout(r, 2500)),
    ]);
    // لوگوی سه‌بعدی بام درمانگاه و تصویر لوگو برای تابلوها
    const img = new Image();
    img.src = `${base}logo.svg`;
    // سایهٔ محیطی (N8AO) فقط روی دسکتاپ دانلود می‌شود
    const tier = pickTier();
    const [{ Story }, { loadLogo }, ao] = await Promise.all([
      import("./story"),
      import("../lib/logo-geo"),
      tier === "high" ? import("n8ao").catch(() => null) : null,
      img.decode().catch(() => {}),
    ]);
    const logo = await loadLogo().catch(() => undefined);
    story = new Story(canvas, labelLayer, overlay, timing, { tier, AO: ao?.N8AOPostPass, logo, logoImg: img.complete && img.naturalWidth ? img : undefined });
    sound.setCues(story.cues);
  } else {
    root.classList.add("no3d");
  }

  // ---------- فصل‌ها روی نوار پیشرفت ----------
  const chapters = $(".sp-chapters");
  chapters.innerHTML = timing.scenes
    .map((s, i) => `<button type="button" class="sp-ch" style="right:${((i === 0 ? 0 : s.t0) / total) * 100}%" data-t="${i === 0 ? 0 : s.t0 - 0.9}" title="${s.title}" aria-label="فصل ${fa(i + 1)}: ${s.title}"></button>`)
    .join("");

  // ---------- زیرنویس: چند کلمه چند کلمه (عبارت‌های کوتاه)، کلمه‌به‌کلمه روشن می‌شود ----------
  /** کلمه‌های هر صحنه به عبارت‌های حداکثر ۴ کلمه‌ای تقسیم می‌شوند؛ بعد از ویرگول و نقطه عبارت می‌شکند */
  const chunks = timing.scenes.map((sc) => {
    const out: { from: number; words: typeof sc.words }[] = [];
    let cur: typeof sc.words = [];
    sc.words.forEach((w, i) => {
      cur.push(w);
      const brk = /[،؛.…!؟:]$/.test(w.w) || cur.length >= 4;
      if (brk || i === sc.words.length - 1) {
        out.push({ from: i - cur.length + 1, words: cur });
        cur = [];
      }
    });
    return out;
  });
  let capKey = "";
  let capWords: HTMLSpanElement[] = [];
  let capWord = -2;
  function captions(t: number) {
    let i = timing.scenes.findIndex((s) => t < s.end);
    if (i < 0) i = timing.scenes.length - 1;
    const s = timing.scenes[i];
    let w = -1;
    for (let j = 0; j < s.words.length; j++) if (t >= s.words[j].t0 - 0.05) w = j;
    const list = chunks[i];
    let c = 0;
    for (let k = 0; k < list.length; k++) if (w >= list[k].from) c = k;
    const key = `${i}:${c}`;
    if (key !== capKey) {
      capKey = key;
      capText.innerHTML = list[c].words.map((x) => `<span>${x.w}</span>`).join(" ");
      capWords = Array.from(capText.children) as HTMLSpanElement[];
      capWord = -2;
      chapterEl.textContent = s.title;
      miniCh.textContent = s.title;
      capText.classList.remove("in");
      void capText.offsetWidth;
      capText.classList.add("in");
    }
    const local = w - list[c].from;
    if (local !== capWord) {
      capWord = local;
      capWords.forEach((el, j) => el.classList.toggle("on", j <= local));
    }
    // بعد از پایان آخرین کلمهٔ هر عبارت، کمی می‌ماند و بعد محو می‌شود
    const last = list[c].words[list[c].words.length - 1];
    capText.parentElement!.classList.toggle("show", t > list[c].words[0].t0 - 0.25 && t < last.t1 + 0.9);
  }

  // ---------- ساعت: صدای راوی مرجع است؛ اگر نبود، ساعت داخلی ----------
  let t = 0, playing = false, started = false, last = performance.now();
  const fmt = (x: number) => fa(`${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, "0")}`);

  async function play() {
    if (t >= total - 0.05) t = 0;
    playing = started = true;
    setState("playing");
    syncMini();
    poster.hidden = true;
    toggle.setAttribute("aria-label", "توقف");
    wake();
    await sound.unlock();
    if (playing && sound.ready) sound.start(t);
  }
  function pause(state = "paused") {
    playing = false;
    sound.stop();
    setState(state);
    toggle.setAttribute("aria-label", "پخش");
    wake();
  }
  function seek(x: number) {
    t = Math.min(Math.max(0, x), total);
    if (playing && sound.ready) sound.start(t);
    if (!playing && root.dataset.state === "ended") setState("paused");
    poster.hidden = true;
    wake();
  }

  // ---------- حلقهٔ رندر: صدا و زمان همیشه جلو می‌روند؛ رندر سه‌بعدی فقط وقتی بخش دیده می‌شود ----------
  let visible = true, raf = 0, frameN = 0;
  const syncMini = () => mini.classList.toggle("show", !visible && started);
  function loop(now: number) {
    raf = 0;
    const dt = (now - last) / 1000;
    last = now;
    if (playing) {
      t = sound.ready ? sound.time() : t + Math.min(dt, 0.1);
      if (t >= total) {
        t = total;
        pause("ended");
      }
    }
    // در حالت توقف، رندر با نیم‌سرعت (صرفه‌جویی باتری)
    // فقط در توسعه: window.__freeze رندر خودکار را نگه می‌دارد تا بتوان نمای دلخواه را بررسی کرد
    const frozen = import.meta.env.DEV && (window as unknown as { __freeze?: boolean }).__freeze;
    if (visible && story && !frozen && (playing || frameN++ % 2 === 0)) {
      story.seek(t);
      story.frame(now / 1000);
      story.engine.sample(dt * 1000);
    }
    captions(t);
    const p = `${(t / total) * 100}%`;
    fill.style.width = p;
    miniFill.style.width = p;
    timeEl.textContent = `${fmt(t)} / ${fmt(total)}`;
    track.setAttribute("aria-valuenow", String(Math.round((t / total) * 100)));
    if (!document.hidden && (visible || playing)) raf = requestAnimationFrame(loop);
  }
  function wake() {
    if (!raf && !document.hidden) {
      last = performance.now();
      raf = requestAnimationFrame(loop);
    }
  }

  // ---------- اندازه ----------
  const ro = new ResizeObserver(() => {
    const r = stage.getBoundingClientRect();
    story?.resize(r.width, r.height);
    stage.style.setProperty("--u", `${r.width / 100}px`);
    wake();
  });
  ro.observe(stage);

  new IntersectionObserver(
    ([e]) => {
      visible = e.isIntersecting;
      syncMini();
      wake();
    },
    { threshold: 0.2 }
  ).observe(root);
  // بازگشت به تب: زمان از ساعت صوتی دوباره خوانده می‌شود
  document.addEventListener("visibilitychange", wake);

  // ---------- کنترل‌ها ----------
  const togglePlay = () => (playing ? pause() : play());
  poster.addEventListener("click", play);
  toggle.addEventListener("click", togglePlay);
  stage.addEventListener("click", (e) => {
    const el = e.target as HTMLElement;
    if (el.closest("[data-act=replay]")) {
      e.stopPropagation();
      t = 0;
      play();
      return;
    }
    if (el.closest("a,button")) return;
    togglePlay();
  });
  mini.querySelector(".spm-toggle")!.addEventListener("click", togglePlay);
  mini.querySelector(".spm-info")!.addEventListener("click", () => root.scrollIntoView({ behavior: "smooth", block: "center" }));
  chapters.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>(".sp-ch");
    if (b) {
      e.stopPropagation();
      seek(+b.dataset.t!);
    }
  });
  const fromPointer = (e: PointerEvent) => {
    const r = track.getBoundingClientRect();
    seek(((r.right - e.clientX) / r.width) * total);
  };
  track.addEventListener("pointerdown", (e) => {
    if ((e.target as HTMLElement).closest(".sp-ch")) return;
    fromPointer(e);
    track.setPointerCapture(e.pointerId);
    track.onpointermove = fromPointer;
  });
  track.addEventListener("pointerup", () => (track.onpointermove = null));

  muteBtn.addEventListener("click", () => {
    sound.setMuted(!sound.muted);
    root.classList.toggle("muted", sound.muted);
    muteBtn.setAttribute("aria-label", sound.muted ? "پخش صدا" : "بی‌صدا");
  });
  ccBtn.addEventListener("click", () => {
    const off = root.classList.toggle("nocc");
    ccBtn.setAttribute("aria-pressed", String(!off));
  });
  root.addEventListener("keydown", (e) => {
    if ((e.target as HTMLElement).matches("input,textarea")) return;
    const k = e.key.toLowerCase();
    if (k === " " || k === "k") {
      e.preventDefault();
      togglePlay();
    } else if (k === "arrowleft") seek(t + 5); // در RTL، چپ یعنی جلو
    else if (k === "arrowright") seek(t - 5);
    else if (k === "m") muteBtn.click();
    else if (k === "c") ccBtn.click();
  });

  setState("paused");
  wake();
  if (opts.autoplay || root.dataset.wantPlay) play();
  return { play, pause, seek, get time() { return t; }, total, get story() { return story; } };
}
