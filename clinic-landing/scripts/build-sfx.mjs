// افکت‌های صوتی و موسیقی زمینهٔ انیمیشن را با ElevenLabs می‌سازد (فقط یک بار؛ فایل موجود دوباره ساخته نمی‌شود).
// اجرا:  npm run sfx            |  npm run sfx -- --force <name>
import { writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { loadEnv } from "./elevenlabs.mjs";

loadEnv();
const key = process.env.ELEVENLABS_API_KEY;
if (!key) throw new Error("ELEVENLABS_API_KEY در .env.local تعریف نشده است");
const OUT = "public/story/fx";

/** افکت‌ها: متن انگلیسی برای مدل صدا، طول به ثانیه */
const SFX = {
  heart: ["Two slow deep heartbeats, cinematic low thump, clean, no music, dry", 1.8],
  whoosh: ["Soft airy cinematic whoosh transition, smooth, short, subtle", 1.0],
  pop: ["Soft bubbly UI pop, friendly, clean, short", 0.5],
  send: ["Pleasant smartphone message sent swoosh with soft ding, clean UI sound", 1.0],
  alert: ["Gentle two-tone warning chime on a smartphone, soft, not harsh", 1.2],
  ring: ["Modern smartphone ringing softly with vibration buzz, two short rings", 2.0],
  tick: ["Soft paper calendar flip with a light tick, satisfying UI sound", 0.6],
  success: ["Warm uplifting success chime, soft bells and sparkle, hopeful, short", 1.8],
};
const MUSIC = {
  prompt:
    "Warm hopeful cinematic ambient underscore for a healthcare explainer video. Starts minimal and slightly tense with soft low piano notes and a subtle pulse, then around 18 seconds becomes gentle and caring with soft strings and light plucked piano, building to an uplifting, bright, reassuring finale near the end. No vocals, no drums kit, calm tempo around 90 bpm, unobtrusive under a voiceover.",
  ms: 76000,
};

const args = process.argv.slice(2);
const force = (n) => args.includes("--force") && (args.length === 1 || args.includes(n));
await mkdir(OUT, { recursive: true });

async function save(name, res) {
  if (!res.ok) throw new Error(`${name}: ${res.status} ${await res.text()}`);
  await writeFile(`${OUT}/${name}.mp3`, Buffer.from(await res.arrayBuffer()));
  console.log("ok", name);
}

for (const [name, [text, dur]] of Object.entries(SFX)) {
  if (existsSync(`${OUT}/${name}.mp3`) && !force(name)) continue;
  await save(
    name,
    await fetch("https://api.elevenlabs.io/v1/sound-generation?output_format=mp3_44100_64", {
      method: "POST",
      headers: { "xi-api-key": key, "Content-Type": "application/json" },
      body: JSON.stringify({ text, duration_seconds: dur, prompt_influence: 0.6 }),
    })
  );
}

if (!existsSync(`${OUT}/music.mp3`) || force("music")) {
  await save(
    "music",
    await fetch("https://api.elevenlabs.io/v1/music?output_format=mp3_44100_64", {
      method: "POST",
      headers: { "xi-api-key": key, "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: MUSIC.prompt, music_length_ms: MUSIC.ms }),
    })
  );
}
