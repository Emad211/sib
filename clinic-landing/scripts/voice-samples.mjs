// تولید نمونه‌صدای کوتاه برای مقایسهٔ گوینده‌ها + بازشنوی خودکار (تبدیل صدا به متن) برای یافتن تلفظ‌های غلط
import { writeFile, mkdir } from "node:fs/promises";
import { loadEnv, tts, transcribe, diffWords } from "./elevenlabs.mjs";

loadEnv();
/** گوینده‌های فارسی‌زبانِ کتابخانهٔ ElevenLabs */
const VOICES = {
  roya: "ndcUYGFbbd96WXiZVVaQ",
  aran: "PEdcOLzbXYhL8IKOswXB",
  niloofar: "N7wuKHXQQ5fKd75noieq",
  connery: "4D8rX5FMrycxXbF6iW0I",
};
const TEXT =
  "این آقای رضاست؛ پنجاه و هشت سال دارد، با دیابت و فشار خون. حالش خوب است... یا فکر می‌کند خوب است. یک روز، فشارش بالا ثبت می‌شود و پرستار ما زنگ می‌زند.";

const only = process.argv.slice(2);
await mkdir("voice-samples", { recursive: true });
for (const [name, id] of Object.entries(VOICES)) {
  if (only.length && !only.includes(name)) continue;
  try {
    const { audio, duration } = await tts({ voiceId: id, text: TEXT });
    await writeFile(`voice-samples/${name}.mp3`, audio);
    const heard = await transcribe(audio);
    const miss = diffWords(TEXT, heard);
    console.log(`${name} ok ${duration.toFixed(2)}s · ${miss.length} mismatch`);
    console.log(`  heard: ${heard}`);
    for (const m of miss) console.log(`  ✗ ${m}`);
  } catch (e) {
    console.log(name, "failed:", e.message);
  }
}
