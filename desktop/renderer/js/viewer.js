// three.js viewer — environment-lit PBR scene matched to Marshmallow's
// Viewport3D. Hemisphere + key/rim directionals, ACES tone mapping, an
// IBL environment via PMREMGenerator so PBR materials from Bespoke / TRELLIS
// look right (metallic + roughness need a reflection probe to be visible).

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const instances = new WeakMap();

function makeViewer(host) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  // Make sure pointer events reach the canvas. Some grid/scrolling parents
  // can swallow them otherwise.
  Object.assign(renderer.domElement.style, {
    position: 'absolute', inset: '0', width: '100%', height: '100%',
    display: 'block', pointerEvents: 'auto', userSelect: 'none', cursor: 'grab',
  });
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = null;

  // PMREM-baked environment so PBR materials show metallic/roughness properly.
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envRT = pmrem.fromScene(new RoomEnvironment(renderer), 0.04);
  scene.environment = envRT.texture;
  scene.environmentIntensity = 0.7;

  const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 500);
  camera.position.set(3.2, 2.1, 4.0);

  scene.add(new THREE.HemisphereLight(0xd8e6ec, 0x1a1916, 0.5));
  const key = new THREE.DirectionalLight(0xffe6c7, 1.4);
  key.position.set(6, 8, 5); scene.add(key);
  const rim = new THREE.DirectionalLight(0x22d3ee, 0.45);
  rim.position.set(-5, 4, -4); scene.add(rim);

  // Cyan-tinted infinite grid + axes for orientation
  const grid = new THREE.GridHelper(20, 40, 0x3b3633, 0x2a2724);
  grid.material.transparent = true; grid.material.opacity = 0.6;
  scene.add(grid);
  const axes = new THREE.AxesHelper(0.5);
  axes.material.transparent = true; axes.material.opacity = 0.6;
  scene.add(axes);

  // Make the canvas focusable + give it pointer events so wheel/drag reach
  // OrbitControls reliably (some Electron / grid-parent combos eat scroll).
  renderer.domElement.style.outline = 'none';
  renderer.domElement.style.touchAction = 'none';
  renderer.domElement.tabIndex = 0;

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.target.set(0, 0.5, 0);
  controls.enableZoom = true;
  controls.zoomSpeed = 1.0;
  controls.zoomToCursor = true;
  controls.screenSpacePanning = true;
  controls.minDistance = 0.05;
  controls.maxDistance = 500;
  // Stop the wheel from bubbling out and scrolling the surrounding tab.
  renderer.domElement.addEventListener('wheel', (e) => e.preventDefault(), { passive: false });

  let model = null;
  let bboxHelper = null;
  let raf = 0;

  function resize() {
    const w = host.clientWidth || 600;
    const h = host.clientHeight || 400;
    renderer.setSize(w, h, false);
    camera.aspect = w / Math.max(h, 1);
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  function tick() {
    raf = requestAnimationFrame(tick);
    controls.update();
    renderer.render(scene, camera);
  }
  tick();

  function disposeModel() {
    if (!model) return;
    const root = model._carmelaRoot;
    if (root && root.parent) scene.remove(root);
    else if (model.parent) scene.remove(model);
    model.traverse((c) => {
      if (c.geometry) c.geometry.dispose?.();
      if (c.material) (Array.isArray(c.material) ? c.material : [c.material]).forEach((m) => m.dispose?.());
    });
    model = null;
  }

  function setModel(obj) {
    disposeModel();
    if (!obj) { host.classList.add('empty'); return; }
    host.classList.remove('empty');
    model = obj;
    // Wrap so we can pivot the whole model rigidly regardless of internal
    // transforms baked into the loader output. Centering becomes: move the
    // wrapper, never touch the original meshes.
    const root = new THREE.Group();
    root.name = 'modelRoot';
    root.add(model);
    scene.add(root);

    // Force PBR materials to react to the environment map.
    root.traverse((c) => {
      if (c.isMesh && c.material) {
        const mats = Array.isArray(c.material) ? c.material : [c.material];
        mats.forEach((m) => {
          if (m.isMeshStandardMaterial || m.isMeshPhysicalMaterial) {
            m.needsUpdate = true;
            if (m.envMapIntensity === undefined || m.envMapIntensity < 0.05) m.envMapIntensity = 1.0;
          }
        });
      }
    });
    // Track this root for frameModel + dispose.
    model._carmelaRoot = root;

    // Frame on the next 2 frames (waiting for layout + matrix update).
    requestAnimationFrame(() => requestAnimationFrame(() => frameModel()));
  }

  function frameModel() {
    if (!model) return;
    const root = model._carmelaRoot || model;
    root.position.set(0, 0, 0);  // reset before measuring
    root.updateMatrixWorld(true);
    const box0 = new THREE.Box3().setFromObject(root);
    if (box0.isEmpty()) return;
    const c0 = box0.getCenter(new THREE.Vector3());
    // Re-pivot so centroid → origin (X/Z), lowest point → ground (Y=0).
    root.position.set(-c0.x, -box0.min.y, -c0.z);
    root.updateMatrixWorld(true);

    const box = new THREE.Box3().setFromObject(root);
    const size3 = box.getSize(new THREE.Vector3());
    const sizeMax = Math.max(size3.x, size3.y, size3.z) || 1;
    const center = box.getCenter(new THREE.Vector3());

    // Grid auto-scales — never dwarf sub-meter assets, never cut off big ones.
    grid.scale.setScalar(Math.max(0.2, sizeMax / 5));

    const w = host.clientWidth || 600;
    const h = host.clientHeight || 400;
    const aspect = w / Math.max(h, 1);
    const fovV = camera.fov * Math.PI / 180;
    const fovH = 2 * Math.atan(Math.tan(fovV / 2) * aspect);
    const fitFov = Math.min(fovV, fovH);
    // 2.6× margin → asset fills ~60% of the frame, lots of orbit headroom.
    const dist = (sizeMax / 2) / Math.tan(fitFov / 2) * 2.6;
    const dir = new THREE.Vector3(0.25, 0.20, 1).normalize().multiplyScalar(dist);
    camera.position.copy(center).add(dir);
    camera.near = Math.max(0.001, sizeMax / 1000);
    camera.far  = Math.max(80, sizeMax * 50);
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.update();
  }

  function setBBox(min, max, kind = 'tight') {
    if (bboxHelper) { scene.remove(bboxHelper); bboxHelper.geometry.dispose(); bboxHelper.material.dispose(); bboxHelper = null; }
    if (!min || !max) return;
    const box = new THREE.Box3(new THREE.Vector3(...min), new THREE.Vector3(...max));
    const color = kind === 'collision' ? 0x7da9ff : 0x22d3ee;
    bboxHelper = new THREE.Box3Helper(box, color);
    bboxHelper.material.transparent = true;
    bboxHelper.material.opacity = 0.85;
    scene.add(bboxHelper);
  }

  host.classList.add('empty');

  // Recenter button + nav hint overlay (don't recreate on each model load).
  if (!host.querySelector('.recenter-btn')) {
    const btn = document.createElement('button');
    btn.className = 'recenter-btn';
    btn.type = 'button';
    btn.textContent = 'Recenter';
    btn.addEventListener('click', (e) => { e.stopPropagation(); frameModel(); });
    host.appendChild(btn);
    const hint = document.createElement('div');
    hint.className = 'nav-hint';
    hint.textContent = 'drag · scroll · right-drag to pan';
    host.appendChild(hint);
  }
  renderer.domElement.addEventListener('pointerdown', () => { renderer.domElement.style.cursor = 'grabbing'; });
  renderer.domElement.addEventListener('pointerup',   () => { renderer.domElement.style.cursor = 'grab'; });
  renderer.domElement.addEventListener('pointerleave',() => { renderer.domElement.style.cursor = 'grab'; });

  return {
    renderer, scene, camera, controls, setModel, setBBox, frameModel,
    getBounds() {
      if (!model) return null;
      const root = model._carmelaRoot || model;
      const b = new THREE.Box3().setFromObject(root);
      return { min: b.min.toArray(), max: b.max.toArray() };
    },
    dispose() {
      cancelAnimationFrame(raf);
      ro.disconnect();
      disposeModel();
      pmrem.dispose();
      envRT.dispose();
      renderer.dispose();
      host.innerHTML = '';
    },
  };
}

export function mount(host) {
  if (!host) return null;
  if (instances.has(host)) return instances.get(host);
  const inst = makeViewer(host);
  instances.set(host, inst);
  return inst;
}

const gltfLoader = new GLTFLoader();
const plyLoader = new PLYLoader();

export async function loadGLB(host, url) {
  const inst = mount(host);
  return new Promise((resolve, reject) => {
    gltfLoader.load(
      url,
      (gltf) => {
        try { inst.setModel(gltf.scene); resolve({ inst, gltf, bounds: inst.getBounds() }); }
        catch (e) { reject(e); }
      },
      undefined,
      (err) => { console.error('[viewer] GLB load failed for', url, err); reject(err); }
    );
  });
}

export async function loadPLY(host, url) {
  const inst = mount(host);
  return new Promise((resolve, reject) => {
    plyLoader.load(
      url,
      (geometry) => {
        geometry.computeVertexNormals();
        const mat = new THREE.MeshStandardMaterial({
          color: 0xb6bcc8, metalness: 0.05, roughness: 0.65, flatShading: false,
          vertexColors: geometry.hasAttribute('color'),
        });
        const mesh = new THREE.Mesh(geometry, mat);
        const grp = new THREE.Group();
        grp.add(mesh);
        inst.setModel(grp);
        resolve({ inst, mesh, bounds: inst.getBounds() });
      },
      undefined,
      (err) => { console.error('[viewer] PLY load failed for', url, err); reject(err); }
    );
  });
}

export function clear(host) { const inst = instances.get(host); if (inst) inst.setModel(null); }
export function setBBox(host, min, max, kind) { const inst = instances.get(host); if (inst) inst.setBBox(min, max, kind); }
export function getBounds(host) { const inst = instances.get(host); return inst ? inst.getBounds() : null; }
export function recenter(host) { const inst = instances.get(host); if (inst) inst.frameModel(); }
