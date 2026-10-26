// صدای روایت را از روی src/story/narration.json می‌سازد.
// کل متن در یک درخواست فرستاده می‌شود تا لحن پیوسته بماند. بعد با زمان‌بندی حرف‌به‌حرف،
// فایل صدا به «بخش»های هر صحنه تقسیم می‌شود و پخش‌کننده بین صحنه‌ها مکث می‌گذارد
// (بدون نیاز به برش فایل). خروجی: public/story/narration.mp3 و src/story/timing.json
//
// اجرا:  npm run voice                 (اگر متن/گوینده عوض نشده باشد، از کش استفاده می‌کند)
//        npm run voice -- --force      (ساخت دوباره حتی با کش)
//        npm run voice -- --voice <id>
import { readFile, writeFile, mkdir, readdir, unlink, rename } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { loadEnv, tts, transcribe, diffWords, MODEL, SETTINGS } from "./elevenlabs.mjs";

// زمان‌بندی روایت (ثانیه)
const INTRO = 0.6; // سکوت ابتدای فیلم — کوتاه، تا قلاب اول زود برسد
const GAP = 0.75; // مکث بین صحنه‌ها
const TAIL = 3.5; // ماندن صحنهٔ آخر پس از پایان صدا

loadEnv();
const args = process.argv.slice(2);
const narration = JSON.parse(await readFile("src/story/narration.json", "utf8"));
const voiceId = args.includes("--voice") ? args[args.indexOf("--voice") + 1] : narration.voice.id;

const SEP = "\n\n";
let full = "";
const ranges = narration.scenes.map((s) => {
  const start = full.length;
  full += s.text;
  const end = full.length;
  full += SEP;
  return { start, end };
});
full = full.slice(0, -SEP.length);

// ---------- تولید یا خواندن از کش ----------
const hash = createHash("sha1").update(JSON.stringify([full, voiceId, MODEL, SETTINGS])).digest("hex").slice(0, 12);
const cacheDir = "voice-cache";
const cacheJson = `${cacheDir}/${hash}.json`, cacheMp3 = `${cacheDir}/${hash}.mp3`;
let audio, alignment;
if (existsSync(cacheJson) && !args.includes("--force")) {
  console.log(`cache ${hash}`);
  audio = await readFile(cacheMp3);
  alignment = JSON.parse(await readFile(cacheJson, "utf8"));
} else {
  console.log(`model=${MODEL} voice=${voiceId} chars=${full.length}`);
  ({ audio, alignment } = await tts({ voiceId, text: full }));
  await mkdir(cacheDir, { recursive: true });
  await writeFile(cacheMp3, audio);
  await writeFile(cacheJson, JSON.stringify(alignment));
}

const chars = alignment.characters.join("");
if (chars !== full) console.warn("⚠ متن برگشتی با متن ارسالی یکسان نیست؛ زمان‌بندی تقریبی است.");
const S = alignment.character_start_times_seconds, E = alignment.character_end_times_seconds;
const duration = E[E.length - 1];
const at = (arr, i) => arr[Math.min(i, arr.length - 1)];
const r3 = (x) => Math.round(x * 1000) / 1000;

// ---------- بخش‌بندی صدا: مرز هر بخش وسط سکوتِ بین دو صحنه ----------
const n = narration.scenes.length;
const speechStart = ranges.map((r) => at(S, r.start));
const speechEnd = ranges.map((r) => at(E, r.end - 1));
const a0 = ranges.map((_, k) => (k === 0 ? 0 : (speechEnd[k - 1] + speechStart[k]) / 2));
const a1 = ranges.map((_, k) => (k < n - 1 ? a0[k + 1] : duration + 0.3));
const shift = ranges.map((_, k) => INTRO + k * GAP); // زمان داستان = زمان صدا + shift

const scenes = narration.scenes.map((s, k) => {
  const { start } = ranges[k];
  const words = [];
  const re = /\S+/g;
  let m;
  while ((m = re.exec(s.text))) {
    const i = start + m.index, j = i + m[0].length - 1;
    words.push({ w: m[0], t0: r3(at(S, i) + shift[k]), t1: r3(at(E, j) + shift[k]) });
  }
  return {
    id: s.id,
    title: s.title,
    text: s.text,
    t0: r3(speechStart[k] + shift[k]),
    t1: r3(speechEnd[k] + shift[k]),
    end: 0,
    // بخش صدا: از a0 تا a1 در فایل، در لحظهٔ at داستان پخش می‌شود
    audio: { a0: r3(a0[k]), a1: r3(a1[k]), at: r3(a0[k] + shift[k]) },
    words,
  };
});
scenes.forEach((s, k) => (s.end = r3(k < n - 1 ? scenes[k + 1].t0 - 0.15 : s.t1 + TAIL)));

// نام فایل صدا شامل هش است تا مرورگر و CDN هرگز نسخهٔ قدیمی را پخش نکنند
const audioName = `narration-${hash}.mp3`;
await mkdir("public/story", { recursive: true });
for (const f of await readdir("public/story")) if (/^narration.*\.mp3$/.test(f) && f !== audioName) await unlink(`public/story/${f}`);
await writeFile(`public/story/${audioName}`, audio);
// صدای راوی تک‌کاناله است؛ اگر ffmpeg نصب باشد، فایل با ۶۴kbps مونو نصف حجم می‌شود (کیفیت گفتار یکسان)
const ff = spawnSync("ffmpeg", ["-v", "error", "-y", "-i", `public/story/${audioName}`, "-ac", "1", "-b:a", "64k", "public/story/tmp.mp3"]);
if (ff.status === 0) await rename("public/story/tmp.mp3", `public/story/${audioName}`);
else console.log("ffmpeg پیدا نشد؛ فایل صدا با کیفیت اصلی ذخیره شد");
await writeFile(
  "src/story/timing.json",
  JSON.stringify({ model: MODEL, voiceId, audio: `story/${audioName}`, duration: r3(duration), total: scenes.at(-1).end, scenes }, null, 1)
);
console.log(`ok · ${duration.toFixed(1)}s speech · ${scenes.at(-1).end.toFixed(1)}s story · ${n} scenes`);
for (const s of scenes) console.log(`  ${s.id.padEnd(9)} ${s.t0.toFixed(2)} → ${s.end.toFixed(2)}`);

// ---------- بازشنوی: صدا دوباره به متن تبدیل می‌شود تا تلفظ‌های غلط دیده شوند ----------
if (!args.includes("--no-check")) {
  try {
    const heard = await transcribe(audio);
    const miss = diffWords(full, heard);
    console.log(`check · ${miss.length} mismatch (عددها ممکن است به‌صورت رقم شنیده شوند)`);
    for (const m of miss) console.log(`  ✗ ${m}`);
  } catch (e) {
    console.log("check skipped:", e.message);
  }
}
