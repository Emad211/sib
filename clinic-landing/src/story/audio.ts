import type { SceneTiming } from "./types";

/** یک افکت صوتی در لحظهٔ مشخصی از داستان */
export interface SfxCue {
  name: string;
  at: number;
  gain: number;
}

const FX = ["heart", "whoosh", "pop", "send", "alert", "ring", "tick", "success"] as const;
export type FxName = (typeof FX)[number];

/** بلندی موسیقی: زیر صدای راوی پایین می‌آید (داکینگ) و در مکث‌ها بالا می‌رود */
const MUSIC_UP = 0.3;
const MUSIC_DUCK = 0.11;

/**
 * صدای انیمیشن با Web Audio: راوی، موسیقی زمینه و افکت‌ها.
 * فایل راوی یک‌بار دیکد می‌شود و هر بخش (جملهٔ هر صحنه) در زمان دقیق خودش زمان‌بندی می‌شود؛
 * ساعت AudioContext مرجع زمانِ کل انیمیشن است. با هر جابه‌جایی (seek) همه‌چیز از نو زمان‌بندی می‌شود.
 */
export class Soundtrack {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private voice: AudioBuffer | null = null;
  private music: AudioBuffer | null = null;
  private fx = new Map<string, AudioBuffer>();
  private sources: AudioScheduledSourceNode[] = [];
  private musicGain: GainNode | null = null;
  private bytes: Promise<ArrayBuffer | null>;
  private extra: Promise<[string, ArrayBuffer | null][]>;
  private cues: SfxCue[] = [];
  private base = 0; // ctx.currentTime منهای زمان داستان
  muted = false;
  ok = true;

  constructor(
    url: string,
    private segments: SceneTiming["audio"][],
    private speech: [number, number][],
    private total: number,
    fxBase: string
  ) {
    const get = (u: string) =>
      fetch(u)
        .then((r) => (r.ok ? r.arrayBuffer() : null))
        .catch(() => null);
    // مکث‌های کوتاه بین جمله‌ها موسیقی را بالا نمی‌آورند؛ بازه‌های نزدیک یکی می‌شوند
    const merged: [number, number][] = [];
    for (const [a, b] of speech) {
      const last = merged[merged.length - 1];
      if (last && a - last[1] < 1.6) last[1] = b;
      else merged.push([a, b]);
    }
    this.speech = merged;
    // دانلود از همین حالا (بدون نیاز به تعامل کاربر)
    this.bytes = get(url);
    this.extra = Promise.all(["music", ...FX].map(async (n) => [n, await get(`${fxBase}${n}.mp3`)] as [string, ArrayBuffer | null]));
  }

  setCues(cues: SfxCue[]) {
    this.cues = cues.slice().sort((a, b) => a.at - b.at);
  }

  /** باید در پاسخ به کلیک کاربر صدا زده شود (سیاست autoplay مرورگرها) */
  async unlock() {
    if (!this.ctx) {
      const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.ctx = new AC({ latencyHint: "playback" });
      this.master = this.ctx.createGain();
      this.master.connect(this.ctx.destination);
      this.master.gain.value = this.muted ? 0 : 1;
    }
    if (this.ctx.state === "suspended") await this.ctx.resume().catch(() => {});
    if (!this.voice && this.ok) {
      const decode = async (b: ArrayBuffer | null) => (b ? this.ctx!.decodeAudioData(b.slice(0)).catch(() => null) : null);
      const [voice, extra] = await Promise.all([this.bytes.then(decode), this.extra]);
      this.voice = voice;
      if (!voice) this.ok = false;
      // موسیقی و افکت‌ها اختیاری‌اند؛ اگر نرسیدند، انیمیشن بی‌آن‌ها پخش می‌شود
      await Promise.all(
        extra.map(async ([n, b]) => {
          const buf = await decode(b);
          if (!buf) return;
          if (n === "music") this.music = buf;
          else this.fx.set(n, buf);
        })
      );
    }
    return this.ok;
  }

  get ready() {
    return !!(this.ctx && this.voice && this.ctx.state === "running");
  }

  /** زمان فعلی داستان بر اساس ساعت صوتی */
  time() {
    return this.ctx ? this.ctx.currentTime - this.base : 0;
  }

  private play(buf: AudioBuffer, when: number, offset: number, dur: number | undefined, out: AudioNode, gain = 1) {
    const ctx = this.ctx!;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    let node: AudioNode = src;
    if (gain !== 1) {
      const g = ctx.createGain();
      g.gain.value = gain;
      src.connect(g);
      node = g;
    }
    node.connect(out);
    src.start(when, offset, dur);
    this.sources.push(src);
  }

  /** پخش از زمان t داستان: همهٔ بخش‌های باقی‌مانده زمان‌بندی می‌شوند */
  start(t: number) {
    this.stop();
    const ctx = this.ctx, buf = this.voice, out = this.master;
    if (!ctx || !buf || !out) return;
    const now = ctx.currentTime + 0.03;
    this.base = now - t;
    const at = (storyT: number) => this.base + storyT;

    // راوی
    for (const s of this.segments) {
      const len = s.a1 - s.a0;
      const into = t - s.at;
      if (into >= len) continue;
      const skip = Math.max(0, into);
      this.play(buf, now + Math.max(0, -into), s.a0 + skip, Math.min(len - skip, buf.duration - s.a0 - skip), out);
    }

    // موسیقی زمینه با داکینگ زیر صدای راوی
    if (this.music && t < this.music.duration - 0.1) {
      const g = (this.musicGain = ctx.createGain());
      g.connect(out);
      const level = (x: number) => (this.speech.some(([a, b]) => x > a - 0.25 && x < b + 0.2) ? MUSIC_DUCK : MUSIC_UP);
      g.gain.setValueAtTime(0, now);
      g.gain.linearRampToValueAtTime(level(t), now + (t < 0.1 ? 1.2 : 0.35));
      for (const [a, b] of this.speech) {
        if (b < t) continue;
        if (a - 0.35 > t) {
          g.gain.setValueAtTime(MUSIC_UP, at(a - 0.35));
          g.gain.linearRampToValueAtTime(MUSIC_DUCK, at(a - 0.05));
        }
        g.gain.setValueAtTime(MUSIC_DUCK, at(Math.max(t + 0.4, b + 0.1)));
        g.gain.linearRampToValueAtTime(MUSIC_UP, at(Math.max(t + 0.8, b + 0.6)));
      }
      const fade = Math.max(t + 0.5, this.total - 2.5);
      g.gain.setValueAtTime(MUSIC_UP, at(fade));
      g.gain.linearRampToValueAtTime(0, at(this.total));
      this.play(this.music, now, t, undefined, g);
    }

    // افکت‌ها
    for (const c of this.cues) {
      if (c.at < t - 0.05) continue;
      const b = this.fx.get(c.name);
      if (b) this.play(b, at(c.at), 0, undefined, out, c.gain);
    }
  }

  stop() {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        /* قبلاً تمام شده */
      }
      s.disconnect();
    }
    this.sources = [];
    this.musicGain?.disconnect();
    this.musicGain = null;
  }

  setMuted(m: boolean) {
    this.muted = m;
    if (this.master && this.ctx) this.master.gain.setTargetAtTime(m ? 0 : 1, this.ctx.currentTime, 0.03);
  }
}
