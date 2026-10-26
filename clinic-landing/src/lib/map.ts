import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// شکل‌دهی درست متن راست‌به‌چپ (اتصال حروف و ترتیب فارسی) روی نقشه
// فایل افزونه از node_modules/@mapbox/mapbox-gl-rtl-text به public/vendor کپی شده (پکیج آن را export نمی‌کند)
const rtlPlugin = `${location.origin}${import.meta.env.BASE_URL}vendor/mapbox-gl-rtl-text.js`;

if (maplibregl.getRTLTextPluginStatus() === "unavailable") maplibregl.setRTLTextPlugin(rtlPlugin, false).catch(() => {});

/** مختصات درمانگاه سیب (از لینک گوگل‌مپ) */
const CLINIC: [number, number] = [54.4308881, 36.8495077];

/**
 * نقشهٔ سه‌بعدی (MapLibre + کاشی‌های رایگان OpenFreeMap، بدون کلید API):
 * نمای مایل با ساختمان‌های سه‌بعدی، چرخش آرام تا وقتی کاربر دست نزده.
 * اسکرول صفحه گیر نمی‌کند: زوم با چرخ موس خاموش است و با دکمه‌ها یا دو انگشت انجام می‌شود.
 */
export function mountMap(el: HTMLElement) {
  const map = new maplibregl.Map({
    container: el,
    style: "https://tiles.openfreemap.org/styles/liberty",
    center: CLINIC,
    zoom: 16.4,
    pitch: 58,
    bearing: -25,
    maxPitch: 70,
    scrollZoom: false,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left");

  const pin = document.createElement("div");
  pin.className = "map-pin";
  pin.innerHTML = `<span class="map-pin-logo"></span><b>درمانگاه سیب</b>`;
  new maplibregl.Marker({ element: pin, anchor: "bottom" }).setLngLat(CLINIC).addTo(map);

  // چرخش آرام دور درمانگاه تا اولین تعامل کاربر
  let spin = true;
  const stop = () => (spin = false);
  ["mousedown", "touchstart", "wheel", "dragstart"].forEach((e) => map.on(e as "mousedown", stop));
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const turn = () => {
    if (!spin || reduce) return;
    map.easeTo({ bearing: map.getBearing() + 12, duration: 3000, easing: (t) => t });
  };
  map.on("load", turn);
  map.on("moveend", turn);
}
