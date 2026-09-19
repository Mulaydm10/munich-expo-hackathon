/* FlexGrid 3D depot scene.
 *
 * Geometry note: the depot is built procedurally by ./depot-model.js so the
 * page has no binary dependency. To use the exported GLB instead, replace
 * buildDepot() below with a GLTFLoader load of assets/depot.glb — the mesh and
 * group names are identical (see README "mesh-name → animation mapping").
 */
import * as THREE from '/static/vendor/three.module.js';
import { OrbitControls } from '/static/vendor/OrbitControls.js';
import { buildDepot } from './app-depot-model.js';

export function webglAvailable() {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch (e) {
    return false;
  }
}

const SKY = {
  evening: { top: 0x1b2b48, ground: 0x0a1020, sun: 0xffb46a, sunI: 0.55, hemi: 0.5 },
  night: { top: 0x080e1c, ground: 0x05080f, sun: 0x6f8ec0, sunI: 0.18, hemi: 0.26 },
  dawn: { top: 0x2a3552, ground: 0x121a28, sun: 0xffd0a0, sunI: 0.5, hemi: 0.55 },
};
const mix = (a, b, t) => a + (b - a) * t;
function lerpColor(a, b, t) {
  const ca = new THREE.Color(a), cb = new THREE.Color(b);
  return ca.lerp(cb, t);
}

export class DepotScene {
  constructor(mount, { mode = 'hero', orbit = false } = {}) {
    this.mount = mount;
    this.mode = mode;
    this.clock = new THREE.Clock();
    this.pulses = [];
    this.parallax = { x: 0, y: 0 };
    this.timeHour = 20; // local hour, 18 → 30 (next morning)
    this.phase = 'baseline';
    this._boot(orbit);
  }

  _boot(orbit) {
    // preserveDrawingBuffer keeps the last frame readable for screenshots
    const r = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
    r.setPixelRatio(Math.min(devicePixelRatio, this.mode === 'mini' ? 1.4 : 2));
    r.shadowMap.enabled = this.mode === 'hero';
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = 1.02;
    this.renderer = r;
    this.mount.appendChild(r.domElement);
    r.domElement.style.cssText = 'display:block;width:100%;height:100%';

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x070b14);
    scene.fog = new THREE.Fog(0x070b14, 150, 520);
    this.scene = scene;

    this.hemi = new THREE.HemisphereLight(0x2a3d63, 0x070a12, 0.4);
    scene.add(this.hemi);
    this.moon = new THREE.DirectionalLight(0x8fb0e6, 0.4);
    this.moon.position.set(-46, 44, 36);
    this.moon.castShadow = this.mode === 'hero';
    if (this.moon.shadow) {
      this.moon.shadow.mapSize.set(1024, 1024);
      const d = 46;
      Object.assign(this.moon.shadow.camera, { left: -d, right: d, top: d, bottom: -d, near: 1, far: 190 });
    }
    scene.add(this.moon);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.5, 800);
    this.model = buildDepot();
    scene.add(this.model);
    this._index();

    if (orbit) {
      this.controls = new OrbitControls(this.camera, r.domElement);
      this.controls.enableDamping = true;
      this.controls.maxPolarAngle = Math.PI * 0.49;
      this.controls.target.set(0, 3, 0);
    }
    this.camera.position.set(this.mode === 'mini' ? 30 : 108, this.mode === 'mini' ? 20 : 42, this.mode === 'mini' ? 40 : 128);
    this.camera.lookAt(0, 3, 0);
    this.home = this.mode === 'mini' ? new THREE.Vector3(27, 15, 34) : new THREE.Vector3(26, 10, 34);

    this._peakWall();
    this._buildPulses();
    this.setTime(20);

    this.ro = new ResizeObserver(() => this._resize());
    this.ro.observe(this.mount);
    addEventListener('resize', () => this._resize());
    this._resize();
    requestAnimationFrame(() => this._resize());
    this._loop = this._loop.bind(this);
    this._frames = 0;
    r.render(this.scene, this.camera); // paint one frame immediately
    r.setAnimationLoop(this._loop);
    // some embedding contexts throttle rAF; fall back to a timer loop
    setTimeout(() => {
      if (this._frames < 2) {
        r.setAnimationLoop(null);
        this._timer = setInterval(this._loop, 33);
      }
    }, 1200);
  }

  /* per-part handles + private materials so each charger/car animates alone */
  _index() {
    this.chargers = [];
    this.cars = [];
    this.model.traverse((o) => {
      if (!o.isGroup) return;
      if (/^charger_\d+$/.test(o.name)) this.chargers.push(o);
      if (/^car_\d+$/.test(o.name)) this.cars.push(o);
      if (o.name === 'transformer') this.transformer = o;
      if (o.name === 'canopy') this.canopy = o;
    });
    this.chargers.sort((a, b) => +a.name.split('_')[1] - +b.name.split('_')[1]);
    this.chargers.forEach((g) => {
      g.userData.lit = [];
      g.traverse((m) => {
        if (m.isMesh && /(status_bar|screen_glow)$/.test(m.name)) {
          m.material = m.material.clone();
          g.userData.lit.push(m.material);
        }
      });
      g.userData.lit.forEach((mm) => (mm.emissiveIntensity = 0.05));
    });
    this.cars.forEach((g) => {
      g.traverse((m) => {
        if (m.isMesh && /charge_port$/.test(m.name)) {
          m.material = m.material.clone();
          g.userData.port = m.material;
          m.material.emissiveIntensity = 0.3;
        }
      });
    });
    this.txLight = null;
    this.model.traverse((m) => { if (m.isMesh && m.name === 'transformer_status_light') { m.material = m.material.clone(); this.txLight = m.material; } });
  }

  _peakWall() {
    const geo = new THREE.PlaneGeometry(52, 14, 1, 1);
    const mat = new THREE.MeshBasicMaterial({ color: 0xff8a4a, transparent: true, opacity: 0.0, side: THREE.DoubleSide, depthWrite: false });
    const wall = new THREE.Mesh(geo, mat);
    wall.name = 'peak_wall';
    wall.position.set(0, 7, -15.5);
    wall.scale.y = 1;
    this.scene.add(wall);
    this.peakWall = wall;
  }

  _buildPulses() {
    const geo = new THREE.SphereGeometry(0.26, 10, 8);
    const mat = new THREE.MeshBasicMaterial({ color: 0x8be8ff });
    const tx = new THREE.Vector3(25.5, 0.6, -9.5);
    this.chargers.forEach((c, i) => {
      const target = new THREE.Vector3();
      c.getWorldPosition(target);
      target.y = 0.5;
      const m = new THREE.Mesh(geo, mat.clone());
      m.name = `pulse_${i + 1}`;
      m.visible = false;
      m.userData = { from: tx.clone(), to: target, t: Math.random(), speed: 0.16 + Math.random() * 0.1, paused: false, shifted: false };
      this.scene.add(m);
      this.pulses.push(m);
    });
  }

  /* ---- public animation API ---- */
  setTime(hour) {
    this.timeHour = hour;
    const h = ((hour % 24) + 24) % 24;
    let a, b, t;
    if (h >= 18 && h < 22) { a = SKY.evening; b = SKY.night; t = (h - 18) / 4; }
    else if (h >= 22 || h < 4) { a = SKY.night; b = SKY.night; t = 0; }
    else { a = SKY.night; b = SKY.dawn; t = Math.min(1, (h - 4) / 4); }
    this.hemi.intensity = mix(a.hemi, b.hemi, t);
    this.hemi.color = lerpColor(a.top, b.top, t);
    this.hemi.groundColor = lerpColor(a.ground, b.ground, t);
    this.moon.intensity = mix(a.sunI, b.sunI, t);
    this.moon.color = lerpColor(a.sun, b.sun, t);
    const sky = lerpColor(a.ground, b.ground, t);
    this.scene.background.copy(sky);
    this.scene.fog.color.copy(sky);
  }

  /** load 0..1 of envelope — drives pulse density, transformer light, wall height */
  setLoad(frac, { phase = this.phase } = {}) {
    this.phase = phase;
    this.loadFrac = frac;
    const active = Math.round(THREE.MathUtils.clamp(frac, 0, 1) * this.pulses.length);
    this.pulses.forEach((p, i) => { p.visible = i < active; p.userData.speed = 0.13 + frac * 0.24; });
    this.chargers.forEach((c, i) => {
      const on = i < active;
      c.userData.lit.forEach((m) => { m.emissiveIntensity = on ? (phase === 'optimised' ? 1.3 : 1.8) : 0.06; });
    });
    if (this.txLight) {
      this.txLight.emissive.set(phase === 'optimised' ? 0x2ecfe8 : frac > 0.82 ? 0xff7a3c : 0x2ecfe8);
      this.txLight.emissiveIntensity = 0.9 + frac * 1.6;
    }
  }

  setPeakWall(level) { // 0 hidden, 1 full height
    const l = THREE.MathUtils.clamp(level, 0, 1);
    this.peakWall.material.opacity = l * 0.3;
    this.peakWall.scale.y = 0.25 + l * 0.85;
    this.peakWall.position.y = 3.2 + l * 4.2;
  }

  setReady(n) { // n cars showing a departure-ready marker
    this.cars.forEach((c, i) => {
      if (!c.userData.port) return;
      const ready = i < n;
      c.userData.port.emissive.set(ready ? 0xb9e84a : 0x2ecfe8);
      c.userData.port.emissiveIntensity = ready ? 2.2 : 1.0;
    });
  }

  flyTo(pos, target = new THREE.Vector3(0, 3, 0), ms = 2600) {
    this._fly = { from: this.camera.position.clone(), to: pos.clone(), fromT: this._camTarget().clone(), toT: target.clone(), t0: performance.now(), ms };
  }
  _camTarget() {
    if (this.controls) return this.controls.target;
    if (!this._lookAt) this._lookAt = new THREE.Vector3(0, 3, 0);
    return this._lookAt;
  }

  setParallax(nx, ny) { this.parallax.x = nx; this.parallax.y = ny; }

  _resize() {
    const w = this.mount.clientWidth, h = this.mount.clientHeight;
    if (!w || !h) return false; // no layout yet — try again next frame
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    return true;
  }

  _checkSize() {
    const ratio = this.renderer.getPixelRatio();
    const want = Math.round((this.mount.clientWidth || 0) * ratio);
    if (want && this.renderer.domElement.width !== want) this._resize();
  }

  _loop() {
    this._frames++;
    this._checkSize();
    const dt = Math.min(this.clock.getDelta(), 0.05);
    const now = performance.now();
    if (this._fly) {
      const f = this._fly;
      const k = Math.min(1, (now - f.t0) / f.ms);
      const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      this.camera.position.lerpVectors(f.from, f.to, e);
      this._camTarget().lerpVectors(f.fromT, f.toT, e);
      if (k >= 1) this._fly = null;
    }
    // pointer parallax (subtle, hero only)
    if (this.mode === 'hero' && !this._fly) {
      const base = this.home;
      this.camera.position.x += ((base.x + this.parallax.x * 4.2) - this.camera.position.x) * 0.045;
      this.camera.position.y += ((base.y - this.parallax.y * 1.8) - this.camera.position.y) * 0.045;
      this.camera.position.z += (base.z - this.camera.position.z) * 0.03;
    }
    if (this.controls) this.controls.update();
    else this.camera.lookAt(this._camTarget());

    this.pulses.forEach((p) => {
      const u = p.userData;
      if (!p.visible) return;
      if (!u.paused) u.t = (u.t + dt * u.speed) % 1;
      p.position.lerpVectors(u.from, u.to, u.t);
      p.position.y = 0.55 + Math.sin(u.t * Math.PI) * 1.1;
      p.material.color.set(u.shifted ? 0xb9e84a : 0x8be8ff);
      p.scale.setScalar(u.paused ? 0.55 : 1);
    });
    this.renderer.render(this.scene, this.camera);
  }

  /** move a share of pulses into the "shifted to later" state */
  shiftPulses(frac) {
    const n = Math.round(this.pulses.length * frac);
    this.pulses.forEach((p, i) => {
      p.userData.shifted = i < n;
      p.userData.paused = i >= n && i < n + 3;
    });
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    if (this._timer) clearInterval(this._timer);
    this.ro.disconnect();
    this.renderer.dispose();
    while (this.mount.firstChild) this.mount.removeChild(this.mount.firstChild);
  }
}
