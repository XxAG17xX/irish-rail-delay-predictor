// @ts-check
/**
 * The plate's trains in 3D: each railcar stands on its last reported station on the tilted
 * sheet, lit, with a soft shadow on the paper. Loaded after the page is idle and only where
 * WebGL works; the 2D trains underneath stay the clickable, keyboard-reachable layer, and
 * remain what a visitor sees whenever this does not run.
 *
 * Alignment: the sheet is tilted by CSS (perspective on the stage, rotate on the sheet). The
 * camera here reproduces that projection exactly, a perspective camera at the stage's CSS
 * perspective distance looking at the stage centre, so a model placed at a sheet point lands
 * on that point's pixels. CSS space is y-down and Three's y-up; positions and the sheet's axes
 * are mirrored in y, which turns the sheet's rotation into a proper rotation in Three.
 *
 * Nothing animates. The scene renders once per layout, because trains move only when a new
 * report arrives.
 */

import {
  WebGLRenderer, Scene, PerspectiveCamera, InstancedMesh, MeshStandardMaterial, ShadowMaterial,
  PlaneGeometry, Mesh, HemisphereLight, DirectionalLight, Matrix4, Vector3, PCFShadowMap,
  SRGBColorSpace,
} from "./vendor/three.js";
import { consist } from "./train3d.js";

/**
 * @param {HTMLElement} stage the element with the CSS perspective
 * @param {HTMLElement} sheet the tilted sheet holding the plate's SVG
 * @param {{trains: () => {x: number, y: number, angle: number}[], window: () => {x: number, y: number, w: number, h: number}, trainPx: () => number, svg: SVGElement}} plate
 * @returns {{render: () => void, dispose: () => void} | null} null when WebGL is unavailable
 */
export function mountTrains(stage, sheet, plate) {
  /** @type {WebGLRenderer} */
  let renderer;
  try {
    renderer = new WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  } catch {
    return null;
  }
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = PCFShadowMap;
  const canvas = renderer.domElement;
  canvas.className = "plate-3d";
  canvas.setAttribute("aria-hidden", "true");
  stage.append(canvas);

  const scene = new Scene();
  const camera = new PerspectiveCamera(30, 1, 10, 20000);
  scene.add(new HemisphereLight(0xffffff, 0x9aa0aa, 1.8));
  const sun = new DirectionalLight(0xffffff, 2.1);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0004;
  scene.add(sun, sun.target);

  const material = new MeshStandardMaterial({ vertexColors: true, roughness: 0.42, metalness: 0.06 });
  const geometry = consist(3);
  /** @type {InstancedMesh|null} */
  let fleet = null;
  const ground = new Mesh(new PlaneGeometry(1, 1), new ShadowMaterial({ opacity: 0.34 }));
  ground.receiveShadow = true;
  ground.matrixAutoUpdate = false;
  scene.add(ground);

  /** CSS y-down to Three y-up. @param {DOMPoint} p */
  const flip = (p) => new Vector3(p.x, -p.y, p.z);

  function render() {
    const sw = stage.clientWidth;
    const sh = stage.clientHeight;
    if (!sw || !sh) return;
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    renderer.setSize(sw, sh, false);
    canvas.style.width = sw + "px";
    canvas.style.height = sh + "px";

    // The stage's CSS perspective, reproduced: eye at that distance, frustum the stage's size.
    const d = parseFloat(getComputedStyle(stage).perspective) || 1700;
    camera.fov = (2 * Math.atan(sh / 2 / d) * 180) / Math.PI;
    camera.aspect = sw / sh;
    camera.near = d / 20;
    camera.far = d * 6;
    camera.position.set(0, 0, d);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();

    // The sheet's transform about its own centre, placed in stage coordinates from the centre.
    const w = sheet.offsetWidth;
    const h = sheet.offsetHeight;
    const css = getComputedStyle(sheet).transform;
    const M = new DOMMatrix(css === "none" ? undefined : css);
    const C = new DOMMatrix().translate(sheet.offsetLeft + w / 2 - sw / 2, sheet.offsetTop + h / 2 - sh / 2).multiply(M);
    /** A point on the sheet, from its top-left in CSS pixels, in Three space. */
    const at = (/** @type {number} */ u, /** @type {number} */ v, z = 0) => flip(C.transformPoint(new DOMPoint(u - w / 2, v - h / 2, z)));
    const origin = at(w / 2, h / 2);
    const axisX = at(w / 2 + 1, h / 2).sub(origin).normalize();
    const normal = at(w / 2, h / 2, 1).sub(origin).normalize();
    const axisY = new Vector3().crossVectors(normal, axisX);

    // Where the plate's SVG draws a kilometre, inside the sheet's padding ("meet" scaling).
    const sheetStyle = getComputedStyle(sheet);
    const padL = parseFloat(sheetStyle.paddingLeft) || 0;
    const padT = parseFloat(sheetStyle.paddingTop) || 0;
    const svgW = plate.svg.clientWidth;
    const svgH = plate.svg.clientHeight;
    const vb = plate.window();
    const k = Math.min(svgW / vb.w, svgH / vb.h);
    const ox = padL + (svgW - vb.w * k) / 2;
    const oy = padT + (svgH - vb.h * k) / 2;

    ground.matrix.makeBasis(axisX.clone().multiplyScalar(w), axisY.clone().multiplyScalar(h), normal).setPosition(origin);
    ground.matrixWorldNeedsUpdate = true;

    // Light from the sheet's upper left, high, so each railcar casts a short shadow to the
    // lower right on the paper, the way the 2D plate is read.
    const lightDir = axisX.clone().multiplyScalar(-0.45).add(axisY.clone().multiplyScalar(0.55)).add(normal.clone().multiplyScalar(1.2)).normalize();
    sun.position.copy(origin).addScaledVector(lightDir, d);
    sun.target.position.copy(origin);
    const half = Math.max(w, h) * 0.7;
    Object.assign(sun.shadow.camera, { left: -half, right: half, top: half, bottom: -half, near: d * 0.2, far: d * 2.5 });
    sun.shadow.camera.updateProjectionMatrix();

    const trains = plate.trains();
    if (!fleet || fleet.count !== trains.length) {
      if (fleet) scene.remove(fleet);
      fleet = new InstancedMesh(geometry, material, Math.max(trains.length, 1));
      fleet.castShadow = true;
      scene.add(fleet);
    }
    // A three-car unit, longer than the 2D mark it replaces, so each car keeps a readable
  // size: one car at the mark's length read as a bus.
    const length = plate.trainPx() * 1.7;
    const m = new Matrix4();
    trains.forEach((t, i) => {
      const p = at(ox + (t.x - vb.x) * k, oy + (t.y - vb.y) * k);
      // The 2D heading is measured y-down on the sheet; in the sheet's y-up axes it reverses.
      const a = (-t.angle * Math.PI) / 180;
      const fwd = axisX.clone().multiplyScalar(Math.cos(a)).addScaledVector(axisY, Math.sin(a));
      const side = new Vector3().crossVectors(normal, fwd);
      m.makeBasis(fwd.multiplyScalar(length), side.multiplyScalar(length), normal.clone().multiplyScalar(length)).setPosition(p);
      fleet?.setMatrixAt(i, m);
    });
    fleet.count = trains.length;
    fleet.instanceMatrix.needsUpdate = true;
    renderer.render(scene, camera);
  }

  return {
    render,
    dispose() {
      renderer.dispose();
      geometry.dispose();
      material.dispose();
      canvas.remove();
    },
  };
}
