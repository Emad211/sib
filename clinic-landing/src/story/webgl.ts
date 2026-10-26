/** سطح کیفیت: high = دسکتاپ (سایهٔ محیطی + سایهٔ ۲۰۴۸)، low = موبایل و دستگاه کند */
export type Tier = "high" | "low";

export function pickTier(): Tier {
  const coarse = matchMedia("(pointer: coarse)").matches;
  const small = Math.min(screen.width, screen.height) < 700;
  const weak = (navigator.hardwareConcurrency || 4) <= 4;
  return coarse || small || weak ? "low" : "high";
}

export function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}
