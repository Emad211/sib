import { C, CSS, Panel } from "../kit/kit";
import { drawTube, phone, pulseRing } from "../kit/props";
import { frameAt, stationAngle } from "../world";
import { LEAD, pop, travel, type SceneBuilder } from "../rig";
import { button, card, screenFrame } from "./screens";

/** ۵ — هشدار: فشار بالا، گوشی می‌لرزد، پرتو تماس از درمانگاه به رضا، تنظیم دارو */
const alert: SceneBuilder = (ctx, T, k) => {
  const { tl, labels, reza, world } = ctx;
  const st = ctx.world.stations[k];
  const RX = 1.45, RZ = 0.7;
  travel(ctx, k, T.t0 - LEAD, { x: RX, z: RZ, yaw: -0.5 });

  const panel = new Panel(480, 960, { bad: 0, ok: 0 }, (g, w, h, s) => {
    screenFrame(g, w, h, "پیگیری سلامت");
    if (s.ok > 0) {
      card(g, w, 200, "فشار خون امروز", "۱۷۲/۱۰۴", "mmHg", 1 - s.ok * 0.6, "bad");
      card(g, w, 380, "پیام درمانگاه", "تنظیم دارو", "", s.ok, "ok");
      button(g, w, 600, "تماس انجام شد ✓", s.ok, CSS.teal);
    } else {
      card(g, w, 200, "فشار خون امروز", s.bad > 0.5 ? "۱۷۲/۱۰۴" : "۱۳۰/۸۲", "mmHg", 1, s.bad > 0.5 ? "bad" : "ok");
      if (s.bad > 0.5) {
        g.save();
        g.globalAlpha = Math.min(1, (s.bad - 0.5) * 2);
        g.fillStyle = CSS.red;
        g.textAlign = "center";
        g.font = `800 34px Vazirmatn, sans-serif`;
        g.fillText("خارج از محدودهٔ هدف", w / 2, 420);
        g.restore();
      }
    }
  });
  const ph = phone(panel);
  ph.position.set(-1.05, 1.95, 0.1);
  ph.rotation.y = 0.28;
  st.add(ph);
  ph.scale.setScalar(0.001);
  pop(ctx, ph, T.t0 - 0.3);
  ctx.onFrame((time) => (ph.position.y = 1.95 + Math.sin(time * 1.2) * 0.06));

  const bad = T.at("بالا");
  ctx.sfx("alert", bad + 0.1, 0.55);
  // لبه‌های قرمز قاب: حس خطر
  const red = ctx.kt("", "kt-red");
  tl.to(red, { autoAlpha: 1, duration: 0.35, ease: "power2.out" }, bad);
  tl.to(red, { autoAlpha: 0.45, duration: 0.5, yoyo: true, repeat: 3, ease: "sine.inOut" }, bad + 0.35);
  tl.to(red, { autoAlpha: 0, duration: 0.8 }, T.at("پرستار") + 0.2);
  tl.to(panel.state, { bad: 1, duration: 0.4 }, bad);
  tl.to(ph.rotation, { z: 0.09, duration: 0.06, yoyo: true, repeat: 9, ease: "sine.inOut" }, bad + 0.2);

  // موج‌های قرمز هشدار دور گوشی
  const rings = [0, 1, 2].map(() => pulseRing(C.red, 0.95));
  rings.forEach((r, i) => {
    r.mesh.position.copy(ph.position);
    st.add(r.mesh);
    tl.fromTo(r.state, { t: 0 }, { t: 2, duration: 2.6, ease: "none", onUpdate: r.apply, immediateRender: false }, bad + i * 0.4);
  });

  // پرتو تماس: از تابلوی درمانگاه تا سرِ رضا
  const f = frameAt(stationAngle(k));
  const head = f.pos.clone().addScaledVector(f.right, RX).addScaledVector(f.out, RZ).setY(2.5);
  const port = world.hubPort.clone();
  const peak = port.clone().lerp(head, 0.55).setY(8);
  const beam = drawTube([port, port.clone().lerp(peak, 0.5).setY(7.4), peak, head.clone().setY(4.4), head], 0.045, C.teal, { emissive: true });
  world.root.add(beam.group);
  const call = T.at("پرستار");
  ctx.sfx("ring", T.at("زنگ") - 0.35, 0.28);
  tl.fromTo(beam.state, { p: 0 }, { p: 1, duration: 1.3, ease: "power2.inOut", onUpdate: beam.apply, immediateRender: false }, call - 0.5);
  tl.to(beam.state, { p: 0, duration: 0.6, ease: "power1.in", onUpdate: beam.apply }, T.end - 0.6);
  tl.to(world.hubPulse, { v: 1, duration: 0.2, yoyo: true, repeat: 3 }, call - 0.6);

  // موج سبز تماس دور سر رضا
  const callRings = [0, 1].map(() => pulseRing(C.teal, 0.55));
  callRings.forEach((r, i) => {
    reza.root.add(r.mesh);
    r.mesh.position.set(0, 2.0, 0);
    tl.fromTo(r.state, { t: 0 }, { t: 3, duration: 3.2, ease: "none", onUpdate: r.apply, immediateRender: false }, call + 0.6 + i * 0.5);
    tl.set(r.state, { t: 0, onComplete: r.apply }, T.end);
  });
  tl.to(reza.pose, { nod: 1, duration: 0.3 }, T.at("زنگ") + 0.4);
  tl.to(reza.pose, { nod: 0, duration: 0.3 }, T.at("زنگ") + 1.7);

  const bubble = labels.add(
    `<span class="av">پ</span><span><b>پرستار درمانگاه</b>«سلام آقا رضا، فشارتان بالا ثبت شده؛ داروهایتان را مرتب خورده‌اید؟»</span>`,
    reza.root,
    { offset: [-0.35, 2.75, 0], cls: "l-bubble", align: "right" }
  );
  ctx.showAt(T.at("زنگ") - 0.1, bubble);

  const fix = T.at("تنظیم");
  ctx.sfx("success", fix, 0.4);
  tl.to(panel.state, { ok: 1, duration: 0.6 }, fix - 0.2);
  const okL = labels.add("تنظیم دارو با نظر پزشک ✓", ph, { offset: [0, -1.4, 0.2], cls: "l-chip l-ok" });
  ctx.showAt(fix, okL);
  ctx.hideAt(T.end - 0.5, bubble, okL);
};
export default alert;
