import * as THREE from '/static/vendor/three.module.js';

const M = {};
function mat(name, color, opts = {}) {
  const m = new THREE.MeshStandardMaterial({ color: new THREE.Color(color), roughness: 0.8, metalness: 0.1, ...opts });
  m.name = name;
  M[name] = m;
  return m;
}

mat('asphalt', '#23262c', { roughness: 0.97 });
mat('concrete_pale', '#a9adb4', { roughness: 0.85 });
mat('concrete_pad', '#787e87', { roughness: 0.92 });
mat('aluminium', '#c3c8d1', { roughness: 0.34, metalness: 0.4 });
mat('soffit_panel', '#e7eaf0', { roughness: 0.6, emissive: new THREE.Color('#aebfdc'), emissiveIntensity: 0.16 });
mat('graphite', '#2c3037', { roughness: 0.5, metalness: 0.3 });
mat('deep_blue', '#12307f', { roughness: 0.32, metalness: 0.34 });
mat('pv_glass', '#111a2e', { roughness: 0.18, metalness: 0.32 });
mat('glass_dark', '#0d1420', { roughness: 0.14, metalness: 0.32 });
mat('led_warm', '#fff2dc', { emissive: new THREE.Color('#ffb861'), emissiveIntensity: 2.0, roughness: 0.4 });
mat('led_white', '#ffffff', { emissive: new THREE.Color('#e8f1ff'), emissiveIntensity: 2.4, roughness: 0.4 });
mat('led_blue', '#d5ebff', { emissive: new THREE.Color('#3f86ff'), emissiveIntensity: 1.5, roughness: 0.4 });
mat('led_cyan', '#dcfbff', { emissive: new THREE.Color('#2ecfe8'), emissiveIntensity: 1.4, roughness: 0.4 });
mat('paint_line', '#e9edf3', { roughness: 0.8 });
mat('rubber', '#0e1116', { roughness: 1.0 });
mat('car_paint', '#59707c', { roughness: 0.28, metalness: 0.32 });
mat('car_paint_dark', '#2f3a44', { roughness: 0.3, metalness: 0.32 });

function box(name, w, h, d, material, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  m.name = name; m.position.set(x, y + h / 2, z);
  return m;
}
function cyl(name, rTop, rBot, h, material, x = 0, y = 0, z = 0, seg = 20) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(rTop, rBot, h, seg), material);
  m.name = name; m.position.set(x, y + h / 2, z);
  return m;
}
const UP = new THREE.Vector3(0, 1, 0);
function strut(name, from, to, thick, material, taper = 1) {
  const a = new THREE.Vector3(...from), b = new THREE.Vector3(...to);
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length();
  const m = new THREE.Mesh(new THREE.CylinderGeometry(thick * taper, thick, len, 12), material);
  m.name = name;
  m.position.copy(a).addScaledVector(dir, 0.5);
  m.quaternion.setFromUnitVectors(UP, dir.clone().normalize());
  return m;
}

const SITE_W = 62, SITE_D = 40;
const CAN_W = 46, CAN_D = 23, CAN_Y = 7.4, CAN_T = 0.42;
const FORECOURT_W = CAN_W + 4, FORECOURT_D = CAN_D + 5;

/* ---------- site ---------- */
function buildSite() {
  const g = new THREE.Group();
  g.name = 'site';
  g.add(box('apron_asphalt', SITE_W, 0.3, SITE_D, M.asphalt, 0, -0.3, 0));
  g.add(box('apron_kerb', SITE_W + 0.7, 0.13, SITE_D + 0.7, M.concrete_pale, 0, -0.41, 0));
  // pale poured forecourt slab under and around the canopy
  g.add(box('forecourt_slab', FORECOURT_W, 0.06, FORECOURT_D, M.concrete_pad, 0, 0, 0));
  g.add(box('forecourt_reveal_north', FORECOURT_W + 0.2, 0.04, 0.1, M.led_blue, 0, 0.06, -FORECOURT_D / 2));
  g.add(box('forecourt_reveal_south', FORECOURT_W + 0.2, 0.04, 0.1, M.led_blue, 0, 0.06, FORECOURT_D / 2));
  g.add(box('lane_centre', CAN_W - 3, 0.02, 0.14, M.paint_line, 0, 0.065, 0));
  return g;
}

/* ---------- canopy ---------- */
function buildCanopy() {
  const g = new THREE.Group();
  g.name = 'canopy';
  g.add(box('canopy_deck', CAN_W, CAN_T, CAN_D, M.aluminium, 0, CAN_Y, 0));

  // coffered soffit: recessed panels between the beams
  const bays = 8, bw = CAN_W / bays;
  const soffit = new THREE.Group(); soffit.name = 'canopy_soffit';
  for (let i = 0; i < bays; i++) {
    const x = -CAN_W / 2 + bw * (i + 0.5);
    soffit.add(box(`soffit_panel_${i + 1}`, bw - 0.34, 0.1, CAN_D - 0.5, M.soffit_panel, x, CAN_Y - 0.1, 0));
  }
  g.add(soffit);
  for (let i = 0; i <= bays; i++) {
    const x = -CAN_W / 2 + bw * i;
    g.add(box(`canopy_beam_${i + 1}`, 0.2, 0.2, CAN_D - 0.2, M.aluminium, x, CAN_Y - 0.2, 0));
  }

  // recessed linear light coves
  [-CAN_D / 4, 0, CAN_D / 4].forEach((z, i) => {
    g.add(box(`canopy_cove_housing_${i + 1}`, CAN_W - 1.2, 0.12, 0.6, M.aluminium, 0, CAN_Y - 0.18, z));
    g.add(box(`canopy_cove_light_${i + 1}`, CAN_W - 1.6, 0.06, 0.4, M.led_white, 0, CAN_Y - 0.19, z));
  });

  // precision fascia: aluminium band, shadow gap, tucked warm light line
  const band = 0.34;
  const sides = [
    ['north', CAN_W + 0.6, band, 0.2, 0, -CAN_D / 2 - 0.1],
    ['south', CAN_W + 0.6, band, 0.2, 0, CAN_D / 2 + 0.1],
  ];
  sides.forEach(([n, w, h, d, x, z]) => {
    g.add(box(`canopy_fascia_${n}`, w, h, d, M.aluminium, x, CAN_Y + CAN_T - h, z));
    g.add(box(`canopy_shadow_gap_${n}`, w - 0.1, 0.1, 0.14, M.graphite, x, CAN_Y - 0.06, z + (z > 0 ? 0.02 : -0.02)));
    g.add(box(`canopy_lightline_${n}`, w - 0.3, 0.1, 0.1, M.led_warm, x, CAN_Y - 0.2, z + (z > 0 ? 0.04 : -0.04)));
  });
  [['west', -CAN_W / 2 - 0.1], ['east', CAN_W / 2 + 0.1]].forEach(([n, x]) => {
    g.add(box(`canopy_fascia_${n}`, 0.2, band, CAN_D + 0.6, M.aluminium, x, CAN_Y + CAN_T - band, 0));
    g.add(box(`canopy_shadow_gap_${n}`, 0.14, 0.1, CAN_D + 0.5, M.graphite, x + (x > 0 ? 0.02 : -0.02), CAN_Y - 0.06, 0));
    g.add(box(`canopy_lightline_${n}`, 0.1, 0.1, CAN_D + 0.3, M.led_warm, x + (x > 0 ? 0.04 : -0.04), CAN_Y - 0.2, 0));
  });

  // photovoltaic field, framed
  const cols = 12, rows = 6;
  const pw = (CAN_W - 2.2) / cols, pd = (CAN_D - 2.2) / rows;
  const pv = new THREE.Group(); pv.name = 'canopy_pv_array';
  for (let i = 0; i < cols; i++) {
    for (let j = 0; j < rows; j++) {
      const x = -CAN_W / 2 + 1.1 + pw * (i + 0.5);
      const z = -CAN_D / 2 + 1.1 + pd * (j + 0.5);
      pv.add(box(`pv_panel_${i + 1}_${j + 1}`, pw - 0.1, 0.06, pd - 0.1, M.pv_glass, x, CAN_Y + CAN_T, z));
    }
  }
  g.add(pv);
  g.add(box('pv_frame_north', CAN_W - 1.4, 0.1, 0.12, M.aluminium, 0, CAN_Y + CAN_T, -CAN_D / 2 + 1.0));
  g.add(box('pv_frame_south', CAN_W - 1.4, 0.1, 0.12, M.aluminium, 0, CAN_Y + CAN_T, CAN_D / 2 - 1.0));

  for (let i = 0; i < 4; i++) {
    const p = new THREE.PointLight(0xe6f0ff, 95, 36, 2);
    p.name = `canopy_light_source_${i + 1}`;
    p.position.set(-17 + i * 11.3, CAN_Y - 0.7, 0);
    g.add(p);
  }
  const warm = new THREE.PointLight(0xffb45c, 34, 24, 2);
  warm.name = 'canopy_edge_light_source';
  warm.position.set(0, CAN_Y - 0.5, CAN_D / 2);
  g.add(warm);
  return g;
}

/* ---------- branching column ---------- */
function buildColumn(i, x) {
  const g = new THREE.Group();
  g.name = `column_${i}`;
  g.add(box(`column_${i}_plinth`, 1.7, 0.22, 1.7, M.concrete_pale, x, 0.02, 0));
  g.add(box(`column_${i}_plinth_reveal`, 1.86, 0.05, 1.86, M.led_blue, x, 0.02, 0));
  g.add(cyl(`column_${i}_shoe`, 0.46, 0.56, 0.34, M.aluminium, x, 0.24, 0));
  g.add(cyl(`column_${i}_trunk`, 0.3, 0.44, 3.5, M.deep_blue, x, 0.58, 0, 24));
  const y0 = 4.0, yTop = CAN_Y - 0.26;
  [[-2.3, -3.2], [2.3, -3.2], [-2.3, 3.2], [2.3, 3.2]].forEach(([dx, dz], k) => {
    g.add(strut(`column_${i}_arm_${k + 1}`, [x, y0 - 0.1, 0], [x + dx, yTop, dz], 0.19, M.deep_blue, 0.6));
  });
  g.add(cyl(`column_${i}_collar`, 0.4, 0.46, 0.26, M.aluminium, x, y0 - 0.22, 0, 24));
  return g;
}

/* ---------- charger ---------- */
function buildCharger(i, x, z, facing) {
  const g = new THREE.Group();
  g.name = `charger_${i}`;
  g.add(box(`charger_${i}_body`, 0.96, 1.86, 0.54, M.graphite, 0, 0, 0));
  g.add(box(`charger_${i}_side_accent_l`, 0.05, 1.7, 0.56, M.deep_blue, -0.5, 0.08, 0));
  g.add(box(`charger_${i}_side_accent_r`, 0.05, 1.7, 0.56, M.deep_blue, 0.5, 0.08, 0));
  g.add(box(`charger_${i}_top_cap`, 1.04, 0.09, 0.62, M.aluminium, 0, 1.86, 0));
  g.add(box(`charger_${i}_front_panel`, 0.82, 1.6, 0.04, M.graphite, 0, 0.14, 0.28));
  g.add(box(`charger_${i}_screen`, 0.62, 0.46, 0.04, M.glass_dark, 0, 1.16, 0.3));
  g.add(box(`charger_${i}_screen_glow`, 0.56, 0.4, 0.02, M.led_cyan, 0, 1.19, 0.32));
  g.add(box(`charger_${i}_status_bar`, 0.74, 0.06, 0.03, M.led_blue, 0, 1.74, 0.31));
  g.add(box(`charger_${i}_base_shadow_gap`, 1.0, 0.07, 0.58, M.rubber, 0, 0, 0));
  [-0.3, 0.3].forEach((dx, k) => {
    g.add(box(`charger_${i}_holster_${k + 1}`, 0.18, 0.3, 0.2, M.graphite, dx, 0.82, -0.32));
    g.add(box(`charger_${i}_connector_${k + 1}`, 0.14, 0.22, 0.14, M.rubber, dx, 0.86, -0.4));
    const cable = new THREE.Mesh(new THREE.TorusGeometry(0.26, 0.042, 8, 18, Math.PI * 1.3), M.rubber);
    cable.name = `charger_${i}_cable_${k + 1}`;
    cable.position.set(dx, 0.5, -0.42);
    cable.rotation.y = Math.PI / 2;
    g.add(cable);
  });
  g.position.set(x, 0.06, z);
  g.rotation.y = facing;
  return g;
}

/* ---------- charger island ---------- */
function buildIsland(i, x, z, side) {
  const g = new THREE.Group();
  g.name = `island_${i}`;
  g.add(box(`island_${i}_kerb`, 3.3, 0.14, 1.9, M.concrete_pale, x, 0.06, z));
  g.add(box(`island_${i}_face_reveal`, 3.36, 0.04, 1.96, M.led_blue, x, 0.06, z));
  [-1.45, 1.45].forEach((dx, k) => {
    g.add(cyl(`island_${i}_bollard_${k + 1}`, 0.07, 0.07, 0.85, M.aluminium, x + dx, 0.2, z, 14));
    g.add(cyl(`island_${i}_bollard_light_${k + 1}`, 0.075, 0.075, 0.1, M.led_warm, x + dx, 1.0, z, 14));
  });
  return g;
}

/* ---------- bay ---------- */
function buildBay(i, x, z, w, l) {
  const g = new THREE.Group();
  g.name = `bay_${i}`;
  g.add(box(`bay_${i}_line_west`, 0.1, 0.02, l, M.paint_line, x - w / 2, 0.062, z));
  g.add(box(`bay_${i}_line_east`, 0.1, 0.02, l, M.paint_line, x + w / 2, 0.062, z));
  g.add(box(`bay_${i}_head_reveal`, w - 0.5, 0.03, 0.1, M.led_blue, x, 0.062, z - Math.sign(z) * (l / 2 - 0.3)));
  g.add(box(`bay_${i}_number`, 0.38, 0.02, 0.38, M.paint_line, x, 0.063, z + Math.sign(z) * (l / 2 - 0.7)));
  return g;
}

/* ---------- vehicle ---------- */
function buildCar(i) {
  const g = new THREE.Group();
  g.name = `car_${i}`;
  g.add(box(`car_${i}_lower_body`, 1.9, 0.5, 4.56, M.car_paint, 0, 0.32, 0));
  g.add(box(`car_${i}_shoulder`, 1.84, 0.34, 4.3, M.car_paint, 0, 0.82, -0.02));
  g.add(box(`car_${i}_rocker`, 1.72, 0.3, 4.2, M.car_paint_dark, 0, 0.1, 0));
  g.add(box(`car_${i}_cabin`, 1.7, 0.5, 2.36, M.car_paint, 0, 1.16, -0.2));
  g.add(box(`car_${i}_roof`, 1.5, 0.08, 2.2, M.car_paint_dark, 0, 1.66, -0.24));
  g.add(box(`car_${i}_windshield`, 1.6, 0.46, 0.08, M.glass_dark, 0, 1.18, 0.96));
  g.add(box(`car_${i}_rear_glass`, 1.52, 0.42, 0.08, M.glass_dark, 0, 1.2, -1.38));
  g.add(box(`car_${i}_window_left`, 0.06, 0.4, 2.1, M.glass_dark, -0.86, 1.2, -0.2));
  g.add(box(`car_${i}_window_right`, 0.06, 0.4, 2.1, M.glass_dark, 0.86, 1.2, -0.2));
  g.add(box(`car_${i}_headlight`, 1.22, 0.08, 0.06, M.led_white, 0, 0.78, 2.28));
  g.add(box(`car_${i}_taillight`, 1.3, 0.07, 0.06, M.led_warm, 0, 0.84, -2.28));
  g.add(box(`car_${i}_charge_port`, 0.22, 0.22, 0.05, M.led_cyan, -0.94, 0.8, -1.5));
  g.add(box(`car_${i}_mirror_left`, 0.26, 0.1, 0.12, M.car_paint_dark, -1.0, 1.06, 0.8));
  g.add(box(`car_${i}_mirror_right`, 0.26, 0.1, 0.12, M.car_paint_dark, 1.0, 1.06, 0.8));
  const tyre = new THREE.CylinderGeometry(0.37, 0.37, 0.26, 22);
  const rim = new THREE.CylinderGeometry(0.22, 0.22, 0.28, 18);
  [[-0.9, 1.45], [0.9, 1.45], [-0.9, -1.45], [0.9, -1.45]].forEach(([wx, wz], k) => {
    const w = new THREE.Mesh(tyre, M.rubber);
    w.name = `car_${i}_tyre_${k + 1}`;
    w.position.set(wx, 0.37, wz); w.rotation.z = Math.PI / 2;
    g.add(w);
    const r = new THREE.Mesh(rim, M.aluminium);
    r.name = `car_${i}_rim_${k + 1}`;
    r.position.set(wx, 0.37, wz); r.rotation.z = Math.PI / 2;
    g.add(r);
  });
  return g;
}

/* ---------- support structures ---------- */
function buildKiosk() {
  const g = new THREE.Group();
  g.name = 'service_kiosk';
  g.add(box('kiosk_shell', 7.4, 3.1, 5.0, M.concrete_pale, 0, 0, 0));
  g.add(box('kiosk_glazing', 6.6, 2.0, 0.08, M.glass_dark, 0, 0.5, -2.52));
  g.add(box('kiosk_mullion_l', 0.1, 2.0, 0.12, M.aluminium, -2.0, 0.5, -2.54));
  g.add(box('kiosk_mullion_r', 0.1, 2.0, 0.12, M.aluminium, 2.0, 0.5, -2.54));
  g.add(box('kiosk_roof', 8.0, 0.22, 5.6, M.aluminium, 0, 3.1, 0));
  g.add(box('kiosk_roof_reveal', 8.12, 0.07, 5.72, M.led_warm, 0, 3.06, 0));
  g.add(box('kiosk_door', 1.1, 2.2, 0.1, M.graphite, 2.9, 0, -2.52));
  return g;
}

function buildTransformer() {
  const g = new THREE.Group();
  g.name = 'transformer';
  g.add(box('transformer_pad', 5.2, 0.26, 4.2, M.concrete_pale, 0, 0.0, 0));
  g.add(box('transformer_enclosure', 3.3, 2.3, 2.0, M.graphite, 0, 0.26, 0));
  g.add(box('transformer_cap', 3.5, 0.16, 2.2, M.aluminium, 0, 2.56, 0));
  g.add(box('transformer_louvre_band', 2.6, 0.9, 0.06, M.aluminium, 0, 0.7, 1.02));
  for (let i = 0; i < 9; i++) {
    g.add(box(`transformer_fin_${i + 1}`, 0.08, 1.6, 0.5, M.aluminium, -1.6 + i * 0.4, 0.4, -1.1));
  }
  for (let i = 0; i < 3; i++) {
    const x = -0.85 + i * 0.85;
    g.add(cyl(`transformer_bushing_${i + 1}`, 0.1, 0.14, 0.7, M.concrete_pale, x, 2.72, -0.5, 14));
    g.add(cyl(`transformer_bushing_cap_${i + 1}`, 0.18, 0.18, 0.1, M.deep_blue, x, 3.42, -0.5, 14));
  }
  g.add(box('transformer_status_light', 0.9, 0.1, 0.05, M.led_cyan, 0.9, 2.1, 1.04));
  [[-2.3, -1.8], [2.3, -1.8], [-2.3, 1.8], [2.3, 1.8]].forEach(([x, z], i) =>
    g.add(cyl(`transformer_bollard_${i + 1}`, 0.09, 0.09, 0.9, M.aluminium, x, 0.26, z, 12)));
  return g;
}

function buildMast(i, x, z) {
  const g = new THREE.Group();
  g.name = `light_mast_${i}`;
  g.add(cyl(`light_mast_${i}_base`, 0.26, 0.3, 0.4, M.concrete_pale, x, 0, z, 14));
  g.add(cyl(`light_mast_${i}_pole`, 0.1, 0.19, 9.8, M.aluminium, x, 0.4, z, 16));
  const inward = z > 0 ? -1 : 1;
  g.add(box(`light_mast_${i}_arm`, 0.12, 0.12, 1.5, M.aluminium, x, 10.0, z + inward * 0.75));
  g.add(box(`light_mast_${i}_luminaire`, 0.8, 0.13, 0.42, M.graphite, x, 9.92, z + inward * 1.5));
  g.add(box(`light_mast_${i}_lens`, 0.72, 0.05, 0.34, M.led_white, x, 9.88, z + inward * 1.5));
  return g;
}

export function buildDepot() {
  const hub = new THREE.Group();
  hub.name = 'flexgrid_charging_hub';

  hub.add(buildSite());
  hub.add(buildCanopy());

  [-18.4, -11.0, -3.7, 3.7, 11.0, 18.4].forEach((x, i) => hub.add(buildColumn(i + 1, x)));

  const bayW = 3.3, bayL = 5.6;
  const islandZ = 3.4, bayZ = islandZ + 1.0 + bayL / 2;
  const bayX = [-11.55, -8.25, -4.95, -1.65, 1.65, 4.95, 8.25, 11.55];
  const islandX = [-9.9, -3.3, 3.3, 9.9];

  let bayN = 0, chargerN = 0, islandN = 0;
  [1, -1].forEach((side) => {
    bayX.forEach((x) => {
      bayN++;
      hub.add(buildBay(bayN, x, side * bayZ, bayW, bayL));
    });
    islandX.forEach((x) => {
      islandN++;
      hub.add(buildIsland(islandN, x, side * islandZ, side));
      [-0.8, 0.8].forEach((dx) => {
        chargerN++;
        hub.add(buildCharger(chargerN, x + dx, side * islandZ, side > 0 ? 0 : Math.PI));
      });
    });
  });

  [
    { x: -8.25, side: 1 }, { x: 1.65, side: 1 },
    { x: -4.95, side: -1 }, { x: 8.25, side: -1 },
  ].forEach((p, i) => {
    const car = buildCar(i + 1);
    car.position.set(p.x, 0.06, p.side * (bayZ - 0.3));
    car.rotation.y = p.side > 0 ? Math.PI : 0;
    hub.add(car);
  });

  const kiosk = buildKiosk();
  kiosk.position.set(24.5, 0, 13.5);
  hub.add(kiosk);

  const tx = buildTransformer();
  tx.position.set(25.5, 0, -9.5);
  hub.add(tx);

  [[-27, -16], [27, -16], [-27, 16], [27, 16]].forEach(([x, z], i) => hub.add(buildMast(i + 1, x, z)));

  hub.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  return hub;
}
