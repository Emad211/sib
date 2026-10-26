import type { Engine } from "./engine";
import type { World } from "./world";
import type { Labels } from "./labels";
import type { Character } from "./kit/character";
import type { Cue } from "./types";
import type { FxName, SfxCue } from "./audio";
import { R, stationAngle } from "./world";

/** پارامترهای دوربین: موقعیت روی دایره (a, r, y) و نقطهٔ هدف (ta, tr, ty) */
export interface Shot {
  a: number; r: number; y: number;
  ta: number; tr: number; ty: number;
  fov: number;
}

export const SHOT = {
  station: (k: number, o: Partial<Shot> = {}): Shot => ({
    a: stationAngle(k) + 0.06, r: R + 10.2, y: 3.3, ta: stationAngle(k), tr: R, ty: 1.2, fov: 30, ...o,
  }),
  overview: (a: number, o: Partial<Shot> = {}): Shot => ({ a, r: R + 21, y: 21, ta: a, tr: 0, ty: 0, fov: 38, ...o }),
};

/** وضعیت رضا روی حلقه: زاویه، جابه‌جایی محلی (x راست/چپ، z جلو)، شدت راه‌رفتن، چرخش اضافه */
export interface Walker { a: number; x: number; z: number; walk: number; yaw: number }

export interface Ctx {
  engine: Engine;
  world: World;
  labels: Labels;
  reza: Character;
  walker: Walker;
  cam: Shot;
  tl: gsap.core.Timeline;
  /** لایهٔ HTML تمام‌قاب روی صحنه برای تایپوگرافی متحرک */
  overlay: HTMLElement;
  /** افکت‌های صوتی زمان‌بندی‌شده */
  cues: SfxCue[];
  onFrame(fn: (time: number) => void): void;
  hideAt(t: number, ...els: HTMLElement[]): void;
  showAt(t: number, el: HTMLElement, o?: gsap.TweenVars): void;
  /** پخش افکت صوتی در لحظهٔ t داستان */
  sfx(name: FxName, t: number, gain?: number): void;
  /** یک عنصر روی لایهٔ تایپوگرافی؛ در ابتدا پنهان است */
  kt(html: string, cls: string): HTMLElement;
}

/** k: شمارهٔ ایستگاه روی حلقه (برای صحنهٔ قلاب که ایستگاه ندارد، ۱-) */
export type SceneBuilder = (ctx: Ctx, T: Cue, k: number) => void;

/** طول جابه‌جایی بین ایستگاه‌ها و اینکه چقدر قبل از روایت شروع شود (ثانیه) */
export const MOVE = 1.9;
export const LEAD = 1.0;

/** حرکت دوربین و رضا به ایستگاه k */
export function travel(ctx: Ctx, k: number, t: number, to: Partial<Walker> = {}, shot: Partial<Shot> = {}) {
  const a = stationAngle(k);
  const { fov, ...pos } = SHOT.station(k, shot);
  ctx.tl.to(ctx.cam, { ...pos, duration: MOVE, ease: "power3.inOut" }, t);
  // «ضربهٔ» لنز در میانهٔ حرکت: حس سرعت، بدون هزینهٔ پردازش
  ctx.tl.to(ctx.cam, { fov: fov + 7, duration: MOVE * 0.45, ease: "sine.in" }, t);
  ctx.tl.to(ctx.cam, { fov, duration: MOVE * 0.55, ease: "sine.out" }, t + MOVE * 0.45);
  ctx.sfx("whoosh", t + 0.15, 0.55);
  ctx.tl.to(ctx.walker, { a, x: to.x ?? 0, z: to.z ?? 0.4, duration: MOVE * 0.95, ease: "sine.inOut" }, t);
  ctx.tl.to(ctx.walker, { yaw: 0, duration: 0.4 }, t);
  ctx.tl.to(ctx.walker, { walk: 1, duration: 0.35, ease: "sine.out" }, t);
  ctx.tl.to(ctx.walker, { walk: 0, duration: 0.45, ease: "sine.in" }, t + MOVE * 0.95 - 0.4);
  if (to.yaw !== undefined) ctx.tl.to(ctx.walker, { yaw: to.yaw, duration: 0.6 }, t + MOVE * 0.85);
}

/** ظاهرشدن «پاپ» یک شیء سه‌بعدی */
export function pop(ctx: Ctx, o: { scale: { x: number; y: number; z: number } }, t: number, s = 1, dur = 0.7, sound = true) {
  if (sound) ctx.sfx("pop", t, 0.45);
  ctx.tl.fromTo(o.scale, { x: 0.001, y: 0.001, z: 0.001 }, { x: s, y: s, z: s, duration: dur, ease: "back.out(1.8)", immediateRender: false }, t);
}
