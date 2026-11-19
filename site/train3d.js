// @ts-check
/**
 * A small railcar, built in code from the turnaround reference (design/refs/03-train-turnaround):
 * a white body with a blue band through the window line, dark windows set in it, pale grey
 * doors (the reference's yellow is not used, because amber means the real arrival on this
 * site), a raked cab, a dark underframe, two bogies and three roof boxes.
 *
 * One geometry, one material: every triangle carries its own colour, so the livery has crisp
 * edges without a texture and a whole fleet draws as a single instanced mesh.
 *
 * Frame: length along +x (cab at +x), width along y, height along +z from the railhead at
 * z = 0. Length 1, so instances scale to whatever size the map needs. The far side and the
 * underside are not in the reference; they are assumed symmetric and plain.
 */

import { BufferGeometry, Float32BufferAttribute } from "./vendor/three.js";

const C = {
  white: [0.95, 0.955, 0.945],
  // The roof is what the tilted map mostly shows, so it is a clear mid grey, not white.
  roof: [0.6, 0.63, 0.67],
  band: [0.169, 0.208, 0.686], // #2b35af, the site's ultramarine
  glass: [0.09, 0.105, 0.14],
  door: [0.78, 0.8, 0.83],
  frame: [0.12, 0.135, 0.15],
  bogie: [0.2, 0.22, 0.24],
  box: [0.4, 0.43, 0.47],
  lamp: [1, 0.98, 0.9],
};

// Proportions measured from the reference's side and front views: the body is 0.205 of the
// length tall above a 0.07 underframe, and the car is 0.67 as wide as it is tall overall.
const W = 0.094; // half width
const Z0 = 0.07; // body floor
const Z1 = 0.275; // roof top
/** @type {[number, number]} */
const ZB = [0.131, 0.218]; // the blue band
/** @type {[number, number]} */
const ZW = [0.144, 0.201]; // windows within it

/** The body's cross-section, half-width y and height z, round the loop from the floor. */
const PROFILE = /** @type {[number, number][]} */ ((() => {
  /** @type {[number, number][]} */
  const right = [[0, Z0], [W * 0.92, Z0], [W, Z0 + 0.012], [W, ZB[0]], [W, ZB[1]], [W, 0.248]];
  for (let i = 1; i <= 5; i++) {
    const a = (i / 5) * (Math.PI / 2);
    right.push([W * 0.55 + W * 0.45 * Math.cos(a), 0.248 + (Z1 - 0.248) * Math.sin(a)]);
  }
  /** @type {[number, number][]} */
  const left = right.slice(1, -1).reverse().map(([y, z]) => [-y, z]);
  return [...right, [0, Z1], ...left];
})());

/** Window and door openings along the side, as [x0, x1]. */
/** @type {[number, number][]} */
const DOORS = [[-0.359, -0.307], [0.2, 0.254]];
/** Window openings along the side; a car with no cab carries them to its far end. */
const windowsFor = (/** @type {boolean} */ cab) => {
  /** @type {[number, number][]} */
  const out = [];
  for (let x = -0.44; x < (cab ? 0.33 : 0.4); x += 0.074) {
    /** @type {[number, number]} */
    const w = [x, x + 0.06];
    if (!DOORS.some(([a, b]) => w[1] > a - 0.01 && w[0] < b + 0.01)) out.push(w);
  }
  return out;
};

/** Sections along the length: x, width scale, height scale. A cab rakes over its last 12%;
 * a car without one ends as the rear does, square with rounded corners.
 * @param {boolean} cab @returns {[number, number, number][]} */
const sectionsFor = (cab) => {
  const xs = new Set([-0.475, cab ? 0.38 : 0.475]);
  for (const [a, b] of [...DOORS, ...windowsFor(cab)]) {
    xs.add(a);
    xs.add(b);
  }
  /** @type {[number, number, number][]} */
  const mid = [...xs].sort((p, q) => p - q).map((x) => [x, 1, 1]);
  /** @type {[number, number, number][]} */
  const tail = cab
    ? [[0.42, 0.98, 0.97], [0.452, 0.93, 0.86], [0.477, 0.83, 0.72], [0.493, 0.64, 0.56], [0.5, 0.42, 0.42]]
    : [[0.492, 0.97, 0.98], [0.5, 0.9, 0.93]];
  return [[-0.5, 0.9, 0.93], [-0.492, 0.97, 0.98], ...mid, ...tail];
};

const inside = (/** @type {number} */ x, /** @type {[number, number][]} */ spans) => spans.some(([a, b]) => x > a && x < b);

/**
 * Colour of a body panel. x is along the car; y and z are the panel's place in the untapered
 * cross-section, so a band edge stays on one row of vertices even where the ends taper (taper
 * moves the vertices, and colouring by their moved height sawtoothed the band).
 */
function bodyColour(/** @type {number} */ x, /** @type {number} */ y, /** @type {number} */ z, /** @type {boolean} */ end, /** @type {boolean} */ cab, /** @type {[number, number][]} */ windows) {
  if (end) {
    // The ends: the cab's tip is windscreen over a white nose; every other end is a plain
    // white face with a gangway window.
    if (cab && x > 0) return z > 0.13 ? C.glass : C.white;
    return z > 0.16 && z < 0.235 && Math.abs(y) < 0.05 ? C.glass : C.white;
  }
  // The windscreen wraps the upper cab, as in the reference's front view; the band sweeps up
  // the cab side beneath it.
  if (cab && ((x > 0.455 && z > 0.14) || (x > 0.43 && z > 0.17 && z < 0.255))) return C.glass;
  if (cab && x > 0.46 && z > 0.095 && z < 0.12 && Math.abs(y) > W * 0.35 && Math.abs(y) < W * 0.8) return C.lamp;
  const lift = cab && x > 0.37 ? (x - 0.37) * 0.5 : 0;
  const side = Math.abs(y) > W * 0.8;
  if (z > 0.252) return C.roof;
  if (side && inside(x, DOORS) && z > Z0 + 0.004 && z < 0.233) {
    return z > 0.119 && z < 0.211 ? C.glass : C.door;
  }
  if (side && inside(x, windows) && z > ZW[0] && z < ZW[1]) return C.glass;
  if (z > ZB[0] + lift && z < ZB[1] + lift * 0.3) return C.band;
  return C.white;
}

/**
 * One car's triangles, appended to `pos` and `col`. `place` maps each vertex into the
 * consist: shortened, shifted along it, and for the last car turned round (a rotation, so the
 * triangles keep their winding).
 * @param {boolean} cab @param {number[]} pos @param {number[]} col
 * @param {(p: number[]) => number[]} place
 */
function car(cab, pos, col, place) {
  const SECTIONS = sectionsFor(cab);
  const windows = windowsFor(cab);

  /** @param {number[][]} tri @param {number[]} c */
  const push = (tri, c) => {
    for (const raw of tri) {
      const p = place(raw);
      pos.push(p[0] ?? 0, p[1] ?? 0, p[2] ?? 0);
    }
    for (let i = 0; i < 3; i++) col.push(c[0] ?? 0, c[1] ?? 0, c[2] ?? 0);
  };
  /** A cap triangle, coloured from its own centroid. */
  const capTri = (/** @type {number[][]} */ tri) => {
    const [a = [0, 0, 0], b = [0, 0, 0], c = [0, 0, 0]] = tri;
    const cy = ((a[1] ?? 0) + (b[1] ?? 0) + (c[1] ?? 0)) / 3;
    const cz = ((a[2] ?? 0) + (b[2] ?? 0) + (c[2] ?? 0)) / 3;
    push(tri, bodyColour(a[0] ?? 0, cy, cz, true, cab, windows));
  };

  // Loft the cross-section through every section; the profile shrinks toward the floor line.
  /** @type {number[][][]} */
  const ring = SECTIONS.map(([x, sy, sz]) => PROFILE.map(([y, z]) => [x, y * sy, Z0 + (z - Z0) * sz]));
  for (let i = 0; i + 1 < ring.length; i++) {
    const r0 = ring[i] || [];
    const r1 = ring[i + 1] || [];
    const xm = (((SECTIONS[i] || [0])[0] ?? 0) + ((SECTIONS[i + 1] || [0])[0] ?? 0)) / 2;
    for (let j = 0; j < PROFILE.length; j++) {
      const k = (j + 1) % PROFILE.length;
      /** @type {number[]} */ const a = r0[j] || [0, 0, 0];
      /** @type {number[]} */ const b = r0[k] || [0, 0, 0];
      /** @type {number[]} */ const c = r1[j] || [0, 0, 0];
      /** @type {number[]} */ const d = r1[k] || [0, 0, 0];
      const [pj = [0, 0], pk = [0, 0]] = [PROFILE[j], PROFILE[k]];
      const colour = bodyColour(xm, (pj[0] + pk[0]) / 2, (pj[1] + pk[1]) / 2, false, cab, windows);
      push([a, b, c], colour);
      push([b, d, c], colour);
    }
  }
  // End caps, fanned from each end's centre.
  for (const [r, flip] of [[ring[0] || [], true], [ring[ring.length - 1] || [], false]]) {
    const ends = /** @type {number[][]} */ (r);
    const n = ends.length;
    const ctr = [ends[0]?.[0] ?? 0, 0, ends.reduce((s, p) => s + (p[2] ?? 0), 0) / n];
    for (let j = 0; j < n; j++) {
      const a = ends[j] || [0, 0, 0], b = ends[(j + 1) % n] || [0, 0, 0];
      capTri(flip ? [ctr, b, a] : [ctr, a, b]);
    }
  }

  /** An axis-aligned box, outward-facing. */
  const box = (/** @type {number[]} */ [x0 = 0, x1 = 0, y0 = 0, y1 = 0, z0 = 0, z1 = 0], /** @type {number[]} */ c) => {
    const v = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]];
    const faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [3, 0, 4, 7]];
    for (const [a = 0, b = 0, cc = 0, dd = 0] of faces) {
      push([v[a] || [], v[b] || [], v[cc] || []], c);
      push([v[a] || [], v[cc] || [], v[dd] || []], c);
    }
  };
  box([-0.46, 0.44, -0.082, 0.082, 0.042, Z0 + 0.002], C.frame); // underframe
  box([-0.18, -0.03, -0.072, 0.072, 0.024, 0.044], C.frame); // equipment boxes below the floor
  box([0.03, 0.2, -0.072, 0.072, 0.024, 0.044], C.frame);
  for (const bx of [-0.3, 0.36]) {
    box([bx - 0.075, bx + 0.075, -0.078, 0.078, 0.016, 0.05], C.bogie); // bogie frame
    for (const wx of [bx - 0.048, bx + 0.048]) box([wx - 0.026, wx + 0.026, -0.07, 0.07, 0, 0.042], C.frame); // wheelsets
  }
  for (const [a, b] of /** @type {[number, number][]} */ ([[-0.37, -0.24], [-0.07, 0.05], [0.16, 0.27]])) {
    box([a, b, -0.042, 0.042, Z1 - 0.004, Z1 + 0.016], C.box); // roof equipment
  }

}

/**
 * A three-car unit, the way a train reads at map scale where one car reads as a bus: a cab
 * car leading, a middle car, and the cab car turned round at the back. Each car is drawn at
 * 0.46 of its true length so its windows and band survive the map's size, and the unit is
 * scaled to length 1 along +x, leading cab at +x.
 * @returns {BufferGeometry}
 */
export function consist(cars = 3) {
  /** @type {number[]} */ const pos = [];
  /** @type {number[]} */ const col = [];
  const SHORT = 0.46;
  // Wide enough to show at map size, where a true coupling gap is under a pixel.
  const GAP = 0.1;
  const total = cars * SHORT + (cars - 1) * GAP;
  for (let i = 0; i < cars; i++) {
    const centre = total / 2 - SHORT / 2 - i * (SHORT + GAP);
    const last = i === cars - 1 && cars > 1;
    const cab = i === 0 || last;
    car(cab, pos, col, (p) => {
      const x = (last ? -1 : 1) * (p[0] ?? 0) * SHORT;
      const y = (last ? -1 : 1) * (p[1] ?? 0);
      return [(x + centre) / total, y / total, (p[2] ?? 0) / total];
    });
  }
  const g = new BufferGeometry();
  g.setAttribute("position", new Float32BufferAttribute(pos, 3));
  g.setAttribute("color", new Float32BufferAttribute(col, 3));
  g.computeVertexNormals();
  return g;
}
