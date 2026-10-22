// The 3D scenes of the Video Maker. Six different ones, each for one place:
//   work  : her clips orbit a phone and are drawn into it as the video is made   (processing screen)
//   house : a little house with its door and warm windows, a key circling it      (sign-in page)
//   flow  : clips -> captions -> finished card, with light flowing through       (start page, nothing chosen yet)
//   ready : the finished video's picture on a card, platform badges flying in     (result page)
//   week  : seven day blocks, three to five rise and glow (the weekly goal)      (My videos)
//   skyline: a calm grid of towers with a light band reading across them         (Team page)
// Source file. Built with `npm run build:3d` (esbuild + three.js, tree-shaken) into web/vendor/scene3d.js.
// Nothing here changes data: it only draws. web/motion.js (and the sign-in page) mount it; never with reduced motion or
// without WebGL, and it stops drawing when the tab or its box is not visible.
import {
  AdditiveBlending, NormalBlending, AmbientLight, BoxGeometry, BufferGeometry, CanvasTexture, CircleGeometry, Color, CylinderGeometry, DirectionalLight, DoubleSide,
  ExtrudeGeometry, Float32BufferAttribute, Group, HemisphereLight, LinearFilter, MathUtils, Mesh, MeshBasicMaterial,
  MeshPhysicalMaterial, MeshStandardMaterial, Path, PerspectiveCamera, PlaneGeometry, Points, PointsMaterial, QuadraticBezierCurve3,
  RingGeometry, SRGBColorSpace, Scene, Shape, TorusGeometry, Vector3, WebGLRenderer,
} from "three";

const TAU = Math.PI * 2;
const clamp01 = x => Math.max(0, Math.min(1, x));
const easeOut = x => 1 - Math.pow(1 - clamp01(x), 3);
const easeOutBack = x => { const c = 1.9, k = clamp01(x) - 1; return 1 + (c + 1) * k * k * k + c * k * k; };

function roundedRect(w, h, r) {
  const s = new Shape(), x = -w / 2, y = -h / 2;
  s.moveTo(x + r, y);
  s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r); s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h); s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y);
  return s;
}

function cssColor(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function texture(canvas) {
  const t = new CanvasTexture(canvas);
  t.colorSpace = SRGBColorSpace; t.minFilter = LinearFilter; t.generateMipmaps = false;
  return t;
}
function canvasOf(w, h) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  return [c, c.getContext("2d")];
}

function glowTexture(color) {
  const [c, g] = canvasOf(128, 128);
  const r = g.createRadialGradient(64, 64, 4, 64, 64, 64);
  r.addColorStop(0, color); r.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = r; g.fillRect(0, 0, 128, 128);
  return texture(c);
}

// ---------------------------------------------------------------- shared look
function lights(scene, brand) {
  scene.add(new HemisphereLight(0xffffff, 0x9bb7cf, 1.1), new AmbientLight(0xffffff, 0.35));
  const key = new DirectionalLight(0xffffff, 2.2); key.position.set(3, 4, 5); scene.add(key);
  const rim = new DirectionalLight(new Color(brand), 1.4); rim.position.set(-4, 1, -3); scene.add(rim);
}

// ================================================================ scene: work (phone + clips)
function clipTexture(tint, i) {
  const [c, g] = canvasOf(180, 320);
  g.beginPath(); g.roundRect(4, 4, 172, 312, 22); g.clip();
  const grad = g.createLinearGradient(0, 0, 180, 320);
  grad.addColorStop(0, tint); grad.addColorStop(1, "#1d4c75");
  g.fillStyle = grad; g.fillRect(0, 0, 180, 320);
  g.fillStyle = "rgba(255,255,255,.16)";
  if (i % 3 === 0) { g.beginPath(); g.moveTo(20, 230); g.lineTo(90, 150); g.lineTo(160, 230); g.closePath(); g.fill(); g.fillRect(34, 230, 112, 50); }
  else if (i % 3 === 1) { g.fillRect(28, 70, 54, 70); g.fillRect(98, 70, 54, 70); g.fillRect(28, 156, 124, 90); }
  else { g.beginPath(); g.arc(120, 90, 34, 0, TAU); g.fill(); g.fillRect(20, 220, 140, 60); }
  g.fillStyle = "rgba(255,255,255,.92)";
  g.beginPath(); g.moveTo(74, 132); g.lineTo(74, 188); g.lineTo(122, 160); g.closePath(); g.fill();
  g.fillStyle = "rgba(255,255,255,.85)"; g.beginPath(); g.roundRect(40, 286, 100, 9, 4); g.fill();
  g.strokeStyle = "rgba(255,255,255,.55)"; g.lineWidth = 3; g.beginPath(); g.roundRect(4, 4, 172, 312, 22); g.stroke();
  return texture(c);
}

function phoneScreen(brand, progress) {
  const [c, g] = canvasOf(256, 512);
  const grad = g.createLinearGradient(0, 0, 0, 512);
  grad.addColorStop(0, "#0f2236"); grad.addColorStop(1, "#173a58");
  g.fillStyle = grad; g.fillRect(0, 0, 256, 512);
  const sky = g.createLinearGradient(0, 0, 0, 300);
  sky.addColorStop(0, brand); sky.addColorStop(1, "rgba(108,174,223,0)");
  g.globalAlpha = 0.55 + 0.4 * progress; g.fillStyle = sky; g.fillRect(0, 0, 256, 300); g.globalAlpha = 1;
  g.fillStyle = "rgba(255,255,255,.12)";
  g.beginPath(); g.moveTo(24, 330); g.lineTo(128, 250); g.lineTo(232, 330); g.lineTo(232, 400); g.lineTo(24, 400); g.closePath(); g.fill();
  [0.62, 0.8, 0.5].forEach((w, i) => {
    g.fillStyle = progress > (i + 1) / 4.2 ? "rgba(255,255,255,.95)" : "rgba(255,255,255,.22)";
    const bw = 170 * w;
    g.beginPath(); g.roundRect(128 - bw / 2, 420 + i * 22, bw, 11, 5); g.fill();
  });
  g.lineWidth = 7; g.strokeStyle = "rgba(255,255,255,.2)";
  g.beginPath(); g.arc(214, 42, 20, 0, TAU); g.stroke();
  g.strokeStyle = "#9fd0f2";
  g.beginPath(); g.arc(214, 42, 20, -Math.PI / 2, -Math.PI / 2 + TAU * progress); g.stroke();
  return texture(c);
}

function buildWork(ctx) {
  const { scene, brand } = ctx;
  const stage = new Group(), phone = new Group();
  const body = new Mesh(
    new ExtrudeGeometry(roundedRect(1.02, 2.0, 0.17), { depth: 0.1, bevelEnabled: true, bevelThickness: 0.035, bevelSize: 0.035, bevelSegments: 4, curveSegments: 12 }),
    new MeshPhysicalMaterial({ color: 0x1b2a3a, metalness: 0.55, roughness: 0.28, clearcoat: 0.8, clearcoatRoughness: 0.2 }));
  body.position.z = -0.05;
  const screenMat = new MeshBasicMaterial({ map: phoneScreen(brand, 0) });
  const screen = new Mesh(new PlaneGeometry(0.88, 1.84), screenMat);
  screen.position.z = 0.105;
  const glow = new Mesh(new PlaneGeometry(3.4, 3.4), new MeshBasicMaterial({ map: glowTexture("rgba(108,174,223,.55)"), transparent: true, depthWrite: false, opacity: 0.45 }));
  glow.position.z = -0.4;
  phone.add(body, screen);
  stage.add(glow, phone);
  scene.add(stage);

  const tints = [brand, "#7fb8e3", "#4d94c8", "#8fc4ea", "#3b86bd"];
  const N = tints.length;
  const cards = tints.map((tint, i) => {
    const m = new Mesh(new PlaneGeometry(0.62, 1.1), new MeshBasicMaterial({ map: clipTexture(tint, i), transparent: true, side: DoubleSide }));
    stage.add(m);
    return { m, i, phase: (i / N) * TAU + 0.4, taken: 0 };
  });
  const flash = new Mesh(new RingGeometry(1.15, 1.2, 64), new MeshBasicMaterial({ color: new Color(brand), transparent: true, opacity: 0, depthWrite: false }));
  flash.position.z = 0.1; stage.add(flash);
  let redrawAt = -1;

  return {
    camera: { pos: [0, 0.05, 5.7], fov: 30 },
    update(S) {
      const spin = S.done ? Math.min(1, S.doneAt / 1.1) : 0;
      stage.rotation.x = S.tiltX * 0.8 + Math.sin(S.t * 0.7) * 0.04;
      stage.rotation.y = S.tiltY + Math.sin(S.t * 0.5) * 0.12 + easeOut(spin) * TAU;
      stage.position.y = Math.sin(S.t * 1.1) * 0.06;
      glow.material.opacity = 0.32 + 0.25 * S.shown + (S.done ? 0.25 * Math.sin(Math.min(1, S.doneAt / 1.2) * Math.PI) : 0);
      glow.rotation.z = -stage.rotation.y;
      cards.forEach(c => {
        const need = (c.i + 1) / (N + 1);
        const want = (S.done || (!S.idle && S.shown > need + 0.04)) ? 1 : 0;
        c.taken += (want - c.taken) * Math.min(1, S.dt * 4);
        const ang = c.phase + S.t * 0.5 * (1 - c.taken * 0.6);
        const rad = 1.75 * (1 - c.taken) + 0.05;
        const lift = Math.sin(S.t * 0.9 + c.i * 1.7) * 0.12 * (1 - c.taken);
        c.m.position.set(Math.cos(ang) * rad, lift, Math.sin(ang) * rad * 0.7 + 0.12 * c.taken);
        c.m.rotation.set(0, Math.sin(ang) * 0.4, 0.06 * Math.cos(S.t * 0.8 + c.i));
        c.m.scale.setScalar((0.78 + 0.22 * (Math.sin(ang) * 0.5 + 0.5)) * (1 - 0.8 * c.taken * c.taken));
        c.m.visible = c.taken < 0.985;
      });
      flash.material.opacity = S.done ? Math.max(0, 0.9 - S.doneAt * 0.75) * (S.doneAt < 1.2 ? 1 : 0) : 0;
      const k = 1 + (S.done ? S.doneAt * 1.6 : 0);
      flash.scale.set(k, k, 1);
      const step = Math.round(S.shown * 24);
      if (step !== redrawAt) {
        redrawAt = step;
        const old = screenMat.map;
        screenMat.map = phoneScreen(brand, Math.min(1, S.shown));
        screenMat.needsUpdate = true;
        if (old) old.dispose();
      }
    },
  };
}

// ================================================================ scene: house (sign-in)
function buildHouse(ctx) {
  const { scene, accent } = ctx;
  const g = new Group();
  scene.add(g);
  const ground = new Mesh(new CircleGeometry(1.9, 48), new MeshBasicMaterial({ map: glowTexture("rgba(44,118,174,.40)"), transparent: true, depthWrite: false }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.64;
  g.add(ground);
  const wall = new Mesh(new BoxGeometry(1.5, 0.95, 1.05), new MeshStandardMaterial({ color: 0xf4f7fb, roughness: 0.7 }));
  wall.position.y = -0.15;
  const roofShape = new Shape();
  roofShape.moveTo(-0.95, 0); roofShape.lineTo(0.95, 0); roofShape.lineTo(0, 0.72); roofShape.closePath();
  const roof = new Mesh(new ExtrudeGeometry(roofShape, { depth: 1.25, bevelEnabled: false }), new MeshStandardMaterial({ color: new Color(accent), roughness: 0.5 }));
  roof.position.set(0, 0.325, -0.625);
  const chimney = new Mesh(new BoxGeometry(0.17, 0.42, 0.17), new MeshStandardMaterial({ color: 0xc9d6e2, roughness: 0.8 }));
  chimney.position.set(0.5, 0.78, -0.2);
  const door = new Mesh(new PlaneGeometry(0.3, 0.52), new MeshStandardMaterial({ color: 0x17324a, roughness: 0.5 }));
  door.position.set(0, -0.365, 0.531);
  const knob = new Mesh(new CircleGeometry(0.025, 16), new MeshBasicMaterial({ color: 0xe0b25a }));
  knob.position.set(0.09, -0.37, 0.535);
  const winMats = [];
  [-0.48, 0.48].forEach(x => {
    const frame = new Mesh(new PlaneGeometry(0.34, 0.34), new MeshStandardMaterial({ color: 0x17324a }));
    frame.position.set(x, -0.05, 0.529);
    const pane = new Mesh(new PlaneGeometry(0.26, 0.26), new MeshBasicMaterial({ color: 0xffd98a }));
    pane.position.set(x, -0.05, 0.534);
    winMats.push(pane.material);
    g.add(frame, pane);
  });
  g.add(wall, roof, chimney, door, knob);

  // the key: a brass ring, a shaft and two teeth, circling the house
  const brass = new MeshStandardMaterial({ color: 0xe0b25a, metalness: 0.85, roughness: 0.3 });
  const key = new Group();
  const head = new Mesh(new TorusGeometry(0.12, 0.04, 12, 28), brass);
  const shaft = new Mesh(new CylinderGeometry(0.032, 0.032, 0.42, 12), brass);
  shaft.rotation.z = Math.PI / 2; shaft.position.x = 0.3;
  [0.4, 0.5].forEach(x => { const tooth = new Mesh(new BoxGeometry(0.05, 0.12, 0.05), brass); tooth.position.set(x, -0.07, 0); key.add(tooth); });
  key.add(head, shaft);
  g.add(key);

  const warm = new Color(0xffd98a), dim = new Color(0xc79a4a);
  return {
    camera: { pos: [0, 0.75, 5.5], fov: 30 },
    update(S) {
      g.rotation.y = Math.sin(S.t * 0.45) * 0.55 + S.tiltY;
      g.rotation.x = -0.12 + S.tiltX * 0.5;
      g.position.y = Math.sin(S.t * 1.0) * 0.04;
      const pulse = 0.5 + 0.5 * Math.sin(S.t * 1.7);
      winMats.forEach((m, i) => m.color.copy(dim).lerp(warm, clamp01(0.55 + 0.22 * Math.sin(S.t * 1.7 + i * 1.2) + 0.25 * pulse)));
      const a = S.t * 0.75;
      key.position.set(Math.cos(a) * 1.55, 0.3 + Math.sin(S.t * 1.3) * 0.1, Math.sin(a) * 0.95 + 0.2);
      key.rotation.set(0.3 * Math.sin(S.t), -a + Math.PI / 2, 0.5 * Math.sin(S.t * 0.8));
    },
  };
}

// ================================================================ scene: flow (start page)
// What the tool does, as a picture: clips go in on the left, captions are made in the middle, a finished card with a
// title comes out on the right, and light flows through all three.
function captionCardTexture(brand) {
  const [c, g] = canvasOf(256, 320);
  g.fillStyle = "rgba(14,30,48,.94)"; g.beginPath(); g.roundRect(6, 6, 244, 308, 26); g.fill();
  g.strokeStyle = brand; g.lineWidth = 5; g.beginPath(); g.roundRect(6, 6, 244, 308, 26); g.stroke();
  g.fillStyle = "#fff"; g.font = "800 92px Inter, system-ui, sans-serif"; g.textAlign = "left"; g.textBaseline = "alphabetic";
  g.fillText("Aa", 30, 130);
  [[200, 0.82], [236, 0.62], [272, 0.72]].forEach(([y, w]) => {
    g.fillStyle = "#fff"; g.beginPath(); g.roundRect(30, y, 196 * w, 16, 8); g.fill();
  });
  return texture(c);
}
function titleCardTexture(brand) {
  const [c, g] = canvasOf(256, 320);
  g.fillStyle = "rgba(244,247,251,.97)"; g.beginPath(); g.roundRect(6, 6, 244, 308, 26); g.fill();
  g.strokeStyle = brand; g.lineWidth = 5; g.beginPath(); g.roundRect(6, 6, 244, 308, 26); g.stroke();
  const grad = g.createLinearGradient(0, 24, 0, 170); grad.addColorStop(0, brand); grad.addColorStop(1, "#173a58");
  g.fillStyle = grad; g.beginPath(); g.roundRect(24, 24, 208, 146, 16); g.fill();
  g.fillStyle = "rgba(255,255,255,.95)"; g.beginPath(); g.moveTo(112, 66); g.lineTo(112, 128); g.lineTo(162, 97); g.closePath(); g.fill();
  [[190, 0.9], [220, 0.65]].forEach(([y, w]) => { g.fillStyle = "#1f2f3f"; g.beginPath(); g.roundRect(24, y, 208 * w, 15, 7); g.fill(); });
  ["#e53935", "#15151b", "#c13584", "#1877f2"].forEach((col, i) => { g.fillStyle = col; g.beginPath(); g.arc(40 + i * 34, 274, 13, 0, TAU); g.fill(); });
  return texture(c);
}

function buildFlow(ctx) {
  const { scene, brand, accent } = ctx;
  const g = new Group();
  scene.add(g);
  // on a light page the flowing dots are the deeper blue and plain; on a dark page they are pale and additive (they glow)
  const bg = cssColor("--bg", "#f3f7fb").replace("#", "");
  const lum = bg.length === 6 ? (parseInt(bg.slice(0, 2), 16) * 0.299 + parseInt(bg.slice(2, 4), 16) * 0.587 + parseInt(bg.slice(4, 6), 16) * 0.114) : 255;
  const lightPage = lum > 140;
  const glow = new Mesh(new PlaneGeometry(6.4, 4.2), new MeshBasicMaterial({ map: glowTexture("rgba(108,174,223,.5)"), transparent: true, depthWrite: false, opacity: 0.4 }));
  glow.position.z = -0.7;
  g.add(glow);

  // clips going in: a little stack on the left
  const tints = [brand, "#7fb8e3", "#4d94c8"];
  const stack = tints.map((tint, i) => {
    const m = new Mesh(new PlaneGeometry(0.72, 1.28), new MeshBasicMaterial({ map: clipTexture(tint, i), transparent: true, side: DoubleSide }));
    m.position.set(-2.15 + i * 0.13, -0.04 * i, -0.1 * i);
    g.add(m);
    return m;
  });
  // captions being made: the middle card
  const mid = new Mesh(new PlaneGeometry(1.1, 1.375), new MeshBasicMaterial({ map: captionCardTexture(brand), transparent: true, side: DoubleSide }));
  mid.position.set(0, 0, 0.1);
  g.add(mid);
  // the finished card with its title and the four places it goes
  const out = new Mesh(new PlaneGeometry(1.1, 1.375), new MeshBasicMaterial({ map: titleCardTexture(brand), transparent: true, side: DoubleSide }));
  out.position.set(2.15, 0, 0);
  g.add(out);

  // light flowing left to right through all three
  const COUNT = 70, pos = new Float32Array(COUNT * 3);
  const geo = new BufferGeometry();
  geo.setAttribute("position", new Float32BufferAttribute(pos, 3));
  const dots = new Points(geo, new PointsMaterial({ color: new Color(lightPage ? accent : "#bfe2fa"), size: lightPage ? 0.11 : 0.09,
    transparent: true, opacity: 0.95, depthWrite: false, blending: lightPage ? NormalBlending : AdditiveBlending }));
  g.add(dots);

  return {
    camera: { pos: [0, 0.1, 5.1], fov: 32, fitWidth: 6.2 },   // fitWidth: keep the whole clips -> card -> video row in view in a narrow box
    update(S) {
      g.rotation.y = Math.sin(S.t * 0.35) * 0.32 + S.tiltY;
      g.rotation.x = S.tiltX * 0.5;
      stack.forEach((m, i) => { m.position.y = -0.04 * i + Math.sin(S.t * 1.1 + i * 1.3) * 0.06; });
      mid.position.y = Math.sin(S.t * 1.0 + 0.8) * 0.05;
      mid.scale.setScalar(1 + 0.025 * Math.sin(S.t * 2.2));
      out.position.y = Math.sin(S.t * 1.0 + 2.1) * 0.05;
      const arr = geo.attributes.position.array;
      for (let i = 0; i < COUNT; i++) {
        const u = (i / COUNT + S.t * 0.16) % 1;               // 0 at the clips, 1 at the finished card
        const lane = (i * 7) % 5 - 2;                         // five lanes, spread a little in height and depth
        const x = -1.55 + u * 3.1;
        const y = lane * 0.16 + Math.sin(u * TAU * 1.5 + i) * 0.1;
        const z = 0.25 + (lane * 0.05);
        arr[i * 3] = x; arr[i * 3 + 1] = y; arr[i * 3 + 2] = z;
      }
      geo.attributes.position.needsUpdate = true;
    },
  };
}

// ================================================================ scene: ready (result page)
const BADGES = [["YT", "#e53935"], ["TT", "#15151b"], ["IG", "#c13584"], ["FB", "#1877f2"]];
function badgeTexture(label, color) {
  const [c, g] = canvasOf(128, 128);
  g.fillStyle = color; g.beginPath(); g.arc(64, 64, 60, 0, TAU); g.fill();
  g.strokeStyle = "rgba(255,255,255,.85)"; g.lineWidth = 4; g.beginPath(); g.arc(64, 64, 56, 0, TAU); g.stroke();
  g.fillStyle = "#fff"; g.font = "700 44px Inter, system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText(label, 64, 68);
  return texture(c);
}
function posterPlaceholder(brand) {
  const [c, g] = canvasOf(256, 460);
  const grad = g.createLinearGradient(0, 0, 0, 460);
  grad.addColorStop(0, brand); grad.addColorStop(1, "#173a58");
  g.fillStyle = grad; g.fillRect(0, 0, 256, 460);
  g.fillStyle = "rgba(255,255,255,.92)";
  g.beginPath(); g.moveTo(104, 190); g.lineTo(104, 270); g.lineTo(168, 230); g.closePath(); g.fill();
  return texture(c);
}

function buildReady(ctx) {
  const { scene, brand } = ctx;
  const g = new Group();
  scene.add(g);
  const card = new Group();
  const slab = new Mesh(
    new ExtrudeGeometry(roundedRect(1.0, 1.78, 0.12), { depth: 0.07, bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.025, bevelSegments: 3, curveSegments: 10 }),
    new MeshPhysicalMaterial({ color: 0xf4f7fb, roughness: 0.4, clearcoat: 0.6 }));
  slab.position.z = -0.05;
  const picMat = new MeshBasicMaterial({ map: posterPlaceholder(brand) });
  const pic = new Mesh(new PlaneGeometry(0.9, 1.66), picMat);
  pic.position.z = 0.062;
  card.add(slab, pic);
  g.add(card);

  const badges = BADGES.map(([label, color], i) => {
    const m = new Mesh(new CircleGeometry(0.27, 40), new MeshBasicMaterial({ map: badgeTexture(label, color), transparent: true, side: DoubleSide }));
    g.add(m);
    return { m, i, delay: 0.3 + i * 0.14 };
  });

  // a burst of sparks when it lands (deterministic, so it looks the same every time)
  const COUNT = 46, pos = new Float32Array(COUNT * 3), vel = [];
  for (let i = 0; i < COUNT; i++) {
    const a = (i / COUNT) * TAU * 3.7, e = ((i * 37) % 17) / 17 - 0.5, sp = 1.1 + (((i * 53) % 11) / 11) * 1.6;
    vel.push(new Vector3(Math.cos(a) * sp, e * sp + 0.4, Math.sin(a) * sp * 0.5));
  }
  const geo = new BufferGeometry();
  geo.setAttribute("position", new Float32BufferAttribute(pos, 3));
  const sparkMat = new PointsMaterial({ color: new Color(brand), size: 0.07, transparent: true, opacity: 0, depthWrite: false });
  g.add(new Points(geo, sparkMat));

  return {
    camera: { pos: [0, 0, 5.7], fov: 30 },
    setPoster(img) {
      const [c, cg] = canvasOf(256, 460);
      const s = Math.max(256 / img.naturalWidth, 460 / img.naturalHeight);
      const w = img.naturalWidth * s, h = img.naturalHeight * s;
      cg.drawImage(img, (256 - w) / 2, (460 - h) / 2, w, h);
      const old = picMat.map;
      picMat.map = texture(c); picMat.needsUpdate = true;
      if (old) old.dispose();
    },
    update(S) {
      const k = S.done ? S.doneAt : -1;                    // seconds since "ready"
      const pop = k < 0 ? 0 : easeOutBack(k / 0.7);
      card.visible = k >= 0;
      card.scale.setScalar(Math.max(0.001, pop));
      card.rotation.y = (1 - easeOut(k / 1.0)) * -TAU * 0.5 + Math.sin(S.t * 0.6) * 0.16 + S.tiltY;
      card.rotation.x = S.tiltX * 0.6;
      card.position.y = Math.sin(S.t * 1.2) * 0.05;
      badges.forEach(b => {
        const e = easeOut((k - b.delay) / 0.9);
        const ang = (b.i / badges.length) * TAU + S.t * 0.8;
        const rad = 4.6 - (4.6 - 1.45) * e;
        b.m.visible = k >= b.delay;
        b.m.position.set(Math.cos(ang) * rad, Math.sin(ang + b.i) * 0.28 + (1 - e) * 1.2, Math.sin(ang) * rad * 0.45 + 0.35);
        b.m.scale.setScalar(0.4 + 0.6 * e);
      });
      const arr = geo.attributes.position.array;
      const life = k < 0 ? 0 : clamp01(k / 1.5);
      for (let i = 0; i < COUNT; i++) {
        arr[i * 3] = vel[i].x * life * 1.5;
        arr[i * 3 + 1] = vel[i].y * life * 1.5 - life * life * 0.6;
        arr[i * 3 + 2] = vel[i].z * life * 1.5;
      }
      geo.attributes.position.needsUpdate = true;
      sparkMat.opacity = k < 0 || life >= 1 ? 0 : (1 - life) * 0.95;
    },
  };
}

// ================================================================ scene: week (My videos)
// Her week as a picture: seven rounded day blocks on a soft base. A calm wave rises across them and three to five of the
// days glow in the brand colour and stand taller, like the "3 to 5 videos a week" goal being met. Nothing is real data.
function buildWeek(ctx) {
  const { scene, brand, accent } = ctx;
  const g = new Group();
  scene.add(g);
  const base = new Mesh(new BoxGeometry(4.0, 0.08, 1.0), new MeshStandardMaterial({ color: 0xe8eff6, roughness: 0.85 }));
  base.position.y = -0.62;
  const glow = new Mesh(new CircleGeometry(2.4, 48), new MeshBasicMaterial({ map: glowTexture("rgba(108,174,223,.35)"), transparent: true, depthWrite: false }));
  glow.rotation.x = -Math.PI / 2; glow.position.y = -0.575;
  g.add(base, glow);
  const lit = [1, 2, 4, 5];   // four of seven days: inside the goal
  const on = new Color(brand), deep = new Color(accent), off = new Color(0xd3dfea);
  const days = [];
  for (let i = 0; i < 7; i++) {
    const mat = new MeshPhysicalMaterial({ color: off.clone(), roughness: 0.45, clearcoat: 0.5 });
    const m = new Mesh(new BoxGeometry(0.36, 1, 0.36), mat);
    m.position.x = (i - 3) * 0.52;
    g.add(m);
    const cap = lit.includes(i) ? new Mesh(new CircleGeometry(0.1, 24), new MeshBasicMaterial({ map: glowTexture("rgba(255,255,255,.95)"), transparent: true, depthWrite: false, blending: AdditiveBlending })) : null;
    if (cap) g.add(cap);
    days.push({ m, mat, cap, i, goal: lit.includes(i) ? 0.75 + ((i * 7) % 5) * 0.09 : 0.22 + ((i * 3) % 4) * 0.04 });
  }
  return {
    camera: { pos: [0, 0.35, 5.4], fov: 30 },
    update(S) {
      g.rotation.y = Math.sin(S.t * 0.3) * 0.28 + S.tiltY * 0.8;
      g.rotation.x = 0.22 + S.tiltX * 0.4;
      g.position.y = 0.05;
      const cycle = (S.t % 9) / 9;   // rises over the first part, rests, then settles back softly
      const rise = cycle < 0.55 ? easeOut(cycle / 0.55) : cycle < 0.85 ? 1 : 1 - 0.35 * easeOut((cycle - 0.85) / 0.15);
      days.forEach(d => {
        const k = clamp01(rise * 1.6 - d.i * 0.09);
        const h = 0.12 + (d.goal - 0.12) * easeOut(k) + Math.sin(S.t * 1.4 + d.i) * 0.012;
        d.m.scale.y = h;
        d.m.position.y = -0.58 + h / 2;
        d.mat.color.copy(off).lerp(lit.includes(d.i) ? (d.i % 2 ? on : deep) : off, lit.includes(d.i) ? k : 0);
        if (d.cap) { d.cap.position.set(d.m.position.x, -0.58 + h + 0.12, 0.2); d.cap.material.opacity = k * (0.7 + 0.3 * Math.sin(S.t * 2 + d.i)); }
      });
    },
  };
}

// ================================================================ scene: skyline (Team page)
// A calm results skyline: a small grid of slim towers of different heights that breathe slowly, with a thin band of light
// sweeping across them like a chart being read. Quiet and slow on purpose.
function buildSkyline(ctx) {
  const { scene, brand, accent } = ctx;
  const g = new Group();
  scene.add(g);
  const floor = new Mesh(new PlaneGeometry(4.2, 2.6), new MeshBasicMaterial({ map: glowTexture("rgba(44,118,174,.35)"), transparent: true, depthWrite: false }));
  floor.rotation.x = -Math.PI / 2; floor.position.y = -0.6;
  g.add(floor);
  const cols = 7, rows = 3, towers = [];
  const c1 = new Color(brand), c2 = new Color(accent), pale = new Color(0xeef3f8);
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const n = r * cols + c;
    const h = 0.25 + (((n * 37) % 23) / 23) * 0.95 * (1 - r * 0.18);
    const mat = new MeshStandardMaterial({ color: pale.clone().lerp(r === 0 ? c1 : c2, 0.45 + r * 0.2), roughness: 0.5 });
    const m = new Mesh(new BoxGeometry(0.3, 1, 0.3), mat);
    m.position.set((c - (cols - 1) / 2) * 0.46, 0, (r - 1) * 0.46);
    g.add(m);
    towers.push({ m, mat, h, c, base: mat.color.clone() });
  }
  const band = new Mesh(new PlaneGeometry(0.16, 2.2), new MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.0, depthWrite: false, blending: AdditiveBlending, side: DoubleSide }));
  band.rotation.x = -Math.PI / 2; band.position.y = 0.9;
  g.add(band);
  const hi = new Color(0xffffff);
  return {
    camera: { pos: [0, 0.3, 5.0], fov: 30 },
    update(S) {
      g.rotation.y = -0.35 + Math.sin(S.t * 0.18) * 0.22 + S.tiltY * 0.7;
      g.rotation.x = 0.42 + S.tiltX * 0.3;
      g.position.y = 0.2;   // lifted so the front row is not cut off at the bottom of its box
      const sweep = ((S.t * 0.22) % 1.4) - 0.2;   // 0..1 across, then a pause
      const sx = (sweep - 0.5) * 3.4;
      band.position.x = sx;
      band.material.opacity = sweep > 0 && sweep < 1 ? 0.18 : 0;
      towers.forEach(t => {
        const h = t.h * (0.92 + 0.08 * Math.sin(S.t * 0.7 + t.c * 0.6));
        t.m.scale.y = h; t.m.position.y = -0.6 + h / 2;
        const near = Math.max(0, 1 - Math.abs(t.m.position.x - sx) / 0.5) * (sweep > 0 && sweep < 1 ? 1 : 0);
        t.mat.color.copy(t.base).lerp(hi, near * 0.35);
      });
    },
  };
}

// ================================================================ engine
const BUILDERS = { work: buildWork, house: buildHouse, flow: buildFlow, ready: buildReady, week: buildWeek, skyline: buildSkyline };

export function supported() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl")));
  } catch (_) { return false; }
}

// opts.kind: "work" (default) | "house" | "flow" | "ready" | "week" | "skyline".  opts.idle: for "work", nothing is being made: clips just orbit.
export function mount(container, opts) {
  opts = opts || {};
  const kind = BUILDERS[opts.kind] ? opts.kind : "work";
  const idle = !!opts.idle;
  if (!supported()) return null;
  const brand = cssColor("--brand", "#6caedf"), accent = cssColor("--accent", "#2c76ae");
  let renderer;
  try {
    renderer = new WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  } catch (_) { return null; }
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  const canvas = renderer.domElement;
  canvas.style.cssText = "display:block;width:100%;height:100%;touch-action:pan-y";
  container.appendChild(canvas);

  const scene = new Scene();
  const camera = new PerspectiveCamera(30, 1, 0.1, 50);
  lights(scene, brand);
  const built = BUILDERS[kind]({ scene, brand, accent, idle });
  camera.position.set(...built.camera.pos);
  const baseFov = built.camera.fov;
  const fitW = built.camera.fitWidth || 0, dist = Math.hypot(...built.camera.pos);

  const S = { t: 0, dt: 0.016, shown: idle ? 0.35 : 0, target: idle ? 0.35 : 0, done: 0, doneAt: 0, idle, tiltX: 0, tiltY: 0 };
  let aimX = 0, aimY = 0, visible = true, running = true, raf = 0, last = performance.now();

  function resize() {
    const w = Math.max(1, container.clientWidth), h = Math.max(1, container.clientHeight);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    let fov = w / h < 1.5 ? baseFov + 6 : baseFov;
    if (fitW) {   // widen just enough that the scene's own width still fits (a narrow box would otherwise cut off its sides)
      const need = 2 * Math.atan(fitW / 2 / dist / camera.aspect) * 180 / Math.PI;
      fov = Math.max(fov, Math.min(need, 70));
    }
    camera.fov = fov;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize); ro.observe(container); resize();

  function onMove(e) {
    const r = container.getBoundingClientRect();
    const p = e.touches && e.touches[0] ? e.touches[0] : e;
    aimY = MathUtils.clamp(((p.clientX - r.left) / r.width - 0.5) * 2, -1, 1) * 0.55;
    aimX = MathUtils.clamp(((p.clientY - r.top) / r.height - 0.5) * 2, -1, 1) * 0.25;
  }
  function onLeave() { aimX = aimY = 0; }
  container.addEventListener("pointermove", onMove);
  container.addEventListener("pointerleave", onLeave);
  const io = new IntersectionObserver(es => { visible = es[0].isIntersecting; if (visible) kick(); });
  io.observe(container);
  const onVis = () => { if (!document.hidden) kick(); };
  document.addEventListener("visibilitychange", onVis);

  function frame(now) {
    raf = 0;
    if (!running || !visible || document.hidden) return;
    S.dt = Math.min(0.05, (now - last) / 1000); last = now;
    S.t = now / 1000;
    S.shown += (S.target - S.shown) * Math.min(1, S.dt * 3.2);
    if (S.done) S.doneAt += S.dt;
    S.tiltX += (aimX - S.tiltX) * Math.min(1, S.dt * 5);
    S.tiltY += (aimY - S.tiltY) * Math.min(1, S.dt * 5);
    built.update(S);
    renderer.render(scene, camera);
    raf = requestAnimationFrame(frame);
  }
  function kick() { if (!raf && running) { last = performance.now(); raf = requestAnimationFrame(frame); } }
  kick();

  return {
    kind,
    setProgress(f) { if (!idle) S.target = MathUtils.clamp(+f || 0, 0, 1); },
    setDone() { S.done = 1; S.doneAt = 0; S.target = 1; },
    // the finished video's first picture (same site only), used by the "ready" scene
    setPoster(url) {
      if (!built.setPoster) return;
      const img = new Image();
      img.onload = () => { if (running) built.setPoster(img); };
      img.src = url;
    },
    dispose() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      io.disconnect(); ro.disconnect();
      container.removeEventListener("pointermove", onMove);
      container.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVis);
      scene.traverse(o => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { if (m.map) m.map.dispose(); m.dispose(); });
      });
      renderer.dispose();
      if (renderer.forceContextLoss) renderer.forceContextLoss();
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    },
  };
}
