import * as THREE from "three";
import { C, geo, mat, mesh, blobShadow, Panel, type PanelState } from "./kit";

/** کاشی شناور با آیکن سه‌بعدی (قطره، قلب، صلیب) */
export function badge(icon: "drop" | "heart" | "cross", color: number) {
  const g = new THREE.Group();
  const inner = new THREE.Group();
  inner.add(mesh(geo.rbox(0.95, 0.95, 0.24, 0.2), mat(C.white, { rough: 0.35 })));
  const ic = mesh(icon === "drop" ? geo.drop() : icon === "heart" ? geo.heart() : geo.cross(), mat(color, { rough: 0.3 }), 0, 0, 0.2);
  if (icon === "drop") ic.scale.setScalar(1.05);
  if (icon === "heart") ic.scale.setScalar(0.95);
  if (icon === "cross") ic.scale.setScalar(0.85);
  inner.add(ic);
  g.add(inner);
  g.userData.inner = inner;
  return g;
}

/**
 * لوله‌ای که در طول مسیر «کشیده» می‌شود (نمودار، پرتو تماس).
 * setP(0..1) مقدار رسم‌شده را تعیین می‌کند؛ یک گوی هم سر خط حرکت می‌کند.
 */
export function drawTube(points: THREE.Vector3[], radius: number, color: number, o: { closed?: boolean; tension?: number; emissive?: boolean } = {}) {
  const curve = new THREE.CatmullRomCurve3(points, o.closed ?? false, "catmullrom", o.tension ?? 0.5);
  const segs = 160, radial = 10;
  const g = new THREE.TubeGeometry(curve, segs, radius, radial, false);
  const m = mat(color, { rough: 0.35, emissive: o.emissive ? color : 0, ei: o.emissive ? 0.35 : 1 });
  const tube = new THREE.Mesh(g, m);
  const head = mesh(geo.sphere(radius * 2.4), mat(color, { rough: 0.3, emissive: color, ei: 0.3 }));
  const group = new THREE.Group();
  group.add(tube, head);
  const total = g.index!.count;
  const state = { p: 0 };
  const apply = () => {
    const p = THREE.MathUtils.clamp(state.p, 0, 1);
    g.setDrawRange(0, Math.floor((total * p) / (radial * 6)) * radial * 6);
    head.visible = p > 0.001;
    if (head.visible) curve.getPointAt(p, head.position);
  };
  apply();
  return { group, curve, state, apply };
}

/** حلقهٔ موج که از مرکز باز می‌شود و محو می‌شود (هشدار، تماس) */
export function pulseRing(color: number, radius = 0.7) {
  const m = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0, depthWrite: false });
  const r = new THREE.Mesh(geo.torus(radius, 0.035), m);
  const state = { t: 0 };
  const apply = () => {
    const t = state.t % 1;
    r.scale.setScalar(0.6 + t * 1.6);
    m.opacity = state.t <= 0 ? 0 : (1 - t) * 0.85;
  };
  apply();
  return { mesh: r, state, apply };
}

/** گوشی هوشمند با صفحهٔ Panel */
export function phone<S extends PanelState>(panel: Panel<S>) {
  const g = new THREE.Group();
  g.add(mesh(geo.rbox(1.05, 2.05, 0.12, 0.16), mat(C.forest, { rough: 0.3, metal: 0.2 })));
  const screen = new THREE.Mesh(geo.plane(0.94, 1.9), new THREE.MeshBasicMaterial({ map: panel.texture, toneMapped: false }));
  screen.position.z = 0.062;
  g.add(screen);
  g.add(mesh(geo.rbox(0.3, 0.05, 0.02, 0.02), mat(0x0f1f1d), 0, 0.9, 0.07));
  return g;
}

/** دستگاه تست قند کوچک */
export function glucometer() {
  const g = new THREE.Group();
  g.add(mesh(geo.rbox(0.42, 0.66, 0.14, 0.1), mat(C.white, { rough: 0.35 })));
  g.add(mesh(geo.rbox(0.32, 0.22, 0.03, 0.04), mat(0x9fe3d6, { emissive: 0x7fe3a6, ei: 0.4, rough: 0.2 }), 0, 0.12, 0.07));
  g.add(mesh(geo.sphere(0.06), mat(C.teal), 0, -0.16, 0.07));
  g.add(mesh(geo.rbox(0.08, 0.22, 0.02, 0.01), mat(C.amber), 0, 0.42, 0));
  return g;
}

/** ستون «ویزیت» با صلیب شناور */
export function visitPost(color: number = C.teal) {
  const g = new THREE.Group();
  g.add(blobShadow(0.55, 0.55, 0.5));
  g.add(mesh(geo.cyl(0.36, 0.42, 1.0, 40), mat(C.white, { rough: 0.4 }), 0, 0.5, 0));
  g.add(mesh(geo.cyl(0.44, 0.44, 0.1, 40), mat(color, { rough: 0.4 }), 0, 1.05, 0));
  const b = badge("cross", C.red);
  b.position.y = 1.85;
  b.scale.setScalar(0.62);
  g.add(b);
  g.userData.badge = b;
  return g;
}

/** ذرات داده که روی یک منحنی حرکت می‌کنند (از گوشی تا درمانگاه) */
export function dataStream(count: number, colorA: number, colorB: number) {
  const g = geo.sphere(0.11, 16, 12);
  const m = new THREE.MeshStandardMaterial({ roughness: 0.3, emissiveIntensity: 0.5 });
  const inst = new THREE.InstancedMesh(g, m, count);
  const col = new THREE.Color();
  for (let i = 0; i < count; i++) inst.setColorAt(i, col.set(i % 2 ? colorA : colorB));
  inst.frustumCulled = false;
  const state = { p: 0 };
  const dummy = new THREE.Object3D();
  let curve: THREE.Curve<THREE.Vector3> | null = null;
  const setCurve = (c: THREE.Curve<THREE.Vector3>) => (curve = c);
  const apply = () => {
    for (let i = 0; i < count; i++) {
      const t = state.p * 1.6 - i * 0.07;
      const on = t > 0 && t < 1 && curve;
      if (on) curve!.getPointAt(t, dummy.position);
      dummy.scale.setScalar(on ? Math.sin(t * Math.PI) * 0.7 + 0.5 : 0);
      dummy.updateMatrix();
      inst.setMatrixAt(i, dummy.matrix);
    }
    inst.instanceMatrix.needsUpdate = true;
  };
  apply();
  return { mesh: inst, state, apply, setCurve };
}
