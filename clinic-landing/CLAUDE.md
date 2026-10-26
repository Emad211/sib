# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-page, Persian (RTL) marketing landing page for a clinic's diabetes & hypertension follow-up program ("درمانگاه شبانه‌روزی سیب"). Vite + vanilla TypeScript (no framework), three.js + GSAP for a narrated 3D story ("the story of آقا رضا", ~78s) and a 3D hero logo, plus a phone-number lead form. Deployed as static files on Vercel. User-facing copy, code comments and README are in Persian — keep that convention. Brand line: «بین دو ویزیت، حواسمان به شما هست.» The narration is deliberately conversational spoken Tehrani (محاوره) and always says «آقا رضا»; page copy is standard written Persian. Headings and body both use Vazirmatn (Noto Kufi was dropped because its letterforms, e.g. «دو», looked wrong); never use letter-spacing on Persian text — it breaks letter joins.

## Commands

```bash
npm install
npm run dev            # Vite dev server on port 5173
npm run build          # tsc --noEmit (type-check) then vite build → dist/
npm run preview        # serve dist/
npm run voice          # regenerate narration audio + word timings via ElevenLabs (needs .env.local); re-transcribes to flag mispronounced words
npm run voice -- --force | --voice <voiceId> | --no-check
npm run voice:samples  # sample the Persian library voices into voice-samples/ with auto pronunciation check
npm run sfx            # generate missing sound effects + music bed into public/story/fx/ (-- --force <name> to redo one)
npm run bake:logo      # re-bake public/hero-logo.bin from public/logo3d.svg
```

There are no tests and no linter; `npm run build` (strict TS with `noUnusedLocals`/`noUnusedParameters`) is the only check. In dev, the story player is exposed as `window.__player` (`play/pause/seek/time/total/story`). Set `window.__freeze = true` to stop the render loop, then move `__player.story.ctx.cam` and call `__player.story.frame(t)` to inspect a close-up. ElevenLabs calls cost credits (Starter plan, 40k/month); `voice` reuses `voice-cache/` when text/voice/settings are unchanged (titles are not part of the hash).

## Architecture

**Page shell** — `index.html` holds all static content (icon sprite at top; icons are `<use href="#i-…">`). `src/main.ts` fills clinic info from `src/config.ts` into `.js-name` / `.js-tel` / `.js-tel-btn`, binds `.js-lead` forms, reveals `.rv` sections (and runs `[data-count]` count-ups), starts the care-ring (`src/lib/ring.ts`, "روال کار": the section is tall and its `.how-pin` child is `position: sticky`; scroll progress through the section drives steps ۱–۶, clicks scroll to the matching position), and lazy-loads the heavy parts:
- `#hero3d`: `import("./hero3d")` right after load. The logo geometry is pre-baked by `scripts/bake-logo.mjs` (SVG paths → simplified outline → extrude → int16 positions/int8 normals) into `public/hero-logo.bin`, preloaded in `<head>`; the browser never parses the SVG. Shaders are compiled with `compileAsync` before the canvas fades in over the CSS fallback.
- `#sp` (story player): `import("./story/player")` only when within 700px of the viewport (or on poster click).
- `vite.config.ts` puts `three` and `gsap` into separate chunks. Don't import three/gsap from `main.ts`.

**Lead form** (`src/lib/lead.ts`) — normalizes Iranian mobiles (Persian/Arabic digits, `+98`, `0098`) to `09xxxxxxxxx`. POSTs JSON to `CONFIG.leadEndpoint`; if that is empty (current state), leads are only saved to the visitor's `localStorage` — i.e. there is no backend yet.

**Narrated story — audio is the master clock.** The pipeline:
1. `src/story/narration.json` — scene ids, titles, narration text, voice id (Roya, a native Persian library voice; English voices mispronounce Persian).
2. `scripts/build-voice.mjs` sends the full text in one ElevenLabs request (with character timestamps), writes `public/story/narration-<hash>.mp3` (old ones deleted; re-encoded to 64k mono if ffmpeg exists) and `src/story/timing.json` (per-scene `t0/t1/end`, audio segment `{a0,a1,at}`, per-word `t0/t1`). Inter-scene gaps (`INTRO`, `GAP`, `TAIL`) are inserted at playback time. The API key lives only in `.env.local`.
3. `src/story/audio.ts` (`Soundtrack`) decodes voice, music bed and SFX with Web Audio. On every `start(t)` it reschedules voice segments, the music (ducked under speech intervals) and all SFX cues at/after `t`, so seeking stays sample-accurate. `AudioContext.currentTime` drives story time.
4. `src/story/player.ts` runs the rAF loop: reads time → `story.seek(t)` → `story.frame()`, captions shown as short phrases (≤4 words, breaking at punctuation) lit word by word, chapters, keyboard shortcuts. Playback does **not** stop when scrolled out of view — 3D rendering stops, and a floating mini-player (`.sp-mini`, appended to `body`) appears. No fullscreen by design.
5. `src/story/story.ts` (`Story`) builds one paused GSAP timeline of length `timing.total` that is *seeked*, never played. Scene `hook` gets station `-1` (no ring station); the rest map to ring stations 0–7 in order. `SCENES` keys in `scenes/index.ts` must equal `narration.json` ids.

**Scene builders** (`src/story/scenes/*.ts`, `SceneBuilder = (ctx, T, k)` in `rig.ts`) add tweens to `ctx.tl` at times anchored to narration words via `Cue`: `T.at("word")` / `T.after("word")` (substring match on the nth word; warns in DEV and falls back to scene start). If narration text changes, cue words that disappear must be updated in the scene files. Helpers: `travel()` (camera + Reza to station k, with FOV punch and whoosh), `pop()` (with pop sound), `ctx.showAt/hideAt` for 3D-anchored HTML labels, `ctx.kt(html, cls)` for full-frame kinetic-typography overlays (`.sp-kt` layer: hook text, red alert frame, end card), `ctx.sfx(name, t, gain)` for sound cues, `ctx.onFrame` for idle motion. Use `fromTo(..., immediateRender: false)` for anything that must look right when seeking backwards.

**Rendering** — `engine.ts` renders through pmndrs `postprocessing` (RenderPass → optional N8AO ambient occlusion → bloom/vignette/neutral tone mapping → SMAA); renderer tone mapping is off because the composer does it. `webgl.ts` `pickTier()` picks `high` (desktop: AO, 2048 shadow map, DPR ≤2) or `low` (touch/small/≤4 cores: no AO, 1024 shadows); `n8ao` is dynamically imported only for `high`. `Engine.sample()` degrades adaptively (AO off first, then DPR). Directional-light shadows follow the camera look-at via `world.focus()` so the shadow frustum stays tight.

**World & kit** — `world.ts`: ring of radius `R` with `N = 8` stations; ring progress shader uniform `uP`; gradient sky dome + stars, two procedural mountain rings, vertex-coloured ground, instanced round trees/cypresses/bushes with shader wind sway, and the «درمانگاه سیب» building with facade sign panels and the 3D apple logo (from `hero-logo.bin`, loaded by `src/lib/logo-geo.ts` and passed in as `WorldAssets`) rotating on the roof as `hubSign`. `world.mood.v` (0 night → 1 day) drives sky/fog/lights, glowing windows/logo and stars (the hook runs at night). `kit/kit.ts`: palette `C`, materials (cached by `mat()`), `Panel` canvas textures drawn in Vazirmatn, `fa()` Persian digits. `kit/character.ts`: detailed procedural `reza`/`nurse` (lathe-profiled torso/coat, eyes with blinking lids, glasses/mustache for Reza) with pose params; the nurse headscarf is split into head-parented hood and body-parented cape so nodding never clips. `labels.ts`: HTML labels projected onto 3D anchors.

## Gotchas

- RTL everywhere: progress bar fills from the right; ← seeks forward, → back; charts read right-to-left.
- Story panels wait (≤2.5s) for Vazirmatn before drawing canvas text.
- `src/config.ts` needs a real `leadEndpoint` before launch.
- `vercel.json`: immutable caching for `/assets/*`, 1-week cache for `/story/*`. Narration is content-hashed; `story/fx/*.mp3` are not, so regenerated SFX/music may be served stale for up to a week.
- Medical stats in the "چرا پیگیری" section are sourced (WHO 2023, UKPDS/BMJ 2000, Lancet 2016) — keep a citation if you change them.
- The built-in browser pane is often a hidden document: rAF is throttled there, so screenshots of the story/hero lag one frame behind `__player.seek()`. Take a throwaway screenshot first.
- `.vercelignore` excludes `scripts/`, `.env*`, `README.md`; `.gitignore` excludes `.env.local`, `voice-cache`, `voice-samples`, `dist`, `.vercel`.
