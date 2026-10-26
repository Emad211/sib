import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { C, CSS, FONT, Panel, geo, mat, mesh, blobShadow, rng } from "./kit/kit";

/** شعاع حلقه و تعداد ایستگاه‌ها — کل داستان روی یک «حلقه» اتفاق می‌افتد، درمانگاه سیب در مرکز. */
export const R = 13;
export const N = 8;
export const TAU = Math.PI * 2;
export const PLATFORM_Y = 0.18;
export const stationAngle = (k: number) => (k * TAU) / N;

/** نقطه و جهت‌های محلی روی حلقه در زاویهٔ a. right = سمت راست تصویر وقتی دوربین از بیرون به مرکز نگاه می‌کند */
export function frameAt(a: number, target = { pos: new THREE.Vector3(), right: new THREE.Vector3(), out: new THREE.Vector3() }) {
  const c = Math.cos(a), s = Math.sin(a);
  target.out.set(c, 0, s);
  target.right.set(s, 0, -c);
  target.pos.set(R * c, PLATFORM_Y, R * s);
  return target;
}
/** زاویهٔ y که محور +z محلی را به سمت بیرون حلقه (به سمت دوربین) می‌چرخاند */
export const yawOut = (a: number) => Math.PI / 2 - a;

/** نویز نرم و قطعی (بدون کتابخانه) برای کوه، زمین و تاج درخت */
function noise(x: number, y: number, seed = 0) {
  return (
    Math.sin(x * 1.7 + seed) * Math.cos(y * 1.3 - seed * 0.7) * 0.5 +
    Math.sin(x * 3.1 - y * 2.3 + seed * 1.9) * 0.3 +
    Math.sin(x * 6.7 + y * 5.9 + seed * 3.1) * 0.2
  );
}

export interface WorldAssets {
  /** هندسهٔ لوگوی سیب (از hero-logo.bin): [بدنه، بخش روشن] */
  logo?: THREE.BufferGeometry[];
  /** تصویر لوگو برای تابلوهای نمای ساختمان */
  logoImg?: HTMLImageElement;
}

export class World {
  readonly root = new THREE.Group();
  readonly stations: THREE.Group[] = [];
  readonly ringUniforms = {
    uP: { value: 0 },
    uOn: { value: new THREE.Color(C.teal) },
    uOff: { value: new THREE.Color(0xcfc4ad) },
    uTime: { value: 0 },
  };
  readonly hub = new THREE.Group();
  /** لوگوی سه‌بعدی سیب روی بام؛ برچسب‌ها و «تپش» به آن وصل‌اند */
  readonly hubSign = new THREE.Group();
  /** برای «تپش» لوگوی درمانگاه وقتی داده‌ای می‌رسد */
  readonly hubPulse = { v: 0 };
  /** نقطهٔ بالای درمانگاه که داده‌ها و تماس‌ها به آن وصل می‌شوند */
  readonly hubPort = new THREE.Vector3(0, 6.3, 0);
  /** حال‌وهوای صحنه: ۰ = شب (قلاب ابتدای داستان)، ۱ = روز */
  readonly mood = { v: 1 };
  private lastMood = -1;
  private clouds = new THREE.Group();
  private hemi = new THREE.HemisphereLight(0xfffaf0, 0xb9c9a0, 1.25);
  private sun = new THREE.DirectionalLight(0xfff1dc, 2.2);
  private rim = new THREE.DirectionalLight(0xdff7f1, 0.5);
  private glass = new THREE.MeshPhysicalMaterial({ color: 0x9fd8cf, roughness: 0.08, metalness: 0.1, emissive: 0xffe7a8, emissiveIntensity: 0.1, clearcoat: 1 });
  private logoMat = new THREE.MeshPhysicalMaterial({ color: 0x14824a, roughness: 0.28, metalness: 0.05, clearcoat: 1, clearcoatRoughness: 0.08, emissive: 0x1b9157, emissiveIntensity: 0 });
  private sky = {
    top: new THREE.Color(), horizon: new THREE.Color(),
    dayTop: new THREE.Color(0xa9d3ea), dayHorizon: new THREE.Color(0xf4efe3),
    nightTop: new THREE.Color(0x040b12), nightHorizon: new THREE.Color(0x0f2a22),
  };
  private skyUniforms = { uTop: { value: this.sky.top }, uHorizon: { value: this.sky.horizon } };
  private stars!: THREE.Points;
  private swayUniform = { value: 0 };

  constructor(private scene: THREE.Scene, assets: WorldAssets = {}) {
    scene.fog = new THREE.Fog(C.bg, 42, 230);
    scene.add(this.root);

    // نور: آسمان/زمین + خورشید با سایهٔ واقعی + نور لبه
    this.root.add(this.hemi);
    this.sun.castShadow = true;
    const sc = this.sun.shadow.camera;
    sc.left = sc.bottom = -11;
    sc.right = sc.top = 11;
    sc.near = 1;
    sc.far = 70;
    this.sun.shadow.bias = -0.0004;
    this.sun.shadow.normalBias = 0.025;
    this.sun.shadow.radius = 4;
    this.root.add(this.sun, this.sun.target);
    this.rim.position.set(-12, 8, -10);
    this.root.add(this.rim);

    this.buildSky();
    this.buildGround();
    this.buildMountains();
    this.buildRing();
    this.buildStations();
    this.buildHub(assets);
    this.buildNature();
    this.buildClouds();
  }

  /** اندازهٔ نقشهٔ سایه (۲۰۴۸ دسکتاپ، ۱۰۲۴ موبایل) */
  setShadowSize(px: number) {
    this.sun.shadow.mapSize.set(px, px);
  }

  /** خورشید و قاب سایه همراه نقطهٔ توجه دوربین حرکت می‌کنند تا سایه‌ها همیشه تیز باشند */
  focus(target: THREE.Vector3) {
    this.sun.target.position.copy(target);
    this.sun.position.copy(target).add(new THREE.Vector3(14, 26, 11));
  }

  // ---------- آسمان: گرادیان + ستاره‌ها (شب) ----------
  private buildSky() {
    const m = new THREE.ShaderMaterial({
      uniforms: this.skyUniforms,
      side: THREE.BackSide,
      depthWrite: false,
      fog: false,
      vertexShader: /* glsl */ `varying vec3 vP; void main(){ vP = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }`,
      fragmentShader: /* glsl */ `
        uniform vec3 uTop; uniform vec3 uHorizon; varying vec3 vP;
        void main(){ float h = clamp(vP.y * 1.6, 0.0, 1.0); vec3 c = mix(uHorizon, uTop, pow(h, 0.7)); gl_FragColor = vec4(c, 1.0);
          #include <colorspace_fragment>
        }`,
    });
    const dome = new THREE.Mesh(new THREE.SphereGeometry(320, 48, 24), m);
    dome.renderOrder = -10;
    this.root.add(dome);

    const rand = rng(31), n = 900, p = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const a = rand() * TAU, y = 0.12 + rand() * 0.88, r = Math.sqrt(1 - y * y);
      p.set([Math.cos(a) * r * 300, y * 300, Math.sin(a) * r * 300], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(p, 3));
    this.stars = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xffffff, size: 1.6, sizeAttenuation: false, transparent: true, opacity: 0, fog: false, depthWrite: false }));
    this.root.add(this.stars);
  }

  // ---------- زمین: چمن نرم با لکه‌های رنگی، محوطهٔ روشن دور درمانگاه ----------
  private buildGround() {
    const g = new THREE.CircleGeometry(320, 160, 0, TAU);
    // چگالی بیشتر نزدیک مرکز برای رنگ‌آمیزی ظریف
    const pos = g.attributes.position as THREE.BufferAttribute;
    const col = new Float32Array(pos.count * 3);
    const grassA = new THREE.Color(0xa3c985), grassB = new THREE.Color(0x86b86c), plaza = new THREE.Color(0xe7dcc6), c = new THREE.Color();
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), y = pos.getY(i), r = Math.hypot(x, y);
      c.copy(grassA).lerp(grassB, 0.5 + 0.5 * noise(x * 0.08, y * 0.08, 2));
      c.lerp(plaza, 1 - THREE.MathUtils.smoothstep(r, 6.5, 9.5));
      col.set([c.r, c.g, c.b], i * 3);
    }
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    const floor = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1 }));
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    this.root.add(floor);
  }

  // ---------- کوه‌های دوردست (رشته‌کوه البرز پشت گرگان) ----------
  private buildMountains() {
    const ring = (radius: number, depth: number, base: number, amp: number, seed: number, color: number) => {
      const cols = 220, rows = 7;
      const pos: number[] = [], idx: number[] = [];
      for (let i = 0; i <= cols; i++) {
        const a = (i / cols) * TAU;
        const ridge = base + amp * (0.55 + 0.45 * noise(Math.cos(a) * 3, Math.sin(a) * 3, seed)) + amp * 0.35 * Math.max(0, noise(a * 4, seed, seed));
        for (let j = 0; j <= rows; j++) {
          const t = j / rows;
          const r = radius + depth * t;
          const y = -1 + (ridge + 1) * Math.sin((t * Math.PI) / 2) + noise(a * 20, t * 4, seed) * 0.8 * t;
          pos.push(Math.cos(a) * r, y, Math.sin(a) * r);
        }
      }
      for (let i = 0; i < cols; i++)
        for (let j = 0; j < rows; j++) {
          const a = i * (rows + 1) + j, b = a + rows + 1;
          idx.push(a, a + 1, b, b, a + 1, b + 1);
        }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      g.setIndex(idx);
      g.computeVertexNormals();
      const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color, roughness: 1, side: THREE.DoubleSide }));
      this.root.add(m);
    };
    ring(115, 40, 10, 16, 1.3, 0x7fae86);
    ring(170, 50, 22, 26, 4.1, 0x8fb3b0);
  }

  /** مسیر حلقه با شیدر: هرچه داستان جلو می‌رود، حلقه پر می‌شود. */
  private buildRing() {
    const m = new THREE.ShaderMaterial({
      uniforms: this.ringUniforms,
      transparent: true,
      depthWrite: false,
      vertexShader: /* glsl */ `
        varying vec3 vW;
        void main(){ vec4 w = modelMatrix * vec4(position,1.); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`,
      fragmentShader: /* glsl */ `
        uniform float uP; uniform vec3 uOn; uniform vec3 uOff; uniform float uTime;
        varying vec3 vW;
        void main(){
          float f = fract(atan(vW.z, vW.x) / 6.2831853 + 1.0);
          float on = (1.0 - smoothstep(uP - 0.003, uP, f)) * step(0.0005, uP);
          float r = length(vW.xz);
          float edge = smoothstep(${(R - 0.32).toFixed(2)}, ${(R - 0.24).toFixed(2)}, r) * (1.0 - smoothstep(${(R + 0.24).toFixed(2)}, ${(R + 0.32).toFixed(2)}, r));
          float dash = step(0.45, fract(f * 180.0));
          float flow = 0.5 + 0.5 * sin(f * 900.0 - uTime * 6.0);
          float head = exp(-pow((f - uP) * 160.0, 2.0)) * step(0.0005, uP);
          vec3 col = mix(uOff, uOn + flow * 0.08, on) + head * 1.4;
          float a = mix(0.55 * dash, 0.95, on) * edge + head * 0.6;
          gl_FragColor = vec4(col, a);
          #include <colorspace_fragment>
        }`,
    });
    const ring = new THREE.Mesh(new THREE.RingGeometry(R - 0.34, R + 0.34, 320, 1), m);
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.03;
    this.root.add(ring);
  }

  private buildStations() {
    const top = mat(C.platform, { rough: 0.85 });
    const edge = mat(C.teal, { rough: 0.5 });
    for (let k = 0; k < N; k++) {
      const a = stationAngle(k);
      const f = frameAt(a);
      const base = new THREE.Group();
      base.position.set(f.pos.x, 0, f.pos.z);
      const disc = mesh(geo.cyl(3.3, 3.42, PLATFORM_Y, 72), top, 0, PLATFORM_Y / 2, 0);
      disc.receiveShadow = true;
      base.add(disc);
      const lip = mesh(geo.torus(3.3, 0.03), edge, 0, PLATFORM_Y, 0);
      lip.rotation.x = Math.PI / 2;
      base.add(lip);
      this.root.add(base);

      const st = new THREE.Group();
      st.position.copy(f.pos);
      st.rotation.y = yawOut(a);
      this.stations.push(st);
      this.root.add(st);
    }
  }

  // ---------- ساختمان «درمانگاه سیب» در مرکز حلقه ----------
  private buildHub({ logo, logoImg }: WorldAssets) {
    const h = this.hub;
    const white = new THREE.MeshPhysicalMaterial({ color: 0xfbf8f1, roughness: 0.55, clearcoat: 0.2 });
    const pine = mat(C.pine, { rough: 0.45 });
    const stone = mat(0xe4dccb, { rough: 0.9 });

    const plinth = mesh(geo.rbox(8.6, 0.32, 8.6, 0.12), stone, 0, 0.16, 0);
    plinth.receiveShadow = true;
    h.add(plinth);
    const body = mesh(geo.rbox(6.4, 3.5, 6.4, 0.32), white, 0, 2.07, 0);
    h.add(body);
    h.add(mesh(geo.rbox(7.1, 0.3, 7.1, 0.12), pine, 0, 3.95, 0));
    const roof = mesh(geo.rbox(3.0, 0.5, 3.0, 0.12), white, 0, 4.35, 0);
    h.add(roof);

    // تابلوی «درمانگاه سیب» برای هر چهار نما
    const sign = new Panel(1024, 200, {}, (g, w, hh) => {
      g.fillStyle = CSS.pine;
      g.beginPath();
      g.roundRect(0, 0, w, hh, 40);
      g.fill();
      g.fillStyle = "#fff";
      g.beginPath();
      g.arc(w - 100, hh / 2, 72, 0, TAU);
      g.fill();
      if (logoImg) g.drawImage(logoImg, w - 145, hh / 2 - 62, 90, 124);
      g.fillStyle = "#fff";
      g.font = `900 104px ${FONT}`;
      g.textAlign = "right";
      g.fillText("درمانگاه سیب", w - 205, hh / 2 + 38);
      g.fillStyle = CSS.mint;
      g.font = `700 46px ${FONT}`;
      g.textAlign = "left";
      g.fillText("۲۴ ساعته", 48, hh / 2 + 18);
    });
    const signMat = new THREE.MeshStandardMaterial({ map: sign.texture, roughness: 0.5, emissive: 0xffffff, emissiveMap: sign.texture, emissiveIntensity: 0.12 });

    for (let side = 0; side < 4; side++) {
      const face = new THREE.Group();
      face.rotation.y = (side * Math.PI) / 2;
      // نوار شیشه‌ای دو طبقه با قاب‌بندی عمودی
      for (const y of [1.25, 2.75]) {
        face.add(mesh(geo.rbox(5.4, 0.95, 0.1, 0.05), this.glass, 0, y, 3.2));
        for (let i = -3; i <= 3; i++) face.add(mesh(geo.rbox(0.07, 0.95, 0.16, 0.02), white, i * 0.86, y, 3.23));
      }
      // ورودی: سایبان سبز و در شیشه‌ای
      face.add(mesh(geo.rbox(2.2, 0.14, 1.1, 0.05), pine, 0, 2.02, 3.62));
      face.add(mesh(geo.rbox(1.3, 1.7, 0.14, 0.06), this.glass, 0, 1.03, 3.22));
      const s = new THREE.Mesh(geo.plane(3.4, 0.66), signMat);
      s.position.set(0, 3.42, 3.235);
      face.add(s);
      // گلدان‌های کنار ورودی
      for (const x of [-1.55, 1.55]) {
        face.add(mesh(geo.cyl(0.26, 0.2, 0.42, 20), stone, x, 0.53, 3.75));
        const bush = mesh(this.crown(0.42, 5), mat(C.leaf2, { rough: 0.85 }), x, 0.98, 3.75);
        face.add(bush);
      }
      h.add(face);
    }

    // لوگوی سه‌بعدی سیب روی بام — آرام می‌چرخد و از همهٔ ایستگاه‌ها دیده می‌شود
    this.hubSign.position.set(0, 6.25, 0);
    if (logo) {
      const s = 2.9 / 1940;
      const lg = new THREE.Group();
      const g0 = new THREE.Mesh(logo[0], this.logoMat);
      const g1 = new THREE.Mesh(logo[1], new THREE.MeshPhysicalMaterial({ color: 0xe0f4e6, roughness: 0.15, clearcoat: 1 }));
      lg.add(g0, g1);
      lg.scale.set(s, -s, s);
      lg.position.z = -0.2;
      this.hubSign.add(lg);
    } else {
      this.hubSign.add(mesh(geo.cross(), mat(C.red, { rough: 0.35 })));
    }
    h.add(this.hubSign);
    h.add(mesh(geo.cyl(0.55, 0.7, 0.22, 40), pine, 0, 4.7, 0));

    h.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) {
        o.castShadow = true;
        o.receiveShadow = true;
      }
    });
    h.add(blobShadow(5.8, 5.8, 0.3));
    this.root.add(h);
  }

  /** تاج درخت: چند کرهٔ ناهموار در هم — پرحجم‌تر و طبیعی‌تر از یک کرهٔ ساده */
  private crown(size: number, seed: number, lobes = 5) {
    const rand = rng(seed);
    const parts: THREE.BufferGeometry[] = [];
    for (let i = 0; i < lobes; i++) {
      const g = new THREE.IcosahedronGeometry(size * (0.55 + rand() * 0.35), 3);
      const a = rand() * TAU, d = i === 0 ? 0 : size * (0.35 + rand() * 0.25);
      g.translate(Math.cos(a) * d, (rand() - 0.3) * size * 0.5, Math.sin(a) * d);
      parts.push(g);
    }
    const g = mergeGeometries(parts.map((p) => p.toNonIndexed()), false);
    const p = g.attributes.position as THREE.BufferAttribute, v = new THREE.Vector3();
    for (let i = 0; i < p.count; i++) {
      v.fromBufferAttribute(p, i);
      const k = 1 + noise(v.x * 3.2, v.y * 3.2 + v.z * 2.1, seed) * 0.12;
      p.setXYZ(i, v.x * k, v.y * k, v.z * k);
    }
    g.computeVertexNormals();
    return g;
  }

  /** سرو ایرانی: بلند و کشیده */
  private cypress() {
    const pts: THREE.Vector2[] = [];
    for (let i = 0; i <= 24; i++) {
      const t = i / 24;
      pts.push(new THREE.Vector2(Math.sin(Math.pow(t, 0.75) * Math.PI) * 0.5 * (1 - t * 0.35) + 0.02, t * 3.4));
    }
    const g = new THREE.LatheGeometry(pts, 28);
    const p = g.attributes.position as THREE.BufferAttribute, v = new THREE.Vector3();
    for (let i = 0; i < p.count; i++) {
      v.fromBufferAttribute(p, i);
      const k = 1 + noise(Math.atan2(v.z, v.x) * 3, v.y * 4, 9) * 0.16;
      p.setXYZ(i, v.x * k, v.y, v.z * k);
    }
    g.computeVertexNormals();
    return g.translate(0, 0.5, 0);
  }

  /** نوسان ملایم درخت‌ها در باد (در شیدر؛ هزینهٔ CPU ندارد) */
  private sway(m: THREE.MeshStandardMaterial, amount: number) {
    m.onBeforeCompile = (sh) => {
      sh.uniforms.uSway = this.swayUniform;
      sh.vertexShader = "uniform float uSway;\n" + sh.vertexShader.replace(
        "#include <begin_vertex>",
        `#include <begin_vertex>
        #ifdef USE_INSTANCING
          vec3 ip = instanceMatrix[3].xyz;
          float hgt = max(0.0, position.y);
          transformed.x += sin(uSway * 1.3 + ip.x * 0.35 + ip.z * 0.2) * ${amount.toFixed(3)} * hgt;
          transformed.z += cos(uSway * 1.1 + ip.z * 0.31) * ${(amount * 0.6).toFixed(3)} * hgt;
        #endif`
      );
    };
    return m;
  }

  // ---------- درخت، سرو و بوته — InstancedMesh تا با صدها شیء هم سبک بماند ----------
  private buildNature() {
    const rand = rng(7);
    const round: THREE.Matrix4[] = [], tall: THREE.Matrix4[] = [], bushes: THREE.Matrix4[] = [];
    const q = new THREE.Quaternion(), e = new THREE.Euler();
    const m4 = (x: number, y: number, z: number, s: number, sy = s) =>
      new THREE.Matrix4().compose(new THREE.Vector3(x, y, z), q.setFromEuler(e.set(0, rand() * TAU, 0)), new THREE.Vector3(s, sy, s));
    const polar = (r: number, a: number) => [Math.cos(a) * r, Math.sin(a) * r] as const;
    const half = Math.PI / N;

    // داخل حلقه، پشت ایستگاه‌ها: عمق صحنه (سروها کنار درمانگاه)
    for (let i = 0; i < 12; i++) {
      const [x, z] = polar(5.6 + rand() * 1.8, ((i + rand() * 0.6) / 12) * TAU);
      (i % 2 ? tall : round).push(m4(x, 0, z, 0.75 + rand() * 0.45));
    }
    // بین ایستگاه‌ها، کنار مسیر
    for (let k = 0; k < N; k++) {
      const mid = stationAngle(k) + half;
      for (let i = 0; i < 5; i++) {
        const [x, z] = polar(R + (rand() - 0.5) * 3.4, mid + (rand() - 0.5) * 0.24);
        bushes.push(m4(x, 0.15, z, 0.38 + rand() * 0.32, 0.32 + rand() * 0.28));
      }
      const [tx, tz] = polar(R + 2.6 + rand(), mid + (rand() - 0.5) * 0.12);
      round.push(m4(tx, 0, tz, 0.95 + rand() * 0.4));
      const [cx, cz] = polar(R + 4.2 + rand(), mid + (rand() - 0.5) * 0.2);
      tall.push(m4(cx, 0, cz, 0.85 + rand() * 0.4, 0.9 + rand() * 0.5));
    }
    // بیرون حلقه: جنگل هیرکانی تا دامنهٔ کوه
    for (let i = 0; i < 150; i++) {
      const [x, z] = polar(24 + rand() * 70, rand() * TAU);
      (rand() > 0.3 ? round : tall).push(m4(x, 0, z, 1 + rand() * 1.1));
    }

    const leafA = this.sway(new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.82 }), 0.035);
    const leafB = this.sway(new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.85 }), 0.02);
    const trunkMat = mat(C.trunk, { rough: 0.9 });
    const trunkG = new THREE.CylinderGeometry(0.09, 0.15, 1.3, 10).translate(0, 0.65, 0);
    const crownG = this.crown(0.85, 3).translate(0, 1.85, 0);
    const cypG = this.cypress();

    const inst = (g: THREE.BufferGeometry, m: THREE.Material, list: THREE.Matrix4[], colors?: number[]) => {
      const im = new THREE.InstancedMesh(g, m, list.length);
      list.forEach((mx, i) => {
        im.setMatrixAt(i, mx);
        if (colors) im.setColorAt(i, new THREE.Color(colors[Math.floor(rand() * colors.length)]));
      });
      im.castShadow = true;
      im.receiveShadow = true;
      this.root.add(im);
    };
    inst(trunkG, trunkMat, round);
    inst(crownG, leafA, round, [0x6fae6a, 0x5c9f62, 0x82bb72, 0x4f9160, 0x8cc37c]);
    inst(cypG, leafB, tall, [0x2f6f4c, 0x3a7d55, 0x2a6445]);
    inst(this.crown(1, 11, 3), leafA, bushes, [0x79b46f, 0x67a868, 0x8fc47e]);
  }

  private buildClouds() {
    const rand = rng(11);
    const m = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, emissive: 0xffffff, emissiveIntensity: 0.15, transparent: true, opacity: 0.95 });
    for (let i = 0; i < 12; i++) {
      const g = new THREE.Group();
      for (let j = 0; j < 5; j++) g.add(mesh(geo.sphere(1, 24, 16), m, j * 1.05 - 2.1, Math.sin(j * 1.7) * 0.35, rand() - 0.5));
      g.children.forEach((c, j) => c.scale.set(0.9 + ((j * 7) % 3) * 0.35, 0.7 + ((j * 5) % 3) * 0.2, 0.9));
      const a = rand() * TAU, r = 30 + rand() * 60;
      g.position.set(Math.cos(a) * r, 18 + rand() * 10, Math.sin(a) * r);
      g.scale.setScalar(1.2 + rand() * 1.2);
      this.clouds.add(g);
    }
    this.root.add(this.clouds);
  }

  /** نور و رنگ آسمان بین شب و روز؛ شب‌ها پنجره‌ها و لوگوی درمانگاه روشن‌اند */
  private applyMood() {
    const v = this.mood.v;
    if (Math.abs(v - this.lastMood) < 1e-4) return;
    this.lastMood = v;
    const e = v * v * (3 - 2 * v);
    const s = this.sky;
    s.top.lerpColors(s.nightTop, s.dayTop, e);
    s.horizon.lerpColors(s.nightHorizon, s.dayHorizon, e);
    (this.scene.fog as THREE.Fog).color.copy(s.horizon);
    this.hemi.intensity = 0.18 + 1.07 * e;
    this.sun.intensity = 0.1 + 2.1 * e;
    this.rim.intensity = 1.0 - 0.5 * e;
    this.rim.color.setHex(0x9fd4ff).lerp(new THREE.Color(0xdff7f1), e);
    this.scene.environmentIntensity = 0.08 + 0.42 * e;
    this.glass.emissiveIntensity = 2.6 - 2.5 * e;
    this.logoMat.emissiveIntensity = 0.9 * (1 - e);
    (this.stars.material as THREE.PointsMaterial).opacity = 0.85 * (1 - e);
  }

  update(time: number) {
    this.applyMood();
    this.swayUniform.value = time;
    this.ringUniforms.uTime.value = time;
    this.hubSign.rotation.y = time * 0.45;
    const pulse = 1 + this.hubPulse.v * 0.18;
    this.hubSign.scale.setScalar(pulse);
    this.hubSign.position.y = 6.25 + Math.sin(time * 1.2) * 0.1;
    this.clouds.rotation.y = time * 0.006;
  }
}
