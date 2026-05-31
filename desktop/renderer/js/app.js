// CARMELA renderer — all UI logic, talks to FastAPI sidecar over HTTP.
import { loadGLB, loadPLY, mount, setBBox, getBounds, clear as clearViewer } from './viewer.js';

let API_BASE = 'http://127.0.0.1:5174';
const tcAPI = window.tcAPI;

async function api(path, opts = {}) {
  const r = await fetch(API_BASE + path, opts);
  if (!r.ok) {
    const txt = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${txt.slice(0, 220)}`);
  }
  if (r.status === 204) return null;
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.text();
}

// --------- toasts ----------
function toast(msg, level = 'info', timeout = 4000) {
  const host = document.getElementById('toast-host');
  const t = document.createElement('div');
  t.className = `toast ${level}`;
  t.innerHTML = `<div>${escapeHtml(msg)}</div>`;
  host.appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 250); }, timeout);
}

// --------- bootstrap ----------
async function bootstrap() {
  if (tcAPI?.apiPort) {
    const port = await tcAPI.apiPort();
    API_BASE = `http://127.0.0.1:${port}`;
  }
  wireNav();
  wireTopbar();
  wireDashboard();
  wireGenerate();
  wireBatch();
  wireLibrary();
  wireCarlaExport();
  wireCarlaSim();
  wireSettings();
  wireBboxModal();
  await refreshAllStatus();
  loadDashboard();
}

// ============ navigation ============
const SECTION_OF = {
  dashboard: 'Workspace', generate: 'Workspace', batch: 'Workspace', library: 'Workspace',
  'carla-export': 'CARLA', 'carla-sim': 'CARLA',
  settings: 'System',
};
const PAGE_NAME = {
  dashboard: 'Dashboard', generate: 'Generate', batch: 'Batch', library: 'Library',
  'carla-export': 'Export Pipeline', 'carla-sim': 'Live Simulator', settings: 'Settings',
};
function goto(view) {
  document.querySelectorAll('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
  document.getElementById('crumb-section').textContent = SECTION_OF[view] || '';
  document.getElementById('crumb-page').textContent = PAGE_NAME[view] || view;
  // refresh on demand
  if (view === 'dashboard') loadDashboard();
  if (view === 'library') loadLibrary();
  if (view === 'carla-export') loadExportRows();
  if (view === 'settings') refreshSettings();
  // Nudge any three.js viewers in the now-visible tab to re-measure their
  // canvas — display:none parents leave clientWidth at 0 so the WebGL
  // context renders at 0×0 until something forces a resize.
  requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
}
function wireNav() {
  document.querySelectorAll('.nav-item').forEach((b) => b.addEventListener('click', () => goto(b.dataset.view)));
  document.querySelectorAll('[data-goto]').forEach((b) => b.addEventListener('click', (e) => {
    e.preventDefault?.();
    goto(b.dataset.goto);
  }));
}

function wireTopbar() {
  document.getElementById('action-refresh').addEventListener('click', refreshAllStatus);
  document.getElementById('action-launch-carla').addEventListener('click', async () => {
    if (!tcAPI) return;
    const r = await tcAPI.startCarla();
    toast(r.ok ? 'CarlaUE4 launching…' : r.error, r.ok ? 'ok' : 'err');
    setTimeout(refreshAllStatus, 8000);
  });
  // Open external URLs via Electron shell for any element with data-url.
  document.querySelectorAll('[data-url]').forEach((el) => el.addEventListener('click', (e) => {
    e.preventDefault();
    if (tcAPI?.openExternal) tcAPI.openExternal(el.dataset.url);
    else window.open(el.dataset.url, '_blank');
  }));
}

// ============ status (sidebar dots + dashboard) ============
async function refreshAllStatus() {
  let backends = [];
  try { backends = await api('/backends/health'); } catch (e) { toast('sidecar: ' + e.message, 'err'); return; }
  const find = (n) => backends.find((b) => b.name === n);
  setDot('sb-be-bespoke', find('bespoke_api'));
  setDot('sb-be-trellis', find('local_trellis2'));
  // Refresh the Bespoke onboarding banner whenever we re-poll.
  refreshBespokeKey();
  let carlaInfo = null;
  try { carlaInfo = await api(`/carla/status?host=${val('carla-host') || 'localhost'}&port=${val('carla-port') || 2000}`); } catch (e) { /* noop */ }
  const carlaEl = document.getElementById('sb-be-carla');
  const dot = carlaEl.querySelector('.dot');
  if (carlaInfo && carlaInfo.ok) { dot.className = 'dot ok'; carlaEl.lastChild.textContent = ` CARLA · ${carlaInfo.server_version}`; }
  else { dot.className = 'dot off'; carlaEl.lastChild.textContent = ' CARLA'; }

  // Dashboard KPIs
  const stats = await api('/library/stats');
  document.getElementById('kpi-total').textContent = stats.total;
  document.getElementById('kpi-classes').textContent = Object.entries(stats.by_class).map(([k, v]) => `${k}=${v}`).join(' · ') || '—';
  const active = backends.find((b) => b.ok);
  document.getElementById('kpi-backend').textContent = active ? active.name : 'none';
  document.getElementById('kpi-backend-msg').textContent = active ? active.message : 'configure a backend in Settings';
  document.getElementById('kpi-carla').textContent = carlaInfo?.ok ? 'online' : 'offline';
  document.getElementById('kpi-carla-msg').textContent = carlaInfo?.ok ? `srv ${carlaInfo.server_version}` : (carlaInfo?.error || 'not connected');
  // Backend table on dashboard
  const tbody = document.querySelector('#dash-be tbody');
  tbody.innerHTML = '';
  backends.forEach((b) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${b.ok ? '🟢' : '⚫'}</td><td><b>${b.name}</b></td><td><code>${escapeHtml(b.message)}</code></td>`;
    tbody.appendChild(tr);
  });
  // VRAM detection (best effort from trellis2 message)
  const trellisMsg = find('local_trellis2')?.message || '';
  const m = trellisMsg.match(/(\d+\.?\d*)GB VRAM/);
  if (m) {
    document.getElementById('kpi-vram').textContent = m[1] + ' GB';
    const v = parseFloat(m[1]);
    document.getElementById('kpi-vram-msg').textContent = v < 16 ? 'low-VRAM mode active' : 'plenty';
  }
  // Library badge in sidebar
  document.getElementById('nav-lib-count').textContent = stats.total > 0 ? stats.total : '';
}
function setDot(rowId, state) {
  const el = document.getElementById(rowId);
  if (!el) return;
  const dot = el.querySelector('.dot');
  dot.className = 'dot ' + (state?.ok ? 'ok' : 'off');
  el.title = state?.message || '';
}

// ============ dashboard ============
function wireDashboard() { /* nothing else, refreshAllStatus does it */ }
async function loadDashboard() {
  try {
    const lib = await api('/library');
    const recent = lib.slice(-6).reverse();
    const host = document.getElementById('dash-recent');
    if (!recent.length) {
      host.innerHTML = '<div class="empty">No assets yet — generate one to populate the library.</div>';
    } else {
      host.innerHTML = recent.map((e) => `
        <div class="lib-card" data-id="${e.asset_id}">
          <span class="class-chip">${e.request.asset_class}</span>
          <h4>${escapeHtml((e.request.prompt || '').slice(0, 36) || e.asset_id)}</h4>
          <div class="meta-line">${e.backend.split(':')[0]}</div>
        </div>`).join('');
      host.querySelectorAll('.lib-card').forEach((c) => c.addEventListener('click', () => { selectAsset(c.dataset.id); goto('library'); }));
    }
  } catch (e) { /* noop */ }
}

// ============ Generate ============
let chosenImage = null;
let lastGenerated = null;

function wireGenerate() {
  // Image drop
  const drop = document.getElementById('gen-drop');
  drop.addEventListener('click', async () => {
    if (!tcAPI) return;
    const p = await tcAPI.openFile();
    if (!p) return;
    setImage(p);
  });
  ['dragover', 'dragenter'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.borderColor = 'var(--accent)'; }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.borderColor = ''; }));
  drop.addEventListener('drop', async (e) => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    const r = await api('/uploads/image', { method: 'POST', body: fd });
    setImage(r.path);
  });

  // Class change toggles vehicle-only options
  document.getElementById('gen-class').addEventListener('change', (e) => {
    document.querySelectorAll('.vehicle-only').forEach((el) => {
      el.style.display = e.target.value === 'vehicle' ? '' : 'none';
    });
  });

  // Preview tabs (GLB vs PLY)
  document.querySelectorAll('#gen-preview-tabs .seg-btn').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('#gen-preview-tabs .seg-btn').forEach((x) => x.classList.toggle('active', x === b));
    const glb = document.getElementById('gen-viewer-glb');
    const ply = document.getElementById('gen-viewer-ply');
    if (b.dataset.pv === 'glb') { glb.style.display = ''; ply.style.display = 'none'; }
    else                         { glb.style.display = 'none'; ply.style.display = ''; }
  }));

  document.getElementById('comp-edit-bbox').addEventListener('click', openBboxModal);

  // Downloads
  const dlRow = document.getElementById('gen-downloads');
  document.getElementById('gen-dl-glb').addEventListener('click', async () => {
    if (!lastGenerated) return;
    const src = dlRow.dataset.glb;
    const name = `${lastGenerated.asset_id.slice(0,10)}_${lastGenerated.request.asset_class}.glb`;
    if (tcAPI?.saveAs) {
      const r = await tcAPI.saveAs(name, src);
      if (r.ok) toast('Saved to ' + r.dest, 'ok');
      else if (!r.canceled) toast(r.error || 'save failed', 'err');
    } else {
      const a = document.createElement('a');
      a.href = `${API_BASE}/library/${lastGenerated.asset_id}/glb`;
      a.download = name;
      a.click();
    }
  });
  document.getElementById('gen-dl-ply').addEventListener('click', async () => {
    if (!lastGenerated) return;
    const src = dlRow.dataset.ply;
    const name = `${lastGenerated.asset_id.slice(0,10)}_${lastGenerated.request.asset_class}.ply`;
    if (tcAPI?.saveAs && src) {
      const r = await tcAPI.saveAs(name, src);
      if (r.ok) toast('Saved to ' + r.dest, 'ok');
      else if (!r.canceled) toast(r.error || 'save failed', 'err');
    }
  });
  document.getElementById('gen-open-folder').addEventListener('click', () => {
    if (!lastGenerated || !tcAPI?.showInFolder) return;
    tcAPI.showInFolder(dlRow.dataset.glb);
  });

  document.getElementById('gen-run').addEventListener('click', runGenerate);
  mount(document.getElementById('gen-viewer-glb'));
  mount(document.getElementById('gen-viewer-ply'));
}

function setImage(path) {
  chosenImage = path;
  document.getElementById('gen-image-path').textContent = path.split(/[\\/]/).pop();
  document.getElementById('gen-image-preview').src = 'file:///' + path.replace(/\\/g, '/');
}

async function runGenerate() {
  const status = document.getElementById('gen-status');
  const progressWrap = document.getElementById('gen-progress');
  const progressFill = document.getElementById('gen-progress-fill');
  const progressPct  = document.getElementById('gen-progress-pct');
  const progressMsg  = document.getElementById('gen-progress-msg');
  const dlRow = document.getElementById('gen-downloads');

  status.className = 'status-line busy'; status.textContent = '· submitting…';
  progressWrap.hidden = false;
  progressFill.style.width = '0%';
  progressPct.textContent = '0%';
  progressMsg.textContent = 'Submitting…';
  dlRow.hidden = true;

  const wantPLY = document.getElementById('fmt-ply').checked;
  const body = {
    prompt: val('gen-prompt') || 'asset',
    image_path: chosenImage,
    asset_class: val('gen-class'),
    quality: val('gen-quality'),
    seed: +val('gen-seed') || 0,
    target_height_m: val('gen-height') ? +val('gen-height') : null,
    backend: val('gen-backend') || null,
    formats: ['glb', wantPLY ? 'ply' : null, document.getElementById('fmt-collision').checked ? 'collision' : null].filter(Boolean),
    compliance: {
      semantic_tag: val('comp-semantic') || null,
      pivot: val('comp-pivot'),
      collision_shape: val('comp-collision'),
      lod_targets: [+val('lod-0'), +val('lod-1'), +val('lod-2')].filter((n) => n > 0),
    },
  };
  try {
    const { job_id } = await api('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    status.textContent = `· job ${job_id.slice(0, 8)} queued…`;
    pollJob(job_id, async (job) => {
      // progress bar
      const pct = Math.max(0, Math.min(100, +job.progress || 0));
      progressFill.style.width = pct.toFixed(0) + '%';
      progressPct.textContent = pct.toFixed(0) + '%';
      if (job.progress_msg) progressMsg.textContent = job.progress_msg;

      if (job.status === 'completed') {
        const r = job.result;
        lastGenerated = r;
        status.className = 'status-line ok';
        const dur = (job.finished_at - job.started_at).toFixed(1);
        status.textContent = `· ${r.backend} · ${dur}s`;
        document.getElementById('gen-meta').textContent = JSON.stringify(r, null, 2);
        // Cache-bust the GLB URL so we always see the latest output
        const glbUrl = `${API_BASE}/library/${r.asset_id}/glb?t=${Date.now()}`;
        try {
          const res = await loadGLB(document.getElementById('gen-viewer-glb'), glbUrl);
          const b = res.bounds;
          if (b) {
            const ext = b.max.map((v, i) => (v - b.min[i]).toFixed(2));
            document.getElementById('comp-bbox').textContent = `${ext[0]} × ${ext[1]} × ${ext[2]}  m`;
            document.getElementById('comp-edit-bbox').disabled = false;
            document.getElementById('compliance-summary').textContent = 'Auto-detected. Edit if needed before export.';
            setBBox(document.getElementById('gen-viewer-glb'), b.min, b.max, 'tight');
            document.getElementById('comp-bbox').dataset.min = JSON.stringify(b.min);
            document.getElementById('comp-bbox').dataset.max = JSON.stringify(b.max);
          }
        } catch (err) {
          console.error('GLB load failed', err);
          toast('Preview failed to load — file may be malformed.', 'warn');
        }
        const plyServerPath = r.raw_response?.ply_path;
        document.getElementById('gen-dl-ply').hidden = !plyServerPath;
        if (plyServerPath) {
          loadPLY(document.getElementById('gen-viewer-ply'), `${API_BASE}/library/${r.asset_id}/ply`).catch((e) => console.warn('PLY load failed', e));
        }
        dlRow.hidden = false;
        dlRow.dataset.assetId = r.asset_id;
        dlRow.dataset.glb = r.carla_glb || '';
        dlRow.dataset.ply = plyServerPath || '';
        toast(`Generated · ${r.asset_id.slice(0, 10)}`, 'ok');
        // Fade out progress bar after a moment
        setTimeout(() => { progressWrap.hidden = true; }, 1200);
        refreshAllStatus();
      } else if (job.status === 'error') {
        status.className = 'status-line err';
        status.textContent = '· ' + job.error;
        document.getElementById('gen-meta').textContent = job.trace || job.error;
        toast(job.error, 'err', 7000);
        progressWrap.hidden = true;
      } else {
        status.textContent = `· ${job.status}…`;
      }
    });
  } catch (e) {
    status.className = 'status-line err';
    status.textContent = '· ' + e.message;
    progressWrap.hidden = true;
  }
}

async function pollJob(jobId, cb) {
  const tick = async () => {
    let job;
    try { job = await api(`/jobs/${jobId}`); } catch (e) { cb({ status: 'error', error: e.message }); return; }
    cb(job);
    if (job.status === 'pending' || job.status === 'running') setTimeout(tick, 1500);
  };
  tick();
}

// ============ Batch ============
function wireBatch() {
  document.getElementById('batch-run').addEventListener('click', runBatch);
}
async function runBatch() {
  const lines = val('batch-prompts').split('\n').map((s) => s.trim()).filter(Boolean);
  if (!lines.length) return;
  const items = lines.map((p) => ({
    prompt: p,
    asset_class: val('batch-class'),
    quality: val('batch-quality'),
    seed: 0,
  }));
  const backend = val('batch-backend') || null;
  const progress = document.getElementById('batch-progress');
  const tbody = document.getElementById('batch-results');
  tbody.innerHTML = '';
  progress.className = 'status-line busy';
  progress.textContent = `· submitting ${items.length}…`;
  const { job_ids } = await api('/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items, backend }),
  });
  let done = 0;
  job_ids.forEach((jid, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td class="hint">${i + 1}</td><td>${escapeHtml(items[i].prompt.slice(0, 60))}</td><td>${items[i].asset_class}</td><td>—</td><td>—</td><td><span class="chip warn">pending</span></td>`;
    tbody.appendChild(tr);
    pollJob(jid, (job) => {
      if (job.status === 'completed') {
        const r = job.result;
        tr.children[3].textContent = r.backend.split(':')[0];
        tr.children[4].textContent = (job.finished_at - job.started_at).toFixed(1);
        tr.children[5].innerHTML = `<span class="chip" style="background:rgba(94,230,140,0.12);color:var(--ok)">ok</span>`;
        done++;
        progress.textContent = `· ${done}/${items.length} done`;
        if (done === items.length) { progress.className = 'status-line ok'; refreshAllStatus(); }
      } else if (job.status === 'error') {
        tr.children[5].innerHTML = `<span class="chip" style="background:rgba(255,122,122,0.12);color:var(--err)" title="${escapeAttr(job.error || '')}">err</span>`;
        done++;
      } else {
        tr.children[5].innerHTML = `<span class="chip warn">${job.status}</span>`;
      }
    });
  });
}

// ============ Library ============
let libFilter = '';
let libEntries = [];
let libSelected = new Set();
let libCursor = null;

function wireLibrary() {
  document.querySelectorAll('#lib-chips .chip').forEach((c) => c.addEventListener('click', () => {
    document.querySelectorAll('#lib-chips .chip').forEach((x) => x.classList.toggle('active', x === c));
    libFilter = c.dataset.cls || '';
    loadLibrary();
  }));
  document.getElementById('lib-refresh').addEventListener('click', loadLibrary);
  document.getElementById('lib-select-all').addEventListener('click', () => {
    if (libSelected.size === libEntries.length) libSelected = new Set();
    else libSelected = new Set(libEntries.map((e) => e.asset_id));
    renderLibrary();
  });
  document.getElementById('lib-stage').addEventListener('click', () => {
    if (!libSelected.size) { toast('Select assets first', 'warn'); return; }
    window.__carmela_export_ids = [...libSelected];
    goto('carla-export');
    loadExportRows();
  });

  // Download / Open / Delete from the library detail panel
  document.getElementById('lib-dl-glb').addEventListener('click', async () => {
    const e = libEntries.find((x) => x.asset_id === libCursor); if (!e) return;
    const name = `${e.asset_id.slice(0,10)}_${e.request.asset_class}.glb`;
    if (tcAPI?.saveAs) {
      const r = await tcAPI.saveAs(name, e.carla_glb);
      if (r.ok) toast('Saved to ' + r.dest, 'ok');
      else if (!r.canceled) toast(r.error || 'save failed', 'err');
    } else {
      const a = document.createElement('a');
      a.href = `${API_BASE}/library/${e.asset_id}/glb`;
      a.download = name; a.click();
    }
  });
  document.getElementById('lib-dl-ply').addEventListener('click', async () => {
    const e = libEntries.find((x) => x.asset_id === libCursor); if (!e) return;
    const ply = e.raw_response?.ply_path; if (!ply) return;
    const name = `${e.asset_id.slice(0,10)}_${e.request.asset_class}.ply`;
    if (tcAPI?.saveAs) {
      const r = await tcAPI.saveAs(name, ply);
      if (r.ok) toast('Saved to ' + r.dest, 'ok');
      else if (!r.canceled) toast(r.error || 'save failed', 'err');
    }
  });
  document.getElementById('lib-open-folder').addEventListener('click', () => {
    const e = libEntries.find((x) => x.asset_id === libCursor); if (!e) return;
    if (tcAPI?.showInFolder) tcAPI.showInFolder(e.carla_glb);
  });
  document.getElementById('lib-delete').addEventListener('click', async () => {
    const e = libEntries.find((x) => x.asset_id === libCursor); if (!e) return;
    if (!confirm(`Remove ${e.asset_id.slice(0, 10)} from library? (Files remain on disk.)`)) return;
    try {
      await api(`/library/${e.asset_id}`, { method: 'DELETE' });
      toast('Removed from library', 'ok');
      libCursor = null;
      libSelected.delete(e.asset_id);
      loadLibrary();
      document.getElementById('lib-detail-actions').hidden = true;
      document.getElementById('lib-info').hidden = true;
      clearViewer(document.getElementById('lib-viewer'));
    } catch (err) { toast(err.message, 'err'); }
  });
}
async function loadLibrary() {
  const params = libFilter ? `?asset_class=${libFilter}` : '';
  libEntries = await api('/library' + params);
  renderLibrary();
}
function renderLibrary() {
  const grid = document.getElementById('lib-grid');
  grid.innerHTML = '';
  libEntries.forEach((e) => {
    const card = document.createElement('div');
    card.className = 'lib-card' + (libCursor === e.asset_id ? ' selected' : '') + (libSelected.has(e.asset_id) ? ' checked' : '');
    card.innerHTML = `
      <div class="lib-check"></div>
      <span class="class-chip">${e.request.asset_class}</span>
      <h4>${escapeHtml((e.request.prompt || '').slice(0, 38) || e.asset_id)}</h4>
      <div class="meta-line">${e.backend.split(':')[0]} · ${e.asset_id.slice(0, 10)}</div>`;
    card.addEventListener('click', (ev) => {
      if (ev.target.classList.contains('lib-check') || ev.shiftKey) {
        if (libSelected.has(e.asset_id)) libSelected.delete(e.asset_id);
        else libSelected.add(e.asset_id);
        renderLibrary();
      } else {
        selectAsset(e.asset_id);
      }
    });
    grid.appendChild(card);
  });
  document.getElementById('lib-selcount').textContent = libSelected.size;
}
async function selectAsset(assetId) {
  libCursor = assetId;
  renderLibrary();
  const e = libEntries.find((x) => x.asset_id === assetId);
  if (!e) return;
  // Mount the viewer lazily — the library tab may have been hidden when wireLibrary ran.
  mount(document.getElementById('lib-viewer'));
  renderInfoCard(e);
  const actions = document.getElementById('lib-detail-actions');
  actions.hidden = false;
  document.getElementById('lib-dl-ply').hidden = !e.raw_response?.ply_path;
  try {
    await loadGLB(document.getElementById('lib-viewer'), `${API_BASE}/library/${assetId}/glb?t=${Date.now()}`);
  } catch (err) {
    console.error('library preview load failed', err);
    toast('Preview failed: ' + (err.message || 'unknown'), 'err', 5000);
  }
}

function renderInfoCard(e) {
  const host = document.getElementById('lib-info');
  host.hidden = false;
  const created = (e.created_at || '').replace('T', ' ').slice(0, 19);
  const dur = (e.duration_s || 0).toFixed(1) + 's';
  const thumb = e.request?.image_path
    ? `<img class="info-thumb" src="file:///${e.request.image_path.replace(/\\/g, '/')}" onerror="this.style.display='none'"/>`
    : `<div class="info-thumb" style="display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:18px;font-family:var(--font-display)">·</div>`;
  const promptText = (e.request?.prompt || '—').trim() || '—';
  const rows = [
    ['Class', e.request?.asset_class || '—'],
    ['Quality', e.request?.quality || '—'],
    ['Seed', e.request?.seed ?? '—'],
    ['Backend', (e.backend || '').split(':')[0] || '—'],
    ['Duration', dur],
    ['Created', created || '—'],
  ];
  const pathRows = [
    ['GLB', e.carla_glb || '—'],
    ['Metadata', e.metadata_path || '—'],
  ];
  if (e.raw_response?.ply_path) pathRows.push(['PLY', e.raw_response.ply_path]);
  if (e.raw_response?.credits_used != null) rows.push(['Credits used', String(e.raw_response.credits_used)]);

  host.innerHTML = `
    <div class="info-head">
      ${thumb}
      <div style="flex:1;min-width:0">
        <div class="info-title" title="${escapeAttr(promptText)}">${escapeHtml(promptText.slice(0, 60))}${promptText.length > 60 ? '…' : ''}</div>
        <div class="info-id">${escapeHtml(e.asset_id)}</div>
      </div>
    </div>
    <div class="info-grid">
      ${rows.map(([k, v]) => `
        <div class="info-row">
          <span class="info-label">${escapeHtml(k)}</span>
          <span class="info-value">${escapeHtml(String(v))}</span>
        </div>`).join('')}
    </div>
    <div class="info-grid">
      ${pathRows.map(([k, v]) => `
        <div class="info-row full">
          <span class="info-label">${escapeHtml(k)}</span>
          <span class="info-value copy" title="${escapeAttr(v)} (click to copy)" data-copy="${escapeAttr(v)}">${escapeHtml(String(v))}</span>
        </div>`).join('')}
    </div>
  `;
  host.querySelectorAll('[data-copy]').forEach((el) => el.addEventListener('click', () => {
    navigator.clipboard?.writeText(el.dataset.copy);
    toast('Copied to clipboard', 'ok', 1500);
  }));
}

// ============ CARLA Export ============
function wireCarlaExport() {
  document.getElementById('exp-stage').addEventListener('click', stageExport);
  document.getElementById('exp-copy-cmd').addEventListener('click', () => {
    navigator.clipboard?.writeText(document.getElementById('exp-bake-cmd').textContent);
    toast('Command copied', 'ok');
  });
  document.getElementById('pkg-name').addEventListener('input', updateBakeCmd);
  updateBakeCmd();
  // Initialize CARLA root from env
  document.getElementById('carla-root').value = window.__carlaRoot || '';
}
function updateBakeCmd() {
  document.getElementById('exp-bake-cmd').textContent =
    `make import ARGS="--package ${val('pkg-name') || 'carmela_pack'}"`;
}
async function loadExportRows() {
  const ids = window.__carmela_export_ids || [];
  const tbody = document.getElementById('exp-rows');
  if (!ids.length) {
    tbody.innerHTML = '<tr><td colspan=6 class="hint empty">Nothing selected — pick assets from Library.</td></tr>';
    document.getElementById('exp-selected-count').textContent = '0 selected · pick from Library';
    return;
  }
  document.getElementById('exp-selected-count').textContent = `${ids.length} selected for export`;
  const all = await api('/library');
  const rows = ids.map((id) => all.find((e) => e.asset_id === id)).filter(Boolean);
  tbody.innerHTML = rows.map((e) => {
    const meta = e.metadata_path ? '' : '';
    return `<tr>
      <td>${e.asset_id.slice(0, 12)}</td>
      <td>${e.request.asset_class}</td>
      <td class="hint">—</td>
      <td class="hint">—</td>
      <td>${(window.__semanticMap?.[e.asset_id]) || classToSemantic(e.request.asset_class)}</td>
      <td><button class="btn xs" data-rm="${e.asset_id}">remove</button></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('[data-rm]').forEach((b) => b.addEventListener('click', () => {
    window.__carmela_export_ids = (window.__carmela_export_ids || []).filter((x) => x !== b.dataset.rm);
    loadExportRows();
  }));
}
function classToSemantic(c) {
  return { vehicle: 'Vehicle', pedestrian: 'Pedestrian', sign: 'TrafficSign', barrier: 'Static', prop: 'Other', debris: 'Dynamic', vegetation: 'Vegetation', building: 'Building' }[c] || 'Other';
}
async function stageExport() {
  const out = document.getElementById('exp-stage-out');
  out.className = 'status-line busy'; out.textContent = '· staging…';
  const body = {
    package_name: val('pkg-name') || 'carmela_pack',
    asset_class: null,
    carla_root: val('carla-root') || null,
    remote_host: val('carla-remote-host') || null,
    transport: val('carla-transport'),
    include_collision: document.getElementById('pkg-with-collision').checked,
    include_lods: document.getElementById('pkg-with-lods').checked,
    naming: val('pkg-naming'),
    asset_ids: window.__carmela_export_ids || [],
  };
  try {
    const r = await api('/library/package', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    out.className = 'status-line ok';
    out.textContent = `· staged ${r.converted} converted, ${r.skipped.length} skipped → ${r.import_dir}`;
    toast(`Package staged at ${r.import_dir}`, 'ok', 6000);
    if (tcAPI && r.import_dir) tcAPI.openFolder(r.import_dir);
  } catch (e) {
    out.className = 'status-line err';
    out.textContent = '· ' + e.message;
    toast(e.message, 'err', 7000);
  }
}

// ============ CARLA Sim ============
function wireCarlaSim() {
  document.getElementById('carla-check').addEventListener('click', async () => {
    const h = val('carla-host'), p = +val('carla-port');
    const s = document.getElementById('carla-status');
    s.className = 'status-line busy'; s.textContent = '· checking…';
    try {
      const r = await api(`/carla/status?host=${h}&port=${p}`);
      if (r.ok) { s.className = 'status-line ok'; s.textContent = `· connected · client ${r.client_version} ↔ server ${r.server_version}`; }
      else { s.className = 'status-line err'; s.textContent = '· ' + (r.error || 'offline'); }
    } catch (e) {
      s.className = 'status-line err'; s.textContent = '· ' + e.message;
    }
    refreshAllStatus();
  });
  document.getElementById('carla-launch').addEventListener('click', async () => {
    if (!tcAPI) return;
    const r = await tcAPI.startCarla();
    toast(r.ok ? 'CarlaUE4 launching…' : r.error, r.ok ? 'ok' : 'err');
  });
  document.getElementById('carla-bp-list').addEventListener('click', async () => {
    const r = await api(`/carla/blueprints?host=${val('carla-host')}&port=${+val('carla-port')}&filter_prefix=${encodeURIComponent(val('carla-bp-filter'))}`);
    const sel = document.getElementById('carla-bp');
    sel.innerHTML = (r.ids || []).map((id) => `<option>${id}</option>`).join('');
  });
  document.getElementById('carla-spawn').addEventListener('click', async () => {
    const sel = document.getElementById('carla-bp');
    if (!sel.value) { toast('Pick a blueprint first', 'warn'); return; }
    const body = {
      host: val('carla-host'), port: +val('carla-port'),
      blueprint_id: sel.value,
      x: +val('sp-x'), y: +val('sp-y'), z: +val('sp-z'), yaw_deg: +val('sp-yaw'),
      as_vehicle: document.getElementById('sp-vehicle').checked,
    };
    try {
      const r = await api('/carla/spawn', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      document.getElementById('carla-spawn-out').textContent = JSON.stringify(r, null, 2);
      toast(`Spawned ${r.type_id}`, 'ok');
    } catch (e) {
      document.getElementById('carla-spawn-out').textContent = e.message;
      toast(e.message, 'err');
    }
  });
}

// ============ Settings ============
function wireSettings() {
  document.getElementById('settings-refresh').addEventListener('click', refreshSettings);
  wireTrellisCard();

  const keyInput = document.getElementById('bespoke-key-input');
  document.getElementById('bespoke-key-reveal').addEventListener('click', () => {
    keyInput.type = keyInput.type === 'password' ? 'text' : 'password';
  });
  document.getElementById('bespoke-key-save').addEventListener('click', saveBespokeKey);
  keyInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveBespokeKey(); });
}

// ============ TRELLIS 2.0 install card ============
let t2PollTimer = null;

function wireTrellisCard() {
  document.getElementById('t2-refresh').addEventListener('click', refreshTrellisStatus);
}

async function refreshTrellisStatus() {
  let s;
  try { s = await api('/system/trellis2/status'); } catch (e) { return; }
  const exts = s.extensions || {};
  document.querySelectorAll('.t2-ext').forEach((el) => {
    const ok = exts[el.dataset.ext] === 'ok';
    el.querySelector('.dot').className = 'dot ' + (ok ? 'ok' : 'off');
  });
  const pill = document.getElementById('t2-pill');
  const okCount = Object.values(exts).filter((v) => v === 'ok').length;
  if (s.ready) {
    pill.className = 'pill ok';
    pill.textContent = 'ready · 5/5';
  } else if (okCount > 0) {
    pill.className = 'pill warn';
    pill.textContent = `partial · ${okCount}/5 — re-run scripts/install.ps1`;
  } else {
    pill.className = 'pill err';
    pill.textContent = 'not installed — run scripts/install.ps1';
  }
}
function renderTrellisLog(lines) {
  const log = document.getElementById('t2-log');
  const html = lines.map((ln) => {
    let cls = '';
    if (/\[OK\]/.test(ln))        cls = 'log-ok';
    else if (/\[FAIL\]|ERROR/.test(ln)) cls = 'log-err';
    else if (/^===|^\[/.test(ln)) cls = 'log-step';
    return `<span class="${cls}">${escapeHtml(ln)}</span>`;
  }).join('\n');
  log.innerHTML = html;
  log.scrollTop = log.scrollHeight;
}

async function refreshBespokeKey() {
  const status = await api('/system/bespoke-key').catch(() => null);
  const banner = document.getElementById('bespoke-banner');
  const pill = document.getElementById('key-pill');
  const input = document.getElementById('bespoke-key-input');
  if (!status) return;
  if (status.set) {
    banner.setAttribute('hidden', '');
    if (pill) { pill.textContent = 'set · ' + status.preview; pill.className = 'pill ok'; }
    if (input && !input.value) input.placeholder = status.preview;
  } else {
    banner.removeAttribute('hidden');
    if (pill) { pill.textContent = 'not set'; pill.className = 'pill err'; }
  }
}

async function saveBespokeKey() {
  const input = document.getElementById('bespoke-key-input');
  const status = document.getElementById('bespoke-key-status');
  const pill = document.getElementById('key-pill');
  const key = (input.value || '').trim();
  if (!key) { toast('Paste your key first', 'warn'); return; }
  if (!key.startsWith('bspk_')) { toast('BespokeAI keys start with bspk_', 'err'); return; }
  status.className = 'status-line busy';
  status.textContent = '· saving…';
  pill.className = 'pill busy';
  pill.textContent = 'saving…';
  try {
    await api('/system/bespoke-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key }),
    });
    status.textContent = '· saved · testing connection…';
    const test = await api('/system/bespoke-key/test', { method: 'POST' });
    if (test.ok) {
      status.className = 'status-line ok';
      status.textContent = '· connected · ' + (test.message || 'ready');
      pill.className = 'pill ok';
      pill.textContent = 'connected';
      toast('BespokeAI connected', 'ok');
      input.value = '';
    } else {
      status.className = 'status-line err';
      status.textContent = '· saved, but test failed: ' + (test.message || 'unknown');
      pill.className = 'pill err';
      pill.textContent = 'invalid';
      toast('Saved, but test failed — check the key.', 'err');
    }
  } catch (e) {
    status.className = 'status-line err';
    status.textContent = '· ' + e.message;
    pill.className = 'pill err';
    pill.textContent = 'error';
  }
  refreshAllStatus();
}
async function refreshSettings() {
  const tb = document.querySelector('#settings-backends tbody');
  const rows = await api('/backends/health');
  tb.innerHTML = rows.map((r) => `
    <tr>
      <td>${r.ok ? '🟢' : '⚫'}</td>
      <td><b>${r.name}</b></td>
      <td><code>${escapeHtml(r.message)}</code></td>
    </tr>`).join('');

  const paths = await api('/system/env');
  const pt = document.querySelector('#settings-paths tbody');
  pt.innerHTML = Object.entries(paths).map(([k, v]) => `<tr><td>${k}</td><td><code>${escapeHtml(v || '—')}</code></td></tr>`).join('');
  if (paths.CARLA_ROOT) { document.getElementById('carla-root').value = paths.CARLA_ROOT; window.__carlaRoot = paths.CARLA_ROOT; }
  refreshBespokeKey();
  refreshTrellisStatus();
}

// ============ BBOX modal ============
function wireBboxModal() {
  const m = document.getElementById('modal-bbox');
  m.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => m.setAttribute('hidden', '')));
  document.getElementById('bbox-auto').addEventListener('click', () => {
    const b = getBounds(document.getElementById('bbox-viewer'));
    if (!b) return;
    fillBboxInputs('bbox-t', b.min, b.max);
    fillBboxInputs('bbox-c', b.min, b.max);
    drawBboxPreview();
  });
  document.getElementById('bbox-pad').addEventListener('click', () => {
    ['t','c'].forEach((k) => {
      const min = readBboxInputs('bbox-' + k + 'min');
      const max = readBboxInputs('bbox-' + k + 'max');
      const pad = max.map((v, i) => (v - min[i]) * 0.025);
      const nmin = min.map((v, i) => +(v - pad[i]).toFixed(3));
      const nmax = max.map((v, i) => +(v + pad[i]).toFixed(3));
      fillBboxInputs('bbox-' + k, nmin, nmax);
    });
    drawBboxPreview();
  });
  document.getElementById('bbox-save').addEventListener('click', async () => {
    if (!lastGenerated) { toast('Nothing to save bbox to', 'warn'); m.setAttribute('hidden', ''); return; }
    const body = {
      tight_min: readBboxInputs('bbox-tmin'),
      tight_max: readBboxInputs('bbox-tmax'),
      collision_min: readBboxInputs('bbox-cmin'),
      collision_max: readBboxInputs('bbox-cmax'),
    };
    try {
      await api(`/library/${lastGenerated.asset_id}/bbox`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      toast('bbox saved', 'ok');
      m.setAttribute('hidden', '');
    } catch (e) { toast(e.message, 'err'); }
  });
  ['bbox-tmin-x','bbox-tmin-y','bbox-tmin-z','bbox-tmax-x','bbox-tmax-y','bbox-tmax-z',
   'bbox-cmin-x','bbox-cmin-y','bbox-cmin-z','bbox-cmax-x','bbox-cmax-y','bbox-cmax-z']
    .forEach((id) => document.getElementById(id).addEventListener('input', drawBboxPreview));
}
function fillBboxInputs(prefix, min, max) {
  ['x','y','z'].forEach((ax, i) => {
    document.getElementById(`${prefix}min-${ax}`).value = +min[i].toFixed(3);
    document.getElementById(`${prefix}max-${ax}`).value = +max[i].toFixed(3);
  });
}
function readBboxInputs(prefix) {
  return ['x','y','z'].map((ax) => +document.getElementById(`${prefix}-${ax}`).value || 0);
}
function drawBboxPreview() {
  const tmin = readBboxInputs('bbox-tmin'), tmax = readBboxInputs('bbox-tmax');
  setBBox(document.getElementById('bbox-viewer'), tmin, tmax, 'tight');
}

async function openBboxModal() {
  if (!lastGenerated) return;
  const m = document.getElementById('modal-bbox');
  m.removeAttribute('hidden');
  try {
    const res = await loadGLB(document.getElementById('bbox-viewer'), `${API_BASE}/library/${lastGenerated.asset_id}/glb`);
    if (res.bounds) {
      fillBboxInputs('bbox-t', res.bounds.min, res.bounds.max);
      fillBboxInputs('bbox-c', res.bounds.min, res.bounds.max);
      drawBboxPreview();
    }
  } catch (e) { /* noop */ }
}

// ============ helpers ============
function val(id) { const el = document.getElementById(id); return el ? el.value : ''; }
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function escapeAttr(s) {
  return String(s ?? '').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

bootstrap().catch((e) => toast('bootstrap failed: ' + e.message, 'err', 8000));
