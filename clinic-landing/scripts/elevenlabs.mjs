// کلاینت کوچک ElevenLabs — فقط در زمان build اجرا می‌شود؛ کلید هرگز به مرورگر نمی‌رسد.
import { readFileSync, existsSync } from "node:fs";

export function loadEnv(file = ".env.local") {
  if (!existsSync(file)) return;
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
  }
}

export const MODEL = "eleven_v4";
export const SETTINGS = { stability: 0.5, similarity_boost: 0.8, style: 0.25, use_speaker_boost: true, speed: 1.1 };

/**
 * متن را به صدا تبدیل می‌کند و زمان‌بندی هر حرف را برمی‌گرداند.
 * previousText/nextText باعث می‌شود لحن جمله‌های پشت‌سرهم پیوسته بماند.
 */
export async function tts({ voiceId, text, previousText, nextText, settings = {}, model = MODEL }) {
  const key = process.env.ELEVENLABS_API_KEY;
  if (!key) throw new Error("ELEVENLABS_API_KEY در .env.local تعریف نشده است");
  const res = await fetch(
    `https://api.elevenlabs.io/v1/text-to-speech/${voiceId}/with-timestamps?output_format=mp3_44100_128`,
    {
      method: "POST",
      headers: { "xi-api-key": key, "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        model_id: model,
        language_code: "fa",
        previous_text: previousText,
        next_text: nextText,
        voice_settings: { ...SETTINGS, ...settings },
      }),
    }
  );
  const body = await res.json();
  if (!res.ok) throw new Error(`${res.status} ${JSON.stringify(body.detail ?? body)}`);
  const a = body.normalized_alignment ?? body.alignment;
  const ends = a?.character_end_times_seconds ?? [];
  return {
    audio: Buffer.from(body.audio_base64, "base64"),
    alignment: a,
    duration: ends.length ? ends[ends.length - 1] : 0,
  };
}

/** صدا را دوباره به متن تبدیل می‌کند تا تلفظ‌های غلط پیدا شوند (ElevenLabs Scribe) */
export async function transcribe(audio) {
  const key = process.env.ELEVENLABS_API_KEY;
  const form = new FormData();
  form.append("model_id", "scribe_v1");
  form.append("language_code", "fa");
  form.append("file", new Blob([audio], { type: "audio/mpeg" }), "a.mp3");
  const res = await fetch("https://api.elevenlabs.io/v1/speech-to-text", { method: "POST", headers: { "xi-api-key": key }, body: form });
  const body = await res.json();
  if (!res.ok) throw new Error(`stt ${res.status} ${JSON.stringify(body.detail ?? body)}`);
  return body.text.trim();
}

/** یکسان‌سازی املای فارسی برای مقایسه: ی/ک عربی، نیم‌فاصله، اعراب و نشانه‌ها */
const norm = (s) =>
  s
    .replace(/[يى]/g, "ی")
    .replace(/ك/g, "ک")
    .replace(/ۀ/g, "ه")
    .replace(/[ً-ٰٟٔ]/g, "")
    .replace(/‌/g, "")
    .replace(/[‏‎]/g, "")
    .replace(/[.,،؛؟!?:;«»"()…-]/g, " ")
    .split(/\s+/)
    .filter(Boolean);

/** واژه‌هایی از متن اصلی که در بازشنوی پیدا نشدند (مقایسهٔ ترتیبی با LCS) */
export function diffWords(expected, heard) {
  const a = norm(expected), b = norm(heard);
  const L = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--) L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < a.length) {
    if (j < b.length && a[i] === b[j]) (i++, j++);
    else if (j < b.length && L[i][j + 1] >= L[i + 1][j]) j++;
    else out.push(`«${a[i]}» ← شنیده: «${b[j] ?? "—"}»`), i++;
  }
  return out;
}
