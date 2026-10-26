import { CSS, FONT, rr } from "../kit/kit";

/** کارت عدد روی صفحهٔ گوشی */
export function card(g: CanvasRenderingContext2D, w: number, y: number, label: string, value: string, unit: string, a: number, tone: "ok" | "bad" | "good" = "ok") {
  if (a <= 0) return;
  g.save();
  g.globalAlpha = Math.min(1, a);
  g.translate(0, (1 - Math.min(1, a)) * 30);
  const bg = tone === "bad" ? CSS.redSoft : CSS.mintSoft;
  const fg = tone === "bad" ? CSS.red : CSS.forest;
  g.fillStyle = bg;
  rr(g, 28, y, w - 56, 150, 28);
  g.fill();
  if (tone === "bad") {
    g.strokeStyle = CSS.red;
    g.lineWidth = 5;
    g.stroke();
  }
  g.textAlign = "right";
  g.fillStyle = CSS.muted;
  g.font = `500 30px ${FONT}`;
  g.fillText(label, w - 56, y + 52);
  g.fillStyle = fg;
  g.font = `800 64px ${FONT}`;
  g.fillText(value, w - 56, y + 124);
  g.textAlign = "left";
  g.fillStyle = CSS.muted;
  g.font = `500 24px ${FONT}`;
  g.fillText(unit, 56, y + 120);
  g.restore();
}

/** قاب صفحهٔ گوشی: پس‌زمینه + نوار بالا */
export function screenFrame(g: CanvasRenderingContext2D, w: number, h: number, title: string) {
  g.fillStyle = CSS.paper;
  g.fillRect(0, 0, w, h);
  g.fillStyle = CSS.pine;
  g.fillRect(0, 0, w, 150);
  g.fillStyle = CSS.mint;
  g.textAlign = "right";
  g.font = `700 36px ${FONT}`;
  g.fillText(title, w - 40, 104);
  g.fillStyle = "rgba(255,255,255,.18)";
  g.beginPath();
  g.arc(70, 92, 26, 0, Math.PI * 2);
  g.fill();
}

export function button(g: CanvasRenderingContext2D, w: number, y: number, text: string, a: number, color = CSS.pine) {
  if (a <= 0) return;
  g.save();
  g.globalAlpha = Math.min(1, a);
  g.fillStyle = color;
  rr(g, 28, y, w - 56, 96, 48);
  g.fill();
  g.fillStyle = CSS.white;
  g.textAlign = "center";
  g.font = `700 36px ${FONT}`;
  g.fillText(text, w / 2, y + 62);
  g.restore();
}
