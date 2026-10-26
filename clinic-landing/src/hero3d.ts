import * as THREE from "three";
import { loadLogo } from "./lib/logo-geo";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";

/** لوگوی درمانگاه به‌صورت جسم سه‌بعدی براق، با ضربان ECG و مدارهای ظریف پشت آن. هندسهٔ لوگو از قبل در hero-logo.bin پخته شده (scripts/bake-logo.mjs). */
function ecgPoints(): THREE.Vector3[] {
  const p: [number, number][] = [[-6, 0], [-4.2, 0], [-3.7, 0.25], [-3.2, 0], [-2.4, 0], [-2.0, -0.35], [-1.5, 1.7], [-1.0, -1.1], [-0.6, 0], [0.6, 0], [1.2, 0.5], [1.8, 0], [3.2, 0], [3.6, 0.2], [4, 0], [6, 0]];
  return p.map(([x, y]) => new THREE.Vector3(x, y, 0));
}

export async function mountHero(host: HTMLElement) {
  const canvas = host.querySelector("canvas")!;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.85;
  const scene = new THREE.Scene();
  const pm = new THREE.PMREMGenerator(renderer);
  scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture;
  const cam = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
  cam.position.set(0, 0.3, 15);

  const key = new THREE.DirectionalLight(0xffffff, 1.0);
  key.position.set(4, 6, 8);
  scene.add(key, new THREE.AmbientLight(0xffffff, 0.35));

  // ---- لوگو ----
  const group = new THREE.Group();
  scene.add(group);
  const [dark, light] = await loadLogo();
  const logo = new THREE.Group();
  const bodyMat = new THREE.MeshPhysicalMaterial({ color: 0x0f7a45, roughness: 0.3, metalness: 0.1, clearcoat: 1, clearcoatRoughness: 0.1, envMapIntensity: 0.7 });
  logo.add(new THREE.Mesh(dark, bodyMat));
  logo.add(new THREE.Mesh(light, new THREE.MeshPhysicalMaterial({ color: 0xe0f4e6, roughness: 0.15, clearcoat: 1, transparent: true, opacity: 0.92 })));
  const LS = 8.6 / 1940;
  logo.scale.set(LS, -LS, LS);
  logo.position.z = -0.4;
  const apple = new THREE.Group();
  apple.add(logo);
  group.add(apple);

  // ---- مدارها ----
  const ringMat = new THREE.MeshBasicMaterial({ color: 0x1b9157, transparent: true, opacity: 0.28 });
  const rings: THREE.Mesh[] = [];
  const orbs: { m: THREE.Mesh; r: number; s: number; o: number }[] = [];
  [
    { r: 4.4, tilt: [1.15, 0.25], s: 0.35, c: 0x7fe3a6 },
    { r: 5.3, tilt: [1.35, -0.5], s: -0.22, c: 0xe6ab3c },
  ].forEach((d, i) => {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(d.r, 0.012, 8, 160), ringMat);
    ring.rotation.set(d.tilt[0], d.tilt[1], 0);
    group.add(ring);
    rings.push(ring);
    const m = new THREE.Mesh(
      new THREE.SphereGeometry(0.17 - i * 0.03, 24, 24),
      new THREE.MeshPhysicalMaterial({ color: d.c, roughness: 0.2, clearcoat: 1, emissive: d.c, emissiveIntensity: 0.25 })
    );
    ring.add(m);
    orbs.push({ m, r: d.r, s: d.s, o: i * 2.1 });
  });

  // ---- ECG ----
  const curve = new THREE.CatmullRomCurve3(ecgPoints(), false, "catmullrom", 0.05);
  const tube = new THREE.TubeGeometry(curve, 400, 0.045, 8, false);
  const ecg = new THREE.Mesh(tube, new THREE.MeshBasicMaterial({ color: 0x0f5a38, transparent: true, opacity: 0.9 }));
  ecg.position.set(0, -0.6, -1.8);
  group.add(ecg);
  const idxTotal = tube.index!.count;

  // ---- سایه ----
  const sc = document.createElement("canvas");
  sc.width = sc.height = 128;
  const g = sc.getContext("2d")!;
  const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  gr.addColorStop(0, "rgba(8,51,31,.38)");
  gr.addColorStop(1, "rgba(8,51,31,0)");
  g.fillStyle = gr;
  g.fillRect(0, 0, 128, 128);
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(6, 6), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(sc), transparent: true, depthWrite: false }));
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = -4.2;
  scene.add(shadow);

  // ---- اندازه و حلقهٔ رندر ----
  const size = () => {
    const w = host.clientWidth, h = host.clientHeight;
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(w, h, false);
    cam.aspect = w / h;
    cam.position.z = 19;
    cam.updateProjectionMatrix();
  };
  size();
  new ResizeObserver(size).observe(host);

  const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
  addEventListener(
    "pointermove",
    (e) => {
      mouse.tx = (e.clientX / innerWidth - 0.5) * 2;
      mouse.ty = (e.clientY / innerHeight - 0.5) * 2;
    },
    { passive: true }
  );

  const frame = (t: number) => {
    mouse.x += (mouse.tx - mouse.x) * 0.05;
    mouse.y += (mouse.ty - mouse.y) * 0.05;
    group.rotation.y = Math.sin(t * 0.45) * 0.35 - mouse.x * 0.35;
    group.rotation.x = mouse.y * 0.12;
    group.position.y = Math.sin(t * 1.1) * 0.15;
    apple.rotation.y = Math.sin(t * 0.7) * 0.12;
    rings.forEach((r, i) => (r.rotation.z = t * (i ? -0.08 : 0.1)));
    orbs.forEach((o) => {
      const a = t * o.s + o.o;
      o.m.position.set(Math.cos(a) * o.r, Math.sin(a) * o.r, 0);
    });
    const ph = (t * 0.45) % 1.6; // جاروی ECG و بعد محو شدن
    const head = Math.min(ph / 1.0, 1);
    tube.setDrawRange(0, Math.floor((idxTotal * head) / 6) * 6);
    (ecg.material as THREE.MeshBasicMaterial).opacity = ph < 1 ? 0.9 : Math.max(0, 0.9 * (1 - (ph - 1) / 0.6));
    const s = 1 + Math.max(0, Math.sin(t * 2.6)) ** 24 * 0.035; // ضربان ملایم
    apple.scale.setScalar(s);
    shadow.scale.setScalar(1 - (group.position.y + 0.15) * 0.12);
    renderer.render(scene, cam);
  };

  // شیدرها را پیش از اولین نمایش کامپایل می‌کنیم تا ظاهر شدن لوگو پرش نداشته باشد
  await renderer.compileAsync(scene, cam).catch(() => {});
  frame(0);
  host.classList.add("ready");
  if (reduce) {
    tube.setDrawRange(0, idxTotal);
    frame(0.6);
    return;
  }
  let raf = 0, visible = true;
  const t0 = performance.now();
  const loop = (now: number) => {
    raf = 0;
    if (!visible || document.hidden) return;
    frame((now - t0) / 1000);
    raf = requestAnimationFrame(loop);
  };
  const kick = () => {
    if (!raf && visible && !document.hidden) raf = requestAnimationFrame(loop);
  };
  new IntersectionObserver((es) => {
    visible = es[0].isIntersecting;
    kick();
  }).observe(host);
  document.addEventListener("visibilitychange", kick);
  kick();
}

