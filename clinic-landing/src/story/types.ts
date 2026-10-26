export interface Word {
  w: string;
  t0: number;
  t1: number;
}

export interface SceneTiming {
  id: string;
  title: string;
  text: string;
  /** شروع اولین کلمهٔ صحنه */
  t0: number;
  /** پایان آخرین کلمهٔ صحنه */
  t1: number;
  /** صحنه تا این لحظه روی صفحه می‌ماند (شروع صحنهٔ بعد) */
  end: number;
  /** بخش صدای این صحنه: از a0 تا a1 در فایل، پخش در لحظهٔ at داستان */
  audio: { a0: number; a1: number; at: number };
  words: Word[];
}

export interface Timing {
  model: string;
  voiceId: string;
  audio: string;
  duration: number;
  total: number;
  scenes: SceneTiming[];
}

/** زمان کلمه‌ها را برای هماهنگ‌کردن حرکت‌ها با صدای راوی پیدا می‌کند. */
export class Cue {
  constructor(readonly s: SceneTiming) {}
  get t0() {
    return this.s.t0;
  }
  get end() {
    return this.s.end;
  }
  /** لحظهٔ شروع n-امین کلمه‌ای که شامل `sub` است */
  at(sub: string, nth = 0): number {
    let n = 0;
    for (const w of this.s.words) if (w.w.includes(sub) && n++ === nth) return w.t0;
    if (import.meta.env.DEV) console.warn(`[story] cue "${sub}" در صحنهٔ ${this.s.id} پیدا نشد`);
    return this.s.t0;
  }
  /** لحظهٔ پایان n-امین کلمه‌ای که شامل `sub` است */
  after(sub: string, nth = 0): number {
    let n = 0;
    for (const w of this.s.words) if (w.w.includes(sub) && n++ === nth) return w.t1;
    return this.s.t1;
  }
}
