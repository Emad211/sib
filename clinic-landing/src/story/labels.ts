import * as THREE from "three";
import gsap from "gsap";

/** center: وسط روی نقطه | right: لبهٔ راست روی نقطه (متن به چپ باز می‌شود) | left: برعکس */
export type Align = "center" | "right" | "left";

interface Item {
  el: HTMLDivElement;
  inner: HTMLDivElement;
  anchor: THREE.Object3D;
  offset: THREE.Vector3;
}

/**
 * برچسب‌های HTML که به اجسام سه‌بعدی چسبیده‌اند.
 * متن فارسی با موتور مرورگر رندر می‌شود: تیز، درست و قابل دسترس برای صفحه‌خوان.
 */
export class Labels {
  private items: Item[] = [];
  private v = new THREE.Vector3();

  constructor(private layer: HTMLElement, private camera: THREE.Camera) {}

  add(html: string, anchor: THREE.Object3D, o: { offset?: [number, number, number]; cls?: string; align?: Align } = {}) {
    const el = document.createElement("div");
    el.className = "lbl";
    const inner = document.createElement("div");
    inner.className = `lbl-in ${o.cls ?? ""}`;
    inner.innerHTML = html;
    el.append(inner);
    this.layer.append(el);
    const xp = o.align === "right" ? -100 : o.align === "left" ? 0 : -50;
    gsap.set(inner, { xPercent: xp, yPercent: -50, autoAlpha: 0 });
    this.items.push({ el, inner, anchor, offset: new THREE.Vector3(...(o.offset ?? [0, 0, 0])) });
    return inner;
  }

  update(w: number, h: number) {
    for (const it of this.items) {
      if (it.inner.style.visibility === "hidden") continue;
      it.anchor.localToWorld(this.v.copy(it.offset)).project(this.camera);
      if (this.v.z > 1) {
        it.el.style.transform = "translate3d(-9999px,0,0)";
        continue;
      }
      const x = (this.v.x * 0.5 + 0.5) * w;
      const y = (-this.v.y * 0.5 + 0.5) * h;
      it.el.style.transform = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0)`;
    }
  }
}
